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
    STATUS_PARTIAL,
    EnrichmentBackfillResult,
    PmidOutcome,
    RunResult,
    run_daily_enrichment,
    run_enrichment_backfill,
)
from pipeline_enrichment.cost_guard import estimate_run_cost
from pipeline_enrichment.watermark import Watermark
from utils.bedrock_client import SONNET_MODEL
from utils.openai_client import GPT5_MODEL


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
    model: str = SONNET_MODEL
    input_tokens: int = 100
    output_tokens: int = 30
    error: Optional[str] = None


@dataclass
class FakeImpactResult:
    pmid: str
    impact_score: Optional[int]
    justification: Optional[str] = None
    model: str = SONNET_MODEL
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
    huge_delta = _delta_rows(list(range(1, 3500)))  # 3499 pmids × default rate > $30 threshold
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
    huge_delta = _delta_rows(list(range(1, 4000)))  # 3999 pmids × default rate > $30 threshold
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


def test_info_heartbeat_fires_on_full_success(fake_engine, fake_watermark, fake_writer):
    """Successful daily run posts a single INFO heartbeat (#133) — once the
    cron runs unattended, silence is indistinguishable from a broken tick."""
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
    alert_mock.assert_called_once()
    severity, _title, _msg = alert_mock.call_args.args
    assert severity == "INFO"
    ctx = alert_mock.call_args.kwargs["context"]
    assert ctx["mode"] == "scheduled"
    assert ctx["delta_size"] == 3
    assert ctx["content_filter_retries"] == 0
    assert alert_mock.call_args.kwargs["mention"] is False


def test_info_heartbeat_fires_on_no_op_empty_delta(
    fake_engine, fake_watermark, fake_writer
):
    """Empty delta still posts the heartbeat: the no-op case IS the signal
    that the cron fired and reached the orchestrator."""
    alert_mock = MagicMock()
    with patch.object(daily_job, "fetch_new_publications", return_value=[]):
        result = run_daily_enrichment(engine=fake_engine, alert_fn=alert_mock)
    assert result.status == "no_op"
    alert_mock.assert_called_once()
    assert alert_mock.call_args.args[0] == "INFO"
    assert alert_mock.call_args.kwargs["context"]["delta_size"] == 0


def test_info_heartbeat_includes_full_flag_in_mode(
    fake_engine, fake_watermark, fake_writer
):
    """`--full` rescore/bootstrap runs distinguish themselves in the
    heartbeat's `mode` field."""
    delta = _delta_rows([100])
    alert_mock = MagicMock()
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        run_daily_enrichment(
            engine=fake_engine, full=True,
            generate_synopsis=_ok_synopsis, score_impact=_ok_impact,
            alert_fn=alert_mock,
        )
    assert alert_mock.call_args.kwargs["context"]["mode"] == "scheduled (--full)"


def test_failed_run_does_not_double_post_info_alongside_error(
    fake_engine, fake_watermark, fake_writer
):
    """A failing tick posts only the existing ERROR card — no INFO
    heartbeat alongside it (per #133 §3)."""
    delta = _delta_rows([1, 2])
    alert_mock = MagicMock()
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_failing_synopsis,
            score_impact=_ok_impact,
            alert_fn=alert_mock,
        )
    severities = [c.args[0] for c in alert_mock.call_args_list]
    assert "INFO" not in severities
    assert "ERROR" in severities


def test_info_heartbeat_counts_content_filter_retries(
    fake_engine, fake_watermark, fake_writer
):
    """When the synopsis stage falls back to gpt-5.1 (content-filter), the
    heartbeat surfaces the retry count so operators can spot fallback
    frequency trends without scraping logs."""
    delta = _delta_rows([1, 2])

    def syn_fallback_on_2(*, pmid, **kwargs):
        # PMID 2 hit the content filter on Bedrock and resolved via
        # OpenAI gpt-5.1 — the outcome carries model=GPT5_MODEL.
        return FakeSynopsisResult(
            pmid=pmid, synopsis=f"syn-{pmid}",
            model=GPT5_MODEL if pmid == "2" else SONNET_MODEL,
        )

    alert_mock = MagicMock()
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=syn_fallback_on_2,
            score_impact=_ok_impact,
            alert_fn=alert_mock,
        )
    info_call = next(c for c in alert_mock.call_args_list if c.args[0] == "INFO")
    assert info_call.kwargs["context"]["content_filter_retries"] == 1


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

