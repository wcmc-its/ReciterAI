"""Daily enrichment orchestrator (#37 step 2 + step 3 PR 3.1).

One cycle:
    read_watermark  →  fetch_new_publications  →  cost_guard  →
        per-pmid loop ( ensure_entity → synopsis → impact → MariaDB ) →
        end-of-run DDB IMPACT# batch dual-write →
        advance watermark on full success

Step 2 (already shipped): MariaDB writes for synopsis + impact only.
Step 3 PR 3.1 (this PR): adds an end-of-run batch dual-write of the same
synopsis + impact data into DDB IMPACT#pmid_{pmid}/SK SCORE items, per the
SPS reader contract documented in `pipeline_enrichment/ddb_writer.py`.
Topic scoring, STAGE# row, cross-check, and backfill land in PRs 3.2–3.4
and the topic-scoring takeover question is split off to issue #52.

DDB write timing within the loop is "batch at end-of-run after all MariaDB
writes succeed" (not per-pmid inline) so the failure model stays
all-or-nothing per run: a failed DDB batch leaves the watermark
unadvanced and the next retry redoes the whole delta. DDB writes are
upserts so retry is safe.

Failure model (per #37's "Failed runs leave watermark untouched" spec):
- All-or-nothing watermark advancement. If ANY pmid fails (synopsis OR
  impact OR a MariaDB write), the run is marked failed and the watermark
  stays at its previous value. The next run re-attempts the same delta;
  idempotent writes in mariadb_writer make that safe.
- Within a single pmid, a failed synopsis short-circuits the impact call
  (no point scoring impact for a paper we couldn't summarize). A failed
  impact after a successful synopsis leaves the synopsis row in place —
  the pmid is still counted as a failure but the next retry only re-does
  the impact.

Bootstrap: per #37, the annual rescore uses `--full` to bypass the cost
guard. The same flag bootstraps the first daily run on a stale watermark
(the ~1,865-paper backlog as of 2026-05-14).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Optional

from openai import OpenAI
from sqlalchemy.engine import Engine

from pipeline_enrichment import alerting, cost_guard, ddb_writer, mariadb_writer
from pipeline_enrichment import watermark as wm
from pipeline_enrichment.impact import score_impact as _default_score_impact
from pipeline_enrichment.synopsis import generate_synopsis as _default_generate_synopsis
from utils.dynamodb_helpers import get_table as _default_get_table
from utils.iso_clock import now_iso
from utils.llm_cost import CostAccumulator
from utils.openai_client import GPT5_MODEL
from utils.sql_queries import fetch_new_publications

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
    """

    pmid: str
    synopsis_ok: bool = False
    impact_ok: bool = False
    synopsis_error: Optional[str] = None
    impact_error: Optional[str] = None
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


# Status values returned in RunResult.
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


def run_daily_enrichment(
    *,
    full: bool = False,
    limit: int = 1000,
    threshold_usd: Decimal = cost_guard.DEFAULT_THRESHOLD_USD,
    per_paper_usd: Decimal = cost_guard.DEFAULT_PER_PAPER_USD,
    engine: Engine,
    openai_client: Optional[OpenAI] = None,
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
        engine: sqlalchemy Engine for MariaDB (required).
        openai_client: optional injected OpenAI client; defaults to the
            lazy singleton in pipeline_enrichment.synopsis/impact.
        ddb_table: optional injected DDB Table resource for the
            watermark; defaults to utils.dynamodb_helpers.get_table().
        generate_synopsis, score_impact, alert_fn: injectable for
            testing. Pass a no-op for alert_fn to suppress Teams posts
            during local development without unsetting the webhook env.

    Returns:
        RunResult with status + per-pmid outcomes.
    """
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

    # Empty delta — return without touching the watermark.
    if delta_size == 0:
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
    accumulator = CostAccumulator()
    outcomes: list[PmidOutcome] = []
    for row in delta:
        outcome = _process_one_pmid(
            row,
            engine=engine,
            openai_client=openai_client,
            generate_synopsis=generate_synopsis,
            score_impact=score_impact,
            accumulator=accumulator,
        )
        outcomes.append(outcome)
        if not outcome.fully_succeeded:
            logger.warning(
                "daily run: pmid=%s failed (synopsis_ok=%s, impact_ok=%s, "
                "synopsis_err=%s, impact_err=%s)",
                outcome.pmid, outcome.synopsis_ok, outcome.impact_ok,
                outcome.synopsis_error, outcome.impact_error,
            )
    logger.info(
        "daily run: measured cost $%s across %d calls (input=%d, output=%d tokens)",
        accumulator.total_usd, accumulator.call_count,
        accumulator.total_input_tokens, accumulator.total_output_tokens,
    )

    # 6. Advance watermark on all-success.
    all_succeeded = all(o.fully_succeeded for o in outcomes)
    if all_succeeded:
        # 6a. End-of-run DDB IMPACT# batch dual-write (#37 step 3 PR 3.1).
        # Sequenced BEFORE mark_run_complete so a DDB batch failure leaves
        # the watermark unadvanced. Next run's retry redoes the same delta;
        # MariaDB upserts and DDB upserts are both idempotent.
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
            for o in outcomes
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
    failures = sum(1 for o in outcomes if not o.fully_succeeded)
    logger.error(
        "daily run: failed — %d/%d pmids failed, watermark NOT advanced",
        failures, delta_size,
    )
    failed_outcomes = [o for o in outcomes if not o.fully_succeeded]
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
    openai_client: Optional[OpenAI],
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
    so they contribute nothing). Recording uses the price-table model key
    `GPT5_MODEL` ("gpt-5.1"), NOT the dated `response.model` that lands in
    MariaDB. OpenAI prices by requested model; the dated snapshot is for
    audit only.
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
        client=openai_client,
    )
    if syn.input_tokens or syn.output_tokens:
        accumulator.record(
            model=GPT5_MODEL,
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
    imp = score_impact(pub_data=row, client=openai_client)
    if imp.input_tokens or imp.output_tokens:
        accumulator.record(
            model=GPT5_MODEL,
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
