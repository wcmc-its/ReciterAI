"""Scope-parity lock for the #222 attribution-decrease cull.

The cull (cli/reconcile_attribution.py, not yet built) will delete stored
``TOPIC#``/``SCORE#`` rows whose ``(faculty_uid, pmid)`` pair is absent from a
freshly-computed *live set*. That is only safe when the cull derives its live
set at the SAME author-position scope the rows were originally MINTED under.

Probe verdict (#222, high confidence, adversarially verified): the TOPIC# mint
path applies NO author-position filter. ``AUTHOR_MAPPING_SQL``
(utils/sql_queries.py) filters only on ``fullTimeFaculty='yes'`` and returns
first/last/middle/NULL authors; ``build_topic_rows_for_pmid`` mints one row per
(qualifying topic) x (every author CWID), reading only ``author['cwid']`` and
never ``author['position']``. Position is in neither the PK nor the SK, so row
identity is ``(topic_id, score, pmid, cwid)``.

Consequence: the cull MUST compute its live set from the raw, all-positions
author mapping (``extract_author_mapping`` output, no first/last re-scope). A
first/last-scoped live set would classify every middle-author TOPIC# row as
stale and mass-delete on a perfectly healthy read.

These tests fail loudly if anyone later (a) adds a first/last filter to the
mint path without re-scoping the cull's live set in lockstep, or (b) makes
author position part of TOPIC# row identity. The comments at
sql_queries.py:121-123 / :275 claiming the load step "uses first/last for v1"
are STALE/aspirational, contradicted by the code these tests pin.
"""

from __future__ import annotations

from utils.topic_records import build_topic_rows_for_pmid

# A PMID with faculty authors at every position the live SQL can return:
# first, last, middle, and NULL (stored as "" by extract_author_mapping;
# the schema treats NULL position as a middle author).
_ALL_POSITION_AUTHORS = [
    {"cwid": "first01", "position": "first"},
    {"cwid": "mid0001", "position": "middle"},
    {"cwid": "last001", "position": "last"},
    {"cwid": "null0001", "position": ""},  # NULL authorPosition == middle author
]


def _topic_rows(authors):
    return build_topic_rows_for_pmid(
        pmid="900001",
        dense_scores={"cardio": {"score": 0.9}},
        authors=authors,
        taxonomy_version="taxonomy_v2",
        min_score=0.3,
    )


def test_mint_includes_every_author_position_not_just_first_last():
    """Mint scope == ALL faculty author positions. One TOPIC# row per CWID,
    regardless of first/last/middle/NULL. This is the scope the cull's live
    set must match."""
    rows = _topic_rows(_ALL_POSITION_AUTHORS)
    minted_cwids = {r["faculty_uid"]["S"] for r in rows}
    assert minted_cwids == {
        "cwid_first01",
        "cwid_mid0001",
        "cwid_last001",
        "cwid_null0001",
    }
    # One row per author x the single qualifying topic — nothing dropped.
    assert len(rows) == len(_ALL_POSITION_AUTHORS)


def test_middle_author_row_is_minted_so_first_last_cull_would_overdelete():
    """The load-bearing failure mode: a middle-author TOPIC# row physically
    exists. A live set scoped to first/last would not contain its
    (faculty_uid, pmid) pair and would mark it stale -> mass-delete on a
    healthy read. Pinning the row's existence makes that regression loud."""
    rows = _topic_rows(_ALL_POSITION_AUTHORS)
    middle_pairs = {
        (r["faculty_uid"]["S"], r["pmid"]["S"])
        for r in rows
        if r["faculty_uid"]["S"] in {"cwid_mid0001", "cwid_null0001"}
    }
    assert middle_pairs == {
        ("cwid_mid0001", "900001"),
        ("cwid_null0001", "900001"),
    }


def test_author_position_is_not_part_of_topic_row_identity():
    """Row identity is (topic_id, score, pmid, cwid). The same CWID at two
    different positions collapses to ONE row (dedup on PK+SK), and position
    appears in neither key nor as an attribute — so the cull cannot and must
    not key on position."""
    rows = _topic_rows(
        [
            {"cwid": "dup0001", "position": "first"},
            {"cwid": "dup0001", "position": "middle"},
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    # Position must not leak into PK, SK, or any attribute.
    assert "position" not in row
    assert "position" not in row["PK"]["S"]
    assert "position" not in row["SK"]["S"]
    # Identity carries the CWID, never the position.
    assert row["SK"]["S"].endswith("#cwid_dup0001")


def test_mint_does_not_filter_on_position_value():
    """Equivalence guard: the rows minted for an all-positions author list
    equal those for the same CWIDs relabelled all-first. If the mint ever
    started filtering by position, these two sets would diverge — and the
    cull would need to re-scope in lockstep."""
    all_positions = _topic_rows(_ALL_POSITION_AUTHORS)
    same_cwids_all_first = _topic_rows(
        [{"cwid": a["cwid"], "position": "first"} for a in _ALL_POSITION_AUTHORS]
    )
    keys = lambda rows: {(r["PK"]["S"], r["SK"]["S"]) for r in rows}
    assert keys(all_positions) == keys(same_cwids_all_first)
