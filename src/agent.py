"""Text-to-SQL agent: one structured LLM call per turn, static validation, one error retry."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Literal

import openai
from pydantic import BaseModel, ValidationError

from src import llm
from src.db import QueryResult, QueryTimeout, run_sql
from src.intent import looks_like_write_request
from src.schema import DESC_DIR, build_schema_text, closest_names
from src.sqlguard import UnsafeSQL, parse_select, tables_used

OUT_OF_SCOPE_MESSAGE = (
    "This question requires multi-step analysis. At this point in time I can only answer "
    "questions that need single-step SQL."
)

READ_ONLY_MESSAGE = "I can only run read-only queries, so I can't change or delete data."
MALFORMED_MESSAGE = "Something went wrong while reading the model's answer. Please try again."

TurnType = Literal["follow_up", "same_tables_new_question", "new_topic"]
Status = Literal["answer", "clarify", "out_of_scope"]
Reason = Literal["multi_step_analysis", "not_in_database", "ambiguous", "write_attempt", "none"]
REFUSAL_REASONS = (
    "multi_step_analysis",
    "not_in_database",
    "write_attempt",
)  # the reasons an out_of_scope turn may carry
MAX_ASSUMED_CHARS = 160  # `assumed` is one short line printed under the SQL
MAX_CONCEPT_CHARS = 80


class Clarification(BaseModel):
    question: str
    options: list[str]


class TurnOutput(BaseModel):
    turn_type: TurnType
    status: Status
    reason: Reason = "none"
    sql: str | None = None
    assumed: str | None = None  # an interpretation the model chose, shown to the user with the answer
    concept: str | None = None  # for reason=not_in_database: the missing concept, named in the fixed message
    clarification: Clarification | None = None


# Flat schema (no oneOf) because grammar-constrained decoding handles unions poorly.
OUTPUT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "turn_type": {"type": "string", "enum": ["follow_up", "same_tables_new_question", "new_topic"]},
        "status": {"type": "string", "enum": ["answer", "clarify", "out_of_scope"]},
        "reason": {
            "type": "string",
            "enum": ["multi_step_analysis", "not_in_database", "ambiguous", "write_attempt", "none"],
        },
        "sql": {"type": ["string", "null"]},
        "assumed": {"type": ["string", "null"]},
        "concept": {"type": ["string", "null"]},
        "clarification": {
            "type": ["object", "null"],
            "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}},
            "required": ["question", "options"],
            "additionalProperties": False,
        },
    },
    "required": ["turn_type", "status", "reason", "sql", "assumed", "concept", "clarification"],
    "additionalProperties": False,
}

SYSTEM_TEMPLATE = """You are a text-to-SQL agent for a SQLite database. Respond with ONE JSON object only.

DATABASE SCHEMA (use only these tables and columns; quote reserved words such as "order"):
{schema}

RULES
- Write a single read-only SQLite SELECT. Never invent tables or columns. Use the value lists and
  descriptions above for filter values.
