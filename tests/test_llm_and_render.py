"""F-14: request building, cost arithmetic, key checks, and what the terminal shows for each kind of turn."""

import asyncio
import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from src import llm
from src.agent import Clarification, Stats, TurnResult
from src.db import QueryResult
from src.render import fmt, show_turn, summarize_result


# ---- llm ----------------------------------------------------------------------------------------------------------------
def fake_response(prompt=1000, cached=400, completion=200, finish="stop", content="hi", reasoning="why"):
    msg = SimpleNamespace(content=content, reasoning_content=reasoning)
    usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=SimpleNamespace(cached_tokens=cached)
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)], usage=usage)


def test_cost_uses_cached_and_uncached_input_and_output_prices():
    price_in, price_cached, price_out = llm.PRICES[llm.DEFAULT_MODEL]
    r = llm._result(fake_response(prompt=1000, cached=400, completion=200), llm.DEFAULT_MODEL, 0.5)
    assert r.cost_usd == pytest.approx((600 * price_in + 400 * price_cached + 200 * price_out) / 1e6)
    assert (r.text, r.latency_s, r.cached_tokens, r.reasoning, r.truncated) == ("hi", 0.5, 400, "why", False)


def test_a_length_finish_marks_the_output_truncated():
    assert llm._result(fake_response(finish="length"), llm.DEFAULT_MODEL, 0.1).truncated is True


def test_request_carries_schema_effort_and_token_cap():
    kw = llm._kwargs([{"role": "user", "content": "x"}], "m", {"type": "object"}, "medium", 123)
    assert kw["max_tokens"] == 123 and kw["temperature"] == 0 and kw["extra_body"] == {"reasoning_effort": "medium"}
    assert kw["response_format"]["json_schema"]["strict"] is True


def test_default_effort_sends_no_reasoning_parameter():
    assert "extra_body" not in llm._kwargs([], "m", None, "default", 10)
    assert "response_format" not in llm._kwargs([], "m", None, None, 10)


@pytest.mark.parametrize("factory", [llm.get_client, llm.get_async_client])
def test_a_missing_key_is_a_clear_error(monkeypatch, factory):
    monkeypatch.setattr(llm.os, "getenv", lambda name, default=None: None)
    with pytest.raises(RuntimeError, match="FIREWORKS_API_KEY"):
        factory()


def test_clients_use_the_shipped_timeout_and_retry_limits(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    client = llm.get_async_client()
    assert client.timeout == llm.REQUEST_TIMEOUT_S and client.max_retries == llm.MAX_RETRIES
    asyncio.run(client.close())


def test_chat_and_achat_call_the_sdk_and_time_it():
    calls = []

    class Sync:
        chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: calls.append(kw) or fake_response()))

    class Async:
        class completions:
            @staticmethod
            async def create(**kw):
                calls.append(kw)
                return fake_response()

        chat = SimpleNamespace(completions=completions)

    assert llm.chat(Sync, [{"role": "user", "content": "q"}], max_tokens=7).text == "hi"
    assert asyncio.run(llm.achat(Async, [{"role": "user", "content": "q"}], max_tokens=8)).text == "hi"
    assert [c["max_tokens"] for c in calls] == [7, 8]


# ---- render -------------------------------------------------------------------------------------------------------------
def render(turn, verbose=False):
    buf = io.StringIO()
    show_turn(Console(file=buf, width=120, force_terminal=False), turn, verbose)
    return buf.getvalue()


def answer_turn(**kw):
    base = dict(
        status="answer",
        turn_type="new_topic",
        question="q",
        sql="SELECT 1",
        tables=["loan"],
        result=QueryResult(["loan_id", "amount"], [(1, 1234.5), (2, None)], 2, False, 0.0),
    )
    base.update(kw)
    return TurnResult(**base)


def test_numbers_get_separators_but_ids_do_not():
    assert fmt(1234567, "amount") == "1,234,567" and fmt(1234.5, "x") == "1,234.50"
    assert fmt(1234567, "client_id") == "1234567" and fmt(7, "id") == "7" and fmt(None) == "NULL"


def test_an_answer_shows_tables_sql_rows_and_the_assumption_line():
    out = render(answer_turn(assumed="income = salary"))
    assert "Tables used: loan" in out and "SELECT 1" in out and "1,234.50" in out and "NULL" in out
    assert "Assumed: income = salary" in out


def test_a_long_result_says_how_many_rows_are_shown():
    rows = [(i, i) for i in range(40)]
    out = render(answer_turn(result=QueryResult(["a", "b"], rows, 1001, True, 0.0)))
    assert "of 1001 rows" in out and "capped" in out


def test_error_and_refusal_and_clarification_are_shown_plainly():
    assert "Boom" in render(TurnResult(status="error", turn_type="new_topic", question="q", error="Boom", detail="why"))
    assert "why" in render(
        TurnResult(status="error", turn_type="new_topic", question="q", error="Boom", detail="why"), True
    )
    assert "multi-step" in render(
        TurnResult(status="out_of_scope", turn_type="new_topic", question="q", refusal="scope")
    )
    clar = TurnResult(
        status="clarify",
        turn_type="new_topic",
        question="q",
        clarification=Clarification(question="Which?", options=["a", "b"]),
    )
    out = render(clar)
    assert "Which?" in out and "1. a" in out and "2. b" in out
    failed = TurnResult(status="answer", turn_type="new_topic", question="q", sql="SELECT x", error="no such column")
    assert "no such column" in render(failed) and "SELECT x" in render(failed)


def test_verbose_shows_timing_cost_and_reasoning():
    turn = answer_turn(
        total_s=1.5,
        stats=Stats(
            llm_calls=1, prompt_tokens=10, cached_tokens=5, completion_tokens=3, cost_usd=0.0001, reasoning=" thought "
        ),
    )
    out = render(turn, verbose=True)
    assert "1.50s total" in out and "10/5/3" in out and "reasoning: thought" in out


def test_summaries_for_each_kind_of_turn():
    assert summarize_result(answer_turn(result=QueryResult(["n"], [(1234,)], 1, False, 0.0))) == "n: 1,234"
    many = answer_turn(result=QueryResult(["a"], [(i,) for i in range(8)], 8, False, 0.0))
    assert summarize_result(many).startswith("8 rows: a=0; a=1") and summarize_result(many).endswith("(+3 more)")
    assert summarize_result(TurnResult(status="error", turn_type="new_topic", question="q", error="x")) == "Error: x"
    clar = TurnResult(
        status="clarify",
        turn_type="new_topic",
        question="q",
        clarification=Clarification(question="Which?", options=["a", "b"]),
    )
    assert summarize_result(clar) == "Clarification needed: Which? (a / b)"
