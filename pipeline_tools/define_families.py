"""Family DEFINE pass (#879) — generate a short render-only definition per family.

Near-clone of :func:`pipeline_tools.relabel.relabel_families`. Runs AFTER labels are
stable (the relabel/consolidation pass), generates a 1-2 sentence, capability-framed
definition for every family via the injected ``call_json`` seam (Bedrock Sonnet ->
OpenAI fallback), validates it (length / voice), and accretes it onto the durable
``family_id`` so a re-cluster never orphans it.

Partial-failure tolerant: a batch whose LLM call raises, or a family whose definition
fails validation after one bounded re-prompt, is left ``definition=None`` and flagged —
the run never aborts. The definition is RENDER-ONLY (D-19 LOCKED): nothing downstream
re-consumes it as LLM/embedding/retrieval context.
"""

from __future__ import annotations

import logging
import re

from pipeline_tools.registry import FamilyRegistry

logger = logging.getLogger(__name__)

DEFAULT_DEFINE_BATCH = 40
DEFINE_LOW_CONFIDENCE_FLAG = "family_define_low_confidence"
DEFINE_INVALID_FLAG = "family_define_invalid"

# Hard caps for the validator (the prompt names ~40 words as the soft target; allow a
# little slack before rejecting, so a 41-word answer is not re-prompted needlessly).
MAX_WORDS = 45
MAX_SENTENCES = 2
MIN_CHARS = 12

# Voice guardrails: efficacy/outcome claims and marketing language have no place in a
# neutral, capability-framed definition. Substrings, case-insensitive.
_BANNED_PHRASES = (
    "cutting-edge", "state-of-the-art", "state of the art", "world-class", "world class",
    "gold standard", "best-in-class", "revolutionary", "groundbreaking", "powerful",
    "improves survival", "improves outcomes", "effective for", "effective in treating",
    "safe and effective", "clinically proven", "first-line",
)
_SENTENCE_SPLIT = re.compile(r"[.!?]+(?:\s|$)")


def _validate_definition(text: str) -> tuple[bool, str]:
    """Return (ok, reason). A capability-framed, neutral, 1-2 sentence definition."""
    t = (text or "").strip()
    if len(t) < MIN_CHARS:
        return False, "too short / empty"
    words = t.split()
    if len(words) > MAX_WORDS:
        return False, f"too long ({len(words)} words > {MAX_WORDS})"
    sentences = [s for s in _SENTENCE_SPLIT.split(t) if s.strip()]
    if len(sentences) > MAX_SENTENCES:
        return False, f"too many sentences ({len(sentences)} > {MAX_SENTENCES})"
    low = t.lower()
    for phrase in _BANNED_PHRASES:
        if phrase in low:
            return False, f"banned marketing/efficacy phrase: {phrase!r}"
    return True, ""


def _apply_batch(
    families: FamilyRegistry,
    batch: list[dict],
    *,
    call_json,
    system_prompt: str,
    build_user_message,
    prior_failures: dict | None,
    deltas: list[dict],
    failures: dict[str, str],
    batch_index: int,
    batch_total: int,
) -> None:
    """Define one batch in place: set valid definitions, route the rest into ``failures``."""
    try:
        resp = call_json(system_prompt, build_user_message(batch, prior_failures=prior_failures))
        entries = resp.get("families", []) if isinstance(resp, dict) else []
    except Exception as exc:  # noqa: BLE001 — partial-failure tolerance by design
        logger.warning(
            "define batch %d/%d failed (%s); %d family definition(s) left null",
            batch_index, batch_total, exc, len(batch),
        )
        for fam in batch:
            failures.setdefault(fam["family_id"], "llm call failed")
        return
    model = resp.get("_model") if isinstance(resp, dict) else None
    by_id = {e.get("family_id"): e for e in entries if e.get("family_id")}
    for fam in batch:
        fid = fam["family_id"]
        entry = by_id.get(fid)
        definition = ((entry or {}).get("definition") or "").strip()
        if not entry or not definition:
            failures[fid] = "no definition returned"
            continue
        ok, reason = _validate_definition(definition)
        if not ok:
            failures[fid] = reason
            continue
        failures.pop(fid, None)
        confidence = "low" if str(entry.get("confidence", "")).lower() == "low" else "high"
        families.set_definition(fid, definition, confidence=confidence)
        deltas.append({
            "family_id": fid,
            "definition": definition,
            "confidence": confidence,
            "model": model,
        })


def define_families(
    families: FamilyRegistry,
    *,
    call_json,
    batch_size: int = DEFAULT_DEFINE_BATCH,
) -> list[dict]:
    """Generate a render-only definition for every family; one bounded re-prompt on failure.

    Returns define deltas ``[{family_id, definition, confidence, model}]`` (one per family
    actually defined). Families whose definition never passes validation are left
    ``definition=None`` (the publish field is simply null and SPS shows no definition).
    """
    from prompts.tool_family_define import DEFINE_SYSTEM_PROMPT, build_define_user_message

    records = families.records()
    deltas: list[dict] = []
    failures: dict[str, str] = {}

    batches = [records[i:i + batch_size] for i in range(0, len(records), batch_size)]
    for bi, batch in enumerate(batches, 1):
        _apply_batch(
            families, batch,
            call_json=call_json, system_prompt=DEFINE_SYSTEM_PROMPT,
            build_user_message=build_define_user_message, prior_failures=None,
            deltas=deltas, failures=failures, batch_index=bi, batch_total=len(batches),
        )

    # One bounded re-prompt round over the families that failed (validation or empty),
    # naming the specific defect so the model can correct it (the spotlight critic
    # prior-failure pattern). A family still failing after this is left undefined.
    retry_ids = dict(failures)
    retry_records = [f for f in records if f["family_id"] in retry_ids]
    if retry_records:
        logger.info("define: re-prompting %d family/families that failed validation", len(retry_records))
        retry_batches = [retry_records[i:i + batch_size] for i in range(0, len(retry_records), batch_size)]
        for bi, batch in enumerate(retry_batches, 1):
            prior = {f["family_id"]: retry_ids[f["family_id"]] for f in batch}
            _apply_batch(
                families, batch,
                call_json=call_json, system_prompt=DEFINE_SYSTEM_PROMPT,
                build_user_message=build_define_user_message, prior_failures=prior,
                deltas=deltas, failures=failures, batch_index=bi, batch_total=len(retry_batches),
            )

    defined_ids = {d["family_id"] for d in deltas}
    still_failing = sorted(fid for fid in failures if fid not in defined_ids)
    logger.info(
        "define: %d/%d families defined (%d low-confidence, %d undefined)",
        len(deltas), len(records),
        sum(1 for d in deltas if d["confidence"] == "low"), len(still_failing),
    )
    return deltas
