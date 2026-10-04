"""Build the static schema prompt: DDL, column descriptions, and profiled values.

Levels (for ablation): "ddl" < "desc" < "values".
"""

from __future__ import annotations

import csv
import difflib
import re
import sqlite3
from pathlib import Path

DESC_DIR = "data/database_description"
MAX_DISTINCT = 20  # a text column with at most this many distinct values gets its value list in the prompt
PII_HINT = re.compile(r"name|email|phone|address|ssn|passport|birth", re.IGNORECASE)
VALUE_MAX_CHARS = (
    40  # profiled values and date samples are cut to this length (database content goes straight into the prompt)
)
SAMPLE_SIZE = 5  # non-null values sampled per column to detect a date format
# Names that suggest a date/time column. Deliberately narrow: "start", "end", "to", "from" would flag ordinary text columns.
DATE_NAME_HINT = re.compile(
    r"date|_dt$|^dt_|time|stamp|(^|_)ts$|created|updated|issued|born|birth|dob|_on$|_at$", re.IGNORECASE
)
# Values that read like instructions to the model are dropped from the prompt (the SQL gate is the backstop, this is hygiene).
INSTRUCTION_LIKE = (
    re.compile(r"\bignore\b.{0,40}\b(instructions?|rules|prompt)\b", re.IGNORECASE),
    re.compile(r"\b(disregard|forget)\b.{0,40}\b(instructions?|rules)\b", re.IGNORECASE),
    re.compile(r"^\s*(system|assistant|developer|user)\s*:", re.IGNORECASE),
)
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$")
SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
EPOCH_SECONDS = (315_532_800, 4_102_444_800)  # 1980..2100
EPOCH_MILLIS = (EPOCH_SECONDS[0] * 1000, EPOCH_SECONDS[1] * 1000)


def _load_descriptions(desc_dir: str | None) -> dict[str, dict[str, str]]:
    """{table: {column: 'description; value meanings'}} from the BIRD-style CSVs."""
    out: dict[str, dict[str, str]] = {}
    if desc_dir is None or not Path(desc_dir).is_dir():
        return out
    for path in sorted(Path(desc_dir).glob("*.csv")):
        with path.open(newline="", encoding="utf-8-sig") as fh:
            cols: dict[str, str] = {}
            for row in csv.DictReader(fh):
                name = (row.get("original_column_name") or "").strip()
                bits = [
                    (row.get("column_name") or "").strip(),
                    (row.get("column_description") or "").strip(),
                    (row.get("value_description") or "").strip(),
                ]
                text = "; ".join(dict.fromkeys(b for b in bits if b))  # de-dup, keep order
                cols[name.lower()] = re.sub(r"\s+", " ", text)
            out[path.stem.lower()] = cols
    return out


def matched_description_count(conn: sqlite3.Connection, desc_dir: str | None) -> int:
    """Columns of THIS database that have a non-empty description in `desc_dir`.

    0 means the folder is missing, empty, or describes other tables, i.e. the prompt gets no descriptions (F-06).
    """
    descs = _load_descriptions(desc_dir)
    total = 0
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        columns = {row[1].lower() for row in conn.execute(f'PRAGMA table_info("{table}")')}
        total += sum(1 for col, text in descs.get(table.lower(), {}).items() if text and col in columns)
    return total


def sanitize_value(value) -> str | None:
    """Make a database value safe to print into the prompt: printable characters only, collapsed whitespace, at most
    VALUE_MAX_CHARS characters, and None for values that look like instructions to the model."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    text = " ".join(text.split())
    if not text or any(pattern.search(text) for pattern in INSTRUCTION_LIKE):
        return None
    return text if len(text) <= VALUE_MAX_CHARS else text[: VALUE_MAX_CHARS - 1] + "…"


def schema_blocks(schema_text: str) -> dict[str, str]:
    """Split the schema text into {table name: its block}, so a prompt can include only the tables an answer used."""
    blocks: dict[str, str] = {}
    for match in re.finditer(r'^TABLE "([^"]+)" \(.*?^\)', schema_text, re.S | re.M):
        blocks[match.group(1)] = match.group(0)
    return blocks


def closest_names(conn: sqlite3.Connection, concept: str, limit: int = 3) -> list[str]:
    """Tables and `table.column` names closest to a concept the question asked for (no LLM; used in the not-in-database message).

    Word overlap first (a word equals or sits inside a name part), then spelling similarity of at least 0.75.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", (concept or "").lower()) if len(w) >= 3]
    if not words:
        return []
    names: list[str] = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        names.append(table)
        names += [f"{table}.{row[1]}" for row in conn.execute(f'PRAGMA table_info("{table}")')]

    def part_score(word: str, part: str) -> float:
        stem = word[:-1] if word.endswith("s") and len(word) > 3 else word
        if stem == part:
            return 1.0
        if len(part) >= 3 and (stem in part or part in stem):
            return 0.9
        ratio = difflib.SequenceMatcher(None, stem, part).ratio()
        return ratio if ratio >= 0.75 else 0.0

    scored = []
    for name in names:
        parts = re.findall(r"[a-z0-9]+", name.lower())
        score = sum(max((part_score(w, p) for p in parts), default=0.0) for w in words)
        if score > 0:
            scored.append((-score, "." in name, name))  # best first; tables before columns on ties
    return [name for _, _, name in sorted(scored)[:limit]]


