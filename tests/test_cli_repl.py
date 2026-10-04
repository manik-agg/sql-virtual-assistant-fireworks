"""F-14: the real repl() loop, driven end to end with piped input, a temp database and a fake model (no network)."""

import asyncio
import contextlib
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from src import cli, llm


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shop.sqlite"
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE loan (loan_id INTEGER PRIMARY KEY, amount INTEGER); INSERT INTO loan VALUES (1,100),(2,300);"
    )
    c.commit()
    c.close()
    return str(path)


def model_output(**kw):
    base = {
        "turn_type": "new_topic",
        "status": "answer",
        "reason": "none",
        "sql": "SELECT COUNT(*) AS n FROM loan",
        "assumed": None,
        "concept": None,
        "clarification": None,
    }
    base.update(kw)
    return json.dumps(base)


@pytest.fixture
def run_repl(monkeypatch, db_path, tmp_path, capsys):
    """Run repl() on the given input lines. `agent_replies` feed the SQL call; `explanation` / `audit_reply` feed the side calls."""

    def run(
        lines, agent_replies, explanation="An explanation.", audit_reply="EXACT", audit=True, log=True, desc_dir=None
    ):
        agent_queue = list(agent_replies)

        def fake_chat(client, messages, **kw):
            return llm.LLMResult(agent_queue.pop(0), 0.01, 100, 0, 20, 0.0001)

        async def fake_achat(client, messages, **kw):
            is_audit = (
                "Compare the question with the SQL" in messages[-1]["content"]
            )  # the two side calls run concurrently
            text = audit_reply if is_audit else explanation
            return llm.LLMResult(text, 0.01, 50, 0, 10, 0.00005)

        monkeypatch.setattr("src.agent.llm.chat", fake_chat)
        monkeypatch.setattr(llm, "achat", fake_achat)
        monkeypatch.setattr(
            cli, "patch_stdout", lambda raw=True: contextlib.nullcontext()
        )  # the pty checks cover the real one
        monkeypatch.setattr(llm, "get_async_client", lambda: object())
        monkeypatch.setattr(llm, "get_client", lambda: object())  # Agent() builds its own client: no key may be needed
        monkeypatch.setattr(sys, "stdin", io.StringIO("".join(line + "\n" for line in lines)))
        log_path = str(tmp_path / "logs" / "turns.jsonl") if log else None
        asyncio.run(cli.repl(db_path, False, "m", desc_dir, "/nowhere/database_description", audit, log_path))
        return capsys.readouterr().out, log_path

    return run


def test_one_question_shows_sql_result_explanation_and_logs_the_turn(run_repl):
    out, log = run_repl(["How many loans are there?", "exit"], [model_output()])
    assert "SELECT COUNT(*) AS n FROM loan" in out
    assert "2" in out and "Interpretation: An explanation." in out
    record = json.loads(Path(log).read_text().splitlines()[0])
    assert record["status"] == "answer" and record["row_count"] == 1 and "2" not in str(record["sql"])


def test_a_flagged_assumption_prints_after_the_answer(run_repl):
    out, _ = run_repl(["How many loans?", "exit"], [model_output()], audit_reply="ASSUMES: loans = rows of loan")
    assert "Assumed (checked after the answer): loans = rows of loan" in out


def test_no_audit_flag_skips_the_check(run_repl):
    out, _ = run_repl(["How many loans?", "exit"], [model_output()], audit_reply="ASSUMES: x", audit=False)
    assert "checked after the answer" not in out


def test_no_log_writes_nothing(run_repl, tmp_path):
    run_repl(["How many loans?", "exit"], [model_output()], log=False)
    assert not (tmp_path / "logs").exists()


def test_a_write_request_gets_the_read_only_message_and_no_result(run_repl):
    out, _ = run_repl(
        ["Delete all loans", "exit"], [model_output(status="out_of_scope", reason="write_attempt", sql=None)]
    )
    assert "read-only" in out.lower() and "SELECT" not in out


def test_commands_and_unknown_commands_are_handled_locally(run_repl):
    out, _ = run_repl(["/clear", "/bogus", "/help", "quit"], [])
    assert "Unknown command" in out or "unknown command" in out.lower()
    assert "/keep" in out  # the help text


def test_a_missing_description_folder_prints_the_warning(run_repl):
    out, _ = run_repl(["exit"], [])
    assert "no column descriptions found at /nowhere/database_description" in out


def test_end_of_input_ends_the_session(run_repl):
    out, _ = run_repl([], [])
    assert "SQL Virtual Assistant" in out


def test_a_model_failure_is_a_plain_message_not_a_crash(run_repl):
    out, _ = run_repl(["How many loans?", "exit"], ["not json at all", "still not json"])
    assert "Traceback" not in out


def test_main_exits_1_with_a_message_when_the_database_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cli", "--db", "/definitely/not/here.sqlite"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1 and "error:" in capsys.readouterr().err


def test_main_passes_the_flags_through(monkeypatch):
    seen = {}

    async def fake_repl(*args):
        seen["args"] = args

    monkeypatch.setattr(cli, "repl", fake_repl)
    monkeypatch.setattr(sys, "argv", ["cli", "--db", "x.sqlite", "--no-audit", "--no-log", "-v", "--descriptions", "d"])
    cli.main()
    db, verbose, model, desc, expected, audit, log = seen["args"]
    assert (db, verbose, audit, log, desc) == ("x.sqlite", True, False, None, "d")


def test_a_daemon_thread_delivers_results_and_errors():
    async def go():
        loop = asyncio.get_running_loop()
        assert await cli.run_in_daemon_thread(loop, lambda a: a + 1, 1) == 2
        with pytest.raises(ValueError):
            await cli.run_in_daemon_thread(loop, lambda: (_ for _ in ()).throw(ValueError("boom")))

    asyncio.run(go())


def test_exit_words():
    assert all(cli.is_exit(w) for w in ("exit", " QUIT ", "Exit."))
    assert not cli.is_exit("exits")
