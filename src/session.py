"""Session context: sliding window of turn records, topic-switch detection, /keep and /clear."""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass, field

from src.agent import Agent, TurnResult
from src.sqlguard import literals

WINDOW = 5
TINY_RESULT_CELLS = 10  # keep result rows in the record only when this small

SWITCH_NOTICE = (
    "This looks like a new topic, so I'm starting with fresh context. Type /keep to restore the previous context."
)
CLEAR_COMMANDS = {"/clear", "/reset", "clear context"}
HELP_TEXT = (
    'Commands: /clear (also /reset or "clear context") wipes the conversation context; /keep restores the previous '
    "context after a topic switch or /clear; /help shows this; exit or quit ends the session. Ctrl-C cancels a running question."
)


@dataclass
class TurnRecord:
    question: str
    status: str
    turn_type: str
    sql: str | None = None
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    head_rows: list[tuple] = field(default_factory=list)
    clarification: str | None = None
    assumed: str | None = None

    def render(self, i: int) -> str:
        if self.status == "clarify":
            return f"{i}. Q: {self.question} | assistant asked for clarification: {self.clarification}"
        line = (
            f"{i}. Q: {self.question} | SQL: {self.sql} | tables: {', '.join(self.tables)} | "
            f"columns: {', '.join(self.columns)} | rows: {self.row_count}"
        )
        if self.head_rows:  # label values by column so "that client" can be resolved unambiguously
            rows = [dict(zip(self.columns, r)) for r in self.head_rows]
            line += f" | result: {rows}"
        if self.assumed:
            line += f" | assumed: {self.assumed}"
        return line


def hub_tables(conn: sqlite3.Connection) -> set[str]:
    """Tables referenced by FKs from >= 2 other tables. Derived from the FK graph, not hardcoded."""
    refs: dict[str, set[str]] = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in conn.execute(f'PRAGMA foreign_key_list("{t}")'):
            refs.setdefault(row[2].lower(), set()).add(t.lower())
    return {t for t, srcs in refs.items() if len(srcs) >= 2}


@dataclass
class Event:
    """What the CLI should show for one input."""

    kind: str  # "turn" | "message"
    turn: TurnResult | None = None
    notice: str | None = None
    message: str | None = None
    switched: bool = False
    disagreement: bool = False  # model label and sqlglot overlap disagreed


class Session:
    def __init__(self, agent: Agent):
        self.agent = agent
        self._gen = 0  # bumped by cancel(); a turn that finishes after a cancel must not touch state
        self.hubs = hub_tables(agent.conn)
        self.window: deque[TurnRecord] = deque(maxlen=WINDOW)
        self.stash: list[TurnRecord] = []  # segment removed by the last switch or /clear, for /keep

    # ---- context ----
    def context_text(self) -> str:
        if not self.window:
            return ""
        lines = "\n".join(r.render(i) for i, r in enumerate(self.window, 1))
        return "Recent turns (oldest first). The new question may continue them or start a new topic.\n" + lines

    def tables(self) -> set[str]:
        return {t for r in self.window for t in r.tables}

    def _overlaps(self, new_tables: list[str]) -> bool:
        old, new = self.tables(), set(new_tables)
        core_old, core_new = old - self.hubs, new - self.hubs
        if core_old and core_new:  # ignore hub tables (account, district) that join everything
            return bool(core_old & core_new)
        return bool(old & new)

    def _reuses_previous_values(self, sql: str | None) -> bool:
        """True if the new SQL hard-codes a value taken from a recent result (e.g. `client_id = 9340`).

        That is a follow-up on a previous result even when it touches a different table.
        """
        if not sql:
            return False
        prior = {str(v) for rec in self.window for row in rec.head_rows for v in row if len(str(v)) >= 3}
        return bool(prior & set(literals(sql)))

    # ---- commands ----
    def handle(self, text: str) -> Event:
        cmd = text.strip().lower()
        if cmd in CLEAR_COMMANDS:
            self.stash = list(self.window)
            self.window.clear()
            return Event("message", message="Context cleared. Type /keep to restore it.")
        if cmd == "/keep":
            if not self.stash:
                return Event("message", message="Nothing to restore.")
            restored = self.stash + list(self.window)
            self.window = deque(restored[-WINDOW:], maxlen=WINDOW)
            self.stash = []
            return Event("message", message=f"Previous context restored ({len(self.window)} turns).")
        if cmd == "/help":
            return Event("message", message=HELP_TEXT)
        if cmd.startswith("/"):  # an unknown command is answered locally, never sent to the model
            return Event("message", message=f"Unknown command {text.split()[0]}. Type /help.")
        return self.ask(text)

    def cancel(self) -> None:
        """Abandon the turn currently running in a worker thread (Ctrl-C). Its result is discarded."""
        self._gen += 1

    def ask(self, question: str) -> Event:
        gen = self._gen
        turn = self.agent.turn(question, self.context_text())
        if gen != self._gen:
            return Event("message", message="Cancelled.")
        if turn.status == "error":  # API failure: leave the session untouched
            return Event("turn", turn=turn)
        previous_tables = self.tables()
        switched = disagreement = False
        if turn.status == "answer" and previous_tables:
            overlap = self._overlaps(turn.tables) or self._reuses_previous_values(turn.sql)
            # Show the notice when EITHER signal says "new topic" (favour recall: a missed switch is silent and can carry a
            # stale filter into a new question; a false one is visible and /keep undoes it). With no tables in the window
            # (empty, or only a clarification) there is no previous topic to leave, so neither signal applies (F-04).
            switched = turn.turn_type == "new_topic" or not overlap
            disagreement = (turn.turn_type == "new_topic") == overlap  # the two signals disagree
        elif turn.status == "answer":
            turn.turn_type = "new_topic"  # nothing to continue from
        notice = None
        if switched:
            self.stash = list(self.window)
            self.window.clear()
            notice = SWITCH_NOTICE
        self._record(turn)
        return Event("turn", turn=turn, notice=notice, switched=switched, disagreement=disagreement)

    def _record(self, turn: TurnResult) -> None:
        if turn.status == "out_of_scope" or turn.error and turn.status == "answer" and turn.result is None:
            return  # nothing useful for follow-ups
        rec = TurnRecord(turn.question, turn.status, turn.turn_type, turn.sql, turn.tables, assumed=turn.assumed)
        if turn.status == "clarify" and turn.clarification:
            rec.clarification = f"{turn.clarification.question} options: {turn.clarification.options}"
        if turn.result:
            rec.columns, rec.row_count = turn.result.columns, turn.result.total_rows
            if turn.result.rows and len(turn.result.rows) * len(turn.result.columns) <= TINY_RESULT_CELLS:
                rec.head_rows = turn.result.rows
        self.window.append(rec)
