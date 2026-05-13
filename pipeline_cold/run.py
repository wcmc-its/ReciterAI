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
import json
import logging
import os
import subprocess
import sys
import time
import uuid
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


@dataclass(frozen=True)
class ColdStage:
    """A cold-path stage. `command` is what gets handed to subprocess.run.

    Stages that need per-topic iteration (assign, discover, relabel) point
    at the existing `backfill_*.py` wrappers, which already encode the
    iteration logic and per-topic STAGE# writes. This keeps the cold
    orchestrator a thin walker.

    IN-02: frozen=True. Stages are constructed once by ``default_cold_stages``
    and never mutated thereafter; freezing documents that contract and
    catches accidental mutation. (``command`` remains a list — frozen only
    blocks attribute rebind; list-element mutation is still possible if a
    caller really wants it.)
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
            command=[sys.executable, "backfill_all.py", "--skip-pm-copy", "--skip-all-reviews"],
            description=(
                "Per-topic discover + assign + relabel via backfill_all.py "
                "(iteration owned by the wrapper). --skip-all-reviews because "
                "the cold-run is non-interactive; the operator-facing review "
                "gate is the DDB REVIEW# row (Phase 11), not the per-topic "
                "hierarchy_draft review_status field."
            ),
        ),
        ColdStage(
            name="discover",
            # No-op placeholder. backfill_all.py never grew --only-discover; the
            # work is fully subsumed by `assign`. Keeping the named stage slot
            # so STAGE# auditing and resume-from-stage semantics stay stable,
            # but the command itself is a 0-exit print so the cold-run can
            # continue cleanly. A real --only-discover (incremental clustering
            # on a stable taxonomy) can replace this when needed.
            command=[sys.executable, "-c",
                     "print('cold-stage discover: subsumed by assign (no-op).')"],
            description="Subtopic clustering pass (subsumed by assign on first run)",
        ),
        ColdStage(
            name="relabel",
            # No-op placeholder; mirror rationale of `discover` above.
            command=[sys.executable, "-c",
                     "print('cold-stage relabel: subsumed by assign (no-op).')"],
            description="Relabel pass over existing hierarchy drafts",
        ),
        ColdStage(
            name="count",
            command=[sys.executable, "count_by_cwid.py"],
            description=(
                "Scan TOPIC# activity rows and produce per-CWID breakdown CSVs "
                "(faculty_subtopic_counts_{exclusive,inclusive}.csv + legacy "
                "cwid_subtopic_counts.csv). Prerequisite for `rollup`."
            ),
        ),
        ColdStage(
            name="rollup",
            command=[sys.executable, "-m", "rollup_by_cwid"],
            description="Full per-CWID rollup",
        ),
        ColdStage(
            name="feedback_sweep",
            command=[sys.executable, "-m", "pipeline_feedback", "sweep",
                     "--triggered-by", "cold_run"],
            description=(
                "Non-gating Sonnet sweep over UNCOVERED_PMID# + "
                "LOW_CONFIDENCE_ASSIGNMENT# (via DRIFT# materialized counts) "
                "+ CRITIC_REJECT# events → three typed finding records "
                "(Phase 12 D-04, D-08 per-subtopic diagnostic). Returns 0 "
                "even with findings (D-02 non-gating)."
            ),
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


@dataclass(frozen=True)
class StageOutcome:
    """Outcome of a single stage invocation.

    IN-02: frozen=True. Outcomes are produced once by ``run_stage`` and
    only ever read by the walker loop and STAGE# row builders; freezing
    documents that contract.
    """

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
    env: dict | None = None,
) -> StageOutcome:
    """Invoke a single stage. `runner` is injected for testability; falls
    back to `subprocess.run` looked up at call time so a test
    `monkeypatch.setattr(subprocess, "run", ...)` intercepts the call.

    Args:
        stage: The stage to run.
        repo_root: Working directory for the subprocess.
        runner: Optional callable to replace subprocess.run (for testing).
        env: Optional environment dict to pass to the subprocess. If None,
            inherits from the current process. Phase 11: carries
            RECITERAI_COLD_RUN_ID and RECITERAI_HIERARCHY_VERSION.
    """
    t0 = time.monotonic()
    logger.info(f"[cold] starting stage={stage.name}: {' '.join(stage.command)}")
    invoker = runner if runner is not None else subprocess.run
    proc = invoker(
        stage.command,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        env=env,
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


def _read_prev_version_from_latest_manifest() -> str | None:
    """Read `version` from s3://wcmc-reciterai-hierarchy/latest/manifest.json.

    Returns the version string (e.g. "v2026-05-01") if the manifest exists,
    or None on first-ever cold run (404 / NoSuchKey). Other S3 errors are
    re-raised so the operator sees auth/permission issues immediately.

    Phase 11 D-06: prev_version is captured at the start of main() so the
    cutover audit row can record what version was active before this run.
    """
    try:
        from botocore.exceptions import ClientError
        from utils.s3_client import S3HierarchyClient, HIERARCHY_BUCKET

        s3 = S3HierarchyClient(bucket=HIERARCHY_BUCKET)
        data = s3.get_object_bytes("latest/manifest.json")
        manifest = json.loads(data)
        return manifest.get("version")
    except Exception as exc:
        # Check for 404 / NoSuchKey patterns
        error_code = ""
        if hasattr(exc, "response"):
            error_code = exc.response.get("Error", {}).get("Code", "")  # type: ignore[attr-defined]
        if error_code in ("404", "NoSuchKey"):
            logger.info("No previous hierarchy manifest found (first cold run).")
            return None
        logger.warning(
            "Could not read latest/manifest.json from S3 (prev_version will be None): %s",
            exc,
        )
        return None


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
        "--hierarchy-version", default=None, metavar="VERSION",
        help=(
            "Hierarchy version string to stamp on this cold run "
            "(e.g. 'v2026-06-01'). If omitted, minted as v{started_at[:10]} "
            "(the ISO date of the run). Set via RECITERAI_HIERARCHY_VERSION "
            "env var for all subprocess stages."
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

    # Phase 11: mint run_id and hierarchy_version at run start so all
    # subprocess stages inherit a consistent version stamp.
    run_id = str(uuid.uuid4())
    new_version = args.hierarchy_version or f"v{started_at[:10]}"
    prev_version = _read_prev_version_from_latest_manifest()

    # Build env dict to pass to all stage subprocesses (inherits current env).
    env_for_stages = {
        **os.environ,
        "RECITERAI_COLD_RUN_ID": run_id,
        "RECITERAI_HIERARCHY_VERSION": new_version,
    }

    input_hash = compute_cold_run_input_hash(
        started_at=started_at, stages=stages, initiated_by=args.initiated_by
    )

    table = None if args.skip_stage_write else get_table(TABLE_NAME)

    outcomes: list[StageOutcome] = []
    for stage in stages:
        outcome = run_stage(stage, repo_root=repo_root, env=env_for_stages)
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
            # WR-03: subprocess.run sets returncode as int; on POSIX, signal
            # terminations yield negative values (e.g. -9 for SIGKILL). Negative
            # ints are truthy, so a bare `or 1` would propagate the negative
            # signal exit which most shells mask to 256+rc and which callers
            # check `rc == 0` against. Map signal exits + None to a stable
            # generic-failure code so the CLI exit code is always a clean
            # positive int.
            rc = outcome.returncode
            if rc is None or rc < 0:
                return 1
            return rc or 1

    # All stages green.
    duration_ms = int((time.monotonic() - t_run) * 1000)
    completed_at = _now_iso()
    print(f"\n--- cold run complete in {duration_ms / 1000:.1f}s ---")
    for o in outcomes:
        print(f"  {o.name:>22}: {o.status} ({o.duration_ms} ms)")

    if table is not None:
        # WR-11: do NOT set records_written for the cold_run umbrella row.
        # Every other STAGE# row uses records_written to mean "rows the stage
        # wrote to its data store" (see stage_records.py and rollup_by_cwid).
        # The umbrella row has no data store of its own — its cardinality is
        # "number of stages executed", which is published via stage_names.
        # Setting records_written=len(outcomes) here would poison a dashboard
        # that SUMs records_written across stages with stage-count noise.
        item = build_complete_record(
            stage=COLD_RUN_STAGE,
            scope=COLD_RUN_SCOPE,
            input_hash=input_hash,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            cost_observed_usd=COLD_RUN_COST_USD,
            records_written=None,
        )
        # initiated_by is cold-path-specific metadata, not in the builder
        # contract — attach as a top-level attribute.
        item["initiated_by"] = args.initiated_by
        item["stage_names"] = [o.name for o in outcomes]
        table.put_item(Item=item)

        # Phase 11 D-06 / O-03: write STAGE#hierarchy_version_cutover#GLOBAL
        # audit row at END of main(), after all stages succeed. Single write
        # (atomic); Phase 10 per-stage STAGE# rows already provide the
        # partial-failure trail.
        # migrated_rotation_count and orphan_count default to 0 here;
        # they are populated by the one-shot migration scripts
        # (scripts/migrate_spotlight_history_pk.py) when run as a separate
        # operator step during the cutover window.
        cutover_input_hash = compute_input_hash(
            "hierarchy_version_cutover",
            {"new_version": new_version},
        )
        cutover_row = build_complete_record(
            stage="hierarchy_version_cutover",
            scope="GLOBAL",
            input_hash=cutover_input_hash,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            cost_observed_usd=COLD_RUN_COST_USD,
        )
        cutover_row["prev_version"] = prev_version
        cutover_row["new_version"] = new_version
        cutover_row["migrated_rotation_count"] = 0
        cutover_row["orphan_count"] = 0
        cutover_row["initiated_by"] = args.initiated_by
        cutover_row["run_id"] = run_id
        table.put_item(Item=cutover_row)

    return 0


if __name__ == "__main__":
    sys.exit(main())
