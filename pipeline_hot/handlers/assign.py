"""Hot-path Assign Lambda handler.

Wraps `assign_subtopics.py --emit-envelope` per topic.

In the hot path the state machine fans out one Assign Task per topic
present in the delta PMID set. This handler accepts a single topic and
its associated delta PMID list, invokes the upstream script, and
returns the STAGE# envelope. The fan-out itself is handled at the
state-machine layer (`state_machine.asl.json`), not in Python — Step
Functions Map state owns parallelism.
"""

from __future__ import annotations

import json
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
    """Invoke assign_subtopics --emit-envelope and return the envelope.

    Expected event:
        {
          "topic_id":    "cardiovascular_disease",
          "delta_pmids": ["pmid1", "pmid2", ...]
        }
    """
    topic_id = event.get("topic_id")
    if not topic_id:
        raise ValueError("assign handler: missing 'topic_id' in event")
    delta_pmids = event.get("delta_pmids") or []

    cmd = [
        sys.executable,
        "-m",
        "assign_subtopics",
        "--topic", topic_id,
        "--emit-envelope",
    ]
    if delta_pmids:
        cmd += ["--delta-pmids", ",".join(str(p) for p in delta_pmids)]

    logger.info(
        f"assign handler invoking: {' '.join(cmd[:6])}... "
        f"({len(delta_pmids)} pmids, topic={topic_id})"
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
            f"assign_subtopics exited {proc.returncode}: "
            f"stderr={proc.stderr[-2000:]}"
        )
    return _to_ddb_typed_envelope(_parse_envelope_from_stdout(proc.stdout))
