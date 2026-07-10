"""Read-only scan of reciterai GRANT# items + regex-flag port + canary + stratified sample.

Step 1 of the eligibility-capture audit (see README.md in this directory and
docs/grant-matching-measurements-runbook.md). Ports the SPS `deriveEligibilityFlags`
regexes (Scholars-Profile-System `etl/dynamodb/grant-opportunity-mapper.ts`,
origin/master) verbatim to Python, scans every GRANT#/META item, and writes:

  out/corpus_stats.json  (counts, canaries, per-source breakdown)
  out/sample.json        (~100 stratified items, preferring non-empty eligibility_raw)

Runnable from anywhere:  python scripts/eligibility_audit/scan_grants.py
Outputs are point-in-time snapshots of live grant text — out/ is gitignored; never
commit them.
"""
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import boto3

OUT_DIR = Path(__file__).resolve().parent / "out"
TABLE = "reciterai"
SEED = 20260709  # fixed — reruns against the same corpus snapshot reproduce the sample

# ---- deriveEligibilityFlags port (regexes copied from the TS mapper) ----
FOREIGN_ONLY = [
    re.compile(r"\bforeign (institutions?|organizations?|entities)\s+only\b"),
    re.compile(r"\bnon-?u\.?s\.?\s+(institutions?|organizations?|entities)\s+only\b"),
    re.compile(r"\boutside the united states only\b"),
]
STUDENT_ONLY = [
    re.compile(r"\b(predoctoral|pre-doctoral|dissertation)\b"),
    re.compile(r"\bstudents? only\b"),
    re.compile(r"\bmust be (a|an )?(enrolled|currently enrolled) (student|predoctoral)\b"),
    re.compile(r"\benrolled (predoctoral|doctoral) students?\b"),
]
FACULTY_ONLY = [
    re.compile(r"\bindependent (faculty )?(investigators?|researchers?)\b"),
    re.compile(r"\bmust hold (an? )?(independent )?faculty appointment\b"),
    re.compile(r"\bno postdoctoral (fellows?|researchers?)\b"),
]
LIMITED = [
    re.compile(r"\blimited submission\b"),
    re.compile(r"\binternal competition\b"),
    re.compile(r"\b(only|up to)\s+\w+\s+applications? per institution\b"),
]


def derive_flags(eligibility_raw):
    text = (eligibility_raw or "").lower()
    flags = []
    foreign_only = any(p.search(text) for p in FOREIGN_ONLY)
    if not foreign_only:
        flags.append("us_eligible")
    student_only = any(p.search(text) for p in STUDENT_ONLY)
    if student_only:
        flags.append("student_only")
    if not student_only:
        flags.append("faculty_eligible")
    faculty_only = any(p.search(text) for p in FACULTY_ONLY)
    if not faculty_only and not student_only:
        flags.append("postdoc_eligible")
    if any(p.search(text) for p in LIMITED):
        flags.append("internal_limited_submission")
    return flags, {
        "foreign_only": foreign_only,
        "student_only": student_only,
        "faculty_only": faculty_only,
        "limited": any(p.search(text) for p in LIMITED),
    }


DEFAULT_FLAGS = {"us_eligible", "faculty_eligible", "postdoc_eligible"}

# ---- scan ----
OUT_DIR.mkdir(exist_ok=True)
client = boto3.client("dynamodb", region_name="us-east-1")
paginator = client.get_paginator("scan")
items = []
scanned = 0
for page in paginator.paginate(
    TableName=TABLE,
    FilterExpression="begins_with(PK, :g) AND SK = :meta",
    ExpressionAttributeValues={":g": {"S": "GRANT#"}, ":meta": {"S": "META"}},
    ProjectionExpression="PK, opportunity_id, #src, sponsor, title, eligibility_raw, synopsis, mechanism, award_ceiling",
    ExpressionAttributeNames={"#src": "source"},
):
    scanned += page.get("ScannedCount", 0)
    for it in page.get("Items", []):
        items.append({
            "pk": it.get("PK", {}).get("S", ""),
            "opportunity_id": it.get("opportunity_id", {}).get("S", ""),
            "source": it.get("source", {}).get("S", ""),
            "sponsor": it.get("sponsor", {}).get("S", ""),
            "title": it.get("title", {}).get("S", ""),
            "eligibility_raw": it.get("eligibility_raw", {}).get("S", ""),
            "synopsis": it.get("synopsis", {}).get("S", ""),
            "mechanism": it.get("mechanism", {}).get("S", ""),
            "award_ceiling": it.get("award_ceiling", {}).get("N"),
        })

