"""Tests for spotlight/publish.py (Plan 06-07 Task 1).

Covers SPOT-11 (validate + upload + idempotent re-run) and SPOT-12 (manifest
with locked 7-field insertion order including spotlight_version).

Twelve tests per Plan 06-07 <behavior> block:

  1.  publish_artifact with valid artifact returns 0; calls put_object
      exactly 6 times.
  2.  publish_artifact with invalid artifact returns 1; calls put_object
      zero times (validation precedes upload, D-16 fail-fast).
  3.  dry_run=True returns 0, calls put_object zero times, prints manifest
      preview.
  4.  Manifest dict has exactly 7 keys in locked insertion order.
  5.  manifest.sha256 == sha256 of in-memory json.dumps bytes (not file).
  6.  PutObject keys are emitted in the locked order.
  7.  All PutObject calls target ARTIFACTS_BUCKET (not HIERARCHY_BUCKET).
  8.  NoCredentialsError → returns 1, prints operator hint mentioning
      AWS_ACCESS_KEY_ID and ~/.zshrc, never logs the value.
  9.  Same-day re-publish: key_exists True → publish proceeds with
      logger.warning (overwrites).
  10. After 6 successful PutObjects, history_writer.update_history is
      called once with selections + publish_id == version.
  11. json.dumps in publish_artifact does NOT pass sort_keys=True
      (grep on source).
  12. spotlight_version manifest field == "spotlight_v1".
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import NoCredentialsError

from spotlight.publish import publish_artifact
from spotlight.rotation_selector import Selection
from spotlight.types import Author, Paper, PoolEntry

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Synthetic factories
# ---------------------------------------------------------------------------


def _make_selection(subtopic_id: str = "test_aging_001") -> Selection:
    """Build a minimal Selection for history_writer post-publish call."""
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


@pytest.fixture
def valid_artifact() -> dict:
    return json.loads((FIXTURES_DIR / "spotlight_valid_min.json").read_text())


@pytest.fixture
def invalid_artifact() -> dict:
    return json.loads((FIXTURES_DIR / "spotlight_invalid.json").read_text())


@pytest.fixture
def schema() -> dict:
    return json.loads(Path("docs/spotlight.schema.json").read_text())


@pytest.fixture
def mock_s3() -> MagicMock:
    s3 = MagicMock()
    s3.key_exists.return_value = False
    s3.put_object.return_value = None
    s3.bucket = "wcmc-reciterai-artifacts"
    return s3


@pytest.fixture
def mock_dynamo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def selections() -> list[Selection]:
    return [_make_selection("test_aging_001")]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_01_valid_artifact_returns_zero_six_putobjects(
    valid_artifact, schema, selections, mock_s3, mock_dynamo
):
    rc = publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
    )
    assert rc == 0
    assert mock_s3.put_object.call_count == 6


def test_02_invalid_artifact_returns_one_no_putobjects(
    invalid_artifact, schema, selections, mock_s3, mock_dynamo, capsys
):
    rc = publish_artifact(
        artifact=invalid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
    )
    assert rc == 1
    assert mock_s3.put_object.call_count == 0
    captured = capsys.readouterr()
    assert "SCHEMA VALIDATION FAILED" in captured.out


def test_03_dry_run_returns_zero_no_putobjects(
    valid_artifact, schema, selections, mock_s3, mock_dynamo, capsys
):
    rc = publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=True,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
    )
    assert rc == 0
    assert mock_s3.put_object.call_count == 0
    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out
    # update_history must NOT be called in dry-run.
    assert mock_dynamo.update_item.call_count == 0


def test_04_manifest_has_seven_keys_in_locked_order():
    """Static check on the source: manifest dict literal has the 7 keys
    in the locked insertion order [schema_version, spotlight_version,
    taxonomy_version, version, generated_at, sha256, artifact_bytes].
    """
    src = Path("spotlight/publish.py").read_text()
    m = re.search(r"manifest\s*=\s*\{(.*?)\}", src, re.DOTALL)
    assert m is not None, "manifest dict literal not found"
    keys = re.findall(r'"(\w+)"\s*:', m.group(1))
    expected = [
        "schema_version",
        "spotlight_version",
        "taxonomy_version",
        "version",
        "generated_at",
        "sha256",
        "artifact_bytes",
    ]
    assert keys == expected, f"manifest key order: {keys}"


def test_05_manifest_sha256_over_in_memory_bytes(
    valid_artifact, schema, selections, mock_s3, mock_dynamo
):
    """The sha256 in the uploaded manifest equals the sha256 of the
    in-memory json.dumps bytes that get uploaded as spotlight.json.
    """
    publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
    )
    # Recompute expected sha256 the same way publish_artifact does.
    expected_bytes = json.dumps(valid_artifact, indent=2, ensure_ascii=False).encode(
        "utf-8"
    )
    expected_sha = hashlib.sha256(expected_bytes).hexdigest()
    # Find manifest payload across the 6 PutObject calls.
    manifest_payload = None
    for call in mock_s3.put_object.call_args_list:
        args, kwargs = call.args, call.kwargs
        key = args[0] if args else kwargs.get("key")
        body = args[1] if len(args) > 1 else kwargs.get("body")
        if key and key.endswith("manifest.json"):
            manifest_payload = json.loads(body.decode("utf-8"))
            break
    assert manifest_payload is not None, "no manifest.json upload found"
    assert manifest_payload["sha256"] == expected_sha


def test_06_putobject_keys_in_locked_order(
    valid_artifact, schema, selections, mock_s3, mock_dynamo
):
    publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
    )
    keys = [c.args[0] for c in mock_s3.put_object.call_args_list]
    today = date.today().isoformat()
    expected_keys = [
        f"spotlight/v{today}/spotlight.json",
        f"spotlight/v{today}/spotlight.schema.json",
        f"spotlight/v{today}/manifest.json",
        "spotlight/latest/spotlight.json",
        "spotlight/latest/spotlight.schema.json",
        "spotlight/latest/manifest.json",
    ]
    assert keys == expected_keys


def test_07_target_artifacts_bucket_constant():
    """Static check: the source instantiates S3HierarchyClient with
    bucket=ARTIFACTS_BUCKET, and imports ARTIFACTS_BUCKET from
    utils.s3_client (Phase 6 delta vs. Phase 5 HIERARCHY_BUCKET)."""
    src = Path("spotlight/publish.py").read_text()
    assert "S3HierarchyClient(bucket=ARTIFACTS_BUCKET)" in src
    # ARTIFACTS_BUCKET must be imported (constant, not string literal).
    assert re.search(
        r"from\s+utils\.s3_client\s+import[^\n]*ARTIFACTS_BUCKET", src
    ) is not None
    # The hierarchy bucket constant must NOT be referenced as Python code
    # (it is OK if the docstring mentions it in prose for context).
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code_only = re.sub(r"#[^\n]*", "", code_only)
    assert "HIERARCHY_BUCKET" not in code_only


def test_08_no_credentials_error_operator_hint(
    valid_artifact, schema, selections, mock_dynamo, capsys
):
    s3 = MagicMock()
    s3.key_exists.side_effect = NoCredentialsError()
    rc = publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=s3,
        dynamo_client=mock_dynamo,
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "AWS credentials not found" in captured.out
    assert "AWS_ACCESS_KEY_ID" in captured.out
    assert "~/.zshrc" in captured.out


def test_09_same_day_republish_warns_overwrites(
    valid_artifact, schema, selections, mock_s3, mock_dynamo, caplog
):
    mock_s3.key_exists.return_value = True
    with caplog.at_level(logging.WARNING, logger="spotlight.publish"):
        rc = publish_artifact(
            artifact=valid_artifact,
            schema=schema,
            selections=selections,
            dry_run=False,
            s3_client=mock_s3,
            dynamo_client=mock_dynamo,
        )
    assert rc == 0
    # Still 6 uploads (idempotent overwrite, not skip).
    assert mock_s3.put_object.call_count == 6
    # Warning emitted.
    assert any(
        "overwriting" in r.getMessage().lower()
        or "already exists" in r.getMessage().lower()
        for r in caplog.records
    )


def test_10_history_writer_called_after_six_putobjects(
    valid_artifact, schema, selections, mock_s3, mock_dynamo
):
    """After the 6 PutObjects succeed, update_history is called exactly
    once per Selection (one update_item per selection in the list)."""
    publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
    )
    # history_writer.update_history calls update_item once per selection.
    assert mock_dynamo.update_item.call_count == len(selections)
    # The publish_id passed to update_history equals the version stamp.
    today = date.today().isoformat()
    expected_pid = f"v{today}"
    call = mock_dynamo.update_item.call_args
    eav = call.kwargs.get("ExpressionAttributeValues", {})
    assert eav.get(":pid", {}).get("S") == expected_pid


def test_11_no_sort_keys_true_in_source():
    """Pitfall 4: never sort_keys=True; insertion order is canonical."""
    src = Path("spotlight/publish.py").read_text()
    # No `sort_keys=True` substring anywhere in publish.py.
    assert re.search(r"sort_keys\s*=\s*True", src) is None


def test_12_spotlight_version_field_locked(
    valid_artifact, schema, selections, mock_s3, mock_dynamo
):
    publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
    )
    manifest_payload = None
    for call in mock_s3.put_object.call_args_list:
        key = call.args[0]
        body = call.args[1]
        if key.endswith("manifest.json"):
            manifest_payload = json.loads(body.decode("utf-8"))
            break
    assert manifest_payload is not None
    assert manifest_payload["spotlight_version"] == "spotlight_v1"


# ---------------------------------------------------------------------------
# Immutable per-run archive (run_id) — preserves every run's full output even
# when same-day re-publishes overwrite the date-keyed v{date}/ prefix.
# ---------------------------------------------------------------------------

def test_run_id_writes_immutable_archive(
    valid_artifact, schema, selections, mock_s3, mock_dynamo
):
    """With run_id set: the 6 version/latest puts PLUS 3 runs/{run_id}/ puts."""
    rc = publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
        run_id="2026-06-11T01-14-30Z",
    )
    assert rc == 0
    keys = [c.args[0] for c in mock_s3.put_object.call_args_list]
    assert len(keys) == 9
    assert "spotlight/runs/2026-06-11T01-14-30Z/spotlight.json" in keys
    assert "spotlight/runs/2026-06-11T01-14-30Z/manifest.json" in keys
    assert "spotlight/runs/2026-06-11T01-14-30Z/spotlight.schema.json" in keys


def test_no_run_id_skips_archive(
    valid_artifact, schema, selections, mock_s3, mock_dynamo
):
    """Default (run_id=None): unchanged — only the 6 version/latest puts."""
    rc = publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
    )
    assert rc == 0
    keys = [c.args[0] for c in mock_s3.put_object.call_args_list]
    assert len(keys) == 6
    assert not any("runs/" in k for k in keys)


def test_run_archive_failure_does_not_fail_publish(
    valid_artifact, schema, selections, mock_s3, mock_dynamo
):
    """A run-archive PutObject failure is best-effort: the publish (already
    written to version/latest) still returns 0, and history still advances."""
    def _fail_on_runs(key, body):
        if "/runs/" in key:
            raise RuntimeError("simulated archive failure")
        return None

    mock_s3.put_object.side_effect = _fail_on_runs
    rc = publish_artifact(
        artifact=valid_artifact,
        schema=schema,
        selections=selections,
        dry_run=False,
        s3_client=mock_s3,
        dynamo_client=mock_dynamo,
        run_id="2026-06-11T01-14-30Z",
    )
    assert rc == 0
    # the consumer-facing version/latest writes still happened
    keys = [c.args[0] for c in mock_s3.put_object.call_args_list]
    assert "spotlight/latest/spotlight.json" in keys


# §1.5 absolute-floor guard — a schema-valid but collapsed artifact (below the
# floor) must NOT overwrite latest/, even with no prior to relatively compare.
def test_13_below_floor_aborts_no_putobject(
    valid_artifact, schema, selections, mock_s3, mock_dynamo, capsys
):
    mock_s3.key_exists.return_value = False  # no prior: relative guard fails open
    collapsed = {**valid_artifact, "spotlights": valid_artifact["spotlights"][:1]}
    rc = publish_artifact(
        artifact=collapsed, schema=schema, selections=selections,
        dry_run=False, s3_client=mock_s3, dynamo_client=mock_dynamo,
    )
    assert rc == 1
    assert mock_s3.put_object.call_count == 0
    assert "absolute" in capsys.readouterr().out.lower()


def test_14_force_overrides_the_floor(
    valid_artifact, schema, selections, mock_s3, mock_dynamo
):
    mock_s3.key_exists.return_value = False
    collapsed = {**valid_artifact, "spotlights": valid_artifact["spotlights"][:1]}
    rc = publish_artifact(
        artifact=collapsed, schema=schema, selections=selections,
        dry_run=False, s3_client=mock_s3, dynamo_client=mock_dynamo, force=True,
    )
    assert rc == 0
    assert mock_s3.put_object.call_count == 6
