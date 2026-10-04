"""F-17: one JSON line per turn for debugging and cost review. No result values, no keys, never breaks the session."""

import json
import os

from src.agent import Stats, TurnResult
from src.db import QueryResult
from src.session import Event
from src.turnlog import DEFAULT_LOG_PATH, TurnLog, turn_record

FAKE_KEY = "fw_" + "SECRETKEY123"  # built at runtime so a secret scan of the ZIP finds nothing


def make_turn(**kw):
    stats = Stats(
        llm_calls=1,
        llm_latency_s=0.8,
        exec_latency_s=0.01,
        prompt_tokens=30000,
        cached_tokens=29000,
        completion_tokens=200,
        cost_usd=0.0021,
        reasoning="secret chain of thought",
    )
    base = dict(
        status="answer",
        turn_type="new_topic",
        question="How many loans?",
        sql="SELECT COUNT(*) FROM loan",
        tables=["loan"],
        result=QueryResult(["n"], [(682,)], 1, False, 0.01),
        total_s=1.2,
        stats=stats,
    )
    base.update(kw)
    return TurnResult(**base)


def test_record_has_the_fields_needed_for_debugging_and_cost():
    rec = turn_record(Event("turn", make_turn(), switched=True))
    for key in (
        "ts",
        "question",
        "status",
        "reason",
        "turn_type",
        "sql",
        "tables",
        "row_count",
        "truncated",
        "retries",
        "first_attempt_ok",
        "total_s",
        "llm_s",
        "exec_s",
        "prompt_tokens",
        "cached_tokens",
        "completion_tokens",
        "cost_usd",
        "switched",
        "error",
        "model",
    ):
        assert key in rec, key
    assert rec["row_count"] == 1 and rec["switched"] is True and rec["cost_usd"] == 0.0021


def test_record_never_contains_result_values_or_reasoning():
    line = json.dumps(turn_record(Event("turn", make_turn())))
    assert "682" not in line and "secret chain" not in line


def test_record_never_contains_the_api_key(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", FAKE_KEY)
    line = json.dumps(turn_record(Event("turn", make_turn(error=f"bad key {FAKE_KEY} rejected"))))
    assert FAKE_KEY not in line


def test_log_appends_one_line_per_turn(tmp_path):
    log = TurnLog(str(tmp_path / "logs" / "turns.jsonl"))
    log.write(Event("turn", make_turn()))
    log.write(Event("turn", make_turn(question="second")))
    lines = (tmp_path / "logs" / "turns.jsonl").read_text().splitlines()
    assert [json.loads(x)["question"] for x in lines] == ["How many loans?", "second"]


def test_messages_without_a_turn_are_not_logged(tmp_path):
    log = TurnLog(str(tmp_path / "t.jsonl"))
    log.write(Event("message", message="Cleared."))
    assert not (tmp_path / "t.jsonl").exists()


def test_disabled_log_writes_nothing(tmp_path):
    log = TurnLog(None)
    log.write(Event("turn", make_turn()))
    assert list(tmp_path.iterdir()) == []


def test_an_unwritable_path_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    TurnLog(str(blocker / "sub" / "t.jsonl")).write(Event("turn", make_turn()))  # parent is a file: must not raise


def test_default_path_is_under_logs():
    assert os.path.join("logs", "turns.jsonl") == DEFAULT_LOG_PATH


def test_cli_has_a_no_log_flag():
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "src.cli", "--help"], capture_output=True, text=True).stdout
    assert "--no-log" in out
