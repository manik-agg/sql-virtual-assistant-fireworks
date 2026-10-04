from conftest import answer

from src.agent import Agent, AgentConfig
from src.session import SWITCH_NOTICE, WINDOW, Session


def session(conn, fake_llm, outputs):
    fake_llm(outputs)
    return Session(Agent(conn, AgentConfig(desc_dir=None), client=object()))


def test_follow_up_keeps_context_and_records_sql(conn, fake_llm):
    s = session(
        conn,
        fake_llm,
        [answer("SELECT COUNT(*) FROM loan"), answer("SELECT COUNT(*) FROM loan WHERE status='A'", "follow_up")],
    )
    s.ask("how many loans")
    ev = s.ask("of those, status A?")
    assert not ev.switched and len(s.window) == 2
    assert "SELECT COUNT(*) FROM loan" in s.context_text()


def test_unrelated_tables_switch_with_notice_and_keep_restores(conn, fake_llm):
    s = session(conn, fake_llm, [answer("SELECT COUNT(*) FROM loan"), answer("SELECT COUNT(*) FROM card")])
    s.ask("loans?")
    ev = s.ask("cards?")
    assert ev.switched and ev.notice == SWITCH_NOTICE and len(s.window) == 1
    assert "restored" in s.handle("/keep").message and len(s.window) == 2


def test_hub_tables_alone_do_not_hide_a_switch(conn, fake_llm):
    # both queries touch district (a hub) but the non-hub tables differ
    s = session(
        conn,
        fake_llm,
        [
            answer("SELECT 1 FROM loan JOIN account USING(account_id) JOIN district USING(district_id)"),
            answer("SELECT 1 FROM client JOIN district USING(district_id)"),
        ],
    )
    s.ask("loans by region")
    assert s.ask("clients by region").switched


def test_model_new_topic_label_with_overlapping_tables_still_switches(conn, fake_llm):
    # F-03 (rewritten): the design says a switch is shown when the model marks new_topic OR the tables do not overlap.
    # The old version of this test asserted the OPPOSITE (that overlap beats the model's label). That was wrong: a missed
    # switch is silent and can carry a stale filter into a new question, while a false switch is visible and /keep undoes it.
    s = session(
        conn, fake_llm, [answer("SELECT COUNT(*) FROM loan"), answer("SELECT SUM(amount) FROM loan", "new_topic")]
    )
    s.ask("loans")
    ev = s.ask("a different question about the same table")
    assert ev.switched and ev.notice == SWITCH_NOTICE and ev.disagreement is True  # label says new, tables say same
    assert len(s.window) == 1 and len(s.stash) == 1


def test_overlapping_tables_and_follow_up_label_do_not_switch(conn, fake_llm):
    s = session(
        conn, fake_llm, [answer("SELECT COUNT(*) FROM loan"), answer("SELECT SUM(amount) FROM loan", "follow_up")]
    )
    s.ask("loans")
    ev = s.ask("and their total amount?")
    assert not ev.switched and len(s.window) == 2


def clarify(question="Which one?"):
    return {
        "turn_type": "new_topic",
        "status": "clarify",
        "sql": None,
        "clarification": {"question": question, "options": ["a", "b"]},
    }


def test_answering_a_clarification_never_triggers_the_switch_notice(conn, fake_llm):
    # F-04: the clarify record has no tables, so there is no previous topic to leave, whatever label the model gives
    for label in ("follow_up", "new_topic", "same_tables_new_question"):
        s = session(conn, fake_llm, [clarify(), answer("SELECT COUNT(*) FROM loan WHERE amount > 300", label)])
        s.ask("how many big loans?")
        ev = s.ask("bigger than 300")
        assert not ev.switched and ev.notice is None, label
        assert len(s.window) == 2, label


def test_first_turn_never_switches(conn, fake_llm):
    s = session(conn, fake_llm, [answer("SELECT COUNT(*) FROM loan", "follow_up")])
    assert not s.ask("loans").switched


def test_switching_back_to_an_earlier_topic_is_another_switch_with_fresh_context(conn, fake_llm):
    s = session(
        conn,
        fake_llm,
        [
            answer("SELECT COUNT(*) FROM loan"),
            answer("SELECT COUNT(*) FROM card"),
            answer("SELECT COUNT(*) FROM loan WHERE status='D'", "new_topic"),
        ],
    )
    s.ask("loans")
    assert s.ask("cards").switched
    ev = s.ask("back to loans: how many are in debt?")
    assert ev.switched and ev.notice == SWITCH_NOTICE and len(s.window) == 1
    assert [r.tables for r in s.stash] == [["card"]]  # /keep would restore the card topic, not the first loans one


