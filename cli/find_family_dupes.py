#!/usr/bin/env python3
"""Surface near-duplicate method families via scholar co-assignment (read-only).

Reads the published ``family_registry.json`` + ``tool_faculty_rollup.json`` and
writes a review artifact (``family-dupe-candidates.md`` + ``.json``) ranking
family pairs that collide on real scholar profile pages. No AWS, no mutation —
fixes are encoded by hand into ``config/family_adhoc_dedup.json`` after review.

    python -m cli.find_family_dupes
    python -m cli.find_family_dupes -k 2 --min-coscholars 3
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pipeline_tools.family_dupe_scan import render_markdown, scan_family_dupes

DEFAULT_DIR = Path("out/tools/a2")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", type=Path, default=DEFAULT_DIR / "family_registry.json",
                   help="family registry JSON (published family set)")
    p.add_argument("--rollup", type=Path, default=DEFAULT_DIR / "tool_faculty_rollup.json",
                   help="per-scholar tool/family rollup JSON")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_DIR / "_review",
                   help="where to write family-dupe-candidates.{md,json}")
    p.add_argument("-k", type=int, default=2,
                   help="min pubs a family must cover for a scholar to count it (default 2)")
    p.add_argument("--min-coscholars", type=int, default=3,
                   help="min scholars a pair must collide on to enter the gated list (default 3)")
    p.add_argument("--label-jaccard-min", type=float, default=0.34)
    p.add_argument("--member-jaccard-min", type=float, default=0.18)
    args = p.parse_args(argv)

    if not args.registry.exists():
        p.error(f"registry not found: {args.registry}")
    if not args.rollup.exists():
        p.error(f"rollup not found: {args.rollup}")

    result = scan_family_dupes(
        args.registry, args.rollup,
        k=args.k, min_coscholars=args.min_coscholars,
        label_jaccard_min=args.label_jaccard_min, member_jaccard_min=args.member_jaccard_min,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.out_dir / "family-dupe-candidates.md"
    json_path = args.out_dir / "family-dupe-candidates.json"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    s = result["stats"]
    print(f"\n{s['co_assigned_pairs']} co-assigned pairs (k={s['k']}) "
          f"→ {s['gated_candidates']} gated candidates, {s['identical_label_pairs']} identical-label")
    print(f"  wrote {md_path}")
    print(f"  wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
