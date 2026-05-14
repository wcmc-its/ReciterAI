"""Unit tests for the daily enrichment orchestrator (#37 step 2)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from pipeline_enrichment import daily_job
from pipeline_enrichment.daily_job import (
    STATUS_COMPLETE,
    STATUS_COST_GUARD_TRIPPED,
    STATUS_DDB_BATCH_FAILED,
    STATUS_FAILED,
    STATUS_NO_OP,
    PmidOutcome,
    RunResult,
    run_daily_enrichment,
)
from pipeline_enrichment.watermark import Watermark


# Defensive default: ensure tests never fire real Teams alerts even if a
# developer happens to have RECITERAI_TEAMS_WEBHOOK_URL set in their shell.
@pytest.fixture(autouse=True)
def _no_real_webhook(monkeypatch):
    monkeypatch.delenv("RECITERAI_TEAMS_WEBHOOK_URL", raising=False)


# Step 3 PR 3.1: orchestrator's end-of-run DDB batch falls back to
# get_table() when ddb_table=None. Without this autouse stub it would hit
# boto3 / AWS during every happy-path test. The MagicMock natively supports
# `with mock.batch_writer() as batch: batch.put_item(...)` because
# MagicMock implements __enter__/__exit__.
@pytest.fixture(autouse=True)
def _stub_default_ddb_table():
    with patch.object(daily_job, "_default_get_table") as fake:
        fake.return_value = MagicMock(name="fake_ddb_table")
        yield fake


# ---------------------------------------------------------------------------
# Fake Synopsis/Impact results — mirror the real dataclasses' relevant fields
# ---------------------------------------------------------------------------

@dataclass
class FakeSynopsisResult:
    pmid: str
    synopsis: Optional[str]
    model: str = "gpt-5.1"
    input_tokens: int = 100
    output_tokens: int = 30
    error: Optional[str] = None


@dataclass
class FakeImpactResult:
    pmid: str
    impact_score: Optional[int]
    justification: Optional[str] = None
    model: str = "gpt-5.1"
    prompt_version: str = "v2"
    input_tokens: int = 200
    output_tokens: int = 80
    error: Optional[str] = None


def _ok_synopsis(*, pmid, title, journal=None, year=None, abstract=None, client=None):
    return FakeSynopsisResult(pmid=pmid, synopsis=f"synopsis for {pmid}")


def _ok_impact(*, pub_data, client=None):
    return FakeImpactResult(
        pmid=str(pub_data["pmid"]),
        impact_score=50,
        justification="ok",
    )


def _failing_synopsis(*, pmid, title, journal=None, year=None, abstract=None, client=None):
    # Real synopsis.py sets tokens=0 on error (the call failed before usage
    # was recorded). Mirror that here so cost accumulator tests are accurate.
    return FakeSynopsisResult(
        pmid=pmid, synopsis=None, input_tokens=0, output_tokens=0, error="boom",
    )


def _failing_impact(*, pub_data, client=None):
    return FakeImpactResult(
        pmid=str(pub_data["pmid"]),
        impact_score=None,
        input_tokens=0,
        output_tokens=0,
        error="boom",
    )


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

def _delta_rows(pmids):
    return [
        {
            "pmid": p,
            "articleTitle": f"title {p}",
            "journalTitleVerbose": "J Sci",
            "articleYear": 2025,
            "datePublicationAddedToEntrez": "2025-01-01",
            "citationCountNIH": 0,
            "percentileNIH": None,
            "relativeCitationRatioNIH": None,
            "abstractVarchar": f"abstract {p}",
        }
        for p in pmids
    ]


@pytest.fixture
def fake_writer():
    """Patch the mariadb_writer module functions to no-ops returning entity_id=1."""
    with patch.object(daily_job.mariadb_writer, "ensure_entity", return_value=1) as ensure_e, \
         patch.object(daily_job.mariadb_writer, "upsert_synopsis", return_value=None) as up_s, \
         patch.object(daily_job.mariadb_writer, "upsert_impact", return_value=None) as up_i:
        yield {"ensure": ensure_e, "synopsis": up_s, "impact": up_i}


@pytest.fixture
def fake_watermark():
    """Patch watermark.* with mocks. Default: never-run state (None)."""
    with patch.object(daily_job.wm, "read_watermark", return_value=None) as r, \
         patch.object(daily_job.wm, "mark_run_started", return_value="run-xyz") as s, \
         patch.object(daily_job.wm, "mark_run_complete", return_value="2026-05-14T00:00:00Z") as c, \
         patch.object(daily_job.wm, "mark_run_failed", return_value=None) as f:
        yield {"read": r, "started": s, "complete": c, "failed": f}


@pytest.fixture
def fake_engine():
    return MagicMock(name="engine")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_three_pmids_all_succeed_advances_watermark(fake_engine, fake_watermark, fake_writer):
    delta = _delta_rows([1001, 1002, 1003])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )

    assert result.status == STATUS_COMPLETE
    assert result.delta_size == 3
    assert result.successes == 3
    assert result.new_watermark_pmid == 1003
    # Watermark transitions: started → complete; never marked failed.
    fake_watermark["started"].assert_called_once()
    fake_watermark["complete"].assert_called_once()
    fake_watermark["failed"].assert_not_called()
    complete_kwargs = fake_watermark["complete"].call_args.kwargs
    assert complete_kwargs["max_pmid"] == 1003


def test_first_run_with_no_watermark_uses_zero_as_last_max(fake_engine, fake_watermark, fake_writer):
    """read_watermark returns None on first-ever run; orchestrator passes 0
    to fetch_new_publications so the whole corpus is in scope (subject to
    cost guard / --full)."""
    with patch.object(daily_job, "fetch_new_publications", return_value=[]) as fetch:
        run_daily_enrichment(engine=fake_engine)
    fetch.assert_called_once()
    assert fetch.call_args.kwargs["last_max_pmid"] == 0


def test_watermark_with_null_max_pmid_treated_as_zero(fake_engine, fake_watermark, fake_writer):
    """A failed-then-never-completed watermark may have last_run_status set
    but last_successful_max_pmid still None. Don't crash on that."""
    fake_watermark["read"].return_value = Watermark(
        last_successful_run_at=None,
        last_successful_max_pmid=None,
        last_run_started_at="2026-01-01T00:00:00Z",
        last_run_status="failed",
    )
    with patch.object(daily_job, "fetch_new_publications", return_value=[]) as fetch:
        run_daily_enrichment(engine=fake_engine)
    assert fetch.call_args.kwargs["last_max_pmid"] == 0


