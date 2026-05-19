"""Synopsis generation — per-PMID worker.

Generates a ≤95-char publication synopsis on AWS Bedrock Claude Sonnet 4.6,
with a one-shot OpenAI gpt-5.1 content-filter fallback (see
`pipeline_enrichment.llm_call`). Ported from POC `core/synopsis.py`.

Inputs: (pmid, title, journal, year, abstract)
Output: SynopsisResult — synopsis string + token usage for cost
attribution + the model actually used (Sonnet, or gpt-5.1 on a fallback).

Length enforcement: Bedrock Converse has no strict-JSON `maxLength`, so the
≤95-char rule is enforced solely by the Python reinforcement-retry loop
below (initial call + up to two retries). The model's actual output is
never truncated — see #50 / feedback_no_arbitrary_truncation.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from pipeline_enrichment.llm_call import call_with_fallback, parse_json_lenient
from pipeline_enrichment.prompts import SYNOPSIS_SYSTEM, build_synopsis_user_content
from utils.bedrock_client import BedrockClient, SONNET_MODEL

logger = logging.getLogger(__name__)

# The ≤95-char synopsis rule is enforced by a reinforcement-retry loop: an
# initial call plus up to two retries, each retry reinforcing the limit in
# the prompt. Bedrock Converse has no strict-JSON `maxLength` to lean on
# (the retired OpenAI path used a `json_schema`), so this loop is the SOLE
# enforcement. On a final overrun the model's actual output is preserved
# with a length-violation error — NEVER truncated, since truncation
# silently drops content the model considered necessary for user-facing
# faculty-page text (see #50).
SYNOPSIS_MAX_CHARS = 95

# Initial call + 2 reinforcement retries. Widened from 1 retry (D4): the
# Bedrock model spike overran 95 chars on ~23% of first attempts.
SYNOPSIS_MAX_ATTEMPTS = 3


@dataclass
class SynopsisResult:
    """One synopsis generation outcome.

    `synopsis` is None when the call failed; `error` holds the message.
    `model` is the model actually used — Bedrock Sonnet 4.6, or `gpt-5.1`
    when the content-filter fallback fired. `input_tokens` / `output_tokens`
    feed `utils.llm_cost.CostAccumulator.record`.
    """

    pmid: str
    synopsis: Optional[str]
    model: str
    input_tokens: int
    output_tokens: int
    error: Optional[str] = None


def generate_synopsis(
    *,
    pmid: str,
    title: str,
    journal: str | None = None,
    year: int | None = None,
    abstract: str | None = None,
    client: BedrockClient | None = None,
    model: str = SONNET_MODEL,
    max_tokens: int = 512,
) -> SynopsisResult:
    """Generate a ≤95-char synopsis for one PMID on Bedrock Sonnet 4.6.

    Args:
        pmid: PMID string, used only for tagging the result + log lines.
        title: publication title (required).
        journal, year, abstract: optional context; abstract is the
            primary driver of synopsis content.
        client: Bedrock client; defaults to the shared lazy singleton in
            `pipeline_enrichment.llm_call`. The OpenAI content-filter
            fallback's client is managed inside that module.
        model: Bedrock model ID (default SONNET_MODEL).
        max_tokens: Bedrock response token cap. The synopsis JSON object
            is tiny, so 512 is ample.

    Returns:
        SynopsisResult. On success `.synopsis` is the produced string and
        `.model` is the model actually used. On failure `.synopsis` is None
        and `.error` describes the issue.
    """
    user_content = build_synopsis_user_content(
        title=title, journal=journal, year=year, abstract=abstract
    )

    input_tokens_total = 0
    output_tokens_total = 0
    response_model: str = model
    synopsis_text = ""

    for attempt in range(SYNOPSIS_MAX_ATTEMPTS):
        try:
            call_result = call_with_fallback(
                system_prompt=SYNOPSIS_SYSTEM,
                user_prompt=user_content,
                model=model,
                max_tokens=max_tokens,
                bedrock_client=client,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("synopsis call failed for pmid=%s: %s", pmid, e)
            return SynopsisResult(
                pmid=pmid, synopsis=None, model=response_model,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error=str(e),
            )

        input_tokens_total += call_result.input_tokens
        output_tokens_total += call_result.output_tokens
        response_model = call_result.model

        try:
            payload = parse_json_lenient(call_result.text)
        except json.JSONDecodeError as e:
            return SynopsisResult(
                pmid=pmid, synopsis=None, model=response_model,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error=f"json decode: {e}",
            )

        synopsis_text = (
            (payload.get("synopsis") or "").strip()
            if isinstance(payload, dict) else ""
        )
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
        # Overrun — reinforce the limit and retry (unless this was the last
        # attempt, in which case the loop falls through to the block below).
        logger.info(
            "synopsis length=%d > %d for pmid=%s (attempt %d/%d); "
            "retrying with reinforcement",
            len(synopsis_text), SYNOPSIS_MAX_CHARS, pmid,
            attempt + 1, SYNOPSIS_MAX_ATTEMPTS,
        )
        user_content += (
            f"\n\nYour previous attempt was {len(synopsis_text)} characters "
            f"(hard limit {SYNOPSIS_MAX_CHARS}). Produce a strictly shorter "
            f"version. Count characters before answering."
        )

    # Every attempt overran. Preserve the model's actual output, flag the
    # violation — NEVER truncate (see #50).
    return SynopsisResult(
        pmid=pmid, synopsis=synopsis_text, model=response_model,
        input_tokens=input_tokens_total, output_tokens=output_tokens_total,
        error=f"length violation: {len(synopsis_text)} chars "
              f"(limit {SYNOPSIS_MAX_CHARS})",
    )
