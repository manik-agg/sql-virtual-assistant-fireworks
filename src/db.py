"""Read-only SQLite access with a timeout and row cap."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = "data/financial.sqlite"
MAX_ROWS = 1000
TIMEOUT_S = float(os.getenv("SQL_TIMEOUT_S", "10"))  # env override is also how the timeout is tested end to end


class QueryTimeout(sqlite3.Error):
    """The query ran past TIMEOUT_S and was stopped."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    total_rows: int  # rows returned before truncation to MAX_ROWS (capped at MAX_ROWS + 1)
    truncated: bool
    elapsed_s: float


def sqlite_path(target: str) -> str:
    """Accept a plain path or a SQLAlchemy-style URL: sqlite:///relative.db, sqlite:////abs/path.db."""
    for prefix in ("sqlite:///", "sqlite://"):
        if target.startswith(prefix):
            return target[len(prefix) :] or ":memory:"
    return target


def connect_readonly(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(sqlite_path(db_path)).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}. Run setup.sh first.")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    return conn


def run_sql(conn: sqlite3.Connection, sql: str) -> QueryResult:
    """Execute `sql`; raises sqlite3.Error on failure or timeout."""
    start = time.perf_counter()
    conn.set_progress_handler(lambda: 1 if time.perf_counter() - start > TIMEOUT_S else 0, 100_000)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS + 1)
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e).lower():
            raise QueryTimeout(
                f"Query took longer than {TIMEOUT_S:g}s and was stopped. "
                "Use a more selective filter or avoid scanning the whole table."
            ) from e
        raise
    finally:
        conn.set_progress_handler(None, 0)
    truncated = len(rows) > MAX_ROWS
    return QueryResult(columns, rows[:MAX_ROWS], len(rows), truncated, time.perf_counter() - start)