# ---------------------------------------------------------------------------
# Empty delta
# ---------------------------------------------------------------------------

def test_empty_delta_returns_no_op_without_touching_watermark(fake_engine, fake_watermark, fake_writer):
    with patch.object(daily_job, "fetch_new_publications", return_value=[]):
        result = run_daily_enrichment(engine=fake_engine)

    assert result.status == STATUS_NO_OP
    assert result.delta_size == 0
    assert result.new_watermark_pmid is None
    # No watermark mutations on empty delta.
    fake_watermark["started"].assert_not_called()
    fake_watermark["complete"].assert_not_called()
    fake_watermark["failed"].assert_not_called()


# ---------------------------------------------------------------------------
# Cost guard
# ---------------------------------------------------------------------------

def test_cost_guard_trips_marks_failed_without_invoking_llm(fake_engine, fake_watermark, fake_writer):
    """Anomalously large delta → refused before any LLM call."""
    huge_delta = _delta_rows(list(range(1, 3500)))  # 3499 pmids × $0.010 = $34.99 > $30
    syn = MagicMock(side_effect=AssertionError("LLM must not be called"))
    imp = MagicMock(side_effect=AssertionError("LLM must not be called"))
    with patch.object(daily_job, "fetch_new_publications", return_value=huge_delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=syn,
            score_impact=imp,
        )
    assert result.status == STATUS_COST_GUARD_TRIPPED
    assert result.cost_estimate is not None
    assert result.cost_estimate.delta_size == 3499
    syn.assert_not_called()
    imp.assert_not_called()
    # Trip is recorded on the watermark (started + failed), but
    # last_successful_max_pmid is NOT advanced.
    fake_watermark["started"].assert_called_once()
    fake_watermark["failed"].assert_called_once()
    fake_watermark["complete"].assert_not_called()


