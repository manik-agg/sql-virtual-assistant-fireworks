"""F-26: the COUNT(DISTINCT) rule is tightened; the schema block stays first and byte-stable."""

from src.agent import SYSTEM_TEMPLATE

RULE = (
    'Count "how many X" with COUNT(*). Use COUNT(DISTINCT col) only when a join can multiply rows; '
    "never on the primary key of the table being counted."
)


def test_count_distinct_rule_is_the_tightened_wording():
    assert RULE in SYSTEM_TEMPLATE
    assert "when joins could duplicate rows" not in SYSTEM_TEMPLATE


def test_the_schema_block_is_at_the_start_of_the_prompt():
    assert SYSTEM_TEMPLATE.index("{schema}") < SYSTEM_TEMPLATE.index("RULES") if "RULES" in SYSTEM_TEMPLATE else True
    assert SYSTEM_TEMPLATE.index("{schema}") < 400
