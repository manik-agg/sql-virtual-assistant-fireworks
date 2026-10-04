"""The showcase set: 10 new questions with reference SQL written and checked BEFORE the agent saw them, and the answers file that
records, per question, what was returned against what was expected."""

import json
import re
import sqlite3
from pathlib import Path

import pytest

from eval import run as ev
from eval.make_showcase_answers import build_showcase
from src.agent import Stats, TurnResult
from src.db import DEFAULT_DB_PATH, QueryResult, connect_readonly
from src.sqlguard import parse_select

ROOT = Path(__file__).resolve().parent.parent
CASES = ev.SUITES["showcase"].load()


def shingles(text: str, n: int = 6) -> set[str]:
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(w[i : i + n]) for i in range(len(w) - n + 1)}


def other_questions() -> list[str]:
    out = [c["question"] for suite in ("heldout", "shop", "blind30", "blind15") for c in ev.SUITES[suite].load()]
    for name in (
        "behavior_cases.json",
        "proxy_cases.json",
        "reason_cases.json",
        "reason_cases_confirm.json",
        "write_cases.json",
    ):
        data = json.loads((ROOT / "eval" / "independent" / name).read_text())
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
    return out


def test_the_set_has_ten_unique_questions_across_three_tiers():
    assert [c["id"] for c in CASES] == [f"s_{i:02d}" for i in range(1, 11)]
    assert len({c["question"] for c in CASES}) == 10
    tiers = [c["tier"] for c in CASES]
    assert set(tiers) == {1, 2, 3} and tiers == sorted(tiers)


def test_every_reference_query_is_a_single_read_only_select():
    for c in CASES:
        parse_select(c["gold_sql"])


def test_no_question_repeats_the_wording_of_any_other_question_set():
    others = set().union(*(shingles(q) for q in other_questions()))
    clashes = [(c["id"], s) for c in CASES for s in shingles(c["question"]) if s in others]
    assert not clashes, clashes


def test_reference_queries_return_real_non_trivial_answers():
    if not Path(DEFAULT_DB_PATH).exists():
        pytest.skip("data/financial.sqlite not downloaded: run scripts/download_data.sh")
    conn = connect_readonly()
    try:
        for c in CASES:
            rows = conn.execute(c["gold_sql"]).fetchall()
            assert rows and rows != [(0,)] and rows != [(None,)], c["id"]  # no empty, zero or NULL "answers"
    finally:
        conn.close()


# ---- the answers file ---------------------------------------------------------------------------------------------------
@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t(a INTEGER)")
    c.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    yield c
    c.close()


def fake_turn(rows, sql="SELECT COUNT(*) FROM t", status="answer"):
    res = QueryResult(["n"], rows, len(rows), False, 0.0) if rows is not None else None
    return TurnResult(
        status=status,
        turn_type="new_topic",
        question="q",
        sql=sql,
        tables=["t"],
        result=res,
        total_s=1.0,
        stats=Stats(cost_usd=0.0002),
    )


def test_build_showcase_scores_each_case_against_gold_and_records_both_sides(conn):
    cases = [
        {"id": "a", "tier": 1, "question": "q1", "gold_sql": "SELECT COUNT(*) FROM t"},
        {"id": "b", "tier": 2, "question": "q2", "gold_sql": "SELECT COUNT(*) FROM t"},
    ]
    asked = []
    replies = {"q1": fake_turn([(3,)]), "q2": fake_turn([(4,)])}

    def turn_fn(question):
        asked.append(question)
        return replies[question]

    out = build_showcase(cases, conn, turn_fn, lambda turn: "The count is 3.")
    assert asked == ["q1", "q2"]  # the agent only ever receives the question
    assert out["summary"]["strict_match"] == 1 and out["summary"]["of"] == 2
    a, b = out["answers"]["a"], out["answers"]["b"]
    assert a["match"] is True and b["match"] is False
    assert a["expected"] == [[3]] and b["returned"] == [[4]] and b["expected"] == [[3]]
    assert a["gold_sql"] == "SELECT COUNT(*) FROM t" and a["answer"] == "The count is 3."


def test_a_turn_without_a_result_is_a_miss_not_a_crash(conn):
    cases = [{"id": "a", "tier": 1, "question": "q1", "gold_sql": "SELECT COUNT(*) FROM t"}]
    out = build_showcase(cases, conn, lambda q: fake_turn(None, sql=None, status="clarify"), lambda turn: None)
    assert out["answers"]["a"]["match"] is False and out["answers"]["a"]["status"] == "clarify"
    assert out["answers"]["a"]["answer"] is None


def test_summary_reports_latency_and_cost(conn):
    cases = [{"id": str(i), "tier": 1, "question": "q", "gold_sql": "SELECT COUNT(*) FROM t"} for i in range(3)]
    out = build_showcase(cases, conn, lambda q: fake_turn([(3,)]), lambda turn: "ok")
    s = out["summary"]
    assert s["median_latency_s"] == 1.0 and s["total_cost_usd"] == pytest.approx(0.0006) and s["first_attempt_ok"] == 3


def perturb(rows):
    """A deliberately wrong answer: change the first cell of the first row."""
    first = list(rows[0])
    v = first[0]
    first[0] = (v or 0) + 1 if isinstance(v, (int, float)) or v is None else str(v) + "x"
    return [tuple(first)] + list(rows[1:])


def test_the_scorer_rejects_a_perturbed_reference_for_every_question():
    # negative control: the set can fail. If a wrong answer were accepted, a perfect score would mean nothing
    if not Path(DEFAULT_DB_PATH).exists():
        pytest.skip("data/financial.sqlite not downloaded: run scripts/download_data.sh")
    from eval.compare import compare

    conn = connect_readonly()
    try:
        for c in CASES:
            gold = conn.execute(c["gold_sql"]).fetchall()
            assert compare(gold, gold, c["gold_sql"])["match"], c["id"]
            assert not compare(perturb(gold), gold, c["gold_sql"])["match"], c["id"]
    finally:
        conn.close()