def test_full_flag_bypasses_cost_guard(fake_engine, fake_watermark, fake_writer):
    """Annual rescore / cold-start backfill path."""
    huge_delta = _delta_rows(list(range(1, 3500)))  # well past trip count
    with patch.object(daily_job, "fetch_new_publications", return_value=huge_delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            full=True,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert result.status == STATUS_COMPLETE
    assert result.successes == 3499
    # Cost estimate is still computed for diagnostics.
    assert result.cost_estimate is not None
    assert result.cost_estimate.delta_size == 3499


def test_custom_threshold_and_per_paper_overrides_are_honored(fake_engine, fake_watermark, fake_writer):
    """Operator can widen the threshold for a planned catch-up run."""
    delta = _delta_rows([1, 2, 3])  # tiny; doesn't trip default
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            threshold_usd=Decimal("0.05"),  # absurd low threshold
            per_paper_usd=Decimal("0.10"),  # 3 × 0.10 = 0.30 > 0.05
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert result.status == STATUS_COST_GUARD_TRIPPED


# ---------------------------------------------------------------------------
# Failure semantics
# ---------------------------------------------------------------------------

def test_synopsis_failure_skips_impact_for_that_pmid(fake_engine, fake_watermark, fake_writer):
    delta = _delta_rows([2001])
    impact_mock = MagicMock(wraps=_ok_impact)
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_failing_synopsis,
            score_impact=impact_mock,
        )
    assert result.status == STATUS_FAILED
    assert len(result.outcomes) == 1
    o = result.outcomes[0]
    assert o.synopsis_ok is False
    assert o.impact_ok is False
    assert "boom" in (o.synopsis_error or "")
    # impact must not have been called for a pmid whose synopsis failed.
    impact_mock.assert_not_called()
    fake_writer["impact"].assert_not_called()


def test_impact_failure_leaves_synopsis_row_in_place_but_run_fails(fake_engine, fake_watermark, fake_writer):
    delta = _delta_rows([3001])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_failing_impact,
        )
    assert result.status == STATUS_FAILED
    o = result.outcomes[0]
    assert o.synopsis_ok is True   # synopsis succeeded + wrote
    assert o.impact_ok is False
    # Run as a whole is failed, so watermark is NOT advanced.
    fake_watermark["failed"].assert_called_once()
    fake_watermark["complete"].assert_not_called()


def test_one_failure_among_many_does_not_stop_iteration(fake_engine, fake_watermark, fake_writer):
    """The orchestrator must process every pmid even after a failure, so
    the operator gets a complete report instead of a partial one."""
    delta = _delta_rows([1, 2, 3, 4, 5])

    def synopsis_failing_on_3(*, pmid, title, journal=None, year=None, abstract=None, client=None):
        if pmid == "3":
            return FakeSynopsisResult(
                pmid=pmid, synopsis=None,
                input_tokens=0, output_tokens=0, error="bad abstract",
            )
        return FakeSynopsisResult(pmid=pmid, synopsis=f"syn-{pmid}")

    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=synopsis_failing_on_3,
            score_impact=_ok_impact,
        )

    assert result.status == STATUS_FAILED
    assert len(result.outcomes) == 5  # all five attempted
    # pmid 3 failed; the other four are fine but the run is failed overall.
    by_pmid = {o.pmid: o for o in result.outcomes}
    assert by_pmid["3"].synopsis_ok is False
    for p in ("1", "2", "4", "5"):
        assert by_pmid[p].fully_succeeded
    # Watermark does NOT advance even partially.
    fake_watermark["complete"].assert_not_called()
    fake_watermark["failed"].assert_called_once()


def test_mariadb_synopsis_write_failure_marks_synopsis_failed(fake_engine, fake_watermark):
    """Distinct from LLM-call failure: synopsis returned content but the
    DB write blew up."""
    delta = _delta_rows([4001])
    with patch.object(daily_job.mariadb_writer, "ensure_entity", return_value=1), \
         patch.object(
             daily_job.mariadb_writer, "upsert_synopsis",
             side_effect=RuntimeError("connection lost"),
         ), \
         patch.object(daily_job.mariadb_writer, "upsert_impact") as up_i, \
         patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    o = result.outcomes[0]
    assert o.synopsis_ok is False
    assert "connection lost" in o.synopsis_error
    # Impact must not have been attempted.
    up_i.assert_not_called()
    assert result.status == STATUS_FAILED


def test_ensure_entity_failure_blocks_synopsis_and_impact(fake_engine, fake_watermark):
    delta = _delta_rows([5001])
    with patch.object(
        daily_job.mariadb_writer, "ensure_entity",
        side_effect=RuntimeError("entity table locked"),
    ), \
         patch.object(daily_job.mariadb_writer, "upsert_synopsis") as up_s, \
         patch.object(daily_job.mariadb_writer, "upsert_impact") as up_i, \
         patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    up_s.assert_not_called()
    up_i.assert_not_called()
    assert result.status == STATUS_FAILED
    assert "entity table locked" in result.outcomes[0].synopsis_error


