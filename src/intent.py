"""Is the user asking to change data? A deterministic backup to the model's `reason="write_attempt"`.

Two kinds of mistake matter and both are tested: a missed write request gets the wrong refusal wording, and a false positive
would call a legitimate read question a write attempt ("Change in total loans between 1996 and 1997?", "Create a ranking of
districts"). So strong database verbs are always writes, while ambiguous English verbs need a structural cue.
"""

from __future__ import annotations

import re

POLITE_PREFIX = re.compile(
    r"^(please|kindly|just|can you|could you|would you|will you|i want you to|i need you to|i would like you to"
    r"|i'd like you to|go ahead and)\b[\s,]*",
    re.IGNORECASE,
)
# Words that only ever mean a write when they start a request (the one exception, "update me on ...", is handled below).
STRONG_WRITE_VERBS = {
    "delete",
    "drop",
    "update",
    "insert",
    "alter",
    "truncate",
    "rename",
    "grant",
    "revoke",
    "merge",
    "wipe",
    "erase",
    "append",
}
# Ordinary English verbs: a write only when they assign a value to a RECORD ("set client 12's district to 5", "mark every loan as
# paid"). "Change the region to Prague" or "set the threshold to 100000" change the query, not the data, so a record cue is required.
ASSIGN_VERBS = {"set", "change", "modify", "edit", "overwrite", "replace", "reset", "mark"}
NOUN_USES = {
    "in",
    "of",
    "over",
    "between",
    "up",
    "aside",
    "from",
    "by",
    "on",
}  # "change in ...", "set of ...", "set aside ..."
ASSIGN_CUE = re.compile(r"\b(to|with|as)\b|=")
# A record cue: a quantifier, a schema word, or an identifier (a word followed by a number, other than "to 1997", "above 5000", "top 5").
RECORD_CUE = re.compile(
    r"\b(every|all|each|column|columns|table|row|rows|record|records|field|fields|where)\b"
    r"|\b(?!(?:to|than|at|of|by|above|below|over|under|least|most|in|from|and|or|is|are|be|top|first|last|next)\b)[a-z_]+\s+#?\d+\b"
)
ROW_OBJECT = re.compile(
    r"\b(row|rows|record|records|column|columns|table)\b"
)  # "edit row 5 of the client table" needs no "to"
EMPTY_VALUE = re.compile(r"\b(zero|null|empty|0)\b")
SCHEMA_OBJECTS = {"table", "view", "index", "database", "schema", "trigger", "user"}
PRESENTATION_AFTER_OBJECT = {
    "of",
    "showing",
    "listing",
    "with",
    "that",
    "for",
}  # "create a table OF loans per region" is a read
RESULT_WORDS = re.compile(r"\bfrom (the )?(results?|list|output|report)\b")


def looks_like_write_request(question: str) -> bool:
    text = question.strip().lower()
    while match := POLITE_PREFIX.match(text):
        text = text[match.end() :]
    words = re.findall(r"[a-z0-9_'=]+", text)
    if not words:
        return False
    verb, rest_words, rest = words[0], words[1:], " ".join(words[1:])
    next_word = rest_words[0] if rest_words else ""

    if verb == "update":
        return next_word not in {"me", "us", "on"}  # "update me on how many loans ..." is a read
    if verb in STRONG_WRITE_VERBS:
        return True
    if verb in ASSIGN_VERBS:
        if next_word in NOUN_USES or not RECORD_CUE.search(text):
            return False
        return bool(ASSIGN_CUE.search(text)) or bool(ROW_OBJECT.search(text))
    if verb == "make":
        return (next_word in {"every", "all", "each", "the"} or bool(RECORD_CUE.search(rest))) and bool(
            EMPTY_VALUE.search(rest)
        )
    if verb == "add":
        return (
            next_word == "new"
            or rest.startswith(("a new", "an new"))
            or bool(re.search(r"\b(into|to)\b.*\b(table|database)\b", rest))
        )
    if verb == "create":
        obj = [w for w in rest_words if w not in {"a", "an", "the", "new"}][:2]
        if not obj or obj[0] not in SCHEMA_OBJECTS:
            return False
        return len(obj) < 2 or obj[1] not in PRESENTATION_AFTER_OBJECT
    if verb == "remove":
        return "duplicate" not in rest and not RESULT_WORDS.search(text)
    return False