print(f"scanned_table_items={scanned} grant_meta_items={len(items)}", file=sys.stderr)

# ---- corpus stats + canaries ----
by_source = Counter(i["source"] for i in items)
with_prose = [i for i in items if i["eligibility_raw"].strip()]
by_source_prose = Counter(i["source"] for i in with_prose)

literal_zero = 0
no_signal = 0  # prose present but NO regex fired -> pure default flag set
no_signal_by_source = Counter()
for i in with_prose:
    flags, fired = derive_flags(i["eligibility_raw"])
    i["derived_flags"] = flags
    if not flags:
        literal_zero += 1
    if not any(fired.values()):
        no_signal += 1
        no_signal_by_source[i["source"]] += 1

# fired-signal counts across prose corpus
fired_counts = Counter()
for i in with_prose:
    _, fired = derive_flags(i["eligibility_raw"])
    for k, v in fired.items():
        if v:
            fired_counts[k] += 1

stats = {
    "grant_meta_items": len(items),
    "by_source": dict(by_source),
    "with_eligibility_prose": len(with_prose),
    "with_prose_by_source": dict(by_source_prose),
    "canary_literal_zero_flags": literal_zero,
    "canary_prose_but_no_regex_fired": no_signal,
    "canary_no_signal_by_source": dict(no_signal_by_source),
    "regex_fired_counts_over_prose_corpus": dict(fired_counts),
    "prose_length_stats": {
        "min": min(len(i["eligibility_raw"]) for i in with_prose) if with_prose else 0,
        "max": max(len(i["eligibility_raw"]) for i in with_prose) if with_prose else 0,
        "mean": round(sum(len(i["eligibility_raw"]) for i in with_prose) / len(with_prose), 1) if with_prose else 0,
    },
}
with open(OUT_DIR / "corpus_stats.json", "w") as f:
    json.dump(stats, f, indent=2)
print(json.dumps(stats, indent=2))

# ---- stratified sample (~100), prefer non-empty eligibility prose ----
random.seed(SEED)
TARGET = 100
groups = defaultdict(list)
for i in items:
    groups[i["source"]].append(i)

sources = sorted(groups.keys())
per_source = max(1, TARGET // max(1, len(sources)))
sample = []
for src in sources:
    pool = groups[src]
    prose_pool = [i for i in pool if i["eligibility_raw"].strip()]
    take = min(per_source, len(pool))
    if len(prose_pool) >= take:
        sample.extend(random.sample(prose_pool, take))
    else:
        sample.extend(prose_pool)
        rest = [i for i in pool if not i["eligibility_raw"].strip()]
        sample.extend(random.sample(rest, min(take - len(prose_pool), len(rest))))

# top up to TARGET from remaining prose items across all sources
chosen = {i["pk"] for i in sample}
leftover = [i for i in with_prose if i["pk"] not in chosen]
random.shuffle(leftover)
while len(sample) < TARGET and leftover:
    sample.append(leftover.pop())

for i in sample:
    if "derived_flags" not in i:
        i["derived_flags"], _ = derive_flags(i["eligibility_raw"])

print(f"sample_size={len(sample)} by_source={Counter(i['source'] for i in sample)}", file=sys.stderr)
with open(OUT_DIR / "sample.json", "w") as f:
    json.dump(sample, f, indent=2)
