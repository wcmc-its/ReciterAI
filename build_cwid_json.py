"""Build per-CWID nested JSON of topic + subtopic counts.

Phase 12 D-13: reads faculty_subtopic_counts_exclusive.csv (canonical);
falls back to legacy cwid_subtopic_counts.csv with a deprecation warning
if the new name is absent (one-cycle deprecation window per D-13).
"""
import csv
import json
import logging
import sys
import warnings
from collections import defaultdict
from pathlib import Path

# Ensure repo root is importable regardless of cwd, so utils/* resolves
# when the script is run as `python build_cwid_json.py`.
sys.path.insert(0, str(Path(__file__).parent))

# IN-05: shared with rollup_by_cwid.py via utils/csv_paths.py to keep the
# Phase 12 D-13 dual-name resolver and the dual-header column picker in
# exactly one place.
from utils.csv_paths import (
    pick_subtopic_id_column as _pick_subtopic_id_column,
    resolve_subtopic_csv as _resolve_subtopic_csv,
)

logger = logging.getLogger(__name__)


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
