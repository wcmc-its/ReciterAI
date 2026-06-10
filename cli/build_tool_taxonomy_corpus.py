"""Operator CLI for the Phase 8 A2 corpus taxonomy build (Steps C-G).

Consumes the raw mentions produced by the extraction harness
(``cli.extract_tool_mentions`` -> ``out/tools/a2_mentions.json``), LOADS the seed
registries (so canonical ids carry forward, D-06), and runs the corpus pipeline
(``pipeline_tools.corpus_run``): classify -> §8 tool registry (real pub_ids,
grants kept out of the pub-filter) -> §5 grounded salience -> §7 family
match-or-mint -> §7.2 relabel + §7 dedup -> faculty rollup -> §9 outputs. It then
ASSEMBLES the SPS-facing ``tools.json`` and, only with ``--publish``, uploads it
to ``s3://wcmc-reciterai-artifacts/tools/`` (default is a dry-run report — the
D-07 review gate).

Cost: this is a second LLM spend after extraction — Bedrock Sonnet classification
(+ optional relabel) over the UNIQUE surface forms, not the raw mentions. Run a
``--limit`` probe first and inspect telemetry (mint-vs-attach, salience
distribution, family count) before the full corpus.

Conventions per CLAUDE.md: lazy Bedrock/S3 construction (no AWS at import),
credentials from env only, no model-ID literals here (the live seams import
theirs). The accreted registries are written to a SEPARATE ``--out-dir`` by
default so the seed snapshot is never clobbered before review.

Usage:
    python -m cli.build_tool_taxonomy_corpus --limit 500            # cheap probe
    python -m cli.build_tool_taxonomy_corpus                        # full (dry-run publish)
    python -m cli.build_tool_taxonomy_corpus --publish              # full + real S3 upload
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline_tools.corpus_run import group_mentions, run_corpus, write_outputs  # noqa: E402
from pipeline_tools.publish import build_publish_payload, publish_artifacts  # noqa: E402
from pipeline_tools.salience import load_force_c_terms  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("build_tool_taxonomy_corpus")

DEFAULT_INPUT = REPO_ROOT / "out" / "tools" / "a2_mentions.json"
DEFAULT_REGISTRY_DIR = REPO_ROOT / "out" / "tools"
DEFAULT_OUT_DIR = REPO_ROOT / "out" / "tools" / "a2"


def load_mentions(input_path: Path, limit: int | None) -> list[dict]:
    """Load raw mentions {raw_name, tool_category, context, pmid, source_kind, authors}."""
    data = json.loads(input_path.read_text(encoding="utf-8"))
    rows = data.get("mentions", data) if isinstance(data, dict) else data
    rows = [r for r in rows if (r.get("raw_name") or "").strip()]
    logger.info("Loaded %d raw mention(s) from %s", len(rows), input_path)
    if limit:
        rows = rows[:limit]
        logger.info("Limited to first %d raw mention(s)", limit)
    return rows


def _print_summary(t: dict, paths: list[Path], publish_report: list[dict], published: bool) -> None:
    print("\n=== A2 corpus run summary ===")
    for key in ("unique_forms", "canonical_tools", "tool_minted", "tool_attached", "tool_denied",
                "tool_unclassified", "mint_vs_attach_ratio", "families", "family_minted",
                "family_flagged_cross_supercat", "method_tools", "method_tools_grounded",
                "s_spread_cutoff", "max_spread", "grant_signal_tools", "faculty_count",
                "exceptions_total"):
        print(f"  {key:28s} {t.get(key)}")
    print(f"  {'salience_distribution':28s} {t.get('salience_distribution')}")
    print(f"  {'salience_basis_distribution':28s} {t.get('salience_basis_distribution')}")
    print(f"  {'supercategory_distribution':28s} {t.get('supercategory_distribution')}")
    print(f"  {'exceptions_by_type':28s} {t.get('exceptions_by_type')}")
    print("\n  wrote:")
    for p in paths:
        print(f"    {p}")
    print(f"\n  publish ({'UPLOADED' if published else 'DRY-RUN — re-run with --publish to upload'}):")
    for r in publish_report:
        print(f"    s3://wcmc-reciterai-artifacts/{r['key']}  ({r['bytes']:,} bytes)  uploaded={r['uploaded']}")


def main(argv: list[str] | None = None, *, call_json=None, embed=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 8 A2 corpus tool/method taxonomy build (Steps C-G).")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="raw mentions JSON (extraction output)")
    parser.add_argument("--registry-dir", type=Path, default=DEFAULT_REGISTRY_DIR,
                        help="dir to LOAD seed/prior registries from (default: out/tools)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help="dir to WRITE accreted registries + artifacts (default: out/tools/a2 — keeps seed pristine)")
    parser.add_argument("--limit", type=int, default=None, help="cap to first N raw mentions (cheaper probe)")
    parser.add_argument("--batch-size", type=int, default=50, help="classification batch size")
    parser.add_argument("--relabel-batch-size", type=int, default=40, help="family relabel batch size")
    parser.add_argument("--no-relabel", action="store_true", help="skip the §7.2 LLM relabel pass (cheaper dev run)")
    parser.add_argument("--no-family-overrides", action="store_true",
                        help="skip the D-07 deterministic family fix batch (reroute/relabel/merge, v6→v7)")
    parser.add_argument("--no-consolidation", action="store_true",
                        help="skip the 820 consolidation batch (within-supercat merges + relabels + display tiers, v7'→v8)")
    parser.add_argument("--no-adhoc-dedup", action="store_true",
                        help="skip the accreting ad-hoc dedup batch (scholar co-assignment dupe scan → config/family_adhoc_dedup.json)")
    parser.add_argument("--checkpoint-dir", type=Path, default=None,
                        help="resumable classify-checkpoint dir (default: <out-dir>/_checkpoint; a crash re-classifies only the missing forms)")
    parser.add_argument("--publish", action="store_true",
                        help="upload tools.json to s3://wcmc-reciterai-artifacts/tools/ (default: dry-run report)")
    args = parser.parse_args(argv)
    checkpoint_dir = args.checkpoint_dir if args.checkpoint_dir is not None else (args.out_dir / "_checkpoint")

    mentions = load_mentions(args.input, args.limit)
    if not mentions:
        logger.error("No mentions loaded from %s", args.input)
        return 1

    uniques = group_mentions(mentions)
    n_classify_batches = (len(uniques) + args.batch_size - 1) // args.batch_size
    logger.info("preflight: %d raw -> %d unique form(s) -> ~%d classification batch(es)%s",
                len(mentions), len(uniques), n_classify_batches,
                "" if args.no_relabel else " + relabel pass")

    # Load the seed registries WITH a Titan-backed embedding cache (lazy live seams).
    from pipeline_tools.embeddings import EmbeddingCache
    from pipeline_tools.registry import FamilyRegistry, ToolRegistry

    if call_json is None:
        from pipeline_tools.classify import make_classifier_call_json
        call_json = make_classifier_call_json()
    if embed is None:
        from pipeline_tools.embeddings import titan_embed
        embed = titan_embed
    cache = EmbeddingCache(embed=embed)

    reg = args.registry_dir
    tool_registry = ToolRegistry.load(reg / "tool_registry.json", reg / "tool_denylist.json", cache=cache)
    family_registry = FamilyRegistry.load(reg / "family_registry.json", cache=cache)
    logger.info("loaded seed registries: %d tools, %d families from %s",
                len(tool_registry), len(family_registry), reg)

    logger.info("classify checkpoint dir: %s", checkpoint_dir)
    result = run_corpus(
        mentions, call_json=call_json,
        tool_registry=tool_registry, family_registry=family_registry,
        force_c_terms=load_force_c_terms(),
        batch_size=args.batch_size, relabel_batch_size=args.relabel_batch_size,
        relabel=not args.no_relabel, apply_family_overrides=not args.no_family_overrides,
        apply_consolidation=not args.no_consolidation,
        apply_adhoc_dedup=not args.no_adhoc_dedup,
        checkpoint_dir=checkpoint_dir,
    )

    provenance = {
        "run_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input": str(args.input),
        "raw_mentions": len(mentions),
        "limit": args.limit,
        "registry_dir": str(args.registry_dir),
    }
    payload = build_publish_payload(result, provenance=provenance)
    paths = write_outputs(result, args.out_dir, payload=payload)
    publish_report = publish_artifacts(payload, dry_run=not args.publish)

    _print_summary(result.telemetry, paths, publish_report, published=args.publish)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
