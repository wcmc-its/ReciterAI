"""Operator CLI for the Phase 8 tool/method seed build (A1).

Runs the seed pipeline (docs/tool-classifier-spec.md) over the committed
``tools_to_canonicalize.json`` seed and writes the two persistent registries
plus the three §9 review artifacts to ``out/tools/``. Salience is seed-mode
(llm_provisional, S withheld) and every method_tool is queued for A2
regrounding — the output is for HUMAN REVIEW before it feeds any downstream
stage (D-07 gate). It is NOT published to S3/DynamoDB.

Cost: A1 over the 230-name seed is bounded — a handful of Sonnet classification
calls + ~230 Titan v2 embeds. The corpus-wide A2 extraction is the expensive
fan-out and is a separate run that must go through a cost guard (out of scope
here). Use ``--limit N`` for a cheaper top-N probe first.

Conventions per CLAUDE.md:
  - Lazy boto3 / Bedrock client construction (no AWS at import).
  - No model-ID literals here — the live seams import their model ids from
    utils.bedrock_client (classifier) and pipeline_tools.embeddings (Titan).
  - Credentials flow from env vars only; never logged or branched on.

Usage:
    python -m cli.build_tool_taxonomy [--input PATH] [--out-dir DIR]
        [--limit N] [--batch-size N]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline_tools.salience import load_force_c_terms  # noqa: E402
from pipeline_tools.seed import run_seed, write_outputs  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("build_tool_taxonomy")

DEFAULT_INPUT = REPO_ROOT / "tools_to_canonicalize.json"
DEFAULT_OUT = REPO_ROOT / "out" / "tools"


def load_mentions(input_path: Path, limit: int | None) -> list[dict]:
    """Load seed mentions {raw_name, tool_category, pub_count}; optional top-N by pub_count."""
    data = json.loads(input_path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("tools", [])
    rows = [r for r in rows if (r.get("raw_name") or "").strip()]
    logger.info("Loaded %d seed mentions from %s", len(rows), input_path)
    if limit:
        rows = sorted(rows, key=lambda r: -(r.get("pub_count") or 0))[:limit]
        logger.info("Limited to top %d by pub_count", limit)
    return rows


def _print_summary(telemetry: dict, paths: list[Path]) -> None:
    print("\n=== seed run summary ===")
    for key in ("mentions", "canonical_tools", "tool_minted", "tool_attached", "tool_denied",
                "tool_unclassified", "mint_vs_attach_ratio", "families", "family_minted",
                "family_flagged_cross_supercat", "awaiting_a2_regrounding", "exceptions_total"):
        print(f"  {key:32s} {telemetry.get(key)}")
    print(f"  {'salience_distribution':32s} {telemetry.get('salience_distribution')}")
    print(f"  {'disposition_distribution':32s} {telemetry.get('disposition_distribution')}")
    print(f"  {'exceptions_by_type':32s} {telemetry.get('exceptions_by_type')}")
    print("\n  per-supercategory family count:")
    for sc, n in (telemetry.get("per_supercategory_family_count") or {}).items():
        print(f"    {sc:34s} {n}")
    print("\n  wrote:")
    for p in paths:
        print(f"    {p}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 8 tool/method seed build (A1).")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="seed JSON (default: tools_to_canonicalize.json)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="output dir (default: out/tools)")
    parser.add_argument("--limit", type=int, default=None, help="cap to top-N mentions by pub_count (cheaper probe)")
    parser.add_argument("--batch-size", type=int, default=50, help="classification batch size")
    args = parser.parse_args(argv)

    mentions = load_mentions(args.input, args.limit)
    if not mentions:
        logger.error("No mentions loaded from %s", args.input)
        return 1

    # Live seams (lazy — constructed here, not at import).
    from pipeline_tools.classify import make_classifier_call_json
    from pipeline_tools.embeddings import titan_embed

    call_json = make_classifier_call_json()
    result = run_seed(
        mentions,
        call_json=call_json,
        embed=titan_embed,
        force_c_terms=load_force_c_terms(),
        batch_size=args.batch_size,
    )
    paths = write_outputs(result, args.out_dir)
    _print_summary(result.telemetry, paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
