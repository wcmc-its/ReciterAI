"""One AREAINPUT item -> one AREASIGNAL row: synopsis, area scores, cost.

What the 2026-10-14 pilot validated, and this module reproduces:

- Synopsis only when there is text (``mode == "full"`` and a non-empty
  ``text``). A title-only item skips the synopsis and the title goes in the
  synopsis slot: a title is already synopsis-sized, and synopsizing it would
  invent content.
- Grants use the POC ``synopsis-grant`` prompt; trials use the pilot's trial
  prompt (``prompts``). Both go through the production synopsis generator
  (length retries, content-filter fallback).
- Area scores come from the production two-pass scorer
  (``score_publications.score_one_publication``) with a no-op DynamoDB client,
  so it writes no ``TOPIC#`` or ``PROCESSING#`` rows. The scorer reads only
  ``synopsis`` and ``abstract``. For a grant the abstract slot is the input
  ``text`` (the abstract, or the short funder text of a title-only grant); for a
  trial it is the brief summary plus conditions and interventions.
- No impact score.

Cost: ``score_one_publication`` calls Bedrock through ``BedrockClient.call``,
which returns no token usage, and the synopsis path's ``call_with_usage`` does
not feed the process-wide accumulator. ``MeteredBedrockClient`` captures the
usage block of every Converse response at ``_call_with_retry``, which both paths
share, so each item's Bedrock spend is recorded from the actual responses. The
OpenAI content-filter fallbacks bypass Bedrock: the synopsis fallback reports its
own tokens on ``SynopsisResult``; the dense-scoring fallback reports none, so it
is counted and charged at a fixed conservative estimate.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

import score_publications as sp
from pipeline_area_signals import prompts
from pipeline_enrichment.synopsis import generate_synopsis
from pipeline_grants.scoring import _NoOpDynamoClient
from utils.bedrock_client import BedrockClient
from utils.iso_clock import now_iso
from utils.openai_client import GPT5_MODEL
from utils.taxonomy import content_hash

KINDS = ("grant", "trial")
MODES = ("full", "title")

INPUT_PK = "AREAINPUT"
SIGNAL_PK_PREFIX = "AREASIGNAL#"
SIGNAL_SK = "META"

# Every dense score at or above this is persisted, for every topic (excluded
# topics included; SPS's topic FK decides what is shown). Matches the scorer's
# own screening floor and the pilot's qualifying-area cut.
TOPIC_SCORE_FLOOR = 0.3

# Charged per dense-scoring call that fell back to OpenAI, which reports no
# usage through the scorer. Roughly 2x a measured Sonnet dense call
# (docs/cost-model.md, ~$0.009), so the guard errs high.
DENSE_FALLBACK_ESTIMATE_USD = Decimal("0.02")

# synopsis_model value for an item scored on its title (no synopsis call).
TITLE_ONLY_SYNOPSIS_MODEL = "title"


class InvalidInput(ValueError):
    """An AREAINPUT item that can never be scored as written (permanent)."""


class ScoringFailed(RuntimeError):
    """A scoring attempt that failed and may succeed on a retry (transient)."""


def signal_pk(kind: str, text_key: str) -> str:
    return f"{SIGNAL_PK_PREFIX}{kind}:{text_key}"


def current_taxonomy_version(taxonomy: dict) -> str:
    """Version stamp that moves whenever scoring-relevant taxonomy content moves.

    ``taxonomy["taxonomy_version"]`` alone is a static family label
    (``"taxonomy_v2"``), so it would never trigger a re-score. The content hash
    (``utils.taxonomy.content_hash``) changes on any topic edit.
    """
    return f"{taxonomy.get('taxonomy_version', 'unknown')}@{content_hash(taxonomy)[:12]}"


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(v).strip() for v in value if v is not None and str(v).strip()]


@dataclass(frozen=True)
class AreaInput:
    kind: str
    text_key: str
    mode: str
    title: str
    text: str = ""
    funder: str = ""
    mechanism: str = ""
    phase: str = ""
    study_type: str = ""
    conditions: tuple = ()
    interventions: tuple = ()
    start_year: Optional[int] = None

    @classmethod
    def from_item(cls, item: dict) -> "AreaInput":
        """Validate a DynamoDB resource item. Raises InvalidInput."""
        kind = _str(item.get("kind"))
        text_key = _str(item.get("text_key"))
        mode = _str(item.get("mode"))
        title = _str(item.get("title"))
        if kind not in KINDS:
            raise InvalidInput(f"unknown kind {kind!r}")
        if not text_key:
            raise InvalidInput("missing text_key")
        if mode not in MODES:
            raise InvalidInput(f"unknown mode {mode!r}")
        if not title:
            raise InvalidInput("missing title")
        raw_year = item.get("start_year")
        try:
            start_year = int(raw_year) if raw_year not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise InvalidInput(f"bad start_year {raw_year!r}") from exc
        return cls(
            kind=kind, text_key=text_key, mode=mode, title=title,
            text=_str(item.get("text")), funder=_str(item.get("funder")),
            mechanism=_str(item.get("mechanism")), phase=_str(item.get("phase")),
            study_type=_str(item.get("study_type")),
            conditions=tuple(_str_list(item.get("conditions"))),
            interventions=tuple(_str_list(item.get("interventions"))),
            start_year=start_year,
        )

    @property
    def wants_synopsis(self) -> bool:
        return self.mode == "full" and bool(self.text)

    def synopsis_context(self) -> str:
        """The synopsis prompt's context line (the generator's ``journal`` slot)."""
        if self.kind == "grant":
            return " ".join(x for x in (self.funder, self.mechanism) if x)
        return f"Study type: {self.study_type or 'N/A'}; Phase: {self.phase or 'N/A'}"

    def scorer_abstract(self) -> str:
        """What the scorer reads in its abstract slot."""
        if self.kind == "grant":
            return self.text
        parts = [self.text]
        if self.conditions:
            parts.append(f"Conditions: {'; '.join(self.conditions)}")
        if self.interventions:
            parts.append(f"Interventions: {'; '.join(self.interventions)}")
        return "\n".join(p for p in parts if p)

    def input_hash(self) -> str:
        """Hash of everything that reaches a model, plus the prompt version."""
        payload = {
            "kind": self.kind, "mode": self.mode, "title": self.title, "text": self.text,
            "context": self.synopsis_context(), "abstract": self.scorer_abstract(),
            "start_year": self.start_year, "prompt_version": prompts.PROMPT_VERSION,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MeteredBedrockClient(BedrockClient):
    """BedrockClient that records every Converse response's token usage.

    ``usage`` collects ``(model, input_tokens, output_tokens)``; callers reset it
    before an item and read it after. One instance per worker thread.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.usage: list[tuple[str, int, int]] = []

    def _call_with_retry(self, model, *args, **kwargs):
        response = super()._call_with_retry(model, *args, **kwargs)
        usage = (response or {}).get("usage") or {}
        self.usage.append(
            (model, int(usage.get("inputTokens") or 0), int(usage.get("outputTokens") or 0))
        )
        return response


