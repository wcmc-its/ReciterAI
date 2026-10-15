"""Daily area-signals drain: ``python -m pipeline_area_signals.run``.

Order of a run:

1. Taxonomy preflight (``utils.taxonomy_preflight``) before any spend. The
   image bakes ``taxonomy_v2.json``, and a stale image has shipped before; a
   blocked preflight fails the run (STAGE# failed row + Teams ERROR) without a
   single model call.
2. List ``PK=AREAINPUT`` and select what is due: ``pending`` items, then
   ``scored`` items whose ``taxonomy_version`` or ``prompt_version`` is not the
   current one (``--requeue-failed`` adds ``failed`` items, attempts reset),
   then ``scored`` items whose ``subarea_version`` is not the current one.
3. Score in batches of ``--workers``. Each item re-reads its own status right
   before spending (SPS may have deleted or rewritten it since the listing),
   then claims it with a conditional lease so an overlapping run skips it.
   A scored item also gets its subareas (``subareas``): one Haiku call per
   qualifying area. An item whose stored AREASIGNAL row already matches its
   input hash and the current taxonomy is not re-scored: only its stale areas
   are re-assigned (none for a crash recovery that already finished them).
   That assign-only path is how a hierarchy recompute, a subarea prompt change
   and the one-time backfill of rows scored before subareas are handled. Its
   failures count ``subarea_attempts``; on the third the run stops retrying the
   item until the subarea version moves, and reports it.
4. Circuit breaker. A transient item failure is not written until the run
   knows it was the item's fault: failures since the last success are held, and
   written (attempt counted, ``failed`` on the third) when a later item
   succeeds or the run ends. A system-wide Bedrock error (bad or expired token,
   access denied, throttling or an outage that outlasted the client's retries,
   an unknown model id) or ``BREAKER_STREAK`` consecutive failures open the
   breaker: the run stops, the held items are released with no attempt counted,
   the STAGE# row is ``failed`` and Teams gets an ERROR. A total outage
   therefore never walks the queue to ``failed``.
5. Cost: every call is recorded from its own response (``signals``), and the
   run stops launching new batches once the next batch's estimate would cross
   ``--max-cost-usd``. What it did not reach stays due for the next run.
6. One ``STAGE#area_signals#GLOBAL`` row per run (measured cost, counts) for
   SPS's /edit/etl-status board, and a Teams alert when the run failed, an item
   reached ``failed``, or the cost ceiling stopped the drain.

``--dry-run`` lists what is due, the estimated assign calls and the estimated
cost; it makes no model call and writes nothing (no status updates, no
AREASIGNAL rows, no STAGE# row). It reads the AREASIGNAL row of each item due
for subareas only, to count that item's stale areas exactly.

Exit codes: 0 when the run completed (including item failures and a ceiling
stop, which are reported, not fatal); 1 when it could not run or the breaker
opened.
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from pipeline_area_signals import drain, signals, subareas
from pipeline_enrichment import alerting
from utils import stage_records
from utils.iso_clock import now_iso
from utils.llm_cost import CostAccumulator
from utils.taxonomy import load_taxonomy

log = logging.getLogger("pipeline_area_signals.run")

STAGE = "area_signals"
SCOPE = "GLOBAL"

MAX_COST_ENV = "RECITERAI_AREA_SIGNALS_MAX_COST_USD"
DEFAULT_MAX_COST_USD = Decimal("25")
DEFAULT_WORKERS = 4

# Consecutive transient failures, with no success between them, that open the
# breaker even when no single error is recognizably system-wide (an OpenAI
# fallback outage, say). Two full default batches: a run of bad items that long
# is far less likely than a broken dependency.
BREAKER_STREAK = 8

# Per-item estimates for the dry-run report and the ceiling check, from
# docs/cost-model.md: screening ~$0.0035 + dense ~$0.009 per item, and a
# synopsis at about half of enrichment's measured $0.0141 synopsis+impact,
# rounded up. Measured spend replaces these on the STAGE# row.
EST_SCORE_USD = Decimal("0.0125")
EST_SYNOPSIS_USD = Decimal("0.0075")

# One subarea classifier call: Haiku 5.5 (reasoning off) at $0.10/M in, $0.50/M
# out (Anthropic list price; config/llm_prices.yaml). Its tokenizer counts ~50%
# more input than 4.5 for the same prompt: measured median 4,574 input and 121
# output tokens per call = $0.000457 + $0.000061 = ~$0.00052; the largest area
# (~6k in, scaled from 4.5's ~4k; not measured) is ~$0.0007. The median rounded
# up: $0.0006. And the expected calls for
# an item not yet scored (pilot medians at the 0.3 floor: 7 areas per grant
# less the excluded ones, 4 per trial; rounded up). Measured spend replaces
# both on the STAGE# row.
EST_ASSIGN_USD = Decimal("0.0006")
EST_ASSIGN_CALLS = {"grant": 7, "trial": 5}

# Synopsis and screening calls are small; bound a hung socket well below the
# client's 900 s default (same rationale as pipeline_cores triage).
BEDROCK_READ_TIMEOUT = 120

OUTCOME_SCORED = "scored"
OUTCOME_REUSED = "reused"          # stored row already current; no scoring call
OUTCOME_SUBAREAS = "subareas"      # assign-only refresh of a scored row
OUTCOME_SKIPPED = "skipped"        # no longer due on re-read
OUTCOME_RETRY = "retry"            # transient failure, still due
OUTCOME_FAILED = "failed"          # terminal
OUTCOME_ERROR = "error"            # transient failure not yet written (process_one)
OUTCOME_RELEASED = "released"      # failure held when the breaker opened; no attempt


@dataclass
class Outcome:
    sk: str
    kind: str
    status: str
    usage: list = field(default_factory=list)
    estimated_usd: Decimal = Decimal("0")
    error: Optional[str] = None
    content_filtered: bool = False
    attempts: int = 0
    systemic: Optional[str] = None     # system-wide error description, if any
    assign_only: bool = False          # an ERROR from the assign-only path
    assign_calls: int = 0
    subareas_incomplete: bool = False  # scored, but some area's call failed


def expected_assign_calls(item: dict, ctx: subareas.SubareaContext) -> int:
    """Assign calls an item is expected to need, without reading its row."""
    if ctx.version is None:
        return 0
    return EST_ASSIGN_CALLS.get(str(item.get("kind") or ""), max(EST_ASSIGN_CALLS.values()))


def estimate_item_usd(item: dict, reason: str = drain.REASON_PENDING, *,
                      assign_calls: int = 0) -> Decimal:
    assign = EST_ASSIGN_USD * assign_calls
    if reason == drain.REASON_SUBAREA:
        return assign
    wants_synopsis = item.get("mode") == "full" and bool(str(item.get("text") or "").strip())
    return EST_SCORE_USD + (EST_SYNOPSIS_USD if wants_synopsis else Decimal("0")) + assign


def _max_cost_default() -> Decimal:
    raw = os.environ.get(MAX_COST_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_COST_USD
    try:
        return Decimal(raw)
    except InvalidOperation:
        log.warning("ignoring unparseable %s=%r", MAX_COST_ENV, raw)
        return DEFAULT_MAX_COST_USD


class _ClientPool:
    """One MeteredBedrockClient per worker thread (each item resets its usage)."""

    def __init__(self, factory: Callable[[], signals.MeteredBedrockClient]):
        self._factory = factory
        self._local = threading.local()

    def get(self) -> signals.MeteredBedrockClient:
        if not hasattr(self._local, "client"):
            self._local.client = self._factory()
        return self._local.client


def _row_is_current(row: Optional[dict], item: dict, taxonomy_version: str) -> bool:
    """process_one's test for reusing a stored AREASIGNAL row (no re-score)."""
    if row is None or row.get("taxonomy_version") != taxonomy_version:
        return False
    try:
        return row.get("input_hash") == signals.AreaInput.from_item(item).input_hash()
    except signals.InvalidInput:
        return False


