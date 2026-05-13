"""Per-CWID rollup builder.

Reads `cwid_topic_counts.csv` and `cwid_subtopic_counts.csv`, aggregates per
`personIdentifier` (CWID), and writes `cwid_rollup.csv` sorted by
`-n_activities, cwid_asc` (the secondary key was implicit before; now
explicit so incremental and full runs produce byte-identical outputs).

Phase 10 D-07 / D-08 wiring:

- `--cwids 'c1,c2,...'` (or `--cwids @/path/to/file`) switches to
  *incremental mode*: read prior `cwid_rollup.csv`, recompute only the
  listed CWIDs from the breakdown CSVs filtered to those CWIDs, and
  merge into the prior output. CWIDs not in the dirty set carry over
  from the prior file unchanged. Dirty CWIDs that have no rows in the
  breakdown CSVs are removed from the rollup.
- `--emit-envelope` mode (Phase 10 hot path): on exit, emits the
  STAGE# complete/skipped record as JSON on stdout instead of writing
  to DynamoDB. Step Functions DynamoDB:PutItem SDK integration
  consumes the envelope.

Parity contract (T5 acceptance gate):

    full_rollup(N) == incremental_rollup(dirty_subset) merged with
                     prior_rollup(N \\ dirty_subset)

Byte-identical CSVs after the deterministic sort. The parity test in
`tests/test_rollup_incremental_parity.py` is the highest-risk gate of
Phase 10 (D-08); a regression there blocks the wave.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sys
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

# Ensure repo root is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).parent))

from utils.dynamodb_helpers import get_table, TABLE_NAME
from utils.stage_records import (
    build_complete_record,
    build_skipped_record,
    compute_input_hash,
    should_skip,
    write_complete,
    write_skipped,
)
# IN-05: subtopic-CSV path/header helpers live in utils/csv_paths.py so
# build_cwid_json.py shares the same logic. Re-exported below for any
# external caller that imported the names from this module.
from utils.csv_paths import (
    DEFAULT_SUBTOPIC_CSV,
    INCLUSIVE_SUBTOPIC_CSV,
    LEGACY_SUBTOPIC_CSV,
    SUBTOPIC_ID_COLUMNS,
    pick_subtopic_id_column as _pick_subtopic_id_column,
    resolve_subtopic_csv as _resolve_subtopic_csv,
)
from utils.iso_clock import now_iso


# --- Constants -------------------------------------------------------------

DEFAULT_TOPIC_CSV = Path("cwid_topic_counts.csv")
DEFAULT_OUT_CSV = Path("cwid_rollup.csv")

ROLLUP_HEADER = [
    "personIdentifier",
    "n_activities",
    "n_distinct_topics",
    "n_subtopic_activities",
    "n_distinct_subtopics",
]

STAGE_NAME = "rollup_by_cwid"
STAGE_SCOPE_GLOBAL = "GLOBAL"
ROLLUP_COST_USD = Decimal("0")  # rollup is local CSV aggregation, no Bedrock


logger = logging.getLogger(__name__)




# --- Aggregation -----------------------------------------------------------


def aggregate_from_breakdowns(
    topic_csv: Path,
    subtopic_csv: Path,
    *,
    cwid_filter: set[str] | None = None,
) -> dict[str, tuple[int, int, int, int]]:
    """Aggregate per-CWID counts from the two breakdown CSVs.

    Returns {cwid: (n_activities, n_distinct_topics, n_subtopic_activities,
                    n_distinct_subtopics)}.

    When `cwid_filter` is provided, only those CWIDs are aggregated.
    """
    n_activities: dict[str, int] = defaultdict(int)
    n_topics: dict[str, set] = defaultdict(set)
    n_subtopic_activities: dict[str, int] = defaultdict(int)
    n_subtopics: dict[str, set] = defaultdict(set)

    with open(topic_csv) as f:
        for row in csv.DictReader(f):
            cwid = row["personIdentifier"]
            if cwid_filter is not None and cwid not in cwid_filter:
                continue
            n_activities[cwid] += int(row["n_activities"])
            n_topics[cwid].add(row["topic_id"])

    with open(subtopic_csv) as f:
        reader = csv.DictReader(f)
        sub_id_key = _pick_subtopic_id_column(reader.fieldnames, subtopic_csv)
        for row in reader:
            cwid = row["personIdentifier"]
            if cwid_filter is not None and cwid not in cwid_filter:
                continue
            n_subtopic_activities[cwid] += int(row["n_activities"])
            n_subtopics[cwid].add(row[sub_id_key])

    all_cwids = set(n_activities) | set(n_subtopics)
    return {
        cwid: (
            n_activities[cwid],
            len(n_topics[cwid]),
            n_subtopic_activities[cwid],
            len(n_subtopics[cwid]),
        )
        for cwid in all_cwids
    }


def sort_rollup_rows(
    rollup: dict[str, tuple[int, int, int, int]],
) -> list[tuple]:
    """Deterministic sort: n_activities desc, then cwid asc."""
    rows = [(cwid, *vals) for cwid, vals in rollup.items()]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def write_rollup_csv(path: Path, rollup: dict[str, tuple[int, int, int, int]]) -> int:
    rows = sort_rollup_rows(rollup)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(ROLLUP_HEADER)
        for row in rows:
            w.writerow(row)
    return len(rows)


def read_rollup_csv(path: Path) -> dict[str, tuple[int, int, int, int]]:
    """Inverse of write_rollup_csv. Used by incremental mode for the prior."""
    rollup: dict[str, tuple[int, int, int, int]] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            rollup[row["personIdentifier"]] = (
                int(row["n_activities"]),
                int(row["n_distinct_topics"]),
                int(row["n_subtopic_activities"]),
                int(row["n_distinct_subtopics"]),
            )
    return rollup


# --- Full and incremental rollups -----------------------------------------


def full_rollup(
    topic_csv: Path, subtopic_csv: Path
) -> dict[str, tuple[int, int, int, int]]:
    return aggregate_from_breakdowns(topic_csv, subtopic_csv)


def incremental_rollup(
    topic_csv: Path,
    subtopic_csv: Path,
    *,
    dirty_cwids: set[str],
    prior_rollup: dict[str, tuple[int, int, int, int]],
) -> dict[str, tuple[int, int, int, int]]:
    """Merge a recomputed dirty subset into the prior rollup.

    Dirty CWIDs that no longer appear in the breakdown CSVs (e.g., all
    their activities were removed) drop out of the rollup. CWIDs not in
    the dirty set carry over from `prior_rollup` unchanged.
    """
    dirty_aggregated = aggregate_from_breakdowns(
        topic_csv, subtopic_csv, cwid_filter=dirty_cwids
    )
    merged = {
        cwid: vals for cwid, vals in prior_rollup.items() if cwid not in dirty_cwids
    }
    for cwid in dirty_cwids:
        if cwid in dirty_aggregated:
            merged[cwid] = dirty_aggregated[cwid]
    return merged


# --- input_hash ------------------------------------------------------------


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compute_rollup_input_hash(
    *,
    topic_csv: Path,
    subtopic_csv: Path,
    cwids: list[str] | None,
) -> str:
    """Content-addressed input_hash for a rollup run.

    Substitutes file-content sha256 of each breakdown CSV for the
    `score_version` / `hierarchy_version` placeholders until Phase 11
    stamps those first-class on every record (spec §3, D-2).

    `cwids` is the sorted-and-deduped dirty subset for incremental
    runs, or None for a full run (encoded as the sentinel "*ALL*" so
    full vs. empty-incremental don't collide).
    """
    cwid_key: object
    if cwids is None:
        cwid_key = "*ALL*"
    else:
        cwid_key = sorted({str(c) for c in cwids})
    return compute_input_hash(
        STAGE_NAME,
        {
            "score_version_sha256": _sha256_path(topic_csv),
            "hierarchy_version_sha256": _sha256_path(subtopic_csv),
            "cwid_set": cwid_key,
        },
    )


# --- CLI helpers -----------------------------------------------------------


def _parse_cwid_list_arg(value: str | None) -> list[str] | None:
    """Parse --cwids: 'c1,c2,...' or '@/path/to/file' (one CWID per line)."""
    if value is None:
        return None
    if value.startswith("@"):
        path = Path(value[1:])
        if not path.exists():
            raise SystemExit(f"--cwids file not found: {path}")
        return [
            line.strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return [s.strip() for s in value.split(",") if s.strip()]


def _print_top_rows(rollup: dict[str, tuple[int, int, int, int]], n: int = 10) -> None:
    rows = sort_rollup_rows(rollup)
    print(f"\nTop {min(n, len(rows))} by n_activities:")
    print(
        f"{'cwid':<12} {'acts':>6} {'topics':>7} {'sub_acts':>9} {'subtopics':>10}"
    )
    for row in rows[:n]:
        print(f"{row[0]:<12} {row[1]:>6} {row[2]:>7} {row[3]:>9} {row[4]:>10}")


# --- main ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rollup_by_cwid")
    parser.add_argument(
        "--topic-csv", type=Path, default=DEFAULT_TOPIC_CSV,
        help="Path to cwid_topic_counts.csv (default: cwid_topic_counts.csv)",
    )
    parser.add_argument(
        "--subtopic-csv", type=Path, default=None,
        help=(
            "Path to the exclusive subtopic counts CSV "
            "(default: faculty_subtopic_counts_exclusive.csv; "
            "falls back to legacy cwid_subtopic_counts.csv with a deprecation warning "
            "if the new name is absent — Phase 12 D-13 dual-write deprecation window)."
        ),
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT_CSV,
        help="Output CSV path (default: cwid_rollup.csv)",
    )
    parser.add_argument(
        "--cwids", default=None, metavar="LIST",
        help=(
            "Phase 10 incremental mode. Comma-separated CWIDs or '@/path' "
            "to a one-CWID-per-line file. Recomputes only the listed CWIDs "
            "and merges into the prior --out CSV. Default: full re-rollup."
        ),
    )
    parser.add_argument(
        "--emit-envelope", action="store_true",
        help=(
            "Phase 10 D-07 hot-path mode. Emit the STAGE# record as JSON "
            "on stdout instead of writing to DynamoDB."
        ),
    )
    parser.add_argument(
        "--skip-stage-write", action="store_true",
        help=(
            "Skip the STAGE# DynamoDB write entirely (no envelope either). "
            "Useful for local CSV-only runs without AWS credentials."
        ),
    )
    args = parser.parse_args(argv)

    stage_started_at = now_iso()
    t_stage_start = time.monotonic()

    dirty_cwids = _parse_cwid_list_arg(args.cwids)

    # Phase 12 D-13: resolve canonical subtopic CSV name (with legacy fallback).
    subtopic_csv = _resolve_subtopic_csv(args.subtopic_csv)

    stage_table = None
    if not args.skip_stage_write:
        stage_table = get_table(TABLE_NAME)

    input_hash = compute_rollup_input_hash(
        topic_csv=args.topic_csv,
        subtopic_csv=subtopic_csv,
        cwids=dirty_cwids,
    )

    # --- STAGE# should_skip gate ---
    if stage_table is not None:
        skip, prior = should_skip(
            stage_table,
            stage=STAGE_NAME,
            scope=STAGE_SCOPE_GLOBAL,
            input_hash=input_hash,
        )
        if skip:
            duration_ms = int((time.monotonic() - t_stage_start) * 1000)
            skipped_kwargs = dict(
                stage=STAGE_NAME,
                scope=STAGE_SCOPE_GLOBAL,
                input_hash=input_hash,
                skip_reason=(
                    f"input_hash unchanged since prior complete run at "
                    f"{prior.get('started_at', '?')}"
                ),
                started_at=stage_started_at,
                completed_at=now_iso(),
                duration_ms=duration_ms,
            )
            if args.emit_envelope:
                print(json.dumps(build_skipped_record(**skipped_kwargs), default=str))
            else:
                write_skipped(stage_table, **skipped_kwargs)
            print(
                f"[STAGE# skip] rollup_by_cwid skipped — prior complete at "
                f"{prior.get('started_at', '?')} (input_hash {input_hash[:12]})"
            )
            return 0

    # --- Compute rollup ---
    if dirty_cwids is None:
        rollup = full_rollup(args.topic_csv, subtopic_csv)
        mode_label = "full"
    else:
        prior = read_rollup_csv(args.out)
        rollup = incremental_rollup(
            args.topic_csv,
            subtopic_csv,
            dirty_cwids=set(dirty_cwids),
            prior_rollup=prior,
        )
        mode_label = f"incremental(|dirty|={len(dirty_cwids)})"

    n_written = write_rollup_csv(args.out, rollup)
    print(f"Wrote {args.out} ({n_written:,} rows, mode={mode_label})")
    _print_top_rows(rollup)

    # --- STAGE# complete row ---
    if stage_table is not None:
        completed_at = now_iso()
        duration_ms = int((time.monotonic() - t_stage_start) * 1000)
        complete_kwargs = dict(
            stage=STAGE_NAME,
            scope=STAGE_SCOPE_GLOBAL,
            input_hash=input_hash,
            started_at=stage_started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            cost_observed_usd=ROLLUP_COST_USD,
            output_pointer=str(args.out),
            records_written=n_written,
        )
        if args.emit_envelope:
            print(json.dumps(build_complete_record(**complete_kwargs), default=str))
        else:
            write_complete(stage_table, **complete_kwargs)

    return 0


if __name__ == "__main__":
    sys.exit(main())