@dataclass
class SignalResult:
    item: dict
    usage: list = field(default_factory=list)  # [(model, input_tokens, output_tokens)]
    estimated_usd: Decimal = Decimal("0")       # charges with no reported usage
    synopsis_error: Optional[str] = None


def _synopsis(inp: AreaInput, client) -> tuple[str, str, Optional[str], list]:
    """(synopsis, synopsis_model, synopsis_error, extra_usage)."""
    if not inp.wants_synopsis:
        return inp.title, TITLE_ONLY_SYNOPSIS_MODEL, None, []
    system, builder = (
        (prompts.GRANT_SYNOPSIS_SYSTEM, prompts.build_grant_user_content)
        if inp.kind == "grant"
        else (prompts.TRIAL_SYNOPSIS_SYSTEM, prompts.build_trial_user_content)
    )
    result = generate_synopsis(
        pmid=f"{inp.kind}:{inp.text_key}", title=inp.title, journal=inp.synopsis_context(),
        year=inp.start_year, abstract=inp.text, client=client,
        system_prompt=system, user_content_builder=builder,
    )
    # The OpenAI fallback never reaches Bedrock, so the meter misses it; its
    # tokens are on the result. (They can include earlier Bedrock attempts,
    # which the meter also saw: a small, conservative double count.)
    extra = ([(GPT5_MODEL, result.input_tokens, result.output_tokens)]
             if result.model == GPT5_MODEL else [])
    if not result.synopsis:
        raise ScoringFailed(f"synopsis: {result.error or 'empty'}")
    # A length violation keeps the model's text (never truncated, #50), as the
    # enrichment job does; the error rides on the row.
    return result.synopsis, result.model, result.error, extra


def score_input(
    inp: AreaInput,
    *,
    bedrock: MeteredBedrockClient,
    taxonomy: dict,
    int_to_id: dict,
    id_to_int: dict,
    taxonomy_version: str,
) -> SignalResult:
    """Synopsis + area scores for one input. Raises ScoringFailed (transient).

    Resets ``bedrock.usage`` first; on a raise, what it holds is the Bedrock
    spend of the failed attempt.
    """
    bedrock.usage = []
    synopsis, synopsis_model, synopsis_error, extra = _synopsis(inp, bedrock)
    scored = sp.score_one_publication(
        {
            "pmid": f"{inp.kind}:{inp.text_key}",
            "title": inp.title,
            "synopsis": synopsis,
            "abstract": inp.scorer_abstract(),
            "year": str(inp.start_year or ""),
        },
        bedrock, taxonomy, _NoOpDynamoClient(), "reciterai", int_to_id, id_to_int,
    )
    usage = list(bedrock.usage) + extra
    estimated = DENSE_FALLBACK_ESTIMATE_USD if scored.fallback_model else Decimal("0")
    if scored.status != "complete":
        # The spend so far stays on bedrock.usage for the caller to record.
        raise ScoringFailed(f"area scores: {scored.error or scored.status}")
    topic_scores = sorted(
        (
            {"topic_id": tid, "score": Decimal(str(round(float(v["score"]), 4)))}
            for tid, v in scored.dense_scores.items()
            if float(v["score"]) >= TOPIC_SCORE_FLOOR
        ),
        key=lambda t: (-t["score"], t["topic_id"]),
    )
    item = {
        "PK": signal_pk(inp.kind, inp.text_key),
        "SK": SIGNAL_SK,
        "kind": inp.kind,
        "text_key": inp.text_key,
        "mode": inp.mode,
        "synopsis": synopsis,
        "synopsis_model": synopsis_model,
        "topic_scores": topic_scores,
        "taxonomy_version": taxonomy_version,
        "input_hash": inp.input_hash(),
        "scored_at": now_iso(),
        "scorer_fallback_model": scored.fallback_model,
    }
    if synopsis_error:
        item["synopsis_error"] = synopsis_error[:500]
    return SignalResult(item=item, usage=usage, estimated_usd=estimated,
                        synopsis_error=synopsis_error)
