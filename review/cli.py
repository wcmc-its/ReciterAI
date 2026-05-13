"""Ad-hoc review CLI — `python -m review approve|validate`.

Use cases:
  - python -m review approve --artifact hierarchy --version v2026-06-01
    Opens $EDITOR on a pre-populated YAML, validates on save, writes REVIEW#.
  - python -m review validate <path-to-yaml>
    Validates a draft YAML without writing.

Exit codes:
  0 — approval committed (approve) OR validation passed (validate)
  2 — argparse error / required flag missing
  3 — validation failed (any rule rejected the YAML)
  4 — reviewer_cwid unresolvable (no config, no env)
  5 — DDB write failed
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

import yaml

from review.config import ReviewerCwidUnresolvable, load_reviewer_cwid
from review.store import write_review, read_run_signals
from review.template import build_template
from review.validator import validate, RunSignals
from utils.iso_clock import now_iso


# ---------------------------------------------------------------------------
# Default I/O seams (overridable for testing)
# ---------------------------------------------------------------------------

def _default_invoke_editor(path: Path) -> int:
    """Open path in $EDITOR (defaults to vim). Returns editor exit code."""
    editor = os.environ.get("EDITOR", "vim")
    return subprocess.run([editor, str(path)]).returncode


def _default_get_table():
    from utils.dynamodb_helpers import get_table
    return get_table()


def _default_get_s3_client():
    from utils.s3_client import S3HierarchyClient
    return S3HierarchyClient()




# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _run_approve(
    args: argparse.Namespace,
    *,
    invoke_editor: Callable[[Path], int] = _default_invoke_editor,
    get_table: Callable = _default_get_table,
    get_s3_client: Callable = _default_get_s3_client,
) -> int:
    """Handle the `approve` subcommand.

    1. Resolve reviewer_cwid (config → env → actionable error).
    2. Build pre-populated YAML template.
    3. Write template to tempfile, open in $EDITOR.
    4. Re-load and parse YAML after editor exits.
    5. Fetch RunSignals from DDB + S3.
    6. Validate YAML dict against all rules.
    7. On success: PutItem REVIEW# row to DDB.

    Args:
        args: Parsed argparse.Namespace with .artifact and .version.
        invoke_editor: Callable(path) -> int; default opens $EDITOR.
        get_table: Factory returning a boto3 DynamoDB Table resource.
        get_s3_client: Factory returning an S3HierarchyClient.

    Returns:
        Integer exit code (0 success, 3 validation failure, 4 cwid error, 5 DDB error).
    """
    # 1. Resolve reviewer_cwid
    try:
        cwid = load_reviewer_cwid()
    except ReviewerCwidUnresolvable as exc:
        print(str(exc), file=sys.stderr)
        return 4

    # 2. Compute pre-populated template
    # Note: summary_stats currently uses zeros as placeholder. The integration
    # with compute_diff() from the change-signaling plan (11-change-signaling)
    # will populate real stats when both plans land in the same wave. The wiring
    # point is _run_approve() here — replace the zeros dict with the result of
    # compute_diff(artifact_type=args.artifact, version=args.version).
    summary_stats = {"topics_added": 0, "subtopics_renamed": 0, "pmids_reassigned": 0}
    proposed_artifact_uri = (
        f"s3://wcmc-reciterai-hierarchy/{args.version}/hierarchy.json"
    )
    template_str = build_template(
        artifact_type=args.artifact,
        version=args.version,
        proposed_artifact_uri=proposed_artifact_uri,
        summary_stats=summary_stats,
        reviewer_cwid=cwid,
    )

    # 3. Write template to tempfile, open in $EDITOR
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as fp:
        fp.write(template_str)
        tmppath = Path(fp.name)

    rc = invoke_editor(tmppath)
    if rc != 0:
        print(
            f"$EDITOR exited with code {rc}; leaving draft at {tmppath}",
            file=sys.stderr,
        )
        return 3

    # 4. Re-load YAML
    try:
        yaml_dict = yaml.safe_load(tmppath.read_text()) or {}
    except yaml.YAMLError as exc:
        print(f"YAML parse error: {exc}; draft at {tmppath}", file=sys.stderr)
        return 3

    # 5. Fetch RunSignals (table + s3 head)
    table = get_table()
    s3 = get_s3_client()
    run_id = os.environ.get("RECITERAI_COLD_RUN_ID", "")
    signals = read_run_signals(
        table,
        s3,
        run_id=run_id,
        proposed_artifact_uri=yaml_dict.get("proposed_artifact_uri", ""),
    )

    # 6. Validate
    result = validate(yaml_dict, signals)
    if not result.ok:
        print("validation FAILED:", file=sys.stderr)
        for e in result.errors:
            print(f"  - {e}", file=sys.stderr)
        print(f"draft preserved at {tmppath}", file=sys.stderr)
        return 3

    # 7. PutItem
    review_dict = {
        "proposed_artifact_uri": yaml_dict["proposed_artifact_uri"],
        "summary_stats": yaml_dict.get("summary_stats", {}),
        "status": "approved" if yaml_dict["decision"] == "approve" else "rejected",
        "reviewer_cwid": yaml_dict["reviewer_cwid"],
        "reviewed_at": now_iso(),
        "rationale": yaml_dict["rationale"],
        "decision": yaml_dict["decision"],
    }
    try:
        written = write_review(
            table,
            artifact_type=args.artifact,
            version=args.version,
            review_dict=review_dict,
        )
    except Exception as exc:
        print(f"DDB write failed: {exc}", file=sys.stderr)
        return 5

    print(f"REVIEW written: {written['PK']}")
    # Cleanup temp file on success
    tmppath.unlink(missing_ok=True)
    return 0


def _run_validate(
    args: argparse.Namespace,
    *,
    get_table: Callable = _default_get_table,
    get_s3_client: Callable = _default_get_s3_client,
) -> int:
    """Handle the `validate` subcommand (dry run — no DDB write).

    Args:
        args: Parsed argparse.Namespace with .path (path to YAML file).
        get_table: Factory returning a boto3 DynamoDB Table resource.
        get_s3_client: Factory returning an S3HierarchyClient.

    Returns:
        0 on validation pass; 3 on validation failure or file error.
    """
    path = Path(args.path)
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 3

    try:
        yaml_dict = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        print(f"YAML parse error: {exc}", file=sys.stderr)
        return 3

    table = get_table()
    s3 = get_s3_client()
    run_id = os.environ.get("RECITERAI_COLD_RUN_ID", "")
    signals = read_run_signals(
        table,
        s3,
        run_id=run_id,
        proposed_artifact_uri=yaml_dict.get("proposed_artifact_uri", ""),
    )
    result = validate(yaml_dict, signals)
    if not result.ok:
        print("validation FAILED:", file=sys.stderr)
        for e in result.errors:
            print(f"  - {e}", file=sys.stderr)
        return 3

    print("validation passed")
    return 0


# ---------------------------------------------------------------------------
# Argparse entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    """Parse arguments and dispatch to the appropriate subcommand.

    Args:
        argv: Argument list (default: sys.argv[1:]). Useful for testing.

    Returns:
        Integer exit code.
    """
    parser = argparse.ArgumentParser(
        prog="review",
        description="ReciterAI artifact review CLI — approve or validate hierarchy artifacts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # approve subcommand
    p_approve = sub.add_parser(
        "approve",
        help="Open $EDITOR on a pre-populated YAML, validate, and write REVIEW# to DDB.",
    )
    p_approve.add_argument(
        "--artifact",
        required=True,
        choices=["hierarchy"],
        help="Artifact type to approve (currently only 'hierarchy').",
    )
    p_approve.add_argument(
        "--version",
        required=True,
        help="Version string (e.g. v2026-06-01).",
    )

    # validate subcommand
    p_validate = sub.add_parser(
        "validate",
        help="Validate a draft review YAML without writing to DDB.",
    )
    p_validate.add_argument(
        "path",
        help="Path to the YAML file to validate.",
    )

    args = parser.parse_args(argv)
    if args.command == "approve":
        return _run_approve(args)
    return _run_validate(args)