# Sonnet 4.6 prices from config/llm_prices.yaml: input $3.00/Mtok, output
# $15.00/Mtok. The Fake*Result.model defaults are SONNET_MODEL (the Bedrock
# happy path), so the orchestrator's accumulator.record keys each call on it.
# FakeSynopsisResult: 100 in / 30 out → 100*3/1M + 30*15/1M = $0.00075
# FakeImpactResult:   200 in / 80 out → 200*3/1M + 80*15/1M = $0.00180
# Per-pmid total: $0.00255
_EXPECTED_PER_PMID_USD = Decimal("0.00255")


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
    # by_model groups under each result's actual model — the Bedrock happy
    # path here, so SONNET_MODEL, never the gpt-5.1 content-filter fallback.
    assert SONNET_MODEL in summary["by_model"]
    assert "gpt-5.1" not in summary["by_model"]


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
    assert result.cost_observed_usd == Decimal("0.00075")  # synopsis only (Sonnet)


def test_cost_observed_is_none_on_cost_guard_tripped(fake_engine, fake_watermark, fake_writer):
    """Cost guard refuses BEFORE any LLM call. Nothing measured to report."""
    huge_delta = _delta_rows(list(range(1, 4000)))  # 3999 × default rate trips
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


def test_cost_attributed_per_model_when_fallback_fires(fake_engine, fake_watermark, fake_writer):
    """The M1 fix: each LLM call's cost is recorded against its result's
    actual `.model`, not a hardcoded constant. A run whose impact call
    content-filtered to the gpt-5.1 fallback attributes synopsis cost to
    Sonnet and impact cost to gpt-5.1 — by_model carries both keys."""
    delta = _delta_rows([1])

    def _impact_via_fallback(*, pub_data, client=None):
        # The content-filter fallback fired: the result records gpt-5.1 as
        # the model actually used (see pipeline_enrichment.llm_call).
        return FakeImpactResult(
            pmid=str(pub_data["pmid"]), impact_score=50,
            justification="ok", model="gpt-5.1",
        )

    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,       # Sonnet happy path
            score_impact=_impact_via_fallback,    # gpt-5.1 content-filter fallback
            alert_fn=MagicMock(),
        )
    assert result.status == STATUS_COMPLETE
    by_model = result.cost_summary["by_model"]
    assert set(by_model) == {SONNET_MODEL, "gpt-5.1"}
    # Synopsis on Sonnet (100/30 → $0.00075) + impact on gpt-5.1
    # (200/80 → $0.00105) — each priced against its own model's rate.
    assert result.cost_observed_usd == Decimal("0.0018")


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
    assert item["model"] == SONNET_MODEL
    assert item["synopsis"] == "synopsis for 3001"
    assert item["synopsis_model"] == SONNET_MODEL
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


# ---------------------------------------------------------------------------
# #112 — run_enrichment_backfill (watermark-free explicit-PMID enrichment)
# ---------------------------------------------------------------------------

