"""Hot-path / onboarding Rollup Lambda handler.

Wraps `rollup_by_cwid.py --cwid --emit-envelope` and returns the STAGE#
envelope — already converted to DynamoDB attribute-typed format — for the
state machine's `arn:aws:states:::dynamodb:putItem` SDK integration to
consume directly via `Item.$`.

The handler accepts one event shape, the CWID-scoped rollup event
(`{"cwid": "..."}`). Both callers send it: the hot path's `RollupFanOut`
Map (one iteration per dirty CWID, #119) and the new-researcher
onboarding state machine's Rollup stage (#80 Phase 2 / #90). It routes to
the PMID-aware `rollup_by_cwid --cwid` DynamoDB path.

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

# The rollup handler's `--cwid` path runs `rollup_by_cwid --cwid`, which
# queries ReciterDB (get_pmids_for_cwid) for the CWID's accepted PMID set;
# importing secrets_loader populates the DB_* env vars the subprocess
# inherits. No-op outside Lambda.
import utils.secrets_loader  # noqa: F401, E402

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def handler(event: dict, context: Any = None) -> dict:
    """Invoke `rollup_by_cwid --cwid --emit-envelope` and return the envelope.

    CWID-scoped rollup (#80 Phase 2 / #90):
        {"cwid": "abc1234"}
    Both callers send this shape — the hot path's `RollupFanOut` Map (one
    iteration per dirty CWID, #119) and the onboarding state machine's
    Rollup stage. Routes to `rollup_by_cwid --cwid …` — the PMID-aware
    DynamoDB path that writes a `STAGE#rollup_by_cwid#cwid:{cwid}` row
    carrying `input_pmid_set`. An absent or empty `cwid` is a contract
    violation and raises.
    """
    cwid = event.get("cwid")
    if cwid is None:
        raise ValueError(
            "rollup handler: event carries no 'cwid'; the hot RollupFanOut "
            "Map and the onboarding Rollup stage must both invoke this "
            "Lambda with the per-CWID 'cwid' key"
        )
    cwid = str(cwid).strip()
    if not cwid:
        raise ValueError(
            "rollup handler: event carries an empty 'cwid'; the caller "
            "(the hot RollupFanOut Map or the onboarding Rollup stage) "
            "must invoke Rollup with a CWID"
        )
    cmd = [
        sys.executable, "-m", "rollup_by_cwid", "--emit-envelope",
        "--cwid", cwid,
    ]
    logger.info(f"rollup handler invoking: --cwid {cwid}")

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
