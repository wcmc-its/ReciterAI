"""Tests for the run_daily_enrichment CLI — daily vs enrichment-backfill
dispatch and flag-combination validation (#112)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipeline_enrichment.daily_job import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NO_OP,
    EnrichmentBackfillResult,
    RunResult,
)
from scripts.run_daily_enrichment import main


@pytest.fixture(autouse=True)
def _stub_engine():
    """main() builds an engine before dispatch; stub it for every test."""
    with patch(
        "scripts.run_daily_enrichment.get_engine", return_value=MagicMock()
    ):
        yield


def _patch_daily(result=None):
    return patch(
        "scripts.run_daily_enrichment.run_daily_enrichment",
        return_value=result or RunResult(status=STATUS_COMPLETE, delta_size=0),
    )


def _patch_backfill(result=None):
    return patch(
        "scripts.run_daily_enrichment.run_enrichment_backfill",
        return_value=result or EnrichmentBackfillResult(status=STATUS_COMPLETE),
    )


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------

def test_no_flags_runs_the_daily_job():
    with _patch_daily() as daily, _patch_backfill() as backfill:
        rc = main([])
    assert rc == 0
    daily.assert_called_once()
    backfill.assert_not_called()


def test_pmids_flag_dispatches_to_the_backfill():
    with _patch_daily() as daily, _patch_backfill() as backfill:
        rc = main(["--pmids", "39001234, 39005678 ,39001234"])
    assert rc == 0
    daily.assert_not_called()
    backfill.assert_called_once()
    # Comma-split and stripped; the CLI passes the set through as-is
    # (run_enrichment_backfill owns de-duplication).
    assert backfill.call_args.kwargs["pmids"] == [
        "39001234", "39005678", "39001234",
    ]


def test_from_gap_scan_uses_only_synopsis_missing_pmids():
    gap_rows = [
        {"cwid": "a", "pmid": "300", "has_synopsis": False},
        {"cwid": "a", "pmid": "100", "has_synopsis": False},
        {"cwid": "b", "pmid": "100", "has_synopsis": False},  # duplicate PMID
        {"cwid": "b", "pmid": "200", "has_synopsis": True},   # already has one
    ]
    with _patch_backfill() as backfill, patch(
        "utils.sql_queries.scan_faculty_publication_gaps", return_value=gap_rows
    ):
        rc = main(["--from-gap-scan"])
    assert rc == 0
    # Synopsis-less PMIDs only, de-duplicated and sorted.
    assert backfill.call_args.kwargs["pmids"] == ["100", "300"]


def test_dry_run_and_force_flags_pass_through_to_the_backfill():
    with _patch_backfill() as backfill:
        main(["--pmids", "1", "--dry-run", "--force"])
    kwargs = backfill.call_args.kwargs
    assert kwargs["dry_run"] is True
    assert kwargs["force"] is True


# ---------------------------------------------------------------------------
# Flag-combination validation
# ---------------------------------------------------------------------------

def test_pmids_and_from_gap_scan_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main(["--pmids", "1", "--from-gap-scan"])


def test_dry_run_requires_backfill_mode():
    with pytest.raises(SystemExit):
        main(["--dry-run"])


def test_force_requires_backfill_mode():
    with pytest.raises(SystemExit):
        main(["--force"])


def test_daily_cost_flags_are_rejected_in_backfill_mode():
    with pytest.raises(SystemExit):
        main(["--pmids", "1", "--full"])


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

def test_exit_code_is_one_on_backfill_failure():
    failed = EnrichmentBackfillResult(
        status=STATUS_FAILED, attempted=1, failed=1
    )
    with _patch_backfill(failed):
        assert main(["--pmids", "1"]) == 1


def test_exit_code_is_zero_on_backfill_no_op():
    with _patch_backfill(EnrichmentBackfillResult(status=STATUS_NO_OP)):
        assert main(["--pmids", "1"]) == 0