def test_backfill_all_succeed_writes_synopsis_impact_and_ddb_batch(
    fake_engine, fake_writer
):
    """Happy path: every PMID gets synopsis + impact in MariaDB and one
    IMPACT# item in the end-of-run DynamoDB batch."""
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1", "2", "3"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1, 2, 3])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch",
                      return_value=3) as wb:
        result = run_enrichment_backfill(
            pmids=["1", "2", "3"],
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert result.status == STATUS_COMPLETE
    assert (result.requested, result.attempted) == (3, 3)
    assert (result.succeeded, result.failed) == (3, 0)
    assert result.impact_rows_written == 3
    wb.assert_called_once()
    items = wb.call_args.args[1]
    assert sorted(i["PK"] for i in items) == [
        "IMPACT#pmid_1", "IMPACT#pmid_2", "IMPACT#pmid_3",
    ]
    # Cost is both estimated (pre-run, conservative) and observed (post-run).
    assert result.cost_estimate_usd == estimate_run_cost(3)
    assert result.cost_observed_usd == _EXPECTED_PER_PMID_USD * 3


def test_backfill_idempotency_cull_skips_already_complete(fake_engine, fake_writer):
    """check_enrichment_coverage's `complete` set is culled — only the
    `incomplete` PMIDs are fetched and enriched."""
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": ["1", "2"], "incomplete": ["3"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([3])) as fetch, \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=1):
        result = run_enrichment_backfill(
            pmids=["1", "2", "3"],
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert result.status == STATUS_COMPLETE
    assert result.requested == 3
    assert result.already_complete == 2
    assert result.attempted == 1
    # Only the incomplete PMID reached the publication fetch.
    assert fetch.call_args.args[1] == ["3"]


def test_backfill_force_bypasses_the_idempotency_cull(fake_engine, fake_writer):
    """--force: check_enrichment_coverage is never consulted; every
    requested PMID is reprocessed."""
    with patch.object(daily_job, "check_enrichment_coverage") as cov, \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1, 2])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=2):
        result = run_enrichment_backfill(
            pmids=["1", "2"],
            engine=fake_engine,
            force=True,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    cov.assert_not_called()
    assert result.forced is True
    assert result.attempted == 2
    assert result.already_complete == 0


def test_backfill_partial_failure_commits_succeeded_and_alerts(
    fake_engine, fake_writer
):
    """A failed PMID does not block the rest: the succeeded PMIDs are still
    IMPACT#-batched, status is `partial`, and the operator is alerted."""
    def syn_failing_on_2(*, pmid, **kwargs):
        if pmid == "2":
            return FakeSynopsisResult(
                pmid=pmid, synopsis=None,
                input_tokens=0, output_tokens=0, error="bad abstract",
            )
        return FakeSynopsisResult(pmid=pmid, synopsis=f"syn-{pmid}")

    alert = MagicMock()
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1", "2", "3"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1, 2, 3])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch",
                      return_value=2) as wb:
        result = run_enrichment_backfill(
            pmids=["1", "2", "3"],
            engine=fake_engine,
            generate_synopsis=syn_failing_on_2,
            score_impact=_ok_impact,
            alert_fn=alert,
        )
    assert result.status == STATUS_PARTIAL
    assert (result.succeeded, result.failed) == (2, 1)
    # Only the 2 succeeded PMIDs are batched to DynamoDB.
    items = wb.call_args.args[1]
    assert sorted(i["PK"] for i in items) == ["IMPACT#pmid_1", "IMPACT#pmid_3"]
    alert.assert_called_once()
    assert alert.call_args.args[0] == "WARN"


def test_backfill_complete_posts_info_heartbeat(fake_engine, fake_writer):
    """Operator-driven backfill that completes cleanly also posts the
    #133 heartbeat — mode='backfill', no watermark in context."""
    alert = MagicMock()
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1", "2"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1, 2])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=2):
        result = run_enrichment_backfill(
            pmids=["1", "2"],
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
            alert_fn=alert,
        )
    assert result.status == STATUS_COMPLETE
    alert.assert_called_once()
    assert alert.call_args.args[0] == "INFO"
    ctx = alert.call_args.kwargs["context"]
    assert ctx["mode"] == "backfill"
    assert ctx["delta_size"] == 2
    assert "watermark" not in ctx  # backfill has no watermark
    assert alert.call_args.kwargs["mention"] is False


def test_backfill_force_mode_label_in_heartbeat(fake_engine, fake_writer):
    """--force backfills surface the flag in the mode field."""
    alert = MagicMock()
    with patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=1):
        run_enrichment_backfill(
            pmids=["1"], engine=fake_engine, force=True,
            generate_synopsis=_ok_synopsis, score_impact=_ok_impact,
            alert_fn=alert,
        )
    assert alert.call_args.kwargs["context"]["mode"] == "backfill (--force)"


def test_backfill_partial_does_not_post_info_heartbeat(fake_engine, fake_writer):
    """Partial backfills go through _alert_backfill (WARN), not the
    INFO heartbeat path."""
    def syn_failing_on_2(*, pmid, **kwargs):
        if pmid == "2":
            return FakeSynopsisResult(
                pmid=pmid, synopsis=None,
                input_tokens=0, output_tokens=0, error="boom",
            )
        return FakeSynopsisResult(pmid=pmid, synopsis=f"syn-{pmid}")

    alert = MagicMock()
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1", "2"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1, 2])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=1):
        run_enrichment_backfill(
            pmids=["1", "2"], engine=fake_engine,
            generate_synopsis=syn_failing_on_2, score_impact=_ok_impact,
            alert_fn=alert,
        )
    severities = [c.args[0] for c in alert.call_args_list]
    assert "INFO" not in severities
    assert "WARN" in severities


