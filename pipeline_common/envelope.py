"""Shared STAGE# envelope helpers for the per-stage Lambda handlers.

Promoted from `pipeline_hot/handlers/score.py` (#80 Phase 2, PR 1) so the
hot-path handlers and the onboarding package both import shared public
infrastructure instead of reaching into `score.py`'s private names.

The per-stage scripts (`score_publications.py`, `assign_subtopics.py`,
`compute_top_topic.py`, `rollup_by_cwid.py`) run with `--emit-envelope`
and print the STAGE# record as JSON on stdout. A Lambda handler then:

  1. `parse_envelope_from_stdout` — extracts that JSON envelope (the last
     JSON object on stdout; everything else is log noise).
  2. `to_ddb_typed_envelope` — converts it to DynamoDB attribute-typed
     format so the state machine's `dynamodb:putItem` SDK integration can
     consume it directly via `Item.$`.

`to_ddb_typed_envelope` is the load-bearing fix for the optimized
DynamoDB integration: it does NOT auto-type plain Python values, so
passing a bare dict like `{"PK": "..."}` raises `States.Runtime`
("Cannot construct instance of AttributeValue").
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeSerializer

_serializer = TypeSerializer()

# Envelope fields that arrive as JSON strings (because the upstream script
# serializes via `json.dumps(..., default=str)` to handle Decimal) but
# represent numeric attributes in DynamoDB. Without this coercion
# `TypeSerializer` would emit `{"S": "0"}` for cost — wrong DDB type,
# breaks numeric aggregation queries.
_NUMERIC_STR_FIELDS = {"cost_observed_usd"}


def parse_envelope_from_stdout(stdout: str) -> dict:
    """Return the STAGE# envelope — the last JSON object on `stdout`.

    Everything else on stdout is log noise from the upstream script's
    `print` statements during processing. Walks lines bottom-up; the last
    `{...}` block that parses as JSON is the envelope.

    Raises `RuntimeError` if no JSON object is found.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("no JSON envelope found on upstream stdout")


def to_ddb_typed_envelope(envelope: dict) -> dict:
    """Convert a parsed envelope dict to DynamoDB attribute-typed format.

    The state machine's `WriteXStageRow` states feed this return value
    straight into `dynamodb:putItem` via `Item.$`, which requires every
    value to be already wrapped (e.g. `{"S": "..."}`, `{"N": "5271"}`).

    Fields in `_NUMERIC_STR_FIELDS` (e.g. `cost_observed_usd`) are
    serialized by the upstream script's `default=str` into a JSON string,
    so they are re-cast to `Decimal` before `TypeSerializer` sees them —
    the resulting attribute is `N`, not `S`.
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
