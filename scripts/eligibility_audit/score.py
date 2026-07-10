"""Score per-facet agreement: regex-derived flags vs Sonnet reference extraction.

Step 3 of the eligibility-capture audit. Reads out/sample.json + out/extractions.json
and writes out/scores.json (agreement per comparable facet, miss rows with snippets,
prevalence of facets the regex layer cannot represent, value distributions).

Runnable from anywhere:  python scripts/eligibility_audit/score.py
Pure local computation — no AWS calls.
"""
import json
from collections import Counter
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "out"

with open(OUT_DIR / "sample.json") as f:
    sample = json.load(f)
with open(OUT_DIR / "extractions.json") as f:
    ext = json.load(f)

FACULTY_STAGES = {"early_career_faculty", "mid_career_faculty", "senior_faculty", "any_faculty", "clinician"}
STUDENT_STAGES = {"undergraduate", "graduate_student"}


def snippet(item, n=220):
    t = (item["eligibility_raw"] or "").replace("\n", " ").strip()
    return (t[:n] + ("…" if len(t) > n else ""))


rows = []
for it in sample:
    pk = it["pk"]
    if pk not in ext:
        continue
    e = ext[pk]
    flags = set(it["derived_flags"])
    stages = set(e.get("career_stages") or [])
    orgs = set(e.get("applicant_org_types") or [])

    ref = {
        "student_only": bool(stages) and stages <= STUDENT_STAGES,
        "faculty_eligible": (not stages) or bool(stages & FACULTY_STAGES),
        "postdoc_eligible": (not stages) or ("postdoc" in stages),
        "internal_limited_submission": bool(e.get("limited_submission")),
        "us_eligible": not (orgs == {"foreign_org"}),
    }
    reg = {
        "student_only": "student_only" in flags,
        "faculty_eligible": "faculty_eligible" in flags,
        "postdoc_eligible": "postdoc_eligible" in flags,
        "internal_limited_submission": "internal_limited_submission" in flags,
        "us_eligible": "us_eligible" in flags,
    }
    rows.append({"pk": pk, "item": it, "ext": e, "ref": ref, "reg": reg})

# --- agreement per comparable facet ---
facets = ["us_eligible", "student_only", "faculty_eligible", "postdoc_eligible", "internal_limited_submission"]
agreement = {}
misses = {f: [] for f in facets}
for f in facets:
    agree = 0
    for r in rows:
        if r["reg"][f] == r["ref"][f]:
            agree += 1
        else:
            misses[f].append({
                "pk": r["pk"],
                "source": r["item"]["source"],
                "title": r["item"]["title"][:110],
                "regex": r["reg"][f],
                "reference": r["ref"][f],
                "career_stages": sorted(r["ext"].get("career_stages") or []),
                "snippet": snippet(r["item"]),
            })
    agreement[f] = {"agree": agree, "n": len(rows), "pct": round(100.0 * agree / len(rows), 1)}

# --- prevalence of facets the regex layer cannot represent at all ---
prev = Counter()
examples = {}
def note(key, r):
    prev[key] += 1
    if key not in examples:
        examples[key] = {"title": r["item"]["title"][:110], "snippet": snippet(r["item"], 180)}

for r in rows:
    e = r["ext"]
    orgs = set(e.get("applicant_org_types") or [])
    if orgs and orgs != {"unrestricted"}:
        note("applicant_org_types_restricted", r)
    if e.get("degree_required"):
        note("degree_required_stated", r)
    if e.get("citizenship_requirement") not in (None, "", "not_stated"):
        note("citizenship_requirement_stated", r)
    if e.get("esi_targeted"):
        note("esi_targeted", r)
    if e.get("small_business_only"):
        note("small_business_only", r)
    if e.get("government_only"):
        note("government_only", r)
    if e.get("cost_sharing_required"):
        note("cost_sharing_required", r)
    if e.get("individual_award"):
        note("individual_award", r)

# distribution details for the doc
org_counter = Counter()
stage_counter = Counter()
degree_counter = Counter()
cit_counter = Counter()
for r in rows:
    for o in r["ext"].get("applicant_org_types") or []:
        org_counter[o] += 1
    for s in r["ext"].get("career_stages") or []:
        stage_counter[s] += 1
    for d in r["ext"].get("degree_required") or []:
        degree_counter[d] += 1
    cit_counter[r["ext"].get("citizenship_requirement") or "not_stated"] += 1

out = {
    "n_scored": len(rows),
    "agreement": agreement,
    "misses": misses,
    "uncaptured_prevalence": dict(prev),
    "uncaptured_examples": examples,
    "distributions": {
        "applicant_org_types": dict(org_counter),
        "career_stages": dict(stage_counter),
        "degree_required": dict(degree_counter),
        "citizenship_requirement": dict(cit_counter),
    },
}
with open(OUT_DIR / "scores.json", "w") as f:
    json.dump(out, f, indent=2)

print(json.dumps({k: out[k] for k in ("n_scored", "agreement", "uncaptured_prevalence", "distributions")}, indent=2))
print("\nMISS COUNTS:", {f: len(m) for f, m in misses.items()})