# ---------------------------------------------------------------------------
# Watermark interactions
# ---------------------------------------------------------------------------

def test_mark_run_complete_called_with_max_pmid_of_delta(fake_engine, fake_watermark, fake_writer):
    delta = _delta_rows([100, 200, 50])  # unordered just in case
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    kwargs = fake_watermark["complete"].call_args.kwargs
    assert kwargs["max_pmid"] == 200


def test_run_id_from_mark_started_propagates_to_result(fake_engine, fake_watermark, fake_writer):
    fake_watermark["started"].return_value = "abcd-1234"
    delta = _delta_rows([7777])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert result.run_id == "abcd-1234"


# ---------------------------------------------------------------------------
# Alerting behaviour
# ---------------------------------------------------------------------------

def test_alert_fires_on_cost_guard_trip(fake_engine, fake_watermark, fake_writer):
    huge_delta = _delta_rows(list(range(1, 4000)))  # 3999 pmids × $0.010 = $39.99 > $30
    alert_mock = MagicMock(return_value=True)
    with patch.object(daily_job, "fetch_new_publications", return_value=huge_delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=MagicMock(side_effect=AssertionError),
            score_impact=MagicMock(side_effect=AssertionError),
            alert_fn=alert_mock,
        )
    assert result.status == "cost_guard_tripped"
    alert_mock.assert_called_once()
    severity, title, message = alert_mock.call_args.args[:3]
    assert severity == "ERROR"
    assert "Cost guard" in title
    assert "--full" in message  # tells operator how to bypass
    context = alert_mock.call_args.kwargs.get("context") or (
        alert_mock.call_args.args[3] if len(alert_mock.call_args.args) > 3 else {}
    )
    assert context["delta_size"] == 3999
    assert "estimated_usd" in context
    assert "threshold_usd" in context


def test_alert_fires_on_run_failure(fake_engine, fake_watermark, fake_writer):
    delta = _delta_rows([10, 20, 30])
    alert_mock = MagicMock(return_value=True)

    def syn_failing_on_20(*, pmid, **kwargs):
        if pmid == "20":
            return FakeSynopsisResult(
                pmid=pmid, synopsis=None,
                input_tokens=0, output_tokens=0, error="bad abstract",
            )
        return FakeSynopsisResult(pmid=pmid, synopsis=f"syn-{pmid}")

    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=syn_failing_on_20,
            score_impact=_ok_impact,
            alert_fn=alert_mock,
        )
    assert result.status == "failed"
    alert_mock.assert_called_once()
    severity, title, _message = alert_mock.call_args.args[:3]
    assert severity == "ERROR"
    assert "failed" in title.lower()
    context = alert_mock.call_args.kwargs.get("context") or {}
    assert context["delta_size"] == 3
    assert context["failures"] == 1
    # Sample failures must name the offending pmid so the alert is actionable.
    assert "20" in context["sample_failures"]


def test_no_alert_on_full_success(fake_engine, fake_watermark, fake_writer):
    """Successful runs don't ping the operator — silent success is the norm
    for cron jobs."""
    delta = _delta_rows([100, 200, 300])
    alert_mock = MagicMock()
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
            alert_fn=alert_mock,
        )
    assert result.status == "complete"
    alert_mock.assert_not_called()


def test_no_alert_on_no_op_empty_delta(fake_engine, fake_watermark, fake_writer):
    """Empty delta = nothing happened. Operators don't need a daily 'all
    quiet' notification."""
    alert_mock = MagicMock()
    with patch.object(daily_job, "fetch_new_publications", return_value=[]):
        result = run_daily_enrichment(engine=fake_engine, alert_fn=alert_mock)
    assert result.status == "no_op"
    alert_mock.assert_not_called()


def test_default_alert_fn_is_alerting_module_function():
    """If the caller doesn't inject alert_fn, the orchestrator's default
    points at pipeline_enrichment.alerting.alert (which is a no-op when
    the webhook env is unset, so safe by default)."""
    import inspect
    from pipeline_enrichment import alerting as _alerting

    sig = inspect.signature(run_daily_enrichment)
    assert sig.parameters["alert_fn"].default is _alerting.alert