def _valid_ymd(digits: str) -> bool:
    year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
    return 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


def _classify_dates(samples: list, name_hint: bool) -> str | None:
    """Format class of sampled values: iso | ymd_text | ymd_int | epoch_s | epoch_ms | slash | unrecognized | None (not a date)."""
    is_text = lambda v: isinstance(v, str)  # noqa: E731
    is_int = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
    if all(is_text(v) and ISO_DATE.match(v.strip()) and _valid_ymd(v.strip()[:10].replace("-", "")) for v in samples):
        return "iso"
    if all(is_text(v) and re.fullmatch(r"\d{8}", v.strip()) and _valid_ymd(v.strip()) for v in samples):
        return "ymd_text"
    if all(is_int(v) and 10_000_000 <= v <= 99_999_999 and _valid_ymd(str(v)) for v in samples):
        return "ymd_int"
    if name_hint and all(is_int(v) and EPOCH_SECONDS[0] <= v <= EPOCH_SECONDS[1] for v in samples):
        return "epoch_s"
    if name_hint and all(is_int(v) and EPOCH_MILLIS[0] <= v <= EPOCH_MILLIS[1] for v in samples):
        return "epoch_ms"
    if all(is_text(v) and SLASH_DATE.match(v.strip()) for v in samples):
        return "slash"
    return "unrecognized" if name_hint else None


def _slash_order(conn: sqlite3.Connection, table: str, col: str) -> str:
    """dmy | mdy | ambiguous, decided from the largest first and second part over the whole column."""
    q, c = f'"{table}"', f'"{col}"'
    row = conn.execute(
        f"WITH r AS (SELECT {c} AS v, substr({c}, instr({c}, '/') + 1) AS rest FROM {q} WHERE {c} LIKE '%/%/%') "
        "SELECT MAX(CAST(substr(v, 1, instr(v, '/') - 1) AS INTEGER)), MAX(CAST(substr(rest, 1, instr(rest, '/') - 1) AS INTEGER)) FROM r"
    ).fetchone()
    first, second = row or (None, None)
    if first and first > 12 >= (second or 0):
        return "dmy"
    if second and second > 12 >= (first or 0):
        return "mdy"
    return "ambiguous"


