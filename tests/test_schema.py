from src.schema import build_schema_text
from src.session import hub_tables


def test_schema_levels(conn):
    ddl = build_schema_text(conn, "ddl", None)
    vals = build_schema_text(conn, "values", None)
    assert '"status" TEXT' in ddl and "values:" not in ddl
    assert "values: 'A', 'B'" in vals  # low-cardinality text columns are profiled
    assert "REFERENCES district(district_id)" in vals


def test_hub_tables_come_from_fk_graph(conn):
    assert hub_tables(conn) == {"district"}  # referenced by account and client
