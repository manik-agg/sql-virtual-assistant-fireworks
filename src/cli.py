"""Interactive CLI. Run with: uv run cli (or python -m src.cli)

The async explanation prints above the prompt (prompt_toolkit patch_stdout) and is cancelled when
the user submits the next input.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sqlite3
import sys
import threading
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live

from src import llm
from src.agent import Agent, AgentConfig
from src.db import DEFAULT_DB_PATH, connect_readonly, sqlite_path
from src.explain import audit_answer, explain
from src.render import ElapsedSpinner, show_turn
from src.schema import matched_description_count, schema_blocks
from src.session import Session
from src.turnlog import DEFAULT_LOG_PATH, TurnLog

EXIT_WORDS = {"exit", "quit"}


def is_exit(text: str) -> bool:
    """exit / quit in any case, with surrounding spaces or a trailing period."""
    return text.strip().lower().rstrip(".") in EXIT_WORDS


DESCRIPTIONS_DIRNAME = "database_description"  # looked up next to the database file unless --descriptions is given


def expected_desc_dir(db_path: str, explicit: str | None) -> str:
    """Where descriptions are (or would be) read from; used to name the path in the warning."""
    return explicit or str(Path(sqlite_path(db_path)).resolve().parent / DESCRIPTIONS_DIRNAME)


def default_desc_dir(db_path: str, explicit: str | None) -> str | None:
    path = expected_desc_dir(db_path, explicit)
    return path if explicit or Path(path).is_dir() else None


def description_warning(expected_dir: str, matched: int) -> str | None:
    """One line shown at startup when the prompt will have no column descriptions (F-06). Never fatal."""
    if matched > 0:
        return None
    return f"Warning: no column descriptions found at {expected_dir}. Accuracy may drop without them."


def run_in_daemon_thread(loop: asyncio.AbstractEventLoop, fn, *args) -> asyncio.Future:
    """Run a blocking call in a DAEMON thread and return an awaitable future.

    `asyncio.to_thread` uses non-daemon pool threads that Python joins at exit, so quitting right after Ctrl-C waited for the
    abandoned model call (11 s measured, up to about 30 s in the worst case). A daemon thread is simply dropped at exit (F-08).
    """
    future = loop.create_future()

    def deliver(result, error) -> None:
        if future.cancelled():
            return
        future.set_exception(error) if error is not None else future.set_result(result)

    def work() -> None:
        try:
            result, error = fn(*args), None
        except BaseException as e:
            result, error = None, e
        try:
            loop.call_soon_threadsafe(deliver, result, error)
        except RuntimeError:  # the loop is already closed: the user quit, nobody is waiting
            pass

    threading.Thread(target=work, daemon=True).start()
    return future


async def read_line(session: PromptSession | None) -> str:
    if session is not None:
        return await session.prompt_async("ask> ")
    loop = asyncio.get_running_loop()
    line = await loop.run_in_executor(None, sys.stdin.readline)
    if not line:
        raise EOFError
    return line.rstrip("\n")


async def explain_and_print(client, turn, console: Console, verbose: bool) -> None:
    try:
        text, r = await explain(client, turn)
    except asyncio.CancelledError:
        raise
    except Exception:
        return  # the explanation is best-effort and must never disturb the session
    console.print(f"[green]Interpretation:[/green] {text}")
    if verbose:
        console.print(f"[dim]explanation: {r.latency_s:.2f}s ${r.cost_usd:.5f}[/dim]")


def should_audit(turn, enabled: bool) -> bool:
    """Audit answered turns where the model did not already state an assumption."""
    return enabled and turn.status == "answer" and turn.result is not None and not turn.assumed


async def audit_and_print(client, turn, blocks, console: Console, verbose: bool) -> None:
    """Print `Assumed (checked after the answer): ...` when the audit finds one. Best effort: failures print nothing."""
    try:
        text, r = await audit_answer(client, turn, blocks)
    except asyncio.CancelledError:
        raise
    except Exception:
        return
    if text:
        console.print(f"[yellow]Assumed (checked after the answer):[/yellow] {text}")
        if verbose:
            console.print(f"[dim]audit: {r.latency_s:.2f}s ${r.cost_usd:.5f}[/dim]")


async def warm_up(client, agent: Agent) -> None:
    """Prime the connection and the provider's prefix cache while the user reads the banner and types.

    A cold prefix cost ~1.9s on the first query vs ~0.65s warm (measured). Best effort: failures are ignored.
    """
    try:
        await llm.achat(
            client,
            [{"role": "system", "content": agent.system_prompt}, {"role": "user", "content": "Question: ok"}],
            model=agent.config.model,
            reasoning_effort=agent.config.reasoning_effort,
            max_tokens=64,
        )
    except Exception:
        pass


async def repl(
    db_path: str,
    verbose: bool,
    model: str,
    desc_dir: str | None,
    expected_desc: str,
    audit: bool = True,
    log_path: str | None = None,
) -> None:
    console = Console()
    conn = connect_readonly(db_path)
    agent = Agent(conn, AgentConfig(model=model, desc_dir=desc_dir))
    session = Session(agent)
    turn_log = TurnLog(log_path, model)
    aclient = llm.get_async_client()
    warm = asyncio.create_task(warm_up(aclient, agent))
    interactive = sys.stdin.isatty()
    prompt = PromptSession() if interactive else None
    console.print("[bold]SQL Virtual Assistant.[/bold] Ask a question, or type /clear, /keep, exit.")
    warning = description_warning(expected_desc, matched_description_count(conn, desc_dir))
    if warning:
        console.print(f"[yellow]{warning}[/yellow]")
    blocks = schema_blocks(agent.system_prompt)
    pending: list[
        asyncio.Task
    ] = []  # the explanation and the answer audit: both are cancelled when the next input arrives
    with patch_stdout(raw=True):
        while True:
            try:
                text = (await read_line(prompt)).strip()
            except EOFError:  # Ctrl-D or end of piped input
                break
            except KeyboardInterrupt:  # Ctrl-C at the prompt: drop the line, keep the session
                console.print("[dim]Press Ctrl-D or type exit to quit.[/dim]")
                continue
            for task in pending:
                task.cancel()  # user moved on; abort the in-flight explanation and audit
            if not text:
                continue
            if is_exit(text):
                break
            loop = asyncio.get_running_loop()
            turn_task = run_in_daemon_thread(loop, session.handle, text)

            def on_sigint(turn_task=turn_task) -> None:  # Ctrl-C while a turn runs: stop waiting and discard its result
                session.cancel()
                turn_task.cancel()

            try:
                loop.add_signal_handler(signal.SIGINT, on_sigint)
            except (NotImplementedError, RuntimeError):  # e.g. Windows: fall back to default handling
                pass
            try:
                if (
                    interactive
                ):  # spinner with elapsed seconds; transient so it leaves nothing behind, stops cleanly on cancel
                    with Live(ElapsedSpinner(), console=console, refresh_per_second=10, transient=True):
                        event = await turn_task
                else:
                    event = await turn_task
            except asyncio.CancelledError:
                console.print("[yellow]Cancelled.[/yellow]")
                continue
            except Exception as e:  # never let one bad turn end the session
                console.print(f"[red]Unexpected error: {type(e).__name__}: {e}[/red]")
                continue
            finally:
                try:
                    loop.remove_signal_handler(signal.SIGINT)
                except (NotImplementedError, RuntimeError):
                    pass
            if event.kind == "message":
                console.print(f"[dim]{event.message}[/dim]")
                continue
            if event.notice:
                console.print(f"[yellow]{event.notice}[/yellow]")
            show_turn(console, event.turn, verbose)
            turn_log.write(event)
            if event.turn.status == "answer" and event.turn.result is not None:
                pending = [asyncio.create_task(explain_and_print(aclient, event.turn, console, verbose))]
                if should_audit(event.turn, audit):
                    pending.append(asyncio.create_task(audit_and_print(aclient, event.turn, blocks, console, verbose)))
                if not interactive:
                    await asyncio.gather(*pending)  # piped/eval runs: keep output deterministic
    for task in [*pending, warm]:
        if not task.done():
            task.cancel()
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli")
    ap.add_argument("--db", default=DEFAULT_DB_PATH, help="path to a SQLite database")
    ap.add_argument(
        "--descriptions",
        help="folder of column-description CSVs (default: <db folder>/database_description if present)",
    )
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument(
        "--no-log",
        action="store_true",
        help=f"do not write {DEFAULT_LOG_PATH} (questions, SQL, timings, cost; never result values or keys)",
    )
    ap.add_argument(
        "--no-audit",
        action="store_true",
        help="skip the after-the-answer check for assumptions (saves about $0.0001 per query)",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="show timings, tokens and model reasoning")
    args = ap.parse_args()
    try:
        asyncio.run(
            repl(
                args.db,
                args.verbose,
                args.model,
                default_desc_dir(args.db, args.descriptions),
                expected_desc_dir(args.db, args.descriptions),
                not args.no_audit,
                None if args.no_log else DEFAULT_LOG_PATH,
            )
        )
    except (RuntimeError, FileNotFoundError, sqlite3.Error) as e:  # missing key, bad path, unreadable DB
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
