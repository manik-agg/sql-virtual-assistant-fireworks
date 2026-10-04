"""Thin Fireworks (OpenAI-compatible) client that records latency, tokens and cost."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI

load_dotenv()

BASE_URL = "https://api.fireworks.ai/inference/v1"
# A normal turn takes 1-3 s. A hung endpoint used to cost 2 x 30 s = 60.7 s of silence (measured); at 15 s per attempt and one
# retry the worst case is about 30 s (F-08).
REQUEST_TIMEOUT_S = 15.0
MAX_RETRIES = 1
# Reasoning tokens count toward max_tokens, so this must leave room for thinking plus the JSON answer.
MAX_TOKENS = 3000
DEFAULT_MODEL = os.getenv("FIREWORKS_MODEL", "accounts/fireworks/models/gpt-oss-120b")

# USD per 1M tokens (input, cached input, output): serverless Standard tier from
# docs.fireworks.ai/serverless/pricing, fetched 2026-09-30. Models missing from that page use
# DEFAULT_PRICE, an overestimate. Re-check before quoting.
PRICES = {
    "accounts/fireworks/models/gpt-oss-120b": (0.15, 0.015, 0.60),
    "accounts/fireworks/models/deepseek-v4p1-flash": (0.30, 0.006, 1.20),
    "accounts/fireworks/models/glm-5p3": (1.40, 0.26, 4.40),
    "accounts/fireworks/models/minimax-m3": (0.30, 0.06, 1.20),
    "accounts/fireworks/models/kimi-k3": (3.00, 0.30, 15.00),
}
DEFAULT_PRICE = (0.60, 0.30, 2.50)


@dataclass
class LLMResult:
    text: str
    latency_s: float
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    cost_usd: float
    reasoning: str = ""
    truncated: bool = False  # finish_reason == "length": the output was cut off


def get_client() -> OpenAI:
    key = os.getenv("FIREWORKS_API_KEY")
    if not key:
        raise RuntimeError("FIREWORKS_API_KEY is not set (export it or put it in .env).")
    return OpenAI(api_key=key, base_url=BASE_URL, timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES)


def get_async_client() -> AsyncOpenAI:
    key = os.getenv("FIREWORKS_API_KEY")
    if not key:
        raise RuntimeError("FIREWORKS_API_KEY is not set (export it or put it in .env).")
    return AsyncOpenAI(api_key=key, base_url=BASE_URL, timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES)


def _kwargs(messages, model, json_schema, reasoning_effort, max_tokens) -> dict:
    kwargs: dict = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    if json_schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "turn", "schema": json_schema, "strict": True},
        }
    if reasoning_effort and reasoning_effort != "default":  # "default" = do not send the param
        kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}
    return kwargs


def _result(resp, model: str, latency: float) -> LLMResult:
    usage, msg = resp.usage, resp.choices[0].message
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    p_in, p_cached, p_out = PRICES.get(model, DEFAULT_PRICE)
    cost = ((usage.prompt_tokens - cached) * p_in + cached * p_cached + usage.completion_tokens * p_out) / 1e6
    reasoning = getattr(msg, "reasoning_content", None) or ""
    return LLMResult(
        msg.content or "",
        latency,
        usage.prompt_tokens,
        cached,
        usage.completion_tokens,
        cost,
        reasoning,
        resp.choices[0].finish_reason == "length",
    )


def chat(
    client, messages, model=DEFAULT_MODEL, json_schema=None, reasoning_effort=None, max_tokens=MAX_TOKENS
) -> LLMResult:
    start = time.perf_counter()
    resp = client.chat.completions.create(**_kwargs(messages, model, json_schema, reasoning_effort, max_tokens))
    return _result(resp, model, time.perf_counter() - start)


async def achat(
    client, messages, model=DEFAULT_MODEL, json_schema=None, reasoning_effort=None, max_tokens=MAX_TOKENS
) -> LLMResult:
    """Async variant: cancelling the awaiting task closes the HTTP request."""
    start = time.perf_counter()
    resp = await client.chat.completions.create(**_kwargs(messages, model, json_schema, reasoning_effort, max_tokens))
    return _result(resp, model, time.perf_counter() - start)
