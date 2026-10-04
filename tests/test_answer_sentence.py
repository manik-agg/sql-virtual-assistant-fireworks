from src import llm
from src.agent import TurnResult
from src.db import QueryResult
from src.explain import answer_sentence
from src.render import fmt


def turn(rows, cols=("n",)):
    return TurnResult(
        "answer",
        "new_topic",
        "How many clients are there?",
        sql="SELECT COUNT(*) AS n FROM client",
        result=QueryResult(list(cols), rows, len(rows), False, 0.0),
    )


def reply(monkeypatch, *texts):
    queue = list(texts)
    monkeypatch.setattr("src.explain.llm.chat", lambda *a, **k: llm.LLMResult(queue.pop(0), 0.0, 0, 0, 0, 0.0))


def test_grounded_sentence_is_accepted(monkeypatch):
    reply(monkeypatch, "There are 554 clients.")
    assert answer_sentence(None, turn([(554,)])) == "There are 554 clients."


def test_sentence_that_only_repeats_the_row_count_is_rejected(monkeypatch):
    # regression: the model once answered "1 distinct client" for a result of 554
    reply(monkeypatch, "There is 1 distinct client.", "Still 1 client.", "Just 1 client here.")
    assert answer_sentence(None, turn([(554,)])) is None


def test_invented_number_is_rejected_then_retry_can_fix_it(monkeypatch):
    reply(monkeypatch, "There are 554 clients, up from 400.", "There are 554 clients.")
    assert answer_sentence(None, turn([(554,)])) == "There are 554 clients."


def test_ids_are_not_formatted_with_separators_but_measures_are():
    assert fmt(9340, "client_id") == "9340" and fmt(590820, "total_loan_amount") == "590,820"
    assert fmt(4190.664, "avg") == "4,190.66"


def test_a_bare_number_is_not_a_sentence(monkeypatch):
    reply(monkeypatch, "554", "554", "554")
    assert answer_sentence(None, turn([(554,)])) is None


def test_single_value_answer_must_not_talk_about_rows(monkeypatch):
    reply(monkeypatch, "There is 1 row: count is 554.", "The result has 1 row, 554.", "There are 554 clients.")
    assert answer_sentence(None, turn([(554,)])) == "There are 554 clients."
