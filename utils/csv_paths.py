"""Shared per-CWID breakdown CSV path/header helpers (IN-05).

Phase 12 D-13 renamed the per-CWID subtopic breakdown CSV from
``cwid_subtopic_counts.csv`` to ``faculty_subtopic_counts_exclusive.csv``
and added a sibling ``faculty_subtopic_counts_inclusive.csv``. Both
``rollup_by_cwid`` and ``build_cwid_json`` need to:

1. Resolve the canonical exclusive CSV (with one-cycle legacy fallback).
2. Pick the subtopic-id column from a header row, since the exclusive CSV
   uses ``primary_subtopic_id`` and the inclusive CSV uses ``subtopic_id``.

The two modules used to carry byte-identical copies of these two helpers
(IN-05). This module consolidates them so a future schema change touches
one file instead of two.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Phase 12 D-13 CSV name constants.
DEFAULT_SUBTOPIC_CSV = Path("faculty_subtopic_counts_exclusive.csv")
LEGACY_SUBTOPIC_CSV = Path("cwid_subtopic_counts.csv")
INCLUSIVE_SUBTOPIC_CSV = Path("faculty_subtopic_counts_inclusive.csv")

# Acceptable column names for the subtopic identifier in a subtopic CSV.
# Exclusive CSV uses "primary_subtopic_id"; inclusive CSV uses "subtopic_id".
SUBTOPIC_ID_COLUMNS = ("primary_subtopic_id", "subtopic_id")


def resolve_subtopic_csv(provided: Path | None = None) -> Path:
    """Resolve the per-CWID exclusive subtopic CSV path.

    Resolution order:
    1. ``provided`` argument (explicit caller override) — returned as-is.
    2. :data:`DEFAULT_SUBTOPIC_CSV` if it exists on disk.
    3. :data:`LEGACY_SUBTOPIC_CSV` if it exists — emits a deprecation warning.
    4. Otherwise, raises ``FileNotFoundError`` naming BOTH candidate paths
       (WR-07: avoids the "exclusive.csv not found" confusion for operators
       whose file lives under the legacy name).

    Phase 12 D-13 producers write both names for one cycle; this fallback
    will be removed in a later phase once SPS and all readers have migrated.
    """
    if provided is not None:
        return provided
    if DEFAULT_SUBTOPIC_CSV.exists():
        return DEFAULT_SUBTOPIC_CSV
    if LEGACY_SUBTOPIC_CSV.exists():
        logger.warning(
            "Reading legacy CSV name '%s'. Phase 12 D-13 renamed this to '%s'. "
            "Update producers to write the new name; this fallback will be removed "
            "in a later phase.",
            LEGACY_SUBTOPIC_CSV,
            DEFAULT_SUBTOPIC_CSV,
        )
        return LEGACY_SUBTOPIC_CSV
    raise FileNotFoundError(
        f"Subtopic CSV not found. Checked: {DEFAULT_SUBTOPIC_CSV} (canonical, "
        f"Phase 12 D-13) and {LEGACY_SUBTOPIC_CSV} (legacy). "
        "Generate via count_by_cwid.py."
    )


def pick_subtopic_id_column(fieldnames: list[str] | None, source: Path) -> str:
    """Return the subtopic-id column name from a subtopic CSV header (CR-02).

    Accepts either of the Phase 12 D-13 schemas:

    - exclusive CSV header: ``primary_subtopic_id``
    - inclusive CSV header: ``subtopic_id``

    Raises ``ValueError`` with a clear message if neither column is present
    (defends against an operator pointing ``--subtopic-csv`` at an inclusive
    CSV by mistake, or against a future schema regression).
    """
    cols = set(fieldnames or [])
    for candidate in SUBTOPIC_ID_COLUMNS:
        if candidate in cols:
            return candidate
    raise ValueError(
        f"Subtopic CSV {source!r} is missing both expected columns "
        f"({SUBTOPIC_ID_COLUMNS}). Found columns: {sorted(cols)}. "
        "The exclusive CSV uses 'primary_subtopic_id'; the inclusive CSV uses "
        "'subtopic_id'. Check the producer (count_by_cwid.py) or pass an explicit "
        "--subtopic-csv path."
    )
