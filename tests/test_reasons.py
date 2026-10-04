"""F-05 / F-24 / F-22: structured `reason`, `assumed` and `concept`, fixed refusal messages in code, /help, unknown commands."""

import io

import pytest
from conftest import answer
from pydantic import ValidationError
from rich.console import Console

from src.agent import OUT_OF_SCOPE_MESSAGE, OUTPUT_JSON_SCHEMA, READ_ONLY_MESSAGE, Agent, AgentConfig, parse_output
from src.render import refusal_message, show_turn, summarize_result
from src.schema import closest_names
from src.session import Session


def out(status, reason, sql=None, assumed=None, concept=None, turn_type="new_topic", clarification=None):
    return {
        "turn_type": turn_type,
        "status": status,
        "reason": reason,
        "sql": sql,
        "assumed": assumed,
        "concept": concept,
        "clarification": clarification,
    }


def make(conn, outputs, fake_llm):
    fake = fake_llm(outputs)
    return Agent(conn, AgentConfig(desc_dir=None), client=object()), fake


# ---- the schema and the parsing rules ----------------------------------------------------------------------------------
def test_json_schema_requires_the_three_new_fields_and_lists_the_closed_reason_enum():
    props, required = OUTPUT_JSON_SCHEMA["properties"], OUTPUT_JSON_SCHEMA["required"]
    assert {"reason", "assumed", "concept"} <= set(props) and {"reason", "assumed", "concept"} <= set(required)
    assert props["reason"]["enum"] == ["multi_step_analysis", "not_in_database", "ambiguous", "write_attempt", "none"]


def test_reason_is_forced_to_match_the_status():
    assert (
        parse_output(
            '{"turn_type":"new_topic","status":"answer","reason":"not_in_database","sql":"SELECT 1","clarification":null}'
        ).reason
        == "none"
    )
    clar = '{"question":"Which?","options":["a","b"]}'
    assert (
        parse_output(
            '{"turn_type":"new_topic","status":"clarify","reason":"none","sql":null,"clarification":' + clar + "}"
        ).reason
        == "ambiguous"
    )
    assert (
        parse_output(
            '{"turn_type":"new_topic","status":"out_of_scope","reason":"none","sql":null,"clarification":null}'
        ).reason
        == "multi_step_analysis"
    )


def test_an_unknown_reason_is_invalid_output_not_silently_accepted():
    with pytest.raises(ValidationError):
        parse_output(
            '{"turn_type":"new_topic","status":"out_of_scope","reason":"because","sql":null,"clarification":null}'
        )


# ---- routing to one fixed message per reason ---------------------------------------------------------------------------
def test_multi_step_analysis_gets_the_existing_fixed_message(conn, fake_llm):
    agent, _ = make(conn, [out("out_of_scope", "multi_step_analysis")], fake_llm)
    t = agent.turn("Why is this count so high?")
    assert t.reason == "multi_step_analysis" and refusal_message(t) == OUT_OF_SCOPE_MESSAGE


def test_write_attempt_gets_the_read_only_message_and_never_runs_sql(conn, fake_llm):
    agent, _ = make(conn, [out("out_of_scope", "write_attempt")], fake_llm)
    t = agent.turn("please make every loan amount zero")  # no leading verb, so only the model's reason can catch it
    assert t.refusal == "read_only" and refusal_message(t) == READ_ONLY_MESSAGE and t.result is None


def test_the_write_verb_regex_still_backs_up_the_model(conn, fake_llm):
    agent, _ = make(conn, [out("out_of_scope", "multi_step_analysis")], fake_llm)
    assert agent.turn("Delete all loans").refusal == "read_only"


def test_not_in_database_names_the_concept_and_lists_the_closest_match(conn, fake_llm):
    agent, _ = make(conn, [out("out_of_scope", "not_in_database", concept="loan interest")], fake_llm)
    t = agent.turn("What is the interest on loans?")
    msg = refusal_message(t)
    assert t.refusal == "not_in_database" and t.sql is None
    assert (
        msg.startswith("I couldn't find data for loan interest in this database.")
        and "loan" in t.closest
        and "loan" in msg
    )


def test_not_in_database_without_a_concept_still_gives_a_sensible_message(conn, fake_llm):
    agent, _ = make(conn, [out("out_of_scope", "not_in_database")], fake_llm)
    assert refusal_message(agent.turn("???")) == "I couldn't find that in this database."


def test_not_in_database_with_nothing_close_adds_no_hint():
    class T:  # minimal stand-in for a TurnResult
        refusal, concept, closest = "not_in_database", "mortgages", []

    assert refusal_message(T()) == "I couldn't find data for mortgages in this database."


