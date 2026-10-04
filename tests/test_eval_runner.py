"""F-14: the eval runner's scoring, summary and comparison logic, with no network."""

import json
from pathlib import Path

import pytest

from eval import run as ev
from src.db import DEFAULT_DB_PATH, connect_readonly


def fake_out(rows, **kw):
    out = {
        "sql": "SELECT 1",
        "status": "answer",
        "executed": True,
        "rows": rows,
        "first_attempt_ok": True,
        "retries": 0,
        "total_s": 1.0,
        "cost_usd": 0.001,
        "tokens_out": 5,
        "error": None,
    }
    out.update(kw)
    return out


@pytest.fixture
def db():
    if not Path(DEFAULT_DB_PATH).exists():
        pytest.skip("data/financial.sqlite not downloaded: run scripts/download_data.sh")
    c = connect_readonly()
    yield c
    c.close()


def test_a_correct_result_matches_and_a_wrong_one_does_not(db):
    case = {"id": "x", "question": "q", "gold_sql": "SELECT COUNT(*) FROM loan"}
    n = db.execute("SELECT COUNT(*) FROM loan").fetchone()[0]
    assert ev.run_sql_case(case, db, lambda q: fake_out([(n,)]))["match"] is True
    assert ev.run_sql_case(case, db, lambda q: fake_out([(n + 1,)]))["match"] is False


def test_an_api_failure_is_a_miss_with_the_reason_not_a_crash(db):
    def boom(q):
        raise ConnectionError("down")

    rec = ev.run_sql_case({"id": "x", "question": "q", "gold_sql": "SELECT 1"}, db, boom)
    assert rec["match"] is False and "ConnectionError: down" in rec["error"]


def test_a_query_that_did_not_execute_is_never_scored_as_a_match(db):
    rec = ev.run_sql_case(
        {"id": "x", "question": "q", "gold_sql": "SELECT 1"}, db, lambda q: fake_out([(1,)], executed=False)
    )
    assert rec["match"] is False


def test_a_shape_difference_is_reported_but_not_a_match(db):
    case = {"id": "x", "question": "q", "gold_sql": "SELECT loan_id, amount FROM loan LIMIT 2"}
    gold = db.execute(case["gold_sql"]).fetchall()
    swapped = [(b, a) for a, b in gold]
    rec = ev.run_sql_case(case, db, lambda q: fake_out(swapped))
    assert rec["match"] is False and rec["shape_differs"] is True


def test_label_cases_score_status_against_expectation():
    cases = [{"id": "a", "question": "q1", "expect": "answer"}, {"id": "b", "question": "q2", "expect": "clarify"}]
    out = ev.run_label_cases(cases, lambda q: {"status": "answer", "sql": "SELECT 1", "total_s": 0.5})
    assert [r["match"] for r in out] == [True, False] and out[0]["sql_emitted"] is True


def test_summary_prints_counts_latency_cost_and_every_miss(capsys):
    recs = [
        {
            "id": "q_001",
            "question": "ok",
            "match": True,
            "total_s": 1.0,
            "cost_usd": 0.001,
            "executed": True,
            "first_attempt_ok": True,
            "shape_differs": False,
        },
        {
            "id": "q_004",
            "question": "bad",
            "match": False,
            "total_s": 3.0,
            "cost_usd": 0.003,
            "executed": True,
            "first_attempt_ok": True,
            "shape_differs": False,
            "sql": "SELECT 2",
        },
    ]
    ev.summarize("retry/dev", recs)
    out = capsys.readouterr().out
    assert "1/2 match" in out and "MISS q_004" in out and "SELECT 2" in out and "$0.00200" in out


def test_compare_lists_gained_and_lost_questions(tmp_path, capsys):
    def result(match_a, match_b):
        return {"suites": {"dev": [[{"id": "q1", "match": match_a}, {"id": "q2", "match": match_b}]]}}

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(result(False, True)))
    b.write_text(json.dumps(result(True, False)))
    ev.do_compare(str(a), str(b))
    assert "gained ['q1'] lost ['q2']" in capsys.readouterr().out


def test_gold_check_passes_on_the_shipped_suites(capsys):
    ev.gold_check()
    assert "gold self-check ok" in capsys.readouterr().out


def test_baseline_runner_executes_the_model_sql(db, monkeypatch):
    from src import llm

    monkeypatch.setattr(
        ev,
        "baseline_sql",
        lambda client, q, model, schema: ("SELECT COUNT(*) FROM loan", llm.LLMResult("", 0.1, 10, 0, 5, 0.001)),
    )
    out = ev.make_runner(ev.PRESETS["b0"], db, None, "m")("how many loans")
    assert (
        out["executed"] is True
        and out["rows"] == [db.execute("SELECT COUNT(*) FROM loan").fetchone()]
        and out["cost_usd"] == 0.001
    )
    monkeypatch.setattr(
        ev,
        "baseline_sql",
        lambda client, q, model, schema: ("SELECT * FROM nope", llm.LLMResult("", 0.1, 10, 0, 5, 0.001)),
    )
    bad = ev.make_runner(ev.PRESETS["b0"], db, None, "m")("q")
    assert bad["executed"] is False and "nope" in bad["error"]


def test_agent_runner_returns_the_fields_the_scorer_needs(db, fake_llm):
    fake_llm(
        [
            {
                "turn_type": "new_topic",
                "status": "answer",
                "reason": "none",
                "sql": "SELECT COUNT(*) FROM loan",
                "assumed": None,
                "concept": None,
                "clarification": None,
            }
        ]
    )
    out = ev.make_runner(ev.PRESETS["retry"], db, object(), "m")("how many loans")
    assert out["status"] == "answer" and out["executed"] is True and out["retries"] == 0 and out["rows"]


def test_conversation_suite_records_one_row_per_turn(db, fake_llm, monkeypatch):
    turns = sum(len(c["turns"]) for c in ev.SUITES["conv"].load())
    replies = [
        {
            "turn_type": "new_topic",
            "status": "answer",
            "reason": "none",
            "sql": "SELECT COUNT(*) FROM loan",
            "assumed": None,
            "concept": None,
            "clarification": None,
        }
    ] * (turns * 3)
    fake_llm(replies)
    recs = ev.run_conversations(db, object(), "m", "medium")
    commands = sum(1 for c in ev.SUITES["conv"].load() for t in c["turns"] if "cmd" in t)
    assert len(recs) == turns and commands > 0 and all("checks" in r and "match" in r for r in recs)


def test_main_runs_a_label_suite_and_saves_the_results(db, fake_llm, monkeypatch, tmp_path, capsys):
    fake_llm(
        [
            {
                "turn_type": "new_topic",
                "status": "answer",
                "reason": "none",
                "sql": "SELECT 1",
                "assumed": None,
                "concept": None,
                "clarification": None,
            }
        ]
        * 50
    )
    monkeypatch.setattr(ev, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(ev.llm, "get_client", lambda: object())
    monkeypatch.setattr("sys.argv", ["evals", "--suite", "scope"])
    ev.main()
    saved = list((tmp_path / "results").glob("*.json"))
    assert len(saved) == 1 and "scope" in json.loads(saved[0].read_text())["suites"]
    assert "match" in capsys.readouterr().out