def _assign_only(table, fresh: dict, existing: dict, inp: signals.AreaInput, *,
                 reason: str, pool: _ClientPool, ctx: subareas.SubareaContext,
                 taxonomy_version: str) -> Outcome:
    """Re-assign a current AREASIGNAL row's stale areas; no synopsis, no re-score."""
    sk, kind = str(fresh.get("SK") or ""), inp.kind
    status = OUTCOME_SUBAREAS if reason == drain.REASON_SUBAREA else OUTCOME_REUSED
    bedrock = pool.get()
    bedrock.usage, bedrock.errors = [], []
    result = None
    # Partial drafts: a topic whose draft is gone from this image has its
    # subarea fields stripped (fail-closed; an image rebuilt with the draft
    # refills them). No drafts at all (ctx.hierarchies empty) is a broken image,
    # not a decision, so the row is left as it is rather than stripped bare.
    if ctx.hierarchies and not subareas.is_current(existing, ctx):
        result = subareas.assign(existing, title=inp.title, client=bedrock, ctx=ctx,
                                 topics=subareas.stale_topics(existing, ctx))
        drain.put_signal(table, result.item)
    calls = result.calls if result else 0
    if result and result.errors:
        error = "subareas: " + "; ".join(f"{t}: {e}" for t, e in result.errors)
        attempts = int(fresh.get("subarea_attempts") or 0) + 1
        systemic = signals.systemic_error(bedrock.errors)
        log.warning("ERROR %s subareas (attempt %d/%d%s): %s", sk, attempts, drain.MAX_ATTEMPTS,
                    f", system-wide: {systemic}" if systemic else "", error)
        if reason != drain.REASON_SUBAREA:
            # Crash recovery of a scored row: it is scored; the areas whose call
            # failed come back through the subarea path on the next run.
            drain.mark_scored(table, sk, taxonomy_version=taxonomy_version)
            return Outcome(sk, kind, status, usage=list(bedrock.usage), assign_calls=calls,
                           subareas_incomplete=True)
        return Outcome(sk, kind, OUTCOME_ERROR, usage=list(bedrock.usage), error=error,
                       attempts=attempts, systemic=systemic, assign_only=True,
                       assign_calls=calls)
    if reason == drain.REASON_SUBAREA:
        drain.mark_subareas(table, sk, subarea_version=ctx.version)
    else:
        drain.mark_scored(table, sk, taxonomy_version=taxonomy_version,
                          subarea_version=ctx.version)
    return Outcome(sk, kind, status, usage=list(bedrock.usage), assign_calls=calls)