def test_backfill_all_fail_skips_ddb_batch_and_alerts_error(
    fake_engine, fake_writer
):
    alert = MagicMock()
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch") as wb:
        result = run_enrichment_backfill(
            pmids=["1"],
            engine=fake_engine,
            generate_synopsis=_failing_synopsis,
            score_impact=MagicMock(side_effect=AssertionError("impact skipped")),
            alert_fn=alert,
        )
    assert result.status == STATUS_FAILED
    assert (result.succeeded, result.failed) == (0, 1)
    wb.assert_not_called()  # nothing succeeded → no IMPACT# batch
    assert alert.call_args.args[0] == "ERROR"


def test_backfill_empty_work_set_is_no_op(fake_engine):
    result = run_enrichment_backfill(pmids=[], engine=fake_engine)
    assert result.status == STATUS_NO_OP
    assert result.requested == 0


def test_backfill_all_already_complete_is_no_op(fake_engine):
    """Every requested PMID already has synopsis + impact — no publication
    fetch, no LLM calls."""
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": ["1", "2"], "incomplete": []}), \
         patch.object(daily_job, "fetch_publications_for_enrichment") as fetch:
        result = run_enrichment_backfill(pmids=["1", "2"], engine=fake_engine)
    assert result.status == STATUS_NO_OP
    assert result.already_complete == 2
    fetch.assert_not_called()


def test_backfill_dry_run_generates_nothing(fake_engine):
    """--dry-run resolves the work set and surfaces the cost estimate but
    makes no LLM call and writes no IMPACT# batch."""
    syn = MagicMock(side_effect=AssertionError("dry-run must not call the LLM"))
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1", "2"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1, 2])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch") as wb:
        result = run_enrichment_backfill(
            pmids=["1", "2"],
            engine=fake_engine,
            dry_run=True,
            generate_synopsis=syn,
            score_impact=syn,
        )
    assert result.dry_run is True
    assert result.attempted == 0
    assert result.cost_estimate_usd == estimate_run_cost(2)
    syn.assert_not_called()
    wb.assert_not_called()


def test_backfill_reports_unresolved_pmids(fake_engine, fake_writer):
    """A PMID in the work set but absent from analysis_summary_article
    (pre-2020 / typo) is reported as unresolved, not silently dropped."""
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1", "2", "999"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1, 2])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=2):
        result = run_enrichment_backfill(
            pmids=["1", "2", "999"],
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert result.status == STATUS_COMPLETE
    assert result.unresolved == 1
    assert result.unresolved_pmids == ["999"]
    assert result.attempted == 2


def test_backfill_ddb_batch_failure_is_ddb_batch_failed(fake_engine, fake_writer):
    """If write_impact_batch raises after the MariaDB writes, the run is
    `ddb_batch_failed` — the operator must re-run with --force."""
    alert = MagicMock()
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1", "2"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1, 2])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch",
                      side_effect=RuntimeError("provisioned throughput exceeded")):
        result = run_enrichment_backfill(
            pmids=["1", "2"],
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
            alert_fn=alert,
        )
    assert result.status == STATUS_DDB_BATCH_FAILED
    assert "provisioned throughput exceeded" in result.failure_reason
    assert alert.call_args.args[0] == "ERROR"


