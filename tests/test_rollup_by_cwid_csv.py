"""CSV-path tests for `rollup_by_cwid` — the GLOBAL full-corpus rollup.

Covers breakdown-CSV aggregation (`full_rollup`), the deterministic sort,
`compute_rollup_input_hash`, the full-mode CLI, and the Phase 12 D-13
subtopic-CSV rename + dual-write.

The per-CWID DynamoDB rollup (`--cwid`, #80 Phase 2 / #90) is covered by
`test_rollup_cwid_scoped.py`.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import rollup_by_cwid as rbc


# --- Synthetic fixtures ----------------------------------------------------

# Universe: 7 CWIDs, 2 topics, 3 subtopics. Some CWIDs share n_activities
# values so the secondary sort key (cwid asc) gets exercised.
TOPIC_ROWS = [
    # (personIdentifier, topic_id, n_activities)
    ("alice",   "cardio", 5),
    ("alice",   "neuro",  3),
    ("bob",     "cardio", 8),
    ("carol",   "cardio", 4),
    ("carol",   "neuro",  4),  # n_activities = 8 (ties with bob)
    ("dave",    "neuro",  2),
    ("erin",    "cardio", 8),  # ties with bob/carol
    ("frank",   "neuro",  1),
    ("grace",   "cardio", 5),  # ties with alice on activity count
]

SUBTOPIC_ROWS = [
    # (personIdentifier, primary_subtopic_id, n_activities)
    ("alice", "afib",     2),
    ("alice", "stroke",   1),
    ("bob",   "afib",     4),
    ("carol", "afib",     3),
    ("carol", "alzhei",   2),
    ("dave",  "alzhei",   2),
    ("erin",  "afib",     6),
    ("frank", "stroke",   1),
    ("grace", "afib",     5),
]


def _write_breakdown_csvs(dest_dir: Path, rows_topic, rows_sub) -> tuple[Path, Path]:
    topic_csv = dest_dir / "cwid_topic_counts.csv"
    subtopic_csv = dest_dir / "faculty_subtopic_counts_exclusive.csv"
    with open(topic_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["personIdentifier", "topic_id", "n_activities"])
        for r in rows_topic:
            w.writerow(r)
    with open(subtopic_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["personIdentifier", "primary_subtopic_id", "n_activities"])
        for r in rows_sub:
            w.writerow(r)
    return topic_csv, subtopic_csv


# --- full_rollup + deterministic sort --------------------------------------


def test_full_rollup_basic_shape(tmp_path: Path):
    topic_csv, subtopic_csv = _write_breakdown_csvs(tmp_path, TOPIC_ROWS, SUBTOPIC_ROWS)
    rollup = rbc.full_rollup(topic_csv, subtopic_csv)
    # All CWIDs present
    assert set(rollup.keys()) == {
        "alice", "bob", "carol", "dave", "erin", "frank", "grace"
    }
    # Spot-check known values
    # alice: topic counts (5 cardio + 3 neuro = 8 acts, 2 distinct topics)
    #        subtopic counts (2 afib + 1 stroke = 3 sub_acts, 2 distinct subs)
    assert rollup["alice"] == (8, 2, 3, 2)
    # bob: 8 cardio activities, 1 distinct topic; 4 afib, 1 distinct subtopic
    assert rollup["bob"] == (8, 1, 4, 1)


def test_deterministic_sort_breaks_ties_by_cwid_asc(tmp_path: Path):
    """alice, bob, carol, erin, grace all reach 8 acts in different ways; sort must
    be deterministic for byte-identical outputs."""
    topic_csv, subtopic_csv = _write_breakdown_csvs(tmp_path, TOPIC_ROWS, SUBTOPIC_ROWS)
    rollup = rbc.full_rollup(topic_csv, subtopic_csv)
    rows = rbc.sort_rollup_rows(rollup)
    # Confirm tied-on-n_activities CWIDs are sorted alphabetically among themselves
    by_acts: dict[int, list[str]] = {}
    for r in rows:
        by_acts.setdefault(r[1], []).append(r[0])
    for acts, cwids in by_acts.items():
        assert cwids == sorted(cwids), f"non-deterministic order for n_activities={acts}: {cwids}"


# --- input_hash ------------------------------------------------------------


def test_input_hash_changes_when_breakdown_csv_changes(tmp_path: Path):
    topic_csv, subtopic_csv = _write_breakdown_csvs(tmp_path, TOPIC_ROWS, SUBTOPIC_ROWS)
    h1 = rbc.compute_rollup_input_hash(topic_csv=topic_csv, subtopic_csv=subtopic_csv)
    # Mutate the topic CSV: drop one row.
    _write_breakdown_csvs(tmp_path, TOPIC_ROWS[:-1], SUBTOPIC_ROWS)
    h2 = rbc.compute_rollup_input_hash(topic_csv=topic_csv, subtopic_csv=subtopic_csv)
    assert h1 != h2
    # Determinism: an unchanged input yields the same hash.
    assert h2 == rbc.compute_rollup_input_hash(
        topic_csv=topic_csv, subtopic_csv=subtopic_csv
    )


# --- CLI smoke test (--skip-stage-write keeps us off AWS) -------------------


def test_cli_full_mode_writes_expected_csv(tmp_path: Path, monkeypatch):
    topic_csv, subtopic_csv = _write_breakdown_csvs(tmp_path, TOPIC_ROWS, SUBTOPIC_ROWS)
    out = tmp_path / "cwid_rollup.csv"
    rc = rbc.main([
        "--topic-csv", str(topic_csv),
        "--subtopic-csv", str(subtopic_csv),
        "--out", str(out),
        "--skip-stage-write",
    ])
    assert rc == 0
    assert out.exists()
    rollup = rbc.read_rollup_csv(out)
    assert rollup["alice"] == (8, 2, 3, 2)


# --- Phase 12 D-13 CSV rename + dual-write tests ----------------------------


def test_reader_falls_back_to_legacy_name(tmp_path: Path, monkeypatch, caplog):
    """rollup_by_cwid reads the legacy cwid_subtopic_counts.csv when the new
    canonical name is absent, and emits a deprecation warning.
    Phase 12 D-13 safe-path default.
    """
    monkeypatch.chdir(tmp_path)
    # Write only the old-name file; the new name does NOT exist.
    legacy = tmp_path / "cwid_subtopic_counts.csv"
    topic_f = tmp_path / "cwid_topic_counts.csv"
    with open(topic_f, "w", newline="") as f:
        csv.writer(f).writerows(
            [["personIdentifier", "topic_id", "n_activities"]] + list(TOPIC_ROWS)
        )
    with open(legacy, "w", newline="") as f:
        csv.writer(f).writerows(
            [["personIdentifier", "primary_subtopic_id", "n_activities"]] + list(SUBTOPIC_ROWS)
        )

    # _resolve_subtopic_csv must fall back to legacy and warn.
    resolved = rbc._resolve_subtopic_csv()
    assert resolved == rbc.LEGACY_SUBTOPIC_CSV

    # Loading from the resolved path must succeed and include the deprecation warning.
    with caplog.at_level(logging.WARNING, logger="rollup_by_cwid"):
        rbc._resolve_subtopic_csv()
    assert any("deprecat" in r.message.lower() or "legacy" in r.message.lower()
                for r in caplog.records), \
        "Expected a deprecation/legacy warning from _resolve_subtopic_csv"


def test_reader_prefers_new_name_when_both_exist(tmp_path: Path, monkeypatch, caplog):
    """When both old and new names exist, rollup_by_cwid prefers the new canonical
    name (faculty_subtopic_counts_exclusive.csv) and does NOT emit a deprecation warning.
    """
    monkeypatch.chdir(tmp_path)
    # Write new name with one content, legacy with different content.
    new_file = tmp_path / "faculty_subtopic_counts_exclusive.csv"
    legacy = tmp_path / "cwid_subtopic_counts.csv"
    with open(new_file, "w", newline="") as f:
        csv.writer(f).writerows(
            [["personIdentifier", "primary_subtopic_id", "n_activities"],
             ["alice", "afib", 42]]
        )
    with open(legacy, "w", newline="") as f:
        csv.writer(f).writerows(
            [["personIdentifier", "primary_subtopic_id", "n_activities"],
             ["alice", "afib", 999]]
        )

    with caplog.at_level(logging.WARNING, logger="rollup_by_cwid"):
        resolved = rbc._resolve_subtopic_csv()

    assert resolved == rbc.DEFAULT_SUBTOPIC_CSV
    # No deprecation warning when the new name is present.
    assert not any("deprecat" in r.message.lower() or "legacy" in r.message.lower()
                   for r in caplog.records), \
        "Should NOT emit deprecation warning when new canonical name exists"


def test_writer_emits_three_files(tmp_path: Path, monkeypatch):
    """count_by_cwid.write_subtopic_csvs emits all three CSV files:
    - faculty_subtopic_counts_exclusive.csv  (new canonical)
    - faculty_subtopic_counts_inclusive.csv  (new inclusive)
    - cwid_subtopic_counts.csv              (legacy dual-write, Phase 12 D-13)
    """
    import count_by_cwid as cbc

    # Exclusive counts: (cwid, subtopic_id) -> n
    exclusive_counts = {("alice", "afib"): 2, ("bob", "stroke"): 3}
    # Inclusive counts: same shape but with additional secondary entries
    inclusive_counts = {
        ("alice", "afib"): 2,
        ("alice", "stroke"): 1,   # secondary assignment
        ("bob", "stroke"): 3,
    }

    monkeypatch.chdir(tmp_path)
    cbc.write_subtopic_csvs(exclusive_counts, inclusive_counts, dest_dir=tmp_path)

    assert (tmp_path / "faculty_subtopic_counts_exclusive.csv").exists(), \
        "faculty_subtopic_counts_exclusive.csv must exist"
    assert (tmp_path / "faculty_subtopic_counts_inclusive.csv").exists(), \
        "faculty_subtopic_counts_inclusive.csv must exist"
    assert (tmp_path / "cwid_subtopic_counts.csv").exists(), \
        "cwid_subtopic_counts.csv (legacy dual-write) must exist"


def test_legacy_and_new_exclusive_have_identical_content(tmp_path: Path, monkeypatch):
    """The legacy cwid_subtopic_counts.csv is byte-identical to
    faculty_subtopic_counts_exclusive.csv (the legacy file is a copy per D-13).
    """
    import count_by_cwid as cbc

    exclusive_counts = {
        ("alice", "afib"): 5,
        ("bob", "stroke"): 3,
        ("carol", "alzhei"): 1,
    }
    inclusive_counts = {
        ("alice", "afib"): 5,
        ("alice", "stroke"): 2,
        ("bob", "stroke"): 3,
        ("carol", "alzhei"): 1,
    }

    monkeypatch.chdir(tmp_path)
    cbc.write_subtopic_csvs(exclusive_counts, inclusive_counts, dest_dir=tmp_path)

    exclusive_bytes = (tmp_path / "faculty_subtopic_counts_exclusive.csv").read_bytes()
    legacy_bytes = (tmp_path / "cwid_subtopic_counts.csv").read_bytes()
    assert exclusive_bytes == legacy_bytes, \
        "Legacy cwid_subtopic_counts.csv must be byte-identical to faculty_subtopic_counts_exclusive.csv"


def test_inclusive_csv_distinct_from_exclusive(tmp_path: Path, monkeypatch):
    """When at least one CWID has secondary subtopic assignments
    (inclusive_counts > exclusive_counts for that subtopic), the inclusive
    CSV must differ from the exclusive CSV.
    """
    import count_by_cwid as cbc

    # alice has a secondary assignment (stroke) that doesn't appear in exclusive.
    exclusive_counts = {("alice", "afib"): 5, ("bob", "stroke"): 3}
    inclusive_counts = {
        ("alice", "afib"): 5,
        ("alice", "stroke"): 2,   # secondary — inclusive > exclusive for this row
        ("bob", "stroke"): 3,
    }

    monkeypatch.chdir(tmp_path)
    cbc.write_subtopic_csvs(exclusive_counts, inclusive_counts, dest_dir=tmp_path)

    excl_bytes = (tmp_path / "faculty_subtopic_counts_exclusive.csv").read_bytes()
    incl_bytes = (tmp_path / "faculty_subtopic_counts_inclusive.csv").read_bytes()
    assert excl_bytes != incl_bytes, \
        "Inclusive CSV must differ from exclusive CSV when secondary assignments exist"
