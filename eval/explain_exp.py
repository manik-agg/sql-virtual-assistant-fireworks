"""Experiment 6d: async grounded explanation vs ~30-token inline interpretation in the main call."""

import asyncio
import copy
import statistics

from eval.compare import compare
from eval.run import load_sql_cases
from src import agent as agent_mod
from src import llm
from src.agent import Agent, AgentConfig
from src.db import connect_readonly, run_sql
from src.explain import check_consistency, explain

INLINE_NOTE = (
    '\n- Also set "interpretation": one plain sentence (max 30 words) saying how you read the question '
    "and which tables and filters you use.\n"
)


async def main() -> None:
    conn, client, aclient = connect_readonly(), llm.get_client(), llm.get_async_client()
    known = {r[0].lower() for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cases = load_sql_cases("dev") + load_sql_cases("heldout")

    # Arm A: async explanation on top of the normal call
    base = Agent(conn, AgentConfig(), client)
    a_rows, a_turns = [], []
    for c in cases:
        turn = base.turn(c["question"])
        if turn.result is None:
            continue
        text, r = await explain(aclient, turn)
        probs = check_consistency(text, turn, known)
        ok = compare(turn.result.rows, run_sql(conn, c["gold_sql"]).rows, c["gold_sql"])["match"]
        a_rows.append(
            {
                "id": c["id"],
                "text": text,
                "lat": r.latency_s,
                "cost": r.cost_usd,
                "tok": r.completion_tokens,
                "problems": probs,
                "ok": ok,
                "main_s": turn.total_s,
                "main_cost": turn.stats.cost_usd,
            }
        )
        a_turns.append(turn)

    # Arm B: inline interpretation field in the main call
    saved = copy.deepcopy(agent_mod.OUTPUT_JSON_SCHEMA)
    agent_mod.OUTPUT_JSON_SCHEMA["properties"]["interpretation"] = {"type": ["string", "null"]}
    agent_mod.OUTPUT_JSON_SCHEMA["required"].append("interpretation")
    inline = Agent(conn, AgentConfig(), client)
    inline.system_prompt += INLINE_NOTE
    b_rows = []
    for c in cases:
        turn = inline.turn(c["question"])
        ok = bool(turn.result) and compare(turn.result.rows, run_sql(conn, c["gold_sql"]).rows, c["gold_sql"])["match"]
        b_rows.append(
            {
                "id": c["id"],
                "ok": ok,
                "s": turn.total_s,
                "cost": turn.stats.cost_usd,
                "tok": turn.stats.completion_tokens,
            }
        )
    agent_mod.OUTPUT_JSON_SCHEMA.clear()
    agent_mod.OUTPUT_JSON_SCHEMA.update(saved)

    n = len(a_rows)
    print(f"\nARM A async: {n} explanations; deterministic problems in {sum(bool(r['problems']) for r in a_rows)}/{n}")
    print(
        f"  explain latency P50 {statistics.median(r['lat'] for r in a_rows):.2f}s max {max(r['lat'] for r in a_rows):.2f}s; "
        f"tokens out mean {statistics.mean(r['tok'] for r in a_rows):.0f}; cost/query ${statistics.mean(r['cost'] for r in a_rows):.5f}"
    )
    print(
        f"  main call: accuracy {sum(r['ok'] for r in a_rows)}/{n}, P50 {statistics.median(r['main_s'] for r in a_rows):.2f}s, "
        f"${statistics.mean(r['main_cost'] for r in a_rows):.5f}"
    )
    print(
        f"ARM B inline: accuracy {sum(r['ok'] for r in b_rows)}/{len(b_rows)}, P50 {statistics.median(r['s'] for r in b_rows):.2f}s, "
        f"tokens out mean {statistics.mean(r['tok'] for r in b_rows):.0f}, ${statistics.mean(r['cost'] for r in b_rows):.5f}"
    )
    print("\nDev explanations (read these):")
    for r in a_rows[:10]:
        print(f"  {r['id']} {'PROBLEMS ' + str(r['problems']) if r['problems'] else 'ok'}: {r['text']}")


if __name__ == "__main__":  # importing this module must not fire API calls (F-32)
    asyncio.run(main())
