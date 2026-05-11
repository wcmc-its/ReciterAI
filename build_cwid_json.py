"""Build per-CWID nested JSON of topic + subtopic counts."""
import csv
import json
from collections import defaultdict

per_cwid = defaultdict(lambda: {"topics": {}, "subtopics": {}})

with open("cwid_topic_counts.csv") as f:
    for row in csv.DictReader(f):
        per_cwid[row["personIdentifier"]]["topics"][row["topic_id"]] = int(row["n_activities"])

with open("cwid_subtopic_counts.csv") as f:
    for row in csv.DictReader(f):
        per_cwid[row["personIdentifier"]]["subtopics"][row["primary_subtopic_id"]] = int(row["n_activities"])

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