# ---------------------------------------------------------------------------
# CostAccumulator wiring
# ---------------------------------------------------------------------------

# GPT-5.1 prices from config/llm_prices.yaml (Batch tier, 2026-05-13):
#   input  $1.25/Mtok   output $10.00/Mtok
# FakeSynopsisResult: 100 in / 30 out → 100*1.25/1M + 30*10/1M = $0.000425
# FakeImpactResult:   200 in / 80 out → 200*1.25/1M + 80*10/1M = $0.001050
# Per-pmid total: $0.001475
_EXPECTED_PER_PMID_USD = Decimal("0.001475")


def test_cost_observed_usd_sums_across_pmids(fake_engine, fake_watermark, fake_writer):
    delta = _delta_rows([1, 2, 3])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
            alert_fn=MagicMock(),
        )
    assert result.status == "complete"
    assert result.cost_observed_usd == _EXPECTED_PER_PMID_USD * 3


def test_cost_summary_present_on_complete(fake_engine, fake_watermark, fake_writer):
    delta = _delta_rows([1, 2])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
            alert_fn=MagicMock(),
        )
    summary = result.cost_summary
    assert summary is not None
    # 2 pmids × 2 calls each = 4 calls.
    assert summary["call_count"] == 4
    assert summary["total_input_tokens"] == 2 * (100 + 200)
    assert summary["total_output_tokens"] == 2 * (30 + 80)
    # by_model groups under the price-table key, not the dated response model.
    assert "gpt-5.1" in summary["by_model"]
    assert "gpt-5.1-2025-11-13" not in summary["by_model"]


def test_cost_observed_recorded_even_on_failed_run(fake_engine, fake_watermark, fake_writer):
    """A pmid that succeeded its synopsis but failed its impact still cost
    money. Cost reporting must reflect that — silent on-failure cost is
    the worst kind of cost."""
    delta = _delta_rows([1])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_failing_impact,  # returns error result; tokens=0
            alert_fn=MagicMock(),
        )
    assert result.status == "failed"
    # Synopsis succeeded → tokens recorded. Impact returned a result with
    # tokens=0 (error path) → no impact tokens contributed.
    assert result.cost_observed_usd == Decimal("0.000425")  # synopsis only


def test_cost_observed_is_none_on_cost_guard_tripped(fake_engine, fake_watermark, fake_writer):
    """Cost guard refuses BEFORE any LLM call. Nothing measured to report."""
    huge_delta = _delta_rows(list(range(1, 4000)))  # 3999 × $0.010 trips
    with patch.object(daily_job, "fetch_new_publications", return_value=huge_delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=MagicMock(),
            score_impact=MagicMock(),
            alert_fn=MagicMock(),
        )
    assert result.status == "cost_guard_tripped"
    assert result.cost_observed_usd is None
    assert result.cost_summary is None


def test_cost_observed_is_none_on_no_op(fake_engine, fake_watermark, fake_writer):
    with patch.object(daily_job, "fetch_new_publications", return_value=[]):
        result = run_daily_enrichment(engine=fake_engine, alert_fn=MagicMock())
    assert result.status == "no_op"
    assert result.cost_observed_usd is None
    assert result.cost_summary is None


def test_zero_token_results_do_not_record_to_accumulator(fake_engine, fake_watermark, fake_writer):
    """Error-path SynopsisResult sets tokens=0; the accumulator must
    not double-count them or charge a zero-token call."""
    delta = _delta_rows([1])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_failing_synopsis,  # tokens=0 on error
            score_impact=MagicMock(side_effect=AssertionError("impact must not be called")),
            alert_fn=MagicMock(),
        )
    assert result.status == "failed"
    # Synopsis errored with tokens=0; impact was skipped. No calls
    # contributed cost.
    assert result.cost_observed_usd == Decimal("0")
    summary = result.cost_summary
    assert summary["call_count"] == 0


# ---------------------------------------------------------------------------
# Step 3 PR 3.1 — DDB IMPACT# end-of-run dual-write
# ---------------------------------------------------------------------------

