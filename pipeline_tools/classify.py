"""Disposition gate + capability classifier (docs/tool-classifier-spec.md §0.5-§6).

For each extracted mention the LLM assigns a ``disposition`` (§0.5, run FIRST),
and for ``method_tool`` records a ``kind`` (§2), ``supercategory`` (§1), and
``attributes`` (§3) per the §6 routing rules. This module batches the calls
(partial-failure tolerant), validates every result against the frozen vocabulary,
and raises review flags that feed the §9(c) exceptions queue.

The LLM call is behind an injected ``call_json(system, user) -> dict`` seam, so
the classifier unit-tests with a stub and never touches AWS/OpenAI. The live seam
(``make_classifier_call_json``) is Bedrock Sonnet primary with an OpenAI gpt-5.x
fallback on a content-filter/empty/transient failure (the operator has no
Anthropic Console key; OpenAI is the cross-model fallback of record).
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from pipeline_tools import vocab
from pipeline_tools.registry import norm_name

logger = logging.getLogger(__name__)

CallJson = Callable[[str, str], dict]  # (system_prompt, user_prompt) -> parsed JSON

DEFAULT_BATCH_SIZE = 50

# Review flags (feed the §9c exceptions queue).
FLAG_LOW_CONFIDENCE = "low_confidence_supercategory"
FLAG_INVALID_SUPERCATEGORY = "invalid_supercategory_routed_to_other"
FLAG_INVALID_KIND = "invalid_kind_nulled"
FLAG_INVALID_DISPOSITION = "invalid_disposition_defaulted_method_tool"
FLAG_INFRASTRUCTURE = "infrastructure_spot_audit"
FLAG_LLM_ERROR = "llm_error_unclassified"
FLAG_MISSING = "missing_from_llm_output"


def _coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return default


def normalize_classification(entry: dict, mention: dict) -> dict:
    """Validate one LLM classification against the frozen vocab; attach review flags.

    Coercion policy (fail SAFE, never drop — flag for review instead):
      - invalid/missing disposition -> method_tool, flagged.
      - method_tool with invalid/missing supercategory -> 'other' (gated remainder), flagged.
      - method_tool with invalid kind -> null, flagged.
      - infrastructure/excluded -> capability fields forced null (§9); infra flagged for spot-audit.
      - attributes: bad enums -> null; bools coerced; nulls backfilled from the §4 legacy prior.
    """
    flags: list[str] = []

    disposition = entry.get("disposition")
    if not vocab.is_valid_disposition(disposition):
        flags.append(FLAG_INVALID_DISPOSITION)
        disposition = vocab.DEFAULT_DISPOSITION

    rec = {
        "raw_name": entry.get("raw_name") or mention.get("raw_name", ""),
        "disposition": disposition,
        "kind": None,
        "supercategory": None,
        "attributes": vocab.default_attributes(),
        "confidence": "low" if str(entry.get("confidence", "")).lower() == "low" else "high",
        "notes": (entry.get("notes") or "").strip(),
        "flags": flags,
    }

    if disposition != vocab.CAPABILITY_DISPOSITION:
        # infrastructure / excluded carry no capability fields (§9).
        if disposition == "infrastructure":
            flags.append(FLAG_INFRASTRUCTURE)
        return rec

    # --- method_tool: kind / supercategory / attributes -------------------
    kind = entry.get("kind")
    if vocab.is_valid_kind(kind):
        rec["kind"] = kind
    else:
        flags.append(FLAG_INVALID_KIND)

    supercat = entry.get("supercategory")
    if vocab.is_valid_supercategory(supercat):
        rec["supercategory"] = supercat
    else:
        rec["supercategory"] = vocab.OTHER_SUPERCATEGORY
        flags.append(FLAG_INVALID_SUPERCATEGORY)

    rec["attributes"] = _normalize_attributes(entry.get("attributes") or {}, mention)

    if rec["confidence"] == "low":
        flags.append(FLAG_LOW_CONFIDENCE)
    return rec


def _normalize_attributes(attrs: dict, mention: dict) -> dict:
    """Coerce attribute enums/bools (§3); backfill nulls from the §4 legacy prior."""
    out = vocab.default_attributes()
    delivery = attrs.get("delivery")
    out["delivery"] = delivery if delivery in vocab.DELIVERY_VALUES else None
    provenance = attrs.get("provenance")
    out["provenance"] = provenance if provenance in vocab.PROVENANCE_VALUES else None
    license_ = attrs.get("license")
    out["license"] = license_ if license_ in vocab.LICENSE_VALUES else None
    out["consumable"] = _coerce_bool(attrs.get("consumable"))
    out["rrid_candidate"] = _coerce_bool(attrs.get("rrid_candidate"))

    # §4 weak prior: deterministic attribute hints fill ONLY still-null fields.
    prior_attrs = vocab.legacy_prior(mention.get("tool_category")).get("attrs", {})
    for field in ("delivery", "provenance", "license"):
        if out[field] is None and prior_attrs.get(field) is not None:
            out[field] = prior_attrs[field]
    if not out["consumable"] and prior_attrs.get("consumable"):
        out["consumable"] = True
    return out


def classify_mentions(
    mentions: list[dict],
    *,
    call_json: CallJson,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[dict]:
    """Classify every mention; return normalized records aligned to ``mentions``.

    Partial-failure tolerant: a batch whose LLM call raises leaves its mentions
    UNCLASSIFIED (disposition None, flag ``llm_error``) and the run continues —
    a failed batch never aborts the others, and the drop is logged, never silent.
    Mentions the LLM omits from an otherwise-good batch are flagged ``missing``.
    """
    results: list[dict] = []
    batches = [mentions[i:i + batch_size] for i in range(0, len(mentions), batch_size)]
    for bi, batch in enumerate(batches, 1):
        results.extend(classify_batch(batch, call_json=call_json, label=f"{bi}/{len(batches)}"))
    return results


def classify_batch(batch: list[dict], *, call_json: CallJson, label: str = "") -> list[dict]:
    """Classify ONE batch; return normalized records aligned to ``batch``.

    The unit of work for both the sequential ``classify_mentions`` loop and the
    parallel corpus-mode classifier. Partial-failure tolerant: an LLM call that
    raises leaves the whole batch UNCLASSIFIED (flagged), never aborting the run;
    a mention the LLM omits is flagged ``missing``.
    """
    # Lazy import keeps the prompt (and its vocab dependency) out of import-time
    # cost for callers that only use normalize_classification.
    from prompts.tool_classify import CLASSIFY_SYSTEM_PROMPT, build_classify_user_message

    try:
        resp = call_json(CLASSIFY_SYSTEM_PROMPT, build_classify_user_message(batch))
        entries = resp.get("classifications", []) if isinstance(resp, dict) else []
    except Exception as exc:  # noqa: BLE001 — partial-failure tolerance by design
        logger.warning("classify batch %s failed (%s); %d mention(s) left unclassified",
                       label or "?", exc, len(batch))
        return [_unclassified(m, FLAG_LLM_ERROR) for m in batch]

    by_name = {norm_name(e.get("raw_name", "")): e for e in entries if e.get("raw_name")}
    out: list[dict] = []
    for m in batch:
        entry = by_name.get(norm_name(m.get("raw_name", "")))
        out.append(_unclassified(m, FLAG_MISSING) if entry is None else normalize_classification(entry, m))
    return out


def _unclassified(mention: dict, flag: str) -> dict:
    """A placeholder record for a mention the LLM did not classify (kept, flagged)."""
    return {
        "raw_name": mention.get("raw_name", ""),
        "disposition": None,
        "kind": None,
        "supercategory": None,
        "attributes": vocab.default_attributes(),
        "confidence": "low",
        "notes": "",
        "flags": [flag],
    }


# ---------------------------------------------------------------------------
# Live LLM seam — Bedrock Sonnet primary, OpenAI gpt-5.x fallback.
# Not exercised by unit tests (spends tokens); covered by the live seed smoke.
# ---------------------------------------------------------------------------

def make_classifier_call_json(*, max_tokens: int = 8192) -> CallJson:
    """Build the production ``call_json`` seam.

    Tries Bedrock Sonnet; on a content-filter/empty/transient failure, falls back
    to OpenAI gpt-5.x with a json_object response format. Either path returns a
    parsed dict. Raises only if BOTH providers fail (caught per-batch upstream).
    """
    from utils.bedrock_client import BedrockClient, MODEL_IDS_BY_STAGE, SONNET_MODEL, BedrockEmptyContentError

    bedrock = BedrockClient()
    model = MODEL_IDS_BY_STAGE.get("tool_classify", SONNET_MODEL)

    def _call(system: str, user: str) -> dict:
        try:
            return bedrock.call_json(
                model=model,
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=max_tokens,
            )
        except (BedrockEmptyContentError, json.JSONDecodeError) as exc:
            logger.warning("Bedrock classify failed (%s); falling back to OpenAI gpt-5.x", exc)
            return _openai_fallback(system, user, max_tokens)

    return _call


def _openai_fallback(system: str, user: str, max_tokens: int) -> dict:
    from utils.openai_client import GPT5_MODEL, call_with_retry, get_default_client

    completion = call_with_retry(
        get_default_client(),
        model=GPT5_MODEL,
        system_prompt=system,
        user_prompt=user,
        response_format={"type": "json_object"},
        max_completion_tokens=max_tokens,
    )
    content = completion.choices[0].message.content
    return json.loads(content)