def process_one(table, item: dict, *, pool: _ClientPool, taxonomy: dict, int_to_id: dict,
                id_to_int: dict, taxonomy_version: str, run_id: str = "",
                requeue_failed: bool = False,
                subarea_ctx: Optional[subareas.SubareaContext] = None) -> Outcome:
    """Score one item. A transient failure comes back as OUTCOME_ERROR with
    nothing written but the lease; the run decides whether it counts."""
    ctx = subarea_ctx if subarea_ctx is not None else subareas.SubareaContext()
    sk = str(item.get("SK") or "")
    kind = str(item.get("kind") or "")
    fresh = drain.get_input(table, sk)
    reason = fresh and drain.due_reason(fresh, taxonomy_version,
                                        requeue_failed=requeue_failed,
                                        subarea_version=ctx.version)
    if not reason:
        return Outcome(sk, kind, OUTCOME_SKIPPED)
    prior = 0 if reason == drain.REASON_REQUEUED else int(fresh.get("attempts") or 0)
    attempts = prior + 1
    try:
        inp = signals.AreaInput.from_item(fresh)
    except signals.InvalidInput as exc:
        if fresh.get("status") != drain.STATUS_FAILED:
            drain.mark_failed(table, sk, reason=f"invalid input: {exc}", attempts=attempts)
        return Outcome(sk, kind, OUTCOME_FAILED, error=str(exc))

    if not drain.claim(table, fresh, run_id=run_id,
                       requeue=reason == drain.REASON_REQUEUED):
        return Outcome(sk, kind, OUTCOME_SKIPPED)

    existing = drain.get_signal(table, inp.kind, inp.text_key)
    if _row_is_current(existing, fresh, taxonomy_version):
        # Crash recovery, or (REASON_SUBAREA) a subarea refresh: the scores are
        # current, so only stale areas are re-assigned.
        return _assign_only(table, fresh, existing, inp, reason=reason, pool=pool, ctx=ctx,
                            taxonomy_version=taxonomy_version)

    bedrock = pool.get()
    try:
        result = signals.score_input(inp, bedrock=bedrock, taxonomy=taxonomy,
                                     int_to_id=int_to_id, id_to_int=id_to_int,
                                     taxonomy_version=taxonomy_version)
    except Exception as exc:  # noqa: BLE001 - one item never aborts the drain
        error = f"{type(exc).__name__}: {exc}"
        systemic = signals.systemic_error(bedrock.errors)
        log.warning("ERROR %s (attempt %d/%d%s): %s", sk, attempts, drain.MAX_ATTEMPTS,
                    f", system-wide: {systemic}" if systemic else "", error)
        return Outcome(sk, kind, OUTCOME_ERROR,
                       usage=list(bedrock.usage) + list(getattr(exc, "extra_usage", [])),
                       estimated_usd=getattr(exc, "estimated_usd", Decimal("0")),
                       error=error, attempts=attempts, systemic=systemic)

    # Subareas for the fresh scores. A failed area does not fail the item: the
    # row is stored with what succeeded and subarea_version is left unset, so
    # the next run re-assigns only the missing areas (assign-only path).
    mark = len(bedrock.usage)
    assigned = subareas.assign(result.item, title=inp.title, client=bedrock, ctx=ctx)
    assign_usage = list(bedrock.usage[mark:])
    drain.put_signal(table, assigned.item)
    drain.mark_scored(table, sk, taxonomy_version=taxonomy_version,
                      subarea_version=None if assigned.errors else ctx.version)
    return Outcome(sk, kind, OUTCOME_SCORED, usage=result.usage + assign_usage,
                   estimated_usd=result.estimated_usd,
                   content_filtered=bool(result.item.get("scorer_fallback_model")),
                   assign_calls=assigned.calls, subareas_incomplete=bool(assigned.errors))


