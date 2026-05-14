"""Impact scoring — per-PMID worker.

Ported from POC `core/impact.py` + `core/impact_scoring_legacy.py`
(publications path only). Calls GPT-5.1 via Chat Completions with the
IMPACT_SCHEMA structured response format. Uses v2 prompt by default
(parity-corrected, 2025-12-28).

Inputs: a `pub_data` dict containing the bibliometric columns produced
by the upstream MariaDB query — `articleTitle`, `journalTitleVerbose`,
`articleYear`, `citationCountNIH`, `percentileNIH`,
`relativeCitationRatioNIH`, `datePublicationAddedToEntrez`,
`abstractVarchar`, `pmid`. Missing optional fields are gracefully
omitted from the prompt.

Output: ImpactResult dataclass — score (0–100) + justification + usage
metadata for cost attribution.

Scheduled wiring lands in #37 step 2.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from pipeline_enrichment.prompts import (
    IMPACT_SCHEMA,
    build_impact_user_content,
    get_impact_system_prompt,
)
from utils.openai_client import GPT5_MODEL, call_with_retry, get_default_client

logger = logging.getLogger(__name__)


@dataclass
class ImpactResult:
    """One impact scoring outcome.

    `impact_score` is None on failure; `error` carries the cause.
    `prompt_version` records which version of the impact prompt was used,
    so a future evaluation can stratify scores by prompt revision.
    """

    pmid: str
    impact_score: Optional[int]
    justification: Optional[str]
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    error: Optional[str] = None


# POC `core/impact_scoring_legacy.py:_score_impact_for_publication` sends
# response_format={"type": "json_object"}, NOT a json_schema. We mirror
# that exactly to preserve equivalence; the schema lives in code as a
# postcondition check, not in the request envelope.
_RESPONSE_FORMAT = {"type": "json_object"}


def score_impact(
    *,
    pub_data: dict,
    client: OpenAI | None = None,
    model: str = GPT5_MODEL,
    prompt_version: str | None = None,
    max_completion_tokens: int = 8000,
    reasoning_effort: str = "medium",
) -> ImpactResult:
    """Score one publication's impact.

    Args:
        pub_data: bibliometric dict. Must include `pmid`. Other fields
            are passed through `build_impact_user_content`; missing
            optionals are omitted from the prompt.
        client: OpenAI client; defaults to the module's lazy singleton.
        model: model ID (default GPT5_MODEL = "gpt-5.1").
        prompt_version: impact prompt version ("v1" or "v2"). Defaults
            to the current default ("v2" as of 2025-12-28).
        max_completion_tokens: GPT-5 reasoning + visible-token ceiling.
        reasoning_effort: forwarded to GPT-5; POC v2 prompt was tuned
            against `medium` so we default to that.

    Returns:
        ImpactResult with score 0–100 + justification on success.
    """
    pmid = str(pub_data.get("pmid", "?"))
    client = client or get_default_client()
    system_prompt = get_impact_system_prompt(prompt_version)
    resolved_version = prompt_version or "v2"
    user_content = (
        "Please analyze the following publication and provide an impact score:\n\n"
        + build_impact_user_content(pub_data)
    )

    try:
        response = call_with_retry(
            client,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_content,
            response_format=_RESPONSE_FORMAT,
            max_completion_tokens=max_completion_tokens,
            reasoning_effort=reasoning_effort,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("impact call failed for pmid=%s: %s", pmid, e)
        return ImpactResult(
            pmid=pmid, impact_score=None, justification=None,
            model=model, prompt_version=resolved_version,
            input_tokens=0, output_tokens=0, error=str(e),
        )

    raw = response.choices[0].message.content or ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        return ImpactResult(
            pmid=pmid, impact_score=None, justification=None,
            model=response.model, prompt_version=resolved_version,
            input_tokens=_safe_input_tokens(response),
            output_tokens=_safe_output_tokens(response),
            error=f"json decode: {e}",
        )

    if not isinstance(payload, dict) or "impactScore" not in payload:
        return ImpactResult(
            pmid=pmid, impact_score=None, justification=None,
            model=response.model, prompt_version=resolved_version,
            input_tokens=_safe_input_tokens(response),
            output_tokens=_safe_output_tokens(response),
            error=f"missing impactScore in response: {raw[:200]}",
        )

    # POC clamps to 0..100 and coerces to int; mirror exactly.
    try:
        score = max(0, min(100, int(payload["impactScore"])))
    except (TypeError, ValueError) as e:
        return ImpactResult(
            pmid=pmid, impact_score=None, justification=None,
            model=response.model, prompt_version=resolved_version,
            input_tokens=_safe_input_tokens(response),
            output_tokens=_safe_output_tokens(response),
            error=f"impactScore not int-coercible: {e}",
        )
    justification = str(payload.get("justification") or "").strip() or None

    # Post-hoc schema check: justification is supposed to be ≤120 chars.
    # If it overruns, truncate + log; we'd rather store a truncated
    # justification than fail the whole row.
    if justification and len(justification) > 120:
        logger.info(
            "impact justification overran 120 chars for pmid=%s (len=%d); truncating",
            pmid, len(justification),
        )
        justification = justification[:120]

    return ImpactResult(
        pmid=pmid,
        impact_score=score,
        justification=justification,
        model=response.model,
        prompt_version=resolved_version,
        input_tokens=_safe_input_tokens(response),
        output_tokens=_safe_output_tokens(response),
        error=None,
    )


def _safe_input_tokens(response) -> int:
    try:
        return int(response.usage.prompt_tokens or 0)
    except Exception:
        return 0


def _safe_output_tokens(response) -> int:
    try:
        return int(response.usage.completion_tokens or 0)
    except Exception:
        return 0
