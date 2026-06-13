#!/usr/bin/env python3
"""A/B validation of the #210 substrate-gate prompt change across a cross-topic sample.

For each sampled (topic, member) the classifier is run TWICE on identical inputs
(same candidate subtopics, same activity) — once with the OLD system prompt
(current branch, no SUBSTRATE MATCHING block) and once with the NEW system prompt
(#210, with the block). Comparing on identical inputs isolates the prompt effect.

The regression signal is churn on NON-substrate topics: if the global prompt
change causes members to drop to *unassigned* (or lose their primary) on topics
that are not substrate-defined, the change is unsafe to ship globally. On
substrate-defined topics, rerouting non-matching members is the intended benefit.

Read-only against DynamoDB. Live Haiku calls (2 per sampled member).

    python3 scripts/validate_substrate_gate_ab.py                 # default sample
    python3 scripts/validate_substrate_gate_ab.py --per-topic 15
    python3 scripts/validate_substrate_gate_ab.py --topics biomedical_informatics,health_economics
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from boto3.dynamodb.conditions import Key

import assign_subtopics as A
from prompts.subtopic_assignment import (
    ASSIGNMENT_SYSTEM_PROMPT as OLD_PROMPT,
    BUILD_ASSIGNMENT_USER_MESSAGE,
)
from utils.bedrock_client import BedrockClient, HAIKU_MODEL
from utils.dynamodb_helpers import get_table, TABLE_NAME

DRAFT_DIR = REPO / ".planning/phases/04-subtopic-system"
NEW_PROMPT_REF = "origin/feat/subtopic-substrate-gate"

# Cross-topic sample. "substrate" topics = the gate is expected to act here;
# "non_substrate" topics = regression-watch (churn here would be a problem).
DEFAULT_TOPICS = {
    "substrate": [
        "biomedical_informatics",
        "radiology_medical_imaging",
        "single_cell_spatial_biology",
        "genetics_genomics_precision_medicine",
        "pathology_laboratory_medicine",
        "microbiome_research",
    ],
    "non_substrate": [
        "bioethics_medical_humanities",
        "health_economics",
        "medical_education_workforce",
        "palliative_end_of_life_care",
        "mental_health_psychiatry",
        "health_services_policy",
    ],
}


def load_new_prompt(ref: str = NEW_PROMPT_REF) -> str:
    """Load ASSIGNMENT_SYSTEM_PROMPT from the #210 branch (the real change text)."""
    src = subprocess.check_output(
        ["git", "show", f"{ref}:prompts/subtopic_assignment.py"], cwd=str(REPO), text=True
    )
    ns: dict = {}
    exec(compile(src, "<#210 prompt>", "exec"), ns)
    return ns["ASSIGNMENT_SYSTEM_PROMPT"]


def classify(client: BedrockClient, system_prompt: str, activity, topic_meta, subtopic_defs):
    user_msg = BUILD_ASSIGNMENT_USER_MESSAGE(
        activity=activity, topic_meta=topic_meta, subtopic_defs=subtopic_defs
    )
    converse_msgs, system_list = client._translate_messages(
        [{"role": "user", "content": user_msg}], system_prompt
    )
    raw = client._call_with_retry(
        model=HAIKU_MODEL,
        messages_converse=converse_msgs,
        system_list=system_list,
        max_tokens=400,
        temperature=0.0,
    )
    text = raw["output"]["message"]["content"][0]["text"]
    parsed = json.loads(A._strip_json_fences(text))
    return parsed.get("assignments", []) or []


