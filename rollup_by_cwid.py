"""Build a per-CWID rollup from the two breakdown CSVs."""
import csv
from collections import defaultdict

n_activities = defaultdict(int)
n_topics = defaultdict(set)
n_subtopic_activities = defaultdict(int)
n_subtopics = defaultdict(set)

with open("cwid_topic_counts.csv") as f:
    r = csv.DictReader(f)
    for row in r:
        cwid = row["personIdentifier"]
        n = int(row["n_activities"])
        n_activities[cwid] += n
        n_topics[cwid].add(row["topic_id"])

with open("cwid_subtopic_counts.csv") as f:
    r = csv.DictReader(f)
    for row in r:
        cwid = row["personIdentifier"]
        n = int(row["n_activities"])
        n_subtopic_activities[cwid] += n
        n_subtopics[cwid].add(row["primary_subtopic_id"])

all_cwids = sorted(set(n_activities) | set(n_subtopics))

with open("cwid_rollup.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow([
        "personIdentifier",
        "n_activities",
        "n_distinct_topics",
        "n_subtopic_activities",
        "n_distinct_subtopics",
    ])
    rows = [
        (
            cwid,
            n_activities[cwid],
            len(n_topics[cwid]),
            n_subtopic_activities[cwid],
            len(n_subtopics[cwid]),
        )
        for cwid in all_cwids
    ]
    rows.sort(key=lambda r: -r[1])
    for row in rows:
        w.writerow(row)

print(f"Wrote cwid_rollup.csv ({len(all_cwids):,} rows)")
print("\nTop 10 by n_activities:")
print(f"{'cwid':<12} {'acts':>6} {'topics':>7} {'sub_acts':>9} {'subtopics':>10}")
for row in rows[:10]:
    print(f"{row[0]:<12} {row[1]:>6} {row[2]:>7} {row[3]:>9} {row[4]:>10}")
