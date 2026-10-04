from eval.compare import compare

GOLD_SQL = "SELECT a, b FROM t"


def test_order_ignored_without_order_by():
    assert compare([(2, "b"), (1, "a")], [(1, "a"), (2, "b")], GOLD_SQL)["match"]


def test_order_enforced_with_order_by():
    assert not compare([(2, "b"), (1, "a")], [(1, "a"), (2, "b")], GOLD_SQL + " ORDER BY a")["match"]


def test_duplicates_matter():
    assert not compare([(1,), (1,)], [(1,)], "SELECT a FROM t")["match"]


def test_float_and_int_equal():
    assert compare([(5.0,)], [(5,)], "SELECT 1")["match"]


def test_unrounded_average_is_a_strict_miss():
    # documents decision 8: q_004 counts as a miss under the strict comparator
    assert not compare([(4190.664,)], [(4190.66,)], "SELECT ROUND(AVG(x),2) FROM t")["match"]


def test_extra_columns_fail_but_are_reported_as_a_shape_difference():
    out = compare([(1, "a", 9), (2, "b", 9)], [(1, "a"), (2, "b")], GOLD_SQL)
    assert out == {"match": False, "shape_differs": True}


def test_swapped_columns_fail():
    out = compare([("a", 1), ("b", 2)], [(1, "a"), (2, "b")], GOLD_SQL)
    assert out["match"] is False and out["shape_differs"] is True


def test_coinciding_extra_column_fails():
    assert not compare([(1, 1), (2, 2)], [(1,), (2,)], "SELECT a FROM t")["match"]


def test_string_case_matters():
    assert not compare([("f",)], [("F",)], GOLD_SQL)["match"]


def test_string_whitespace_matters():
    assert not compare([("Prague ",)], [("Prague",)], GOLD_SQL)["match"]


def test_float_noise_passes_but_a_real_difference_fails():
    assert compare([(0.1 + 0.2,)], [(0.3,)], GOLD_SQL)["match"]
    assert not compare([(1000000.4,)], [(1000000.0,)], GOLD_SQL)["match"]
    assert not compare([(1000000.4,)], [(1000000.0,)], GOLD_SQL + " ORDER BY a")["match"]  # same rule when ordered


def test_alternates_accept_a_second_valid_shape():
    gold, alt = [(1, "a")], [("a", 1)]
    assert not compare(alt, gold, GOLD_SQL)["match"]
    assert compare(alt, gold, GOLD_SQL, alternates=[alt])["match"]
    assert not compare([("z", 1)], gold, GOLD_SQL, alternates=[alt])["match"]


def test_bool_and_negative_zero_still_equal():
    assert compare([(True,)], [(1,)], GOLD_SQL)["match"]
    assert compare([(-0.0,)], [(0.0,)], GOLD_SQL)["match"]


def test_wrong_values_fail():
    assert not compare([(1, "a")], [(1, "z")], GOLD_SQL)["match"]
