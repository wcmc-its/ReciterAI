"""Cold-path operator CLI.

Walks the seven cold stages in order (per CONTEXT stage-assignment
table): score → assign → discover → relabel → rollup →
backfill_spotlight → publish_hierarchy. Each stage shells out to its
existing script in direct-write mode; STAGE# rows for individual
stages are written by those scripts themselves (Wave 2 / Phase 9
substrate). The orchestrator's contribution on top of that is:

1. A single `STAGE#cold_run#GLOBAL` row wrapping the entire cold run,
   stamped with `initiated_by` so drift-triggered cold runs can be
   distinguished from operator-initiated ones.
2. `--from-stage <name>` for resume: skip stages strictly before the
   named stage. Useful when a long cold run dies mid-way and the
   operator wants to pick up without redoing completed stages.
3. `--dry-run` prints the ordered stage list and the exact subprocess
   command that would be invoked for each, without touching any data.

Usage:
    python -m pipeline_cold.run --full --initiated-by operator
    python -m pipeline_cold.run --from-stage rollup
    python -m pipeline_cold.run --dry-run

Per D-02 the cold path is operator-driven from a workstation; there
is no Step Functions wrapper. Multi-hour wall time and Phase-11 review
gates favor a plain Python orchestrator.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

# Ensure repo root is importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.dynamodb_helpers import get_table, TABLE_NAME
from utils.stage_records import (
    build_complete_record,
    build_failed_record,
    compute_input_hash,
    write_complete,
    write_failed,
)

logger = logging.getLogger(__name__)

COLD_RUN_STAGE = "cold_run"
COLD_RUN_SCOPE = "GLOBAL"
COLD_RUN_COST_USD = Decimal("0")  # cold_run wraps stages; per-stage costs already recorded

VALID_INITIATED_BY = ("operator", "drift_alert", "scheduled")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------


@dataclass
class ColdStage:
    """A cold-path stage. `command` is what gets handed to subprocess.run.

    Stages that need per-topic iteration (assign, discover, relabel) point
    at the existing `backfill_*.py` wrappers, which already encode the
    iteration logic and per-topic STAGE# writes. This keeps the cold
    orchestrator a thin walker.
    """

    name: str
    command: list[str]
    description: str = ""


def default_cold_stages() -> list[ColdStage]:
    """Canonical cold-stage list per CONTEXT stage-assignment table."""
    return [
        ColdStage(
            name="score",
            command=[sys.executable, "-m", "score_publications"],
            description="Full re-score of all in-corpus publications",
        ),
        ColdStage(
            name="assign",
            command=[sys.executable, "backfill_all.py", "--skip-pm-copy"],
            description=(
                "Per-topic discover + assign + relabel via backfill_all.py "
                "(iteration owned by the wrapper)"
            ),
        ),
        ColdStage(
            name="discover",
            command=[sys.executable, "backfill_all.py", "--skip-pm-copy", "--only-discover"],
            description="Subtopic clustering pass (subsumed by assign on first run)",
        ),
        ColdStage(
            name="relabel",
            command=[sys.executable, "backfill_all.py", "--skip-pm-copy", "--only-relabel"],
            description="Relabel pass over existing hierarchy drafts",
        ),
        ColdStage(
            name="rollup",
            command=[sys.executable, "-m", "rollup_by_cwid"],
            description="Full per-CWID rollup",
        ),
        ColdStage(
            name="backfill_spotlight",
            command=[sys.executable, "backfill_spotlight.py", "--publish"],
            description="Generate spotlight ledes for ranked subtopic pool",
        ),
        ColdStage(
            name="publish_hierarchy",
            command=[sys.executable, "-m", "pipeline_hierarchy.publish"],
            description="Mint hierarchy_version and upload to S3 (Phase 9)",
        ),
    ]


# ---------------------------------------------------------------------------
# Stage walker
# ---------------------------------------------------------------------------


@dataclass
class StageOutcome:
    name: str
    status: str  # "complete" | "skipped" | "failed"
    duration_ms: int
    returncode: int | None = None
    stderr_tail: str = ""


def select_stages(
    stages: list[ColdStage], *, from_stage: str | None
) -> list[ColdStage]:
    """Return stages from `from_stage` onward; full list when None.

    Raises ValueError if `from_stage` is not in the list.
    """
    if from_stage is None:
        return list(stages)
    for i, s in enumerate(stages):
        if s.name == from_stage:
            return list(stages[i:])
    raise ValueError(
        f"--from-stage '{from_stage}' is not a known cold stage. "
        f"Valid: {[s.name for s in stages]}"
    )


def run_stage(
    stage: ColdStage,
    *,
    repo_root: Path,
    runner=None,
) -> StageOutcome:
    """Invoke a single stage. `runner` is injected for testability; falls
    back to `subprocess.run` looked up at call time so a test
    `monkeypatch.setattr(subprocess, "run", ...)` intercepts the call."""
    t0 = time.monotonic()
    logger.info(f"[cold] starting stage={stage.name}: {' '.join(stage.command)}")
    invoker = runner if runner is not None else subprocess.run
    proc = invoker(
        stage.command,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    if proc.returncode == 0:
        return StageOutcome(
            name=stage.name,
            status="complete",
            duration_ms=duration_ms,
            returncode=0,
        )
    return StageOutcome(
        name=stage.name,
        status="failed",
        duration_ms=duration_ms,
        returncode=proc.returncode,
        stderr_tail=(proc.stderr or "")[-2000:],
    )


def compute_cold_run_input_hash(
    *, started_at: str, stages: list[ColdStage], initiated_by: str
) -> str:
    """Cold runs are not memoized — the operator's intent is to redo work.
    But we still produce an input_hash so the row is queryable by the
    substrate's conventions.
    """
    return compute_input_hash(
        COLD_RUN_STAGE,
        {
            "started_at": started_at,
            "stage_names": [s.name for s in stages],
            "initiated_by": initiated_by,
        },
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _print_plan(stages: list[ColdStage], initiated_by: str) -> None:
    print(f"\n--- pipeline_cold.run plan (initiated_by={initiated_by}) ---")
    for i, s in enumerate(stages, 1):
        print(f"  {i}. {s.name}: {s.description}")
        print(f"       $ {' '.join(s.command)}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline_cold.run")
    parser.add_argument(
        "--full", action="store_true",
        help="Run all cold stages in order (default behavior).",
    )
    parser.add_argument(
        "--from-stage", default=None, metavar="NAME",
        help=(
            "Resume from the named stage; skip earlier stages. Useful when "
            "a long cold run died mid-way. Valid names match the cold-stage "
            "table in CONTEXT (e.g., rollup, publish_hierarchy)."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the planned stage walk and exit; do not invoke anything.",
    )
    parser.add_argument(
        "--initiated-by", choices=list(VALID_INITIATED_BY), default="operator",
        help=(
            "Run provenance — recorded on the STAGE#cold_run#GLOBAL row so "
            "drift-triggered cold runs can be distinguished from operator-"
            "initiated ones in audit queries."
        ),
    )
    parser.add_argument(
        "--skip-stage-write", action="store_true",
        help=(
            "Skip the STAGE#cold_run DynamoDB write (per-stage scripts still "
            "manage their own rows). Useful for local sanity-runs without AWS."
        ),
    )
    args = parser.parse_args(argv)

    stages_all = default_cold_stages()
    try:
        stages = select_stages(stages_all, from_stage=args.from_stage)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    _print_plan(stages, args.initiated_by)

    if args.dry_run:
        print("[--dry-run] no stages invoked.")
        return 0

    repo_root = Path(__file__).resolve().parent.parent
    started_at = _now_iso()
    t_run = time.monotonic()
    input_hash = compute_cold_run_input_hash(
        started_at=started_at, stages=stages, initiated_by=args.initiated_by
    )

    table = None if args.skip_stage_write else get_table(TABLE_NAME)

    outcomes: list[StageOutcome] = []
    for stage in stages:
        outcome = run_stage(stage, repo_root=repo_root)
        outcomes.append(outcome)
        if outcome.status == "failed":
            duration_ms = int((time.monotonic() - t_run) * 1000)
            logger.error(
                f"[cold] stage {stage.name} failed (rc={outcome.returncode}); "
                f"aborting cold run after {len(outcomes)} stage(s)."
            )
            if table is not None:
                write_failed(
                    table,
                    stage=COLD_RUN_STAGE,
                    scope=COLD_RUN_SCOPE,
                    input_hash=input_hash,
                    error_code=f"STAGE_FAILURE_{stage.name}",
                    error_message=(
                        f"stage '{stage.name}' exited rc={outcome.returncode}"
                    ),
                    started_at=started_at,
                    completed_at=_now_iso(),
                    duration_ms=duration_ms,
                    cost_observed_usd=COLD_RUN_COST_USD,
                    failure_details={
                        "failed_stage": stage.name,
                        "completed_stages": [o.name for o in outcomes[:-1]],
                        "stderr_tail": outcome.stderr_tail,
                        "initiated_by": args.initiated_by,
                    },
                )
            return outcome.returncode or 1

    # All stages green.
    duration_ms = int((time.monotonic() - t_run) * 1000)
    print(f"\n--- cold run complete in {duration_ms / 1000:.1f}s ---")
    for o in outcomes:
        print(f"  {o.name:>22}: {o.status} ({o.duration_ms} ms)")

    if table is not None:
        item = build_complete_record(
            stage=COLD_RUN_STAGE,
            scope=COLD_RUN_SCOPE,
            input_hash=input_hash,
            started_at=started_at,
            completed_at=_now_iso(),
            duration_ms=duration_ms,
            cost_observed_usd=COLD_RUN_COST_USD,
            records_written=len(outcomes),
        )
        # initiated_by is cold-path-specific metadata, not in the builder
        # contract — attach as a top-level attribute.
        item["initiated_by"] = args.initiated_by
        item["stage_names"] = [o.name for o in outcomes]
        table.put_item(Item=item)

    return 0


if __name__ == "__main__":
    sys.exit(main())