def test_backfill_uses_an_injected_ddb_table(fake_engine, fake_writer):
    """An injected ddb_table is used for the IMPACT# batch instead of the
    default get_table()."""
    fake_table = MagicMock(name="injected_table")
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1"]}), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch",
                      return_value=1) as wb:
        run_enrichment_backfill(
            pmids=["1"],
            engine=fake_engine,
            ddb_table=fake_table,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert wb.call_args.args[0] is fake_table


def test_backfill_dedupes_requested_pmids(fake_engine, fake_writer):
    """Duplicate / whitespace-padded input PMIDs collapse to one work item."""
    with patch.object(daily_job, "check_enrichment_coverage",
                       return_value={"complete": [], "incomplete": ["1"]}) as cov, \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=_delta_rows([1])), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=1):
        result = run_enrichment_backfill(
            pmids=["1", "1", " 1 ", "1"],
            engine=fake_engine,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert result.requested == 1
    # check_enrichment_coverage receives the de-duplicated set as its second
    # positional arg (the first is the DDB client created at the call site).
    assert cov.call_args.args[1] == ["1"]


# ---------------------------------------------------------------------------
# #137 — Per-PMID quarantine
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_quarantine():
    """Patch the quarantine module's four functions on daily_job.

    Default behavior:
      - is_quarantined → False for every PMID (no prior quarantine state)
      - record_failure → returns 1 (the count after the first call)
      - clear → no-op
    Tests override the return values via the yielded dict.
    """
    with patch.object(daily_job.quarantine, "is_quarantined", return_value=False) as iq, \
         patch.object(daily_job.quarantine, "record_failure", return_value=1) as rf, \
         patch.object(daily_job.quarantine, "clear", return_value=None) as cl:
        yield {"is_quarantined": iq, "record_failure": rf, "clear": cl}


def _impact_score(pub_data, score=50, **_):
    """Score `pub_data["pmid"]`'s impact; default success at 50.

    Used by the failure-mix tests below to pin per-PMID outcomes without
    repeating the FakeImpactResult boilerplate.
    """
    return FakeImpactResult(
        pmid=str(pub_data["pmid"]),
        impact_score=score,
        justification="ok",
    )


def test_quarantine_threshold_crossing_advances_watermark_and_skips_impact_batch(
    fake_engine, fake_watermark, fake_writer, fake_quarantine,
):
    """Acceptance #1 from #137: after the Nth failure on a PMID, that tick
    itself advances the watermark past the PMID, an ERROR Teams card lists
    it, and the IMPACT# batch excludes it."""
    delta = _delta_rows([4001, 4002, 4003, 4004])
    # PMID 4003 fails impact this tick; record_failure says count crossed
    # to 3 (the threshold). Other PMIDs succeed.
    def selective_impact(*, pub_data, client=None):
        pmid = str(pub_data["pmid"])
        if pmid == "4003":
            return FakeImpactResult(
                pmid=pmid, impact_score=None,
                input_tokens=0, output_tokens=0, error="boom",
            )
        return _ok_impact(pub_data=pub_data, client=client)

    fake_quarantine["record_failure"].return_value = 3  # crossed threshold
    alerts: list[tuple] = []
    def capture_alert(severity, title, message, context=None, mention=True):
        alerts.append((severity, title, context or {}))

    captured_batches: list[list] = []
    def capture_batch(table, items):
        captured_batches.append(items)
        return len(items)

    with patch.object(daily_job, "fetch_new_publications", return_value=delta), \
         patch.object(daily_job.ddb_writer, "write_impact_batch",
                      side_effect=capture_batch):
        result = run_daily_enrichment(
            engine=fake_engine,
            quarantine_threshold=3,
            generate_synopsis=_ok_synopsis,
            score_impact=selective_impact,
            alert_fn=capture_alert,
        )

    # Watermark advanced — quarantine-crossed PMID treated as effectively passed.
    assert result.status == STATUS_COMPLETE
    assert result.new_watermark_pmid == 4004
    fake_watermark["complete"].assert_called_once()

    # Outcome for 4003: failed but quarantine-triggered.
    by_pmid = {o.pmid: o for o in result.outcomes}
    assert by_pmid["4003"].fully_succeeded is False
    assert by_pmid["4003"].quarantine_triggered is True
    assert by_pmid["4003"].effectively_passed is True

    # IMPACT# batch had 3 items (4001/4002/4004), NOT 4 — 4003 carries no
    # synopsis/impact payload.
    assert len(captured_batches) == 1
    written_pmids = sorted(item["pmid"] for item in captured_batches[0])
    assert written_pmids == ["4001", "4002", "4004"]

    # ERROR Teams card fired listing 4003 as quarantined (in addition to the
    # INFO success summary).
    error_alerts = [a for a in alerts if a[0] == "ERROR"]
    assert len(error_alerts) == 1
    assert "quarantined" in error_alerts[0][1].lower()
    assert "4003" in error_alerts[0][2].get("quarantined_pmids", "")
    assert error_alerts[0][2]["threshold"] == 3
    assert error_alerts[0][2]["quarantined_count"] == 1


def test_quarantine_below_threshold_preserves_all_or_nothing(
    fake_engine, fake_watermark, fake_writer, fake_quarantine,
):
    """Acceptance #2 from #137: a transient flake (below threshold) still
    stalls the watermark — protection against accidental advancement on
    intermittent failures."""
    delta = _delta_rows([5001, 5002, 5003, 5004])
    def selective_impact(*, pub_data, client=None):
        if str(pub_data["pmid"]) == "5003":
            return FakeImpactResult(
                pmid="5003", impact_score=None,
                input_tokens=0, output_tokens=0, error="flake",
            )
        return _ok_impact(pub_data=pub_data, client=client)

    # 1st consecutive failure → count=1, below threshold of 3.
    fake_quarantine["record_failure"].return_value = 1

    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            quarantine_threshold=3,
            generate_synopsis=_ok_synopsis,
            score_impact=selective_impact,
        )

    # Run failed → watermark unchanged.
    assert result.status == STATUS_FAILED
    fake_watermark["complete"].assert_not_called()
    fake_watermark["failed"].assert_called_once()

    by_pmid = {o.pmid: o for o in result.outcomes}
    assert by_pmid["5003"].fully_succeeded is False
    assert by_pmid["5003"].quarantine_triggered is False
    assert by_pmid["5003"].effectively_passed is False

    # record_failure WAS called (the counter still moves toward threshold).
    fake_quarantine["record_failure"].assert_called_once()
    rf_call = fake_quarantine["record_failure"].call_args
    assert rf_call.args[0] == "5003"
    assert "flake" in rf_call.kwargs["reason"]


