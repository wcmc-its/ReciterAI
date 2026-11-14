"""#1166-B — multi-sentence entity-context augmentation (Phase 1: abstracts).

The entity "how this was used" sentences are inherited 1:1 from the per-(tool, pmid)
`tool_context` snippet — exactly ONE sentence per (entity, pmid). This CLI runs a
SECOND, multi-sentence Haiku pass over the entity-bearing publications (the pmids
already present in the live `entity_context.json`) and emits, per pmid, EVERY
complete verbatim abstract sentence that names a distinctive tool/method. The output
`augment.json` ({pmid: [sentence, ...]}) is fed to `cli/rebuild_entity_context.py
--augment`, whose `build_entity_layer(context_augment=...)` merges these with the live
top-1 snippet per existing (entity, pmid), gated by the entity's own name span.

Why a standalone pass (not the full corpus run):
  - It NEVER rebuilds tools.json / tool_context.json — those stay byte-frozen, so the
    overview/biosketch grounding + SPS `scholar_tool.sample_context` are untouched and
    the sidecar publisher's freeze-assert holds. (`canonical_tool_id` is a counter, not
    a content hash — a full re-run would not reproduce the live ids; the rebuild seeds
    entity↔sentence assignment from the live tools.json instead, in build_entity_layer.)
  - It is DEPTH only (more sentences for existing pairs), not coverage — closing the
    abstract-gap with full text is Phase 2 (`pipeline_cores.fulltext`).

Spend is bounded by the known pmid count × ~$0.006 (preflight refuse-if-over, then a
runtime CostCeiling hard-stop). Resumable via a JSONL checkpoint. Run a --limit probe
first (a few papers, cents) before the full ~3.7k-pmid sweep.

  # cheap probe:
  python -m cli.extract_entity_augment --pmids-from live/entity_context.json --limit 5 \
      --out out/tools/a2/entity_augment.probe.json
  # full Phase 1:
  python -m cli.extract_entity_augment --pmids-from live/entity_context.json \
      --out out/tools/a2/entity_augment.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pipeline_tools import cost_guard
from pipeline_tools.context_quality import accept_snippet
from pipeline_tools.cost_guard import CostCeiling, CostCeilingExceeded

logger = logging.getLogger(__name__)

# How many sentences-per-tool to ask the model for (the verbatim-in-abstract gate +
# build_entity_layer's MAX_USAGE_PER_PAIR cap the kept count regardless).
DEFAULT_MAX_SENTENCES = 4


def build_multi_system_prompt(max_sentences: int) -> str:
    """Multi-sentence variant of prompts.tool_extract — same WHAT-COUNTS/SKIP rules,
    but `contexts` is a LIST of every abstract sentence that names the tool."""
    return f"""You are an expert biomedical research-methods analyst. Given ONE publication's \
title and abstract, extract the DISTINCTIVE research TOOLS and METHODS the study used or produced, \
and for EACH one return EVERY complete sentence in the abstract that names or uses it (not just the first).

WHAT COUNTS: wet-lab / computational / statistical methods & study designs (single-cell RNA-seq, \
patch-clamp, Mendelian randomization, a named CNN); named instruments / platforms; reagents / probes / \
vectors / CRISPR systems used AS tools; organisms, cell lines, transgenic / knockout models, strains; \
datasets, cohorts, registries, software / models the work ran on or against.
WHAT TO SKIP: commodity bench gear (pipettes, generic centrifuge, vortex, −80 freezer); bare elementary \
statistics (t-test, ANOVA, chi-square, p-value); generic office software; and the disease / organ / \
phenomenon STUDIED (that is the subject, not a tool).

For EACH extracted tool / method emit:
  - raw_name: the name VERBATIM as the abstract phrases it (keep acronym + expansion together if both appear).
  - contexts: a LIST of up to {max_sentences} COMPLETE sentences from the abstract that name or use THIS \
tool — each copied VERBATIM and IN FULL (first word to terminal punctuation), each a SINGLE complete \
sentence that reads correctly on its OWN (do NOT begin mid-clause, do NOT merge two sentences). Include \
every DISTINCT sentence that names the tool, in order of appearance. Never invent, paraphrase, summarise, \
or truncate — quote the abstract exactly. If only one sentence names it, return a one-element list.

