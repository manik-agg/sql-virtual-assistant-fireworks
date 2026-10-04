"""Experiment: draft column descriptions with an LLM from schema + sample rows only (no documentation).

Writes CSVs in the same format as the BIRD ones so the rest of the system can consume them unchanged.
Usage: uv run python -m eval.auto_describe data/financial.sqlite eval/data/auto_descriptions
"""

import csv
import json
import re
import sys
from pathlib import Path

from src import llm
from src.db import connect_readonly

PROMPT = """You document a database for analysts. For every column of the table below, write a short
description of what it means and, for coded values, what each code likely stands for. Use only the
schema and sample rows; if a meaning is not inferable, say "unclear" instead of guessing.

Table: {table}
DDL: {ddl}
Sample rows (first 5): {rows}

Return ONLY JSON: {{"<column>": "<description>", ...}} covering every column."""


def main(db: str, out_dir: str) -> None:
    conn, client = connect_readonly(db), llm.get_client()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, ddl in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall():
        cur = conn.execute(f'SELECT * FROM "{name}" LIMIT 5')
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        r = llm.chat(
            client,
            [{"role": "user", "content": PROMPT.format(table=name, ddl=ddl, rows=json.dumps(rows, default=str))}],
            model=llm.DEFAULT_MODEL,
            max_tokens=3000,
        )
        m = re.search(r"\{.*\}", r.text, re.S)
        desc = json.loads(m.group(0)) if m else {}
        with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["original_column_name", "column_name", "column_description", "data_format", "value_description"]
            )
            for c in cols:
                w.writerow([c, "", desc.get(c, ""), "", ""])
        print(name, f"{len(desc)}/{len(cols)} columns described, ${r.cost_usd:.5f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
