"""Regression tests for F-01: check_consistency raised NameError on every call (stray block pasted into it)."""

from src.agent import TurnResult
from src.explain import check_consistency

TABLES = {"account", "district", "loan", "order"}


def turn(sql: str, tables: list[str]) -> TurnResult:
    return TurnResult("answer", "new_topic", "q", sql=sql, tables=tables)


def test_consistent_explanation_has_no_problems():
    t = turn(
        "SELECT COUNT(*) FROM account a JOIN district d ON a.district_id = d.district_id WHERE d.A3 = 'Prague'",
        ["account", "district"],
    )
    text = "It joins the account table to the district table and counts accounts where the region is Prague."
    assert check_consistency(text, t, TABLES) == []


def test_mentioning_a_table_the_sql_does_not_use_is_caught():
    t = turn("SELECT COUNT(*) FROM loan", ["loan"])
    problems = check_consistency("It counts rows by joining the loan table with the account table.", t, TABLES)
    assert any("account" in p for p in problems)


def test_a_number_that_is_not_in_the_sql_is_caught():
    t = turn("SELECT COUNT(*) FROM loan WHERE duration = 24", ["loan"])
    problems = check_consistency("It counts loans with a duration of 60 months.", t, TABLES)
    assert any("60" in p for p in problems)


def test_ordinary_english_words_that_are_table_names_are_not_flagged():
    t = turn("SELECT amount FROM loan ORDER BY amount DESC LIMIT 1", ["loan"])
    assert check_consistency("It orders the loans by amount and takes the top account.", t, TABLES) == []
