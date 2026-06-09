"""Operator CLI for the Phase 8 A2 corpus extraction (docs/tool-classifier-spec.md).

Sweeps the WCM full-time-faculty lead/senior-authored Academic Articles ≥2020,
runs one Bedrock Haiku extraction call per paper, and writes the raw tool/method
mentions that feed the classify → §8 tool registry → §7 family registry → §5
salience stages. This is the EXPENSIVE A2 fan-out the seed CLI
(``cli.build_tool_taxonomy``) defers to a cost guard — so it runs behind one:

  preflight estimate (refuse on anomaly; --full bypasses)  →  derive a hard cap
  →  resume from the checkpoint (skip done PMIDs, seed prior spend)  →  sweep
  under a runtime CostCeiling that hard-stops before overspending.

Corpus source (exactly one):
  --input PATH   a JSON corpus export (list of pub rows) — offline / probe-safe.
  --from-db      load live from RDS via pipeline_tools.corpus (VPN-gated);
                 --source pubs|grants|both selects publications, NIH RePORTER
                 grant abstracts (grant_reporter_project), or both. Grant-sourced
                 mentions feed extraction/salience/families but stay out of the
                 publication pub-filter (docs/tools-producer-model.md §grants).

Run a --limit probe (e.g. 100 PMIDs) and inspect telemetry (mentions/paper,
method-hint fraction, measured cost) BEFORE the full ≈8,146-paper fan-out —
the same cheap-probe-first discipline as the seed. Recalibrate
``--per-pmid-usd`` from the probe's measured cost before going wide.

Conventions per CLAUDE.md: lazy AWS/DB construction (no creds at import),
credentials from env only (never logged), no model-ID literals here (the live
seam imports its model id from utils.bedrock_client).

Usage:
    python -m cli.extract_tool_mentions --input corpus.json --limit 100
    python -m cli.extract_tool_mentions --from-db --source both --limit 100   # probe
    python -m cli.extract_tool_mentions --from-db --source both --full         # full
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline_tools import cost_guard  # noqa: E402
from pipeline_tools.checkpoint import ExtractionCheckpoint  # noqa: E402
from pipeline_tools.cost_guard import CostCeiling  # noqa: E402
from pipeline_tools.extract import run_extraction  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("extract_tool_mentions")

DEFAULT_CHECKPOINT = REPO_ROOT / "out" / "tools" / "a2_extraction_checkpoint.jsonl"
DEFAULT_OUT = REPO_ROOT / "out" / "tools" / "a2_mentions.json"


def load_corpus(args) -> list[dict]:
    """Load the corpus rows from --input JSON or --from-db (VPN)."""
    if args.input is not None:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("corpus", data.get("rows", []))
        rows = [r for r in rows if str(r.get("pmid", "")).strip()]
        logger.info("Loaded %d corpus row(s) from %s", len(rows), args.input)
        if args.limit:
            rows = rows[: args.limit]
            logger.info("Limited to first %d row(s)", args.limit)
        return rows
    # --from-db (lazy: no DB/engine at import).
    from pipeline_tools.corpus import fetch_extraction_corpus, fetch_grant_corpus
    from utils.db import get_engine

    engine = get_engine()
    rows: list[dict] = []
    if args.source in ("pubs", "both"):
        pubs = fetch_extraction_corpus(engine, limit=args.limit)
        logger.info("Loaded %d publication row(s) from RDS", len(pubs))
        rows.extend(pubs)
    if args.source in ("grants", "both"):
        grants = fetch_grant_corpus(engine, limit=args.limit)
        logger.info("Loaded %d grant row(s) from RDS (NIH RePORTER)", len(grants))
        rows.extend(grants)
    return rows


def _print_summary(result, ceiling: CostCeiling, est, cap_usd: Decimal, out_path: Path) -> None:
    t = result.telemetry
    print("\n=== A2 extraction summary ===")
    print(f"  preflight estimate (remaining) ${est.estimated_usd} (n={est.n_pmids}, ${est.per_pmid_usd}/PMID)")
    print(f"  hard cap                       ${cap_usd}")
    for key in ("n_input", "n_skipped_resumed", "n_extracted", "n_failed",
                "new_mentions", "total_mentions", "mentions_per_paper",
                "method_hint_fraction", "halted_on_ceiling"):
        print(f"  {key:24s} {t.get(key)}")
    print(f"  observed cost                  ${ceiling.observed_usd} (cap ${cap_usd}, {ceiling.calls} call(s))")
    if result.halted is not None:
        print(f"  HALTED: {result.halted}")
    print(f"  wrote                          {out_path}")
    if result.failed:
        sample = ", ".join(f"{f['pmid']}" for f in result.failed[:10])
        print(f"  failed PMIDs (retried on resume): {sample}{' …' if len(result.failed) > 10 else ''}")


def main(argv: list[str] | None = None, *, call_llm=None) -> int:
    """Run the A2 extraction sweep.

    ``call_llm`` is injectable so the wiring (preflight → ceiling → checkpoint →
    output) can be smoke-tested offline with a stub; left None, the live
    Haiku→OpenAI seam is built just before the sweep (and only then are AWS/OpenAI
    credentials touched).
    """
    parser = argparse.ArgumentParser(description="Phase 8 A2 corpus tool/method extraction.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=Path, help="JSON corpus export (list of pub rows) — offline/probe")
    src.add_argument("--from-db", action="store_true", help="load corpus live from RDS (VPN-gated)")
    parser.add_argument("--source", choices=("pubs", "grants", "both"), default="pubs",
                        help="--from-db signal scope: publications, NIH RePORTER grants, or both (default: pubs)")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT,
                        help="resumable checkpoint JSONL (default: out/tools/a2_extraction_checkpoint.jsonl)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="mentions output JSON (default: out/tools/a2_mentions.json)")
    parser.add_argument("--limit", type=int, default=None, help="cap to N PMIDs (probe before the full fan-out)")
    parser.add_argument("--threshold-usd", type=Decimal, default=cost_guard.DEFAULT_THRESHOLD_USD,
                        help="preflight refuse-and-stop ceiling")
    parser.add_argument("--per-pmid-usd", type=Decimal, default=cost_guard.DEFAULT_PER_PMID_USD,
                        help="per-PMID cost assumption (recalibrate from the probe's measured cost)")
    parser.add_argument("--hard-cap-usd", type=Decimal, default=None,
                        help="override the derived runtime hard cap on measured spend")
    parser.add_argument("--max-calls", type=int, default=None,
                        help="override the derived runtime cap on total LLM calls")
    parser.add_argument("--max-tokens", type=int, default=2048, help="extraction call max_tokens")
    parser.add_argument("--full", action="store_true", help="bypass the preflight guard (intentional full run)")
    args = parser.parse_args(argv)

    corpus = load_corpus(args)
    if not corpus:
        logger.error("No corpus rows loaded — nothing to extract.")
        return 1

    # Resume: skip already-done PMIDs; the preflight guards THIS run's NEW spend.
    checkpoint = ExtractionCheckpoint.load(args.checkpoint)
    remaining = [r for r in corpus if not checkpoint.is_done(str(r.get("pmid", "")))]
    n_total, n_remaining = len(corpus), len(remaining)
    logger.info("corpus=%d, already done=%d, remaining=%d", n_total, len(checkpoint), n_remaining)

    # 1. Preflight on the remaining work (unless --full).
    try:
        est = cost_guard.check_extraction_guard(
            n_remaining, threshold_usd=args.threshold_usd, per_pmid_usd=args.per_pmid_usd,
        )
    except cost_guard.ExtractionCostGuardTripped as e:
        if not args.full:
            logger.error("Preflight refused the run: %s", e)
            return 2
        logger.warning("Preflight would refuse (%s) — proceeding under --full", e)
        est = e.estimate

    # 2. Runtime hard cap bounds CUMULATIVE spend (this run + any prior resume).
    full_est = cost_guard.ExtractionCostEstimate(
        n_pmids=n_total, per_pmid_usd=args.per_pmid_usd,
        estimated_usd=cost_guard.estimate_extraction_cost(n_total, args.per_pmid_usd),
        threshold_usd=args.threshold_usd,
    )
    cap_usd = args.hard_cap_usd if args.hard_cap_usd is not None else cost_guard.derive_hard_cap(full_est)
    max_calls = args.max_calls if args.max_calls is not None else n_total * cost_guard.MAX_CALLS_PER_PMID
    prior_usd, prior_calls = checkpoint.prior_cost()
    ceiling = CostCeiling(
        cap_usd=cap_usd, max_calls=max_calls, prior_usd=prior_usd, prior_calls=prior_calls,
    )

    # 3. Build the live seam only now (no AWS/OpenAI creds until here).
    if call_llm is None:
        from pipeline_tools.extract import make_extractor_call_llm
        call_llm = make_extractor_call_llm(max_tokens=args.max_tokens)

    # 4. Sweep.
    result = run_extraction(corpus, call_llm=call_llm, checkpoint=checkpoint, ceiling=ceiling)

    # 5. Persist the mentions artifact (full corpus from the checkpoint) + telemetry.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"mentions": result.mentions, "telemetry": result.telemetry}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _print_summary(result, ceiling, est, cap_usd, args.out)
    # Non-zero exit if the ceiling halted the run — the operator must raise the
    # cap or narrow the corpus, then resume (checkpoint is durable).
    return 3 if result.halted is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
