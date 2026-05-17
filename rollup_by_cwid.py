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

#80 Phase 2 / #90 — CWID-scoped, PMID-aware rollup:

- `--cwid CWID` switches to *CWID-scoped mode*. Instead of the CSV
  breakdowns it reads, for one CWID, its live accepted-publication
  PMID set (ReciterDB `analysis_summary_author`, via
  `get_pmids_for_cwid`) and its scored `TOPIC#` activity rows
  (DynamoDB `FacultyIndex`), computes the rollup over the PMID-aware
  intersection of the two — stale `TOPIC#` rows for de-attributed
  PMIDs do not inflate the counts — and writes a
  `STAGE#rollup_by_cwid#cwid:{cwid}` row carrying `input_pmid_set`
  (the snapshot, consumed by the onboarding detector's R9 churn
  check) and `rollup_counts` (the four per-CWID tallies). Mutually
  exclusive with `--cwids`; the hot/cold CSV paths are unchanged.

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
from typing import Any, Iterable, Mapping

# Ensure repo root is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).parent))

from boto3.dynamodb.conditions import Key

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
ROLLUP_COST_USD = Decimal("0")  # rollup is local aggregation (CSV or DDB), no Bedrock

# CWID-scoped rollup (#80 Phase 2 / #90). The per-CWID path queries the
# FacultyIndex GSI for a CWID's TOPIC# activity rows.
_TOPIC_PK_PREFIX = "TOPIC#"
_ACTIVITY_SK_PREFIX = "SCORE#"
_FACULTY_UID_PREFIX = "cwid_"


logger = logging.getLogger(__name__)


def _cwid_scope(cwid: str) -> str:
    """STAGE# scope segment for a CWID-scoped rollup row.

    Yields PK `STAGE#rollup_by_cwid#cwid:{cwid}` — a partition distinct
    from the GLOBAL CSV path's `STAGE#rollup_by_cwid#GLOBAL`, so the two
    rollup modes never share a skip cache. Mirrors `score_publications`'
    `pmid:{pmid}` per-PMID scope convention.
    """
    return f"cwid:{cwid}"




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


# --- CWID-scoped (DynamoDB) rollup — #80 Phase 2 / #90 ---------------------
#
# D-ROLLUP-SRC (settled in PR 2): the per-CWID path reads scored data from
# DynamoDB — the CWID's TOPIC# activity rows via the FacultyIndex GSI — not
# the corpus-wide breakdown CSVs. The CSVs are a whole-corpus export
# regenerated by count_by_cwid.py; sourcing one CWID's onboarding rollup
# from them would couple it to a possibly-stale export. The live TOPIC#
# rows are the source of truth the Score/Assign stages just wrote.


