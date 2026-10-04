"""Strict result-set comparison (F-07).

Columns are compared by position, strings exactly (case and whitespace matter), numbers with one tolerance. Rows are a
multiset unless the gold SQL has a top-level ORDER BY. A reordered or extra column is a miss, reported as `shape_differs`;
a gold case may list `alternates` (other accepted result sets) when a different shape is genuinely correct.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from src.sqlguard import has_top_level_order

# One float tolerance for ordered and unordered results. Relative is tight on purpose: 1000000.4 vs 1000000.0 is a different
# answer, while 0.1 + 0.2 vs 0.3 is float noise (the old 1e-6 relative rule accepted a 1.0 error on a million).
FLOAT_ABS_TOL = 1e-6
FLOAT_REL_TOL = 1e-9


def norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        return v + 0.0  # + 0.0 turns -0.0 into 0.0
    if isinstance(v, int):
        return v  # integers are compared exactly: the float tolerance would let 10**15 + 1 equal 10**15
    return str(v)


def _rows(rows: list[tuple]) -> list[tuple]:
    return [tuple(norm(v) for v in r) for r in rows]


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and (isinstance(a, float) or isinstance(b, float)):
        return math.isclose(a, b, rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL)
    return a == b


def _subset_match(pred: list[tuple], gold: list[tuple], ordered: bool) -> bool:
    """True if some choice of predicted columns (in gold's order) reproduces gold.

    Allows extra predicted columns (e.g. an added name column). Column choice is
    found greedily per gold column by value-matching whole columns.
    """
    if not gold or not pred or len(pred) != len(gold):
        return False
    n_gold, n_pred = len(gold[0]), len(pred[0])
    if n_pred < n_gold:
        return False
    pred_cols = [[r[j] for r in pred] for j in range(n_pred)]
    gold_cols = [[r[j] for r in gold] for j in range(n_gold)]
    mapping: list[int] = []
    for g in gold_cols:
        for j, p in enumerate(pred_cols):
            key = (lambda col: col) if ordered else sorted
            try:
                if all(_close(x, y) for x, y in zip(key(p), key(g), strict=True)):
                    mapping.append(j)
                    break
            except TypeError:
                continue
        else:
            return False
    projected = [tuple(r[j] for j in mapping) for r in pred]
    return _equal(projected, gold, ordered)


def _equal(pred: list[tuple], gold: list[tuple], ordered: bool) -> bool:
    if len(pred) != len(gold):
        return False
    if ordered:
        return all(len(p) == len(g) and all(_close(a, b) for a, b in zip(p, g)) for p, g in zip(pred, gold))
    if Counter(pred) == Counter(gold):  # fast path: exact multiset
        return True
    unmatched = list(gold)  # slow path: pair each predicted row with a distinct gold row within the float tolerance
    for row in pred:
        for i, g in enumerate(unmatched):
            if len(row) == len(g) and all(_close(a, b) for a, b in zip(row, g)):
                del unmatched[i]
                break
        else:
            return False
    return True


def compare(
    pred_rows: list[tuple], gold_rows: list[tuple], gold_sql: str, alternates: list[list[tuple]] | None = None
) -> dict:
    """Return {'match': bool, 'shape_differs': bool}; `shape_differs` marks a miss whose values appear in other columns."""
    ordered = has_top_level_order(gold_sql)
    p = _rows(pred_rows)
    for accepted in [gold_rows, *(alternates or [])]:
        if _equal(p, _rows(accepted), ordered):
            return {"match": True, "shape_differs": False}
    return {"match": False, "shape_differs": _subset_match(p, _rows(gold_rows), ordered)}