- Count "how many X" with COUNT(*). Use COUNT(DISTINCT col) only when a join can multiply rows; never on the primary key of the table being counted.
- Return only the columns the question asks for.
- JSON fields: turn_type, status, reason, sql, assumed, concept, clarification.
- status="out_of_scope" needs a reason. "multi_step_analysis": the user asks why something happened,
  for causes, predictions, forecasts, recommendations, or an analysis that needs multiple steps ("Which
  district has the highest count?" is answerable; "why is this count so high?" is not); it wins whenever
  it applies. "not_in_database": a plain data question that needs a table, column or concept this database
  does not have; name it in `concept`. "write_attempt": it asks to
  change data. Set sql and clarification to null.
- status="clarify" (reason "ambiguous"): ONLY if the ambiguity would change the result and no reasonable
  default exists. Give one question and 2-3 short options; sql is null. Otherwise pick the likeliest reading.
- status="answer" (reason "none"): provide sql; clarification is null. If the question left a choice open,
  or your SQL uses a proxy for what was asked (a nearby column or a related concept), say what you chose in
  `assumed` (a few words, for example "recent" read as the latest 10 rows), else null.
- When the question refers back ("that client", "those", "the top one"), resolve it from the recent
  turns' SQL and result values. Do not ask for clarification if the referent is there; reuse the value
  or embed the earlier query as a subquery.
- turn_type: with no prior turns use "new_topic". Otherwise:
  "follow_up" if the question cannot be understood without the previous turns (words like "those",
  "them", "their", "that", "it", "what about", "and for...", or an extra filter on the previous result);
  "same_tables_new_question" only if it is fully self-contained yet uses the same tables as before;
  "new_topic" if it concerns different data.
"""


@dataclass
class Stats:
    llm_calls: int = 0
    llm_latency_s: float = 0.0
    exec_latency_s: float = 0.0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    reasoning: str = ""

    def add(self, r: llm.LLMResult) -> None:
        self.llm_calls += 1
        self.llm_latency_s += r.latency_s
        self.prompt_tokens += r.prompt_tokens
        self.cached_tokens += r.cached_tokens
        self.completion_tokens += r.completion_tokens
        self.cost_usd += r.cost_usd
        self.reasoning += r.reasoning


@dataclass
class TurnResult:
    status: str  # a Status value, or "error" when the model API failed
    turn_type: TurnType
    question: str
    sql: str | None = None
    tables: list[str] = field(default_factory=list)
    result: QueryResult | None = None
    clarification: Clarification | None = None
    error: str | None = None  # user-facing message when status == "error" (or a SQL error on an answer)
    detail: str | None = None  # technical reason behind `error`, shown with -v and kept for the turn log
    refusal: str | None = None  # "scope" | "read_only" | "not_in_database" when status == "out_of_scope"
    reason: str = "none"  # the model's category (a Reason value), see REFUSAL_REASONS
    assumed: str | None = None
    concept: str | None = None
    closest: list[str] = field(default_factory=list)  # nearest tables/columns, only for not_in_database
    first_attempt_ok: bool = True
    retries: int = 0
    total_s: float = 0.0
    stats: Stats = field(default_factory=Stats)


@dataclass
class AgentConfig:
    model: str = llm.DEFAULT_MODEL
    schema_level: str = "values"  # ddl | desc | values
    desc_dir: str | None = DESC_DIR  # column-description CSVs; None = database has none
    retry: bool = True
    reasoning_effort: str | None = "medium"  # "low" was ~2x faster but flaky on q_008 (wrong join); see README
    structured: bool = True  # grammar-constrained response_format


def friendly_api_error(e: openai.OpenAIError) -> str:
    if isinstance(e, openai.AuthenticationError):
        return "The Fireworks API rejected the API key. Check FIREWORKS_API_KEY."
    if isinstance(e, openai.RateLimitError):
        return "Fireworks rate limit reached. Wait a moment and try again."
    if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError)):
        return "Could not reach Fireworks (timeout or network problem). Try again."
    if isinstance(e, openai.NotFoundError):
        return "The configured model is not available on this account. Set FIREWORKS_MODEL."
    return f"The model API returned an error: {e}"


def _same_sql(a: str, b: str) -> bool:
    """Same query ignoring case, spacing and a trailing semicolon."""
    canon = lambda q: " ".join(q.lower().replace(";", " ").split())  # noqa: E731
    return canon(a) == canon(b)


def parse_output(text: str) -> TurnOutput:
    """Strict JSON first, then extract the first {...} block, then validate cross-field rules."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("no JSON object in model output") from None
        data = json.loads(m.group(0))
    out = TurnOutput.model_validate(data)
    out.assumed = (out.assumed or "").strip()[:MAX_ASSUMED_CHARS] or None
    out.concept = (out.concept or "").strip()[:MAX_CONCEPT_CHARS] or None
    if out.status == "out_of_scope":
        out.sql = None  # safe direction: never execute SQL on a declined turn
        out.clarification = None
        if out.reason not in REFUSAL_REASONS:  # the reason must match the status; default to the original behaviour
            out.reason = "multi_step_analysis"
    elif out.status == "answer":
        out.reason = "none"
        if not (out.sql and out.sql.strip()):
            raise ValueError("status=answer but sql is empty")
    elif out.status == "clarify":
        out.reason = "ambiguous"
        if out.clarification is None:
            raise ValueError("status=clarify but clarification is missing")
    return out


class Agent:
    def __init__(self, conn: sqlite3.Connection, config: AgentConfig | None = None, client=None):
        self.conn = conn
        self.config = config or AgentConfig()
        self.client = client or llm.get_client()
        self.system_prompt = SYSTEM_TEMPLATE.format(
            schema=build_schema_text(conn, self.config.schema_level, self.config.desc_dir)
        )

    def _call(self, messages: list[dict], stats: Stats) -> llm.LLMResult:
        r = llm.chat(
            self.client,
            messages,
            model=self.config.model,
            json_schema=OUTPUT_JSON_SCHEMA if self.config.structured else None,
            reasoning_effort=self.config.reasoning_effort,
        )
        stats.add(r)
        return r

    def _generate(self, messages: list[dict], stats: Stats) -> tuple[TurnOutput, str]:
        """One call, plus a single repair call if the output is malformed."""
        r = self._call(messages, stats)
        try:
            if r.truncated:
                raise ValueError("model output was cut off (max_tokens reached)")
            return parse_output(r.text), r.text
        except (ValueError, ValidationError) as e:
            repair = messages + [
                {"role": "assistant", "content": r.text},
                {"role": "user", "content": f"Your output was invalid ({e}). Return only the corrected JSON object."},
            ]
            r2 = self._call(repair, stats)
            if r2.truncated:
                raise ValueError("model output was cut off (max_tokens reached)") from e
            return parse_output(r2.text), r2.text

    def _execute(self, sql: str, stats: Stats) -> QueryResult:
        parse_select(sql)  # raises UnsafeSQL / ParseError before anything touches the DB
        start = time.perf_counter()
        try:
            return run_sql(self.conn, sql)
        finally:
            stats.exec_latency_s += time.perf_counter() - start

    def turn(self, question: str, context: str = "") -> TurnResult:
        start = time.perf_counter()
        stats = Stats()
        user = (f"{context}\n\n" if context else "") + f"Question: {question}"
        messages = [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": user}]
        try:
            out, raw = self._generate(messages, stats)
        except openai.OpenAIError as e:
            return TurnResult(
                "error",
                "new_topic",
                question,
                error=friendly_api_error(e),
                total_s=time.perf_counter() - start,
                stats=stats,
            )
        except (ValueError, ValidationError) as e:
            # malformed, empty, truncated or refused output is an ERROR, never a clarification (F-09): a "clarify" status
            # could be counted as a correct clarification by the eval scorer and would confuse the session record
            return TurnResult(
                "error",
                "new_topic",
                question,
                error=MALFORMED_MESSAGE,
                detail=f"Malformed model output: {e}",
                total_s=time.perf_counter() - start,
                stats=stats,
            )
        res = TurnResult(
            out.status,
            out.turn_type,
            question,
            clarification=out.clarification,
            stats=stats,
            reason=out.reason,
            assumed=out.assumed,
        )
        if out.status == "out_of_scope":
            self._apply_refusal(res, out, question)
        elif out.status == "answer":
            try:
                self._answer(res, out.sql or "", messages, raw)
            except openai.OpenAIError as e:  # API failed during the error-retry call
                res.error = friendly_api_error(e)
        res.total_s = time.perf_counter() - start
        return res

    def _apply_refusal(self, res: TurnResult, out: TurnOutput, question: str) -> None:
        """Pick the fixed user-facing category for a declined turn. The model only chooses the reason; wording lives in render.py."""
        res.reason, res.concept = out.reason, out.concept
        if (
            looks_like_write_request(question) or out.reason == "write_attempt"
        ):  # the word check stays as a backup to the model
            res.refusal, res.reason = "read_only", "write_attempt"
        elif out.reason == "not_in_database":
            res.refusal, res.closest = "not_in_database", closest_names(self.conn, out.concept or "")
        else:
            res.refusal = "scope"

    def _answer(self, res: TurnResult, sql: str, messages: list[dict], raw: str) -> None:
        for attempt in range(2 if self.config.retry else 1):
            try:
                res.sql = sql
                res.result = self._execute(sql, res.stats)
                res.tables = tables_used(sql)
                res.error = None
                return
            except Exception as e:  # sqlite3.Error, sqlglot ParseError, UnsafeSQL
                res.error = str(e)
                if attempt == 0:
                    res.first_attempt_ok = False
                if isinstance(e, UnsafeSQL):  # never retry or run a write; tell the user plainly
                    res.status, res.refusal, res.reason, res.error = "out_of_scope", "read_only", "write_attempt", None
                    return
                if isinstance(e, QueryTimeout):  # re-running a runaway query would just double the wait (F-08)
                    return
                if attempt == 1 or not self.config.retry:
                    return
                res.retries += 1
                retry_msgs = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That SQL failed with: {e}\nReturn corrected JSON."},
                ]
                try:
                    out, raw = self._generate(retry_msgs, res.stats)
                except (ValueError, ValidationError):
                    return
                if out.status != "answer":
                    res.status, res.sql, res.result = out.status, None, None
                    res.clarification = out.clarification
                    res.reason = out.reason
                    if out.status == "out_of_scope":
                        self._apply_refusal(res, out, res.question)
                    return
                res.assumed = out.assumed or res.assumed
                if _same_sql(
                    out.sql or "", sql
                ):  # asking the model again changed nothing: stop instead of re-running (F-10)
                    res.detail = "The retry returned the same SQL, so it was not run again."
                    return
                sql = out.sql or ""


BASELINE_PROMPT = "Convert this question to SQL:\n{question}"


def baseline_sql(client, question: str, model: str, schema_text: str | None = None) -> tuple[str, llm.LLMResult]:
    """The naive baseline prompt, optionally with the schema prepended (fairness ablation)."""
    prompt = BASELINE_PROMPT.format(question=question)
    if schema_text:
        prompt = f"Database schema:\n{schema_text}\n\n{prompt}"
    r = llm.chat(client, [{"role": "user", "content": prompt}], model=model, max_tokens=llm.MAX_TOKENS)
    m = re.search(r"```(?:sql)?\s*(.*?)```", r.text, re.S | re.I)
    return (m.group(1) if m else r.text).strip(), r
