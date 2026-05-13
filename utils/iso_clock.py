"""Canonical ISO 8601 UTC timestamp helper.

Single home for what used to be byte-identical `_now_iso()` / `_now_iso_z()`
implementations scattered across the pipeline (issue #18, Phase 12 IN-04).

Format: second-precision, ``Z`` rather than ``+00:00``, no microseconds.
The matching regex used by tests is
``^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$``.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
