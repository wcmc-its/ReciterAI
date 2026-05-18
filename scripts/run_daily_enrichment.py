#!/usr/bin/env python3
"""Operator entry point for the daily enrichment job (#37 step 2) and the
enrichment backfill (#112).

Wraps `pipeline_enrichment.daily_job.run_daily_enrichment` for command-line
invocation. The Lambda/ECS handler in PR 2 will share the same orchestrator
function, so this CLI is the canonical way to exercise the code path
locally and the way to run cold-start backfills (--full) until automation
takes over.

With `--pmids` or `--from-gap-scan` the CLI instead invokes
`run_enrichment_backfill`: synopsis + impact for an explicit historical PMID
set the watermark-forward daily delta cannot reach (#80 / #112).

Examples:

    # Routine daily run (cost guard active):
    python -m scripts.run_daily_enrichment

    # Cold-start backfill / annual rescore — bypass cost guard:
    python -m scripts.run_daily_enrichment --full

    # Operator catch-up with a widened threshold:
    python -m scripts.run_daily_enrichment --threshold-usd 100

    # Enrichment backfill (#112) — synopsis + impact for the onboarding
    # detector's gap set; preview first, then run:
    python -m scripts.run_daily_enrichment --from-gap-scan --dry-run
    python -m scripts.run_daily_enrichment --from-gap-scan

    # Backfill an explicit PMID set:
    python -m scripts.run_daily_enrichment --pmids 39001234,39005678
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from decimal import Decimal

from pipeline_enrichment.daily_job import (
    STATUS_COMPLETE,
    STATUS_NO_OP,
    run_daily_enrichment,
    run_enrichment_backfill,
)
from utils.db import get_engine


def _noop_alert(*args, **kwargs):
    """Silent alert function for --no-alerts local iteration."""
    return False


def _result_to_json(result) -> str:
    """Serialize RunResult for log aggregators. Decimals → strings."""
    def _default(o):
        if isinstance(o, Decimal):
            return str(o)
        if hasattr(o, "__dict__"):
            return o.__dict__
        raise TypeError(repr(o))
    return json.dumps(asdict(result), default=_default, indent=2)


def _run_backfill(engine, args):
    """Resolve the backfill work set and invoke run_enrichment_backfill.

    `--pmids` is an explicit comma-separated set; `--from-gap-scan` derives
    the set from the onboarding detector's faculty gap scan — every
    first/last-author faculty PMID with no synopsis (#112).
    """
    if args.pmids:
        work = [p.strip() for p in args.pmids.split(",") if p.strip()]
    else:  # --from-gap-scan
        from utils.sql_queries import scan_faculty_publication_gaps

        gap_rows = scan_faculty_publication_gaps()
        work = sorted({r["pmid"] for r in gap_rows if not r["has_synopsis"]})
        print(
            f"[--from-gap-scan] scanned {len(gap_rows)} faculty first/last-"
            f"author PMID rows; {len(work)} distinct PMID(s) missing a synopsis",
            file=sys.stderr,
        )
    kwargs = dict(
        pmids=work, engine=engine, dry_run=args.dry_run, force=args.force
    )
    if args.no_alerts:
        kwargs["alert_fn"] = _noop_alert
    return run_enrichment_backfill(**kwargs)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Daily enrichment job (#37 step 2): synopsis + impact for new PMIDs."
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Bypass the cost guard. Use for annual rescore + cold-start backfill.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="SQL-level row cap on the delta query (default: 1000).",
    )
    p.add_argument(
        "--threshold-usd",
        type=Decimal,
        default=None,
        help="Override cost-guard threshold (default: $30.00).",
    )
    p.add_argument(
        "--per-paper-usd",
        type=Decimal,
        default=None,
        help="Override cost-guard per-paper assumption (default: $0.010).",
    )
    p.add_argument(
        "--pmids",
        metavar="PMID,PMID,...",
        default=None,
        help="Enrichment-backfill mode: synopsis + impact for this explicit "
             "comma-separated PMID set, regardless of publication date. "
             "Mutually exclusive with --from-gap-scan.",
    )
    p.add_argument(
        "--from-gap-scan",
        action="store_true",
        help="Enrichment-backfill mode: derive the work set from the "
             "onboarding detector's faculty gap scan — every first/last-"
             "author faculty PMID with no synopsis. The #112 backfill entry "
             "point.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Backfill mode only: resolve the work set, apply the "
             "idempotency cull, and print the count + cost estimate without "
             "generating anything.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Backfill mode only: bypass the synopsis+impact idempotency "
             "cull and reprocess every PMID (operator recovery).",
    )
    p.add_argument(
        "--no-alerts",
        action="store_true",
        help="Suppress Teams alerts (local iteration / dry-run). The "
             "webhook env var is otherwise read at module-init.",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show INFO-level orchestrator log messages.",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Quiet noisy clients regardless of -v.
    for n in ("botocore", "boto3", "urllib3", "httpx"):
        logging.getLogger(n).setLevel(logging.WARNING)

    backfill_mode = bool(args.pmids) or args.from_gap_scan

    # Flag-combination validation.
    if args.pmids and args.from_gap_scan:
        p.error(
            "--pmids and --from-gap-scan are mutually exclusive work-set "
            "sources; pass exactly one"
        )
    if not backfill_mode and (args.dry_run or args.force):
        p.error(
            "--dry-run / --force apply only to enrichment-backfill mode "
            "(--pmids or --from-gap-scan)"
        )
    if backfill_mode and (
        args.full
        or args.threshold_usd is not None
        or args.per_paper_usd is not None
    ):
        p.error(
            "--full / --threshold-usd / --per-paper-usd are daily-mode "
            "flags; enrichment-backfill mode surfaces its own cost estimate "
            "and enforces no threshold"
        )

    engine = get_engine()

    if backfill_mode:
        result = _run_backfill(engine, args)
    else:
        kwargs = {"engine": engine, "full": args.full, "limit": args.limit}
        if args.threshold_usd is not None:
            kwargs["threshold_usd"] = args.threshold_usd
        if args.per_paper_usd is not None:
            kwargs["per_paper_usd"] = args.per_paper_usd
        if args.no_alerts:
            kwargs["alert_fn"] = _noop_alert
        result = run_daily_enrichment(**kwargs)

    print(_result_to_json(result))

    # Exit codes: 0 = clean (complete or no_op), 1 = anything else.
    if result.status in (STATUS_COMPLETE, STATUS_NO_OP):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
