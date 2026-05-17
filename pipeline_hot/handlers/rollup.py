"""Hot-path / onboarding Rollup Lambda handler.

Wraps `rollup_by_cwid.py --emit-envelope` and returns the STAGE# envelope
— already converted to DynamoDB attribute-typed format — for the state
machine's `arn:aws:states:::dynamodb:putItem` SDK integration to consume
directly via `Item.$`.

Two event shapes are accepted (see `handler`):
  - the hot-path dirty-CWID event (`{"dirty_cwids": [...]}`), which rolls
    up the CSV breakdowns for the listed CWIDs, and
  - the new-researcher onboarding CWID-scoped event (`{"cwid": "..."}`),
    added for #80 Phase 2 / #90, which routes to the PMID-aware
    `rollup_by_cwid --cwid` DynamoDB path.

The envelope parse + DDB-typing helpers live in `pipeline_common.envelope`
— shared with the other hot handlers and the onboarding package.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from pipeline_common.envelope import (
    parse_envelope_from_stdout,
    to_ddb_typed_envelope,
)

# The onboarding `{cwid}` path runs `rollup_by_cwid --cwid`, which queries
# ReciterDB (get_pmids_for_cwid) for the CWID's accepted PMID set; importing
# secrets_loader populates the DB_* env vars the subprocess inherits. No-op
# outside Lambda, and harmless for the `{dirty_cwids}` CSV path (which never
# opens a DB connection).
import utils.secrets_loader  # noqa: F401, E402

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def handler(event: dict, context: Any = None) -> dict:
    """Invoke rollup_by_cwid --emit-envelope and return the envelope.

    Two event shapes are accepted.

    Hot-path dirty-CWID rollup (the weekly hot run):
        {"dirty_cwids": ["cwid1", "cwid2", ...]}
    Routes to `rollup_by_cwid --cwids …` — the CSV-breakdown incremental
    rollup. An empty list → full re-rollup (the caller should rarely do
    this in the hot path; the cold path owns full re-rollups).

    Onboarding CWID-scoped rollup (#80 Phase 2 / #90):
        {"cwid": "abc1234"}
    Routes to `rollup_by_cwid --cwid …` — the PMID-aware DynamoDB path
    that writes a `STAGE#rollup_by_cwid#cwid:{cwid}` row carrying
    `input_pmid_set`. The onboarding state machine's Rollup stage invokes
    this with the workflow's CWID; an empty `cwid` is a contract
    violation and raises.
    """
    cwid = event.get("cwid")
    if cwid is not None:
        # Onboarding CWID-scoped event (#80 Phase 2 / #90). `--cwid` is a
        # disjoint mode from `--cwids` in rollup_by_cwid, so this branch
        # builds a self-contained command.
        cwid = str(cwid).strip()
        if not cwid:
            raise ValueError(
                "rollup handler: onboarding event carries an empty 'cwid'; "
                "the onboarding state machine must invoke Rollup with the "
                "workflow's CWID"
            )
        cmd = [
            sys.executable, "-m", "rollup_by_cwid", "--emit-envelope",
            "--cwid", cwid,
        ]
        logger.info(f"rollup handler invoking (onboarding): --cwid {cwid}")
    else:
        # Hot-path dirty-CWID event.
        dirty_cwids = event.get("dirty_cwids") or []
        cmd = [sys.executable, "-m", "rollup_by_cwid", "--emit-envelope"]
        if dirty_cwids:
            cmd += ["--cwids", ",".join(str(c) for c in dirty_cwids)]
        logger.info(
            f"rollup handler invoking: {' '.join(cmd[:4])}... "
            f"({len(dirty_cwids)} cwids)"
        )

    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"rollup_by_cwid exited {proc.returncode}: "
            f"stderr={proc.stderr[-2000:]}"
        )
    return to_ddb_typed_envelope(parse_envelope_from_stdout(proc.stdout))
