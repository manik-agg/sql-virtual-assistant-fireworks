import gc
import json
import sqlite3
import warnings

import pytest

from src import llm


@pytest.fixture(autouse=True)
def no_leaked_sqlite_connections():
    """F-20: fail any test that leaves a sqlite connection unclosed (pytest only warns about these, at GC time)."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        yield
        gc.collect()
    leaked = [str(w.message) for w in caught if "unclosed database" in str(w.message)]
    assert not leaked, f"test leaked {len(leaked)} unclosed sqlite connection(s): {leaked[:2]}"


@pytest.fixture()
def conn():
    """Tiny in-memory DB with an FK graph so hub-table logic has something to find."""
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE district (district_id INTEGER PRIMARY KEY, region TEXT);
        CREATE TABLE account (account_id INTEGER PRIMARY KEY, district_id INTEGER REFERENCES district(district_id));
        CREATE TABLE client (client_id INTEGER PRIMARY KEY, district_id INTEGER REFERENCES district(district_id));
        CREATE TABLE loan (loan_id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES account(account_id), status TEXT, amount INTEGER);
        CREATE TABLE card (card_id INTEGER PRIMARY KEY, type TEXT);
        INSERT INTO district VALUES (1,'north'),(2,'south');
        INSERT INTO account VALUES (1,1),(2,2);
        INSERT INTO client VALUES (1,1);
        INSERT INTO loan VALUES (1,1,'A',100),(2,2,'B',300);
        INSERT INTO card VALUES (1,'gold'),(2,'junior');
    """)
    yield c
    c.close()  # F-20: never leave a connection for the garbage collector


class FakeLLM:
    """Replaces llm.chat: returns queued outputs (dicts are JSON-encoded, exceptions are raised)."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, client, messages, **kw):
        self.calls.append(messages)
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return llm.LLMResult(text, 0.01, 100, 0, 20, 0.0001)


def answer(sql, turn_type="new_topic"):
    return {"turn_type": turn_type, "status": "answer", "sql": sql, "clarification": None}


@pytest.fixture()
def fake_llm(monkeypatch):
    def install(outputs):
        fake = FakeLLM(outputs)
        monkeypatch.setattr("src.agent.llm.chat", fake)
        return fake

    return install
