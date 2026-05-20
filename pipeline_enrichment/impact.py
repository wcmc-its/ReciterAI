"""Impact scoring — per-PMID worker.

Ported from POC `core/impact.py` + `core/impact_scoring_legacy.py`
(publications path only). Scores impact on AWS Bedrock Claude Sonnet
4.6 with a one-shot OpenAI gpt-5.1 content-filter fallback (see
`pipeline_enrichment.llm_call`). Uses the v2 prompt by default.

Inputs: a `pub_data` dict containing the bibliometric columns produced
by the upstream MariaDB query — `articleTitle`, `journalTitleVerbose`,
`articleYear`, `citationCountNIH`, `percentileNIH`,
`relativeCitationRatioNIH`, `datePublicationAddedToEntrez`,
`abstractVarchar`, `pmid`. Missing optional fields are gracefully
omitted from the prompt.

Output: ImpactResult dataclass — score (0–100) + justification + usage
metadata for cost attribution + the model actually used.

Justification length (≤120 chars / ≤10 words) is enforced by a Python
reinforcement-retry loop (initial call + 1 retry); the model's output
is never truncated — see #50 / feedback_no_arbitrary_truncation.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from pipeline_enrichment.llm_call import call_with_fallback, parse_json_lenient
from pipeline_enrichment.prompts import build_impact_user_content, get_impact_system_prompt
from utils.bedrock_client import BedrockClient, SONNET_MODEL

logger = logging.getLogger(__name__)

# Justification length constraints from IMPACT_PROMPT_V2 + IMPACT_SCHEMA:
# - ≤120 characters
# - ≤10 words ("≤10 words summarizing the reasoning")
# Bedrock Converse has no strict-JSON response mode, so IMPACT_SCHEMA is a
# Python-side postcondition only — these limits are enforced solely by the
# reinforcement-retry loop below (initial call + 1 retry). The #37 step 2
# bootstrap surfaced 4.2% word-count violations (76/1,815); char-count
# violations were 0/1,815, so the loop stays at one retry (synopsis widened
# to two — impact did not need it). A second overrun preserves the model's
# actual output with an error flag — NEVER truncate, since that drops
# content the model considered necessary (see #50).
JUSTIF_MAX_CHARS = 120
JUSTIF_MAX_WORDS = 10


@dataclass
class ImpactResult:
    """One impact scoring outcome.

    `impact_score` is None on failure; `error` carries the cause.
    `model` is the model actually used — Bedrock Sonnet 4.6, or `gpt-5.1`
    when the content-filter fallback fired. `prompt_version` records which
    version of the impact *prompt* was used (the prompt text, not the
    model), so a future evaluation can stratify scores by prompt revision.
    `input_tokens` / `output_tokens` feed `CostAccumulator.record`.
    """

    pmid: str
    impact_score: Optional[int]
    justification: Optional[str]
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    error: Optional[str] = None


def score_impact(
    *,
    pub_data: dict,
    client: BedrockClient | None = None,
    model: str = SONNET_MODEL,
    prompt_version: str | None = None,
    max_tokens: int = 4096,
) -> ImpactResult:
    """Score one publication's impact on Bedrock Sonnet 4.6.

    Args:
        pub_data: bibliometric dict. Must include `pmid`. Other fields
            are passed through `build_impact_user_content`; missing
            optionals are omitted from the prompt.
        client: Bedrock client; defaults to the shared lazy singleton in
            `pipeline_enrichment.llm_call`. The OpenAI content-filter
            fallback's client is managed inside that module.
        model: Bedrock model ID (default SONNET_MODEL).
        prompt_version: impact prompt version ("v1" or "v2"). Defaults
            to the current default ("v2" as of 2025-12-28).
        max_tokens: Bedrock response token cap. Raised from 512 → 4096
            after the 2026-05-20 11:00 UTC tick failed on PMID 42119587:
            Sonnet 4.6 emitted ~500 tokens of chain-of-thought analysis
            before the closing ``json fence, hitting the 512 cap mid-JSON
            and returning a truncated body the lenient parser couldn't
            recover. 4096 is well over the observed reasoning + JSON
            envelope (~600–800 tokens) with cost-neutral headroom —
            Bedrock bills emitted tokens, not the budget.

    Returns:
        ImpactResult with score 0–100 + justification on success, and
        `.model` set to the model actually used (Sonnet, or `gpt-5.1`
        when the content-filter fallback fired).
    """
    pmid = str(pub_data.get("pmid", "?"))
    system_prompt = get_impact_system_prompt(prompt_version)
    resolved_version = prompt_version or "v2"
    user_content = (
        "Please analyze the following publication and provide an impact score:\n\n"
        + build_impact_user_content(pub_data)
    )

    input_tokens_total = 0
    output_tokens_total = 0
    response_model: str = model
    score: Optional[int] = None
    justification: Optional[str] = None

    for attempt in range(2):  # initial call + 1 reinforcement retry on length violation
        try:
            call_result = call_with_fallback(
                system_prompt=system_prompt,
                user_prompt=user_content,
                model=model,
                max_tokens=max_tokens,
                bedrock_client=client,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("impact call failed for pmid=%s: %s", pmid, e)
            return ImpactResult(
                pmid=pmid, impact_score=None, justification=None,
                model=response_model, prompt_version=resolved_version,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error=str(e),
            )

        input_tokens_total += call_result.input_tokens
        output_tokens_total += call_result.output_tokens
        response_model = call_result.model

        try:
            payload = parse_json_lenient(call_result.text)
        except json.JSONDecodeError as e:
            return ImpactResult(
                pmid=pmid, impact_score=None, justification=None,
                model=response_model, prompt_version=resolved_version,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error=f"json decode: {e}",
            )

        if not isinstance(payload, dict) or "impactScore" not in payload:
            return ImpactResult(
                pmid=pmid, impact_score=None, justification=None,
                model=response_model, prompt_version=resolved_version,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error=f"missing impactScore in response: {call_result.text[:200]}",
            )

        # POC clamps to 0..100 and coerces to int; mirror exactly.
        try:
            score = max(0, min(100, int(payload["impactScore"])))
        except (TypeError, ValueError) as e:
            return ImpactResult(
                pmid=pmid, impact_score=None, justification=None,
                model=response_model, prompt_version=resolved_version,
                input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                error=f"impactScore not int-coercible: {e}",
            )
        justification = str(payload.get("justification") or "").strip() or None

        # Length postcondition. Retry once on violation; never truncate.
        if justification:
            word_count = len(justification.split())
            char_count = len(justification)
            over_chars = char_count > JUSTIF_MAX_CHARS
            over_words = word_count > JUSTIF_MAX_WORDS
            if over_chars or over_words:
                if attempt == 0:
                    logger.info(
                        "impact justification violation for pmid=%s (chars=%d, words=%d); "
                        "retrying with reinforcement",
                        pmid, char_count, word_count,
                    )
                    user_content += (
                        f"\n\nYour previous justification was {char_count} characters / "
                        f"{word_count} words. Hard limits: ≤{JUSTIF_MAX_CHARS} characters "
                        f"AND ≤{JUSTIF_MAX_WORDS} words. Re-score with a strictly shorter "
                        f"justification. Count words and characters before answering."
                    )
                    continue
                # Second overrun: preserve actual output, flag the violation.
                violations = []
                if over_chars:
                    violations.append(f"{char_count} chars (limit {JUSTIF_MAX_CHARS})")
                if over_words:
                    violations.append(f"{word_count} words (limit {JUSTIF_MAX_WORDS})")
                return ImpactResult(
                    pmid=pmid, impact_score=score, justification=justification,
                    model=response_model, prompt_version=resolved_version,
                    input_tokens=input_tokens_total, output_tokens=output_tokens_total,
                    error=f"length violation: {', '.join(violations)}",
                )

        return ImpactResult(
            pmid=pmid, impact_score=score, justification=justification,
            model=response_model, prompt_version=resolved_version,
            input_tokens=input_tokens_total, output_tokens=output_tokens_total,
            error=None,
        )

    # Unreachable (loop always returns), but keep type-checker happy.
    return ImpactResult(
        pmid=pmid, impact_score=score, justification=justification,
        model=response_model, prompt_version=resolved_version,
        input_tokens=input_tokens_total, output_tokens=output_tokens_total,
        error="unexpected: exited validator loop without return",
    )
