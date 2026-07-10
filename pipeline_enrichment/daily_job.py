"""Daily enrichment orchestrator (#37 step 2 + step 3 PR 3.1).

One cycle:
    read_watermark  →  fetch_new_publications  →  cost_guard  →
        per-pmid loop ( ensure_entity → synopsis → impact → MariaDB ) →
        end-of-run DDB IMPACT# batch dual-write →
        advance watermark on full success

Step 2 (already shipped): MariaDB writes for synopsis + impact only.
Step 3 PR 3.1: an end-of-run batch dual-write of the same synopsis + impact
data into DDB IMPACT#pmid_{pmid}/SK SCORE items, per the SPS reader contract
documented in `pipeline_enrichment/ddb_writer.py`.

Each run also writes a per-run STAGE#daily_enrichment#GLOBAL ledger row
(`utils/stage_records`, SK=RUN#<started_at>) carrying the measured
`cost_observed_usd` + `records_written` so spend / ROI is a single PK query
(#37), and records cumulative spend via `pipeline_enrichment.spend_tracker`,
which alerts on each +$increment boundary against the annual ceiling. Topic
scoring and the topic-scoring takeover question are split off to issue #52.

DDB write timing within the loop is "batch at end-of-run after all MariaDB
writes succeed" (not per-pmid inline) so the failure model stays
all-or-nothing per run: a failed DDB batch leaves the watermark
unadvanced and the next retry redoes the whole delta. DDB writes are
upserts so retry is safe.

Failure model (per #37's "Failed runs leave watermark untouched" spec,
extended for #137):
- All-or-nothing watermark advancement. If ANY pmid fails (synopsis OR
  impact OR a MariaDB write) and isn't quarantined, the run is marked
  failed and the watermark stays at its previous value. The next run
  re-attempts the same delta; idempotent writes in mariadb_writer make
  that safe.
- Within a single pmid, a failed synopsis short-circuits the impact call
  (no point scoring impact for a paper we couldn't summarize). A failed
  impact after a successful synopsis leaves the synopsis row in place —
  the pmid is still counted as a failure but the next retry only re-does
  the impact.
- Per-PMID quarantine (#137): a PMID that consistently fails N runs in a
  row is treated as a write-off for the all-or-nothing gate so one stuck
  PMID can't stall the whole delta forever. The threshold-crossing tick
  itself advances the watermark past the quarantined PMID; the operator
  gets an ERROR Teams card listing the PMID(s) for triage. See
  `pipeline_enrichment/quarantine.py`.

Bootstrap: per #37, the annual rescore uses `--full` to bypass the cost
guard. The same flag bootstraps the first daily run on a stale watermark
(the ~1,865-paper backlog as of 2026-05-14).

Enrichment backfill (#112): this module also hosts `run_enrichment_backfill`,
the watermark-free sibling of `run_daily_enrichment`. It enriches an explicit
PMID work set rather than a watermark-scoped delta — for the historical
publications a newly-onboarded researcher brings that the daily delta can
never reach (#80). It reuses `_process_one_pmid` unchanged.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy.engine import Engine

from pipeline_enrichment import alerting, cost_guard, ddb_writer, mariadb_writer
from pipeline_enrichment import quarantine, spend_tracker
from pipeline_enrichment import watermark as wm
from pipeline_enrichment.impact import score_impact as _default_score_impact
from pipeline_enrichment.synopsis import generate_synopsis as _default_generate_synopsis
from utils import stage_records
from utils.bedrock_client import BedrockClient
from utils.dynamodb_helpers import (
    check_enrichment_coverage,
    get_dynamo_client,
    get_table as _default_get_table,
)
from utils.env_check import load_thresholds
from utils.iso_clock import now_iso
from utils.llm_cost import CostAccumulator
from utils.openai_client import GPT5_MODEL
from utils.sql_queries import (
    fetch_new_publications,
    fetch_publications_for_enrichment,
)

logger = logging.getLogger(__name__)


@dataclass
class PmidOutcome:
    """Per-pmid outcome plus payload for downstream sinks.

    Originally just the success/failure flags. Expanded in step 3 PR 3.1
    to also carry the enrichment payload (synopsis text, impact score,
    dated model strings, single `enriched_at` timestamp) so the
    end-of-run DDB batch writer can compose IMPACT# items from the
    collected outcomes without re-querying anything.

    All payload fields stay None until the corresponding step succeeds;
    that keeps existing tests (which only read ok/error flags) unchanged.

    Quarantine flags (#137) are independent of success: `quarantine_skipped`
    is set when a previously-quarantined PMID was skipped before any LLM
    call; `quarantine_triggered` is set when the per-PMID failure path
    incremented the consecutive-failure counter to (or past) the threshold
    on THIS run, marking the PMID as a write-off for the all-or-nothing
    gate. Both let the gate count the outcome as "effectively passed" even
    though `fully_succeeded` is False.
    """

    pmid: str
    synopsis_ok: bool = False
    impact_ok: bool = False
    synopsis_error: Optional[str] = None
    impact_error: Optional[str] = None
    quarantine_skipped: bool = False
    quarantine_triggered: bool = False
    # Payload — populated only on success. Used by ddb_writer.build_impact_item.
    synopsis: Optional[str] = None
    synopsis_model: Optional[str] = None
    impact_score: Optional[int] = None
    justification: Optional[str] = None
    impact_model: Optional[str] = None
    enriched_at: Optional[str] = None  # ISO 8601 UTC, stamped at impact success

    @property
    def fully_succeeded(self) -> bool:
        return self.synopsis_ok and self.impact_ok

    @property
    def effectively_passed(self) -> bool:
        """Counts as a pass for the all-or-nothing watermark gate.

        A PMID effectively passes when it fully succeeded, OR when it was
        skipped for being already-quarantined, OR when this run crossed the
        quarantine threshold (the PMID is a write-off). In every case the
        delta is allowed to advance past the PMID; quarantined ones have
        no synopsis/impact payload and are excluded from the IMPACT# batch.
        """
        return (
            self.fully_succeeded
            or self.quarantine_skipped
            or self.quarantine_triggered
        )


# Status values for RunResult (most are reused by EnrichmentBackfillResult).
STATUS_COMPLETE = "complete"               # All pmids processed cleanly.
STATUS_FAILED = "failed"                    # ≥1 pmid failed.
STATUS_NO_OP = "no_op"                      # Empty delta, nothing to do.
STATUS_COST_GUARD_TRIPPED = "cost_guard_tripped"  # Refused before LLM calls.
STATUS_DDB_BATCH_FAILED = "ddb_batch_failed"      # All pmids OK in MariaDB but
                                                  # end-of-run DDB batch failed.
                                                  # Watermark stays unadvanced;
                                                  # next retry redoes the delta.


@dataclass
class RunResult:
    status: str
    delta_size: int
    outcomes: list[PmidOutcome] = field(default_factory=list)
    cost_estimate: Optional[cost_guard.CostEstimate] = None
    cost_observed_usd: Optional[Decimal] = None
    cost_summary: Optional[dict] = None
    run_id: Optional[str] = None
    new_watermark_pmid: Optional[int] = None
    failure_reason: Optional[str] = None

    @property
    def successes(self) -> int:
        return sum(1 for o in self.outcomes if o.fully_succeeded)


def _write_stage_row(
    table: Any,
    *,
    status: str,
    started_at: str,
    t0: float,
    delta_size: int,
    cost_observed_usd: Decimal = Decimal("0"),
    records_written: int = 0,
    watermark_from: Optional[int] = None,
    watermark_to: Optional[int] = None,
    run_id: Optional[str] = None,
    model_breakdown: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    """Write the per-run STAGE#daily_enrichment#GLOBAL ledger row (#37).

    One row per run (SK=RUN#<started_at>), so cumulative spend / ROI is a single
    PK query — `SUM(cost_observed_usd)` over a date range. The measured cost and
    `records_written` are first-class STAGE# fields; run status, delta size,
    watermark from→to, and per-model cost ride in the `tunable_inputs` audit
    block. ``complete``/``no_op`` write a complete row; everything else writes a
    failed row carrying the error.

    Best-effort: never raises. A STAGE#-write failure must not fail the run or
    unwind the already-committed watermark.
    """
    try:
        duration_ms = max(0, int((time.monotonic() - t0) * 1000))
        input_hash = stage_records.compute_input_hash(
            "daily_enrichment",
            {"watermark_from": watermark_from, "watermark_to": watermark_to},
        )
        tunable: dict[str, Any] = {
            "run_status": status,
            "delta_size": delta_size,
            "watermark_from": watermark_from,
            "watermark_to": watermark_to,
        }
        if model_breakdown:
            tunable["model_breakdown"] = model_breakdown
        if status in (STATUS_NO_OP, STATUS_COMPLETE):
            stage_records.write_complete(
                table,
                stage="daily_enrichment",
                scope="GLOBAL",
                input_hash=input_hash,
                started_at=started_at,
                duration_ms=duration_ms,
                cost_observed_usd=cost_observed_usd,
                records_written=records_written,
                run_id=run_id,
                tunable_inputs=tunable,
            )
        else:
            stage_records.write_failed(
                table,
                stage="daily_enrichment",
                scope="GLOBAL",
                input_hash=input_hash,
                started_at=started_at,
                duration_ms=duration_ms,
                cost_observed_usd=cost_observed_usd,
                error_code=status,
                error_message=(error or status)[:1000],
                run_id=run_id,
                tunable_inputs=tunable,
            )
    except Exception as e:  # noqa: BLE001 — best-effort ledger, must not fail run
        logger.warning(
            "daily run: STAGE# row write failed (best-effort): %s", e
        )


def _propagate_impact_best_effort(
    table: Any,
    impact_items: list[dict[str, Any]],
    *,
    alert_fn: Callable[..., None],
    run_id: Optional[str],
    context_label: str,
) -> int:
    """Best-effort #212 Part B: back-propagate impact_score onto existing
    TOPIC# rows for the just-enriched PMIDs.

    Mirrors the best-effort STAGE# row write at ``_write_stage_row``: NEVER
    raises. ``impact_score`` on TOPIC# is a derived copy of the authoritative
    IMPACT# row, so a propagation failure must not fail the run or unadvance
    the watermark (the caller has already committed it). The gap is auditable
    via ``scripts/backfill_topic_impact_from_impact_rows.py --all`` and is
    re-attempted whenever the same value is later re-enriched (overwrite-if-
    different). On failure we log + fire an ERROR alert for operator triage.

    Returns the number of TOPIC# rows written (0 on failure or no-op).
    """
    if not impact_items:
        return 0
    try:
        written = ddb_writer.propagate_impact_to_topic_rows(table, impact_items)
        logger.info(
            "%s: back-propagated impact_score to %d TOPIC# row(s)",
            context_label, written,
        )
        return written
    except Exception as e:  # noqa: BLE001 — best-effort copy, must not fail run
        logger.error(
            "%s: impact_score back-propagation to TOPIC# rows failed "
            "(best-effort; watermark already committed; IMPACT# rows are the "
            "source of truth and the gap is auditable via the backfill probe). "
            "error=%s",
            context_label, e,
        )
        alert_fn(
            "ERROR",
            "Impact back-propagation to TOPIC# rows failed",
            f"The IMPACT# dual-write succeeded but back-propagating "
            f"impact_score onto existing TOPIC# rows failed (#212 Part B). "
            f"The run is NOT failed and the watermark is NOT unadvanced — "
            f"impact_score on TOPIC# is a derived copy. Audit residual gaps "
            f"with `backfill_topic_impact_from_impact_rows.py --all` (dry-run).",
            context={
                "run_id": run_id,
                "enriched_pmids": len(impact_items),
                "error": str(e),
            },
        )
        return 0


def run_daily_enrichment(
    *,
    full: bool = False,
    limit: int = 1000,
    threshold_usd: Decimal = cost_guard.DEFAULT_THRESHOLD_USD,
    per_paper_usd: Decimal = cost_guard.DEFAULT_PER_PAPER_USD,
    quarantine_threshold: Optional[int] = None,
    engine: Engine,
    client: Optional[BedrockClient] = None,
    ddb_table: Any = None,
    generate_synopsis: Callable = _default_generate_synopsis,
    score_impact: Callable = _default_score_impact,
    alert_fn: Callable = alerting.alert,
) -> RunResult:
    """Run one cycle of the daily enrichment job.

    Args:
        full: bypass the cost guard. Use for annual rescore + first-run
            bootstrap.
        limit: SQL-level safety cap on rows returned. Cost guard fires
            first on anomalous deltas (default trip ~500 papers).
        threshold_usd, per_paper_usd: cost-guard overrides.
        quarantine_threshold: per-PMID consecutive-failure count at which
            a PMID is treated as a write-off (#137). Defaults to
            ``config/thresholds.json`` key
            ``enrichment_quarantine_threshold`` (3).
        engine: sqlalchemy Engine for MariaDB (required).
        client: optional injected Bedrock client; defaults to the shared
            lazy singleton in pipeline_enrichment.llm_call.
        ddb_table: optional injected DDB Table resource for the
            watermark; defaults to utils.dynamodb_helpers.get_table().
        generate_synopsis, score_impact, alert_fn: injectable for
            testing. Pass a no-op for alert_fn to suppress Teams posts
            during local development without unsetting the webhook env.

    Returns:
        RunResult with status + per-pmid outcomes.
    """
    if quarantine_threshold is None:
        quarantine_threshold = int(
            load_thresholds()["enrichment_quarantine_threshold"]
        )
    # Per-run STAGE# ledger timing (#37). Resolve the DDB target once so every
    # terminal return can write its ledger row; get_table() is a lazy resource
    # handle (no AWS call until used).
    started_at = now_iso()
    t0 = time.monotonic()
    stage_target = ddb_table if ddb_table is not None else _default_get_table()

    # 1. Read watermark.
    current = wm.read_watermark(table=ddb_table)
    last_max_pmid = (
        current.last_successful_max_pmid if current and current.last_successful_max_pmid else 0
    )
    logger.info("daily run: last_max_pmid=%s, full=%s", last_max_pmid, full)

    # 2. Query delta.
    delta = fetch_new_publications(
        engine, last_max_pmid=last_max_pmid, limit=limit
    )
    delta_size = len(delta)
    logger.info("daily run: delta_size=%d", delta_size)

    # Empty delta — return without touching the watermark. Still post the
    # heartbeat: a silent no-op tick is indistinguishable from a missed tick
    # once the job runs unattended on Fargate (#133).
    if delta_size == 0:
        _post_success_summary(
            alert_fn,
            mode=("scheduled (--full)" if full else "scheduled"),
            delta_size=0,
            cost_observed_usd=Decimal("0"),
            watermark_before=last_max_pmid or None,
            watermark_after=last_max_pmid or None,
            content_filter_retries=0,
        )
        _write_stage_row(
            stage_target, status=STATUS_NO_OP, started_at=started_at, t0=t0,
            delta_size=0, watermark_from=last_max_pmid or None,
            watermark_to=last_max_pmid or None,
        )
        return RunResult(status=STATUS_NO_OP, delta_size=0)

    # 3. Cost guard (unless --full).
    cost_estimate: Optional[cost_guard.CostEstimate] = None
    if not full:
        try:
            cost_estimate = cost_guard.check_guard(
                delta_size=delta_size,
                threshold_usd=threshold_usd,
                per_paper_usd=per_paper_usd,
            )
        except cost_guard.CostGuardTripped as e:
            # Record the trip on the watermark so operators see it on the
            # next read. Don't advance last_successful_max_pmid.
            wm.mark_run_started(table=ddb_table)
            wm.mark_run_failed(table=ddb_table)
            logger.warning("daily run: cost guard tripped — %s", e)
            alert_fn(
                "ERROR",
                "Cost guard refused daily run",
                f"Estimated cost ${e.estimate.estimated_usd} exceeds threshold "
                f"${e.estimate.threshold_usd}. Run --full to bypass if intentional "
                f"(annual rescore / cold-start backfill).",
                context={
                    "delta_size": e.estimate.delta_size,
                    "per_paper_usd": str(e.estimate.per_paper_usd),
                    "estimated_usd": str(e.estimate.estimated_usd),
                    "threshold_usd": str(e.estimate.threshold_usd),
                    "last_max_pmid": last_max_pmid,
                },
            )
            _write_stage_row(
                stage_target, status=STATUS_COST_GUARD_TRIPPED,
                started_at=started_at, t0=t0, delta_size=delta_size,
                watermark_from=last_max_pmid or None, error=str(e),
            )
            return RunResult(
                status=STATUS_COST_GUARD_TRIPPED,
                delta_size=delta_size,
                cost_estimate=e.estimate,
                failure_reason=str(e),
            )
    else:
        # Compute an estimate anyway for logging / RunResult diagnostics.
        cost_estimate = cost_guard.CostEstimate(
            delta_size=delta_size,
            per_paper_usd=per_paper_usd,
            estimated_usd=cost_guard.estimate_run_cost(delta_size, per_paper_usd),
            threshold_usd=threshold_usd,
        )

    # 4. Claim the run.
    run_id = wm.mark_run_started(table=ddb_table)
    logger.info("daily run: started run_id=%s", run_id)

    # 5. Loop pmids. The accumulator tracks measured cost across all
    # LLM calls — both successful and failed (token counts on failed
    # SynopsisResult/ImpactResult are 0, so they contribute nothing).
    #
    # Quarantine (#137):
    #   - PRE-check: skip PMIDs already at/above the threshold without any LLM
    #     call. Defense in depth — the watermark normally advances past them
    #     on the threshold-crossing tick so they don't re-enter the delta.
    #   - POST-process: on success, clear the row; on failure, increment the
    #     counter and flip `quarantine_triggered` if it crossed the threshold
    #     THIS run. That outcome counts as a pass for the all-or-nothing gate
    #     so the delta moves forward.
    accumulator = CostAccumulator()
    outcomes: list[PmidOutcome] = []
    # Idempotency cull (#312): a prior failed run leaves the watermark unadvanced,
    # so the next delta re-includes every already-enriched PMID. Skip those (their
    # IMPACT# row already carries synopsis + impact) rather than re-spending the LLM.
    already_enriched = set(
        check_enrichment_coverage(get_dynamo_client(), [str(r["pmid"]) for r in delta])["complete"]
    )
    for row in delta:
        pmid = str(row["pmid"])
        if pmid in already_enriched:
            logger.info("daily run: pmid=%s skipped (already enriched)", pmid)
            continue
        if quarantine.is_quarantined(pmid, quarantine_threshold, table=ddb_table):
            outcomes.append(PmidOutcome(pmid=pmid, quarantine_skipped=True))
            logger.info(
                "daily run: pmid=%s skipped (quarantined, threshold=%d)",
                pmid, quarantine_threshold,
            )
            continue
        outcome = _process_one_pmid(
            row,
            engine=engine,
            client=client,
            generate_synopsis=generate_synopsis,
            score_impact=score_impact,
            accumulator=accumulator,
        )
        outcomes.append(outcome)
        if outcome.fully_succeeded:
            quarantine.clear(pmid, table=ddb_table)
        else:
            reason = outcome.synopsis_error or outcome.impact_error or "unknown"
            new_count = quarantine.record_failure(
                pmid, reason=reason, run_id=run_id, table=ddb_table,
            )
            if new_count >= quarantine_threshold:
                outcome.quarantine_triggered = True
                logger.warning(
                    "daily run: pmid=%s crossed quarantine threshold "
                    "(count=%d, threshold=%d) — write-off, watermark will "
                    "advance past it",
                    pmid, new_count, quarantine_threshold,
                )
            logger.warning(
                "daily run: pmid=%s failed (synopsis_ok=%s, impact_ok=%s, "
                "synopsis_err=%s, impact_err=%s, quarantine_count=%d)",
                outcome.pmid, outcome.synopsis_ok, outcome.impact_ok,
                outcome.synopsis_error, outcome.impact_error, new_count,
            )
    logger.info(
        "daily run: measured cost $%s across %d calls (input=%d, output=%d tokens)",
        accumulator.total_usd, accumulator.call_count,
        accumulator.total_input_tokens, accumulator.total_output_tokens,
    )

    # Record cumulative spend + alert on each +$increment boundary (#37). Counts
    # spend on failed runs too — the LLM calls cost money regardless of outcome.
    spend_tracker.record_spend(
        accumulator.total_usd,
        started_at=started_at,
        run_id=run_id,
        table=stage_target,
        alert_fn=alert_fn,
    )

    # 6. Advance watermark on effective pass (success OR quarantined).
    all_succeeded = all(o.effectively_passed for o in outcomes)
    if all_succeeded:
        # 6a. End-of-run DDB IMPACT# batch dual-write (#37 step 3 PR 3.1).
        # Sequenced BEFORE mark_run_complete so a DDB batch failure leaves
        # the watermark unadvanced. Next run's retry redoes the same delta;
        # MariaDB upserts and DDB upserts are both idempotent.
        #
        # Quarantined outcomes (skipped or threshold-triggered) carry no
        # synopsis/impact payload and must be excluded from the batch (#137).
        # Reuse the already-resolved stage_target (same value) so the table is
        # resolved exactly once per run.
        ddb_target = stage_target
        impact_items = [
            ddb_writer.build_impact_item(
                pmid=o.pmid,
                impact_score=o.impact_score,
                justification=o.justification,
                impact_model=o.impact_model,
                synopsis=o.synopsis,
                synopsis_model=o.synopsis_model,
                enriched_at=o.enriched_at,
            )
            for o in outcomes
            if o.fully_succeeded
        ]
        try:
            ddb_writer.write_impact_batch(ddb_target, impact_items)
        except Exception as e:
            wm.mark_run_failed(table=ddb_table)
            logger.error(
                "daily run: DDB IMPACT# batch failed after MariaDB success "
                "— watermark NOT advanced. delta_size=%d, error=%s",
                delta_size, e,
            )
            alert_fn(
                "ERROR",
                "Daily enrichment DDB batch failed",
                f"All {delta_size} pmids written to MariaDB, but the "
                f"end-of-run DDB IMPACT# batch failed. Watermark not "
                f"advanced; next run will retry the same delta. DDB "
                f"writes are idempotent upserts.",
                context={
                    "run_id": run_id,
                    "delta_size": delta_size,
                    "error": str(e),
                },
            )
            _write_stage_row(
                stage_target, status=STATUS_DDB_BATCH_FAILED,
                started_at=started_at, t0=t0, delta_size=delta_size,
                cost_observed_usd=accumulator.total_usd,
                records_written=sum(1 for o in outcomes if o.fully_succeeded),
                watermark_from=last_max_pmid or None, run_id=run_id,
                model_breakdown=accumulator.summary().get("by_model"),
                error=f"DDB IMPACT# batch failed: {e}",
            )
            return RunResult(
                status=STATUS_DDB_BATCH_FAILED,
                delta_size=delta_size,
                outcomes=outcomes,
                cost_estimate=cost_estimate,
                cost_observed_usd=accumulator.total_usd,
                cost_summary=accumulator.summary(),
                run_id=run_id,
                failure_reason=f"DDB IMPACT# batch failed: {e}",
            )

        new_max = max(int(r["pmid"]) for r in delta)
        wm.mark_run_complete(max_pmid=new_max, table=ddb_table)
        logger.info(
            "daily run: complete — %d/%d succeeded, watermark → %d",
            len(outcomes), delta_size, new_max,
        )
        # 6b. #212 Part B — back-propagate the just-written impact_score onto
        # any pre-existing TOPIC# rows for these PMIDs (TOPIC#-before-enrichment
        # ordering). Runs AFTER mark_run_complete, best-effort: a failure here
        # must not fail the run or unadvance the watermark — impact_score on
        # TOPIC# is a derived copy of the authoritative IMPACT# row.
        _propagate_impact_best_effort(
            ddb_target, impact_items,
            alert_fn=alert_fn, run_id=run_id,
            context_label="daily run",
        )
        _post_success_summary(
            alert_fn,
            mode=("scheduled (--full)" if full else "scheduled"),
            delta_size=delta_size,
            cost_observed_usd=accumulator.total_usd,
            watermark_before=last_max_pmid or None,
            watermark_after=new_max,
            content_filter_retries=_count_content_filter_retries(outcomes),
        )
        # Newly-quarantined PMIDs trip an ERROR card even on a passing run so
        # the operator triages them (#137). Already-quarantined-then-skipped
        # PMIDs do NOT refire here — they fired the card on the tick that
        # crossed the threshold.
        newly_quarantined = [o for o in outcomes if o.quarantine_triggered]
        if newly_quarantined:
            _alert_newly_quarantined(
                alert_fn, newly_quarantined,
                threshold=quarantine_threshold,
                run_id=run_id,
            )
        _write_stage_row(
            stage_target, status=STATUS_COMPLETE, started_at=started_at, t0=t0,
            delta_size=delta_size, cost_observed_usd=accumulator.total_usd,
            records_written=sum(1 for o in outcomes if o.fully_succeeded),
            watermark_from=last_max_pmid or None, watermark_to=new_max,
            run_id=run_id, model_breakdown=accumulator.summary().get("by_model"),
        )
        return RunResult(
            status=STATUS_COMPLETE,
            delta_size=delta_size,
            outcomes=outcomes,
            cost_estimate=cost_estimate,
            cost_observed_usd=accumulator.total_usd,
            cost_summary=accumulator.summary(),
            run_id=run_id,
            new_watermark_pmid=new_max,
        )

    wm.mark_run_failed(table=ddb_table)
    failed_outcomes = [o for o in outcomes if not o.effectively_passed]
    failures = len(failed_outcomes)
    logger.error(
        "daily run: failed — %d/%d pmids failed, watermark NOT advanced",
        failures, delta_size,
    )
    sample_failures = ", ".join(
        f"{o.pmid} ({o.synopsis_error or o.impact_error})"
        for o in failed_outcomes[:3]
    )
    if len(failed_outcomes) > 3:
        sample_failures += f", … (+{len(failed_outcomes) - 3} more)"
    alert_fn(
        "ERROR",
        "Daily enrichment run failed",
        f"{failures}/{delta_size} pmids failed. Watermark not advanced; next "
        f"run will retry the same delta. Idempotent writes make this safe.",
        context={
            "run_id": run_id,
            "delta_size": delta_size,
            "failures": failures,
            "sample_failures": sample_failures,
        },
    )
    _write_stage_row(
        stage_target, status=STATUS_FAILED, started_at=started_at, t0=t0,
        delta_size=delta_size, cost_observed_usd=accumulator.total_usd,
        records_written=sum(1 for o in outcomes if o.fully_succeeded),
        watermark_from=last_max_pmid or None, run_id=run_id,
        model_breakdown=accumulator.summary().get("by_model"),
        error=f"{failures}/{delta_size} pmids failed: {sample_failures}",
    )
    return RunResult(
        status=STATUS_FAILED,
        delta_size=delta_size,
        outcomes=outcomes,
        cost_estimate=cost_estimate,
        cost_observed_usd=accumulator.total_usd,
        cost_summary=accumulator.summary(),
        run_id=run_id,
        failure_reason=f"{failures}/{delta_size} pmids failed",
    )


def _process_one_pmid(
    row: dict,
    *,
    engine: Engine,
    client: Optional[BedrockClient],
    generate_synopsis: Callable,
    score_impact: Callable,
    accumulator: CostAccumulator,
) -> PmidOutcome:
    """Synopsis → MariaDB → Impact → MariaDB for a single pmid.

    Synopsis is the gating step: if synopsis fails (LLM call OR MariaDB
    write), skip the impact call. Impact failure after synopsis success
    leaves the synopsis row in place — pmid is still a failed outcome.

    Cost is recorded against the accumulator after each LLM call (success
    OR failure — error-path results carry input_tokens=0 / output_tokens=0
    so they contribute nothing). Recording keys on each result's `.model`
    — the model actually used: `us.anthropic.claude-sonnet-4-6` on the
    Bedrock happy path, or `gpt-5.1` when the content-filter fallback
    fired. Both are `config/llm_prices.yaml` keys, and the same string is
    persisted to the MariaDB `model` column and the DDB IMPACT# item, so
    cost attribution and the recorded model are one value.
    """
    pmid = str(row["pmid"])
    outcome = PmidOutcome(pmid=pmid)

    # Entity registration is a precondition; treat its failure as a
    # synopsis failure for accounting purposes.
    try:
        entity_id = mariadb_writer.ensure_entity(engine, pmid=pmid)
    except Exception as e:
        outcome.synopsis_error = f"ensure_entity: {e}"
        return outcome

    # --- Synopsis ---
    syn = generate_synopsis(
        pmid=pmid,
        title=row.get("articleTitle") or "",
        journal=row.get("journalTitleVerbose"),
        year=row.get("articleYear"),
        abstract=row.get("abstractVarchar"),
        client=client,
    )
    if syn.input_tokens or syn.output_tokens:
        accumulator.record(
            model=syn.model,
            input_tokens=syn.input_tokens,
            output_tokens=syn.output_tokens,
        )
    if syn.synopsis is None:
        outcome.synopsis_error = syn.error or "synopsis call returned None"
        return outcome
    try:
        mariadb_writer.upsert_synopsis(
            engine,
            pmid=pmid,
            entity_id=entity_id,
            synopsis=syn.synopsis,
            model=syn.model,
        )
    except Exception as e:
        outcome.synopsis_error = f"upsert_synopsis: {e}"
        return outcome
    outcome.synopsis_ok = True
    outcome.synopsis = syn.synopsis
    outcome.synopsis_model = syn.model

    # --- Impact (only on synopsis success) ---
    imp = score_impact(pub_data=row, client=client)
    if imp.input_tokens or imp.output_tokens:
        accumulator.record(
            model=imp.model,
            input_tokens=imp.input_tokens,
            output_tokens=imp.output_tokens,
        )
    if imp.impact_score is None:
        outcome.impact_error = imp.error or "impact call returned None"
        return outcome
    try:
        mariadb_writer.upsert_impact(
            engine,
            pmid=pmid,
            entity_id=entity_id,
            impact_score=imp.impact_score,
            justification=imp.justification or "",
            model=imp.model,
        )
    except Exception as e:
        outcome.impact_error = f"upsert_impact: {e}"
        return outcome
    outcome.impact_ok = True
    outcome.impact_score = imp.impact_score
    outcome.justification = imp.justification or ""
    outcome.impact_model = imp.model
    # Single enriched_at for both attributes — synopsis + impact land
    # within seconds of each other in this same call. Stamped after
    # impact MariaDB write succeeds so the timestamp reflects the moment
    # the row is fully durable in MariaDB.
    outcome.enriched_at = now_iso()

    return outcome


# ---------------------------------------------------------------------------
# Enrichment backfill — explicit-PMID synopsis + impact (#112 / onboarding)
# ---------------------------------------------------------------------------
#
# `run_enrichment_backfill` is the watermark-free sibling of
# `run_daily_enrichment`. The daily job is structurally watermark-forward-only
# and cannot reach the historical publications a newly-onboarded researcher
# brings (#80 / #112); this entry point enriches an explicit PMID work set
# instead. It reuses `_process_one_pmid` unchanged — a backfilled PMID gets
# byte-identical synopsis + impact + IMPACT# treatment to a daily-enriched
# one — and drops only the watermark machinery. Unlike the daily job it is
# partial-tolerant: with no watermark to protect, PMIDs that succeed are
# committed and IMPACT#-batched even when others in the set fail.

STATUS_PARTIAL = "partial"  # Backfill: ≥1 PMID succeeded AND ≥1 failed.


@dataclass
class EnrichmentBackfillResult:
    """Outcome of one `run_enrichment_backfill` invocation.

    `requested` is the de-duplicated input size; `already_complete` the
    count culled by the synopsis+impact idempotency check (skipped under
    --force); `unresolved` PMIDs that survived the cull but are absent from
    `analysis_summary_article` / pre-2020; `attempted` the count actually
    sent through `_process_one_pmid`; `succeeded` / `failed` partition it.
    """

    status: str
    requested: int = 0
    already_complete: int = 0
    unresolved: int = 0
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    dry_run: bool = False
    forced: bool = False
    outcomes: list[PmidOutcome] = field(default_factory=list)
    unresolved_pmids: list[str] = field(default_factory=list)
    cost_estimate_usd: Optional[Decimal] = None
    cost_observed_usd: Optional[Decimal] = None
    cost_summary: Optional[dict] = None
    impact_rows_written: Optional[int] = None
    failure_reason: Optional[str] = None


def run_enrichment_backfill(
    *,
    pmids: list[str],
    engine: Engine,
    client: Optional[BedrockClient] = None,
    ddb_table: Any = None,
    generate_synopsis: Callable = _default_generate_synopsis,
    score_impact: Callable = _default_score_impact,
    alert_fn: Callable = alerting.alert,
    per_paper_usd: Decimal = cost_guard.DEFAULT_PER_PAPER_USD,
    dry_run: bool = False,
    force: bool = False,
) -> EnrichmentBackfillResult:
    """Backfill synopsis + impact for an explicit PMID set (#112).

    The watermark-free sibling of `run_daily_enrichment`, for the historical
    publications the daily delta cannot reach. Per PMID it produces the same
    three writes the daily job does — `reciterai_synopsis` + `reciterai_impact`
    (MariaDB) and an end-of-run `IMPACT#` row (DynamoDB).

    Args:
        pmids: the work set; de-duplicated internally.
        engine: sqlalchemy Engine for MariaDB (required).
        client, ddb_table, generate_synopsis, score_impact, alert_fn:
            injectable for testing, as in `run_daily_enrichment`.
        per_paper_usd: per-paper rate for the surfaced cost ESTIMATE only.
            The backfill is bootstrap-class — it surfaces the estimate but
            enforces no threshold (an intentionally large historical set is
            the point; this mirrors `run_daily_enrichment`'s `--full`).
        dry_run: resolve the work set, cull, and surface the cost estimate,
            but generate nothing.
        force: bypass the synopsis+impact idempotency cull and reprocess
            every requested PMID (operator recovery — every write is an
            idempotent upsert, so reprocessing is safe).

    Returns:
        EnrichmentBackfillResult. `status` is `complete` (all attempted
        succeeded), `partial` (some succeeded, some failed), `failed` (all
        attempted failed), `no_op` (empty work set, or everything already
        enriched), or `ddb_batch_failed` (per-PMID MariaDB writes succeeded
        but the end-of-run IMPACT# batch raised — re-run with --force).
    """
    requested = sorted({str(p).strip() for p in pmids if str(p).strip()})
    if not requested:
        logger.info("enrichment backfill: empty work set — nothing to do")
        return EnrichmentBackfillResult(status=STATUS_NO_OP)

    # 1. Idempotency cull — skip PMIDs already carrying BOTH synopsis and
    #    impact, unless --force. A --from-gap-scan work set is entirely
    #    synopsis-less, so nothing is culled on a first run; the cull matters
    #    for re-runs and operator-supplied --pmids sets.
    if force:
        work_pmids, already_complete = requested, 0
    else:
        coverage = check_enrichment_coverage(get_dynamo_client(), requested)
        work_pmids = coverage["incomplete"]
        already_complete = len(coverage["complete"])
    if not work_pmids:
        logger.info(
            "enrichment backfill: all %d requested PMID(s) already have "
            "synopsis + impact — no-op", len(requested),
        )
        return EnrichmentBackfillResult(
            status=STATUS_NO_OP, requested=len(requested),
            already_complete=already_complete, forced=force,
        )

    # 2. Resolve the publication rows. PMIDs absent from
    #    analysis_summary_article (or pre-2020) drop out here.
    rows = fetch_publications_for_enrichment(engine, work_pmids)
    resolved = {str(r["pmid"]) for r in rows}
    unresolved = sorted(set(work_pmids) - resolved)
    if unresolved:
        logger.warning(
            "enrichment backfill: %d PMID(s) not in analysis_summary_article "
            "/ pre-2020 cutoff — skipped: %s%s",
            len(unresolved), ", ".join(unresolved[:10]),
            " …" if len(unresolved) > 10 else "",
        )

    # 3. Cost estimate — surfaced, never enforced (bootstrap-class work).
    cost_estimate = cost_guard.estimate_run_cost(len(rows), per_paper_usd)
    logger.info(
        "enrichment backfill: %d PMID(s) to enrich "
        "(requested=%d, already_complete=%d, unresolved=%d), "
        "estimated cost ~$%s%s",
        len(rows), len(requested), already_complete, len(unresolved),
        cost_estimate, " [dry-run]" if dry_run else "",
    )

    base = dict(
        requested=len(requested),
        already_complete=already_complete,
        unresolved=len(unresolved),
        unresolved_pmids=unresolved,
        forced=force,
        cost_estimate_usd=cost_estimate,
    )

    # 4. Dry run — preview only, no LLM calls.
    if dry_run:
        return EnrichmentBackfillResult(
            status=STATUS_COMPLETE if rows else STATUS_NO_OP,
            dry_run=True, **base,
        )
    if not rows:
        # Every work PMID was unresolved — nothing to enrich.
        return EnrichmentBackfillResult(status=STATUS_NO_OP, **base)

    # 5. Per-PMID synopsis + impact, reusing the daily job's worker.
    accumulator = CostAccumulator()
    outcomes: list[PmidOutcome] = []
    for row in rows:
        outcome = _process_one_pmid(
            row,
            engine=engine,
            client=client,
            generate_synopsis=generate_synopsis,
            score_impact=score_impact,
            accumulator=accumulator,
        )
        outcomes.append(outcome)
        if not outcome.fully_succeeded:
            logger.warning(
                "enrichment backfill: pmid=%s failed (synopsis_ok=%s, "
                "impact_ok=%s, synopsis_err=%s, impact_err=%s)",
                outcome.pmid, outcome.synopsis_ok, outcome.impact_ok,
                outcome.synopsis_error, outcome.impact_error,
            )
    succeeded = [o for o in outcomes if o.fully_succeeded]
    failed = [o for o in outcomes if not o.fully_succeeded]

    # 6. End-of-run IMPACT# dual-write for the succeeded PMIDs. Partial-
    #    tolerant: run_daily_enrichment writes the batch only on all-success
    #    (to protect the watermark); the backfill has no watermark, so it
    #    commits the IMPACT# rows it can and reports the rest.
    impact_rows_written = 0
    ddb_batch_error: Optional[str] = None
    if succeeded:
        ddb_target = ddb_table if ddb_table is not None else _default_get_table()
        impact_items = [
            ddb_writer.build_impact_item(
                pmid=o.pmid,
                impact_score=o.impact_score,
                justification=o.justification,
                impact_model=o.impact_model,
                synopsis=o.synopsis,
                synopsis_model=o.synopsis_model,
                enriched_at=o.enriched_at,
            )
            for o in succeeded
        ]
        try:
            impact_rows_written = ddb_writer.write_impact_batch(
                ddb_target, impact_items
            )
        except Exception as e:  # noqa: BLE001
            ddb_batch_error = str(e)
            logger.error(
                "enrichment backfill: IMPACT# batch failed after MariaDB "
                "writes — %d PMID(s) have synopsis+impact in MariaDB but no "
                "IMPACT# row. Re-run with --force. error=%s",
                len(succeeded), e,
            )
        else:
            # #212 Part B — back-propagate impact_score onto pre-existing
            # TOPIC# rows for the just-enriched PMIDs. Best-effort and
            # partial-tolerant: the backfill has no watermark to protect, so a
            # propagation failure is logged + alerted but never changes the
            # terminal status. Only runs when the IMPACT# batch itself
            # succeeded (no point copying values whose IMPACT# write failed).
            _propagate_impact_best_effort(
                ddb_target, impact_items,
                alert_fn=alert_fn, run_id=None,
                context_label="enrichment backfill",
            )

    # 7. Terminal status + operator alert.
    if ddb_batch_error is not None:
        status = STATUS_DDB_BATCH_FAILED
        failure_reason = f"IMPACT# batch failed: {ddb_batch_error}"
    elif not failed:
        status, failure_reason = STATUS_COMPLETE, None
    elif succeeded:
        status = STATUS_PARTIAL
        failure_reason = f"{len(failed)}/{len(rows)} PMID(s) failed"
    else:
        status = STATUS_FAILED
        failure_reason = f"all {len(rows)} PMID(s) failed"

    result = EnrichmentBackfillResult(
        status=status,
        attempted=len(rows),
        succeeded=len(succeeded),
        failed=len(failed),
        outcomes=outcomes,
        cost_observed_usd=accumulator.total_usd,
        cost_summary=accumulator.summary(),
        impact_rows_written=impact_rows_written,
        failure_reason=failure_reason,
        **base,
    )
    if status in (STATUS_PARTIAL, STATUS_FAILED, STATUS_DDB_BATCH_FAILED):
        _alert_backfill(alert_fn, result, failed)
    elif status == STATUS_COMPLETE:
        _post_success_summary(
            alert_fn,
            mode="backfill (--force)" if force else "backfill",
            delta_size=len(succeeded),
            cost_observed_usd=accumulator.total_usd,
            watermark_before=None,
            watermark_after=None,
            content_filter_retries=_count_content_filter_retries(outcomes),
        )
    logger.info(
        "enrichment backfill: status=%s succeeded=%d failed=%d "
        "impact_rows_written=%d",
        status, len(succeeded), len(failed), impact_rows_written,
    )
    if failed:
        # Surface a copy-pasteable retry command so the operator can replay
        # the failed PMIDs without grepping the per-PMID warnings. Logs are
        # already large; we print the full list here so it survives a `tail`
        # to the runbook. The Teams alert (below) caps at 50 PMIDs for
        # readability.
        logger.warning(
            "enrichment backfill: retry %d failed PMID(s) with "
            "`python -m scripts.run_daily_enrichment --pmids %s`",
            len(failed), ",".join(str(o.pmid) for o in failed),
        )
    return result


def _count_content_filter_retries(outcomes: list[PmidOutcome]) -> int:
    """How many synopsis+impact calls hit the OpenAI content-filter fallback.

    Counts model attribution on the outcome objects: a fallback fire is
    recorded as ``model == GPT5_MODEL`` on the per-pmid result. One outcome
    can contribute 0, 1, or 2 (synopsis + impact each fall back independently).
    """
    return sum(
        1 for o in outcomes if o.synopsis_model == GPT5_MODEL
    ) + sum(
        1 for o in outcomes if o.impact_model == GPT5_MODEL
    )


def _post_success_summary(
    alert_fn: Callable,
    *,
    mode: str,
    delta_size: int,
    cost_observed_usd: Optional[Decimal],
    watermark_before: Optional[int],
    watermark_after: Optional[int],
    content_filter_retries: int,
) -> None:
    """Heartbeat INFO post at the end of a successful daily / backfill run (#133).

    Silence on success was the laptop-era choice; the Fargate-hosted cron
    can't distinguish a no-op tick from a broken cron without an explicit
    heartbeat. `delta_size=0` is the canonical no-op case — still post.
    """
    cost = cost_observed_usd if cost_observed_usd is not None else Decimal("0")
    title = f"Daily enrichment complete — {delta_size} PMID(s), ${cost}"
    context: dict[str, Any] = {
        "mode": mode,
        "delta_size": delta_size,
        "cost_observed_usd": str(cost),
        "content_filter_retries": content_filter_retries,
    }
    if watermark_before is not None or watermark_after is not None:
        context["watermark"] = (
            f"{watermark_before if watermark_before is not None else '∅'} "
            f"→ {watermark_after if watermark_after is not None else '∅'}"
        )
    alert_fn(
        "INFO",
        title,
        f"Run mode: {mode}. Processed {delta_size} PMID(s).",
        context=context,
        mention=False,
    )


def _alert_newly_quarantined(
    alert_fn: Callable,
    quarantined: list[PmidOutcome],
    *,
    threshold: int,
    run_id: str,
) -> None:
    """Send the operator an ERROR card listing newly-quarantined PMIDs (#137).

    Fires from the success path: the run advanced the watermark past these
    PMIDs but each of them crossed the consecutive-failure threshold and now
    needs operator triage (`python scripts/run_daily_enrichment.py
    --retry-quarantine` or `--clear-quarantine PMID`).

    Already-quarantined-then-skipped PMIDs do NOT route through here — they
    fired their ERROR card on the tick that crossed the threshold.
    """
    sample = ", ".join(
        f"{o.pmid} ({o.synopsis_error or o.impact_error or 'unknown'})"
        for o in quarantined[:5]
    )
    if len(quarantined) > 5:
        sample += f", … (+{len(quarantined) - 5} more)"
    alert_fn(
        "ERROR",
        f"Daily enrichment quarantined {len(quarantined)} PMID(s)",
        f"{len(quarantined)} PMID(s) crossed the consecutive-failure "
        f"threshold ({threshold}) and were written off so the delta could "
        "advance. The watermark moved past them; the run as a whole "
        "succeeded. Operator triage: `--retry-quarantine` to replay them, "
        "`--clear-quarantine PMID` to drop a row after a fix.",
        context={
            "run_id": run_id,
            "threshold": threshold,
            "quarantined_count": len(quarantined),
            "quarantined_pmids": sample,
        },
    )


def _alert_backfill(
    alert_fn: Callable,
    result: EnrichmentBackfillResult,
    failed_outcomes: list[PmidOutcome],
) -> None:
    """Send the operator a Teams alert for a non-clean backfill run.

    `no_op` runs do not alert; `complete` runs route through
    `_post_success_summary` (#133 heartbeat); `partial` / `failed` /
    `ddb_batch_failed` go through here.
    """
    if result.status == STATUS_DDB_BATCH_FAILED:
        alert_fn(
            "ERROR",
            "Enrichment backfill — IMPACT# batch failed",
            f"{result.succeeded} PMID(s) were written to MariaDB "
            "(reciterai_synopsis + reciterai_impact) but the end-of-run "
            "IMPACT# DynamoDB batch failed, so their impact scores are not "
            "yet in DynamoDB. Re-run the backfill with --force to retry — "
            "every write is an idempotent upsert.",
            context={
                "succeeded": result.succeeded,
                "failure_reason": result.failure_reason,
            },
        )
        return
    sample = ", ".join(
        f"{o.pmid} ({o.synopsis_error or o.impact_error})"
        for o in failed_outcomes[:3]
    )
    if len(failed_outcomes) > 3:
        sample += f", … (+{len(failed_outcomes) - 3} more)"
    if result.status == STATUS_PARTIAL:
        severity = "WARN"
        title = "Enrichment backfill completed with gaps"
        message = (
            f"{result.failed} of {result.attempted} PMID(s) did not reach a "
            f"fully-enriched state; the other {result.succeeded} were "
            "synopsized, impact-scored, and IMPACT#-written. Re-running the "
            "backfill retries only the gaps (every write is idempotent)."
        )
    else:  # STATUS_FAILED
        severity = "ERROR"
        title = "Enrichment backfill failed"
        message = (
            f"All {result.attempted} PMID(s) failed — no synopsis or impact "
            "was written. Check the run log for the cause."
        )
    # Build a copy-pasteable retry command for the operator. Cap at 50
    # PMIDs so the Teams card stays readable; the run log carries the
    # full list (see logger.warning in run_enrichment_backfill).
    failed_pmids = [str(o.pmid) for o in failed_outcomes]
    if len(failed_pmids) <= 50:
        retry_command = (
            f"python -m scripts.run_daily_enrichment --pmids "
            f"{','.join(failed_pmids)}"
        )
    else:
        retry_command = (
            f"python -m scripts.run_daily_enrichment --pmids "
            f"{','.join(failed_pmids[:50])} "
            f"(+ {len(failed_pmids) - 50} more — see run log)"
        )
    alert_fn(
        severity, title, message,
        context={
            "attempted": result.attempted,
            "succeeded": result.succeeded,
            "failed": result.failed,
            "sample_failures": sample,
            "retry_command": retry_command,
        },
    )


# ---------------------------------------------------------------------------
# #137 — Operator-driven quarantine retry
# ---------------------------------------------------------------------------
#
# `retry_quarantined_pmids` is the operator's batch-recovery entry point for
# PMIDs the daily job has written off. Watermark-free, partial-tolerant; on
# per-PMID success it deletes the quarantine row, on failure it bumps the
# counter (without any threshold check — the operator is explicitly probing
# and doesn't want the row deleted by retry). No Teams alerts — the operator
# reads the printed result.


@dataclass
class QuarantineRetryResult:
    """Outcome of one `retry_quarantined_pmids` invocation.

    `attempted` is the count of quarantine rows replayed; `cleared` how many
    have had their row deleted by a successful retry; `still_failing` how many
    bumped the counter without clearing; `unresolved` rows whose PMIDs aren't
    in `analysis_summary_article` (pre-2020 or dropped).
    """

    attempted: int = 0
    cleared: int = 0
    still_failing: int = 0
    unresolved: int = 0
    outcomes: list[PmidOutcome] = field(default_factory=list)
    unresolved_pmids: list[str] = field(default_factory=list)
    cost_observed_usd: Optional[Decimal] = None
    cost_summary: Optional[dict] = None


def retry_quarantined_pmids(
    *,
    engine: Engine,
    client: Optional[BedrockClient] = None,
    ddb_table: Any = None,
    generate_synopsis: Callable = _default_generate_synopsis,
    score_impact: Callable = _default_score_impact,
    quarantine_threshold: Optional[int] = None,
) -> QuarantineRetryResult:
    """Replay every quarantined PMID through `_process_one_pmid`.

    Operator-interactive — no Teams alerts, no watermark touch. A retry that
    succeeds clears the row; a retry that fails bumps the counter (no
    threshold check — the row stays until the PMID either succeeds or the
    operator manually clears it with `clear_quarantine_for_pmid`).

    Returns a `QuarantineRetryResult` summarizing the batch. The caller is
    `scripts/run_daily_enrichment.py --retry-quarantine`.
    """
    if quarantine_threshold is None:
        quarantine_threshold = int(
            load_thresholds()["enrichment_quarantine_threshold"]
        )
    rows = quarantine.list_quarantined(
        threshold=quarantine_threshold, table=ddb_table,
    )
    if not rows:
        logger.info("retry-quarantine: no rows at or above threshold=%d",
                    quarantine_threshold)
        return QuarantineRetryResult()

    pmids = [r["pmid"] for r in rows]
    pub_rows = fetch_publications_for_enrichment(engine, pmids)
    resolved_by_pmid = {str(r["pmid"]): r for r in pub_rows}
    unresolved = sorted(set(pmids) - set(resolved_by_pmid))
    if unresolved:
        logger.warning(
            "retry-quarantine: %d PMID(s) not in analysis_summary_article "
            "/ pre-2020 cutoff — skipped: %s%s",
            len(unresolved), ", ".join(unresolved[:10]),
            " …" if len(unresolved) > 10 else "",
        )

    accumulator = CostAccumulator()
    outcomes: list[PmidOutcome] = []
    cleared = 0
    still_failing = 0
    for pmid in pmids:
        pub_row = resolved_by_pmid.get(pmid)
        if pub_row is None:
            continue
        outcome = _process_one_pmid(
            pub_row,
            engine=engine,
            client=client,
            generate_synopsis=generate_synopsis,
            score_impact=score_impact,
            accumulator=accumulator,
        )
        outcomes.append(outcome)
        if outcome.fully_succeeded:
            quarantine.clear(pmid, table=ddb_table)
            cleared += 1
            logger.info("retry-quarantine: pmid=%s cleared", pmid)
        else:
            reason = outcome.synopsis_error or outcome.impact_error or "unknown"
            new_count = quarantine.record_failure(
                pmid, reason=reason,
                run_id=f"retry-quarantine-{now_iso()}",
                table=ddb_table,
            )
            still_failing += 1
            logger.warning(
                "retry-quarantine: pmid=%s still failing (count=%d, reason=%s)",
                pmid, new_count, reason,
            )

    return QuarantineRetryResult(
        attempted=len(outcomes),
        cleared=cleared,
        still_failing=still_failing,
        unresolved=len(unresolved),
        outcomes=outcomes,
        unresolved_pmids=unresolved,
        cost_observed_usd=accumulator.total_usd,
        cost_summary=accumulator.summary(),
    )


def clear_quarantine_for_pmid(pmid: str, *, ddb_table: Any = None) -> None:
    """Operator action — delete a specific quarantine row.

    Used after an out-of-band code fix that should make the PMID processable
    on the next daily tick; the orchestrator's `is_quarantined` check no
    longer fires for the cleared PMID. Idempotent: clearing a row that
    doesn't exist is a no-op.
    """
    quarantine.clear(pmid, table=ddb_table)
    logger.info("clear-quarantine: pmid=%s cleared", pmid)