def _date_note(conn: sqlite3.Connection, table: str, col: str, ctype: str) -> str | None:
    """Format note for a date-like column: format class, range or samples, and how to query it (F-12). None if not date-like.

    A format that is ambiguous (DD/MM vs MM/DD) or unrecognized shows raw samples and tells the model not to guess.
    """
    kind_ok = re.search(r"INT|CHAR|TEXT|DATE|TIME|CLOB|^$", ctype.upper()) is not None
    if not kind_ok:
        return None
    q, c = f'"{table}"', f'"{col}"'
    samples = [
        r[0] for r in conn.execute(f"SELECT {c} FROM {q} WHERE {c} IS NOT NULL AND {c} != '' LIMIT {SAMPLE_SIZE}")
    ]
    if not samples:
        return None
    name_hint = bool(DATE_NAME_HINT.search(col)) or re.search(r"DATE|TIME", ctype.upper()) is not None
    kind = _classify_dates(samples, name_hint)
    if kind is None:
        return None
    shown = [s for s in (sanitize_value(v) for v in samples[:3]) if s]
    sample_text = ", ".join(repr(v) for v in shown) if not PII_HINT.search(col) else "withheld, possible personal data"
    if kind in ("iso", "ymd_text", "ymd_int", "epoch_s", "epoch_ms"):
        lo, hi = conn.execute(f"SELECT MIN({c}), MAX({c}) FROM {q} WHERE {c} IS NOT NULL AND {c} != ''").fetchone()
        return {
            "iso": f"dates {lo}..{hi} (ISO text)",
            "ymd_text": f"dates as TEXT in YYYYMMDD form, '{lo}'..'{hi}': use substr(col, 1, 4) for the year and compare as strings; strftime does not parse this",
            "ymd_int": f"dates as INTEGER in YYYYMMDD form, {lo}..{hi}: year = col/10000, month = col/100%100; compare with numbers like 20240101; strftime does not parse this",
            "epoch_s": f"unix epoch SECONDS, {lo}..{hi}: use datetime(col, 'unixepoch') or strftime('%Y', col, 'unixepoch')",
            "epoch_ms": f"unix epoch MILLISECONDS, {lo}..{hi}: use col/1000 with the 'unixepoch' modifier",
        }[kind]
    if kind == "slash":
        order = _slash_order(conn, table, col)
        if order == "dmy":
            return f"dates as TEXT DD/MM/YYYY, e.g. {sample_text}: extract parts with substr/instr; strftime does not parse this"
        if order == "mdy":
            return f"dates as TEXT MM/DD/YYYY, e.g. {sample_text}: extract parts with substr/instr; strftime does not parse this"
        return f"date-like TEXT with slashes, day/month order unclear, samples {sample_text}. Do not guess the order"
    return f"looks like a date but the format is unrecognized, samples {sample_text}. Do not guess"


_FORMAT_HINT = re.compile(r"\s*;?\s*\bin the form\s+[A-Za-z0-9/\-]+", re.IGNORECASE)


def _strip_format_hint(description: str) -> str:
    """Drop a documentation hint like 'in the form YYMMDD' when profiling measured the real format (F-11)."""
    return _FORMAT_HINT.sub("", description).strip(" ;")


def _value_profile(conn: sqlite3.Connection, table: str, col: str, ctype: str) -> str | None:
    """Distinct values (sanitised) for low-cardinality text columns."""
    q, c = f'"{table}"', f'"{col}"'
    if PII_HINT.search(col):
        return None
    if ctype.upper() in ("TEXT", "") or "CHAR" in ctype.upper():
        vals = [r[0] for r in conn.execute(f"SELECT DISTINCT {c} FROM {q} LIMIT {MAX_DISTINCT + 1}")]
        vals = [v for v in vals if v is not None and v != ""]
        if 0 < len(vals) <= MAX_DISTINCT:
            clean = {text for text in (sanitize_value(v) for v in vals) if text}
            # blank-but-not-NULL is data (e.g. ' ' meaning "no code recorded"), so whitespace-only values stay, literally
            blanks = {v[:VALUE_MAX_CHARS] for v in vals if isinstance(v, str) and v and not v.strip()}
            return "values: " + ", ".join(repr(v) for v in sorted(clean | blanks)) if clean | blanks else None
    return None


def build_schema_text(conn: sqlite3.Connection, level: str = "values", desc_dir: str | None = DESC_DIR) -> str:
    descs = _load_descriptions(desc_dir) if level in ("desc", "values") else {}
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    blocks: list[str] = []
    for t in tables:
        info = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
        fks = {r[3]: f"{r[2]}({r[4]})" for r in conn.execute(f'PRAGMA foreign_key_list("{t}")')}
        lines = []
        for _, name, ctype, _notnull, _, pk in info:
            parts = [f'  "{name}" {ctype or ""}'.rstrip()]
            if pk:
                parts.append("PRIMARY KEY")
            if name in fks:
                parts.append(f"REFERENCES {fks[name]}")
            notes = []
            date_note = _date_note(conn, t, name, ctype or "") if level == "values" else None
            description = descs.get(t, {}).get(name.lower())
            if description:
                notes.append(_strip_format_hint(description) if date_note else description)
            if date_note:
                notes.append(date_note)
            elif level == "values":
                prof = _value_profile(conn, t, name, ctype or "")
                if prof:
                    notes.append(prof)
            line = " ".join(parts) + ","
            if notes:
                line += "  -- " + " | ".join(notes)
            lines.append(line)
        blocks.append(f'TABLE "{t}" (\n' + "\n".join(lines) + "\n)")
    return "\n\n".join(blocks)