def test_success_after_partial_quarantine_clears_the_row(
    fake_engine, fake_watermark, fake_writer, fake_quarantine,
):
    """Acceptance #3 from #137: a PMID with 2 prior failures that succeeds
    this run has its quarantine row deleted — transient blips don't
    accumulate forever toward eventual quarantine."""
    delta = _delta_rows([6001, 6002])
    with patch.object(daily_job, "fetch_new_publications", return_value=delta), \
         patch.object(daily_job.ddb_writer, "write_impact_batch", return_value=2):
        result = run_daily_enrichment(
            engine=fake_engine,
            quarantine_threshold=3,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )

    assert result.status == STATUS_COMPLETE
    # quarantine.clear called for every successful PMID — idempotent on rows
    # that don't exist, idempotent on rows that do.
    cleared = {c.args[0] for c in fake_quarantine["clear"].call_args_list}
    assert cleared == {"6001", "6002"}
    fake_quarantine["record_failure"].assert_not_called()


def test_already_quarantined_pmid_is_skipped_not_reprocessed(
    fake_engine, fake_watermark, fake_writer, fake_quarantine,
):
    """Acceptance #4 from #137: a PMID already at/above threshold is skipped
    before any LLM call; the run still passes; watermark advances; the
    skipped PMID is excluded from the IMPACT# batch."""
    delta = _delta_rows([7001, 7002, 7003, 7004])
    fake_quarantine["is_quarantined"].side_effect = (
        lambda pmid, threshold, table=None: pmid == "7003"
    )

    captured_batches: list[list] = []
    def capture_batch(table, items):
        captured_batches.append(items)
        return len(items)

    impact_calls: list[str] = []
    def tracking_impact(*, pub_data, client=None):
        impact_calls.append(str(pub_data["pmid"]))
        return _ok_impact(pub_data=pub_data, client=client)

    alerts: list[tuple] = []
    def capture_alert(severity, title, message, context=None, mention=True):
        alerts.append((severity, title, context or {}))

    with patch.object(daily_job, "fetch_new_publications", return_value=delta), \
         patch.object(daily_job.ddb_writer, "write_impact_batch",
                      side_effect=capture_batch):
        result = run_daily_enrichment(
            engine=fake_engine,
            quarantine_threshold=3,
            generate_synopsis=_ok_synopsis,
            score_impact=tracking_impact,
            alert_fn=capture_alert,
        )

    assert result.status == STATUS_COMPLETE
    assert result.new_watermark_pmid == 7004
    # Impact (and synopsis) never called for the quarantined PMID.
    assert "7003" not in impact_calls
    assert sorted(impact_calls) == ["7001", "7002", "7004"]

    # Outcome carries quarantine_skipped, not _triggered (it was skipped pre-
    # processing, not crossed-this-tick).
    by_pmid = {o.pmid: o for o in result.outcomes}
    assert by_pmid["7003"].quarantine_skipped is True
    assert by_pmid["7003"].quarantine_triggered is False
    assert by_pmid["7003"].effectively_passed is True

    # IMPACT# batch carries only the 3 successfully-enriched PMIDs.
    assert len(captured_batches) == 1
    written_pmids = sorted(item["pmid"] for item in captured_batches[0])
    assert written_pmids == ["7001", "7002", "7004"]

    # No ERROR alert this run — the skipped PMID fired its alert on the
    # threshold-crossing tick (a previous run), not now.
    error_alerts = [a for a in alerts if a[0] == "ERROR"]
    assert error_alerts == []

    # record_failure NOT called for the quarantined PMID (we never tried to
    # process it). clear NOT called either (skipped path bypasses both).
    rf_pmids = {c.args[0] for c in fake_quarantine["record_failure"].call_args_list}
    cl_pmids = {c.args[0] for c in fake_quarantine["clear"].call_args_list}
    assert "7003" not in rf_pmids
    assert "7003" not in cl_pmids


