"""One JSON line per turn in logs/turns.jsonl, for debugging and cost review (F-17).

Logged: the question, status/reason, SQL, row count, timings, tokens, cost. Never logged: result values, the model's
reasoning, or the API key (it is scrubbed from any text). A failure to write never disturbs the session.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from src.session import Event

DEFAULT_LOG_PATH = os.path.join("logs", "turns.jsonl")


def _scrub(text):
    """Remove the API key from a string, if it appears (e.g. inside an error message)."""
    key = os.getenv("FIREWORKS_API_KEY")
    return text.replace(key, "[redacted]") if key and isinstance(text, str) else text


def turn_record(event: Event, model: str | None = None) -> dict:
    t = event.turn
    assert t is not None  # only turn events are logged (see TurnLog.write)
    res = t.result
    return {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model,
        "question": _scrub(t.question),
        "status": t.status,
        "reason": t.reason,
        "turn_type": t.turn_type,
        "switched": event.switched,
        "sql": _scrub(t.sql),
        "tables": t.tables,
        "row_count": res.total_rows if res else None,
        "truncated": res.truncated if res else None,
        "retries": t.retries,
        "first_attempt_ok": t.first_attempt_ok,
        "total_s": round(t.total_s, 3),
        "llm_s": round(t.stats.llm_latency_s, 3),
        "exec_s": round(t.stats.exec_latency_s, 3),
        "prompt_tokens": t.stats.prompt_tokens,
        "cached_tokens": t.stats.cached_tokens,
        "completion_tokens": t.stats.completion_tokens,
        "cost_usd": round(t.stats.cost_usd, 6),
        "error": _scrub(t.error),
        "detail": _scrub(t.detail),
    }


class TurnLog:
    def __init__(self, path: str | None, model: str | None = None):
        self.path, self.model = path, model  # path None = logging off

    def write(self, event: Event) -> None:
        if not self.path or event.turn is None:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(turn_record(event, self.model), ensure_ascii=False) + "\n")
        except OSError:
            pass  # logging is best-effort
