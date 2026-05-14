"""OpenAI client wrapper for GPT-5.x / o-series reasoning models.

Pared-down port of `wcmc-its/ReCiterAI-POC` `core/oai_client.py` +
`core/gpt5_utils.py`. Mirrors `utils/bedrock_client.py`'s shape:
lazy client init, JSON-structured-output via response_format, and
exponential backoff on transient errors.

Models we use today:
- gpt-5.1 (synopsis + impact in the daily enrichment job)

This module deliberately does NOT log to the POC's
`reciterai_llm_usage_v2` table — that DDB-side accounting is replaced by
the `STAGE#.cost_observed_usd` substrate via `utils.llm_cost`.

Security (CLAUDE.md):
- OPENAI_API_KEY read from env var only. Never logged, never returned.
- The `OpenAI()` constructor reads the env var automatically; do not pass
  `api_key=` explicitly (legacy model-routing on some platforms is more
  expensive than the official path).
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


# Default model for the daily enrichment job. Centralized here so a model
# bump is a one-line change. Mirrors utils/bedrock_client.py:HAIKU_MODEL etc.
GPT5_MODEL = "gpt-5.1"


def is_gpt5_model(model: str) -> bool:
    """Detect GPT-5 family or o-series reasoning models."""
    m = (model or "").lower()
    return "gpt-5" in m or m.startswith("o")


def create_gpt5_completion(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_format: Optional[dict] = None,
    max_completion_tokens: int = 8000,
    reasoning_effort: Optional[str] = None,
) -> Any:
    """Issue a chat completion request, tuned for GPT-5 / o-series.

    Mirrors POC `core/gpt5_utils.py::create_gpt5_completion` exactly.
    - Always sends `max_completion_tokens` (covers reasoning + visible tokens).
    - Adds `response_format` (json_object by default; json_schema if provided).
    - Adds `reasoning_effort` for GPT-5/o models when caller provides one.
    """
    params: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_completion_tokens": max_completion_tokens,
    }

    # Non-reasoning models: keep default json_object response format.
    # Reasoning models: include the response_format only if caller passed one.
    if not is_gpt5_model(model):
        params["response_format"] = response_format or {"type": "json_object"}
    elif response_format is not None:
        params["response_format"] = response_format

    if reasoning_effort and is_gpt5_model(model):
        params["reasoning_effort"] = reasoning_effort

    return client.chat.completions.create(**params)


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------


def _is_transient(err: str) -> bool:
    e = (err or "").lower()
    return any(
        marker in e
        for marker in ("rate", "429", "timeout", "temporar",
                       "unavailable", "overloaded", "502", "503", "504")
    )


def _sleep_backoff(attempt: int, base: float = 0.5, cap: float = 8.0) -> None:
    delay = min(cap, base * (2 ** attempt)) + random.uniform(0, 0.25)
    time.sleep(delay)


def call_with_retry(
    client: OpenAI,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_format: Optional[dict] = None,
    max_completion_tokens: int = 8000,
    reasoning_effort: Optional[str] = None,
    max_attempts: int = 3,
) -> Any:
    """create_gpt5_completion + exponential backoff on transient errors.

    Returns the raw OpenAI completion object; caller is responsible for
    parsing `response.choices[0].message.content` and pulling
    `response.usage.{input_tokens,output_tokens}` for cost accounting.
    """
    last_err: Optional[str] = None
    for attempt in range(max_attempts):
        try:
            return create_gpt5_completion(
                client,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format=response_format,
                max_completion_tokens=max_completion_tokens,
                reasoning_effort=reasoning_effort,
            )
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            if attempt + 1 >= max_attempts or not _is_transient(last_err):
                raise
            logger.warning(
                "openai transient error (attempt %d/%d): %s",
                attempt + 1, max_attempts, last_err,
            )
            _sleep_backoff(attempt)
    # Unreachable; final exception re-raised inside loop.
    raise RuntimeError(f"call_with_retry exhausted retries: {last_err}")


# ---------------------------------------------------------------------------
# Lazy default client (mirrors utils/bedrock_client.py pattern)
# ---------------------------------------------------------------------------

_default_client: Optional[OpenAI] = None


def get_default_client() -> OpenAI:
    """Return module-level default OpenAI client, creating on first call.

    No network calls at import time. Tests inject their own client.
    """
    global _default_client
    if _default_client is None:
        _default_client = OpenAI()
    return _default_client
