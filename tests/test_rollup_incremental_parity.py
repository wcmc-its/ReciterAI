"""Phase 10 T5 — parity test: incremental rollup == full rollup.

Highest-risk gate per the slip checkpoint (D-08). The contract:

    full_rollup(N) == incremental_rollup(dirty_subset) merged with
                     prior_rollup(N \\ dirty_subset)

is enforced byte-identically on the CSV outputs. A regression here
blocks the wave.

Construction:
- A synthetic universe of N CWIDs across 2 topics + 3 subtopics, with
  varying per-CWID activity counts (and deliberate ties on
  n_activities to exercise the secondary sort key).
- We split CWIDs into `dirty` and `undirty` subsets.
- Build a "prior" rollup by running the full rollup on the undirty
  subset only (simulating "what was on disk before today's run").
- Build a "today's full" rollup on the full N (the source-of-truth
  answer the incremental run must match).
- Run `incremental_rollup(dirty)` against the prior; merge.
- Assert the two CSV files byte-equal.

Phase 12 D-13 tests (appended below):
- test_reader_falls_back_to_legacy_name
- test_reader_prefers_new_name_when_both_exist
- test_writer_emits_three_files
- test_legacy_and_new_exclusive_have_identical_content
- test_inclusive_csv_distinct_from_exclusive
"""

from __future__ import annotations

import csv
import logging
import shutil
from pathlib import Path

import pytest

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


def _filter_rows(rows, keep_cwids):
    return [r for r in rows if r[0] in keep_cwids]


# --- Tests -----------------------------------------------------------------


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


def test_parity_incremental_eq_full_with_byte_identical_csvs(tmp_path: Path):
    """The headline parity gate. Byte-identical CSVs."""
    full_dir = tmp_path / "full"
    inc_dir = tmp_path / "incremental"
    full_dir.mkdir()
    inc_dir.mkdir()

    # 1) Build the "today's full" answer.
    full_topic_csv, full_subtopic_csv = _write_breakdown_csvs(
        full_dir, TOPIC_ROWS, SUBTOPIC_ROWS
    )
    full_rollup = rbc.full_rollup(full_topic_csv, full_subtopic_csv)
    full_out = full_dir / "cwid_rollup.csv"
    rbc.write_rollup_csv(full_out, full_rollup)

    # 2) Simulate yesterday's state: a "prior" rollup excluding the dirty subset.
    dirty = {"bob", "carol", "erin"}
    undirty = set(full_rollup.keys()) - dirty
    prior_topic = _filter_rows(TOPIC_ROWS, undirty)
    prior_sub = _filter_rows(SUBTOPIC_ROWS, undirty)
    prior_dir = tmp_path / "prior"
    prior_dir.mkdir()
    prior_topic_csv, prior_subtopic_csv = _write_breakdown_csvs(
        prior_dir, prior_topic, prior_sub
    )
    prior_rollup = rbc.full_rollup(prior_topic_csv, prior_subtopic_csv)

    # Sanity: the prior covers undirty only.
    assert set(prior_rollup.keys()) == undirty

    # 3) Today the breakdown CSVs contain ALL rows (including dirty), but we
    #    only want to recompute the dirty subset and merge.
    inc_topic_csv, inc_subtopic_csv = _write_breakdown_csvs(
        inc_dir, TOPIC_ROWS, SUBTOPIC_ROWS
    )
    merged = rbc.incremental_rollup(
        inc_topic_csv,
        inc_subtopic_csv,
        dirty_cwids=dirty,
        prior_rollup=prior_rollup,
    )
    inc_out = inc_dir / "cwid_rollup.csv"
    rbc.write_rollup_csv(inc_out, merged)

    # Equality on the in-memory rollup dict
    assert merged == full_rollup

    # Byte-identical CSVs (the headline contract)
    assert full_out.read_bytes() == inc_out.read_bytes()


def test_parity_dirty_set_with_disappearing_cwid(tmp_path: Path):
    """If a dirty CWID no longer has any rows in today's breakdowns, the
    incremental rollup must remove it — matching a full rollup whose
    breakdowns also lack that CWID."""
    # Prior includes 'dave'. Today's breakdowns drop dave entirely.
    today_topic_rows = [r for r in TOPIC_ROWS if r[0] != "dave"]
    today_sub_rows = [r for r in SUBTOPIC_ROWS if r[0] != "dave"]

    today_dir = tmp_path / "today"
    prior_dir = tmp_path / "prior"
    today_dir.mkdir()
    prior_dir.mkdir()

    today_topic_csv, today_subtopic_csv = _write_breakdown_csvs(
        today_dir, today_topic_rows, today_sub_rows
    )
    # Prior: yesterday's full rollup over the original universe (includes dave)
    prior_topic_csv, prior_subtopic_csv = _write_breakdown_csvs(
        prior_dir, TOPIC_ROWS, SUBTOPIC_ROWS
    )
    prior_rollup = rbc.full_rollup(prior_topic_csv, prior_subtopic_csv)
    assert "dave" in prior_rollup

    merged = rbc.incremental_rollup(
        today_topic_csv,
        today_subtopic_csv,
        dirty_cwids={"dave"},
        prior_rollup=prior_rollup,
    )

    # dave dropped out
    assert "dave" not in merged
    # Other CWIDs unchanged (prior values carry over)
    expected_full = rbc.full_rollup(today_topic_csv, today_subtopic_csv)
    assert merged == expected_full


