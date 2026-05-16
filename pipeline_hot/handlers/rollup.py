"""Hot-path Rollup Lambda handler.

Wraps `rollup_by_cwid.py --cwids <dirty> --emit-envelope`. The dirty
CWID set is computed by `pipeline_common.dirty_set` (introduced
alongside this phase) from the score + assign STAGE# rows. For T7 we
accept the dirty set directly in the event payload — the state
machine's Compute state assembles it from the upstream Task results.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from pipeline_hot.handlers.score import (
    _parse_envelope_from_stdout,
    _to_ddb_typed_envelope,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def handler(event: dict, context: Any = None) -> dict:
    """Invoke rollup_by_cwid --cwids ... --emit-envelope and return the envelope.

    Expected event:
        {
          "dirty_cwids": ["cwid1", "cwid2", ...]
        }
    Empty dirty_cwids → full re-rollup (the caller should rarely do this in
    the hot path; cold path is the right place for full re-rollups).
    """
    dirty_cwids = event.get("dirty_cwids") or []

    cmd = [
        sys.executable,
        "-m",
        "rollup_by_cwid",
        "--emit-envelope",
    ]
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
    return _to_ddb_typed_envelope(_parse_envelope_from_stdout(proc.stdout))