def test_record_failure_receives_run_id_for_idempotency(
    fake_engine, fake_watermark, fake_writer, fake_quarantine,
):
    """The orchestrator must pass the watermark-claimed run_id so a retried
    subprocess can't double-count failures within the same tick (#137)."""
    delta = _delta_rows([8001])
    fake_watermark["started"].return_value = "run-claimed-uuid"
    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        run_daily_enrichment(
            engine=fake_engine,
            quarantine_threshold=3,
            generate_synopsis=_ok_synopsis,
            score_impact=_failing_impact,
        )
    rf_call = fake_quarantine["record_failure"].call_args
    assert rf_call.kwargs["run_id"] == "run-claimed-uuid"


def test_quarantine_threshold_defaults_from_config_when_not_overridden(
    fake_engine, fake_watermark, fake_writer, fake_quarantine,
):
    """No explicit override → daily_job reads `enrichment_quarantine_threshold`
    from config/thresholds.json so the schedule-time tick agrees with the
    operator's `--retry-quarantine` invocation."""
    with patch.object(daily_job, "fetch_new_publications", return_value=[]):
        run_daily_enrichment(engine=fake_engine)
    # is_quarantined wasn't called (empty delta), but the config-read path
    # didn't crash and the run reached the empty-delta no-op return.
    # The default value (3) is pinned by the schema; assert via a second
    # round-trip through the load_thresholds helper.
    from utils.env_check import load_thresholds
    assert int(load_thresholds()["enrichment_quarantine_threshold"]) == 3


def test_mixed_quarantine_skipped_and_below_threshold_failure_still_fails_run(
    fake_engine, fake_watermark, fake_writer, fake_quarantine,
):
    """5-PMID delta: PMID #3 already quarantined (skipped), PMID #5 has a
    transient (below-threshold) failure. Result: run fails because #5 is
    not effectively-passed, watermark does NOT advance, ERROR alert fires
    with `failures=1` (the skipped PMID is NOT counted as a failure)."""
    delta = _delta_rows([9001, 9002, 9003, 9004, 9005])
    fake_quarantine["is_quarantined"].side_effect = (
        lambda pmid, threshold, table=None: pmid == "9003"
    )
    fake_quarantine["record_failure"].return_value = 1  # below threshold of 3

    def selective_impact(*, pub_data, client=None):
        pmid = str(pub_data["pmid"])
        if pmid == "9005":
            return FakeImpactResult(
                pmid=pmid, impact_score=None,
                input_tokens=0, output_tokens=0, error="flake",
            )
        return _ok_impact(pub_data=pub_data, client=client)

    alerts: list[tuple] = []
    def capture_alert(severity, title, message, context=None, mention=True):
        alerts.append((severity, title, message, context or {}))

    with patch.object(daily_job, "fetch_new_publications", return_value=delta):
        result = run_daily_enrichment(
            engine=fake_engine,
            quarantine_threshold=3,
            generate_synopsis=_ok_synopsis,
            score_impact=selective_impact,
            alert_fn=capture_alert,
        )

    assert result.status == STATUS_FAILED
    fake_watermark["complete"].assert_not_called()

    # ERROR alert: failures=1 (PMID 9005), NOT 2 — the skipped PMID 9003
    # isn't a failure for accounting.
    error_alerts = [a for a in alerts if a[0] == "ERROR"]
    assert len(error_alerts) == 1
    assert error_alerts[0][3]["failures"] == 1
    assert "9005" in error_alerts[0][3]["sample_failures"]
    assert "9003" not in error_alerts[0][3]["sample_failures"]


# ---------------------------------------------------------------------------
# #137 — Operator quarantine retry + clear (`retry_quarantined_pmids` /
# `clear_quarantine_for_pmid`)
# ---------------------------------------------------------------------------

from pipeline_enrichment.daily_job import (  # noqa: E402
    QuarantineRetryResult,
    clear_quarantine_for_pmid,
    retry_quarantined_pmids,
)


