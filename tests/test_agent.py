import httpx
import openai
import pytest
from conftest import answer

from src.agent import READ_ONLY_MESSAGE, Agent, AgentConfig, parse_output


def make(conn, **kw):
    return Agent(conn, AgentConfig(desc_dir=None, **kw), client=object())


def test_parse_output_extracts_json_from_chatter():
    out = parse_output('sure! {"turn_type":"new_topic","status":"answer","sql":"select 1","clarification":null}')
    assert out.sql == "select 1"


def test_out_of_scope_never_carries_sql():
    out = parse_output('{"turn_type":"new_topic","status":"out_of_scope","sql":"drop table x","clarification":null}')
    assert out.sql is None


def test_answer_without_sql_is_invalid():
    with pytest.raises(ValueError):
        parse_output('{"turn_type":"new_topic","status":"answer","sql":null,"clarification":null}')


def test_happy_path(conn, fake_llm):
    fake = fake_llm([answer("SELECT COUNT(*) AS n FROM loan")])
    r = make(conn).turn("how many loans")
    assert r.status == "answer" and r.result.rows == [(2,)] and r.tables == ["loan"] and len(fake.calls) == 1


def test_bad_column_is_retried_with_the_error_text(conn, fake_llm):
    fake = fake_llm([answer("SELECT nope FROM loan"), answer("SELECT COUNT(*) FROM loan")])
    r = make(conn).turn("how many loans")
    assert r.result.rows == [(2,)] and r.retries == 1 and r.first_attempt_ok is False
    assert "no such column" in fake.calls[1][-1]["content"]


def test_only_one_retry(conn, fake_llm):
    fake_llm([answer("SELECT nope FROM loan"), answer("SELECT nope2 FROM loan")])
    r = make(conn).turn("q")
    assert r.result is None and r.error and r.retries == 1


def test_malformed_output_gets_one_repair_call(conn, fake_llm):
    fake = fake_llm(["not json at all", answer("SELECT 1")])
    r = make(conn).turn("q")
    assert r.status == "answer" and len(fake.calls) == 2


def test_still_malformed_after_repair_is_an_error_not_a_clarification(conn, fake_llm):
    fake_llm(["nope", "still nope"])
    r = make(conn).turn("q")
    assert r.status == "error" and r.result is None and r.clarification is None
    assert "try again" in r.error.lower() and "Malformed" in r.detail


def test_write_sql_from_the_model_is_never_executed(conn, fake_llm):
    fake_llm([answer("DELETE FROM loan")])
    r = make(conn).turn("remove the loans")
    assert r.status == "out_of_scope" and r.refusal == "read_only" and r.result is None
    assert conn.execute("SELECT COUNT(*) FROM loan").fetchone()[0] == 2


def test_write_request_declined_by_model_gets_read_only_message(conn, fake_llm):
    fake_llm([{"turn_type": "new_topic", "status": "out_of_scope", "sql": None, "clarification": None}])
    r = make(conn).turn("Delete all loans")
    assert r.refusal == "read_only"
    from src.render import summarize_result

    assert summarize_result(r) == READ_ONLY_MESSAGE


@pytest.mark.parametrize(
    "exc, needle",
    [
        (openai.APIConnectionError(request=httpx.Request("POST", "http://x")), "Could not reach"),
        (
            openai.RateLimitError(
                "slow down", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None
            ),
            "rate limit",
        ),
        (
            openai.AuthenticationError(
                "bad", response=httpx.Response(401, request=httpx.Request("POST", "http://x")), body=None
            ),
            "API key",
        ),
    ],
)
def test_api_failures_become_a_friendly_error_not_a_crash(conn, fake_llm, exc, needle):
    fake_llm([exc])
    r = make(conn).turn("q")
    assert r.status == "error" and needle in r.error


def test_truncated_output_is_never_used_and_is_reported(conn, monkeypatch):
    """Regression: a 600-token cap once cut reasoning models off mid-JSON and it looked like flaky accuracy."""
    from src import llm

    def cut_off(client, messages, **kw):
        return llm.LLMResult(
            '{"turn_type":"new_topic","status":"answer","sql":"SELECT 1', 0.01, 100, 0, 3000, 0.0, "", True
        )

    monkeypatch.setattr("src.agent.llm.chat", cut_off)
    r = make(conn).turn("q")
    assert r.status == "error" and r.result is None and "cut off" in r.detail


BAD_OUTPUTS = {
    "malformed text": "this is not json",
    "empty": "",
    "schema violation (unknown status)": '{"turn_type":"new_topic","status":"maybe","sql":null,"clarification":null}',
    "refusal prose": "I'm sorry, I can't help with that.",
}


@pytest.mark.parametrize("text", list(BAD_OUTPUTS.values()), ids=list(BAD_OUTPUTS))
def test_unusable_model_output_is_an_error_after_exactly_one_repair_call(conn, fake_llm, text):
    # F-09: these used to be labelled "clarify", which the eval scorer could count as a correct clarification
    fake = fake_llm([text, text])
    r = make(conn).turn("q")
    assert r.status == "error" and r.clarification is None and r.sql is None
    assert len(fake.calls) == 2 and "try again" in r.error.lower()


def test_a_truncated_answer_is_also_an_error(conn, monkeypatch):
    from src import llm

    def cut_off(client, messages, **kw):
        return llm.LLMResult(
            '{"turn_type":"new_topic","status":"answer","sql":"SELECT', 0.01, 100, 0, 3000, 0.0, "", True
        )

    monkeypatch.setattr("src.agent.llm.chat", cut_off)
    r = make(conn).turn("q")
    assert r.status == "error" and "cut off" in r.detail


def test_the_eval_scorer_never_counts_an_error_as_a_correct_clarification():
    from eval import run as eval_run

    out = eval_run.run_label_cases(
        eval_run.SUITES["clarify"].load(), lambda q: {"status": "error", "sql": None, "total_s": 0.1}
    )
    assert out and not any(rec["match"] for rec in out)