def test_parity_empty_dirty_set_returns_prior_unchanged(tmp_path: Path):
    topic_csv, subtopic_csv = _write_breakdown_csvs(tmp_path, TOPIC_ROWS, SUBTOPIC_ROWS)
    prior = rbc.full_rollup(topic_csv, subtopic_csv)
    merged = rbc.incremental_rollup(
        topic_csv,
        subtopic_csv,
        dirty_cwids=set(),
        prior_rollup=prior,
    )
    assert merged == prior


# --- input_hash --------------------------------------------------------------


def test_input_hash_full_vs_incremental_differ(tmp_path: Path):
    topic_csv, subtopic_csv = _write_breakdown_csvs(tmp_path, TOPIC_ROWS, SUBTOPIC_ROWS)
    h_full = rbc.compute_rollup_input_hash(
        topic_csv=topic_csv, subtopic_csv=subtopic_csv, cwids=None
    )
    h_inc_empty = rbc.compute_rollup_input_hash(
        topic_csv=topic_csv, subtopic_csv=subtopic_csv, cwids=[]
    )
    h_inc_bob = rbc.compute_rollup_input_hash(
        topic_csv=topic_csv, subtopic_csv=subtopic_csv, cwids=["bob"]
    )
    assert h_full != h_inc_empty
    assert h_inc_empty != h_inc_bob
    # Determinism
    assert h_inc_bob == rbc.compute_rollup_input_hash(
        topic_csv=topic_csv, subtopic_csv=subtopic_csv, cwids=["bob"]
    )


def test_input_hash_changes_when_breakdown_csv_changes(tmp_path: Path):
    topic_csv, subtopic_csv = _write_breakdown_csvs(tmp_path, TOPIC_ROWS, SUBTOPIC_ROWS)
    h1 = rbc.compute_rollup_input_hash(
        topic_csv=topic_csv, subtopic_csv=subtopic_csv, cwids=None
    )
    # Mutate the topic CSV: drop one row.
    _write_breakdown_csvs(tmp_path, TOPIC_ROWS[:-1], SUBTOPIC_ROWS)
    h2 = rbc.compute_rollup_input_hash(
        topic_csv=topic_csv, subtopic_csv=subtopic_csv, cwids=None
    )
    assert h1 != h2


# --- CLI parser --------------------------------------------------------------


def test_parse_cwid_list_inline_and_file(tmp_path: Path):
    assert rbc._parse_cwid_list_arg("a,b,c") == ["a", "b", "c"]
    assert rbc._parse_cwid_list_arg(None) is None
    f = tmp_path / "cwids.txt"
    f.write_text("# header\nbob\ncarol\n\n")
    assert rbc._parse_cwid_list_arg(f"@{f}") == ["bob", "carol"]
    with pytest.raises(SystemExit):
        rbc._parse_cwid_list_arg(f"@{tmp_path}/missing.txt")


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


def test_cli_incremental_mode_matches_full(tmp_path: Path):
    """End-to-end CLI parity: running full then incremental over a dirty
    subset produces the same final CSV bytes."""
    full_dir = tmp_path / "full"
    inc_dir = tmp_path / "inc"
    full_dir.mkdir()
    inc_dir.mkdir()

    # Full run
    full_topic, full_sub = _write_breakdown_csvs(full_dir, TOPIC_ROWS, SUBTOPIC_ROWS)
    full_out = full_dir / "cwid_rollup.csv"
    rc = rbc.main([
        "--topic-csv", str(full_topic),
        "--subtopic-csv", str(full_sub),
        "--out", str(full_out),
        "--skip-stage-write",
    ])
    assert rc == 0

    # Set up the incremental run: seed prior from an undirty-only full run.
    dirty = ["bob", "erin"]
    undirty = [c for c in {r[0] for r in TOPIC_ROWS} | {r[0] for r in SUBTOPIC_ROWS}
               if c not in dirty]
    prior_dir = tmp_path / "prior"
    prior_dir.mkdir()
    prior_topic, prior_sub = _write_breakdown_csvs(
        prior_dir,
        _filter_rows(TOPIC_ROWS, set(undirty)),
        _filter_rows(SUBTOPIC_ROWS, set(undirty)),
    )
    prior_out = inc_dir / "cwid_rollup.csv"
    rc = rbc.main([
        "--topic-csv", str(prior_topic),
        "--subtopic-csv", str(prior_sub),
        "--out", str(prior_out),
        "--skip-stage-write",
    ])
    assert rc == 0

    # Now run incremental on full breakdowns, merging into the prior.
    inc_topic, inc_sub = _write_breakdown_csvs(inc_dir, TOPIC_ROWS, SUBTOPIC_ROWS)
    rc = rbc.main([
        "--topic-csv", str(inc_topic),
        "--subtopic-csv", str(inc_sub),
        "--out", str(prior_out),
        "--cwids", ",".join(dirty),
        "--skip-stage-write",
    ])
    assert rc == 0

    # Headline assertion: byte-identical to the full-run output.
    assert full_out.read_bytes() == prior_out.read_bytes()


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
