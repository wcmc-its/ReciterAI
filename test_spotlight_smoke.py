"""End-to-end smoke test for the Phase 6 spotlight pipeline.

Combines invalid-fixture rejection (D-16 fail-fast), dry-run positive path,
author-headshot payload contract (SPOT-09), CLI surface checks, and the
import-without-AWS-creds invariant. All AWS interactions mocked; no real
network calls. Verifies SPOT-09, SPOT-11, and SPOT-12 end-to-end.

Plan 06-08 Task 3 — ties Plans 06-02..06-07 together against the synthetic
fixtures shipped by Plan 06-06.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixture paths (module-level constants — fixtures live in tests/fixtures/)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent
VALID_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "spotlight_valid_min.json"
INVALID_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "spotlight_invalid.json"
SCHEMA_PATH = REPO_ROOT / "docs" / "spotlight.schema.json"


def _make_selection(subtopic_id: str = "test_aging_001"):
    """Build a minimal Selection for the post-publish history-writer call.

    Mirrors test_spotlight_publish.py's factory exactly so the smoke test
    composes the same Selection shape the live pipeline produces.
    """
    from spotlight.rotation_selector import Selection
    from spotlight.types import Author, Paper, PoolEntry

    fa = Author(person_identifier="pid_a", display_name="Alpha", position="first")
    la = Author(person_identifier="pid_b", display_name="Beta", position="last")
    paper = Paper(
        pmid="9000001",
        title="Test paper",
        journal="Test J",
        year=2025,
        impact_score=80.0,
        impact_justification="synthetic",
        synopsis="syn",
        first_author=fa,
        last_author=la,
    )
    entry = PoolEntry(
        subtopic_id=subtopic_id,
        pool_score=100.0,
        parent_topic="Aging / Geroscience",
        papers=(paper,),
    )
    return Selection(entry=entry, sel_score=100.0, last_shown_at=None)


# ---------------------------------------------------------------------------
# Test 1: invalid-fixture rejection (validation gate, no PutObject)
# ---------------------------------------------------------------------------


def test_invalid_fixture_rejection():
    """Plan 06-06 invalid fixture must trip the D-16 validation gate.

    publish_artifact() returns 1 and never touches S3.
    """
    from spotlight.publish import publish_artifact

    artifact = json.loads(INVALID_FIXTURE_PATH.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    s3 = MagicMock()
    s3.key_exists.return_value = False

    rc = publish_artifact(
        artifact=artifact,
        schema=schema,
        selections=[],
        dry_run=False,
        s3_client=s3,
        dynamo_client=MagicMock(),
    )

    assert rc == 1, "invalid fixture must return rc=1"
    assert s3.put_object.call_count == 0, "validation gate must run before any PutObject"


# ---------------------------------------------------------------------------
# Test 2: valid-fixture dry-run (no PutObject, no history writeback)
# ---------------------------------------------------------------------------


def test_valid_fixture_dry_run(capsys):
    """Dry-run validates + previews manifest but never uploads."""
    from spotlight.publish import publish_artifact

    artifact = json.loads(VALID_FIXTURE_PATH.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    s3 = MagicMock()
    dynamo = MagicMock()

    rc = publish_artifact(
        artifact=artifact,
        schema=schema,
        selections=[],
        dry_run=True,
        s3_client=s3,
        dynamo_client=dynamo,
    )

    assert rc == 0, "dry-run with valid fixture must return rc=0"
    assert s3.put_object.call_count == 0, "dry-run must NOT upload"
    assert dynamo.update_item.call_count == 0, "dry-run must NOT advance history"

    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out
    assert "Manifest preview" in captured.out


# ---------------------------------------------------------------------------
# Test 3: valid-fixture publish path (6 PutObjects + history writeback)
# ---------------------------------------------------------------------------


def test_valid_fixture_publish_path():
    """End-to-end positive path: 6 PutObjects + 1 history update_item per selection."""
    from spotlight.publish import publish_artifact

    artifact = json.loads(VALID_FIXTURE_PATH.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    selections = [_make_selection("test_aging_001")]

    s3 = MagicMock()
    s3.key_exists.return_value = False
    s3.put_object.return_value = None
    dynamo = MagicMock()

    rc = publish_artifact(
        artifact=artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=s3,
        dynamo_client=dynamo,
    )

    assert rc == 0, "valid publish must return rc=0"
    assert s3.put_object.call_count == 6, "publish must emit exactly 6 PutObjects"
    assert dynamo.update_item.call_count == len(selections), (
        "history writeback must call update_item once per selection"
    )


# ---------------------------------------------------------------------------
# Test 4: idempotency-warn-overwrites (same-day re-publish)
# ---------------------------------------------------------------------------


def test_idempotency_warning(caplog):
    """Same-day re-publish proceeds with a logger.warning (overwrite, not skip)."""
    import logging

    from spotlight.publish import publish_artifact

    artifact = json.loads(VALID_FIXTURE_PATH.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    selections = [_make_selection("test_aging_001")]

    s3 = MagicMock()
    s3.key_exists.return_value = True   # same-day re-publish trigger
    s3.put_object.return_value = None
    dynamo = MagicMock()

    with caplog.at_level(logging.WARNING, logger="spotlight.publish"):
        rc = publish_artifact(
            artifact=artifact,
            schema=schema,
            selections=selections,
            dry_run=False,
            s3_client=s3,
            dynamo_client=dynamo,
        )

    assert rc == 0
    assert s3.put_object.call_count == 6, (
        "same-day re-publish must overwrite (still 6 PutObjects), not skip"
    )
    assert any(
        "overwriting" in r.getMessage().lower() or "already exists" in r.getMessage().lower()
        for r in caplog.records
    ), "must log a same-day-re-publish overwrite warning"


# ---------------------------------------------------------------------------
# Test 5: manifest field order via dry-run capsys (SPOT-12 contract)
# ---------------------------------------------------------------------------


def test_manifest_field_order(capsys):
    """The dry-run preview prints the manifest JSON; its key order is locked.

    Validates the SPOT-12 7-field locked-order contract end-to-end.
    """
    from spotlight.publish import publish_artifact

    artifact = json.loads(VALID_FIXTURE_PATH.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())

    rc = publish_artifact(
        artifact=artifact,
        schema=schema,
        selections=[],
        dry_run=True,
        s3_client=MagicMock(),
        dynamo_client=MagicMock(),
    )
    assert rc == 0

    captured = capsys.readouterr().out

    # Find the JSON object printed after "Manifest preview:" header.
    # publish.py emits: "\nManifest preview:\n" + json.dumps(manifest, indent=2)
    marker = "Manifest preview:"
    assert marker in captured, "dry-run must print 'Manifest preview:' header"
    after = captured.split(marker, 1)[1]
    # Greedy match for the next balanced top-level JSON object.
    match = re.search(r"\{.*?\n\}", after, re.DOTALL)
    assert match is not None, "no manifest JSON object found after 'Manifest preview:'"
    parsed = json.loads(match.group(0))

    expected_order = [
        "schema_version",
        "spotlight_version",
        "taxonomy_version",
        "version",
        "generated_at",
        "sha256",
        "artifact_bytes",
    ]
    assert list(parsed.keys()) == expected_order, (
        f"manifest key order must be locked: got {list(parsed.keys())}"
    )


# ---------------------------------------------------------------------------
# Test 6: author-headshot payload camelCase contract (SPOT-09)
# ---------------------------------------------------------------------------


def test_author_headshot_payload_contract():
    """Each paper's first_author and last_author has exactly the 3 camelCase
    keys SPS expects: personIdentifier, displayName, position.

    Validates the SPOT-09 author payload contract end-to-end via the shipped
    fixture.
    """
    artifact = json.loads(VALID_FIXTURE_PATH.read_text())
    expected_keys = {"personIdentifier", "displayName", "position"}

    spotlights = artifact.get("spotlights", [])
    assert len(spotlights) >= 1, "fixture must include at least one spotlight"

    seen_authors = 0
    for sp in spotlights:
        for paper in sp.get("papers", []):
            for role in ("first_author", "last_author"):
                author = paper[role]
                assert set(author.keys()) == expected_keys, (
                    f"{role} keys must be exactly {expected_keys}, "
                    f"got {set(author.keys())} (paper pmid={paper.get('pmid')})"
                )
                # Sanity: personIdentifier is non-empty string.
                assert isinstance(author["personIdentifier"], str)
                assert author["personIdentifier"]
                # Position values must be one of the schema enum.
                assert author["position"] in ("first", "last")
                seen_authors += 1

    assert seen_authors >= 2, "must inspect at least one full first+last author pair"


# ---------------------------------------------------------------------------
# Test 7: schema validation passes the valid fixture
# ---------------------------------------------------------------------------


def test_schema_validation_passes_valid_fixture():
    """Direct symmetry check: Draft202012Validator accepts the valid fixture."""
    from jsonschema import Draft202012Validator

    artifact = json.loads(VALID_FIXTURE_PATH.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())

    errors = list(Draft202012Validator(schema).iter_errors(artifact))
    assert errors == [], f"valid fixture must validate; errors: {[e.message for e in errors]}"


# ---------------------------------------------------------------------------
# Test 8: schema validation fails the invalid fixture
# ---------------------------------------------------------------------------


def test_schema_validation_fails_invalid_fixture():
    """Direct symmetry check: Draft202012Validator rejects the invalid fixture."""
    from jsonschema import Draft202012Validator

    artifact = json.loads(INVALID_FIXTURE_PATH.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())

    errors = list(Draft202012Validator(schema).iter_errors(artifact))
    assert len(errors) >= 1, "invalid fixture must fail schema validation"


# ---------------------------------------------------------------------------
# Test 9: backfill_spotlight.py --help lists all 9 documented flags
# ---------------------------------------------------------------------------


def test_backfill_spotlight_help_lists_all_flags():
    """CLI surface smoke: every flag named in the plan + handoff appears in --help."""
    # One-line invocation pattern: subprocess.run backfill_spotlight.py --help (CLI surface acceptance check).
    result = subprocess.run([sys.executable, "backfill_spotlight.py", "--help"], capture_output=True, text=True, timeout=10, cwd=REPO_ROOT)
    assert result.returncode == 0, (
        f"backfill_spotlight.py --help failed (rc={result.returncode}): {result.stderr}"
    )
    for flag in [
        "--dry-run",
        "--dry-run-full",
        "--publish",
        "--regen-only",
        "--review-queue",
        "--publish-id",
        "--approve",
        "--reject",
        "--reset-history",
    ]:
        assert flag in result.stdout, f"missing CLI flag in --help output: {flag}"


# ---------------------------------------------------------------------------
# Test 10: every spotlight module imports cleanly without AWS credentials
# ---------------------------------------------------------------------------


def test_no_aws_calls_at_imports():
    """Strong invariant: every spotlight.* module must import cleanly with no
    AWS credentials in the environment. Lazy boto3 init across the codebase
    makes this work; this test is the canary for any regression that pulls
    a boto3 client construction up to module-load time.
    """
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("AWS_"):
            env.pop(k)  # env.pop(AWS_*) — clear every AWS_* var before subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import spotlight.pool_ranker, spotlight.rotation_selector, "
                "spotlight.lede_generator, spotlight.critic, "
                "spotlight.sensitive_gate, spotlight.review_queue, "
                "spotlight.assembler, spotlight.publish, "
                "spotlight.history_writer; print('OK')"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, (
        f"spotlight imports failed without AWS creds: {result.stderr}"
    )
    assert "OK" in result.stdout