Respond with VALID JSON ONLY, no markdown:
{{"mentions": [{{"raw_name": "<verbatim>", "contexts": ["<complete sentence>", "..."]}}]}}
If the abstract names no distinctive tool or method, return {{"mentions": []}}."""


def build_user_message(pub_row: dict) -> str:
    payload = {
        "pmid": str(pub_row.get("pmid", "")),
        "title": pub_row.get("articleTitle") or pub_row.get("title") or "",
        "journal": pub_row.get("journalTitleVerbose") or pub_row.get("journal"),
        "year": pub_row.get("articleYear") or pub_row.get("year"),
        "abstract": pub_row.get("abstractVarchar") or pub_row.get("abstract") or "",
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(", ", ": "))
    return (
        "Extract the distinctive research tools and methods from this publication, and for each "
        "return EVERY complete verbatim sentence in the abstract that names it:\n" + body
    )


# ---------------------------------------------------------------------------
# Pure logic (unit-tested) — collect the validated, deduped sentences for one pmid.
# ---------------------------------------------------------------------------


def collect_sentences(mentions: list[dict], abstract: str) -> list[str]:
    """Flatten {raw_name, contexts:[...]} mentions into the validated, deduped sentence
    list for one pmid.

    Each candidate sentence is kept only if ``accept_snippet`` passes (verbatim in the
    abstract, names the tool, a single in-length sentence) — the SAME gate the live
    single-sentence path uses, so an augmentation sentence can never be a fragment or a
    hallucination. De-duped case/space-insensitively, first occurrence wins (stable order).
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in mentions or []:
        raw_name = (m.get("raw_name") or "").strip()
        if not raw_name:
            continue
        contexts = m.get("contexts")
        if isinstance(contexts, str):  # tolerate a model that emits a single string
            contexts = [contexts]
        for ctx in contexts or []:
            s = (ctx or "").strip()
            if not s or not accept_snippet(s, abstract, raw_name):
                continue
            key = " ".join(s.lower().split())
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Scope + checkpoint helpers
# ---------------------------------------------------------------------------


def scoped_pmids(entity_context_path: str) -> list[str]:
    """The DISTINCT pmids present in the live entity_context.json (the depth universe)."""
    raw = json.load(open(entity_context_path))
    ctx = raw.get("entity_context", raw) if isinstance(raw, dict) else raw
    pmids: set[str] = set()
    for by_pmid in (ctx or {}).values():
        pmids.update(str(p) for p in (by_pmid or {}).keys())
    return sorted(pmids)


