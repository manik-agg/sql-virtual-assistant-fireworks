"""Run the agent once on the 10 showcase questions and write results/showcase/answers.json.

The file is the evidence that the evaluation was done properly, not just a list of answers: for every question it records the
question, the SQL the agent generated, a grounded one-sentence answer, the reference SQL, the rows the reference expects and the
rows the agent returned, and the strict verdict. Misses are kept. The reference SQL was written and verified before the first run
and was not changed afterwards.

usage: uv run python -m eval.make_showcase_answers      (needs the financial database and a Fireworks key)
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import date
from pathlib import Path

from eval.compare import compare
from eval.run import SUITES
from src import llm
from src.agent import Agent, AgentConfig
from src.db import connect_readonly, run_sql
from src.explain import answer_sentence
from src.render import summarize_result

OUT = Path("results/showcase/answers.json")
PREVIEW_ROWS = 5  # rows of expected and returned results kept per question


def _rows(rows) -> list[list]:
    return [list(r) for r in rows[:PREVIEW_ROWS]]


def build_showcase(cases: list[dict], conn, turn_fn, sentence_fn) -> dict:
    """Score every case. `turn_fn(question)` runs the agent (it only ever sees the question); `sentence_fn(turn)` summarises it."""
    answers: dict[str, dict] = {}
    latencies: list[float] = []
    cost = 0.0
    for case in cases:
        gold_rows = run_sql(conn, case["gold_sql"]).rows
        turn = turn_fn(case["question"])
        verdict = (
            compare(turn.result.rows, gold_rows, case["gold_sql"], case.get("alternates"))
            if turn.result is not None
            else {"match": False, "shape_differs": False}
        )
        sentence = sentence_fn(turn) if turn.result is not None else None
        answers[case["id"]] = {
            "question": case["question"],
            "tier": case["tier"],
            "status": turn.status,
            "sql": turn.sql,
            "answer": sentence or (summarize_result(turn) if turn.result is not None else None),
            "gold_sql": case["gold_sql"],
            "expected": _rows(gold_rows),
            "returned": _rows(turn.result.rows) if turn.result is not None else None,
            "match": bool(verdict["match"]),
            "shape_differs": bool(verdict["shape_differs"]),
            "first_attempt_ok": turn.first_attempt_ok,
            "retries": turn.retries,
            "latency_s": round(turn.total_s, 2),
            "cost_usd": round(turn.stats.cost_usd, 6),
        }
        latencies.append(turn.total_s)
        cost += turn.stats.cost_usd
    return {
        "summary": {
            "strict_match": sum(a["match"] for a in answers.values()),
            "of": len(answers),
            "first_attempt_ok": sum(bool(a["first_attempt_ok"]) for a in answers.values()),
            "median_latency_s": round(statistics.median(latencies), 2) if latencies else None,
            "total_cost_usd": round(cost, 6),
        },
        "answers": answers,
    }


def main() -> None:
    conn = connect_readonly()
    client = llm.get_client()
    agent = Agent(conn, AgentConfig(), client)
    out = build_showcase(
        SUITES["showcase"].load(),
        conn,
        lambda question: agent.turn(question),  # no session context: every question starts fresh
        lambda turn: answer_sentence(client, turn),
    )
    out["meta"] = {
        "model": agent.config.model,
        "date": date.today().isoformat(),
        "scorer": "eval/compare.py (strict: columns by position, exact strings, one float tolerance, order only if the reference has ORDER BY)",
        "cases_sha256": hashlib.sha256(Path("eval/cases/showcase_10.jsonl").read_bytes()).hexdigest(),
        "note": "Questions and reference SQL were written and verified before the first run and not changed afterwards. Misses are kept.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"meta": out["meta"], "summary": out["summary"], "answers": out["answers"]}, indent=2) + "\n"
    )
    s = out["summary"]
    print(
        f"strict match {s['strict_match']}/{s['of']}, first attempt ok {s['first_attempt_ok']}, median {s['median_latency_s']}s, ${s['total_cost_usd']}"
    )
    for qid, a in out["answers"].items():
        print(f"{qid} {'PASS' if a['match'] else 'MISS'} {a['answer']}")
    conn.close()


if __name__ == "__main__":
    main()
