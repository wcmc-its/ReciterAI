#!/usr/bin/env python3
"""Operator entry point for the daily enrichment job (#37 step 2).

Wraps `pipeline_enrichment.daily_job.run_daily_enrichment` for command-line
invocation. The Lambda/ECS handler in PR 2 will share the same orchestrator
function, so this CLI is the canonical way to exercise the code path
locally and the way to run cold-start backfills (--full) until automation
takes over.

Examples:

    # Routine daily run (cost guard active):
    python -m scripts.run_daily_enrichment

    # Cold-start backfill / annual rescore — bypass cost guard:
    python -m scripts.run_daily_enrichment --full

    # Operator catch-up with a widened threshold:
    python -m scripts.run_daily_enrichment --threshold-usd 100

    # Dry-run estimate without LLM calls (cost guard reports estimate
    # but we set per_paper_usd so high it always trips):
    python -m scripts.run_daily_enrichment --per-paper-usd 99999
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
        help="Override cost-guard per-paper assumption (default: $0.060).",
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

    engine = get_engine()
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
