"""Phase 12 feedback CLI — `python -m pipeline_feedback {sweep|render}`.

Use cases:
  - Operator diagnosis: python -m pipeline_feedback sweep --since 7
  - Cold-path stage: invoked by pipeline_cold.run with --triggered-by cold_run
  - Operator render: python -m pipeline_feedback render <run_id>

'Diagnosis vs documentation' framing (CONTEXT specifics):
  - operator-CLI mode = diagnosis (drift alert → investigate before deciding on a cold run)
  - cold-path-stage mode = documentation (cold run is happening; record findings in this
    run_id's context)

Exit codes:
  0 — sweep completed and wrote findings (zero findings is also success)
  2 — argparse error / unknown subcommand
  3 — invalid argument values
  4 — missing DDB env / table not configured
  5 — DDB write failed
  6 — Bedrock unavailable for the Sonnet uncovered-PMID pass
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pipeline_feedback.sweep import run_sweep
from pipeline_feedback.markdown_render import render_sweep_markdown

# IN-03: `boto3.dynamodb.conditions.Attr` is lazy-imported inside
# `_fetch_rows_by_run_id` (the only caller) to keep the module import path
# free of boto3 — consistent with `pipeline_feedback.sweep` and
# `pipeline_drift.evaluator`, which lazy-import boto3 inside their
# DDB-touching helpers. This simplifies test fixtures that monkeypatch boto3.


# ---------------------------------------------------------------------------
# Default I/O seam (overridable for testing)
# ---------------------------------------------------------------------------

def _default_get_table():
    from utils.dynamodb_helpers import get_table
    return get_table()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _run_sweep(args, *, get_table=_default_get_table) -> int:
    triggered_by = args.triggered_by or "operator"
    since = datetime.now(timezone.utc) - timedelta(days=args.since)
    run_id = args.run_id or str(uuid.uuid4())
    try:
        table = get_table()
    except Exception as exc:
        print(f"[error] missing DDB env: {exc}", file=sys.stderr)
        return 4
    try:
        result = run_sweep(
            table=table,
            since=since,
            triggered_by=triggered_by,
            run_id=run_id,
            max_pmids=args.max_pmids,
        )
    except Exception as exc:
        print(f"[error] sweep failed: {exc}", file=sys.stderr)
        return 5
    print(
        f"sweep complete: run_id={result.source_sweep_run_id} "
        f"candidates={len(result.candidate_topics)} "
        f"reclusters={len(result.recluster_recommendations)} "
        f"diagnostics={len(result.spotlight_diagnostics)}"
    )
    return 0


def _run_render(args, *, get_table=_default_get_table) -> int:
    try:
        table = get_table()
    except Exception as exc:
        print(f"[error] missing DDB env: {exc}", file=sys.stderr)
        return 4
    rows = _fetch_rows_by_run_id(table, args.run_id)
    markdown_bytes = render_sweep_markdown(args.run_id, rows)
    if args.output:
        Path(args.output).write_bytes(markdown_bytes)
    else:
        sys.stdout.buffer.write(markdown_bytes)
    return 0


# ---------------------------------------------------------------------------
# Pagination helper — replaces W-5 stub
# ---------------------------------------------------------------------------

def _fetch_rows_by_run_id(table, run_id: str) -> list[dict]:
    """Scan the table for finding rows carrying source_sweep_run_id == run_id.

    Pattern analog: review/validator.py:_query_pending_rows (per PATTERNS.md).
    Pagination: boto3 table.scan returns LastEvaluatedKey when results
    truncate; we loop until absent. Bounded by the number of findings emitted
    in one sweep (3 partitions × ≤ N findings each), so scan is acceptable.
    A GSI keyed by source_sweep_run_id is a deferred optimization.
    """
    from boto3.dynamodb.conditions import Attr  # IN-03: lazy import

    rows: list[dict] = []
    scan_kwargs: dict = {"FilterExpression": Attr("source_sweep_run_id").eq(run_id)}
    while True:
        resp = table.scan(**scan_kwargs)
        rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return rows


# ---------------------------------------------------------------------------
# Argument parser + entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None, *, get_table=None) -> int:
    """CLI entry point.

    Args:
        argv: argv list for argparse (None → sys.argv[1:]).
        get_table: WR-04 seam — overrides the default DDB table factory.
            Threaded through to both subcommand handlers so tests can
            inject a fake table without wrapping the handler functions
            themselves (which would skip the actual main → handler call
            path).
    """
    parser = argparse.ArgumentParser(
        prog="pipeline_feedback",
        description="Phase 12 feedback CLI — operator diagnosis and cold-path sweep.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_sweep = sub.add_parser("sweep", help="Run the feedback sweep.")
    p_sweep.add_argument(
        "--since", type=int, default=7,
        help="Days back to look for events (default 7).",
    )
    p_sweep.add_argument("--run-id", type=str, default=None,
                         help="Supply a fixed run_id (default: fresh UUID).")
    p_sweep.add_argument(
        "--max-pmids", type=int, default=None,
        help="Override feedback_sweep_max_pmids threshold.",
    )
    p_sweep.add_argument(
        "--triggered-by", choices=["operator", "cold_run"], default=None,
        help="Who triggered the sweep (default: operator).",
    )

    p_render = sub.add_parser("render", help="Render a sweep's findings as deterministic markdown.")
    p_render.add_argument("run_id", help="The source_sweep_run_id to render.")
    p_render.add_argument("--output", type=str, default=None,
                          help="Write markdown to this path instead of stdout.")

    args = parser.parse_args(argv)
    handler_kwargs = {"get_table": get_table} if get_table is not None else {}
    if args.command == "sweep":
        return _run_sweep(args, **handler_kwargs)
    return _run_render(args, **handler_kwargs)