def test_closest_names_matches_tables_and_columns_and_never_raises(conn):
    assert "loan" in closest_names(conn, "loans") and "loan.amount" in closest_names(conn, "loan amount")
    assert closest_names(conn, "zzzzqqq") == [] and closest_names(conn, "") == []


def test_summarize_result_uses_the_same_messages(conn, fake_llm):
    agent, _ = make(conn, [out("out_of_scope", "not_in_database", concept="employees")], fake_llm)
    assert summarize_result(agent.turn("How many employees?")).startswith("I couldn't find data for employees")


# ---- assumed: printed synchronously, directly under the SQL ----------------------------------------------------------
def test_assumed_is_kept_on_the_turn_and_printed_between_the_sql_and_the_result(conn, fake_llm):
    agent, _ = make(
        conn, [out("answer", "none", "SELECT COUNT(*) AS n FROM loan", assumed='"mortgages" read as loans')], fake_llm
    )
    t = agent.turn("How many mortgages are there?")
    console = Console(file=io.StringIO(), width=100)
    show_turn(console, t)
    text = console.file.getvalue()
    assert t.assumed == '"mortgages" read as loans'
    assert (
        text.index("SELECT COUNT") < text.index("Assumed:") < text.rindex("2")
    )  # SQL, then the assumption, then the table (2 loans)
    assert 'Assumed: "mortgages" read as loans' in text


def test_a_blank_assumed_string_is_treated_as_none(conn, fake_llm):
    agent, _ = make(conn, [out("answer", "none", "SELECT 1", assumed="   ")], fake_llm)
    assert agent.turn("q").assumed is None


def test_assumed_is_remembered_in_the_session_context(conn, fake_llm):
    fake_llm([out("answer", "none", "SELECT COUNT(*) FROM loan", assumed="recent read as the latest 10 rows")])
    s = Session(Agent(conn, AgentConfig(desc_dir=None), client=object()))
    s.ask("recent loans")
    assert "assumed: recent read as the latest 10 rows" in s.context_text()


def test_refusals_are_not_recorded_in_the_session(conn, fake_llm):
    fake_llm([out("out_of_scope", "not_in_database", concept="employees")])
    s = Session(Agent(conn, AgentConfig(desc_dir=None), client=object()))
    s.ask("how many employees")
    assert len(s.window) == 0


# ---- commands (no model call) ----------------------------------------------------------------------------------------
class Boom(Exception):
    pass


def session_that_must_not_call_the_model(conn, monkeypatch):
    def boom(*a, **k):
        raise Boom("the model must not be called for a command")

    monkeypatch.setattr("src.agent.llm.chat", boom)
    return Session(Agent(conn, AgentConfig(desc_dir=None), client=object()))


def test_help_lists_the_commands_without_calling_the_model(conn, monkeypatch):
    msg = session_that_must_not_call_the_model(conn, monkeypatch).handle("/help").message
    for word in ("/clear", "/keep", "/help", "exit", "Ctrl-C"):
        assert word in msg


def test_an_unknown_slash_command_is_answered_locally(conn, monkeypatch):
    ev = session_that_must_not_call_the_model(conn, monkeypatch).handle("/foo bar")
    assert ev.kind == "message" and "Unknown command /foo" in ev.message and "/help" in ev.message


@pytest.mark.parametrize("text", ["exit", "quit", "EXIT", " quit ", "exit.", "Quit."])
def test_exit_aliases(text):
    from src.cli import is_exit

    assert is_exit(text)


def test_ordinary_questions_are_not_exit_words():
    from src.cli import is_exit

    assert not is_exit("how do I exit the building") and not is_exit("")


@pytest.mark.parametrize(
    "question",
    [
        "Set the amount of loan 5000 to zero.",
        "Change the district of account 1 to Prague.",
        "Can you delete all loans?",
        "Mark every loan as paid",
    ],
)
def test_a_declined_write_phrase_gets_the_read_only_message_even_if_the_model_picks_another_reason(
    conn, fake_llm, question
):
    # the model declined these with the wrong reason ("not_in_database" / "multi_step_analysis"); the word check corrects the wording
    agent, _ = make(conn, [out("out_of_scope", "not_in_database", concept="loan.amount update")], fake_llm)
    t = agent.turn(question)
    assert t.refusal == "read_only" and refusal_message(t) == READ_ONLY_MESSAGE and t.reason == "write_attempt"


def test_a_refinement_the_model_answers_is_never_turned_into_a_refusal(conn, fake_llm):
    agent, fake = make(conn, [answer("SELECT COUNT(*) FROM loan", "follow_up")], fake_llm)
    t = agent.turn("Change the region to Prague")
    assert t.status == "answer" and t.refusal is None  # the word check runs only after the model has declined