def test_retry_quarantined_no_rows_returns_empty_result(
    fake_engine, fake_writer, fake_quarantine,
):
    """No quarantine rows ≥ threshold → no work, no LLM calls, empty result."""
    with patch.object(daily_job.quarantine, "list_quarantined", return_value=[]):
        result = retry_quarantined_pmids(engine=fake_engine)
    assert isinstance(result, QuarantineRetryResult)
    assert result.attempted == 0
    assert result.cleared == 0
    assert result.still_failing == 0


def test_retry_quarantined_successes_clear_rows(
    fake_engine, fake_writer, fake_quarantine,
):
    """A retry that succeeds deletes the row; the operator's batch shrinks."""
    quarantined_rows = [
        {"pmid": "12001", "consecutive_failures": 3},
        {"pmid": "12002", "consecutive_failures": 5},
    ]
    pub_rows = _delta_rows([12001, 12002])
    with patch.object(daily_job.quarantine, "list_quarantined",
                       return_value=quarantined_rows), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=pub_rows):
        result = retry_quarantined_pmids(
            engine=fake_engine,
            quarantine_threshold=3,
            generate_synopsis=_ok_synopsis,
            score_impact=_ok_impact,
        )
    assert result.attempted == 2
    assert result.cleared == 2
    assert result.still_failing == 0
    cleared = {c.args[0] for c in fake_quarantine["clear"].call_args_list}
    assert cleared == {"12001", "12002"}
    # record_failure must NOT fire on success paths.
    fake_quarantine["record_failure"].assert_not_called()


def test_retry_quarantined_failures_bump_counter_no_threshold_check(
    fake_engine, fake_writer, fake_quarantine,
):
    """A retry that fails leaves the row in place and bumps the counter —
    the operator's row survives until they explicitly clear or fix it."""
    quarantined_rows = [{"pmid": "13001", "consecutive_failures": 3}]
    pub_rows = _delta_rows([13001])
    fake_quarantine["record_failure"].return_value = 4  # bumped from 3
    with patch.object(daily_job.quarantine, "list_quarantined",
                       return_value=quarantined_rows), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=pub_rows):
        result = retry_quarantined_pmids(
            engine=fake_engine,
            quarantine_threshold=3,
            generate_synopsis=_ok_synopsis,
            score_impact=_failing_impact,
        )
    assert result.attempted == 1
    assert result.cleared == 0
    assert result.still_failing == 1
    fake_quarantine["clear"].assert_not_called()
    fake_quarantine["record_failure"].assert_called_once()
    # run_id passed to record_failure is a retry-prefixed UUID/timestamp,
    # distinct from the daily-job watermark run_id so it can't collide.
    rf_call = fake_quarantine["record_failure"].call_args
    assert rf_call.kwargs["run_id"].startswith("retry-quarantine-")


def test_retry_quarantined_unresolved_pmids_are_skipped(
    fake_engine, fake_writer, fake_quarantine,
):
    """A quarantined PMID not in analysis_summary_article (pre-2020, dropped)
    is recorded as unresolved and skipped — no LLM call."""
    quarantined_rows = [
        {"pmid": "14001", "consecutive_failures": 3},
        {"pmid": "14002", "consecutive_failures": 3},  # unresolved
    ]
    pub_rows = _delta_rows([14001])  # only 14001 resolves
    impact_calls: list[str] = []
    def tracking_impact(*, pub_data, client=None):
        impact_calls.append(str(pub_data["pmid"]))
        return _ok_impact(pub_data=pub_data, client=client)

    with patch.object(daily_job.quarantine, "list_quarantined",
                       return_value=quarantined_rows), \
         patch.object(daily_job, "fetch_publications_for_enrichment",
                      return_value=pub_rows):
        result = retry_quarantined_pmids(
            engine=fake_engine,
            quarantine_threshold=3,
            generate_synopsis=_ok_synopsis,
            score_impact=tracking_impact,
        )
    assert result.attempted == 1  # only 14001 actually replayed
    assert result.cleared == 1
    assert result.unresolved == 1
    assert result.unresolved_pmids == ["14002"]
    assert impact_calls == ["14001"]


def test_clear_quarantine_for_pmid_calls_module_clear(fake_quarantine):
    """The operator-clear helper delegates to quarantine.clear (idempotent
    DDB DeleteItem)."""
    clear_quarantine_for_pmid("99999")
    fake_quarantine["clear"].assert_called_once()
    assert fake_quarantine["clear"].call_args.args[0] == "99999"
