"""Shared LLM call path for the daily-enrichment workers.

Synopsis and impact generation both run the same call pattern: an AWS
Bedrock Claude Sonnet 4.6 happy path, with a one-shot OpenAI gpt-5.1
fallback when Bedrock's model-side safety filter blocks the response.

Why the fallback exists
-----------------------
Sonnet 4.6 reproducibly content-filters WCM's biomedical animal-model
abstracts (NSG immunocompromised-mouse strains, euthanasia / humane-
endpoint language). The hot path's first production run content-filtered
every PMID in its delta window — see the planning note
`sonnet-content-filter-on-dense-scoring.md`. `score_publications._dense_score`
already runs the identical Bedrock→OpenAI fallback against the same
corpus; this module is the enrichment job's copy of that pattern,
factored once so synopsis and impact share it.

The fallback uses the org-shared `reciterai/openai-api-key` secret — the
same secret `score_publications` already uses in production — so it adds
no new credential surface.

`call_with_fallback` returns the response *text* plus token usage and the
model actually used; callers do their own lenient JSON parse
(`parse_json_lenient`) and their own length-retry loops.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from utils.bedrock_client import (
    BedrockClient,
    BedrockEmptyContentError,
    SONNET_MODEL,
)
from utils.openai_client import (
    GPT5_MODEL,
    call_with_retry as openai_call_with_retry,
    get_default_client as get_default_openai_client,
)

logger = logging.getLogger(__name__)

# Bedrock Converse stopReasons that mean the model-side safety filter
# blocked the response — the trigger for the OpenAI content-filter
# fallback. Any *other* empty return (e.g. 'max_tokens', or 'end_turn'
# with no content) is treated as a transient miss and retried on Bedrock
# once before failing.
CONTENT_FILTER_STOP_REASONS = frozenset({"content_filtered", "guardrail_intervened"})


@dataclass
class LLMCallResult:
    """One enrichment LLM call outcome, provider-agnostic.

    `model` is the model *actually used* — `SONNET_MODEL` on the Bedrock
    happy path, or `GPT5_MODEL` when the content-filter fallback fired —
    so per-call cost attribution and the persisted `model` columns stay
    accurate.
    """

    text: str
    input_tokens: int
    output_tokens: int
    model: str


# Module-level lazy Bedrock client, mirroring
# utils.openai_client.get_default_client. Construction is cheap (the boto3
# client is itself created lazily on first call), but caching it lets the
# per-PMID enrichment loop reuse one boto3 connection.
_default_bedrock_client: Optional[BedrockClient] = None


def get_default_bedrock_client() -> BedrockClient:
    """Return the module-level default BedrockClient, creating it on first use."""
    global _default_bedrock_client
    if _default_bedrock_client is None:
        _default_bedrock_client = BedrockClient()
    return _default_bedrock_client


def parse_json_lenient(text: str) -> dict:
    """Parse a JSON object from an LLM response, tolerating wrapping.

    Bedrock Converse has no strict-JSON response mode, so Claude may wrap
    the object in a ```json fence or surround it with prose. Strips fences
    first; if that still does not parse, extracts the first `{...}` block.

    Raises:
        json.JSONDecodeError: if no JSON object can be recovered.
    """
    cleaned = re.sub(r'```json\n?|\n?```', '', text or '').strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def call_with_fallback(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str = SONNET_MODEL,
    max_tokens: int = 512,
    bedrock_client: Optional[BedrockClient] = None,
    openai_client: Optional[OpenAI] = None,
    openai_max_completion_tokens: int = 8000,
) -> LLMCallResult:
    """Call Bedrock for an enrichment generation, with an OpenAI fallback.

    Happy path: one `BedrockClient.call_with_usage` call on `model`
    (Sonnet 4.6 by default). On a content-filter block it falls back once
    to OpenAI `gpt-5.1` on the *same* system + user prompt. On any other
    empty return it retries Bedrock once.

    Args:
        system_prompt, user_prompt: the prompt, used verbatim for both the
            Bedrock call and (if it fires) the OpenAI fallback.
        model: the Bedrock model ID for the happy path.
        max_tokens: Bedrock `maxTokens`. The enrichment JSON objects are
            small, so the default 512 is ample.
        bedrock_client: injectable BedrockClient; defaults to the module
            lazy singleton.
        openai_client: injectable OpenAI client for the fallback; defaults
            to the lazy `utils.openai_client` singleton, constructed only
            if the fallback actually fires (it needs OPENAI_API_KEY).
        openai_max_completion_tokens: token ceiling for the gpt-5.1 fallback.
            gpt-5.1 is a reasoning model, so this covers reasoning + the small
            visible JSON; 8000 keeps the headroom the retired OpenAI synopsis
            path used (score_publications._dense_score sizes its own fallback
            lower, at 4096, for denser topic-scoring output).

    Returns:
        LLMCallResult — response text, token usage, and the model actually
        used.

    Raises:
        BedrockEmptyContentError: a non-content-filter empty return whose
            single Bedrock retry also came back empty.
        RuntimeError: Bedrock content-filtered AND the OpenAI fallback
            also failed.
        botocore.exceptions.ClientError: non-retryable Bedrock errors.
    """
    bedrock = bedrock_client or get_default_bedrock_client()

    try:
        return _bedrock_call(bedrock, model, system_prompt, user_prompt, max_tokens)
    except BedrockEmptyContentError as empty_err:
        if empty_err.stop_reason in CONTENT_FILTER_STOP_REASONS:
            return _openai_fallback(
                system_prompt, user_prompt, openai_client,
                openai_max_completion_tokens, empty_err,
            )
        # Not a content filter — a transient empty return (long-abstract
        # misses were observed in the model spike). Retry Bedrock once; a
        # second empty propagates as a hard failure the caller marks
        # per-PMID.
        logger.info(
            "Bedrock returned empty content (stopReason=%r, not a content "
            "filter); retrying once",
            empty_err.stop_reason,
        )
        return _bedrock_call(bedrock, model, system_prompt, user_prompt, max_tokens)


def _bedrock_call(
    bedrock: BedrockClient, model: str, system_prompt: str,
    user_prompt: str, max_tokens: int,
) -> LLMCallResult:
    """One Bedrock Converse call → LLMCallResult tagged with the Bedrock model."""
    result = bedrock.call_with_usage(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
        max_tokens=max_tokens,
    )
    return LLMCallResult(
        text=result.text,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        model=model,
    )


def _openai_fallback(
    system_prompt: str, user_prompt: str, openai_client: Optional[OpenAI],
    max_completion_tokens: int, bedrock_err: BedrockEmptyContentError,
) -> LLMCallResult:
    """OpenAI gpt-5.1 fallback after a Bedrock content-filter block.

    Mirrors `score_publications._dense_score`. Raises RuntimeError (chained
    from the underlying error) if the fallback itself fails, so the caller
    sees one clear "filtered and fallback failed" message.
    """
    logger.info(
        "Bedrock content-filtered (stopReason=%r); falling back to %s",
        bedrock_err.stop_reason, GPT5_MODEL,
    )
    client = openai_client or get_default_openai_client()
    try:
        completion = openai_call_with_retry(
            client,
            model=GPT5_MODEL,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format={"type": "json_object"},
            max_completion_tokens=max_completion_tokens,
        )
    except Exception as fallback_err:  # noqa: BLE001
        raise RuntimeError(
            f"Bedrock content-filtered (stopReason="
            f"{bedrock_err.stop_reason!r}) and the {GPT5_MODEL} fallback "
            f"also failed: {fallback_err}"
        ) from fallback_err
    text = (completion.choices[0].message.content or "").strip()
    return LLMCallResult(
        text=text,
        input_tokens=_safe_openai_tokens(completion, "prompt_tokens"),
        output_tokens=_safe_openai_tokens(completion, "completion_tokens"),
        model=GPT5_MODEL,
    )


def _safe_openai_tokens(completion, attr: str) -> int:
    """Pull a token count off an OpenAI completion's usage object defensively.

    A missing or malformed usage object yields 0 — the fallback synopsis
    itself is still usable; only its cost attribution is lost. The warning
    makes a systematic usage-parsing failure visible rather than silent.
    """
    try:
        return int(getattr(completion.usage, attr) or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "could not read OpenAI usage.%s (%s); recording 0 tokens", attr, e
        )
        return 0
