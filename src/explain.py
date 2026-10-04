"""Async post-result explanation, grounded only in the executed SQL and result columns."""

from __future__ import annotations

import os
import re

from src import llm
from src.agent import TurnResult
from src.render import fmt

EXPLAIN_MODEL = os.getenv("FIREWORKS_EXPLAIN_MODEL", "accounts/fireworks/models/gpt-oss-120b")
EXPLAIN_EFFORT = os.getenv("FIREWORKS_EXPLAIN_EFFORT", "low")

PROMPT = """Explain in at most 2 short plain-English sentences how the SQL below answers the question.
Describe ONLY what the SQL does: the tables joined, every filter (with its value), and the aggregation.
Do not add facts that are not in the SQL. Do not restate the result numbers.
Plain text only: no markdown, no bold, no backticks.

Question: {question}
SQL: {sql}
Tables: {tables}
Result columns: {columns} ({rows} rows)"""


def build_prompt(turn: TurnResult) -> str:
    res = turn.result
    assert res is not None  # only answered turns with a result are explained
    return PROMPT.format(
        question=turn.question,
        sql=turn.sql,
        tables=", ".join(turn.tables),
        columns=", ".join(res.columns),
        rows=res.total_rows,
    )


async def explain(client, turn: TurnResult) -> tuple[str, llm.LLMResult]:
    """Cancellable: cancelling the awaiting task aborts the HTTP request."""
    r = await llm.achat(
        client,
        [{"role": "user", "content": build_prompt(turn)}],
        model=EXPLAIN_MODEL,
        reasoning_effort=EXPLAIN_EFFORT,
        max_tokens=llm.MAX_TOKENS,
    )
    return r.text.strip(), r


AUDIT_PROMPT = """Question: {question}
SQL: {sql}
Schema notes for the tables used:
{schema}

Compare the question with the SQL. Name every term in the question that the SQL answers with a different column, concept or definition than the question literally asked for (use the schema notes: a column whose description says "average salary" is not "income"; "customers" are the table `client`). Ordinary joins, counting distinct rows, formatting and unit choices are not assumptions. If every term maps directly, answer EXACT.
Reply with exactly one line: EXACT, or ASSUMES: <the term and what it was read as, at most 15 words>"""
MAX_AUDIT_CHARS = 160  # the audit note is one short line


def parse_audit_reply(reply: str) -> str | None:
    """The assumption text from an `ASSUMES: ...` last line, or None for EXACT, empty or unparsable replies (never guess)."""
    lines = [line.strip() for line in (reply or "").strip().splitlines() if line.strip()]
    match = re.match(r"(?i)^assumes\s*:\s*(.+)$", lines[-1]) if lines else None
    return match.group(1).strip()[:MAX_AUDIT_CHARS] if match else None


async def audit_answer(client, turn: TurnResult, blocks: dict[str, str]) -> tuple[str | None, llm.LLMResult]:
    """Ask a separate, cheap call whether the SQL answers a related question instead of the one asked (F-05 follow-up).

    Runs after the result is shown and is cancelled with the explanation. It cannot change the SQL or the result.
    """
    schema = "\n\n".join(blocks[t] for t in turn.tables if t in blocks)
    prompt = AUDIT_PROMPT.format(question=turn.question, sql=turn.sql, schema=schema)
    r = await llm.achat(
        client,
        [{"role": "user", "content": prompt}],
        model=EXPLAIN_MODEL,
        reasoning_effort=EXPLAIN_EFFORT,
        max_tokens=llm.MAX_TOKENS,
    )
    return parse_audit_reply(r.text), r


def check_consistency(text: str, turn: TurnResult, known_tables: set[str]) -> list[str]:
    """Deterministic contradiction checks; returns a list of problems (empty = consistent)."""
    problems: list[str] = []
    lowered = text.lower()
    used = {t.lower() for t in turn.tables}
    for t in known_tables:
        # Only "table X" / "join X" / "from X" phrasing counts: bare words like "order" or "account"
        # are ordinary English and produced false positives.
        pattern = rf"(?:\btable\s+|\bjoins?\s+(?:to\s+)?|\bfrom\s+)[`'\"*]*{re.escape(t)}\b|\b{re.escape(t)}\s+table\b"
        if t not in used and re.search(pattern, lowered):
            problems.append(f"mentions table '{t}' that the SQL does not use")
    sql_lits = set(re.findall(r"'([^']*)'", turn.sql or ""))
    allowed_numbers = set(re.findall(r"\d+(?:\.\d+)?", turn.sql or ""))
    for lit in sql_lits:
        allowed_numbers.update(re.findall(r"\d+(?:\.\d+)?", lit))
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        n = n.replace(",", "")
        if n not in allowed_numbers:
            problems.append(f"number {n} does not appear in the SQL")
    return problems


ANSWER_PROMPT = """Answer the question in one or two plain sentences using ONLY the result below. If the result is a single
value, state that value in a sentence that answers the question and do not mention rows or columns.
Copy numbers and names exactly as shown in the result; do not compute anything new. For a list, say how many
rows there are and describe each shown row using its column names (for example "client 9340 (M): 590,820"). Plain text only: no markdown.

Question: {question}
Columns: {columns}
The full result has {total} rows. Only the first {shown} are listed here; if you mention how many
rows there are, say {total}, not {shown}.
Rows: {rows}"""


def answer_sentence(client, turn: TurnResult, max_rows: int = 5) -> str | None:
    """A grounded one-sentence answer summary. Returns None if any number is not in the result."""
    res = turn.result
    if res is None:
        return None
    shown = res.rows[:max_rows]
    pretty = [[fmt(v, c) for v, c in zip(r, res.columns)] for r in shown]
    prompt = ANSWER_PROMPT.format(
        question=turn.question, columns=", ".join(res.columns), total=res.total_rows, shown=len(shown), rows=pretty
    )
    for attempt in range(3):  # later attempts name the headline value, since a bare row count is a common slip
        hint = "" if attempt == 0 else f"\nThe sentence MUST state this value: {', '.join(pretty[0])}."
        text = llm.chat(
            client, [{"role": "user", "content": prompt + hint}], model=EXPLAIN_MODEL, reasoning_effort=EXPLAIN_EFFORT
        ).text.strip()
        if _grounded(text, turn, shown, pretty):
            return text
    return None


def _grounded(text: str, turn: TurnResult, shown: list, pretty: list) -> bool:
    res = turn.result
    assert res is not None  # only answered turns with a result are checked
    if len(text.split()) < 4:  # a bare number is not a sentence
        return False
    if res.total_rows == 1 and len(res.columns) == 1 and re.search(r"\brows?\b|\bcolumns?\b", text, re.IGNORECASE):
        return False  # "There is 1 row: count = 18" is not an answer
    allowed = set(
        re.findall(
            r"\d+(?:\.\d+)?",
            f"{turn.question} {turn.sql} {res.total_rows} "
            + " ".join(str(v) for r in shown for v in r)
            + " "
            + " ".join(c for r in pretty for c in r).replace(",", ""),
        )
    )
    allowed |= {f"{float(a):.2f}" for a in allowed}  # 4190.664 may be shown as 4,190.66
    # the headline value must be stated: a sentence that only repeats the row count is not an answer
    plain = text.replace(",", "")
    for v, c in zip(shown[0], res.columns):
        is_measure = isinstance(v, (int, float)) and not (c.lower() == "id" or c.lower().endswith("_id"))
        if is_measure and fmt(v, c).replace(",", "") not in plain:
            return False
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        n = n.replace(",", "").rstrip(".")
        if n not in allowed and str(round(float(n))) not in allowed:
            return False
    return True
