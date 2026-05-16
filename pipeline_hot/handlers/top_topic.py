"""Hot-path TopTopic Lambda handler (#68).

Wraps `compute_top_topic.py --pmid <p1> --pmid <p2> ... --emit-envelope`.
Materializes `top_topic_id` on every activity row for each delta PMID
after Pass 2 (Assign) has populated `subtopic_confidences`.

Read-only field for SPS topic-page UX; not a designation, not a rollup
input. See `docs/topic-cross-listing-display.md`.
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
    """Invoke compute_top_topic --pmid ... --emit-envelope and return the envelope.

    Expected event:
        {
          "delta_pmids": ["pmid1", "pmid2", ...]
        }

    An empty delta is a valid no-op: the script still emits a complete-stage
    envelope with `records_written: 0` so the state machine's stage-row
    write succeeds.
    """
    delta_pmids = event.get("delta_pmids") or []

    cmd = [
        sys.executable,
        "compute_top_topic.py",
        "--emit-envelope",
    ]
    for pmid in delta_pmids:
        cmd += ["--pmid", str(pmid)]

    if not delta_pmids:
        # The script requires at least one of --pmid / --pmids-file / --all.
        # For an empty hot-path delta we point at /dev/null so the script
        # produces a clean zero-row envelope.
        cmd += ["--pmids-file", "/dev/null"]

    logger.info(
        f"top_topic handler invoking: compute_top_topic.py ({len(delta_pmids)} pmids)"
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
            f"compute_top_topic exited {proc.returncode}: "
            f"stderr={proc.stderr[-2000:]}"
        )
    return _to_ddb_typed_envelope(_parse_envelope_from_stdout(proc.stdout))
