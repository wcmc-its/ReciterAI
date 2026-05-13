"""Build per-CWID nested JSON of topic + subtopic counts.

Phase 12 D-13: reads faculty_subtopic_counts_exclusive.csv (canonical);
falls back to legacy cwid_subtopic_counts.csv with a deprecation warning
if the new name is absent (one-cycle deprecation window per D-13).
"""
import csv
import json
import logging
import warnings
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

# Phase 12 D-13 CSV name constants (mirrors rollup_by_cwid.py).
_NEW_EXCLUSIVE_CSV = Path("faculty_subtopic_counts_exclusive.csv")
_LEGACY_CSV = Path("cwid_subtopic_counts.csv")
# Acceptable column names for the subtopic identifier (CR-02): exclusive CSV uses
# "primary_subtopic_id"; inclusive CSV uses "subtopic_id".
_SUBTOPIC_ID_COLUMNS = ("primary_subtopic_id", "subtopic_id")


def _resolve_subtopic_csv(provided: Path | None = None) -> Path:
    """Prefer the Phase 12 D-13 canonical name; fall back to legacy with a deprecation warning.

    Phase 12 D-13: producers write both names for one cycle. This fallback will
    be removed in a later phase once all readers have migrated to the new name.

    WR-07: when neither candidate exists, raise FileNotFoundError naming both
    candidates rather than letting an opaque "exclusive.csv not found" downstream
    error obscure that we tried two paths.
    """
    if provided is not None:
        return provided
    if _NEW_EXCLUSIVE_CSV.exists():
        return _NEW_EXCLUSIVE_CSV
    if _LEGACY_CSV.exists():
        logger.warning(
            "Reading legacy CSV name '%s'. Phase 12 D-13 renamed this to '%s'. "
            "Update producers to write the new name; this fallback will be removed in a later phase.",
            _LEGACY_CSV,
            _NEW_EXCLUSIVE_CSV,
        )
        return _LEGACY_CSV
    raise FileNotFoundError(
        f"Subtopic CSV not found. Checked: {_NEW_EXCLUSIVE_CSV} (canonical, "
        f"Phase 12 D-13) and {_LEGACY_CSV} (legacy). Generate via count_by_cwid.py."
    )


def _pick_subtopic_id_column(fieldnames: list[str] | None, source: Path) -> str:
    """Return the subtopic-id column name from a subtopic CSV header (CR-02).

    Accepts ``primary_subtopic_id`` (exclusive CSV) or ``subtopic_id``
    (inclusive CSV). Raises ValueError with a clear message if neither column
    is present.
    """
    cols = set(fieldnames or [])
    for candidate in _SUBTOPIC_ID_COLUMNS:
        if candidate in cols:
            return candidate
    raise ValueError(
        f"Subtopic CSV {source!r} is missing both expected columns "
        f"({_SUBTOPIC_ID_COLUMNS}). Found columns: {sorted(cols)}. "
        "The exclusive CSV uses 'primary_subtopic_id'; the inclusive CSV uses "
        "'subtopic_id'."
    )


per_cwid = defaultdict(lambda: {"topics": {}, "subtopics": {}})

with open("cwid_topic_counts.csv") as f:
    for row in csv.DictReader(f):
        per_cwid[row["personIdentifier"]]["topics"][row["topic_id"]] = int(row["n_activities"])

subtopic_csv = _resolve_subtopic_csv()
with open(subtopic_csv) as f:
    reader = csv.DictReader(f)
    _sub_id_key = _pick_subtopic_id_column(reader.fieldnames, subtopic_csv)
    for row in reader:
        per_cwid[row["personIdentifier"]]["subtopics"][row[_sub_id_key]] = int(row["n_activities"])

# Add summary fields and sort each dict desc by count
out = {}
for cwid in sorted(per_cwid):
    topics = dict(sorted(per_cwid[cwid]["topics"].items(), key=lambda kv: -kv[1]))
    subtopics = dict(sorted(per_cwid[cwid]["subtopics"].items(), key=lambda kv: -kv[1]))
    out[cwid] = {
        "n_activities": sum(topics.values()),
        "n_distinct_topics": len(topics),
        "n_distinct_subtopics": len(subtopics),
        "topics": topics,
        "subtopics": subtopics,
    }

with open("cwid_topics_subtopics.json", "w") as f:
    json.dump(out, f, indent=2)

print(f"Wrote cwid_topics_subtopics.json ({len(out):,} CWIDs)")
print("\nSample (first CWID):")
sample_cwid = next(iter(out))
print(json.dumps({sample_cwid: out[sample_cwid]}, indent=2)[:800])
