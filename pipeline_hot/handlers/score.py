"""Hot-path Score Lambda handler.

Wraps `score_publications.py --emit-envelope` and returns the STAGE#
envelope dict for the state machine's DynamoDB:PutItem SDK integration.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _parse_envelope_from_stdout(stdout: str) -> dict:
    """The envelope is the last JSON object on stdout. Everything else is
    log noise from the script's `print` statements during scoring."""
    # Walk lines bottom-up; the last `{...}` block is the envelope.
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError(
        "score handler: no JSON envelope found on upstream stdout"
    )


def handler(event: dict, context: Any = None) -> dict:
    """Invoke score_publications --emit-envelope and return the envelope.

    Expected event:
        {
          "delta": {"pmids": [...], "size": N},
          "last_successful_hot_run_at": "<iso8601>" | null
        }
    """
    delta = event.get("delta", {})
    delta_since = event.get("last_successful_hot_run_at")

    cmd = [sys.executable, "-m", "score_publications", "--emit-envelope"]
    if delta_since:
        cmd += ["--delta-since", delta_since]

    logger.info(f"score handler invoking: {' '.join(cmd)} ({delta.get('size', 0)} pmids)")
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"score_publications exited {proc.returncode}: "
            f"stderr={proc.stderr[-2000:]}"
        )
    return _parse_envelope_from_stdout(proc.stdout)