def _load_checkpoint(path: Path) -> dict[str, list[str]]:
    done: dict[str, list[str]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            done[str(rec["pmid"])] = rec.get("sentences", [])
    return done


def main(argv: list[str] | None = None, *, call_llm=None) -> int:
    ap = argparse.ArgumentParser(description="#1166-B multi-sentence entity-context augmentation (abstracts).")
    ap.add_argument("--pmids-from", help="live entity_context.json — derive the entity-bearing pmid scope")
    ap.add_argument("--pmids-file", help="newline-delimited pmids (overrides --pmids-from)")
    ap.add_argument("--out", required=True, help="output augment.json ({pmid: [sentence,...]})")
    ap.add_argument("--checkpoint", default="out/tools/a2_entity_augment_checkpoint.jsonl")
    ap.add_argument("--limit", type=int, default=None, help="cap to N pmids (probe before full fan-out)")
    ap.add_argument("--max-sentences", type=int, default=DEFAULT_MAX_SENTENCES)
    ap.add_argument("--max-tokens", type=int, default=3072, help="LLM call max_tokens (multi-sentence is longer)")
    ap.add_argument("--per-pmid-usd", type=float, default=float(cost_guard.DEFAULT_PER_PMID_USD))
    ap.add_argument("--threshold-usd", type=float, default=float(cost_guard.DEFAULT_THRESHOLD_USD))
    ap.add_argument("--hard-cap-usd", type=float, default=None)
    ap.add_argument("--full", action="store_true", help="bypass the preflight guard")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from decimal import Decimal

    # 1. Scope.
    if args.pmids_file:
        pmids = [p.strip() for p in Path(args.pmids_file).read_text().splitlines() if p.strip()]
    elif args.pmids_from:
        pmids = scoped_pmids(args.pmids_from)
    else:
        ap.error("need --pmids-from <entity_context.json> or --pmids-file")
    if args.limit:
        pmids = pmids[: args.limit]
    logger.info("scope: %d entity-bearing pmid(s)", len(pmids))

    ckpt_path = Path(args.checkpoint)
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    aug = _load_checkpoint(ckpt_path)
    remaining = [p for p in pmids if p not in aug]
    logger.info("already done=%d, remaining=%d", len(pmids) - len(remaining), len(remaining))

    # 2. Preflight + runtime ceiling (bounded by the known pmid count).
    per_pmid = Decimal(str(args.per_pmid_usd))
    try:
        est = cost_guard.check_extraction_guard(
            len(remaining), threshold_usd=Decimal(str(args.threshold_usd)), per_pmid_usd=per_pmid,
        )
    except cost_guard.ExtractionCostGuardTripped as e:
        if not args.full:
            logger.error("preflight refused: %s (use --full to override)", e)
            return 2
        est = e.estimate
    logger.info("preflight estimate: ~$%s for %d pmid(s)", est.estimated_usd, len(remaining))
    full_est = cost_guard.ExtractionCostEstimate(
        n_pmids=len(pmids), per_pmid_usd=per_pmid,
        estimated_usd=cost_guard.estimate_extraction_cost(len(pmids), per_pmid),
        threshold_usd=Decimal(str(args.threshold_usd)),
    )
    cap_usd = Decimal(str(args.hard_cap_usd)) if args.hard_cap_usd is not None else cost_guard.derive_hard_cap(full_est)
    ceiling = CostCeiling(cap_usd=cap_usd, max_calls=len(pmids) * cost_guard.MAX_CALLS_PER_PMID)

    if not remaining:
        logger.info("nothing to do; writing augment from checkpoint")
        return _write_out(aug, args.out)

    # 3. Fetch abstracts (RDS, batched) and build the live LLM seam.
    from utils.db import get_engine
    from utils.sql_queries import fetch_publications_for_enrichment

    engine = get_engine()
    rows: dict[str, dict] = {}
    for i in range(0, len(remaining), 500):
        for r in fetch_publications_for_enrichment(engine, remaining[i : i + 500]):
            rows[str(r.get("pmid"))] = r
    logger.info("fetched %d/%d abstracts from RDS", len(rows), len(remaining))

    if call_llm is None:
        from pipeline_tools.extract import make_extractor_call_llm
        call_llm = make_extractor_call_llm(max_tokens=args.max_tokens)
    from pipeline_enrichment.llm_call import parse_json_lenient

    system = build_multi_system_prompt(args.max_sentences)
    n_done = n_fail = n_sent = 0
    with ckpt_path.open("a", encoding="utf-8") as ck:
        for pmid in remaining:
            row = rows.get(pmid)
            abstract = (row or {}).get("abstractVarchar") or (row or {}).get("abstract") or ""
            if not abstract:
                continue  # no abstract → nothing to extract (left un-checkpointed; harmless)
            try:
                res = call_llm(system, build_user_message(row))
                mentions = parse_json_lenient(res.text).get("mentions", [])
                sentences = collect_sentences(mentions, abstract)
            except Exception as exc:  # noqa: BLE001 — per-pmid tolerance, mirrors run_extraction
                logger.warning("pmid=%s failed: %s", pmid, exc)
                n_fail += 1
                continue
            aug[pmid] = sentences
            n_done += 1
            n_sent += len(sentences)
            ck.write(json.dumps({"pmid": pmid, "sentences": sentences}, ensure_ascii=False) + "\n")
            ck.flush()
            try:
                ceiling.record(model=res.model, input_tokens=res.input_tokens, output_tokens=res.output_tokens)
            except CostCeilingExceeded as exc:
                logger.error("cost ceiling tripped after pmid=%s — halting (%s)", pmid, exc)
                break

    logger.info(
        "done=%d failed=%d sentences=%d (avg %.2f/pmid) observed≈$%s",
        n_done, n_fail, n_sent, (n_sent / n_done if n_done else 0), ceiling.observed_usd,
    )
    return _write_out(aug, args.out)


def _write_out(aug: dict[str, list[str]], out_path: str) -> int:
    # Keep only pmids that actually produced sentences — an empty list adds nothing.
    payload = {pmid: sents for pmid, sents in sorted(aug.items()) if sents}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=0), encoding="utf-8")
    total = sum(len(v) for v in payload.values())
    logger.info("wrote %s: %d pmids, %d sentences", out_path, len(payload), total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
