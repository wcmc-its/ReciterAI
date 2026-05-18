"""#80 Phase 2 (PR 5) — pipeline_onboarding.detector tests.

Covers the pure evaluator (gap partition, baseline-gated R9 churn, flagging
and ordering), the STAGE# row shape, the issue/digest formatters, the
rollup-baseline scan seam, and the run/handler I/O wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

import pipeline_onboarding.detector as det
from utils.sql_queries import FACULTY_GAP_SCAN_SQL


NOW = datetime(2026, 5, 17, 6, 0, 0, tzinfo=timezone.utc)


def _gap(cwid: str, pmid: str, has_synopsis: bool) -> dict:
    """One row as `scan_faculty_publication_gaps()` returns it."""
    return {"cwid": cwid, "pmid": pmid, "has_synopsis": has_synopsis}


def _finding(**overrides) -> det.CwidFinding:
    base = dict(
        cwid="abc1234",
        accepted_pmid_count=0,
        missing_synopsis=[],
        missing_score=[],
        churn_added=[],
        churn_removed=[],
        has_rollup_baseline=False,
    )
    base.update(overrides)
    return det.CwidFinding(**base)


class _FakeTable:
    """DynamoDB Table double — pages `scan`, records `put_item`."""

    def __init__(self, scan_pages=None):
        self._scan_pages = list(scan_pages or [])
        self.put_items: list[dict] = []

    def scan(self, **kwargs):
        if self._scan_pages:
            return self._scan_pages.pop(0)
        return {"Items": [], "LastEvaluatedKey": None}

    def put_item(self, Item):
        self.put_items.append(Item)
        return {}


# ---------------------------------------------------------------------------
# FACULTY_GAP_SCAN_SQL — author-position scope
# ---------------------------------------------------------------------------


def test_faculty_gap_scan_sql_scopes_to_first_last_author():
    """First/last is the v1 faculty-facing author scope (matches
    spotlight/author_resolver.py). Shape test — no behavioral test here
    executes the SQL, so nothing else guards the filter against silent loss."""
    assert "authorPosition IN ('first', 'last')" in FACULTY_GAP_SCAN_SQL


# ---------------------------------------------------------------------------
# evaluate_detector — gap partition
# ---------------------------------------------------------------------------


def test_missing_synopsis_flags_cwid():
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", False), _gap("c1", "2", True)],
        processing_status={"2": "complete"},
        rollup_baselines={},
        now=NOW,
    )
    assert ev.flagged_cwid_count == 1
    finding = ev.findings[0]
    assert finding.cwid == "c1"
    assert finding.missing_synopsis == ["1"]
    # PMID 2 has a synopsis and is complete — no gap.
    assert finding.missing_score == []


def test_missing_score_flags_cwid():
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", True), _gap("c1", "2", True)],
        processing_status={"1": "complete"},  # PMID 2 absent -> unscored
        rollup_baselines={},
        now=NOW,
    )
    finding = ev.findings[0]
    assert finding.missing_synopsis == []
    assert finding.missing_score == ["2"]


def test_fully_scored_cwid_is_not_flagged():
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", True), _gap("c1", "2", True)],
        processing_status={"1": "complete", "2": "complete"},
        rollup_baselines={},
        now=NOW,
    )
    assert ev.flagged_cwid_count == 0
    assert ev.scanned_cwid_count == 1


def test_quarantined_pmid_is_not_a_score_gap():
    """A quarantined PMID is terminally un-scoreable and already audited —
    the detector must not flag a CWID forever because of it."""
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", True)],
        processing_status={"1": "quarantined"},
        rollup_baselines={},
        now=NOW,
    )
    assert ev.flagged_cwid_count == 0


def test_failed_pmid_is_a_score_gap():
    """A `failed` PMID has no successful score yet — it is a real gap."""
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", True)],
        processing_status={"1": "failed"},
        rollup_baselines={},
        now=NOW,
    )
    assert ev.findings[0].missing_score == ["1"]


# ---------------------------------------------------------------------------
# evaluate_detector — R9 churn (baseline-gated)
# ---------------------------------------------------------------------------


def test_churn_added_and_removed_with_a_baseline():
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", True), _gap("c1", "2", True)],
        processing_status={"1": "complete", "2": "complete"},
        rollup_baselines={"c1": ["1", "9"]},  # 9 de-attributed, 2 newly attributed
        now=NOW,
    )
    finding = ev.findings[0]
    assert finding.churn_added == ["2"]
    assert finding.churn_removed == ["9"]
    assert finding.has_rollup_baseline is True


def test_no_baseline_means_churn_is_not_evaluated():
    """Q2: a CWID with no prior CWID-scoped rollup row is gap-scan-only.
    A fully-scored CWID with no baseline must NOT be flagged — treating
    'no rollup' as an empty baseline would flag every faculty CWID."""
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", True), _gap("c1", "2", True)],
        processing_status={"1": "complete", "2": "complete"},
        rollup_baselines={},  # c1 has no baseline
        now=NOW,
    )
    assert ev.flagged_cwid_count == 0


def test_new_researcher_without_baseline_still_caught_by_gap_scan():
    """A brand-new researcher (no rollup baseline, unscored publications) is
    still flagged — by the gap scan, not by churn."""
    ev = det.evaluate_detector(
        gap_rows=[_gap("new1", str(i), True) for i in range(80)],
        processing_status={},  # nothing scored
        rollup_baselines={},
        now=NOW,
    )
    assert ev.flagged_cwid_count == 1
    finding = ev.findings[0]
    assert len(finding.missing_score) == 80
    assert finding.has_rollup_baseline is False


def test_churn_removed_when_cwid_has_zero_current_publications():
    """A CWID with a baseline but no current accepted publications (ReCiter
    de-attributed everything) is still churn-evaluated, not silently dropped."""
    ev = det.evaluate_detector(
        gap_rows=[],
        processing_status={},
        rollup_baselines={"c1": ["1", "2"]},
        now=NOW,
    )
    assert ev.flagged_cwid_count == 1
    finding = ev.findings[0]
    assert finding.churn_removed == ["1", "2"]
    assert finding.churn_added == []
    assert finding.accepted_pmid_count == 0


def test_no_churn_when_set_matches_baseline():
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", True), _gap("c1", "2", True)],
        processing_status={"1": "complete", "2": "complete"},
        rollup_baselines={"c1": ["1", "2"]},
        now=NOW,
    )
    assert ev.flagged_cwid_count == 0


# ---------------------------------------------------------------------------
# evaluate_detector — ordering + totals
# ---------------------------------------------------------------------------


def test_findings_sorted_by_flag_weight_descending():
    ev = det.evaluate_detector(
        gap_rows=(
            [_gap("small", "1", False)]
            + [_gap("big", str(i), False) for i in range(10)]
        ),
        processing_status={},
        rollup_baselines={},
        now=NOW,
    )
    assert [f.cwid for f in ev.findings] == ["big", "small"]


def test_scanned_cwid_count_counts_all_cwids_seen():
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", False), _gap("c2", "2", True), _gap("c3", "3", True)],
        processing_status={"2": "complete", "3": "complete"},
        rollup_baselines={},
        now=NOW,
    )
    assert ev.scanned_cwid_count == 3
    assert ev.flagged_cwid_count == 1  # only c1 has a gap


# ---------------------------------------------------------------------------
# DetectorEvaluation.to_stage_record
# ---------------------------------------------------------------------------


def test_to_stage_record_shape():
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", False), _gap("c2", "2", True)],
        processing_status={},  # c2's PMID 2 has a synopsis but no score -> gap
        rollup_baselines={},
        now=NOW,
    )
    record = ev.to_stage_record(run_id="run-1", input_hash="hash-1", duration_ms=1234)
    assert record["PK"] == "STAGE#onboarding_detector#GLOBAL"
    assert record["SK"] == "RUN#2026-05-17T06:00:00Z"
    assert record["stage"] == "onboarding_detector"
    assert record["scope"] == "GLOBAL"
    assert record["status"] == "complete"
    assert record["record_type"] == "ONBOARDING_DETECTOR_RUN"
    assert record["run_id"] == "run-1"
    assert record["cost_observed_usd"] == Decimal("0")
    assert record["flagged_cwid_count"] == 2
    assert record["scanned_cwid_count"] == 2
    assert set(record["flagged_cwids"]) == {"c1", "c2"}


def test_to_stage_record_quiet_day_omits_flagged_cwids():
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", True)],
        processing_status={"1": "complete"},
        rollup_baselines={},
        now=NOW,
    )
    record = ev.to_stage_record(run_id="r", input_hash="h", duration_ms=0)
    assert record["flagged_cwid_count"] == 0
    assert "flagged_cwids" not in record


# ---------------------------------------------------------------------------
# Issue + digest formatters
# ---------------------------------------------------------------------------


def test_issue_title_backfill_shape():
    finding = _finding(
        missing_synopsis=["1", "2"], missing_score=["3"], accepted_pmid_count=3
    )
    assert (
        det._issue_title(finding)
        == "[onboarding] CWID abc1234 needs backfill (3 PMIDs)"
    )


def test_issue_title_drift_only_shape():
    finding = _finding(
        churn_added=["1"], churn_removed=["2", "3"],
        has_rollup_baseline=True, accepted_pmid_count=5,
    )
    assert (
        det._issue_title(finding)
        == "[onboarding] CWID abc1234 attribution drift (+1, -2 PMIDs)"
    )


def test_issue_body_embeds_one_trigger_command_and_no_dry_run():
    """Q1: the issue itself is the preview — exactly one start-execution
    command, no dry-run variant (PR 3's orchestrator has no dry_run mode)."""
    finding = _finding(missing_score=["10", "11"], accepted_pmid_count=2)
    body = det._issue_body(finding, now=NOW)
    assert body.count("aws stepfunctions start-execution") == 1
    assert "reciterai-onboarding" in body
    assert '"cwid": "abc1234"' in body
    assert "dry-run" not in body.lower() and "dry_run" not in body


def test_issue_body_notes_deferral_when_synopsis_missing():
    finding = _finding(
        missing_synopsis=["1"], missing_score=["2"], accepted_pmid_count=2
    )
    body = det._issue_body(finding, now=NOW)
    assert "defer" in body.lower()


def test_issue_body_lists_gap_pmids():
    finding = _finding(
        missing_synopsis=["111"], missing_score=["222"], accepted_pmid_count=2
    )
    body = det._issue_body(finding, now=NOW)
    assert "111" in body and "222" in body


def test_refresh_comment_summarizes_current_state():
    finding = _finding(missing_synopsis=["1", "2"], missing_score=["3"])
    comment = det._refresh_comment(finding, now=NOW)
    assert "2 missing synopsis" in comment
    assert "1 missing score" in comment
    assert "2026-05-17" in comment


# ---------------------------------------------------------------------------
# scan_rollup_baselines
# ---------------------------------------------------------------------------


def test_scan_rollup_baselines_takes_latest_complete_per_cwid():
    table = _FakeTable(scan_pages=[{
        "Items": [
            {"PK": "STAGE#rollup_by_cwid#cwid:c1", "SK": "RUN#2026-05-01T00:00:00Z",
             "status": "complete", "input_pmid_set": ["1"]},
            {"PK": "STAGE#rollup_by_cwid#cwid:c1", "SK": "RUN#2026-05-10T00:00:00Z",
             "status": "complete", "input_pmid_set": ["1", "2"]},  # newer
        ],
        "LastEvaluatedKey": None,
    }])
    assert det.scan_rollup_baselines(table) == {"c1": ["1", "2"]}


def test_scan_rollup_baselines_ignores_skipped_rows():
    """A skipped rollup row carries no input_pmid_set — the baseline is the
    most recent *complete* row."""
    table = _FakeTable(scan_pages=[{
        "Items": [
            {"PK": "STAGE#rollup_by_cwid#cwid:c1", "SK": "RUN#2026-05-20T00:00:00Z",
             "status": "skipped"},
            {"PK": "STAGE#rollup_by_cwid#cwid:c1", "SK": "RUN#2026-05-10T00:00:00Z",
             "status": "complete", "input_pmid_set": ["1"]},
        ],
        "LastEvaluatedKey": None,
    }])
    assert det.scan_rollup_baselines(table) == {"c1": ["1"]}


def test_scan_rollup_baselines_paginates():
    table = _FakeTable(scan_pages=[
        {"Items": [{"PK": "STAGE#rollup_by_cwid#cwid:c1", "SK": "RUN#1",
                    "status": "complete", "input_pmid_set": ["1"]}],
         "LastEvaluatedKey": {"k": 1}},
        {"Items": [{"PK": "STAGE#rollup_by_cwid#cwid:c2", "SK": "RUN#1",
                    "status": "complete", "input_pmid_set": ["2"]}],
         "LastEvaluatedKey": None},
    ])
    assert det.scan_rollup_baselines(table) == {"c1": ["1"], "c2": ["2"]}


# ---------------------------------------------------------------------------
# run_detector
# ---------------------------------------------------------------------------


def test_run_detector_writes_stage_row_files_issue_and_alerts(monkeypatch):
    table = _FakeTable()
    upserts: list[str] = []
    monkeypatch.setattr(det.github_issues, "list_open_issues", lambda **k: [])
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: True)
    monkeypatch.setattr(
        det.github_issues, "upsert_onboarding_issue",
        lambda cwid, title, body, **k: upserts.append(cwid)
        or {"action": "created", "issue": {"number": 1, "html_url": "u"}},
    )
    alerts: list = []
    monkeypatch.setattr(
        det.alerting, "alert", lambda *a, **k: alerts.append(a) or True
    )

    summary = det.run_detector(
        table=table,
        gap_rows=[_gap("c1", "1", False)],
        processing_status={},
        rollup_baselines={},
        run_id="run-1",
        now=NOW,
    )
    assert summary["flagged_cwid_count"] == 1
    assert summary["issues_created"] == 1
    assert upserts == ["c1"]
    assert len(table.put_items) == 1
    assert table.put_items[0]["PK"] == "STAGE#onboarding_detector#GLOBAL"
    assert summary["digest_sent"] is True
    assert alerts[0][0] == "WARN"
    assert "c1" in alerts[0][2]  # the digest message names the flagged CWID


