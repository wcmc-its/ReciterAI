"""Per-PMID extraction harness for A2 corpus mining (docs/tool-classifier-spec.md).

This is the front of the A2 pipeline: it sweeps the WCM lead/senior-authored
Academic Articles ≥2020, runs one Bedrock Haiku extraction call per paper, and
emits raw tool/method mentions for the existing classify → §8 tool registry →
§7 family registry → §5 salience stages to consume. It is the corpus-mode
sibling of the A1 seed loader: the seed read 230 names from a curated JSON; A2
reads them out of live abstracts.

What it guarantees
------------------
- **Bounded spend.** Every call's measured cost flows through a ``CostCeiling``
  (``pipeline_tools.cost_guard``) that hard-stops the run before it overspends.
- **Resumable.** Each completed PMID is appended to an ``ExtractionCheckpoint``
  before the ceiling can fire, so a crash / ceiling-halt / kill never re-extracts
  a done paper; a resume skips them and seeds the ceiling with prior spend.
- **Partial-failure tolerant.** A PMID whose call or JSON parse fails is logged
  and left UN-checkpointed (so a resume retries it) — it never aborts the sweep,
  mirroring the classifier's per-batch tolerance.

The LLM call is behind an injected ``call_llm(system, user) -> LLMCallResult``
seam (text + measured token usage + the model actually used), so the harness
unit-tests with a stub and never touches AWS/OpenAI. The live seam
(``make_extractor_call_llm``) is ``pipeline_enrichment.llm_call.call_with_fallback``
pinned to Haiku — Bedrock Haiku primary, OpenAI gpt-5.x content-filter fallback,
the cross-model fallback of record (the operator has no Anthropic Console key).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from pipeline_tools import vocab
from pipeline_tools.checkpoint import ExtractionCheckpoint
from pipeline_tools.context_quality import MAX_SENTENCE_CHARS, accept_snippet
from pipeline_tools.cost_guard import CostCeiling, CostCeilingExceeded
from prompts.tool_extract import build_extract_user_message, EXTRACT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# call_llm seam: (system_prompt, user_prompt) -> object with
# .text / .input_tokens / .output_tokens / .model (an LLMCallResult).
CallLLM = Callable[[str, str], "object"]

_VALID_HINTS = frozenset(vocab.LEGACY_CATEGORY_PRIOR.keys())
# Author-identity fields copied verbatim from the pub_row onto each mention, for
# the later per-faculty salience spread (§5) + rollup (§7.1/Step F). The harness
# stays agnostic to the exact join shape — it carries through whatever is present.
_AUTHOR_FIELDS = ("cwid", "faculty", "author_role", "authors")


@dataclass
class ExtractionOutcome:
    """One PMID's extraction result: tagged mentions + the call's measured usage."""

    pmid: str
    mentions: list[dict] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    ok: bool = False
    error: str | None = None


@dataclass
class ExtractionResult:
    n_input: int = 0          # rows considered this run
    n_skipped: int = 0        # already in the checkpoint (resumed)
    n_done: int = 0           # newly extracted this run
    n_failed: int = 0         # call/parse failures (NOT checkpointed; retried on resume)
    mentions: list[dict] = field(default_factory=list)   # FULL corpus from the checkpoint
    new_mentions: list[dict] = field(default_factory=list)  # just this run's
    failed: list[dict] = field(default_factory=list)     # [{pmid, error}]
    halted: CostCeilingExceeded | None = None            # set if the ceiling fired
    telemetry: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Per-PMID extraction (pure — no checkpoint/ceiling side effects)
# ---------------------------------------------------------------------------


def _clean_context(raw_context: str | None, raw_name: str, pub_row: dict) -> str | None:
    """Accept the emitted snippet only if it's a verbatim, tool-naming, single sentence.

    The context is surfaced standalone by SPS AND grounds its bio generator with no
    abstract fallback (#238), so a fragment / off-topic / run-on snippet is harmful.
    We never clamp (that just relocates the fragment to the tail) — a snippet that
    fails the shared guard is dropped to None (the mention keeps its name + hint;
    SPS simply has no usage snippet for that (tool, pmid), and picks another). When
    no abstract is available to verify against (non-prod/edge), keep the snippet if
    it is within the run-on ceiling.
    """
    ctx = (raw_context or "").strip()
    if not ctx:
        return None
    abstract = pub_row.get("abstractVarchar") or pub_row.get("abstract") or ""
    if abstract:
        return ctx if accept_snippet(ctx, abstract, raw_name) else None
    return ctx if len(ctx) <= MAX_SENTENCE_CHARS else None


def _tag_author_fields(mention: dict, pub_row: dict) -> None:
    for fld in _AUTHOR_FIELDS:
        if fld in pub_row and pub_row[fld] is not None:
            mention[fld] = pub_row[fld]


def normalize_mention(raw: dict, pub_row: dict) -> dict | None:
    """Coerce one raw LLM mention into a tagged mention record, or None to drop.

    Drops a mention only when ``raw_name`` is blank (nothing to canonicalize).
    An out-of-vocab ``tool_category_hint`` is nulled (it is a weak prior; the
    classifier decides from name+context regardless), never a reason to drop.
    """
    name = (raw.get("raw_name") or "").strip()
    if not name:
        return None
    hint = raw.get("tool_category_hint")
    hint = hint if hint in _VALID_HINTS else None
    confidence = "low" if str(raw.get("confidence", "")).lower() == "low" else "high"
    mention = {
        "raw_name": name,
        "tool_category": hint,   # keyed 'tool_category' to match the classifier's §4 input
        "context": _clean_context(raw.get("context"), name, pub_row),
        "confidence": confidence,
        "pmid": str(pub_row.get("pmid", "")),
        # publication | grant — grant-sourced mentions feed extraction/salience/
        # family discovery but are kept OUT of the publication pub-filter UX
        # (docs/tools-producer-model.md §grants); default publication.
        "source_kind": (pub_row.get("source_kind") or "publication"),
    }
    _tag_author_fields(mention, pub_row)
    return mention


def extract_mentions(pub_row: dict, *, call_llm: CallLLM) -> ExtractionOutcome:
    """Extract raw tool/method mentions from one publication.

    Returns an ``ExtractionOutcome`` carrying the tagged mentions and the call's
    measured token usage. Usage is reported even on a JSON-parse failure (the
    call was paid for); only a call that never completed reports zero usage.
    Never raises — a failure is captured on the outcome (``ok=False``).
    """
    from pipeline_enrichment.llm_call import parse_json_lenient

    pmid = str(pub_row.get("pmid", ""))
    try:
        result = call_llm(EXTRACT_SYSTEM_PROMPT, build_extract_user_message(pub_row))
    except Exception as exc:  # noqa: BLE001 — a hard call failure is per-PMID, not fatal
        logger.warning("extract pmid=%s: LLM call failed (%s)", pmid, exc)
        return ExtractionOutcome(pmid=pmid, ok=False, error=f"llm_call: {exc}")

    usage = ExtractionOutcome(
        pmid=pmid,
        model=getattr(result, "model", "") or "",
        input_tokens=int(getattr(result, "input_tokens", 0) or 0),
        output_tokens=int(getattr(result, "output_tokens", 0) or 0),
    )
    try:
        parsed = parse_json_lenient(getattr(result, "text", "") or "")
        raw_mentions = parsed.get("mentions", []) if isinstance(parsed, dict) else []
    except Exception as exc:  # noqa: BLE001 — malformed JSON is a per-PMID miss
        logger.warning("extract pmid=%s: response parse failed (%s)", pmid, exc)
        usage.ok = False
        usage.error = f"parse: {exc}"
        return usage

    mentions: list[dict] = []
    for raw in raw_mentions:
        if not isinstance(raw, dict):
            continue
        m = normalize_mention(raw, pub_row)
        if m is not None:
            mentions.append(m)
    usage.mentions = mentions
    usage.ok = True
    return usage


# ---------------------------------------------------------------------------
# Corpus sweep — checkpointed, ceiling-bounded, partial-failure tolerant
# ---------------------------------------------------------------------------


def run_extraction(
    pub_rows: list[dict],
    *,
    call_llm: CallLLM,
    checkpoint: ExtractionCheckpoint,
    ceiling: CostCeiling,
) -> ExtractionResult:
    """Sweep ``pub_rows``, extracting mentions per PMID under the cost ceiling.

    For each row not already in the checkpoint: extract → (on success) checkpoint
    it BEFORE the ceiling can fire, so a breaching-but-successful PMID is durable
    → record the call's measured cost into the ceiling → halt the sweep if the
    ceiling trips. Failed PMIDs are recorded in ``result.failed`` and left
    un-checkpointed so a resume retries only them.

    ``result.mentions`` is the FULL corpus of mentions (this run + any resumed),
    ready to feed the classify → registry stages; ``result.new_mentions`` is just
    this run's.
    """
    result = ExtractionResult(n_input=len(pub_rows))

    for row in pub_rows:
        pmid = str(row.get("pmid", ""))
        if not pmid:
            logger.warning("extract: row with no pmid skipped: %r", {k: row.get(k) for k in ("title",)})
            result.n_failed += 1
            result.failed.append({"pmid": "", "error": "missing pmid"})
            continue
        if checkpoint.is_done(pmid):
            result.n_skipped += 1
            continue

        outcome = extract_mentions(row, call_llm=call_llm)

        if outcome.ok:
            # Durable BEFORE the ceiling can raise: a successful PMID that happens
            # to cross the cap is still checkpointed and never re-extracted.
            checkpoint.record(
                pmid,
                outcome.mentions,
                model=outcome.model,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
            )
            result.n_done += 1
            result.new_mentions.extend(outcome.mentions)
        else:
            result.n_failed += 1
            result.failed.append({"pmid": pmid, "error": outcome.error})

        # Cost accounting + hard ceiling. Records on success AND parse-failure
        # (both paid for the call); a never-completed call reports 0 tokens and
        # contributes nothing.
        if outcome.input_tokens or outcome.output_tokens:
            try:
                ceiling.record(
                    model=outcome.model,
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                )
            except CostCeilingExceeded as exc:
                logger.error(
                    "extract: cost ceiling tripped after pmid=%s — halting sweep (%s)",
                    pmid, exc,
                )
                result.halted = exc
                break

    result.mentions = checkpoint.all_mentions()
    result.telemetry = _telemetry(result, ceiling)
    logger.info(
        "extraction sweep: %d input, %d skipped (resumed), %d extracted, %d failed; "
        "%d total mentions; observed $%s%s",
        result.n_input, result.n_skipped, result.n_done, result.n_failed,
        len(result.mentions), ceiling.observed_usd,
        " [HALTED on ceiling]" if result.halted else "",
    )
    return result


def _telemetry(result: ExtractionResult, ceiling: CostCeiling) -> dict:
    done = result.n_done
    new_n = len(result.new_mentions)
    # Rough method-axis read: the share of THIS run's mentions whose weak hint is
    # method-ish. A coarse signal (the classifier decides for real), surfaced so a
    # probe can sanity-check that A2 is actually pulling methods, not just resources.
    method_hints = {"computational_method", "assay_kit"}
    n_method_hint = sum(1 for m in result.new_mentions if m.get("tool_category") in method_hints)
    return {
        "n_input": result.n_input,
        "n_skipped_resumed": result.n_skipped,
        "n_extracted": done,
        "n_failed": result.n_failed,
        "new_mentions": new_n,
        "total_mentions": len(result.mentions),
        "mentions_per_paper": round(new_n / done, 2) if done else None,
        "method_hint_fraction": round(n_method_hint / new_n, 2) if new_n else None,
        "halted_on_ceiling": result.halted is not None,
        "cost": ceiling.summary(),
    }


# ---------------------------------------------------------------------------
# Live seam — Bedrock Haiku primary, OpenAI gpt-5.x content-filter fallback.
# Not exercised by unit tests (spends tokens); covered by the --limit probe.
# ---------------------------------------------------------------------------


def make_extractor_call_llm(*, max_tokens: int = 2048) -> CallLLM:
    """Build the production ``call_llm`` seam: Haiku extraction + OpenAI fallback.

    Reuses ``pipeline_enrichment.llm_call.call_with_fallback`` (the same seam the
    enrichment workers use) pinned to ``HAIKU_MODEL`` — the cheap per-PMID model
    for extraction — so the Bedrock→gpt-5.x content-filter fallback and the
    measured token usage come for free and stay consistent with the rest of the
    pipeline's cost attribution.
    """
    from pipeline_enrichment.llm_call import call_with_fallback
    from utils.bedrock_client import HAIKU_MODEL

    def _call(system: str, user: str):
        return call_with_fallback(
            system_prompt=system,
            user_prompt=user,
            model=HAIKU_MODEL,
            max_tokens=max_tokens,
        )

    return _call
