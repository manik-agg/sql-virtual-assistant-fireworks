"""F-29: a new eval suite is one registry entry plus a case file; no other function needs editing."""

import sqlite3

import pytest

from eval import run as ev


@pytest.fixture
def ctx(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(a INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    calls = []

    def runner(question):
        calls.append(question)
        return {
            "sql": "SELECT COUNT(*) FROM t",
            "status": "answer",
            "executed": True,
            "rows": [(3,)],
            "first_attempt_ok": True,
            "retries": 0,
            "total_s": 0.1,
            "cost_usd": 0.0,
            "tokens_out": 1,
            "error": None,
        }

    yield ev.Context(conn_for=lambda db: conn, runner_for=lambda db: runner, run_conversations=lambda: []), calls
    conn.close()


def test_the_existing_suites_are_registered():
    assert {"heldout", "blind30", "blind15", "shop", "scope", "clarify", "conv"} <= set(ev.SUITES)


def test_a_suite_is_one_entry_and_a_case_file(ctx, monkeypatch):
    context, calls = ctx
    cases = [{"id": f"x{i}", "question": f"q{i}", "gold_sql": "SELECT COUNT(*) FROM t"} for i in range(5)]
    monkeypatch.setitem(ev.SUITES, "five", ev.Suite("sql", lambda: cases))
    recs = ev.run_suite("five", context)
    assert [r["id"] for r in recs] == ["x0", "x1", "x2", "x3", "x4"] and all(r["match"] for r in recs)
    assert calls == ["q0", "q1", "q2", "q3", "q4"]


def test_gold_check_covers_every_registered_sql_suite_whose_database_exists(monkeypatch, tmp_path):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t(a INTEGER)")
    conn.close()
    bad = [{"id": "broken", "question": "q", "gold_sql": "SELECT * FROM missing_table"}]
    monkeypatch.setitem(ev.SUITES, "broken", ev.Suite("sql", lambda: bad, db=str(db)))
    with pytest.raises(sqlite3.Error):
        ev.gold_check()


def test_gold_check_skips_a_suite_whose_database_is_not_downloaded(monkeypatch, capsys):
    monkeypatch.setitem(
        ev.SUITES,
        "absent",
        ev.Suite("sql", lambda: [{"id": "x", "question": "q", "gold_sql": "SELECT 1"}], db="/nonexistent/none.sqlite"),
    )
    ev.gold_check()
    assert "skipped absent" in capsys.readouterr().out


def test_blind_suites_load_only_the_answerable_questions_with_gold_sql():
    for name, minimum in (("blind30", 30), ("blind15", 14)):
        cases = ev.SUITES[name].load()
        assert len(cases) >= minimum and all(c["gold_sql"] and c["question"] and c["id"] for c in cases)
        assert not any(
            c["id"].startswith("x") for c in cases
        )  # the ambiguous questions expect a clarification, not SQL


def test_unknown_suite_is_a_clear_error(ctx):
    with pytest.raises(SystemExit) as e:
        ev.run_suite("nope", ctx[0])
    assert "nope" in str(e.value) and "heldout" in str(e.value)


def test_alternates_are_honoured_by_the_runner(ctx):
    context, _ = ctx
    case = {"id": "a", "question": "q", "gold_sql": "SELECT a, 1 FROM t"}

    def run(c):
        return ev.run_sql_case(c, context.conn_for(None), context.runner_for(None))["match"]

    assert run(case) is False
    assert run({**case, "alternates": [[(3,)]]}) is True
