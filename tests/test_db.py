import sqlite3

import pytest

from src.db import connect_readonly, run_sql


def test_connection_is_read_only(tmp_path):
    path = tmp_path / "t.sqlite"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE a (x INT)")
    c.commit()
    c.close()
    ro = connect_readonly(str(path))
    try:
        with pytest.raises(sqlite3.Error):
            ro.execute("INSERT INTO a VALUES (1)")
    finally:
        ro.close()


def test_row_cap_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr("src.db.MAX_ROWS", 5)
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE a (x INT)")
    c.executemany("INSERT INTO a VALUES (?)", [(i,) for i in range(20)])
    res = run_sql(c, "SELECT x FROM a")
    c.close()
    assert res.truncated and len(res.rows) == 5


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        connect_readonly(str(tmp_path / "nope.sqlite"))


def test_sqlite_connection_string_forms(tmp_path):
    from src.db import sqlite_path

    assert sqlite_path("data/x.sqlite") == "data/x.sqlite"
    assert sqlite_path("sqlite:///data/x.sqlite") == "data/x.sqlite"
    assert sqlite_path("sqlite:////abs/x.sqlite") == "/abs/x.sqlite"


def test_runaway_query_is_stopped_with_a_clear_message(monkeypatch):
    import sqlite3

    from src.db import QueryTimeout, run_sql

    monkeypatch.setattr("src.db.TIMEOUT_S", 0.2)
    c = sqlite3.connect(":memory:")
    runaway = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT COUNT(*) FROM r"
    with pytest.raises(QueryTimeout, match="longer than"):
        run_sql(c, runaway)
    assert c.execute("SELECT 1").fetchone() == (1,)  # connection still usable afterwards
    c.close()