def fetch_cwid_topic_activity(table: Any, cwid: str) -> list[dict]:
    """Return one CWID's TOPIC# activity rows from the FacultyIndex GSI.

    Queries `faculty_uid = cwid_{cwid}` with `PK begins_with TOPIC#` and
    projects each row down to the three fields the rollup needs:
    `topic_id` (from the PK), `pmid`, `primary_subtopic_id`. Rows whose SK
    is not an activity (`SCORE#...`) are dropped defensively — mirrors
    `compute_top_topic.fetch_activity_rows_for_pmid`. Paginates on
    `LastEvaluatedKey`.
    """
    faculty_uid = f"{_FACULTY_UID_PREFIX}{cwid}"
    rows: list[dict] = []
    last_key = None
    while True:
        kwargs: dict = {
            "IndexName": "FacultyIndex",
            "KeyConditionExpression": (
                Key("faculty_uid").eq(faculty_uid)
                & Key("PK").begins_with(_TOPIC_PK_PREFIX)
            ),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        for item in resp.get("Items", []):
            sk = item.get("SK", "")
            if not (isinstance(sk, str) and sk.startswith(_ACTIVITY_SK_PREFIX)):
                continue
            pk = item.get("PK", "")
            if not (isinstance(pk, str) and pk.startswith(_TOPIC_PK_PREFIX)):
                continue
            pmid = item.get("pmid")
            sub = item.get("primary_subtopic_id")
            rows.append({
                "topic_id": pk[len(_TOPIC_PK_PREFIX):],
                "pmid": str(pmid) if pmid not in (None, "") else None,
                "primary_subtopic_id": sub or None,
            })
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return rows


def _relevant_activity(
    activity_rows: Iterable[Mapping[str, Any]],
    input_pmid_set: Iterable[str],
) -> list[dict]:
    """The activity rows restricted to PMIDs in `input_pmid_set`.

    This is the *PMID-aware* step: a CWID can retain stale TOPIC# rows for
    PMIDs ReCiter has since de-attributed. The rollup counts only rows for
    PMIDs in the live accepted set, so de-attributed work does not inflate
    the tallies and the recorded `rollup_counts` stays consistent with the
    recorded `input_pmid_set`.
    """
    wanted = {str(p) for p in input_pmid_set}
    return [dict(r) for r in activity_rows if r.get("pmid") in wanted]


def compute_cwid_rollup(
    activity_rows: Iterable[Mapping[str, Any]],
    *,
    input_pmid_set: Iterable[str],
) -> dict[str, int]:
    """Compute the four per-CWID rollup counts (#80 Phase 2 / #90).

    Same four tallies as the GLOBAL CSV rollup (`ROLLUP_HEADER` minus
    `personIdentifier`), computed from the CWID's TOPIC# activity rows
    restricted to `input_pmid_set`:

    - `n_activities`           — count of TOPIC# activity rows
    - `n_distinct_topics`      — distinct `topic_id`
    - `n_subtopic_activities`  — rows carrying a `primary_subtopic_id`
    - `n_distinct_subtopics`   — distinct `primary_subtopic_id`

    One TOPIC# row is one (topic, pmid) activity for the CWID — matching
    `count_by_cwid.py`'s `+= 1`-per-row aggregation, so the counts mean
    exactly what the GLOBAL `cwid_rollup.csv` columns mean. Rows are not
    de-duplicated (neither does the CSV path).
    """
    rows = _relevant_activity(activity_rows, input_pmid_set)
    sub_rows = [r for r in rows if r.get("primary_subtopic_id")]
    return {
        "n_activities": len(rows),
        "n_distinct_topics": len({r["topic_id"] for r in rows}),
        "n_subtopic_activities": len(sub_rows),
        "n_distinct_subtopics": len(
            {r["primary_subtopic_id"] for r in sub_rows}
        ),
    }


def compute_cwid_rollup_input_hash(
    *,
    cwid: str,
    activity_rows: Iterable[Mapping[str, Any]],
    input_pmid_set: Iterable[str],
) -> str:
    """Content-addressed input_hash for a CWID-scoped rollup run.

    Addresses exactly what the rollup result depends on: the CWID, the
    live accepted PMID set, and a digest of the (topic, pmid, subtopic)
    triples of the PMID-aware activity rows. A re-run with an unchanged
    accepted set and unchanged scored activity skips; any change to the
    accepted set or the scored topics/subtopics re-runs. A stale TOPIC#
    row outside `input_pmid_set` changing does NOT re-trigger — it cannot
    affect the result.
    """
    rows = _relevant_activity(activity_rows, input_pmid_set)
    triples = sorted(
        (r["topic_id"], r.get("pmid") or "", r.get("primary_subtopic_id") or "")
        for r in rows
    )
    activity_digest = hashlib.sha256(
        json.dumps(triples, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return compute_input_hash(
        STAGE_NAME,
        {
            "cwid": str(cwid),
            "input_pmid_set": sorted({str(p) for p in input_pmid_set}),
            "activity_digest": activity_digest,
        },
    )


def run_cwid_rollup(
    cwid: str,
    *,
    stage_table: Any,
    emit_envelope: bool,
) -> int:
    """CWID-scoped, PMID-aware rollup (#80 Phase 2 / #90).

    Reads the CWID's live accepted PMID set (ReciterDB) and its TOPIC#
    activity rows (DynamoDB), computes the rollup over the PMID-aware
    intersection, and — unless an identical prior run is found — writes or
    emits a `STAGE#rollup_by_cwid#cwid:{cwid}` complete row carrying
    `input_pmid_set` and `rollup_counts`.

    `emit_envelope` True prints the STAGE# record as JSON on stdout (the
    hot-Lambda path); False writes it straight to DynamoDB. Returns a
    process exit code.
    """
    # Lazy import: the ReciterDB dependency belongs only to this path. The
    # GLOBAL CSV path (and its parity tests) stay free of a SQL import.
    from utils.sql_queries import get_pmids_for_cwid

    cwid = cwid.strip()
    if not cwid:
        raise SystemExit("--cwid requires a non-empty CWID")

    scope = _cwid_scope(cwid)
    stage_started_at = now_iso()
    t_stage_start = time.monotonic()

    # Live accepted-publication snapshot — this IS input_pmid_set (R9).
    input_pmid_set = sorted({str(p) for p in get_pmids_for_cwid(cwid)})
    # Scored TOPIC# activity for the CWID.
    activity_rows = fetch_cwid_topic_activity(stage_table, cwid)
    input_hash = compute_cwid_rollup_input_hash(
        cwid=cwid, activity_rows=activity_rows, input_pmid_set=input_pmid_set,
    )

    # --- STAGE# should_skip gate ---
    skip, prior = should_skip(
        stage_table, stage=STAGE_NAME, scope=scope, input_hash=input_hash,
    )
    if skip:
        duration_ms = int((time.monotonic() - t_stage_start) * 1000)
        skipped_kwargs = dict(
            stage=STAGE_NAME,
            scope=scope,
            input_hash=input_hash,
            skip_reason=(
                f"input_hash unchanged since prior complete run at "
                f"{prior.get('started_at', '?')}"
            ),
            started_at=stage_started_at,
            completed_at=now_iso(),
            duration_ms=duration_ms,
        )
        if emit_envelope:
            print(json.dumps(build_skipped_record(**skipped_kwargs), default=str))
        else:
            write_skipped(stage_table, **skipped_kwargs)
        print(
            f"[STAGE# skip] rollup_by_cwid cwid:{cwid} skipped — prior "
            f"complete at {prior.get('started_at', '?')} "
            f"(input_hash {input_hash[:12]})"
        )
        return 0

    # --- Compute rollup ---
    counts = compute_cwid_rollup(activity_rows, input_pmid_set=input_pmid_set)
    print(
        f"[cwid rollup] {cwid}: n_activities={counts['n_activities']} "
        f"n_distinct_topics={counts['n_distinct_topics']} "
        f"n_subtopic_activities={counts['n_subtopic_activities']} "
        f"n_distinct_subtopics={counts['n_distinct_subtopics']} "
        f"(input_pmid_set: {len(input_pmid_set)} accepted PMIDs)"
    )

    # --- STAGE# complete row ---
    completed_at = now_iso()
    duration_ms = int((time.monotonic() - t_stage_start) * 1000)
    complete_kwargs = dict(
        stage=STAGE_NAME,
        scope=scope,
        input_hash=input_hash,
        started_at=stage_started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        cost_observed_usd=ROLLUP_COST_USD,
        records_written=1,
        input_pmid_set=input_pmid_set,
        rollup_counts=counts,
    )
    if emit_envelope:
        print(json.dumps(build_complete_record(**complete_kwargs), default=str))
    else:
        write_complete(stage_table, **complete_kwargs)
    return 0


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
        "--cwid", default=None, metavar="CWID",
        help=(
            "#80 Phase 2 / #90 CWID-scoped mode. A single (bare) CWID. "
            "Reads the CWID's live accepted PMID set (ReciterDB) and its "
            "TOPIC# activity rows (DynamoDB), and writes a "
            "STAGE#rollup_by_cwid#cwid:{cwid} row with input_pmid_set + "
            "rollup_counts. Ignores --topic-csv/--subtopic-csv/--out; "
            "mutually exclusive with --cwids."
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

    # --- #80 Phase 2 / #90: CWID-scoped rollup mode ---
    if args.cwid:
        if args.cwids:
            parser.error(
                "--cwid (CWID-scoped onboarding rollup) and --cwids (CSV "
                "incremental mode) are mutually exclusive"
            )
        if args.skip_stage_write:
            parser.error(
                "--skip-stage-write is not supported with --cwid: the "
                "CWID-scoped rollup reads live data and the STAGE# row is "
                "its only output"
            )
        return run_cwid_rollup(
            args.cwid,
            stage_table=get_table(TABLE_NAME),
            emit_envelope=args.emit_envelope,
        )

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
