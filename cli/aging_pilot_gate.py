"""
Aging Pilot Gate — Plan 04-05.

Enforces D-20..D-23 pilot criteria for aging_geroscience before
Plan 06 full backfill is authorized.

Automated checks:
  D-20  Coverage >= 85%: fraction of TOPIC# activities (score >= 0.3) that
        have primary_subtopic_id assigned.
  D-23  No pairwise overlap > 40%: Jaccard over min-cardinality for every
        (subtopic_a, subtopic_b) pair.

Side outputs:
  D-22  Blind-check worksheet CSV (N random pmids for second reviewer).
  calibration-notes.md  Knee-point candidate for WEIGHT_FLOOR (SUB-17).
  aging_pilot_results.md  Pass/fail gate table.

D-21 (reviewer corrections) is read from the hierarchy draft's
`reviewer_corrections_count` field (if present) or prompted for manual entry.

Usage:
    python aging_pilot_gate.py [--topic aging_geroscience]
        [--overlap-threshold 0.40] [--coverage-threshold 0.85]
        [--blind-sample-size 10] [--seed 42]
        [--output-md .planning/phases/04-subtopic-system/aging_pilot_results.md]
        [--dry-run]

Exit codes:
    0  All gates PASS (D-20, D-23, and D-21 if count available)
    1  One or more gates FAIL
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
from itertools import combinations
from pathlib import Path
from datetime import datetime, timezone

from pipeline_hierarchy.overlap import min_card_overlap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PLAN_DIR = Path(".planning/phases/04-subtopic-system")
DEFAULT_OUTPUT_MD = PLAN_DIR / "aging_pilot_results.md"
CALIBRATION_NOTES = PLAN_DIR / "calibration-notes.md"
WORKSHEET_FILE = Path("aging_blind_check_worksheet.csv")


# ---------------------------------------------------------------------------
# Pure analysis functions (no AWS calls — testable in isolation)
# ---------------------------------------------------------------------------


def compute_coverage(items: list) -> float:
    """
    D-20: Fraction of activities (score >= 0.3) that have primary_subtopic_id.

    Args:
        items: List of DynamoDB item dicts. Items are assumed to already be
               filtered to score >= 0.3 by the caller. Items with
               `primary_subtopic_id` present (non-None, non-empty) count as
               "assigned".

    Returns:
        float in [0.0, 1.0]. Returns 0.0 for empty list.
    """
    if not items:
        return 0.0

    assigned = sum(
        1 for item in items
        if item.get("primary_subtopic_id")
    )
    return assigned / len(items)


def compute_pairwise_overlap(pmid_sets: dict) -> dict:
    """
    D-23: Jaccard over min-cardinality for every (subtopic_a, subtopic_b) pair.

    The formula from CONTEXT.md §D-23:
        overlap = |pmids_a & pmids_b| / min(|pmids_a|, |pmids_b|)

    This is NOT standard Jaccard (|A∩B|/|A∪B|); it is deliberately stricter
    because a small subtopic entirely inside a large one would have standard
    Jaccard ≈ small/large (low) but min-cardinality overlap = 1.0 (high).

    Args:
        pmid_sets: {subtopic_id: set_of_pmids}. Sets may contain any hashable
                   type (typically int or str).

    Returns:
        Dict {(subtopic_a, subtopic_b): overlap_value} for every pair.
        Pairs where min-cardinality is 0 are skipped (result = 0.0, inserted).
        Order of keys in each tuple is sorted (smaller id first).
    """
    keys = sorted(pmid_sets.keys())
    result = {}
    for a, b in combinations(keys, 2):
        # Shared with the durable-ID reconcile (brick B) via pipeline_hierarchy.overlap
        # so the metric stays one source of truth. min_card_overlap returns 0.0 when
        # either set is empty, matching the prior inline min_card==0 -> 0.0 behavior.
        result[(a, b)] = min_card_overlap(pmid_sets[a], pmid_sets[b])
    return result


def build_blind_check_worksheet(
    activities: list,
    subtopics: list,
    n: int = 10,
    seed: int = 42,
) -> list:
    """
    D-22: Random-sample N activities for second-reviewer blind check.

    Reproducible via random.Random(seed) — same seed always produces the same
    sample regardless of global random state.

    Args:
        activities: List of activity dicts (each should have pmid, title,
                    synopsis). Missing keys default to empty string.
        subtopics: List of subtopic dicts (each has id, label, description).
        n: Number of rows to sample (default 10). If fewer activities exist,
           returns all.
        seed: Random seed for reproducibility (default 42).

    Returns:
        List of dicts, each with:
            pmid                str
            title               str
            synopsis            str
            candidate_subtopics str  (formatted subtopic options for reviewer)
            reviewer_picks      ""   (empty — reviewer fills this in)
    """
    rng = random.Random(seed)
    sample_size = min(n, len(activities))
    sampled = rng.sample(activities, sample_size)

    # Format subtopic options as a readable string for the reviewer
    subtopic_options = "; ".join(
        f"{s.get('id', '')} — {s.get('label', '')} ({s.get('description', '')[:80]}...)"
        for s in subtopics
    )

    worksheet = []
    for item in sampled:
        worksheet.append({
            "pmid": str(item.get("pmid", "")),
            "title": str(item.get("title", "")),
            "synopsis": str(item.get("synopsis", "")),
            "candidate_subtopics": subtopic_options,
            "reviewer_picks": "",
        })
    return worksheet


def knee_point(weights_dict: dict) -> float:
    """
    SUB-17: Knee-point detection for WEIGHT_FLOOR calibration.

    Algorithm:
        1. Sort weights descending.
        2. Find the index i where the relative drop w[i]/w[i+1] is largest.
        3. Return w[i+1] as the candidate WEIGHT_FLOOR.

    This separates "dense" subtopics (above the knee) from "sparse" ones
    (below). Sparse subtopics with total_weight < WEIGHT_FLOOR are filtered
    from Tier 1/2 retrieval (see CONTEXT.md §D-13).

    Args:
        weights_dict: {subtopic_id: total_weight_float}

    Returns:
        The total_weight value immediately after the largest relative drop.
        For a single entry, returns that entry's value.
        For uniform weights, returns w[1] (index 1 — first comparison).
    """
    if not weights_dict:
        return 0.0

    sorted_weights = sorted(weights_dict.values(), reverse=True)

    if len(sorted_weights) == 1:
        return sorted_weights[0]

    best_ratio = -1.0
    best_idx = 0
    for i in range(len(sorted_weights) - 1):
        w_curr = sorted_weights[i]
        w_next = sorted_weights[i + 1]
        if w_next == 0:
            # Infinite drop — strong knee
            ratio = float("inf")
        else:
            ratio = w_curr / w_next
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i

    return sorted_weights[best_idx + 1]


# ---------------------------------------------------------------------------
# DynamoDB query (live run only — not called during tests)
# ---------------------------------------------------------------------------


def _query_topic_items(topic_id: str, score_floor: float = 0.3) -> list:
    """
    Fetch all SCORE# rows under TOPIC#<topic_id> with score >= score_floor.

    Returns a list of item dicts (DocumentClient style — plain Python types).
    """
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.dynamodb_helpers import get_table, TABLE_NAME
    from boto3.dynamodb.conditions import Key

    table = get_table(TABLE_NAME)
    pk = f"TOPIC#{topic_id}"
    logger.info(f"Querying DynamoDB partition: {pk}")

    rows = []
    last_key = None
    while True:
        kwargs = {
            "KeyConditionExpression": (
                Key("PK").eq(pk) & Key("SK").begins_with("SCORE#")
            ),
            "Limit": 1000,
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        for item in resp.get("Items", []):
            try:
                score = float(item.get("score") or 0)
            except (TypeError, ValueError):
                score = 0.0
            if score >= score_floor:
                rows.append(item)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    logger.info(f"Fetched {len(rows)} items (score >= {score_floor}) for {topic_id}")
    return rows


def _build_pmid_sets(items: list) -> dict:
    """
    Build {subtopic_id: set(pmid)} from activity records.

    Each item contributes its pmid to its primary_subtopic_id's set.
    Items without primary_subtopic_id are skipped.
    """
    pmid_sets: dict = {}
    for item in items:
        sub = item.get("primary_subtopic_id")
        if not sub:
            continue
        pmid = str(item.get("pmid", ""))
        if not pmid:
            continue
        if sub not in pmid_sets:
            pmid_sets[sub] = set()
        pmid_sets[sub].add(pmid)
    return pmid_sets


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def _write_worksheet_csv(worksheet: list, path: Path) -> None:
    """Write blind-check worksheet to CSV."""
    if not worksheet:
        logger.warning("Worksheet is empty — skipping CSV write")
        return

    fieldnames = ["pmid", "title", "synopsis", "subtopic_options", "reviewer_pick_1", "reviewer_pick_2"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in worksheet:
            writer.writerow({
                "pmid": row.get("pmid", ""),
                "title": row.get("title", ""),
                "synopsis": row.get("synopsis", ""),
                "subtopic_options": row.get("candidate_subtopics", ""),
                "reviewer_pick_1": row.get("reviewer_picks", ""),
                "reviewer_pick_2": "",
            })
    logger.info(f"Wrote blind-check worksheet: {path} ({len(worksheet)} rows)")


def _update_calibration_notes(
    weights_dict: dict,
    knee_value: float,
    overlaps: dict,
    notes_path: Path,
) -> None:
    """
    Update calibration-notes.md with:
      §Aging weight distribution: sorted table + knee-point method annotation
      §Overlap observations: every pairwise Jaccard value from the Aging pilot
    """
    # Read current content
    if notes_path.exists():
        content = notes_path.read_text(encoding="utf-8")
    else:
        content = "# WEIGHT_FLOOR Calibration Notes (SUB-17)\n\n"

    # Build weight table
    sorted_weights = sorted(weights_dict.items(), key=lambda kv: -kv[1])
    weight_rows = []
    for sid, w in sorted_weights:
        weight_rows.append(f"| {sid:<55} | {w:>10.4f} |")

    weight_table = (
        "| subtopic_id                                            | total_weight |\n"
        "| ------------------------------------------------------ | ------------ |\n"
        + "\n".join(weight_rows)
    )

    distribution_section = (
        "## Aging weight distribution\n\n"
        f"{weight_table}\n\n"
        f"### Method: Knee-point\n\n"
        f"Knee-point detected at total_weight = {knee_value:.4f} — "
        f"candidate WEIGHT_FLOOR for v1 (locked during Plan 08 config update).\n\n"
        f"Sort order: descending. Largest relative drop identifies the knee "
        f"separating dense subtopics (above) from sparse subtopics (below).\n"
    )

    # Build overlap observations section
    overlap_rows = []
    for (a, b), val in sorted(overlaps.items(), key=lambda kv: -kv[1]):
        overlap_rows.append(f"| {a} | {b} | {val:.4f} |")

    overlap_section = (
        "## Overlap observations (validates v1 assumption: threshold 0.40)\n\n"
        "Pairwise Jaccard overlap (|A∩B| / min(|A|, |B|)) from Aging pilot run.\n"
        "A7 assumption: no pair should exceed 0.40. Review empirical values below\n"
        "to calibrate or confirm the threshold before full backfill.\n\n"
        "| subtopic_a | subtopic_b | overlap |\n"
        "| ---------- | ---------- | ------- |\n"
    )
    if overlap_rows:
        overlap_section += "\n".join(overlap_rows) + "\n"
    else:
        overlap_section += "| (no pairs — only one subtopic has assigned pmids) | — | — |\n"

    # Replace or append sections
    def _replace_or_append(text: str, header: str, new_section: str) -> str:
        """Replace existing section or append if absent."""
        # Find the section header line
        idx = text.find(f"\n{header}")
        if idx == -1:
            # Try at start of file
            if text.startswith(header):
                idx = 0
            else:
                # Append
                return text.rstrip() + f"\n\n{new_section}"

        # Find the next ## header after this one (or end of file)
        start = idx + 1 if idx > 0 else idx
        next_header_idx = text.find("\n## ", start + len(header))
        if next_header_idx == -1:
            return text[:start].rstrip() + f"\n\n{new_section}"
        else:
            return text[:start].rstrip() + f"\n\n{new_section}\n" + text[next_header_idx + 1:]

    content = _replace_or_append(content, "## Aging weight distribution", distribution_section)
    content = _replace_or_append(content, "## Overlap observations", overlap_section)

    # Also update Method and Chosen value sections if they are placeholders
    if "<!-- Pick one during Plan 05" in content:
        content = content.replace(
            "## Method\n<!-- Pick one during Plan 05 Aging pilot: knee-point | empirical | expert-judgment -->",
            "## Method\nKnee-point (largest relative drop in descending weight sequence)",
        )

    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(content, encoding="utf-8")
    logger.info(f"Updated calibration notes: {notes_path}")


def _write_results_md(
    output_path: Path,
    topic_id: str,
    coverage: float,
    coverage_threshold: float,
    coverage_pass: bool,
    overlaps: dict,
    overlap_threshold: float,
    overlap_pass: bool,
    max_overlap_pair: tuple,
    max_overlap_val: float,
    d21_count: int | None,
    d21_pass: bool | None,
    knee_value: float,
    worksheet_path: Path,
    n_items: int,
    n_assigned: int,
) -> None:
    """Write aging_pilot_results.md with gate table and guidance."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _status(flag):
        if flag is None:
            return "PENDING (manual)"
        return "PASS" if flag else "FAIL"

    lines = [
        f"# Aging Pilot Gate Results",
        f"",
        f"**Topic**: `{topic_id}`",
        f"**Generated**: {now}",
        f"",
        f"## Gate Summary",
        f"",
        f"| Gate | Measured | Threshold | Status |",
        f"| ---- | -------- | --------- | ------ |",
        f"| D-20 Coverage | {coverage:.1%} ({n_assigned}/{n_items} assigned) | ≥ {coverage_threshold:.0%} | {_status(coverage_pass)} |",
        f"| D-21 Reviewer corrections | {'≤' + str(d21_count) + '/subtopic' if d21_count is not None else 'not measured'} | ≤3/subtopic | {_status(d21_pass)} |",
        f"| D-22 Blind spot-check | pending second reviewer | ≥80% agreement | PENDING |",
        f"| D-23 Pairwise overlap | max={max_overlap_val:.3f} ({max_overlap_pair[0]} vs {max_overlap_pair[1]}) | ≤ {overlap_threshold:.2f} | {_status(overlap_pass)} |",
        f"",
        f"> Overlap threshold: 0.40 (v1 assumption — pending empirical calibration against Aging pilot)",
        f"",
        f"## D-22 Blind-Check Worksheet",
        f"",
        f"File: `{worksheet_path}`",
        f"",
        f"Send to second reviewer (Sumanth or research stakeholder). Reviewer fills",
        f"`reviewer_pick_1` and `reviewer_pick_2` WITHOUT seeing Pass 2 DynamoDB assignments.",
        f"Agreement = primary_subtopic_id matches reviewer_pick_1 OR reviewer_pick_2.",
        f"D-22 PASS if agreement ≥ 80% (8 of 10 rows).",
        f"",
        f"## WEIGHT_FLOOR Knee-Point Candidate",
        f"",
        f"Knee-point from `total_weights_{topic_id}.json`: **{knee_value:.4f}**",
        f"",
        f"This separates dense subtopics (high activity) from sparse ones (low activity).",
        f"Candidate WEIGHT_FLOOR = {knee_value:.4f} (to be frozen in Plan 08 chatbot config).",
        f"See `calibration-notes.md` §Aging weight distribution for full sorted table.",
        f"",
        f"## Verdict",
        f"",
        f"<!-- Reviewer fills after D-22 blind check is complete -->",
        f"<!-- `## Verdict: GO` — all four gates PASS → Plan 06 authorized -->",
        f"<!-- `## Verdict: NO-GO` — any gate FAIL → revise Plan 02 prompt, re-run Plans 02/03/04/05 -->",
        f"",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Wrote pilot results: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(
    topic_id: str,
    overlap_threshold: float,
    coverage_threshold: float,
    blind_sample_size: int,
    seed: int,
    output_md: Path,
    dry_run: bool = False,
) -> int:
    """
    Execute D-20 and D-23 automated gate checks for the given topic.

    Returns 0 if all available gates PASS, 1 otherwise.
    """
    # Load hierarchy draft for subtopic definitions
    hierarchy_path = PLAN_DIR / f"hierarchy_draft_{topic_id}.json"
    if not hierarchy_path.exists():
        logger.error(f"Hierarchy draft not found: {hierarchy_path}")
        return 1

    with open(hierarchy_path, encoding="utf-8") as f:
        hierarchy = json.load(f)

    subtopics = hierarchy.get("subtopics", [])
    if not subtopics:
        logger.error(f"No subtopics found in {hierarchy_path}")
        return 1

    # Load total_weights
    weights_path = PLAN_DIR / f"total_weights_{topic_id}.json"
    if not weights_path.exists():
        logger.error(f"Total weights not found: {weights_path}")
        return 1

    with open(weights_path, encoding="utf-8") as f:
        weights_data = json.load(f)

    weights_dict = weights_data.get("total_weights", {})
    if not weights_dict:
        logger.error("total_weights is empty")
        return 1

    # D-21: Check for reviewer corrections count in hierarchy draft
    d21_count = hierarchy.get("reviewer_corrections_count")
    d21_pass: bool | None = None
    if d21_count is not None:
        d21_pass = (d21_count <= 3)
        logger.info(f"D-21 reviewer corrections: {d21_count}/subtopic -> {'PASS' if d21_pass else 'FAIL'}")
    else:
        logger.info(
            "D-21: reviewer_corrections_count not in hierarchy draft. "
            "Manual entry required after Plan 02 review gate."
        )

    # Query DynamoDB
    logger.info(f"Querying DynamoDB for TOPIC#{topic_id} (score >= 0.3)...")
    items = _query_topic_items(topic_id)

    if not items:
        logger.error(f"No items returned for {topic_id} — is DynamoDB populated?")
        return 1

    # D-20 check
    coverage = compute_coverage(items)
    coverage_pass = coverage >= coverage_threshold
    n_assigned = sum(1 for item in items if item.get("primary_subtopic_id"))
    logger.info(
        f"D-20 Coverage: {coverage:.1%} ({n_assigned}/{len(items)}) "
        f"-> {'PASS' if coverage_pass else 'FAIL'}"
    )

    # D-23 check
    pmid_sets = _build_pmid_sets(items)
    # Ensure all subtopics appear in pmid_sets (even empty)
    for sub in subtopics:
        if sub["id"] not in pmid_sets:
            pmid_sets[sub["id"]] = set()

    overlaps = compute_pairwise_overlap(pmid_sets)
    max_overlap_val = max(overlaps.values()) if overlaps else 0.0
    max_overlap_pair = max(overlaps, key=lambda k: overlaps[k]) if overlaps else ("—", "—")
    overlap_pass = max_overlap_val <= overlap_threshold
    logger.info(
        f"D-23 Pairwise overlap: max={max_overlap_val:.3f} "
        f"({max_overlap_pair[0]} vs {max_overlap_pair[1]}) "
        f"-> {'PASS' if overlap_pass else 'FAIL'}"
    )

    # D-22 worksheet
    worksheet = build_blind_check_worksheet(items, subtopics, n=blind_sample_size, seed=seed)
    if not dry_run:
        _write_worksheet_csv(worksheet, WORKSHEET_FILE)

    # Knee-point for WEIGHT_FLOOR calibration
    knee_val = knee_point(weights_dict)
    logger.info(f"Knee-point (WEIGHT_FLOOR candidate): {knee_val:.4f}")

    # Update calibration notes
    if not dry_run:
        _update_calibration_notes(weights_dict, knee_val, overlaps, CALIBRATION_NOTES)

    # Write results markdown
    if not dry_run:
        _write_results_md(
            output_path=output_md,
            topic_id=topic_id,
            coverage=coverage,
            coverage_threshold=coverage_threshold,
            coverage_pass=coverage_pass,
            overlaps=overlaps,
            overlap_threshold=overlap_threshold,
            overlap_pass=overlap_pass,
            max_overlap_pair=max_overlap_pair,
            max_overlap_val=max_overlap_val,
            d21_count=d21_count,
            d21_pass=d21_pass,
            knee_value=knee_val,
            worksheet_path=WORKSHEET_FILE,
            n_items=len(items),
            n_assigned=n_assigned,
        )

    # Print summary
    print("\n=== Aging Pilot Gate Results ===")
    print(f"  D-20 Coverage:  {coverage:.1%} ({n_assigned}/{len(items)}) -> {'PASS' if coverage_pass else 'FAIL'} (threshold: {coverage_threshold:.0%})")
    print(f"  D-21 Reviewer:  {'count=' + str(d21_count) + ' -> ' + ('PASS' if d21_pass else 'FAIL') if d21_count is not None else 'PENDING (manual count required)'}")
    print(f"  D-22 Blind:     worksheet written -> PENDING second reviewer")
    print(f"  D-23 Overlap:   max={max_overlap_val:.3f} -> {'PASS' if overlap_pass else 'FAIL'} (threshold: {overlap_threshold})")
    print(f"  Knee-point:     {knee_val:.4f} (candidate WEIGHT_FLOOR)")

    # Exit code: 0 if D-20 + D-23 pass and D-21 passes (when count is known)
    auto_pass = coverage_pass and overlap_pass
    if d21_pass is not None:
        auto_pass = auto_pass and d21_pass

    if auto_pass:
        print("\nResult: PASS — automated gates satisfied. Proceed to D-22 blind check.")
        return 0
    else:
        print("\nResult: FAIL — one or more automated gates failed. Review above. Do NOT proceed to Plan 06.")
        return 1


def _parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Aging Pilot Gate (Plan 04-05): enforce D-20..D-23 criteria "
            "for aging_geroscience before Plan 06 full backfill."
        )
    )
    parser.add_argument(
        "--topic", default="aging_geroscience",
        help="Topic ID from taxonomy_v2.json (default: aging_geroscience)",
    )
    parser.add_argument(
        "--overlap-threshold", type=float, default=0.40,
        help="Maximum allowed pairwise Jaccard overlap (default: 0.40)",
    )
    parser.add_argument(
        "--coverage-threshold", type=float, default=0.85,
        help="Minimum required coverage (default: 0.85)",
    )
    parser.add_argument(
        "--blind-sample-size", type=int, default=10,
        help="Number of random pmids for blind-check worksheet (default: 10)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for blind-check worksheet sampling (default: 42)",
    )
    parser.add_argument(
        "--output-md", type=Path, default=DEFAULT_OUTPUT_MD,
        help=f"Output path for pilot results markdown (default: {DEFAULT_OUTPUT_MD})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip file writes and DynamoDB queries; print what would happen",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    exit_code = run(
        topic_id=args.topic,
        overlap_threshold=args.overlap_threshold,
        coverage_threshold=args.coverage_threshold,
        blind_sample_size=args.blind_sample_size,
        seed=args.seed,
        output_md=args.output_md,
        dry_run=args.dry_run,
    )
    sys.exit(exit_code)
