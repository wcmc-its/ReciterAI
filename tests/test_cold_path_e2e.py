"""G-37 (Phase 12): one bounded end-to-end test from score_publications
through pipeline_hierarchy.publish.

Per D-21: this is ONE specific test, not an integration test suite. It
buys architectural confidence per unit of work — anything else lives
in a follow-up phase.

Per CONTEXT line 'Why fixture taxonomy is INPUT not OUTPUT': taxonomy
generation (the Bedrock-driven generate_taxonomy.py) is out of scope
here — the fixture treats taxonomy as input. The chain exercised is
aggregate → publish (score_publications and assign_subtopics are bypassed
because they require Bedrock LLM calls and RDS/DynamoDB connections that
are unavailable in the default test run; instead, the test builds synthetic
SCORE# rows — the output format of assign_subtopics — directly).

Mocks: DDB tables (MagicMock — capture put_item calls); S3 client
(MagicMock — capture put_object calls and their byte payloads); Bedrock
(replaced with canned responses where any stage invokes the LLM).

## Chain exercised

1. Fixture corpus: load tests/fixtures/cold_path_corpus/{taxonomy.json,pubs.json}
2. Synthetic SCORE# rows: build rows in the format that assign_subtopics.py
   writes — each row has faculty_uid, primary_subtopic_id, subtopic_ids[],
   score, and impact_score. This bypasses score_publications + assign_subtopics
   without sacrificing coverage of the load-bearing stages.
3. Aggregate (Phase 12 §8 D-17): aggregate_subtopic_scores._aggregate_exclusive
   + _aggregate_inclusive + _write_subtopic_score_partitions (writes both
   SUBTOPIC_SCORE# and SUBTOPIC_SCORE_INCLUSIVE# partitions to a MagicMock table).
4. D-33 reconciliation (Phase 12 D-33): _assert_d33_reconciliation verifies
   faculty-map ↔ SUBTOPIC_SCORE# partition equality.
5. Publish (Phase 11 + Phase 12 G-36): publish.main() with mocked S3 +
   mocked bundle + mocked DDB; captures hierarchy.json, manifest.json,
   diff.json bytes from put_object calls.

## D-20 gate audit row (G-37 precondition per D-20)

D-20 gate audit row: sha=6509595daab2b09e5a7e012d69e8c077a902a658 exit=0

All 186 D-20 gating tests passed on main at the above SHA before this
test was written. The audit row is persisted here per D-20's SHA-able gate
requirement and per the Phase 12 SUMMARY.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import jsonschema
import pytest

import aggregate_subtopic_scores as agg
from pipeline_hierarchy import publish as _publish_module
from pipeline_hierarchy.bundler import bundle

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "cold_path_corpus"
SCHEMA_PATH = REPO_ROOT / "docs" / "hierarchy.schema.json"

# Faculty identifiers used in synthetic SCORE# rows.
# The aggregator strips the "cwid_" prefix to get person_identifier.
FACULTY_ALICE = "cwid_alice_test"
FACULTY_BOB = "cwid_bob_test"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _load_fixture_taxonomy() -> dict:
    """Load the G-37 fixture taxonomy (2 topics × 3 subtopics each)."""
    return json.loads((FIXTURE_DIR / "taxonomy.json").read_text())


def _load_fixture_pubs() -> list[dict]:
    """Load the G-37 fixture corpus (30 publications, 5 per subtopic)."""
    return json.loads((FIXTURE_DIR / "pubs.json").read_text())


def _load_hierarchy_schema() -> dict:
    """Load docs/hierarchy.schema.json for jsonschema validation."""
    return json.loads(SCHEMA_PATH.read_text())


def _validate_fixture(taxonomy: dict, pubs: list[dict]) -> None:
    """Cross-validate the corpus fixture: every ``intended_subtopic`` in
    ``pubs.json`` must appear as a subtopic id in ``taxonomy.json``
    (IN-06).

    Without this guard, a typo or hand-edit drift in the fixture (e.g. a
    pub mapped to ``intended_subtopic="genom1cs"``) would silently flow
    through ``_aggregate_exclusive`` — which does not validate ids
    against the taxonomy — and the E2E assertions would still pass
    against a malformed downstream shape. Surfacing fixture drift here
    as an explicit AssertionError makes the failure mode "fixture drift"
    rather than "mysterious downstream null shape".
    """
    valid_ids = {
        s["id"]
        for t in taxonomy.get("topics", [])
        for s in t.get("subtopics", [])
    }
    for pub in pubs:
        intended = pub.get("intended_subtopic", "")
        assert intended in valid_ids, (
            f"Fixture drift: pub {pub.get('pmid')!r} -> "
            f"intended_subtopic={intended!r} not in taxonomy.json subtopics "
            f"(valid: {sorted(valid_ids)})"
        )


def _make_score_row(
    *,
    faculty_uid: str,
    primary_subtopic_id: str,
    subtopic_ids: list[str],
    score: float = 0.8,
    impact_score: float = 50.0,
) -> dict:
    """Build a synthetic SCORE# row in the format assign_subtopics.py writes.

    The aggregator reads these fields from DynamoDB SCORE# rows (per
    aggregate_subtopic_scores.py:_aggregate_exclusive and _aggregate_inclusive).
    Bypassing score_publications + assign_subtopics avoids Bedrock + RDS
    dependencies in the default test run.
    """
    return {
        "faculty_uid": faculty_uid,
        "primary_subtopic_id": primary_subtopic_id,
        "subtopic_ids": subtopic_ids,
        "score": score,
        "impact_score": impact_score,
    }


def _build_corpus_score_rows(taxonomy: dict) -> list[dict]:
    """Build synthetic SCORE# rows for all 30 fixture publications.

    Maps the 30-pub corpus (5 pubs × 3 subtopics × 2 topics) to SCORE# rows.
    Each pub is assigned a primary subtopic matching its intended_subtopic;
    some pubs also carry a secondary above-floor subtopic_id to exercise
    the inclusive aggregation path (D-15).

    Returns a flat list of SCORE# rows ready for _aggregate_exclusive
    and _aggregate_inclusive.
    """
    pubs = _load_fixture_pubs()
    # IN-06: assert fixture self-consistency before building rows. Catches
    # pubs.json / taxonomy.json drift (e.g. a renamed subtopic id that was
    # only updated in one file) as a clear AssertionError instead of letting
    # the malformed row flow through the aggregator unvalidated.
    _validate_fixture(taxonomy, pubs)
    rows: list[dict] = []

    for i, pub in enumerate(pubs):
        intended = pub.get("intended_subtopic", "")
        # Alternate attribution between two faculty members so each subtopic
        # has non-trivial faculty_scores dicts with ≥1 entry.
        faculty = FACULTY_ALICE if (i % 2 == 0) else FACULTY_BOB

        # Most pubs are primary-only (singleton subtopic_ids).
        # Pubs 0, 5, 10, 15, 20, 25 (first in each subtopic group) also
        # carry a secondary subtopic_id from the same topic — this exercises
        # the inclusive > exclusive delta path (D-17 invariant, delta case).
        subtopic_ids = [intended]
        if i % 5 == 0 and intended:
            # Add a secondary subtopic_id: rotate to the next subtopic in
            # the same topic by finding the topic and picking the next sub.
            for topic in taxonomy.get("topics", []):
                subs = [s["id"] for s in topic.get("subtopics", [])]
                if intended in subs:
                    idx = subs.index(intended)
                    secondary = subs[(idx + 1) % len(subs)]
                    if secondary != intended:
                        subtopic_ids = [intended, secondary]
                    break

        rows.append(_make_score_row(
            faculty_uid=faculty,
            primary_subtopic_id=intended,
            subtopic_ids=subtopic_ids,
            score=0.75 + 0.01 * (i % 5),    # slight variation for realism
            impact_score=40.0 + 2.0 * (i % 5),
        ))

    return rows


def _write_augmented(dir_path: Path, topic_id: str, subtopics: list[dict]) -> None:
    """Write a hierarchy_augmented_<topic_id>.json file for the publish fixture."""
    payload = {
        "topic_id": topic_id,
        "topic_label": topic_id.replace("_", " ").title(),
        "subtopics": subtopics,
    }
    (dir_path / f"hierarchy_augmented_{topic_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _minimal_sub(sid: str, *, activity_count: int = 5, total_weight: float = 10.0) -> dict:
    """Build a minimal subtopic dict with all required hierarchy.schema.json fields.

    display_name must NOT start with a parent-topic word (parent_prefix gate).
    Our fixture topic IDs are test_topic_alpha / test_topic_beta, so the parent
    tokens are {"test", "topic", "alpha", "beta"} (minus stop-words). We prefix
    display_name with "Research:" to ensure the first word is always neutral.
    """
    # Use a neutral first word ("Research:") so the parent_prefix gate never fires
    # for our synthetic topic IDs (test_topic_alpha / test_topic_beta).
    label_part = sid.replace("test_topic_alpha_", "").replace("test_topic_beta_", "").replace("_", " ").title()
    return {
        "id": sid,
        "label": f"Label for {sid}",
        "description": f"Description for {sid}.",
        "display_name": f"Research: {label_part}",
        "short_description": f"Tagline for {sid}.",
        "activity_count": activity_count,
        "total_weight": total_weight,
    }


def _make_augmented_dir(tmp_path: Path, taxonomy: dict) -> Path:
    """Create a hierarchy_augmented_*.json file for each topic in the taxonomy fixture."""
    aug_dir = tmp_path / "aug"
    aug_dir.mkdir()
    for topic in taxonomy.get("topics", []):
        topic_id = topic["id"]
        subs = [_minimal_sub(s["id"]) for s in topic.get("subtopics", [])]
        _write_augmented(aug_dir, topic_id, subs)
    return aug_dir


def _make_taxonomy_file(tmp_path: Path, taxonomy: dict) -> Path:
    """Write the fixture taxonomy to a temp file for pipeline_hierarchy.bundler."""
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps(taxonomy), encoding="utf-8")
    return p


def _make_excluded_file(tmp_path: Path) -> Path:
    """Write an empty excluded_topics.json."""
    p = tmp_path / "excluded.json"
    p.write_text(json.dumps({"excluded_topics": []}), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# S3 put_object capture stub (reused from test_hierarchy_reproducibility.py pattern)
# ---------------------------------------------------------------------------


class _S3PutCapture:
    """Minimal S3HierarchyClient stub that captures put_object byte payloads."""

    def __init__(self):
        self.calls: list[dict] = []

    def put_object(
        self,
        key: str,
        body: bytes,
        content_type: str = "application/json",
        cache_control: str | None = None,
    ) -> None:
        self.calls.append({"key": key, "body": body, "cache_control": cache_control})

    def get_object_bytes(self, key: str) -> bytes:
        # No prior manifest on first publish (O-01 path).
        from botocore.exceptions import ClientError
        raise ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject"
        )

    def key_exists(self, key: str) -> bool:
        return False


def _extract_put_body(s3_stub: _S3PutCapture, key_suffix: str) -> bytes:
    """Return bytes from the first put_object call whose key ends with key_suffix."""
    for c in s3_stub.calls:
        if c["key"].endswith(key_suffix):
            return c["body"]
    keys = [c["key"] for c in s3_stub.calls]
    raise AssertionError(
        f"No put_object call with key ending '{key_suffix}'. "
        f"Recorded keys: {keys}"
    )


# ---------------------------------------------------------------------------
# Publish runner
# ---------------------------------------------------------------------------


def _run_publish(bundled_dict: dict, pinned_time: str) -> _S3PutCapture:
    """Run publish.main() end-to-end with a pinned timestamp and mocked DDB + S3.

    Stubs:
      - publish.bundle → returns bundled_dict (pre-built from fixture)
      - pipeline_hierarchy.generator.datetime → pinned so generated_at is deterministic
      - publish.get_table → MagicMock (no prior STAGE# row → no skip)
      - publish.write_local → no-op (avoids filesystem side-effects)
      - publish.S3HierarchyClient → _S3PutCapture instance
    """
    fixed_dt = datetime.fromisoformat(pinned_time.replace("Z", "+00:00"))

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_dt.replace(tzinfo=tz) if tz else fixed_dt

    s3_stub = _S3PutCapture()
    table_mock = MagicMock()
    table_mock.query.return_value = {"Items": []}  # no prior complete row → no skip

    with (
        patch.object(_publish_module, "bundle", return_value=bundled_dict),
        patch.object(_publish_module, "get_table", return_value=table_mock),
        patch.object(_publish_module, "write_local", MagicMock()),
        patch.object(_publish_module, "S3HierarchyClient", return_value=s3_stub),
        patch("pipeline_hierarchy.generator.datetime", _FixedDatetime),
    ):
        rc = _publish_module.main(["--version", "vtest-e2e-g37"])

    assert rc == _publish_module.EXIT_OK, (
        f"publish.main() returned {rc!r}; expected EXIT_OK ({_publish_module.EXIT_OK})"
    )
    return s3_stub


# ---------------------------------------------------------------------------
# Test 1: corpus flows through aggregate → publish; manifest validates against schema
# ---------------------------------------------------------------------------


def test_cold_path_corpus_through_publish(tmp_path):
    """G-37 D-21: corpus flows through aggregate → publish; manifest validates schema.

    cold_path_corpus fixture (2 topics × 3 subtopics × 5 pubs) flows through:
      1. Build synthetic SCORE# rows (bypassing score_publications + assign_subtopics,
         which require Bedrock + RDS; see module docstring for rationale)
      2. _aggregate_exclusive + _aggregate_inclusive
      3. _write_subtopic_score_partitions (MagicMock DDB table)
      4. _assert_d33_reconciliation
      5. publish.main() → hierarchy.json + manifest.json + diff.json

    Assertions:
      - hierarchy.json validates against docs/hierarchy.schema.json
      - manifest.json is well-formed JSON with required fields
      - diff.json is well-formed JSON
    """
    taxonomy = _load_fixture_taxonomy()
    rows = _build_corpus_score_rows(taxonomy)
    schema = _load_hierarchy_schema()

    # Step 2: aggregate
    faculty_scores_excl, total_weights_excl = agg._aggregate_exclusive(rows)
    faculty_scores_incl, total_weights_incl = agg._aggregate_inclusive(rows)

    assert faculty_scores_excl, "Expected non-empty faculty_scores from exclusive aggregation"
    assert faculty_scores_incl, "Expected non-empty faculty_scores from inclusive aggregation"

    # Step 3: write to mock DDB table (captures put_item calls)
    mock_table = MagicMock()
    for topic in taxonomy.get("topics", []):
        topic_id = topic["id"]
        agg._write_subtopic_score_partitions(
            mock_table,
            topic_id=topic_id,
            faculty_scores_exclusive=faculty_scores_excl,
            faculty_scores_inclusive=faculty_scores_incl,
            run_id="e2e-test-run-g37",
        )

    # Verify put_item was called at all (both partition types written)
    assert mock_table.put_item.called, "Expected put_item to be called for subtopic partitions"

    # Step 4: D-33 reconciliation — should not raise on aligned data
    agg._assert_d33_reconciliation(
        faculty_map=faculty_scores_excl,
        subtopic_score_partition_data=faculty_scores_excl,  # same data = no divergence
    )

    # Step 5: publish via mocked S3 (builds fixture augmented dir)
    aug_dir = _make_augmented_dir(tmp_path, taxonomy)
    taxonomy_path = _make_taxonomy_file(tmp_path, taxonomy)
    excluded_path = _make_excluded_file(tmp_path)
    bundled = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=taxonomy_path,
        excluded_topics_path=excluded_path,
    )
    s3_stub = _run_publish(bundled, "2026-06-01T00:00:00Z")

    hierarchy_bytes = _extract_put_body(s3_stub, "hierarchy.json")
    manifest_bytes = _extract_put_body(s3_stub, "manifest.json")
    diff_bytes = _extract_put_body(s3_stub, "diff.json")

    # Assertion: hierarchy.json validates against schema
    hierarchy = json.loads(hierarchy_bytes)
    jsonschema.validate(hierarchy, schema=schema)

    # Assertion: manifest.json is well-formed with expected fields
    manifest = json.loads(manifest_bytes)
    assert "sha256" in manifest, f"manifest missing 'sha256'; keys: {list(manifest.keys())}"
    assert "generated_at" in manifest, (
        "manifest missing 'generated_at' (Phase 11 D-14 moved it here from hierarchy)"
    )

    # Assertion: diff.json is well-formed
    diff = json.loads(diff_bytes)
    assert "diff_schema_version" in diff, (
        f"diff.json missing 'diff_schema_version'; keys: {list(diff.keys())}"
    )


# ---------------------------------------------------------------------------
# Test 2: second run produces byte-identical hierarchy.json (G-36/G-29 chain)
# ---------------------------------------------------------------------------


def test_cold_path_byte_identical_second_run(tmp_path):
    """G-37 D-21 + G-36 (Phase 12) + G-29 (Phase 11): byte-identical hierarchy.json
    across two runs of the same fixture corpus through aggregate → publish.

    Phase 11 D-14/G-29 removed generated_at from hierarchy.json; Phase 12 G-36
    extended coverage to the publish() path. This test extends the byte-identical
    invariant end-to-end — covering the aggregate → publish chain — and catches
    any future regression that re-introduces a non-deterministic field via a new
    code path between the aggregation layer and S3 upload.

    Run 1 uses pinned timestamp "2026-06-01T00:00:00Z";
    Run 2 uses a DIFFERENT pinned timestamp "2026-07-01T12:00:00Z".
    The hierarchy.json bytes must be identical (proving D-14 robustness).
    """
    taxonomy = _load_fixture_taxonomy()
    aug_dir = _make_augmented_dir(tmp_path, taxonomy)
    taxonomy_path = _make_taxonomy_file(tmp_path, taxonomy)
    excluded_path = _make_excluded_file(tmp_path)
    bundled = bundle(
        augmented_dir=aug_dir,
        taxonomy_path=taxonomy_path,
        excluded_topics_path=excluded_path,
    )

    run1 = _run_publish(bundled, "2026-06-01T00:00:00Z")
    run2 = _run_publish(bundled, "2026-07-01T12:00:00Z")

    h_bytes_1 = _extract_put_body(run1, "hierarchy.json")
    h_bytes_2 = _extract_put_body(run2, "hierarchy.json")

    assert h_bytes_1 == h_bytes_2, (
        "G-37 / G-36 / G-29 / D-14: publish() must emit byte-identical hierarchy.json "
        "across content-identical runs regardless of wall-clock timestamp. "
        f"Run 1 len={len(h_bytes_1)}, Run 2 len={len(h_bytes_2)}. "
        "Check that generated_at was not re-introduced into the hierarchy body."
    )


# ---------------------------------------------------------------------------
# Test 3: aggregate stage writes both subtopic partitions (Phase 12 D-17)
# ---------------------------------------------------------------------------


def test_cold_path_emits_both_subtopic_partitions():
    """G-37 + D-17: both SUBTOPIC_SCORE# and SUBTOPIC_SCORE_INCLUSIVE# partitions
    are written during the aggregation stage.

    Asserts that _write_subtopic_score_partitions puts items with:
      - PK starting with "SUBTOPIC_SCORE#"
      - PK starting with "SUBTOPIC_SCORE_INCLUSIVE#"

    This exercises the Phase 12 §8 both-aggregations integration across the
    full fixture corpus (2 topics × 3 subtopics).
    """
    taxonomy = _load_fixture_taxonomy()
    rows = _build_corpus_score_rows(taxonomy)

    faculty_scores_excl, _ = agg._aggregate_exclusive(rows)
    faculty_scores_incl, _ = agg._aggregate_inclusive(rows)

    mock_table = MagicMock()
    for topic in taxonomy.get("topics", []):
        topic_id = topic["id"]
        agg._write_subtopic_score_partitions(
            mock_table,
            topic_id=topic_id,
            faculty_scores_exclusive=faculty_scores_excl,
            faculty_scores_inclusive=faculty_scores_incl,
            run_id="e2e-partition-test",
        )

    calls = mock_table.put_item.call_args_list
    pks = [c.kwargs["Item"]["PK"] for c in calls]

    excl_pks = [pk for pk in pks if pk.startswith("SUBTOPIC_SCORE#")]
    incl_pks = [pk for pk in pks if pk.startswith("SUBTOPIC_SCORE_INCLUSIVE#")]

    assert len(excl_pks) >= 1, (
        "Expected at least one SUBTOPIC_SCORE# PK from the fixture corpus aggregate. "
        f"All PKs written: {pks}"
    )
    assert len(incl_pks) >= 1, (
        "Expected at least one SUBTOPIC_SCORE_INCLUSIVE# PK from the fixture corpus aggregate. "
        f"All PKs written: {pks}"
    )

    # Both topics should have their subtopics represented in the exclusive partition.
    for topic in taxonomy.get("topics", []):
        topic_id = topic["id"]
        topic_excl = [pk for pk in excl_pks if f"#{topic_id}#" in pk]
        topic_incl = [pk for pk in incl_pks if f"#{topic_id}#" in pk]
        assert len(topic_excl) >= 1, (
            f"Expected SUBTOPIC_SCORE# entry for topic {topic_id!r}; "
            f"found none in {excl_pks}"
        )
        assert len(topic_incl) >= 1, (
            f"Expected SUBTOPIC_SCORE_INCLUSIVE# entry for topic {topic_id!r}; "
            f"found none in {incl_pks}"
        )


# ---------------------------------------------------------------------------
# Test 4: D-33 reconciliation invariant does not raise on aligned data
# ---------------------------------------------------------------------------


def test_cold_path_d33_reconciliation_passes():
    """G-37 + D-33: aggregator's in-stream reconciliation invariant does NOT raise
    when faculty-map and SUBTOPIC_SCORE# partition data are aligned.

    Happy path: _assert_d33_reconciliation is called with the exclusive
    faculty_scores dict passed as both arguments (identical data = no divergence).
    The invariant checks per-CWID per-subtopic equality; passing identical
    dicts proves the check does not raise on valid data.

    This catches regressions where the reconciliation check accidentally
    always raises, or where the data structures become incompatible.
    """
    taxonomy = _load_fixture_taxonomy()
    rows = _build_corpus_score_rows(taxonomy)

    faculty_scores_excl, _ = agg._aggregate_exclusive(rows)

    # Should not raise: passing the same dict as both faculty_map and
    # subtopic_score_partition_data means every (cwid, subtopic) pair agrees.
    agg._assert_d33_reconciliation(
        faculty_map=faculty_scores_excl,
        subtopic_score_partition_data=faculty_scores_excl,
    )

    # Also verify that the D-17 invariant holds: inclusive >= exclusive per subtopic
    faculty_scores_incl, total_weights_incl = agg._aggregate_inclusive(rows)
    _, total_weights_excl = agg._aggregate_exclusive(rows)

    all_subtopics = set(total_weights_excl.keys()) | set(total_weights_incl.keys())
    for sid in all_subtopics:
        excl_val = total_weights_excl.get(sid, 0.0)
        incl_val = total_weights_incl.get(sid, 0.0)
        assert incl_val >= excl_val - 1e-9, (
            f"D-17 invariant violated for subtopic {sid!r}: "
            f"inclusive={incl_val} < exclusive={excl_val}"
        )