def _write_stage_row(table, *, ok: bool, started_at: str, t0: float, cost: Decimal,
                     records_written: int, taxonomy_version: str, details: dict,
                     error: Optional[str] = None) -> None:
    """Best-effort: a STAGE# write failure must not fail a run that did its work."""
    try:
        common = dict(
            stage=STAGE, scope=SCOPE,
            # Provenance only; this stage never skips on it (the queue moves
            # under identical inputs every night).
            input_hash=stage_records.compute_input_hash(
                STAGE, {"taxonomy_version": taxonomy_version}),
            started_at=started_at,
            duration_ms=max(0, int((time.monotonic() - t0) * 1000)),
            cost_observed_usd=cost,
            tunable_inputs=details,
        )
        if ok:
            stage_records.write_complete(table, records_written=records_written, **common)
        else:
            stage_records.write_failed(table, error_code=details.get("run_status", "failed"),
                                       error_message=(error or "failed")[:1000], **common)
    except Exception as exc:  # noqa: BLE001
        log.warning("STAGE#%s#%s row write failed (best-effort): %s", STAGE, SCOPE, exc)


def run(
    *,
    table,
    dry_run: bool = False,
    max_cost_usd: Decimal = DEFAULT_MAX_COST_USD,
    workers: int = DEFAULT_WORKERS,
    limit: Optional[int] = None,
    kinds: tuple = signals.KINDS,
    client_factory: Optional[Callable[[], signals.MeteredBedrockClient]] = None,
    preflight: Optional[Callable[[], tuple]] = None,
    alert: Callable[..., Any] = alerting.alert,
    taxonomy: Optional[dict] = None,
    requeue_failed: bool = False,
    subarea_ctx: Optional[subareas.SubareaContext] = None,
) -> tuple[int, dict]:
    """Drain due AREAINPUT items. Returns (exit_code, summary)."""
    started_at, t0 = now_iso(), time.monotonic()
    run_id = f"{started_at}#{uuid.uuid4().hex[:8]}"
    taxonomy = taxonomy or load_taxonomy()
    taxonomy_version = signals.current_taxonomy_version(taxonomy)
    ctx = subarea_ctx if subarea_ctx is not None else subareas.load_context(taxonomy)
    subarea_version = ctx.version
    subarea_info = {"subarea_version": subarea_version,
                    "subarea_drafts_loaded": len(ctx.hierarchies),
                    "subarea_drafts_missing": list(ctx.missing)}
    if ctx.missing:
        log.warning("subareas: %d covered topic(s) have no usable draft in this image: %s",
                    len(ctx.missing), ", ".join(ctx.missing))
    if preflight is None:
        from utils.taxonomy_preflight import taxonomy_preflight as preflight
    if client_factory is None:
        def client_factory():
            return signals.MeteredBedrockClient(read_timeout=BEDROCK_READ_TIMEOUT)

    preflight_ok, preflight_msg = preflight()
    log.info(preflight_msg)
    if not preflight_ok and not dry_run:
        details = {"run_status": "preflight_blocked", "taxonomy_version": taxonomy_version}
        _write_stage_row(table, ok=False, started_at=started_at, t0=t0, cost=Decimal("0"),
                         records_written=0, taxonomy_version=taxonomy_version,
                         details=details, error=preflight_msg)
        alert("ERROR", "Area signals: taxonomy preflight blocked the run", preflight_msg,
              {"stage": f"STAGE#{STAGE}#{SCOPE}"})
        return 1, {"run_status": "preflight_blocked", "message": preflight_msg}

    try:
        listed = drain.list_inputs(table)
    except Exception as exc:  # noqa: BLE001 - nothing spent yet; report and stop
        error = f"listing AREAINPUT failed: {type(exc).__name__}: {exc}"
        log.exception(error)
        if not dry_run:
            _write_stage_row(table, ok=False, started_at=started_at, t0=t0, cost=Decimal("0"),
                             records_written=0, taxonomy_version=taxonomy_version,
                             details={"run_status": "failed"}, error=error)
            alert("ERROR", "Area signals run failed", error, {"stage": f"STAGE#{STAGE}#{SCOPE}"})
        return 1, {"run_status": "failed", "message": error}
    # An item of an unknown kind is never filtered out by --kind: it goes
    # through and is marked failed, rather than sitting pending forever.
    due = [(i, r) for i, r in drain.select_due(listed, taxonomy_version,
                                               requeue_failed=requeue_failed,
                                               subarea_version=subarea_version)
           if i.get("kind") in kinds or i.get("kind") not in signals.KINDS]
    if limit is not None:
        due = due[:limit]
    by_reason = Counter(r for _, r in due)
    by_kind_mode = Counter(f"{i.get('kind')}/{i.get('mode')}" for i, _ in due)

    def item_estimate(item: dict, reason: str, calls: Optional[int] = None) -> Decimal:
        n = expected_assign_calls(item, ctx) if calls is None else calls
        return estimate_item_usd(item, reason, assign_calls=n)

    if dry_run:
        # Exact stale-area counts for subarea refreshes (one GetItem each); the
        # per-kind expectation for items that are scored first.
        assign_calls = 0
        estimate = Decimal("0")
        for item, reason in due:
            calls = None
            if reason == drain.REASON_SUBAREA:
                row = drain.get_signal(table, str(item.get("kind")), str(item.get("text_key")))
                if _row_is_current(row, item, taxonomy_version):
                    calls = len(subareas.stale_topics(row, ctx))
                else:
                    # process_one re-scores an item whose row is gone or out of
                    # date before assigning, so estimate it as a full score.
                    reason = drain.REASON_PENDING
            if calls is None:
                calls = expected_assign_calls(item, ctx)
            assign_calls += calls
            estimate += item_estimate(item, reason, calls)
        summary = {
            "run_status": "dry_run", "taxonomy_version": taxonomy_version,
            "preflight_ok": preflight_ok, "preflight": preflight_msg,
            "due": len(due), "by_reason": dict(by_reason), "by_kind_mode": dict(by_kind_mode),
            "estimated_assign_calls": assign_calls,
            "estimated_usd": str(estimate), "max_cost_usd": str(max_cost_usd),
            "would_stop_at_ceiling": estimate > max_cost_usd,
            **subarea_info,
        }
        log.info("dry run: %s", summary)
        return 0, summary

    estimate = sum((item_estimate(i, r) for i, r in due), Decimal("0"))

    int_to_id, id_to_int = signals.sp.build_topic_index(taxonomy)
    pool = _ClientPool(client_factory)
    acc = CostAccumulator()
    estimated_extra = Decimal("0")
    counts: Counter = Counter()
    content_filtered = 0
    assign_calls = 0
    subareas_incomplete = 0
    failures: list[str] = []
    budget_stopped = False
    run_error: Optional[str] = None
    breaker: Optional[str] = None
    held: list[Outcome] = []   # transient failures since the last success, unwritten

    def spent() -> Decimal:
        return acc.total_usd + estimated_extra

    def write_failure(o: Outcome) -> None:
        if o.assign_only:
            # Scores stay; only the subarea refresh failed. The third failure
            # stops retrying it until the subarea version moves.
            if o.attempts >= drain.MAX_ATTEMPTS:
                drain.mark_subareas(table, o.sk, subarea_version=subarea_version,
                                    error=o.error or "failed")
                counts[OUTCOME_FAILED] += 1
                failures.append(f"{o.sk}: {o.error}")
            else:
                drain.record_subarea_attempt(table, o.sk, error=o.error or "failed",
                                             attempts=o.attempts)
                counts[OUTCOME_RETRY] += 1
            return
        if o.attempts >= drain.MAX_ATTEMPTS:
            drain.mark_failed(table, o.sk, reason=o.error or "failed", attempts=o.attempts)
            counts[OUTCOME_FAILED] += 1
            failures.append(f"{o.sk}: {o.error}")
        else:
            drain.record_attempt(table, o.sk, error=o.error or "failed", attempts=o.attempts)
            counts[OUTCOME_RETRY] += 1

    def flush_held() -> None:
        while held:
            write_failure(held.pop(0))

    def release_held() -> None:
        while held:
            drain.release(table, held.pop(0).sk)
            counts[OUTCOME_RELEASED] += 1

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for start in range(0, len(due), max(1, workers)):
                batch = due[start:start + max(1, workers)]
                batch_estimate = sum((item_estimate(i, r) for i, r in batch), Decimal("0"))
                if spent() + batch_estimate > max_cost_usd:
                    budget_stopped = True
                    log.warning("cost ceiling: spent $%s + next batch ~$%s > $%s; "
                                "%d item(s) left due for the next run", spent(),
                                batch_estimate, max_cost_usd, len(due) - start)
                    break
                futures = [ex.submit(process_one, table, item, pool=pool, taxonomy=taxonomy,
                                     int_to_id=int_to_id, id_to_int=id_to_int,
                                     taxonomy_version=taxonomy_version, run_id=run_id,
                                     requeue_failed=requeue_failed, subarea_ctx=ctx)
                           for item, _ in batch]
                for fut in futures:
                    outcome = fut.result()
                    for model, tin, tout in outcome.usage:
                        try:
                            acc.record(model=model, input_tokens=tin, output_tokens=tout)
                        except Exception as exc:  # noqa: BLE001 - unknown model price
                            log.warning("cost not recorded for %s: %s", model, exc)
                    estimated_extra += outcome.estimated_usd
                    content_filtered += int(outcome.content_filtered)
                    assign_calls += outcome.assign_calls
                    subareas_incomplete += int(outcome.subareas_incomplete)
                    if outcome.status == OUTCOME_ERROR:
                        held.append(outcome)
                        if breaker is None and outcome.systemic:
                            breaker = f"{outcome.systemic} ({outcome.sk}: {outcome.error})"
                        elif breaker is None and len(held) >= BREAKER_STREAK:
                            breaker = (f"{len(held)} consecutive item failures with no "
                                       f"success; last: {outcome.sk}: {outcome.error}")
                        if breaker:
                            release_held()
                        continue
                    if (outcome.status in (OUTCOME_SCORED, OUTCOME_REUSED, OUTCOME_SUBAREAS)
                            and breaker is None):
                        flush_held()  # the system works, so those failures were the items'
                    counts[outcome.status] += 1
                    if outcome.status == OUTCOME_FAILED:
                        failures.append(f"{outcome.sk}: {outcome.error}")
                if breaker:
                    log.error("circuit breaker open, stopping the run: %s", breaker)
                    break
        if breaker is None:
            flush_held()
    except Exception as exc:  # noqa: BLE001 - a table/IO failure ends the run
        run_error = f"{type(exc).__name__}: {exc}"
        log.exception("area signals run failed")

    if breaker and not run_error:
        run_error = f"circuit breaker: {breaker}"
    run_status = ("aborted" if breaker else "failed") if run_error else (
        "budget_stopped" if budget_stopped else "complete")
    details = {
        "run_status": run_status,
        "taxonomy_version": taxonomy_version,
        "due": len(due),
        "by_reason": dict(by_reason),
        "outcomes": dict(counts),
        "max_cost_usd": str(max_cost_usd),
        "estimated_usd_before_run": str(estimate),
        "measured_usd": str(acc.total_usd),
        "estimated_fallback_usd": str(estimated_extra),
        "model_breakdown": acc.summary()["by_model"],
        "content_filter_fallbacks": content_filtered,
        "assign_calls": assign_calls,
        "subareas_incomplete": subareas_incomplete,
        **subarea_info,
    }
    records_written = counts[OUTCOME_SCORED] + counts[OUTCOME_SUBAREAS]
    _write_stage_row(table, ok=run_error is None, started_at=started_at, t0=t0, cost=spent(),
                     records_written=records_written, taxonomy_version=taxonomy_version,
                     details=details, error=run_error)
    log.info("area signals summary: %s", details)

    context = {"outcomes": dict(counts), "spent_usd": str(spent()), "due": len(due)}
    if breaker:
        alert("ERROR", "Area signals run stopped by its circuit breaker",
              f"{breaker}. No attempt was counted against the held items; they stay "
              "due. Fix the dependency (Bedrock token, quota, OpenAI key) and the next "
              "run picks them up.", context)
        return 1, details
    if run_error:
        alert("ERROR", "Area signals run failed", run_error, context)
        return 1, details
    if failures:
        alert("WARN", f"Area signals: {len(failures)} input(s) failed",
              "These AREAINPUT items failed for the third time and will not be retried "
              "(a 'subareas:' failure keeps its scores and is retried when the subarea "
              "version next moves; any other reached status 'failed'): "
              + "; ".join(failures[:10]), context)
    if ctx.missing:
        alert("WARN", f"Area signals: {len(ctx.missing)} subarea draft(s) missing from the image",
              "These covered research areas have no usable hierarchy draft in this image, so "
              "grant and trial texts get no subareas there: " + ", ".join(ctx.missing[:20])
              + ". Rebuild the image from a checkout that has the approved drafts.",
              context, mention=False)
    if budget_stopped:
        alert("WARN", "Area signals: cost ceiling reached",
              f"Stopped at ${spent()} of a ${max_cost_usd} ceiling; the rest stays due "
              "for the next run.", context, mention=False)
    return 0, details


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Score SPS grant/trial texts into research areas "
                                             "(AREAINPUT -> AREASIGNAL#)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what is due and the estimated cost; no model calls, no writes")
    ap.add_argument("--max-cost-usd", type=Decimal, default=None,
                    help=f"per-run ceiling (default ${DEFAULT_MAX_COST_USD}, or ${MAX_COST_ENV})")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--limit", type=int, default=None, help="score at most N due items")
    ap.add_argument("--kind", choices=signals.KINDS, default=None, help="only this kind")
    ap.add_argument("--requeue-failed", action="store_true",
                    help="also score items in status 'failed' (attempts reset), e.g. after "
                         "an outage that failed them")
    args = ap.parse_args(argv)

    from utils.dynamodb_helpers import get_table  # noqa: PLC0415

    code, _ = run(
        table=get_table(),
        dry_run=args.dry_run,
        max_cost_usd=args.max_cost_usd if args.max_cost_usd is not None else _max_cost_default(),
        workers=args.workers,
        limit=args.limit,
        kinds=(args.kind,) if args.kind else signals.KINDS,
        requeue_failed=args.requeue_failed,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
