"""Synopsis generation — per-PMID worker.

Ported from POC `core/synopsis.py` (publications path only, multi-entity
generality dropped). Calls GPT-5.1 via Chat Completions with the
SYNOPSIS_SCHEMA structured response format.

Inputs: (pmid, title, journal, year, abstract)
Output: SynopsisResult dataclass — synopsis string + usage metadata for
cost attribution + the OpenAI response model for traceability.

Scheduled wiring (watermark, MariaDB write, error rollup) lands in #37
step 2; this module is just the per-PMID worker.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from pipeline_enrichment.prompts import (
    SYNOPSIS_SCHEMA,
    SYNOPSIS_SYSTEM,
    build_synopsis_user_content,
)
from utils.openai_client import GPT5_MODEL, call_with_retry, get_default_client

logger = logging.getLogger(__name__)

# Defense-in-depth on the ≤95-char synopsis rule, in addition to the
# schema-level maxLength on SYNOPSIS_SCHEMA. If the API ever returns a
# longer string (maxLength is best-effort, not a hard refusal), retry
# once with a reinforcement message; on second overrun, surface the
# actual model output with a length-violation error. NEVER truncate —
# truncation drops content the model considered necessary and is a
# silent quality regression for user-facing faculty-page text. (See #50.)
SYNOPSIS_MAX_CHARS = 95


@dataclass
class SynopsisResult:
    """One synopsis generation outcome.

    `synopsis` is None when the call failed; `error` holds the message.
    `input_tokens` / `output_tokens` come from `response.usage` and feed
    `utils.llm_cost.CostAccumulator.record`.
    """

    pmid: str
    synopsis: Optional[str]
    model: str
    input_tokens: int
    output_tokens: int
    error: Optional[str] = None


# Matches POC `core/synopsis.py` response_format shape exactly.
_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "synopsis",
        "strict": True,
        "schema": SYNOPSIS_SCHEMA["schema"],
    },
}


def generate_synopsis(
    *,
    pmid: str,
    title: str,
    journal: str | None = None,
    year: int | None = None,
    abstract: str | None = None,
    client: OpenAI | None = None,
    model: str = GPT5_MODEL,
    max_completion_tokens: int = 8000,
    reasoning_effort: str | None = None,
) -> SynopsisResult:
    """Generate a ≤95-char synopsis for one PMID.

    Args:
        pmid: PMID string, used only for tagging the result + log lines.
        title: publication title (required).
        journal, year, abstract: optional context; abstract is the
            primary driver of synopsis content.
        client: OpenAI client; defaults to the module's lazy singleton.
        model: model ID (default GPT5_MODEL = "gpt-5.1").
        max_completion_tokens: GPT-5 reasoning + visible-token ceiling.
        reasoning_effort: forwarded to GPT-5 ("minimal"/"low"/"medium"/"high"),
            None lets the model use its default.

    Returns:
        SynopsisResult. On success, `.synopsis` is the produced string.
        On failure, `.synopsis` is None and `.error` describes the issue.
    """
    client = client or get_default_client()
    user_content = build_synopsis_user_content(
        title=title, journal=journal, year=year, abstract=abstract
    )

    input_tokens_total = 0
    output_tokens_total = 0
    response_model: Optional[str] = None
    synopsis_text = ""

    for attempt in range(2):  # initial call + 1 reinforcement retry
        try:
            response = call_with_retry(
                client,
                model=model,
                system_prompt=SYNOPSIS_SYSTEM,
                user_prompt=user_content,
                response_format=_RESPONSE_FORMAT,
                max_completion_tokens=max_completion_tokens,
                reasoning_effort=reasoning_effort,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("synopsis call failed for pmid=%s: %s", pmid, e)
            return SynopsisResult(
                pmid=pmid, synopsis=None, model=model,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error=str(e),
            )

        input_tokens_total += _safe_input_tokens(response)
        output_tokens_total += _safe_output_tokens(response)
        response_model = response.model

        raw = response.choices[0].message.content or ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            return SynopsisResult(
                pmid=pmid, synopsis=None, model=response_model,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error=f"json decode: {e}",
            )

        synopsis_text = (payload.get("synopsis") or "").strip() if isinstance(payload, dict) else ""
        if not synopsis_text:
            return SynopsisResult(
                pmid=pmid, synopsis=None, model=response_model,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error="empty synopsis in response",
            )
        if len(synopsis_text) <= SYNOPSIS_MAX_CHARS:
            return SynopsisResult(
                pmid=pmid, synopsis=synopsis_text, model=response_model,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error=None,
            )
        # First overrun: reinforce the limit and retry once.
        logger.info(
            "synopsis length=%d > %d for pmid=%s (attempt %d); retrying with reinforcement",
            len(synopsis_text), SYNOPSIS_MAX_CHARS, pmid, attempt + 1,
        )
        user_content += (
            f"\n\nYour previous attempt was {len(synopsis_text)} characters "
            f"(hard limit {SYNOPSIS_MAX_CHARS}). Produce a strictly shorter version. "
            f"Count characters before answering."
        )

    # Second overrun: preserve the model's actual output, flag the violation.
    # NEVER truncate — drops content the model considered necessary (see #50).
    return SynopsisResult(
        pmid=pmid, synopsis=synopsis_text, model=response_model,
        input_tokens=input_tokens_total, output_tokens=output_tokens_total,
        error=f"length violation: {len(synopsis_text)} chars (limit {SYNOPSIS_MAX_CHARS})",
    )


def _safe_input_tokens(response) -> int:
    """Pull prompt_tokens off the OpenAI response.usage object defensively."""
    try:
        return int(response.usage.prompt_tokens or 0)
    except Exception:
        return 0


def _safe_output_tokens(response) -> int:
    try:
        return int(response.usage.completion_tokens or 0)
    except Exception:
        return 0