def test_dual_write_invokes_ddb_batch_with_one_item_per_pmid(
    fake_engine, fake_watermark, fake_writer
):
    """On full success, the orchestrator builds one IMPACT# item per pmid
    and passes them to ddb_writer.write_impact_batch before advancing the
    watermark."""
    delta = _delta_rows([2001, 2002, 2003])
    fake_table = MagicMock(name="injected_ddb_table")
    with patch.object(daily_job, "fetch_new_publications", return_value=delta), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=3) as wb:
        result = run_daily_enrichment(
            engine=fake_engine,
            ddb_table=fake_table,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )

    assert result.status == STATUS_COMPLETE
    wb.assert_called_once()
    # Same Table instance the caller injected, not the default get_table().
    assert wb.call_args.args[0] is fake_table
    items = wb.call_args.args[1]
    assert len(items) == 3
    pks = sorted(i["PK"] for i in items)
    assert pks == ["IMPACT#pmid_2001", "IMPACT#pmid_2002", "IMPACT#pmid_2003"]


def test_dual_write_item_carries_full_contract_attributes(
    fake_engine, fake_watermark, fake_writer
):
    """Each IMPACT# item carries pmid, impact_score, justification, model,
    synopsis, synopsis_model, and a non-empty enriched_at timestamp.
    `hierarchy_version` is intentionally absent (dropped during 3.1)."""
    delta = _delta_rows([3001])
    fake_table = MagicMock(name="injected_ddb_table")
    with patch.object(daily_job, "fetch_new_publications", return_value=delta), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=1) as wb:
        run_daily_enrichment(
            engine=fake_engine,
            ddb_table=fake_table,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    item = wb.call_args.args[1][0]
    assert item["PK"] == "IMPACT#pmid_3001"
    assert item["SK"] == "SCORE"
    assert item["pmid"] == "3001"
    assert item["impact_score"] == 50
    assert item["justification"] == "ok"
    assert item["model"] == "gpt-5.1"
    assert item["synopsis"] == "synopsis for 3001"
    assert item["synopsis_model"] == "gpt-5.1"
    assert item["enriched_at"]  # non-empty ISO 8601 string
    assert "hierarchy_version" not in item  # explicitly dropped in 3.1


def test_dual_write_failure_leaves_watermark_unadvanced(
    fake_engine, fake_watermark, fake_writer
):
    """If the DDB batch raises after MariaDB succeeded, the orchestrator
    must mark the run failed (watermark unadvanced) and return
    STATUS_DDB_BATCH_FAILED. Next run retries the same delta — DDB
    upserts are idempotent so this is safe."""
    delta = _delta_rows([4001, 4002])
    alert = MagicMock()
    fake_table = MagicMock(name="injected_ddb_table")
    with patch.object(daily_job, "fetch_new_publications", return_value=delta), \
         patch.object(
             daily_job.ddb_writer,
             "write_impact_batch",
             side_effect=RuntimeError("provisioned throughput exceeded"),
         ):
        result = run_daily_enrichment(
            engine=fake_engine,
            ddb_table=fake_table,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
            alert_fn=alert,
        )

    assert result.status == STATUS_DDB_BATCH_FAILED
    assert "provisioned throughput exceeded" in result.failure_reason
    fake_watermark["complete"].assert_not_called()
    fake_watermark["failed"].assert_called_once()
    # Operator alert fires with the structured context.
    alert.assert_called_once()
    args, kwargs = alert.call_args
    assert args[0] == "ERROR"
    assert "DDB batch failed" in args[1]


def test_dual_write_skipped_on_pmid_failure(
    fake_engine, fake_watermark, fake_writer
):
    """If any pmid fails in the per-pmid loop, the run is already a
    failure — the DDB batch must NOT run (no partial writes to DDB
    when some pmids didn't make it to MariaDB)."""
    delta = _delta_rows([5001])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta), \
         patch.object(daily_job.ddb_writer, "write_impact_batch") as wb:
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_failing_synopsis,
            score_impact=MagicMock(side_effect=AssertionError("impact must not be called")),
            alert_fn=MagicMock(),
        )
    assert result.status == STATUS_FAILED
    wb.assert_not_called()


def test_dual_write_falls_back_to_default_table_when_none_injected(
    fake_engine, fake_watermark, fake_writer, _stub_default_ddb_table
):
    """When the caller doesn't inject ddb_table, the orchestrator falls
    back to utils.dynamodb_helpers.get_table() — the same fallback the
    watermark module uses. Verified via the autouse stub."""
    delta = _delta_rows([6001])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=1) as wb:
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert result.status == STATUS_COMPLETE
    _stub_default_ddb_table.assert_called_once()
    # write_impact_batch got the fallback table the stub returned.
    assert wb.call_args.args[0] is _stub_default_ddb_table.return_value