def test_run_detector_quiet_day_writes_row_but_no_alert(monkeypatch):
    table = _FakeTable()
    monkeypatch.setattr(det.github_issues, "list_open_issues", lambda **k: [])
    alerts: list = []
    monkeypatch.setattr(
        det.alerting, "alert", lambda *a, **k: alerts.append(a) or True
    )
    summary = det.run_detector(
        table=table,
        gap_rows=[_gap("c1", "1", True)],
        processing_status={"1": "complete"},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    assert summary["flagged_cwid_count"] == 0
    assert alerts == []  # quiet day -> no Teams alert
    assert len(table.put_items) == 1
    assert table.put_items[0]["flagged_cwid_count"] == 0


def test_run_detector_posts_resolution_comment_for_now_clean_cwid(monkeypatch):
    """A CWID with an open onboarding issue that is clean today gets a
    one-time safe-to-close comment (OQ-3)."""
    table = _FakeTable()
    open_issue = {
        "number": 5,
        "title": "[onboarding] CWID done1 needs backfill (3 PMIDs)",
    }
    resolved: list[int] = []
    monkeypatch.setattr(det.github_issues, "list_open_issues", lambda **k: [open_issue])
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: False)
    monkeypatch.setattr(
        det.github_issues, "post_resolution_comment",
        lambda issue, body, **k: resolved.append(issue["number"]) or True,
    )
    monkeypatch.setattr(det.alerting, "alert", lambda *a, **k: True)

    summary = det.run_detector(
        table=table,
        gap_rows=[_gap("done1", "1", True)],     # done1 fully scored -> clean
        processing_status={"1": "complete"},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    assert resolved == [5]
    assert summary["resolution_comments"] == 1


def test_run_detector_survives_github_outage(monkeypatch):
    """A GitHub outage skips issue filing but still persists the STAGE# row."""
    table = _FakeTable()

    def boom(**kwargs):
        raise det.github_issues.GithubApiError("502 Bad Gateway")

    monkeypatch.setattr(det.github_issues, "list_open_issues", boom)
    monkeypatch.setattr(det.alerting, "alert", lambda *a, **k: True)

    summary = det.run_detector(
        table=table,
        gap_rows=[_gap("c1", "1", False)],
        processing_status={},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    assert summary["issues_created"] == 0
    assert summary["issues_available"] is False
    assert len(table.put_items) == 1  # STAGE# row still written


def test_run_detector_file_issues_false_never_touches_github(monkeypatch):
    table = _FakeTable()
    touched: list[str] = []
    monkeypatch.setattr(
        det.github_issues, "list_open_issues",
        lambda **k: touched.append("list") or [],
    )
    monkeypatch.setattr(det.alerting, "alert", lambda *a, **k: True)

    det.run_detector(
        table=table,
        gap_rows=[_gap("c1", "1", False)],
        processing_status={},
        rollup_baselines={},
        run_id="r",
        now=NOW,
        file_issues=False,
    )
    assert touched == []
    assert len(table.put_items) == 1


# ---------------------------------------------------------------------------
# handler — I/O wiring
# ---------------------------------------------------------------------------


def test_handler_wires_scan_evaluate_and_persist(monkeypatch):
    table = _FakeTable()
    monkeypatch.setattr(
        "utils.sql_queries.scan_faculty_publication_gaps",
        lambda: [_gap("c1", "1", False)],
    )
    monkeypatch.setattr("utils.dynamodb_helpers.get_table", lambda *a, **k: table)
    monkeypatch.setattr(
        "utils.dynamodb_helpers.get_dynamo_client", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        "utils.dynamodb_helpers.get_processing_status", lambda *a, **k: {}
    )
    monkeypatch.setattr(det, "scan_rollup_baselines", lambda table: {})
    monkeypatch.setattr(det.github_issues, "list_open_issues", lambda **k: [])
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: True)
    monkeypatch.setattr(
        det.github_issues, "upsert_onboarding_issue",
        lambda *a, **k: {"action": "created", "issue": {"number": 1}},
    )
    monkeypatch.setattr(det.alerting, "alert", lambda *a, **k: True)

    result = det.handler({"now": "2026-05-17T06:00:00Z", "run_id": "rx"})
    assert result["flagged_cwid_count"] == 1
    assert table.put_items[0]["PK"] == "STAGE#onboarding_detector#GLOBAL"
    assert table.put_items[0]["run_id"] == "rx"


def test_handler_writes_failed_row_then_reraises_on_scan_error(monkeypatch):
    table = _FakeTable()

    def boom():
        raise RuntimeError("mariadb unreachable")

    monkeypatch.setattr("utils.sql_queries.scan_faculty_publication_gaps", boom)
    monkeypatch.setattr("utils.dynamodb_helpers.get_table", lambda *a, **k: table)

    with pytest.raises(RuntimeError, match="mariadb unreachable"):
        det.handler({"now": "2026-05-17T06:00:00Z"})

    # A failed STAGE# row landed before the re-raise (observability).
    assert len(table.put_items) == 1
    assert table.put_items[0]["status"] == "failed"
    assert table.put_items[0]["PK"] == "STAGE#onboarding_detector#GLOBAL"


# ---------------------------------------------------------------------------
# Cold-start guard (#106)
# ---------------------------------------------------------------------------


def _flagging_gap_rows(n_cwids: int, pmids_each: int = 2) -> list[dict]:
    """`n_cwids` distinct CWIDs, each with `pmids_each` synopsis-less PMIDs —
    every CWID flags (a synopsis gap), for exercising the cold-start threshold."""
    return [
        _gap(f"cwid{c:04d}", f"{c:04d}{p}", False)
        for c in range(n_cwids)
        for p in range(pmids_each)
    ]


def test_thresholds_config_has_cold_start_key():
    """config/thresholds.json carries the cold-start threshold (#106). Shape
    test — guards the config key the guard's tuning depends on."""
    from utils.env_check import load_thresholds

    assert "onboarding_detector_cold_start_threshold" in load_thresholds()


def test_cold_start_threshold_reads_config_key(monkeypatch):
    monkeypatch.setattr(
        "utils.env_check.load_thresholds",
        lambda *a, **k: {"onboarding_detector_cold_start_threshold": 250},
    )
    assert det._cold_start_threshold() == 250


def test_cold_start_threshold_falls_back_on_unreadable_config(monkeypatch):
    """A missing/unreadable threshold degrades to the default — it never
    breaks a run (mirrors `_cost_guard_threshold`)."""

    def boom(*a, **k):
        raise FileNotFoundError("thresholds.json missing")

    monkeypatch.setattr("utils.env_check.load_thresholds", boom)
    assert det._cold_start_threshold() == det._DEFAULT_COLD_START_THRESHOLD


def test_to_stage_record_records_cold_start_mode():
    ev = det.evaluate_detector(
        gap_rows=[_gap("c1", "1", False)],
        processing_status={},
        rollup_baselines={},
        now=NOW,
    )
    on = ev.to_stage_record(run_id="r", input_hash="h", duration_ms=0, cold_start=True)
    off = ev.to_stage_record(run_id="r", input_hash="h", duration_ms=0)
    assert on["cold_start_mode"] is True
    assert off["cold_start_mode"] is False


def test_digest_issue_body_carries_totals_and_pointers():
    ev = det.evaluate_detector(
        gap_rows=_flagging_gap_rows(3, pmids_each=2),
        processing_status={},
        rollup_baselines={},
        now=NOW,
    )
    body = det._digest_issue_body(ev, now=NOW, threshold=2)
    assert "3 CWID(s)" in body
    assert "threshold of **2**" in body
    assert "#112" in body and "#106" in body          # backfill + this issue
    assert "Most-affected CWIDs" in body
    assert "cwid0000" in body and "cwid0002" in body   # the full flagged list
    assert "reciterai-onboarding-detector-daily" in body


def test_digest_cleared_body_has_marker_and_safe_to_close():
    ev = det.evaluate_detector(
        gap_rows=[], processing_status={}, rollup_baselines={}, now=NOW
    )
    body = det._digest_cleared_body(ev, now=NOW)
    assert det._DIGEST_CLEARED_MARKER in body
    assert "Safe to close" in body


def test_run_detector_cold_start_files_digest_not_per_cwid(monkeypatch):
    """Above the threshold: one digest issue, zero per-CWID issues."""
    monkeypatch.setattr(det, "_cold_start_threshold", lambda: 2)
    table = _FakeTable()
    per_cwid: list[str] = []
    created: list[str] = []
    monkeypatch.setattr(det.github_issues, "list_open_issues", lambda **k: [])
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: True)
    monkeypatch.setattr(
        det.github_issues, "upsert_onboarding_issue",
        lambda cwid, *a, **k: per_cwid.append(cwid)
        or {"action": "created", "issue": {}},
    )
    monkeypatch.setattr(
        det.github_issues, "create_issue",
        lambda title, body, **k: created.append(title)
        or {"number": 42, "html_url": "https://gh/issues/42"},
    )
    alerts: list = []
    monkeypatch.setattr(
        det.alerting, "alert", lambda *a, **k: alerts.append(a) or True
    )

    summary = det.run_detector(
        table=table,
        gap_rows=_flagging_gap_rows(5),  # 5 flagged CWIDs > threshold 2
        processing_status={},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    assert summary["cold_start_mode"] is True
    assert summary["flagged_cwid_count"] == 5
    assert per_cwid == []                            # no per-CWID flood
    assert created == [det._DIGEST_ISSUE_TITLE]      # exactly one digest issue
    assert summary["issues_created"] == 0
    assert summary["digest_issue_number"] == 42
    # The STAGE# row records the mode + the digest issue number.
    row = table.put_items[0]
    assert row["cold_start_mode"] is True
    assert row["digest_issue_number"] == 42
    # The Teams digest still fires, flagged as a cold-start backlog.
    assert summary["digest_sent"] is True
    assert "cold-start" in alerts[0][1].lower()


def test_run_detector_cold_start_refreshes_existing_digest(monkeypatch):
    """A later cold-start run refreshes the existing digest, not a new one."""
    monkeypatch.setattr(det, "_cold_start_threshold", lambda: 2)
    table = _FakeTable()
    existing = {
        "number": 7,
        "title": det._DIGEST_ISSUE_TITLE,
        "body": "stale",
        "updated_at": "2026-05-17T00:00:00Z",
    }
    updated: list[int] = []
    monkeypatch.setattr(
        det.github_issues, "list_open_issues", lambda **k: [existing]
    )
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: False)
    monkeypatch.setattr(
        det.github_issues, "create_issue",
        lambda *a, **k: pytest.fail("cold-start should refresh, not create"),
    )
    monkeypatch.setattr(
        det.github_issues, "update_issue",
        lambda number, **k: updated.append(number)
        or {"number": number, "html_url": "u"},
    )
    monkeypatch.setattr(det.alerting, "alert", lambda *a, **k: True)

    summary = det.run_detector(
        table=table,
        gap_rows=_flagging_gap_rows(4),
        processing_status={},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    assert summary["cold_start_mode"] is True
    assert updated == [7]
    assert summary["digest_issue_number"] == 7


def test_run_detector_cold_start_still_runs_resolution_pass(monkeypatch):
    """A now-clean CWID's prior individual issue still gets the safe-to-close
    comment in cold-start mode; the digest issue itself is skipped (no CWID)."""
    monkeypatch.setattr(det, "_cold_start_threshold", lambda: 2)
    table = _FakeTable()
    open_issues = [
        {"number": 9, "title": "[onboarding] CWID clean1 needs backfill (2 PMIDs)"},
        {"number": 10, "title": det._DIGEST_ISSUE_TITLE, "body": ""},
    ]
    resolved: list[int] = []
    monkeypatch.setattr(
        det.github_issues, "list_open_issues", lambda **k: open_issues
    )
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: False)
    monkeypatch.setattr(
        det.github_issues, "create_issue",
        lambda *a, **k: {"number": 1, "html_url": "u"},
    )
    monkeypatch.setattr(
        det.github_issues, "update_issue", lambda number, **k: {"number": number}
    )
    monkeypatch.setattr(
        det.github_issues, "post_resolution_comment",
        lambda issue, body, **k: resolved.append(issue["number"]) or True,
    )
    monkeypatch.setattr(det.alerting, "alert", lambda *a, **k: True)

    det.run_detector(
        table=table,
        gap_rows=_flagging_gap_rows(5),  # clean1 is not in this flagged set
        processing_status={},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    # CWID clean1's stale issue resolved; the digest issue (#10) skipped.
    assert resolved == [9]


def test_run_detector_at_threshold_stays_in_normal_mode(monkeypatch):
    """The guard trips strictly above the threshold — exactly `threshold`
    flagged CWIDs still files per-CWID issues."""
    monkeypatch.setattr(det, "_cold_start_threshold", lambda: 3)
    table = _FakeTable()
    per_cwid: list[str] = []
    monkeypatch.setattr(det.github_issues, "list_open_issues", lambda **k: [])
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: True)
    monkeypatch.setattr(
        det.github_issues, "upsert_onboarding_issue",
        lambda cwid, *a, **k: per_cwid.append(cwid)
        or {"action": "created", "issue": {"number": len(per_cwid)}},
    )
    monkeypatch.setattr(
        det.github_issues, "create_issue",
        lambda *a, **k: pytest.fail("3 == threshold must not trip cold-start"),
    )
    monkeypatch.setattr(det.alerting, "alert", lambda *a, **k: True)

    summary = det.run_detector(
        table=table,
        gap_rows=_flagging_gap_rows(3),  # exactly the threshold
        processing_status={},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    assert summary["cold_start_mode"] is False
    assert len(per_cwid) == 3
    assert summary["issues_created"] == 3


def test_run_detector_normal_mode_clears_lingering_digest(monkeypatch):
    """Below the threshold, a digest issue left from a cold-start period is
    marked cleared — its body PATCHed once."""
    table = _FakeTable()  # real threshold 100; 1 flagged CWID -> normal mode
    digest = {
        "number": 7,
        "title": det._DIGEST_ISSUE_TITLE,
        "body": "active digest",
    }
    patched: list[tuple] = []
    monkeypatch.setattr(det.github_issues, "list_open_issues", lambda **k: [digest])
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: False)
    monkeypatch.setattr(
        det.github_issues, "upsert_onboarding_issue",
        lambda *a, **k: {"action": "created", "issue": {"number": 1}},
    )
    monkeypatch.setattr(
        det.github_issues, "update_issue",
        lambda number, *, body=None, **k: patched.append((number, body))
        or {"number": number},
    )
    monkeypatch.setattr(det.alerting, "alert", lambda *a, **k: True)

    summary = det.run_detector(
        table=table,
        gap_rows=[_gap("c1", "1", False)],
        processing_status={},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    assert summary["cold_start_mode"] is False
    assert len(patched) == 1
    assert patched[0][0] == 7
    assert det._DIGEST_CLEARED_MARKER in patched[0][1]


def test_run_detector_normal_mode_skips_already_cleared_digest(monkeypatch):
    """An already-cleared digest issue is not re-PATCHed on later normal runs."""
    table = _FakeTable()
    digest = {
        "number": 7,
        "title": det._DIGEST_ISSUE_TITLE,
        "body": f"cleared\n{det._DIGEST_CLEARED_MARKER}",
    }
    patched: list[int] = []
    monkeypatch.setattr(det.github_issues, "list_open_issues", lambda **k: [digest])
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: False)
    monkeypatch.setattr(
        det.github_issues, "upsert_onboarding_issue",
        lambda *a, **k: {"action": "created", "issue": {"number": 1}},
    )
    monkeypatch.setattr(
        det.github_issues, "update_issue",
        lambda number, **k: patched.append(number) or {"number": number},
    )
    monkeypatch.setattr(det.alerting, "alert", lambda *a, **k: True)

    det.run_detector(
        table=table,
        gap_rows=[_gap("c1", "1", False)],
        processing_status={},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    assert patched == []  # already cleared -> no PATCH


def test_run_detector_cold_start_survives_digest_upsert_failure(monkeypatch):
    """A GitHub error filing the digest is logged; the STAGE# row and Teams
    digest still land."""
    monkeypatch.setattr(det, "_cold_start_threshold", lambda: 2)
    table = _FakeTable()

    def boom(*a, **k):
        raise det.github_issues.GithubApiError("503 Service Unavailable")

    monkeypatch.setattr(det.github_issues, "list_open_issues", lambda **k: [])
    monkeypatch.setattr(det.github_issues, "ensure_label", lambda **k: True)
    monkeypatch.setattr(det.github_issues, "create_issue", boom)
    alerts: list = []
    monkeypatch.setattr(
        det.alerting, "alert", lambda *a, **k: alerts.append(a) or True
    )

    summary = det.run_detector(
        table=table,
        gap_rows=_flagging_gap_rows(6),
        processing_status={},
        rollup_baselines={},
        run_id="r",
        now=NOW,
    )
    assert summary["cold_start_mode"] is True
    assert summary["digest_issue_number"] is None
    assert len(table.put_items) == 1               # STAGE# row still written
    assert table.put_items[0]["cold_start_mode"] is True
    assert "digest_issue_number" not in table.put_items[0]
    assert alerts and alerts[0][0] == "WARN"       # Teams digest still sent
