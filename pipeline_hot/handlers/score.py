"""Hot-path Score Lambda handler.

Wraps `score_publications.py --emit-envelope` and returns the STAGE#
envelope dict — already converted to DynamoDB attribute-typed format —
for the state machine's `arn:aws:states:::dynamodb:putItem` SDK
integration to consume directly via `Item.$": "$.score_envelope"`.

The optimized DynamoDB integration does NOT auto-type plain Python
values; passing a bare dict like `{"PK": "..."}` raises
`States.Runtime` ("Cannot construct instance of AttributeValue").
`_to_ddb_typed_envelope` below is the load-bearing fix.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from boto3.dynamodb.types import TypeSerializer

# Populate DB_* env vars in Lambda before the subprocess to
# `score_publications.py` runs — it queries ReciterDB at startup
# (extract_publications / extract_author_mapping / extract_faculty_
# _metadata). Subprocesses inherit env vars, so importing here makes
# them visible to the subprocess. No-op outside Lambda.
import utils.secrets_loader  # noqa: F401, E402

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_serializer = TypeSerializer()

# Envelope fields that arrive as JSON strings (because the upstream
# script serializes via `json.dumps(..., default=str)` to handle
# Decimal) but represent numeric attributes in DynamoDB. Without this
# coercion `TypeSerializer` would emit `{"S": "0"}` for cost — wrong
# DDB type, breaks numeric aggregation queries.
_NUMERIC_STR_FIELDS = {"cost_observed_usd"}


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


def _to_ddb_typed_envelope(envelope: dict) -> dict:
    """Convert a parsed envelope dict to DDB attribute-typed format.

    The state machine's `WriteXStageRow` states feed this return value
    straight into `dynamodb:putItem` via `Item.$`, which requires every
    value to be already wrapped (e.g. `{"S": "..."}`, `{"N": "5271"}`).

    Handling for `cost_observed_usd`: serialized by `default=str` into
    a JSON string, so re-cast to Decimal before TypeSerializer sees it
    so the resulting attribute is `N`, not `S`.
    """
    canonical: dict[str, Any] = {}
    for k, v in envelope.items():
        if k in _NUMERIC_STR_FIELDS and isinstance(v, str):
            try:
                canonical[k] = Decimal(v)
            except Exception:
                # Fall through unchanged; TypeSerializer will fail loudly
                # rather than silently mis-type.
                canonical[k] = v
        else:
            canonical[k] = v
    return {k: _serializer.serialize(v) for k, v in canonical.items()}


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
    return _to_ddb_typed_envelope(_parse_envelope_from_stdout(proc.stdout))
