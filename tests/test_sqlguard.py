import pytest

from src.sqlguard import UnsafeSQL, has_top_level_order, tables_used


def test_tables_used_handles_quoting_aliases_and_ctes():
    sql = 'WITH t AS (SELECT * FROM loan) SELECT COUNT(*) FROM t JOIN "order" o ON o.a = t.b'
    assert tables_used(sql) == ["loan", "order"]


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE loan",
        "DELETE FROM loan",
        "UPDATE loan SET amount = 0",
        "INSERT INTO loan VALUES (1)",
        "SELECT 1; DROP TABLE loan",
        "PRAGMA writable_schema = 1",
        "CREATE TABLE x (a INT)",
    ],
)
def test_writes_and_multiple_statements_are_rejected(sql):
    with pytest.raises(UnsafeSQL):
        tables_used(sql)


def test_top_level_order_detection():
    assert has_top_level_order("SELECT a FROM t ORDER BY a")
    assert not has_top_level_order("SELECT a FROM (SELECT a FROM t ORDER BY a)")