def sample_members(topic_id: str, pool_size: int, per_topic: int, rng: random.Random):
    """Bounded scan of TOPIC#<topic_id>: collect up to pool_size unique PMIDs with
    a non-empty synopsis, then random.sample per_topic of them."""
    table = get_table(TABLE_NAME)
    pk = f"TOPIC#{topic_id}"
    score_floor = A._get_threshold("score_floor")
    seen: dict[str, dict] = {}
    last_key = None
    while True:
        kwargs = {
            "KeyConditionExpression": Key("PK").eq(pk) & Key("SK").begins_with("SCORE#"),
            "Limit": 500,
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        for row in resp.get("Items", []):
            if A._parse_score_from_sk(row.get("SK", "")) < score_floor:
                continue
            pmid = str(row.get("pmid", "") or "")
            syn = str(row.get("synopsis", "") or "")
            if not pmid or pmid in seen or len(syn) < 40:
                continue
            seen[pmid] = {
                "pmid": int(pmid) if pmid.isdigit() else pmid,
                "title": str(row.get("title", "") or ""),
                "synopsis": syn,
                "stored_primary": row.get("primary_subtopic_id"),
            }
        last_key = resp.get("LastEvaluatedKey")
        if not last_key or len(seen) >= pool_size:
            break
    members = list(seen.values())
    if len(members) > per_topic:
        members = rng.sample(members, per_topic)
    return members


def eval_member(client, member, topic_meta, subtopic_defs, valid_ids, floor):
    try:
        old_raw = classify(client, OLD_PROMPT, member, topic_meta, subtopic_defs)
        new_raw = classify(client, NEW_PROMPT, member, topic_meta, subtopic_defs)
    except Exception as exc:  # parse/bedrock failure — count, don't crash the run
        return {"pmid": member["pmid"], "error": str(exc)[:200]}
    old = A._filter_valid_assignments(old_raw, valid_ids, floor)
    new = A._filter_valid_assignments(new_raw, valid_ids, floor)
    old_set, new_set = set(old), set(new)

    def primary(d):
        return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if d else None

    return {
        "pmid": member["pmid"],
        "title": member["title"][:90],
        "old_set": sorted(old_set),
        "new_set": sorted(new_set),
        "old_primary": primary(old),
        "new_primary": primary(new),
        "changed": old_set != new_set,
        "dropped_to_empty": bool(old_set) and not new_set,
        "lost": sorted(old_set - new_set),
        "gained": sorted(new_set - old_set),
        "primary_changed": primary(old) != primary(new),
    }


def run(topics_by_class, per_topic, pool_size, concurrency, seed):
    rng = random.Random(seed)
    NEW_PROMPT = load_new_prompt()
    assert NEW_PROMPT != OLD_PROMPT, "NEW prompt identical to OLD — wrong branch ref?"
    assert "SUBSTRATE MATCHING" in NEW_PROMPT, "NEW prompt missing the substrate block"
    globals()["NEW_PROMPT"] = NEW_PROMPT  # used by classify via closure-free lookup

    taxonomy = A._load_taxonomy()
    client = BedrockClient()
    report = {"by_class": {}, "topics": []}

    for cls, topic_ids in topics_by_class.items():
        cls_rows = []
        for topic_id in topic_ids:
            draft_path = DRAFT_DIR / f"hierarchy_draft_{topic_id}.json"
            if topic_id not in taxonomy or not draft_path.exists():
                print(f"  [skip] {topic_id}: missing taxonomy/draft", flush=True)
                continue
            te = taxonomy[topic_id]
            topic_meta = {"id": topic_id, "label": te.get("label", ""), "description": te.get("description", "")}
            draft = json.loads(draft_path.read_text())
            subtopic_defs = [
                {"id": s["id"], "label": s.get("label", ""), "description": s.get("description", "")}
                for s in draft.get("subtopics", [])
            ]
            valid_ids = {s["id"] for s in subtopic_defs}
            if not valid_ids:
                print(f"  [skip] {topic_id}: zero subtopics in draft", flush=True)
                continue
            floor = A._get_threshold("confidence_floor")
            members = sample_members(topic_id, pool_size, per_topic, rng)
            print(f"  {cls}/{topic_id}: {len(members)} members, {len(valid_ids)} subtopics", flush=True)

            results = []
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = [
                    ex.submit(eval_member, client, m, topic_meta, subtopic_defs, valid_ids, floor)
                    for m in members
                ]
                for f in as_completed(futs):
                    results.append(f.result())

            ok = [r for r in results if "error" not in r]
            errs = [r for r in results if "error" in r]
            t = {
                "topic": topic_id,
                "class": cls,
                "n": len(ok),
                "errors": len(errs),
                "changed": sum(r["changed"] for r in ok),
                "dropped_to_empty": sum(r["dropped_to_empty"] for r in ok),
                "primary_changed": sum(r["primary_changed"] for r in ok),
                "lost_any": sum(1 for r in ok if r["lost"]),
                "gained_any": sum(1 for r in ok if r["gained"]),
                "examples": [
                    {"title": r["title"], "old": r["old_set"], "new": r["new_set"]}
                    for r in ok if r["changed"]
                ][:3],
            }
            report["topics"].append(t)
            cls_rows.append(t)

        agg = {
            "n": sum(t["n"] for t in cls_rows),
            "changed": sum(t["changed"] for t in cls_rows),
            "dropped_to_empty": sum(t["dropped_to_empty"] for t in cls_rows),
            "primary_changed": sum(t["primary_changed"] for t in cls_rows),
            "lost_any": sum(t["lost_any"] for t in cls_rows),
            "gained_any": sum(t["gained_any"] for t in cls_rows),
        }
        report["by_class"][cls] = agg

    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-topic", type=int, default=12, help="Members sampled per topic.")
    ap.add_argument("--pool-size", type=int, default=60, help="Unique-PMID pool to sample from.")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--topics", default=None, help="Comma-separated override; all treated as one class.")
    args = ap.parse_args(argv)

    topics_by_class = (
        {"custom": [t.strip() for t in args.topics.split(",") if t.strip()]}
        if args.topics else DEFAULT_TOPICS
    )

    print(f"=== #210 substrate-gate A/B — per_topic={args.per_topic} ===", flush=True)
    report = run(topics_by_class, args.per_topic, args.pool_size, args.concurrency, args.seed)

    print("\n=== PER-TOPIC ===")
    print(f"{'class':<14} {'topic':<38} {'n':>3} {'chg':>4} {'drop0':>6} {'primΔ':>6} {'lost':>5} {'gain':>5}")
    for t in report["topics"]:
        print(f"{t['class']:<14} {t['topic']:<38} {t['n']:>3} {t['changed']:>4} "
              f"{t['dropped_to_empty']:>6} {t['primary_changed']:>6} {t['lost_any']:>5} {t['gained_any']:>5}")

    print("\n=== BY CLASS ===")
    for cls, a in report["by_class"].items():
        denom = a["n"] or 1
        print(f"{cls:<14} n={a['n']:>4}  changed={a['changed']} ({100*a['changed']/denom:.0f}%)  "
              f"dropped_to_empty={a['dropped_to_empty']} ({100*a['dropped_to_empty']/denom:.0f}%)  "
              f"primary_changed={a['primary_changed']}  lost_any={a['lost_any']}  gained_any={a['gained_any']}")

    print("\n=== SAMPLE DIFFS ===")
    for t in report["topics"]:
        for ex in t["examples"]:
            print(f"[{t['topic']}] {ex['title']}")
            print(f"    old: {ex['old']}")
            print(f"    new: {ex['new']}")

    out = "/tmp/substrate_gate_ab_report.json"
    json.dump(report, open(out, "w"), indent=2)
    print(f"\nFull report -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
