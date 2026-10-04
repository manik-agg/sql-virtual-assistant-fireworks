"""Eval runner. Examples:
uv run evals --gold-check
uv run evals --config b0 --suite heldout,blind30 --repeats 3
uv run evals --compare eval/results/a.json eval/results/b.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from eval.compare import compare
from src import llm
from src.agent import Agent, AgentConfig, baseline_sql
from src.db import DEFAULT_DB_PATH, connect_readonly, run_sql, sqlite_path
from src.schema import DESC_DIR, build_schema_text
from src.session import Session

CASES = Path("eval/cases")
RESULTS = Path("eval/results")

# Each preset changes ONE thing relative to the previous, so gains are attributable.
PRESETS: dict[str, dict] = {
    "b0": {"mode": "baseline", "schema": None},
    "b1": {"mode": "baseline", "schema": "ddl"},
    "s_ddl": {"mode": "agent", "schema_level": "ddl", "retry": False},
    "s_desc": {"mode": "agent", "schema_level": "desc", "retry": False},
    "s_values": {"mode": "agent", "schema_level": "values", "retry": False},
    "retry": {"mode": "agent", "schema_level": "values", "retry": True},
    # same as "retry" but WITHOUT the column-description CSVs: what a connection-string-only user gets
    # descriptions drafted by an LLM from schema + 5 sample rows (eval/auto_describe.py), no human input
    "autodesc": {"mode": "agent", "schema_level": "values", "retry": True, "desc_dir": "eval/data/auto_descriptions"},
    "nodesc": {"mode": "agent", "schema_level": "values", "retry": True, "desc_dir": None},
}
TAXONOMY = json.loads((CASES / "taxonomy.json").read_text())


def load_jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (CASES / f"{name}.jsonl").read_text().splitlines() if line.strip()]


INDEPENDENT = Path("eval/independent")


def blind_cases(filename: str) -> list[dict]:
    """The answerable questions of an independent question set (the ambiguous ones expect a clarification, not SQL)."""
    rows = json.loads((INDEPENDENT / filename).read_text())
    return [
        {"id": q["id"], "question": q["question"], "gold_sql": q["gold_sql"]}
        for q in rows
        if q.get("gold_sql") and not q.get("expect_status")
    ]


@dataclass(frozen=True)
class Suite:
    """One eval suite. kind: "sql" (score the result against gold_sql), "label" (score `status` against `expect`),
    "conv" (scripted conversations). db: path of the SQLite file the cases run on (None = the main database)."""

    kind: str
    load: Callable[[], list[dict]]
    db: str | None = None
    desc_dir: str | None = None  # column descriptions for a non-main database


# To add a suite: put its cases in eval/cases/<name>.jsonl and add one line here. Nothing else needs editing.
SUITES: dict[str, Suite] = {
    "blind30": Suite(
        "sql", lambda: blind_cases("blind_30.json")
    ),  # written without seeing any output, never tuned against
    "blind15": Suite("sql", lambda: blind_cases("blind_15.json")),
    "heldout": Suite("sql", lambda: load_jsonl("heldout_sql")),
    "showcase": Suite(
        "sql", lambda: load_jsonl("showcase_10")
    ),  # 10 new questions, reference SQL fixed before the first run
    "shop": Suite(
        "sql", lambda: load_jsonl("shop_sql"), db="eval/data/shop.sqlite", desc_dir="eval/data/shop_descriptions"
    ),
    "scope": Suite("label", lambda: load_jsonl("scope")),
    "clarify": Suite("label", lambda: load_jsonl("clarify")),
    "conv": Suite("conv", lambda: load_jsonl("conversations")),
}


@dataclass
class Context:
    """What a suite needs to run: a connection and a runner per database, and the conversation driver."""

    conn_for: Callable[[str | None], object]
    runner_for: Callable[[str | None], Callable[[str], dict]]
    run_conversations: Callable[[], list[dict]]


def run_suite(name: str, ctx: Context) -> list[dict]:
    if name not in SUITES:
        raise SystemExit(f"unknown suite '{name}'; available: {', '.join(SUITES)}")
    suite = SUITES[name]
    if suite.kind == "conv":
        return ctx.run_conversations()
    if suite.kind == "label":
        return run_label_cases(suite.load(), ctx.runner_for(None))
    conn, runner = ctx.conn_for(suite.db), ctx.runner_for(suite.db)
    # sequential: parallel calls would distort per-query latency
    return [run_sql_case(c, conn, runner) for c in suite.load()]


def run_sql_case(case: dict, conn, runner) -> dict:
    gold = run_sql(conn, case["gold_sql"]).rows
    rec = {
        "id": case["id"],
        "question": case["question"],
        "sql": None,
        "first_attempt_ok": False,
        "executed": False,
        "match": False,
        "shape_differs": False,
        "status": None,
        "total_s": None,
        "cost_usd": 0.0,
        "tokens_out": 0,
        "error": None,
    }
    try:
        out = runner(case["question"])
    except Exception as e:  # network/API failure counts as a miss, not a crash
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec
    rec.update(
        {
            k: out.get(k)
            for k in (
                "sql",
                "status",
                "total_s",
                "cost_usd",
                "tokens_out",
                "error",
                "first_attempt_ok",
                "executed",
                "retries",
                "rows",
            )
        }
    )
    if rec["executed"]:
        rec.update(compare(out["rows"], gold, case["gold_sql"], case.get("alternates")))
    rec.pop("rows", None)
    return rec


def make_runner(preset: dict, conn, client, model: str, desc_dir: str | None = DESC_DIR):
    desc_dir = preset.get("desc_dir", desc_dir)
    if preset["mode"] == "baseline":
        schema = build_schema_text(conn, preset["schema"], desc_dir) if preset["schema"] else None

        def run(question: str) -> dict:
            t = time.perf_counter()
            sql, r = baseline_sql(client, question, model, schema)
            out = {"sql": sql, "status": "answer", "tokens_out": r.completion_tokens, "cost_usd": r.cost_usd}
            try:
                res = run_sql(conn, sql)
                out.update(executed=True, first_attempt_ok=True, rows=res.rows)
            except Exception as e:
                out.update(executed=False, first_attempt_ok=False, error=str(e))
            out["total_s"] = time.perf_counter() - t
            return out

        return run

    cfg = AgentConfig(
        model=model,
        schema_level=preset["schema_level"],
        retry=preset["retry"],
        desc_dir=desc_dir,
        reasoning_effort=preset.get("reasoning_effort", "medium"),
    )
    agent = Agent(conn, cfg, client)

    def run(question: str) -> dict:
        r = agent.turn(question)
        return {
            "sql": r.sql,
            "status": r.status,
            "executed": r.result is not None,
            "rows": r.result.rows if r.result else [],
            "first_attempt_ok": r.first_attempt_ok,
            "retries": r.retries,
            "total_s": r.total_s,
            "cost_usd": r.stats.cost_usd,
            "tokens_out": r.stats.completion_tokens,
            "error": r.error,
        }

    return run


def run_label_cases(cases: list[dict], runner) -> list[dict]:
    """scope / clarify suites: score the `status` field against `expect`."""
    recs = []
    for case in cases:
        out = runner(case["question"])
        recs.append(
            {
                "id": case["id"],
                "question": case["question"],
                "expect": case["expect"],
                "status": out["status"],
                "sql_emitted": bool(out.get("sql")),
                "match": out["status"] == case["expect"],
                "total_s": out["total_s"],
            }
        )
    return recs


def run_conversations(conn, client, model: str, reasoning_effort: str | None) -> list[dict]:
    """Drive Session directly (no CLI). One record per turn; `match` covers every asserted field."""
    agent = Agent(conn, AgentConfig(model=model, reasoning_effort=reasoning_effort or "medium"), client)
    recs = []
    for conv in SUITES["conv"].load():
        session = Session(agent)
        for i, t in enumerate(conv["turns"]):
            rec = {"id": f"{conv['id']}.{i}", "question": t.get("say") or t.get("cmd"), "checks": {}}
            if "cmd" in t:
                session.handle(t["cmd"])
            else:
                ev = session.handle(t["say"])
                turn = ev.turn
                rec.update(
                    status=turn.status,
                    turn_type=turn.turn_type,
                    switched=ev.switched,
                    sql=turn.sql,
                    total_s=turn.total_s,
                    cost_usd=turn.stats.cost_usd,
                    disagreement=ev.disagreement,
                )
                if "turn_type" in t:
                    rec["checks"]["turn_type"] = turn.turn_type == t["turn_type"]
                if "expect_notice" in t:
                    rec["checks"]["notice"] = ev.switched == t["expect_notice"]
                elif i > 0 and t.get("turn_type") in ("follow_up", "same_tables_new_question"):
                    rec["checks"]["no_false_switch"] = not ev.switched
                if "expect_status" in t:
                    rec["checks"]["status"] = turn.status == t["expect_status"]
                if "final_sql_gold" in t:
                    gold = run_sql(conn, t["final_sql_gold"]).rows
                    rec["checks"]["result"] = bool(
                        turn.result and compare(turn.result.rows, gold, t["final_sql_gold"])["match"]
                    )
            if "expect_context_len" in t:
                rec["checks"]["context_len"] = len(session.window) == t["expect_context_len"]
            rec["match"] = all(rec["checks"].values())
            recs.append(rec)
    return recs


def summarize(name: str, recs: list[dict]) -> None:
    n = len(recs)
    lat = sorted(r["total_s"] for r in recs if r.get("total_s"))
    recs = [{**r, "cost_usd": r.get("cost_usd") or 0.0} for r in recs]

    def p(q: float) -> float:
        return lat[min(len(lat) - 1, int(q * len(lat)))] if lat else float("nan")

    print(f"\n== {name}: {sum(r['match'] for r in recs)}/{n} match")
    if recs and "executed" in recs[0]:
        print(
            f"   executed {sum(bool(r['executed']) for r in recs)}/{n}, first-attempt ok "
            f"{sum(bool(r['first_attempt_ok']) for r in recs)}/{n}, "
            f"shape-differs {sum(bool(r['shape_differs']) for r in recs)}"
        )
    print(
        f"   latency P50 {p(0.5):.2f}s P95 {p(0.95):.2f}s, cost/query ${statistics.mean(r['cost_usd'] for r in recs):.5f}"
    )
    for r in recs:
        if not r["match"]:
            label = TAXONOMY.get(r["id"], "UNLABELED")
            print(
                f"   MISS {r['id']} [{label[:60]}]: {str(r.get('question'))[:60]} -> {r.get('sql') or r.get('status')} {r.get('checks') or ''} {r.get('error') or ''}"
            )


def do_compare(a: str, b: str) -> None:
    ra, rb = json.loads(Path(a).read_text()), json.loads(Path(b).read_text())
    for suite in ra["suites"]:
        pa = {r["id"]: r["match"] for r in ra["suites"][suite][0]}
        pb = {r["id"]: r["match"] for r in rb["suites"].get(suite, [[]])[0]}
        gained = [i for i in pa if not pa[i] and pb.get(i)]
        lost = [i for i in pa if pa[i] and not pb.get(i, True)]
        print(f"{suite}: gained {gained} lost {lost}")


def gold_check() -> None:
    for name, suite in SUITES.items():
        if suite.kind != "sql":
            continue
        path = suite.db or DEFAULT_DB_PATH
        if not Path(sqlite_path(path)).exists():
            print(f"skipped {name}: {path} not found (run scripts/download_data.sh for the financial database)")
            continue
        db = connect_readonly(path)
        try:
            for c in suite.load():
                g = run_sql(db, c["gold_sql"]).rows
                assert compare(g, g, c["gold_sql"], c.get("alternates"))["match"], f"{name}/{c['id']}"
        finally:
            db.close()
    print("gold self-check ok (every gold SQL executes and matches itself)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=PRESETS, default="retry", help="what to evaluate (default: the shipped agent)")
    ap.add_argument("--suite", default="heldout", help="comma list or 'all'; one of: " + ", ".join(SUITES))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--reasoning-effort")
    ap.add_argument("--shop-docs", action="store_true", help="give the shop DB its (minimal) description CSV")
    ap.add_argument("--gold-check", action="store_true")
    ap.add_argument("--compare", nargs=2)
    args = ap.parse_args()
    if args.gold_check:
        return gold_check()
    if args.compare:
        return do_compare(*args.compare)

    preset = dict(PRESETS[args.config])
    if args.reasoning_effort:
        preset["reasoning_effort"] = args.reasoning_effort
    names = list(SUITES) if args.suite == "all" else args.suite.split(",")
    for n in names:
        if n not in SUITES:
            raise SystemExit(f"unknown suite '{n}'; available: {', '.join(SUITES)}")
    client = llm.get_client()
    conns: dict[str | None, object] = {}
    runners: dict[str | None, Callable[[str], dict]] = {}

    def conn_for(db: str | None):
        if db not in conns:
            conns[db] = connect_readonly(db) if db else connect_readonly()
        return conns[db]

    def runner_for(db: str | None):
        if db not in runners:
            desc = next(
                (s.desc_dir if args.shop_docs else None for s in SUITES.values() if s.db == db and db), DESC_DIR
            )
            runners[db] = make_runner(preset, conn_for(db), client, args.model, desc)
        return runners[db]

    ctx = Context(
        conn_for,
        runner_for,
        lambda: run_conversations(conn_for(None), client, args.model, preset.get("reasoning_effort")),
    )
    all_suites: dict[str, list[list[dict]]] = {}
    for suite in names:
        runs = []
        for _ in range(args.repeats):
            recs = run_suite(suite, ctx)
            if suite == "conv":
                lab = [r for r in recs if "turn_type" in r["checks"]]
                print(f"turn_type accuracy {sum(r['checks']['turn_type'] for r in lab)}/{len(lab)}")
            runs.append(recs)
            summarize(f"{args.config}/{suite}", recs)
        all_suites[suite] = runs
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{args.config}_{args.model.split('/')[-1]}_{int(time.time())}.json"
    out.write_text(
        json.dumps(
            {"config": args.config, "preset": preset, "model": args.model, "suites": all_suites}, indent=1, default=str
        )
    )
    print(f"\nsaved {out}")
    for c in conns.values():
        c.close()


if __name__ == "__main__":
    main()
