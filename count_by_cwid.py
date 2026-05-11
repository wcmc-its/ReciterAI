"""Scan reciterai-chatbot TOPIC# records and produce per-CWID activity counts
broken down by topic and primary_subtopic_id."""
import csv
from collections import defaultdict

import boto3

TABLE = "reciterai-chatbot"

session = boto3.Session()
client = session.client("dynamodb")

topic_counts = defaultdict(int)      # (cwid, topic_id) -> n_activities
subtopic_counts = defaultdict(int)   # (cwid, subtopic_id) -> n_activities

projection = "PK, faculty_uid, primary_subtopic_id"
expr_names = {"#PK": "PK"}

paginator = client.get_paginator("scan")
pages = paginator.paginate(
    TableName=TABLE,
    ProjectionExpression="#PK, faculty_uid, primary_subtopic_id",
    ExpressionAttributeNames=expr_names,
)

scanned = 0
topic_rows = 0
subtopic_rows = 0
for page in pages:
    for item in page.get("Items", []):
        scanned += 1
        pk = item.get("PK", {}).get("S", "")
        if not pk.startswith("TOPIC#"):
            continue
        topic_id = pk[len("TOPIC#"):]
        cwid_raw = item.get("faculty_uid", {}).get("S", "")
        if not cwid_raw:
            continue
        cwid = cwid_raw[len("cwid_"):] if cwid_raw.startswith("cwid_") else cwid_raw
        topic_counts[(cwid, topic_id)] += 1
        topic_rows += 1
        sub = item.get("primary_subtopic_id", {}).get("S")
        if sub:
            subtopic_counts[(cwid, sub)] += 1
            subtopic_rows += 1
    if scanned % 20000 < 1000:
        print(f"  scanned={scanned:,} topic_rows={topic_rows:,} subtopic_rows={subtopic_rows:,}")

print(f"\nTotal scanned: {scanned:,}")
print(f"TOPIC# activity rows: {topic_rows:,}")
print(f"Activity rows with primary_subtopic_id: {subtopic_rows:,}")
print(f"Distinct (cwid, topic) pairs:    {len(topic_counts):,}")
print(f"Distinct (cwid, subtopic) pairs: {len(subtopic_counts):,}")

with open("cwid_topic_counts.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["personIdentifier", "topic_id", "n_activities"])
    for (cwid, t), n in sorted(topic_counts.items(), key=lambda x: (x[0][0], -x[1])):
        w.writerow([cwid, t, n])

with open("cwid_subtopic_counts.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["personIdentifier", "primary_subtopic_id", "n_activities"])
    for (cwid, s), n in sorted(subtopic_counts.items(), key=lambda x: (x[0][0], -x[1])):
        w.writerow([cwid, s, n])

print("\nWrote cwid_topic_counts.csv and cwid_subtopic_counts.csv")
