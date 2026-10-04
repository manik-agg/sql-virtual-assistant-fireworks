"""Terminal rendering with rich, plus a deterministic plain-text answer summary."""

from __future__ import annotations

import time

from rich.console import Console
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table

from src.agent import OUT_OF_SCOPE_MESSAGE, READ_ONLY_MESSAGE, TurnResult
from src.db import MAX_ROWS

NOT_IN_DATABASE_MESSAGE = "I couldn't find data for {concept} in this database."
NOT_IN_DATABASE_GENERIC = "I couldn't find that in this database."
SHOW_ROWS = 20


class ElapsedSpinner:
    """Rich renderable: a spinner plus elapsed seconds ("Working... 2.4s"). Shown while the model and the query run (F-08)."""

    def __init__(self, label: str = "Working...", clock=time.monotonic):
        self.label, self.clock, self.started = label, clock, clock()
        self.spinner = Spinner("dots")

    def text(self) -> str:
        return f"{self.label} {self.clock() - self.started:.1f}s"

    def __rich__(self):
        self.spinner.update(text=self.text())
        return self.spinner


def fmt(v, column: str = "") -> str:
    """Thousands separators and 2 decimals for measures; identifiers (…id columns) are left untouched."""
    if column.lower() == "id" or column.lower().endswith("_id"):
        return str(v)
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return "NULL" if v is None else str(v)


def refusal_message(turn) -> str:
    """The one fixed message per refusal reason (the model only picks the category, it never writes this text)."""
    if turn.refusal == "read_only":
        return READ_ONLY_MESSAGE
    if turn.refusal == "not_in_database":
        message = NOT_IN_DATABASE_MESSAGE.format(concept=turn.concept) if turn.concept else NOT_IN_DATABASE_GENERIC
        return f"{message} Closest in this database: {', '.join(turn.closest)}." if turn.closest else message
    return OUT_OF_SCOPE_MESSAGE


def summarize_result(turn: TurnResult) -> str:
    """Human-readable, deterministic summary used for one-line answer summaries (no LLM)."""
    if turn.status == "error":
        return f"Error: {turn.error}"
    if turn.status == "out_of_scope":
        return refusal_message(turn)
    if turn.status == "clarify" and turn.clarification:
        return f"Clarification needed: {turn.clarification.question} ({' / '.join(turn.clarification.options)})"
    res = turn.result
    if res is None:
        return f"Error: {turn.error}"
    if len(res.rows) == 1 and len(res.columns) == 1:
        return f"{res.columns[0]}: {fmt(res.rows[0][0], res.columns[0])}"
    head = "; ".join(", ".join(f"{c}={fmt(v, c)}" for c, v in zip(res.columns, r)) for r in res.rows[:5])
    more = f" (+{len(res.rows) - 5} more)" if len(res.rows) > 5 else ""
    return f"{len(res.rows)} rows: {head}{more}"


def show_turn(console: Console, turn: TurnResult, verbose: bool = False) -> None:
    if turn.status == "error":
        console.print(f"[red]{turn.error}[/red]")
        if verbose and turn.detail:
            console.print(f"[dim]{turn.detail}[/dim]")
    elif turn.status == "out_of_scope":
        console.print(f"[yellow]{refusal_message(turn)}[/yellow]")
    elif turn.status == "clarify" and turn.clarification:
        console.print(f"[cyan]{turn.clarification.question}[/cyan]")
        for i, opt in enumerate(turn.clarification.options, 1):
            console.print(f"  {i}. {opt}")
    elif turn.error and turn.result is None:
        console.print(f"[red]Could not produce a working query: {turn.error}[/red]")
        if turn.sql:
            console.print(Syntax(turn.sql, "sql", word_wrap=True))
    else:
        console.print(f"[dim]Tables used:[/dim] {', '.join(turn.tables)}")
        console.print(Syntax(turn.sql or "", "sql", word_wrap=True))
        if turn.assumed:  # synchronous, directly under the SQL: the trust fix must not wait for the async explanation
            console.print(f"[yellow]Assumed:[/yellow] {turn.assumed}")
        res = turn.result
        assert res is not None  # an answer without a result and without an error cannot happen
        table = Table(show_lines=False)
        for c in res.columns:
            table.add_column(c)
        for row in res.rows[:SHOW_ROWS]:
            table.add_row(*[fmt(v, c) for v, c in zip(row, res.columns)])
        console.print(table)
        if len(res.rows) > SHOW_ROWS:
            suffix = f"+ more (capped at {MAX_ROWS})" if res.truncated else ""
            console.print(f"[dim]showing {SHOW_ROWS} of {res.total_rows} rows {suffix}[/dim]")
    if verbose:
        s = turn.stats
        console.print(
            f"[dim]{turn.total_s:.2f}s total (llm {s.llm_latency_s:.2f}s, sql {s.exec_latency_s:.3f}s, "
            f"{s.llm_calls} call(s), retries {turn.retries}) tokens in/cached/out "
            f"{s.prompt_tokens}/{s.cached_tokens}/{s.completion_tokens} ${s.cost_usd:.5f}[/dim]"
        )
        if s.reasoning:
            console.print(f"[dim italic]reasoning: {s.reasoning.strip()}[/dim italic]")
