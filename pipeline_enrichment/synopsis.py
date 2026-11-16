"""Synopsis generation — per-PMID worker.

Generates a short publication synopsis (≤120 chars accepted) on AWS Bedrock Claude Sonnet,
with a one-shot OpenAI gpt-5.1 content-filter fallback (see
`pipeline_enrichment.llm_call`). Ported from POC `core/synopsis.py`.

Inputs: (pmid, title, journal, year, abstract)
Output: SynopsisResult — synopsis string + token usage for cost
attribution + the model actually used (Sonnet, or gpt-5.1 on a fallback).

Length enforcement: Bedrock Converse has no strict-JSON `maxLength`, so the
≤120-char rule is enforced solely by the Python reinforcement-retry loop
below (initial call + one retry). The model's actual output is
never truncated — see #50 / feedback_no_arbitrary_truncation.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from pipeline_enrichment.llm_call import call_with_fallback, parse_json_lenient
from pipeline_enrichment.prompts import (
    STAGE_CONFIDENCES,
    SYNOPSIS_SYSTEM,
    TRANSLATIONAL_STAGES,
    build_synopsis_user_content,
)
from utils.bedrock_client import BedrockClient, SONNET_MODEL

logger = logging.getLogger(__name__)

# Length rule: the prompt asks for 70 characters (prompts.SYNOPSIS_SYSTEM) and
# up to SYNOPSIS_MAX_CHARS is accepted. Sonnet 5.5 overshoots a stated limit by
# about 25-30 characters, so asking for the cap itself made nearly every first
# attempt overrun and retry; asking for 70 lands 97% of first attempts within
# 120 (measured 2026-11-16, n=100). Bedrock Converse has no strict-JSON
# `maxLength`, so this check is the SOLE enforcement. On a final overrun the
# model's actual output is preserved with a length-violation error, NEVER
# truncated, since truncation silently drops content the model considered
# necessary for user-facing faculty-page text (see #50).
SYNOPSIS_MAX_CHARS = 120

# Initial call + 1 reinforcement retry. A retry should be rare (about 3% of
# papers); it is logged at WARNING so a regression shows up in CloudWatch
# instead of only in the token bill.
SYNOPSIS_MAX_ATTEMPTS = 2


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
    # Translational stage from the same call (publication prompt only). None
    # when the prompt does not ask for one (grants, trials) or the model's
    # value is missing or not a known label. Never a reason to fail the paper:
    # the synopsis is the product, the stage is an extra.
    stage: Optional[str] = None
    stage_confidence: Optional[str] = None


def _parse_stage(payload) -> tuple[Optional[str], Optional[str]]:
    """Return (stage, confidence) from a parsed response, each None unless valid."""
    if not isinstance(payload, dict):
        return None, None
    stage = str(payload.get("stage") or "").strip().upper()
    if stage not in TRANSLATIONAL_STAGES:
        return None, None
    conf = str(payload.get("stage_confidence") or "").strip().lower()
    return stage, (conf if conf in STAGE_CONFIDENCES else None)


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
    system_prompt: str | None = None,
    user_content_builder: Callable[..., str] | None = None,
) -> SynopsisResult:
    """Generate a synopsis (≤120 chars accepted) for one PMID on Bedrock Sonnet.

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
        system_prompt: system prompt override for non-publication objects
            (grants, trials). None keeps the publication `SYNOPSIS_SYSTEM`.
        user_content_builder: user-content builder override, called with the
            keyword arguments `title`, `journal`, `year`, `abstract`. None
            keeps `build_synopsis_user_content`. Callers reuse the `journal`
            slot for their own context line (funder, trial phase).

    Returns:
        SynopsisResult. On success `.synopsis` is the produced string and
        `.model` is the model actually used. On failure `.synopsis` is None
        and `.error` describes the issue.
    """
    builder = user_content_builder or build_synopsis_user_content
    system = system_prompt if system_prompt is not None else SYNOPSIS_SYSTEM
    user_content = builder(
        title=title, journal=journal, year=year, abstract=abstract
    )

    input_tokens_total = 0
    output_tokens_total = 0
    response_model: str = model
    synopsis_text = ""
    stage: Optional[str] = None
    stage_confidence: Optional[str] = None

    for attempt in range(SYNOPSIS_MAX_ATTEMPTS):
        try:
            call_result = call_with_fallback(
                system_prompt=system,
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
        stage, stage_confidence = _parse_stage(payload)
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
                error=None, stage=stage, stage_confidence=stage_confidence,
            )
        # Overrun — reinforce the limit and retry (unless this was the last
        # attempt, in which case the loop falls through to the block below).
        logger.warning(
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
        stage=stage, stage_confidence=stage_confidence,
    )
