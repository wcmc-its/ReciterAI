"""Daily area-signals drain: ``python -m pipeline_area_signals.run``.

Order of a run:

1. Taxonomy preflight (``utils.taxonomy_preflight``) before any spend. The
   image bakes ``taxonomy_v2.json``, and a stale image has shipped before; a
   blocked preflight fails the run (STAGE# failed row + Teams ERROR) without a
   single model call.
2. List ``PK=AREAINPUT`` and select what is due: ``pending`` items, then
   ``scored`` items whose ``taxonomy_version`` is not the current one.
3. Score in batches of ``--workers``. Each item re-reads its own status right
   before spending (SPS may have deleted or rewritten it since the listing).
   An item whose stored AREASIGNAL row already matches its input hash and the
   current taxonomy is marked scored with no model call (crash recovery).
4. Cost: every call is recorded from its own response (``signals``), and the
   run stops launching new batches once the next batch's estimate would cross
   ``--max-cost-usd``. What it did not reach stays due for the next run.
5. One ``STAGE#area_signals#GLOBAL`` row per run (measured cost, counts) for
   SPS's /edit/etl-status board, and a Teams alert when the run failed, an item
   reached ``failed``, or the cost ceiling stopped the drain.

``--dry-run`` lists what is due and the estimated cost; it makes no model call
and writes nothing (no status updates, no AREASIGNAL rows, no STAGE# row).

Exit codes: 0 when the run completed (including item failures and a ceiling
stop, which are reported, not fatal); 1 when it could not run.
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from pipeline_area_signals import drain, signals
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

# Per-item estimates for the dry-run report and the ceiling check, from
# docs/cost-model.md: screening ~$0.0035 + dense ~$0.009 per item, and a
# synopsis at about half of enrichment's measured $0.0141 synopsis+impact,
# rounded up. Measured spend replaces these on the STAGE# row.
EST_SCORE_USD = Decimal("0.0125")
EST_SYNOPSIS_USD = Decimal("0.0075")

# Synopsis and screening calls are small; bound a hung socket well below the
# client's 900 s default (same rationale as pipeline_cores triage).
BEDROCK_READ_TIMEOUT = 120

OUTCOME_SCORED = "scored"
OUTCOME_REUSED = "reused"          # stored row already current; no model call
OUTCOME_SKIPPED = "skipped"        # no longer due on re-read
OUTCOME_RETRY = "retry"            # transient failure, still due
OUTCOME_FAILED = "failed"          # terminal


@dataclass
class Outcome:
    sk: str
    kind: str
    status: str
    usage: list = field(default_factory=list)
    estimated_usd: Decimal = Decimal("0")
    error: Optional[str] = None
    content_filtered: bool = False


def estimate_item_usd(item: dict) -> Decimal:
    wants_synopsis = item.get("mode") == "full" and bool(str(item.get("text") or "").strip())
    return EST_SCORE_USD + (EST_SYNOPSIS_USD if wants_synopsis else Decimal("0"))


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


def process_one(table, item: dict, *, pool: _ClientPool, taxonomy: dict, int_to_id: dict,
                id_to_int: dict, taxonomy_version: str) -> Outcome:
    sk = str(item.get("SK") or "")
    kind = str(item.get("kind") or "")
    fresh = drain.get_input(table, sk)
    if not fresh or drain.due_reason(fresh, taxonomy_version) is None:
        return Outcome(sk, kind, OUTCOME_SKIPPED)
    attempts = int(fresh.get("attempts") or 0) + 1
    try:
        inp = signals.AreaInput.from_item(fresh)
    except signals.InvalidInput as exc:
        drain.mark_failed(table, sk, reason=f"invalid input: {exc}", attempts=attempts)
        return Outcome(sk, kind, OUTCOME_FAILED, error=str(exc))

    existing = drain.get_signal(table, inp.kind, inp.text_key)
    if (existing and existing.get("input_hash") == inp.input_hash()
            and existing.get("taxonomy_version") == taxonomy_version):
        drain.mark_scored(table, sk, taxonomy_version=taxonomy_version)
        return Outcome(sk, kind, OUTCOME_REUSED)

    bedrock = pool.get()
    try:
        result = signals.score_input(inp, bedrock=bedrock, taxonomy=taxonomy,
                                     int_to_id=int_to_id, id_to_int=id_to_int,
                                     taxonomy_version=taxonomy_version)
    except Exception as exc:  # noqa: BLE001 - one item never aborts the drain
        error = f"{type(exc).__name__}: {exc}"
        if attempts >= drain.MAX_ATTEMPTS:
            drain.mark_failed(table, sk, reason=error, attempts=attempts)
            status = OUTCOME_FAILED
        else:
            drain.record_attempt(table, sk, error=error, attempts=attempts)
            status = OUTCOME_RETRY
        log.warning("%s %s (attempt %d/%d): %s", status.upper(), sk, attempts,
                    drain.MAX_ATTEMPTS, error)
        return Outcome(sk, kind, status, usage=list(bedrock.usage), error=error)

    drain.put_signal(table, result.item)
    drain.mark_scored(table, sk, taxonomy_version=taxonomy_version)
    return Outcome(sk, kind, OUTCOME_SCORED, usage=result.usage,
                   estimated_usd=result.estimated_usd,
                   content_filtered=bool(result.item.get("scorer_fallback_model")))


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
) -> tuple[int, dict]:
    """Drain due AREAINPUT items. Returns (exit_code, summary)."""
    started_at, t0 = now_iso(), time.monotonic()
    taxonomy = taxonomy or load_taxonomy()
    taxonomy_version = signals.current_taxonomy_version(taxonomy)
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
    due = [(i, r) for i, r in drain.select_due(listed, taxonomy_version)
           if i.get("kind") in kinds or i.get("kind") not in signals.KINDS]
    if limit is not None:
        due = due[:limit]
    by_reason = Counter(r for _, r in due)
    by_kind_mode = Counter(f"{i.get('kind')}/{i.get('mode')}" for i, _ in due)
    estimate = sum((estimate_item_usd(i) for i, _ in due), Decimal("0"))

    if dry_run:
        summary = {
            "run_status": "dry_run", "taxonomy_version": taxonomy_version,
            "preflight_ok": preflight_ok, "preflight": preflight_msg,
            "due": len(due), "by_reason": dict(by_reason), "by_kind_mode": dict(by_kind_mode),
            "estimated_usd": str(estimate), "max_cost_usd": str(max_cost_usd),
            "would_stop_at_ceiling": estimate > max_cost_usd,
        }
        log.info("dry run: %s", summary)
        return 0, summary

    int_to_id, id_to_int = signals.sp.build_topic_index(taxonomy)
    pool = _ClientPool(client_factory)
    acc = CostAccumulator()
    estimated_extra = Decimal("0")
    counts: Counter = Counter()
    content_filtered = 0
    failures: list[str] = []
    budget_stopped = False
    run_error: Optional[str] = None

    def spent() -> Decimal:
        return acc.total_usd + estimated_extra

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for start in range(0, len(due), max(1, workers)):
                batch = due[start:start + max(1, workers)]
                batch_estimate = sum((estimate_item_usd(i) for i, _ in batch), Decimal("0"))
                if spent() + batch_estimate > max_cost_usd:
                    budget_stopped = True
                    log.warning("cost ceiling: spent $%s + next batch ~$%s > $%s; "
                                "%d item(s) left due for the next run", spent(),
                                batch_estimate, max_cost_usd, len(due) - start)
                    break
                futures = [ex.submit(process_one, table, item, pool=pool, taxonomy=taxonomy,
                                     int_to_id=int_to_id, id_to_int=id_to_int,
                                     taxonomy_version=taxonomy_version)
                           for item, _ in batch]
                for fut in futures:
                    outcome = fut.result()
                    for model, tin, tout in outcome.usage:
                        try:
                            acc.record(model=model, input_tokens=tin, output_tokens=tout)
                        except Exception as exc:  # noqa: BLE001 - unknown model price
                            log.warning("cost not recorded for %s: %s", model, exc)
                    estimated_extra += outcome.estimated_usd
                    counts[outcome.status] += 1
                    content_filtered += int(outcome.content_filtered)
                    if outcome.status == OUTCOME_FAILED:
                        failures.append(f"{outcome.sk}: {outcome.error}")
    except Exception as exc:  # noqa: BLE001 - a table/IO failure ends the run
        run_error = f"{type(exc).__name__}: {exc}"
        log.exception("area signals run failed")

    run_status = "failed" if run_error else ("budget_stopped" if budget_stopped else "complete")
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
    }
    records_written = counts[OUTCOME_SCORED]
    _write_stage_row(table, ok=run_error is None, started_at=started_at, t0=t0, cost=spent(),
                     records_written=records_written, taxonomy_version=taxonomy_version,
                     details=details, error=run_error)
    log.info("area signals summary: %s", details)

    context = {"outcomes": dict(counts), "spent_usd": str(spent()), "due": len(due)}
    if run_error:
        alert("ERROR", "Area signals run failed", run_error, context)
        return 1, details
    if failures:
        alert("WARN", f"Area signals: {len(failures)} input(s) failed",
              "These AREAINPUT items reached status 'failed' and will not be retried: "
              + "; ".join(failures[:10]), context)
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
    args = ap.parse_args(argv)

    from utils.dynamodb_helpers import get_table  # noqa: PLC0415

    code, _ = run(
        table=get_table(),
        dry_run=args.dry_run,
        max_cost_usd=args.max_cost_usd if args.max_cost_usd is not None else _max_cost_default(),
        workers=args.workers,
        limit=args.limit,
        kinds=(args.kind,) if args.kind else signals.KINDS,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