def test_followup_reusing_a_previous_result_value_is_not_a_switch(conn, fake_llm):
    # "what type is that card?" only touches `card`, but hard-codes the id returned by the previous turn
    s = session(
        conn,
        fake_llm,
        [
            answer("SELECT card_id, type FROM card WHERE type='gold'"),
            answer("SELECT type FROM card WHERE card_id = 100", "follow_up"),
        ],
    )
    s.conn = conn
    conn.execute("INSERT INTO card VALUES (100,'gold')")
    s.ask("which card is gold?")
    s.window[-1].head_rows = [(100, "gold")]
    ev = s.ask("what type is that card?")
    assert not ev.switched


def test_followup_on_unrelated_tables_without_reuse_still_switches(conn, fake_llm):
    s = session(conn, fake_llm, [answer("SELECT COUNT(*) FROM loan"), answer("SELECT COUNT(*) FROM card", "follow_up")])
    s.ask("loans")
    ev = s.ask("cards")
    assert ev.switched and ev.disagreement


def test_clear_is_undoable_and_aliases_work(conn, fake_llm):
    s = session(conn, fake_llm, [answer("SELECT COUNT(*) FROM loan")])
    s.ask("loans")
    for cmd in ("/clear", "/reset", "clear context"):
        s.handle("/keep")
        assert s.handle(cmd).kind == "message" and len(s.window) == 0
    assert "Nothing" in Session(s.agent).handle("/keep").message


def test_window_is_capped(conn, fake_llm):
    s = session(
        conn,
        fake_llm,
        [answer("SELECT COUNT(*) FROM loan")] + [answer("SELECT COUNT(*) FROM loan", "follow_up")] * (WINDOW + 2),
    )
    for i in range(WINDOW + 3):
        s.ask(f"q{i}")
    assert len(s.window) == WINDOW


def test_out_of_scope_and_errors_do_not_pollute_context(conn, fake_llm):
    import httpx
    import openai

    s = session(
        conn,
        fake_llm,
        [
            {"turn_type": "new_topic", "status": "out_of_scope", "sql": None, "clarification": None},
            openai.APIConnectionError(request=httpx.Request("POST", "http://x")),
        ],
    )
    s.ask("why?")
    assert s.ask("anything").turn.status == "error"
    assert len(s.window) == 0


def test_tiny_results_are_stored_large_ones_are_not(conn, fake_llm):
    s = session(
        conn, fake_llm, [answer("SELECT COUNT(*) AS n FROM loan"), answer("SELECT * FROM loan, card", "follow_up")]
    )
    s.ask("count")
    s.ask("all")
    assert s.window[0].head_rows == [(2,)] and s.window[1].head_rows == []


def test_cancelled_turn_leaves_session_untouched(conn, fake_llm):
    """Ctrl-C while a turn runs: the worker thread may still finish, but must not change the context."""
    s = session(conn, fake_llm, [answer("SELECT COUNT(*) FROM loan")])
    real_turn = s.agent.turn

    def turn_then_cancel(question, context=""):
        result = real_turn(question, context)
        s.cancel()  # user pressed Ctrl-C while the model call was in flight
        return result

    s.agent.turn = turn_then_cancel
    ev = s.ask("loans?")
    assert ev.kind == "message" and ev.message == "Cancelled." and len(s.window) == 0


def test_agent_reports_a_timeout_as_an_error_not_a_crash(conn, fake_llm, monkeypatch):
    monkeypatch.setattr("src.db.TIMEOUT_S", 0.1)
    runaway = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT COUNT(*) FROM r"
    fake_llm([answer(runaway), answer(runaway)])
    ev = Session(Agent(conn, AgentConfig(desc_dir=None), client=object())).ask("count forever")
    assert ev.turn.result is None and "longer than" in ev.turn.error


def test_a_malformed_model_answer_is_not_recorded_and_cannot_switch(conn, fake_llm):
    s = session(conn, fake_llm, [answer("SELECT COUNT(*) FROM loan"), "not json", "still not json"])
    s.ask("loans")
    ev = s.ask("garbled")
    assert ev.turn.status == "error" and not ev.switched and len(s.window) == 1
