"""Ad-hoc gate runner — `python -m gates`.

Use cases:

- Verify a published artifact's gates pass without re-running the
  bundler or touching DynamoDB: ``python -m gates --stage publish
  --hierarchy v2026-05-12``
- Validate the round-trip on a versioned S3 prefix:
  ``python -m gates --stage publish_post --version v2026-05-12``
- List registered gates: ``python -m gates --list [--stage X]``

Exit codes match pipeline_hierarchy.publish:
  0 all gates passed (or all failures were warn-severity)
  3 at least one block-severity gate failed and --force not set
  4 --force was passed without --force-reason

The CLI does NOT write any STAGE# records — it's a pure inspection
tool. Integrating stages (publish.py) handle their own substrate
writes. Use this when you want to ask "would this artifact pass?"
without committing the run to the DynamoDB ledger.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Import gate modules so @register_gate decorators run.
import gates.parent_prefix       # noqa: F401
import gates.pii                 # noqa: F401
import gates.schema_roundtrip    # noqa: F401
import gates.schema_validation   # noqa: F401
import gates.shrink_guard         # noqa: F401
from gates.registry import any_blocked, list_gates, run_gates
from utils.s3_client import S3HierarchyClient

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_hierarchy(version: str, from_s3: bool) -> dict:
    if from_s3:
        s3 = S3HierarchyClient()
        body = s3.get_object_bytes(f"{version}/hierarchy.json")
        return json.loads(body.decode("utf-8"))
    local_path = REPO_ROOT / "out" / "hierarchy" / version / "hierarchy.json"
    return json.loads(local_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gates")
    parser.add_argument(
        "--stage",
        help=(
            "Stage name to run gates for (e.g. `publish`, `publish_post`). "
            "Required unless --list is set."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List registered gates instead of running them. Filter by --stage.",
    )
    parser.add_argument(
        "--hierarchy",
        dest="version",
        help=(
            "Artifact version to load (e.g. v2026-05-12). For `publish` stage, "
            "loaded from out/hierarchy/<version>/hierarchy.json locally unless "
            "--from-s3 is set."
        ),
    )
    parser.add_argument(
        "--version",
        dest="version",
        help="Alias for --hierarchy. For `publish_post` this names the S3 prefix.",
    )
    parser.add_argument(
        "--from-s3",
        action="store_true",
        help=(
            "For `publish` stage: fetch hierarchy.json from S3 instead of "
            "out/. Implicit for publish_post (always goes through S3)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Treat block-severity failures as warn (exit 0). Requires --force-reason.",
    )
    parser.add_argument(
        "--force-reason",
        default=None,
        help="Required when --force is set; a one-line audit note.",
    )
    args = parser.parse_args(argv)

    if args.list:
        for g in list_gates(stage=args.stage):
            print(json.dumps(g))
        return 0

    if not args.stage:
        print("error: --stage is required (or use --list)", file=sys.stderr)
        return 2

    if args.force and not args.force_reason:
        print(
            "error: --force requires --force-reason '<audit note>'",
            file=sys.stderr,
        )
        return 4

    # Build kwargs for the gate runner based on stage.
    kwargs: dict = {}
    if args.stage == "publish":
        if not args.version:
            print("error: --hierarchy <version> required for publish stage", file=sys.stderr)
            return 2
        kwargs["hierarchy"] = _load_hierarchy(args.version, from_s3=args.from_s3)
    elif args.stage == "publish_post":
        if not args.version:
            print("error: --version required for publish_post stage", file=sys.stderr)
            return 2
        kwargs["version"] = args.version
        kwargs["s3_client"] = S3HierarchyClient()
    else:
        # Future stages: pass through what's available; gates ignore unknown kwargs.
        if args.version:
            kwargs["version"] = args.version

    results = run_gates(stage=args.stage, **kwargs)

    report = {
        "stage": args.stage,
        "version": args.version,
        "gate_count": len(results),
        "gates": [
            {
                "name": r.name,
                "passed": r.passed,
                "severity": r.severity,
                "summary": r.summary,
                "details": r.details,
            }
            for r in results
        ],
    }
    print(json.dumps(report, indent=2, default=str))

    if any_blocked(results) and not args.force:
        return 3
    if any_blocked(results) and args.force:
        # Note the override in stderr; CLI doesn't persist anything.
        print(
            json.dumps(
                {
                    "event": "force_override",
                    "force_reason": args.force_reason,
                    "overridden_gates": [
                        r.name for r in results if r.blocked
                    ],
                },
                indent=2,
            ),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
