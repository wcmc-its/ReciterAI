"""
Probe cross-topic subtopic overlap (issue #164, Ask A — option 0).

Loads the per-topic ``hierarchy_augmented_*.json`` files, builds the subtopic
set, and runs the dedup decision engine across topic boundaries to QUANTIFY the
overlap before any merge is applied. Read-only: prints (and optionally writes)
the proposed merge groups and parent/child flags. Mutates nothing.

This is the recommended first step in #164: see the real worklist (e.g. the
spaceflight ×3 and disparities ×3 clusters) before building the bundle-time
merge that acts on it.

Membership signal: uses each subtopic's ``seed_pmids`` as the PMID set. That is
the discovery-seed membership persisted in the augmented files; the production
merge should swap in the full Pass-2 assignment sets when available.

Usage:
    # article-overlap only, no AWS needed:
    python -m cli.probe_subtopic_overlap --no-cosine

    # full run (article overlap + Titan description cosine; needs Bedrock creds):
    python -m cli.probe_subtopic_overlap --output /tmp/overlap_probe.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from pipeline_hierarchy.bundler import DEFAULT_AUGMENTED_DIR
from pipeline_hierarchy.subtopic_dedup import (
    Subtopic,
    decide_dedup,
    load_dedup_thresholds,
)


def build_subtopics_from_augmented(augmented_dir: Path) -> list[Subtopic]:
    """Read augmented files into Subtopic records. Pure; no AWS, no embedding.

    Skips subtopics with a blank ``short_description`` (their cosine signal is
    void anyway) only when they also lack a ``description`` fallback; otherwise
    uses ``short_description`` with ``description`` as the fallback text.
    """
    files = sorted(augmented_dir.glob("hierarchy_augmented_*.json"))
    if not files:
        raise FileNotFoundError(
            f"no hierarchy_augmented_*.json files in {augmented_dir}"
        )
    subs: list[Subtopic] = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        topic_id = data.get("topic_id")
        if not topic_id:
            raise ValueError(f"{path}: missing topic_id")
        for s in data.get("subtopics", []):
            description = (s.get("short_description") or s.get("description") or "").strip()
            subs.append(
                Subtopic(
                    id=s["id"],
                    topic_id=topic_id,
                    description=description,
                    pmids=frozenset(s.get("seed_pmids", [])),
                    total_weight=float(s.get("total_weight", 0.0)),
                    activity_count=int(s.get("activity_count", 0)),
                    label=(s.get("display_name") or s.get("label") or s["id"]),
                )
            )
    return subs


def _zero_embed(texts: list[str]) -> list[list[float]]:
    """Degenerate embed -> cosine always 0.0. Drives an article-overlap-only run."""
    return [[0.0] for _ in texts]


def _format_report(plan, n_subtopics: int) -> str:
    lines = [
        f"Subtopics scanned:      {n_subtopics}",
        f"Cross-topic pairs:      {plan.pairs_considered}",
        f"Merge groups proposed:  {len(plan.merges)}",
        f"Parent/child flags:     {len(plan.flags)}",
        f"Thresholds:             {plan.thresholds}",
        "",
    ]
    if plan.merges:
        lines.append("=== MERGE GROUPS (canonical <- folded) ===")
        for g in plan.merges:
            lines.append(f"  {g.canonical_id}  <-  {', '.join(g.member_ids)}")
            lines.append(f"      topics: {', '.join(g.topic_ids)}")
            for e in g.evidence:
                lines.append(
                    f"      {e['pair'][0]} ~ {e['pair'][1]}: {e['signal']}"
                )
        lines.append("")
    if plan.flags:
        lines.append("=== PARENT/CHILD FLAGS (kept, review only) ===")
        for f in plan.flags:
            lines.append(
                f"  {f.parent_id} (parent) ⊃ {f.child_id} (child) — "
                f"overlap={f.article_overlap}, ratio={f.size_ratio}x"
            )
    return "\n".join(lines)


def _format_markdown(plan, subs) -> str:
    """Human-readable worklist: merge groups (largest first) with labels + topics."""
    by_id = {s.id: s for s in subs}

    def show(sid: str) -> str:
        s = by_id.get(sid)
        if s is None:
            return f"`{sid}`"
        return f"**{s.label}** _({s.topic_id}, {len(s.pmids)} pmids, w={s.total_weight:g})_"

    lines = [
        "# Cross-topic subtopic dedup — merge worklist (#164)",
        "",
        f"- Subtopics scanned: **{len(subs)}**",
        f"- Cross-topic pairs evaluated: **{plan.pairs_considered}**",
        f"- Merge groups: **{len(plan.merges)}**  |  Parent/child flags: **{len(plan.flags)}**",
        f"- Thresholds: `{plan.thresholds}`",
        "",
        "Canonical survivor = highest total_weight. Review each group: ✅ keep / "
        "❌ split / ✂️ drop a member.",
        "",
    ]

    ranked = sorted(plan.merges, key=lambda g: (-(1 + len(g.member_ids)), g.canonical_id))
    for i, g in enumerate(ranked, 1):
        size = 1 + len(g.member_ids)
        lines.append(f"## {i}. [{size} members] canonical: {show(g.canonical_id)}")
        for m in g.member_ids:
            lines.append(f"   - ⤷ folds in: {show(m)}")
        for e in g.evidence:
            cos = "" if e["cosine"] is None else f", cosine={e['cosine']}"
            lines.append(
                f"     - _{e['pair'][0]} ~ {e['pair'][1]}: overlap={e['article_overlap']}{cos}_"
            )
        lines.append("")

    if plan.flags:
        lines.append(f"## Parent/child flags ({len(plan.flags)}) — kept, NOT merged")
        lines.append("")
        lines.append("First 40 (by parent id):")
        for f in plan.flags[:40]:
            lines.append(
                f"- {show(f.parent_id)} ⊃ {show(f.child_id)} "
                f"— overlap={f.article_overlap}, ratio={f.size_ratio}x"
            )
        if len(plan.flags) > 40:
            lines.append(f"- … and {len(plan.flags) - 40} more")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--augmented-dir",
        type=Path,
        default=DEFAULT_AUGMENTED_DIR,
        help="directory of hierarchy_augmented_*.json files",
    )
    parser.add_argument(
        "--no-cosine",
        action="store_true",
        help="article-overlap only; skip Titan embedding (no AWS required)",
    )
    parser.add_argument(
        "--grouping",
        choices=("clique", "component"),
        default="clique",
        help="clique (default, breaks chains) or component (transitive)",
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="write the full plan as JSON here"
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="write a human-readable worklist (labels + topics) as Markdown here",
    )
    args = parser.parse_args(argv)

    subs = build_subtopics_from_augmented(args.augmented_dir)
    thresholds = load_dedup_thresholds()
    embed = _zero_embed if args.no_cosine else None

    plan = decide_dedup(subs, embed=embed, grouping=args.grouping, **thresholds)

    print(_format_report(plan, len(subs)))

    if args.output is not None:
        payload = {
            "pairs_considered": plan.pairs_considered,
            "thresholds": plan.thresholds,
            "merges": [asdict(g) for g in plan.merges],
            "flags": [asdict(f) for f in plan.flags],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nWrote {args.output}")

    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(_format_markdown(plan, subs), encoding="utf-8")
        print(f"Wrote {args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
