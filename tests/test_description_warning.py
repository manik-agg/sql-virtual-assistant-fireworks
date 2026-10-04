"""F-06: a missing or useless descriptions folder used to silently cost accuracy. It must now warn (not fail)."""

from pathlib import Path

from src.cli import description_warning, expected_desc_dir
from src.schema import matched_description_count

HEADER = "original_column_name,column_name,column_description,data_format,value_description\n"


def write_csv(folder: Path, table: str, rows: list[str]) -> None:
    folder.mkdir(exist_ok=True)
    (folder / f"{table}.csv").write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def test_counts_only_descriptions_that_match_columns_in_the_database(conn, tmp_path):
    write_csv(
        tmp_path,
        "loan",
        [
            "status,,repayment status,text,A finished",
            "amount,,approved amount,integer,",
            "not_a_column,,does not exist in the db,text,",
        ],
    )
    assert matched_description_count(conn, str(tmp_path)) == 2


def test_missing_directory_counts_zero(conn, tmp_path):
    assert matched_description_count(conn, str(tmp_path / "does_not_exist")) == 0


def test_no_directory_configured_counts_zero(conn):
    assert matched_description_count(conn, None) == 0


def test_empty_directory_counts_zero(conn, tmp_path):
    assert matched_description_count(conn, str(tmp_path)) == 0


def test_descriptions_for_other_tables_count_zero(conn, tmp_path):
    write_csv(tmp_path, "unrelated_table", ["x,,something,text,"])
    assert matched_description_count(conn, str(tmp_path)) == 0


def test_blank_description_text_does_not_count(conn, tmp_path):
    write_csv(tmp_path, "loan", ["status,,,text,"])
    assert matched_description_count(conn, str(tmp_path)) == 0


def test_no_warning_when_descriptions_are_found():
    assert description_warning("data/database_description", 12) is None


def test_warning_names_the_path_and_says_accuracy_may_drop():
    msg = description_warning("/some/dir/database_description", 0)
    assert "/some/dir/database_description" in msg and "accuracy may drop" in msg.lower()


def test_expected_dir_is_the_explicit_option_or_next_to_the_database():
    assert expected_desc_dir("data/x.sqlite", "/custom") == "/custom"
    assert expected_desc_dir("sqlite:///some/where/x.db", None).endswith("some/where/database_description")
