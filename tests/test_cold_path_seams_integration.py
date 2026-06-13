"""Issue #13 — Test B: cross-stage data-contract seams in the cold path.

## What this defends

Stage N's output shape must remain consumable by stage N+1's reader. If
stage N renames a CSV column or a DDB attribute, stage N+1 silently produces
empty / malformed output rather than raising — exactly the failure mode
Phase 11 UAT-3 surfaced (e.g. bug #5: `count_by_cwid` stage missing before
`rollup`, where rollup silently produced an empty rollup against absent
CSVs).

Companion to Test A (`test_cold_run_command_lines_integration.py`):
- **Test A** asserts each ColdStage's command parses against its target
  script's argparser (CLI-flag drift).
- **Test B** (this file) asserts each producer/consumer pair shares a
  compatible data shape (schema drift between stages).

## Scope: two genuinely-uncovered seams

After surveying existing test coverage:
- `aggregate → publish`: covered by `test_cold_path_e2e.py`
- `hierarchy_augmented → bundler → hierarchy.json`: covered by
  `test_hierarchy_bundler.py` (10 tests)
- `SCORE# → assign`: covered by `test_assign_subtopics_stage.py`
- `aggregate → SUBTOPIC_SCORE# partitions`: covered by
  `test_cold_path_e2e.py::test_cold_path_emits_both_subtopic_partitions`

The two uncovered seams worth defending here:

1. **count_by_cwid CSV columns → rollup_by_cwid CSV reader**
   `count_by_cwid` writes three CSVs (topic + exclusive subtopic +
   inclusive subtopic) with specific column headers. `rollup_by_cwid`
   reads two of them by column name (`personIdentifier`, `topic_id`,
   `n_activities`, `primary_subtopic_id`). A rename on either side
   produces a silently-empty rollup, not a hard error.

2. **assign_subtopics TOPIC# rows → pool_ranker.rank_pool reader**
   `assign_subtopics` writes TOPIC# rows to DynamoDB with ~13 attributes
   (`pmid`, `year`, `impact_score`, `primary_subtopic_id`,
   `first_author_person_identifier`, etc.). `spotlight.pool_ranker.rank_pool`
   reads them via `client.get_paginator("scan")` and extracts Papers
   field-by-field. A field rename on the assign side causes papers to
   drop (`pmid` missing → None) or land with empty authors — the cold-run
   "succeeds" but the spotlight pool is empty/malformed.

## Honest limit

This test does not exercise the *producer* path — we don't invoke
`count_by_cwid.write_subtopic_csvs` or `assign_subtopics.run` directly,
because those carry their own RDS / Bedrock dependencies. Instead the
producer-side shape is captured as a fixture inside this test file
(headers + low-level DDB item structure) and changes when the producer
changes. That captured shape is documented inline so a future producer
change forces a deliberate fixture update — making the contract drift
visible at code-review time rather than at production-runtime.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

import rollup_by_cwid
from spotlight.pool_ranker import rank_pool
from utils.scoring import article_score


# ===========================================================================
# Seam 1: count_by_cwid CSV columns → rollup_by_cwid CSV reader
# ===========================================================================

# Captured from count_by_cwid.py (write_subtopic_csvs at line 61–71 and the
# topic CSV writer at line 152–155). Update if count_by_cwid changes its
# output headers — that change should be deliberate and reviewed.
_COUNT_TOPIC_HEADER = ["personIdentifier", "topic_id", "n_activities"]
_COUNT_SUBTOPIC_EXCLUSIVE_HEADER = ["personIdentifier", "primary_subtopic_id", "n_activities"]
_COUNT_SUBTOPIC_INCLUSIVE_HEADER = ["personIdentifier", "subtopic_id", "n_activities"]


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)


def test_count_topic_csv_columns_consumed_by_rollup(tmp_path: Path):
    """The exact column headers `count_by_cwid` writes for cwid_topic_counts.csv
    must be the columns `rollup_by_cwid.aggregate_from_breakdowns` reads."""
    topic_csv = tmp_path / "cwid_topic_counts.csv"
    subtopic_csv = tmp_path / "faculty_subtopic_counts_exclusive.csv"

    _write_csv(topic_csv, _COUNT_TOPIC_HEADER, [
        ["alice", "topic_alpha", "10"],
        ["alice", "topic_beta", "5"],
        ["bob", "topic_alpha", "3"],
    ])
    _write_csv(subtopic_csv, _COUNT_SUBTOPIC_EXCLUSIVE_HEADER, [
        ["alice", "topic_alpha_sub1", "6"],
        ["alice", "topic_alpha_sub2", "4"],
        ["bob", "topic_alpha_sub1", "3"],
    ])

    rollup = rollup_by_cwid.aggregate_from_breakdowns(topic_csv, subtopic_csv)

    # rollup dict shape: {cwid: (n_activities, n_distinct_topics,
    #                             n_subtopic_activities, n_distinct_subtopics)}
    assert "alice" in rollup
    assert "bob" in rollup
    alice = rollup["alice"]
    assert alice == (15, 2, 10, 2), (
        f"Expected alice rollup (n_acts=15, topics=2, sub_acts=10, subs=2); "
        f"got {alice}. If this fails after a count_by_cwid change, the "
        f"_COUNT_TOPIC_HEADER or _COUNT_SUBTOPIC_EXCLUSIVE_HEADER fixture "
        f"is out of date OR rollup_by_cwid.aggregate_from_breakdowns no "
        f"longer reads the headers count_by_cwid writes."
    )


def test_count_inclusive_subtopic_csv_columns_consumed_by_rollup(tmp_path: Path):
    """`faculty_subtopic_counts_inclusive.csv` uses `subtopic_id` (not
    `primary_subtopic_id`) — verify rollup's column-resolution layer
    (`_pick_subtopic_id_column`) accepts that variant.
    """
    topic_csv = tmp_path / "cwid_topic_counts.csv"
    subtopic_csv = tmp_path / "faculty_subtopic_counts_inclusive.csv"

    _write_csv(topic_csv, _COUNT_TOPIC_HEADER, [
        ["alice", "topic_alpha", "10"],
    ])
    _write_csv(subtopic_csv, _COUNT_SUBTOPIC_INCLUSIVE_HEADER, [
        ["alice", "topic_alpha_sub1", "7"],
        ["alice", "topic_alpha_sub2", "4"],
    ])

    rollup = rollup_by_cwid.aggregate_from_breakdowns(topic_csv, subtopic_csv)
    alice = rollup["alice"]
    assert alice == (10, 1, 11, 2), (
        f"Inclusive subtopic header contract broken; got {alice}."
    )


def test_count_csv_with_renamed_column_breaks_rollup_loudly(tmp_path: Path):
    """Sanity check: if `n_activities` is renamed to anything else on the
    producer side, rollup's reader raises (does not silently produce 0
    activities). This is the failure mode we WANT — if it ever becomes
    silent-zero, the contract is degraded and Test B's first two cases
    would pass on bad data."""
    topic_csv = tmp_path / "cwid_topic_counts.csv"
    subtopic_csv = tmp_path / "faculty_subtopic_counts_exclusive.csv"

    _write_csv(topic_csv, ["personIdentifier", "topic_id", "count"], [
        ["alice", "topic_alpha", "10"],
    ])
    _write_csv(subtopic_csv, _COUNT_SUBTOPIC_EXCLUSIVE_HEADER, [
        ["alice", "topic_alpha_sub1", "6"],
    ])

    with pytest.raises(KeyError, match="n_activities"):
        rollup_by_cwid.aggregate_from_breakdowns(topic_csv, subtopic_csv)


# ===========================================================================
# Seam 2: assign_subtopics TOPIC# rows → pool_ranker.rank_pool reader
# ===========================================================================

# Captured from spotlight/pool_ranker.py:_extract_paper (lines 108–161) and
# the cutoff-year + impact-score gates in rank_pool (lines 209, 227–239).
# This is the DDB low-level item format pool_ranker expects to read.
# When assign_subtopics changes the attributes it writes on a TOPIC# row,
# this fixture must be updated — that update is the seam contract.


def _ddb_topic_item(
    *,
    pmid: str,
    primary_subtopic_id: str,
    year: int,
    impact_score: float,
    relevance: float = 1.0,
    title: str = "",
    journal: str = "",
    impact_justification: str = "",
    synopsis: str = "",
    first_pid: str = "",
    first_name: str = "",
    last_pid: str = "",
    last_name: str = "",
    faculty_uid: str = "",
    author_position: str = "",
) -> dict:
    """Build a TOPIC# DDB low-level item in the shape assign_subtopics
    writes and pool_ranker reads.

    The S/N wrappers are DynamoDB's low-level type tags (boto3 client API,
    not the Resource API). pool_ranker uses the low-level client.

    ``relevance`` populates the ``score`` attribute (the dense topic-relevance
    that build_topic_rows_for_pmid always writes); pool_ranker blends it with
    impact_score via utils.scoring.article_score. Defaults to 1.0.
    """
    item = {
        "PK": {"S": f"TOPIC#unused#{pmid}"},
        "pmid": {"S": pmid},
        "primary_subtopic_id": {"S": primary_subtopic_id},
        "year": {"N": str(year)},
        "impact_score": {"N": str(impact_score)},
        "score": {"N": str(relevance)},
    }
    # Optional attributes — only set if non-empty (mirrors real assign output).
    if title:
        item["title"] = {"S": title}
    if journal:
        item["journal"] = {"S": journal}
    if impact_justification:
        item["impact_justification"] = {"S": impact_justification}
    if synopsis:
        item["synopsis"] = {"S": synopsis}
    if first_pid:
        item["first_author_person_identifier"] = {"S": first_pid}
    if first_name:
        item["first_author_display_name"] = {"S": first_name}
    if last_pid:
        item["last_author_person_identifier"] = {"S": last_pid}
    if last_name:
        item["last_author_display_name"] = {"S": last_name}
    if faculty_uid:
        item["faculty_uid"] = {"S": faculty_uid}
    if author_position:
        item["author_position"] = {"S": author_position}
    return item


class _FakeDDBClient:
    """Minimal stand-in for a low-level boto3 DynamoDB client. Provides
    just enough of the paginator interface for pool_ranker.rank_pool to
    work.

    The real client's paginator.paginate(...) returns an iterator of
    pages, each a dict with key ``Items``. We mirror that shape.
    """

    def __init__(self, items: list[dict]):
        self._items = items

    def get_paginator(self, op: str):
        assert op == "scan", f"Unexpected paginator op {op!r}"
        return _FakePaginator(self._items)


class _FakePaginator:
    def __init__(self, items: list[dict]):
        self._items = items

    def paginate(self, **kwargs) -> Iterator[dict]:
        # pool_ranker passes FilterExpression="begins_with(PK, :prefix)"
        # with ExpressionAttributeValues={":prefix": {"S": "TOPIC#"}}. We
        # don't need to actually filter in the test — every item we hand
        # back already has PK=TOPIC#... — but assert the contract so a
        # future filter change is surfaced.
        assert kwargs.get("FilterExpression") == "begins_with(PK, :prefix)", (
            f"pool_ranker filter contract changed: {kwargs!r}"
        )
        assert kwargs.get("ExpressionAttributeValues") == {":prefix": {"S": "TOPIC#"}}, (
            f"pool_ranker prefix-filter changed: {kwargs!r}"
        )
        yield {"Items": self._items}


def test_assign_topic_rows_consumed_by_pool_ranker():
    """A TOPIC# row in the shape assign_subtopics writes must produce a
    PoolEntry with the right subtopic_id, papers, and pool_score.

    If assign renames `primary_subtopic_id` → e.g. `subtopic` on TOPIC#
    rows, _extract_paper continues to read the old name and papers land
    with empty subtopic — they're filtered out at line 232. The pool
    becomes empty and this test fails.
    """
    current_year = date.today().year
    items = [
        _ddb_topic_item(
            pmid="11111",
            primary_subtopic_id="topic_alpha_sub1",
            year=current_year,
            impact_score=85.0,
            title="Paper A on alpha",
            journal="Journal A",
            first_pid="alice", first_name="Alice Smith",
            last_pid="bob", last_name="Bob Jones",
        ),
        _ddb_topic_item(
            pmid="22222",
            primary_subtopic_id="topic_alpha_sub1",
            year=current_year - 1,
            impact_score=60.0,
            title="Paper B on alpha",
            first_pid="alice", first_name="Alice Smith",
            last_pid="carol", last_name="Carol Lee",
        ),
        _ddb_topic_item(
            pmid="33333",
            primary_subtopic_id="topic_beta_sub1",
            year=current_year,
            impact_score=70.0,
            title="Paper C on beta",
            first_pid="dave", first_name="Dave Kim",
            last_pid="eve", last_name="Eve Park",
        ),
    ]

    parent_lookup = {
        "topic_alpha_sub1": "topic_alpha",
        "topic_beta_sub1": "topic_beta",
    }

    pool = rank_pool(
        client=_FakeDDBClient(items),
        parent_lookup=parent_lookup,
        author_resolver=None,  # skip strict-author resolution
    )

    assert len(pool) == 2, (
        f"Expected 2 subtopics in pool (alpha_sub1, beta_sub1); got {len(pool)}. "
        f"If this fails on a current row shape, _extract_paper rejected our "
        f"fixture — either the DDB item fixture in _ddb_topic_item is out of "
        f"date or pool_ranker's reader changed."
    )

    by_sid = {e.subtopic_id: e for e in pool}
    assert "topic_alpha_sub1" in by_sid
    assert "topic_beta_sub1" in by_sid

    alpha = by_sid["topic_alpha_sub1"]
    # pool_score for alpha = sum of top-K article_scores (K ≥ 2). With the
    # default K and 2 papers (impact 85 + 60, relevance 1.0), it's the sum of
    # their article_scores.
    expected_alpha = article_score(85.0, 1.0) + article_score(60.0, 1.0)
    assert alpha.pool_score == expected_alpha, f"alpha pool_score expected {expected_alpha}; got {alpha.pool_score}"
    assert alpha.parent_topic == "topic_alpha"
    assert {p.pmid for p in alpha.papers} == {"11111", "22222"}

    beta = by_sid["topic_beta_sub1"]
    assert beta.pool_score == article_score(70.0, 1.0)
    assert beta.parent_topic == "topic_beta"
    assert beta.papers[0].pmid == "33333"
    assert beta.papers[0].first_author.person_identifier == "dave"
    assert beta.papers[0].last_author.person_identifier == "eve"


def test_assign_topic_row_with_missing_pmid_field_drops_paper():
    """Defensive: if assign ever writes a TOPIC# row without `pmid`,
    _extract_paper returns None and the row drops. This is the documented
    contract (pool_ranker.py:108–110); test pins it so a regression that
    starts emitting silent placeholder PMIDs is loud.
    """
    current_year = date.today().year
    items = [
        # Valid row — should land in the pool.
        _ddb_topic_item(
            pmid="11111",
            primary_subtopic_id="topic_alpha_sub1",
            year=current_year,
            impact_score=50.0,
        ),
        # Malformed: no pmid attribute at all.
        {
            "PK": {"S": "TOPIC#bad#row"},
            "primary_subtopic_id": {"S": "topic_alpha_sub1"},
            "year": {"N": str(current_year)},
            "impact_score": {"N": "99.0"},
        },
    ]

    pool = rank_pool(client=_FakeDDBClient(items), author_resolver=None)

    assert len(pool) == 1
    assert pool[0].pool_score == article_score(50.0, 1.0), (
        "Malformed pmid-less row leaked into the pool; pool_ranker's drop "
        "contract is broken."
    )


def test_assign_topic_row_outside_recency_window_excluded():
    """pool_ranker's 24-month cutoff (line 209) drops old rows. Pin the
    contract — if it ever silently widens, spotlights start surfacing
    stale work."""
    current_year = date.today().year
    items = [
        _ddb_topic_item(
            pmid="11111",
            primary_subtopic_id="topic_alpha_sub1",
            year=current_year,
            impact_score=40.0,
        ),
        _ddb_topic_item(
            pmid="OLD",
            primary_subtopic_id="topic_alpha_sub1",
            year=current_year - 10,  # well outside 24-month window
            impact_score=999.0,  # would dominate if it leaked through
        ),
    ]

    pool = rank_pool(client=_FakeDDBClient(items), author_resolver=None)
    assert len(pool) == 1
    assert pool[0].pool_score == article_score(40.0, 1.0), (
        "Stale (>24mo) paper leaked into pool — recency cutoff regressed."
    )
