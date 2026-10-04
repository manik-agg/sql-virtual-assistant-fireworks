"""SQL safety and introspection with sqlglot (no LLM involved)."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

DIALECT = "sqlite"


class UnsafeSQL(ValueError):
    """Raised when SQL is not a single read-only SELECT."""


def parse_select(sql: str) -> exp.Expression:
    """Parse `sql` and return the tree, or raise UnsafeSQL / sqlglot ParseError."""
    statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    if len(statements) != 1:
        raise UnsafeSQL("Exactly one statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.With)) and not tree.find(exp.Select):
        raise UnsafeSQL("Only SELECT statements are allowed.")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Pragma)
    if isinstance(tree, forbidden) or tree.find(*forbidden):
        raise UnsafeSQL("Write or DDL statements are not allowed.")
    return tree


def tables_used(sql: str) -> list[str]:
    """Real tables referenced (CTE names excluded), sorted for determinism."""
    tree = parse_select(sql)
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    return sorted({t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes})


def has_top_level_order(sql: str) -> bool:
    tree = parse_select(sql)
    return tree.args.get("order") is not None


def literals(sql: str) -> list[str]:
    """String/number literals in the SQL, used by the explanation consistency check."""
    tree = parse_select(sql)
    return [str(lit.this) for lit in tree.find_all(exp.Literal)]
