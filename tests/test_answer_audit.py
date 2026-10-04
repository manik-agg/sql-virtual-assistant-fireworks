"""Answer audit: a separate, asynchronous check that tells the user when the SQL answers a related question (a proxy or a
specific reading) instead of the one asked. It never changes the SQL or the result; it only adds a late note."""

import asyncio

import pytest

from src import llm
from src.agent import Agent, AgentConfig, TurnResult
from src.db import QueryResult
from src.explain import AUDIT_PROMPT, audit_answer, parse_audit_reply
from src.schema import schema_blocks


# ---- parsing the one-line reply -----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "reply, expected",
    [
        ("EXACT", None),
        ("exact", None),
        ("ASSUMES: income read as district average salary", "income read as district average salary"),
        ("assumes: high risk = loan status D", "high risk = loan status D"),
        ("Thinking about it...\nASSUMES: branches read as districts", "branches read as districts"),  # last line wins
        ("ASSUMES:", None),  # nothing to show
        ("", None),
        ("I am not sure", None),  # unparsable: show nothing rather than guess
        ("ASSUMES: " + "x" * 400, "x" * 160),  # one short line only
    ],
)
def test_parse_audit_reply(reply, expected):
    assert parse_audit_reply(reply) == expected


# ---- the prompt is built from the question, the SQL and ONLY the used tables' schema notes ---------------------------------
def test_schema_blocks_are_split_per_table(conn):
    text = Agent(conn, AgentConfig(desc_dir=None), client=object()).system_prompt
    assert set(schema_blocks(text)) == {"account", "card", "client", "district", "loan"}


def make_turn():
    return TurnResult(
        "answer",
        "new_topic",
        "What is the average income of clients?",
        sql="SELECT AVG(A11) FROM district",
        tables=["district"],
        result=QueryResult(["avg"], [(9000.0,)], 1, False, 0.0),
    )


def test_prompt_contains_question_sql_and_only_the_used_tables(conn, monkeypatch):
    sent = {}

    async def fake_achat(client, messages, **kw):
        sent["prompt"] = messages[0]["content"]
        return llm.LLMResult("ASSUMES: income read as district average salary", 0.1, 100, 0, 20, 0.0001)

    monkeypatch.setattr("src.explain.llm.achat", fake_achat)
    blocks = schema_blocks(Agent(conn, AgentConfig(desc_dir=None), client=object()).system_prompt)
    text, result = asyncio.run(audit_answer(None, make_turn(), blocks))
    assert text == "income read as district average salary" and result.cost_usd == 0.0001
    assert (
        "What is the average income of clients?" in sent["prompt"] and "SELECT AVG(A11) FROM district" in sent["prompt"]
    )
    assert 'TABLE "district"' in sent["prompt"] and 'TABLE "loan"' not in sent["prompt"]


def test_audit_prompt_exempts_ordinary_sql_choices():
    assert "Ordinary joins" in AUDIT_PROMPT or "joins" in AUDIT_PROMPT.lower()


def test_an_audit_that_fails_is_silent_not_fatal(conn, monkeypatch):
    async def boom(client, messages, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("src.explain.llm.achat", boom)
    blocks = schema_blocks(Agent(conn, AgentConfig(desc_dir=None), client=object()).system_prompt)
    with pytest.raises(RuntimeError):  # audit_answer itself propagates; the CLI wrapper swallows (tested below)
        asyncio.run(audit_answer(None, make_turn(), blocks))


def test_the_cli_wrapper_swallows_audit_failures_and_prints_only_real_assumptions(monkeypatch, capsys):
    import io

    from rich.console import Console

    from src.cli import audit_and_print

    async def exact(*a, **k):
        return None, llm.LLMResult("EXACT", 0.1, 10, 0, 2, 0.0)

    async def assumes(*a, **k):
        return "branches read as districts", llm.LLMResult("", 0.1, 10, 0, 2, 0.0)

    async def boom(*a, **k):
        raise RuntimeError("x")

    for fn, expect in (
        (exact, ""),
        (assumes, "Assumed (checked after the answer): branches read as districts"),
        (boom, ""),
    ):
        monkeypatch.setattr("src.cli.audit_answer", fn)
        console = Console(file=io.StringIO(), width=120)
        asyncio.run(audit_and_print(None, make_turn(), {}, console, verbose=False))
        assert expect in console.file.getvalue()
        assert (expect == "") == (console.file.getvalue().strip() == "")


def test_the_audit_is_skipped_when_the_model_already_stated_an_assumption():
    from src.cli import should_audit

    turn = make_turn()
    assert should_audit(turn, enabled=True)
    turn.assumed = "income read as salary"
    assert not should_audit(turn, enabled=True)
    turn.assumed = None
    assert not should_audit(turn, enabled=False)
    turn.status = "clarify"
    assert not should_audit(turn, enabled=True)
