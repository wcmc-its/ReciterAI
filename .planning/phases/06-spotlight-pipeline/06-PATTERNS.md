# Phase 6: Spotlight Pipeline & Publishing Contract — Pattern Map

**Mapped:** 2026-05-07
**Files analyzed:** 14 new files (1 CLI + 8 module files + 4 docs + 1 prompt) + 2 ops artifacts (bucket policy, IAM policy) + 1+ test file
**Analogs found:** 14 / 14 — every new file has an exact or strong-role analog already shipped in this repo (Phase 1 enrichment, Phase 5 publish, hierarchy contract docs, SPS handoff brief, lazy boto3 utils, generator prompt v0).

This phase is **assembly-and-publish, not greenfield**. ~90% of the code is glue against existing modules (`utils/s3_client.py`, `utils/bedrock_client.py`, `import_enrichment.py`'s scan/batch-write, `backfill_all.py:_run_publish()`). The genuinely new code is concentrated in three places: pool ranker math (24-month filter + impactScore aggregation), rotation decay formula, and hybrid-critic regex bundle.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `backfill_spotlight.py` | CLI controller | request-response (operator → pipeline) | `backfill_all.py` (especially `_parse_args` + `_run_publish`) | **exact** — same argparse shape, same `_run_*` dispatch, same dry-run/publish semantics |
| `spotlight/__init__.py` | package marker | — | `tests/__init__.py`, `utils/__init__.py` | exact (empty file) |
| `spotlight/pool_ranker.py` | service (read + aggregate) | batch transform (DynamoDB scan → in-memory aggregation) | `import_enrichment.py:scan_topic_records()` (lines 104-123) | **role-match** — same Scan + paginator + FilterExpression shape; new logic is the year-filter + sum |
| `spotlight/rotation_selector.py` | service (read + score + select) | CRUD-read + transform (DynamoDB BatchGetItem on SPOTLIGHT_HISTORY# → decay math → greedy pick) | `import_enrichment.py:scan_topic_records()` for read pattern; no exact analog for decay math | role-match (read pattern); greenfield (decay + diversity selection) |
| `spotlight/lede_generator.py` | service (LLM call) | request-response (Sonnet Converse) | `score_publications.py` Bedrock call sites (uses `BedrockClient.call()`); `utils/bedrock_client.py` is the SDK | **exact** — wrap `BedrockClient.call(model=SONNET_MODEL, ...)` |
| `spotlight/critic.py` | service (regex + LLM judge) | transform (text → verdict) + request-response (Haiku Converse) | `utils/bedrock_client.py:call_json()` for the LLM-judge slice; no analog for regex bundle | exact (LLM slice); greenfield (regex bundle) |
| `spotlight/sensitive_gate.py` | service (DynamoDB read + pattern match) | CRUD-read + transform | DynamoDB GetItem patterns from `import_enrichment.py`; substring match is greenfield | role-match (read); greenfield (match) |
| `spotlight/review_queue.py` | service (DynamoDB write + Query) | CRUD (UpdateItem, Query, BatchWriteItem) | `import_enrichment.py:_flush_batch()` (lines 182-194) for batch write; new partition is greenfield | role-match — reuse `batch_write_item` retry-on-unprocessed pattern verbatim |
| `spotlight/assembler.py` | service (dict assembly) | transform (in-memory) | `backfill_all.py:_run_assemble_only()` (lines 562+) | role-match — assembly logic from in-memory parts to a dict shape |
| `spotlight/publish.py` | service (validate + S3 PutObject loop) | batch publish + idempotency check | `backfill_all.py:_run_publish()` (lines 429-555) | **exact** — copy step-for-step; only deltas are bucket name + prefix + skip PM worktree copy |
| `prompts/spotlight_critic_v0.md` | prompt template | (static asset) | `prompts/spotlight_synopsis_v0.md` (already authored) | exact — same `<role>/<task>/<inputs>/<voice>/<constraints>` markdown shape |
| `docs/spotlight.schema.json` | config (JSON Schema 2020-12) | (static asset) | `docs/hierarchy.schema.json` | **exact** — same `$schema` + `$id` + `$defs._meta.schema_version` layout |
| `docs/spotlight-contract.md` | doc | (static asset) | `docs/hierarchy-contract.md` | **exact** — mirror sections character-for-character |
| `docs/sps-spotlight-handoff.md` | doc | (static asset) | `docs/sps-integration-handoff.md` | **exact** — mirror sections (Background, What's New Upstream, What You Need to Build, Pre-Adaptation Checklist, etc.) |
| `docs/sps-spotlight-etl-reference.ts` | reference TS | (static asset, runnable) | `docs/sps-etl-reference.ts` | **exact** — same env-var preamble, same `@aws-sdk/client-s3` + `ajv/dist/2020` stack |
| `tests/test_spotlight_*.py` | test | (test) | `test_aging_pilot_gate.py` (top-level pattern) | **exact** — top-level `test_*.py` files using pytest, lazy in-test imports |

## Pattern Assignments

### `backfill_spotlight.py` (CLI controller, request-response)

**Analog:** `backfill_all.py`

**Imports + module shape** (from `backfill_all.py:1-90`):
```python
import argparse
import logging
import sys
import time
from pathlib import Path
import json

logger = logging.getLogger(__name__)
```

**Argparse pattern** (`backfill_all.py:815-899`): mutually-exclusive flag groups in a single `argparse.ArgumentParser`, each flag with a long `help=` block describing operator intent + cost implications. Phase 6 mirrors:
```python
parser.add_argument("--dry-run", action="store_true", help="Print pool ranking + selected 10 + draft ledes. NO Bedrock LLM critic, NO publish.")
parser.add_argument("--dry-run-full", action="store_true", help="Full pipeline incl. Bedrock generation/critic; no publish; writes ./out/spotlight-{date}.json")
parser.add_argument("--publish", action="store_true", help="Validate spotlight.json + upload to s3://wcmc-reciterai-artifacts/spotlight/{v{date},latest}/")
parser.add_argument("--regen-only", metavar="SUBTOPIC_ID", help="Re-roll one lede; reads prior pool from latest/spotlight.json")
parser.add_argument("--review-queue", action="store_true", help="List pending review entries (defaults to most-recent publish_id)")
parser.add_argument("--publish-id", metavar="PUBLISH_ID", help="Constrain --review-queue to a specific publish run")
parser.add_argument("--approve", metavar="SUBTOPIC_ID", help="Mark a flagged review entry as approved")
parser.add_argument("--reject", metavar="SUBTOPIC_ID", help="Mark a flagged review entry as rejected")
parser.add_argument("--reset-history", action="store_true", help="Truncate SPOTLIGHT_HISTORY# (use after annual hierarchy recompute when subtopic IDs rotate per D-06)")
```

**Dispatch pattern** (`backfill_all.py:902-912`): `if __name__ == "__main__"` calls `sys.exit(run(...))`; the `run()` function dispatches to `_run_*` helpers. Phase 6 mirrors with `_run_pipeline(dry_run, dry_run_full, publish)`, `_run_review_queue(...)`, `_run_approve(...)`, `_run_reject(...)`, `_run_regen_only(...)`.

---

### `spotlight/pool_ranker.py` (service, batch transform)

**Analog:** `import_enrichment.py:scan_topic_records()` (lines 104-123)

**Scan + paginator pattern** (verbatim reuse — `import_enrichment.py:106-122`):
```python
records = []
params = {
    "TableName": TABLE_NAME,
    "FilterExpression": "begins_with(PK, :prefix)",
    "ExpressionAttributeValues": {":prefix": {"S": "TOPIC#"}},
}
page = 0
while True:
    resp = dynamo_client.scan(**params)
    items = resp.get("Items", [])
    records.extend(items)
    page += 1
    if "LastEvaluatedKey" not in resp:
        break
    params["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
```

**Attribute extraction pattern** (`import_enrichment.py:126-134`) — TOPIC# items use DynamoDB low-level format `{"S": "..."}`/`{"N": "..."}`:
```python
year = int(item.get("year", {}).get("N", "0"))
impact = float(item.get("impact_score", {}).get("N", "0"))
subtopic_id = item.get("primary_subtopic_id", {}).get("S")
```

**New logic (greenfield):** filter `year >= cutoff_year`, `defaultdict(lambda: {"score": 0.0, "papers": []})` aggregation, then sort with deterministic tiebreaker `key=lambda kv: (-score, subtopic_id)` (see RESEARCH.md §Pattern 1, lines 343-388). Return top 50 as `PoolEntry` dataclass.

**Lazy boto3 init:** mirror `utils/bedrock_client.py:_get_client()` — do NOT call `boto3.client("dynamodb")` at module top; receive the client as a function arg or use a module-level lazy singleton helper.

---

### `spotlight/rotation_selector.py` (service, CRUD-read + transform)

**Analog:** read pattern from `import_enrichment.py`; decay math is greenfield from RESEARCH.md §Pattern 2.

**BatchGetItem pattern (greenfield, but standard boto3 shape):**
```python
# Source: derived from CONTEXT.md decision; standard boto3 batch_get_item idiom
keys = [{"PK": {"S": f"SPOTLIGHT_HISTORY#{e.subtopic_id}"}, "SK": {"S": "STATE"}} for e in pool[:25]]
resp = dynamo_client.batch_get_item(RequestItems={TABLE_NAME: {"Keys": keys}})
# Loop a second time for the next 25 (50 total / 25 per batch = 2 calls)
```

**Decay formula (RESEARCH.md §Pattern 2):**
```python
import math
from datetime import datetime, timezone

DECAY_TAU_WEEKS = 12

def selection_score(pool_score: float, last_shown_at: str | None) -> float:
    """SPOT-03. Cold-start: last_shown_at is None → multiplier = 1.0."""
    if last_shown_at is None:
        return pool_score
    last = datetime.fromisoformat(last_shown_at.replace("Z", "+00:00"))
    weeks = (datetime.now(timezone.utc) - last).total_seconds() / (7 * 86400)
    multiplier = 1.0 - math.exp(-weeks / DECAY_TAU_WEEKS)
    return pool_score * multiplier
```

**Greedy parent-diversity selection** (RESEARCH.md §Pattern 2, lines 421-440): sort by `(-sel_score, subtopic_id)`, scan in order, skip when `parent_topic in parents` set, stop at `n=10`.

---

### `spotlight/lede_generator.py` (service, request-response)

**Analog:** `utils/bedrock_client.py` (use directly; no new client class).

**Imports** (verbatim):
```python
from utils.bedrock_client import BedrockClient, SONNET_MODEL
from pathlib import Path
```

**Call shape** (`utils/bedrock_client.py:97-130` — `BedrockClient.call()`):
```python
client = BedrockClient()  # lazy boto3 init
response_text = client.call(
    model=SONNET_MODEL,
    messages=[{"role": "user", "content": rendered_prompt}],
    system="You write editorial ledes for the Scholars @ WCM home page.",
    max_tokens=300,        # 35 words ≈ 50 tokens; 300 gives slack
    temperature=0.5,        # editorial voice variation; A/B test before lock per A1
)
```

**Prompt rendering pattern:** `prompts/spotlight_synopsis_v0.md` already exists and uses `{parent_topic}`, `{subtopic_name}`, `{papers}` template variables (see lines 16-23 of the prompt). Render via `str.format(...)` after extracting the markdown body from inside the ```` ```markdown ```` fence.

**Anti-pattern** (RESEARCH.md §Anti-Patterns): never pass `display_name` or `short_description` to the lede generator (D-19 LOCKED). Use `label` + `description` for subtopic identity; `synopsis` + `impactJustification` for paper grounding.

---

### `spotlight/critic.py` (service, transform + request-response)

**Analog:** `utils/bedrock_client.py:call_json()` (lines 132-190) for the LLM-judge slice.

**Deterministic regex bundle (greenfield, from prompt v0 constraints — RESEARCH.md §Pattern 3):**
```python
import re
from dataclasses import dataclass

EM_DASH_RE   = re.compile(r"[—–]")
TIME_BOUND_RE = re.compile(
    r"\b(this (quarter|year)|currently|right now|recently|of late|in recent (months|years|weeks))\b",
    re.IGNORECASE,
)
MARKETING_RE = re.compile(
    r"\b(cutting[- ]edge|world[- ]class|pioneering|revolutionary|groundbreaking|leading|innovative)\b",
    re.IGNORECASE,
)
DEAD_WORDS_RE = re.compile(r"\b(important|complex|vital|novel)\b", re.IGNORECASE)
TIC_RE       = re.compile(r"\bWCM scholars are [a-zA-Z]+ing\b")  # case-sensitive
```

**LLM-judge slice — call_json pattern** (`utils/bedrock_client.py:132-190`):
```python
from utils.bedrock_client import BedrockClient, HAIKU_MODEL

client = BedrockClient()
verdict = client.call_json(
    model=HAIKU_MODEL,
    messages=[{"role": "user", "content": CRITIC_PROMPT.format(lede=lede, ...)}],
    system="You evaluate editorial ledes against voice constraints.",
    max_tokens=200,
    temperature=0.0,  # deterministic verdict
)
# call_json strips markdown fences automatically and retries once on JSONDecodeError
```

**Generate-and-critic loop** (RESEARCH.md §Pattern 4, lines 471-496): max 3 retries; on persistent failure, write SPOTLIGHT_REVIEW# row with `regen_count=3, status="pending"` and return `ValidatedLede(status="needs_review")`.

**Pitfall guards:**
- Use `re.compile(r"[—–]")` (covers BOTH em-dash U+2014 and en-dash U+2013). `if "—" in lede` is insufficient.
- Length bounds 22-38 (lenient regen room) even though spec is 25-35; matches RESEARCH.md §Pattern 3.

---

### `spotlight/sensitive_gate.py` (service, CRUD-read + transform)

**Analog:** read pattern from `import_enrichment.py`; substring match is greenfield from RESEARCH.md §DynamoDB Schema Design §SPOTLIGHT_CONFIG#.

**GetItem pattern (greenfield, standard boto3):**
```python
resp = dynamo_client.get_item(
    TableName=TABLE_NAME,
    Key={"PK": {"S": "SPOTLIGHT_CONFIG#sensitive_tags"}, "SK": {"S": "CONFIG"}},
)
tags = resp.get("Item", {}).get("tags", {}).get("L", [])
```

**Match logic (RESEARCH.md §SPOTLIGHT_CONFIG, recommended substring strategy):**
```python
def is_sensitive(subtopic, parent_topic, tags) -> tuple[bool, str | None]:
    """Match against label + description + parent_topic.label per Open Question §4."""
    haystack = (subtopic.label + " " + subtopic.description + " " + parent_topic.label).lower()
    for tag_entry in tags:
        pattern = tag_entry["M"]["pattern"]["S"].lower()
        if pattern in haystack:
            return True, pattern
    return False, None
```

**Anti-pattern** (RESEARCH.md §Anti-Patterns): tag list lives in DynamoDB, NOT in the repo. Tests use a fixture written to a stand-in DynamoDB record (or a mocked GetItem response).

---

### `spotlight/review_queue.py` (service, CRUD)

**Analog:** `import_enrichment.py:_flush_batch()` (lines 182-194)

**Batch write with retry-on-unprocessed (verbatim reuse pattern):**
```python
def _flush_batch(dynamo_client, batch):
    request = {TABLE_NAME: batch}
    resp = dynamo_client.batch_write_item(RequestItems=request)
    unprocessed = resp.get("UnprocessedItems", {}).get(TABLE_NAME, [])
    retries = 0
    while unprocessed and retries < 5:
        import time
        time.sleep(2 ** retries * 0.5)
        resp = dynamo_client.batch_write_item(RequestItems={TABLE_NAME: unprocessed})
        unprocessed = resp.get("UnprocessedItems", {}).get(TABLE_NAME, [])
        retries += 1
    if unprocessed:
        print(f"WARNING: {len(unprocessed)} items still unprocessed after retries")
```

**Query pattern for `--review-queue`** (greenfield but standard):
```python
resp = dynamo_client.query(
    TableName=TABLE_NAME,
    KeyConditionExpression="PK = :pk",
    FilterExpression="#s = :pending",
    ExpressionAttributeNames={"#s": "status"},
    ExpressionAttributeValues={
        ":pk": {"S": f"SPOTLIGHT_REVIEW#{publish_id}"},
        ":pending": {"S": "pending"},
    },
)
```

**UpdateItem pattern for `--approve`/`--reject`** (greenfield but standard):
```python
dynamo_client.update_item(
    TableName=TABLE_NAME,
    Key={"PK": {"S": f"SPOTLIGHT_REVIEW#{publish_id}"}, "SK": {"S": f"SUBTOPIC#{subtopic_id}"}},
    UpdateExpression="SET #s = :s, reviewer = :r, reviewed_at = :t",
    ExpressionAttributeNames={"#s": "status"},
    ExpressionAttributeValues={
        ":s": {"S": "approved"},
        ":r": {"S": "cli"},
        ":t": {"S": now_iso_z()},
    },
)
```

**`SPOTLIGHT_HISTORY#` post-publish update (`UpdateItem` with `ADD` for the counter):**
```python
dynamo_client.update_item(
    TableName=TABLE_NAME,
    Key={"PK": {"S": f"SPOTLIGHT_HISTORY#{subtopic_id}"}, "SK": {"S": "STATE"}},
    UpdateExpression="ADD shown_count :one SET last_shown_at = :now, last_shown_publish_id = :pid",
    ExpressionAttributeValues={
        ":one": {"N": "1"},
        ":now": {"S": now_iso_z()},
        ":pid": {"S": publish_id},
    },
)
```

**Pitfall guard:** never interpolate user values into `UpdateExpression` strings. Use `ExpressionAttributeValues`. (CLAUDE.md project rule.)

---

### `spotlight/assembler.py` (service, transform)

**Analog:** `backfill_all.py:_run_assemble_only()` (lines 562+) — purely-in-memory dict assembly.

**Spotlight artifact shape** (RESEARCH.md §Pool snapshot in artifact body, lines 800-842):
```python
def build_artifact(selected: list[ValidatedLede], pool: list[PoolEntry], selected_ids: set[str]) -> dict:
    return {
        "version": "spotlight_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "taxonomy_version": "taxonomy_v2",
        "spotlights": [
            {
                "subtopic_id": s.subtopic_id,
                "label": s.subtopic.label,
                "display_name": s.subtopic.display_name,        # UI ok in artifact
                "short_description": s.subtopic.short_description,
                "parent_topic": s.parent_topic,
                "lede": s.lede,
                "papers": [{
                    "pmid": p.pmid, "title": p.title, "journal": p.journal, "year": p.year,
                    "first_author": {"personIdentifier": p.first_author.person_id,
                                      "displayName": p.first_author.display_name, "position": "first"},
                    "last_author":  {"personIdentifier": p.last_author.person_id,
                                      "displayName": p.last_author.display_name, "position": "last"},
                } for p in s.papers],
            } for s in selected
        ],
        "pool_snapshot": [
            {"subtopic_id": e.subtopic_id, "pool_score": e.pool_score,
             "parent_topic": e.parent_topic, "was_selected": e.subtopic_id in selected_ids}
            for e in pool
        ],
    }
```

**Anti-pattern guard:** the assembler uses `personIdentifier` field name (NEVER `cwid` or `cwid_*`). The literal string `"cwid_"` is isolated to `WCM_FACULTY_UID_PREFIX` and `load_dynamodb.py` only — see CLAUDE.md naming rule. `personIdentifier` flows through from the TOPIC# SK extraction (strip `cwid_` prefix per `extract_person_identifier()` in `import_enrichment.py:126-130`).

---

### `spotlight/publish.py` (service, batch publish + idempotency check)

**Analog:** `backfill_all.py:_run_publish()` (lines 429-555) — copy step-for-step.

**Imports** (verbatim from analog, lines 439-443):
```python
from utils.s3_client import S3HierarchyClient
from jsonschema import Draft202012Validator
from botocore.exceptions import NoCredentialsError
import hashlib
from datetime import date, datetime, timezone
```

**Validation pattern** (`backfill_all.py:462-472`):
```python
validator = Draft202012Validator(schema)
errors = sorted(validator.iter_errors(spotlight), key=lambda e: list(e.absolute_path))
if errors:
    print("\nSCHEMA VALIDATION FAILED — spotlight.json does not match schema:\n")
    for e in errors:
        path = " > ".join(str(p) for p in e.absolute_path) or "<root>"
        print(f"  [{path}] {e.message}")
    return 1
logger.info("Schema validation: PASS")
```

**Manifest construction — LOCKED field order** (`backfill_all.py:474-490`, with Phase 6 additions per RESEARCH.md §Pattern 6):
```python
spotlight_bytes = json.dumps(spotlight, indent=2, ensure_ascii=False).encode("utf-8")
schema_bytes    = json.dumps(schema, indent=2, ensure_ascii=False).encode("utf-8")
sha256          = hashlib.sha256(spotlight_bytes).hexdigest()
version         = f"v{date.today().isoformat()}"
schema_version  = schema.get("$defs", {}).get("_meta", {}).get("schema_version", "1.0.0")

# Manifest field order is LOCKED — do NOT sort_keys.
manifest = {
    "schema_version":   schema_version,
    "spotlight_version": "spotlight_v1",          # NEW for Phase 6
    "taxonomy_version": spotlight.get("taxonomy_version", "unknown"),
    "version":          version,
    "generated_at":     datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "sha256":           sha256,
    "artifact_bytes":   len(spotlight_bytes),
}
manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
```

**S3 upload pattern with idempotency check** (`backfill_all.py:506-524`, with Phase 6 prefix):
```python
ARTIFACTS_BUCKET = "wcmc-reciterai-artifacts"   # NEW for Phase 6
PREFIX = "spotlight"                             # NEW — matches s3://.../spotlight/v{date}/

try:
    s3 = S3HierarchyClient(bucket=ARTIFACTS_BUCKET)
    if s3.key_exists(f"{PREFIX}/{version}/manifest.json"):
        logger.warning(
            f"{PREFIX}/{version}/ already exists in s3://{ARTIFACTS_BUCKET} "
            f"— overwriting (same-day re-publish)."
        )
    s3.put_object(f"{PREFIX}/{version}/spotlight.json",        spotlight_bytes)
    s3.put_object(f"{PREFIX}/{version}/spotlight.schema.json", schema_bytes)
    s3.put_object(f"{PREFIX}/{version}/manifest.json",         manifest_bytes)
    s3.put_object(f"{PREFIX}/latest/spotlight.json",           spotlight_bytes)
    s3.put_object(f"{PREFIX}/latest/spotlight.schema.json",    schema_bytes)
    s3.put_object(f"{PREFIX}/latest/manifest.json",            manifest_bytes)
except NoCredentialsError:
    print("\nABORT: AWS credentials not found. ...")
    return 1
```

**Phase 6 deltas from Phase 5 publish (do NOT carry over):**
- **Skip** the PM worktree copy (`_copy_to_pm()` + `_print_pm_commit_instructions()` — `backfill_all.py:534-541`). Spotlight artifact is SPS-only; PM does not consume it.
- **After** the 6 PutObjects: call `review_queue.update_history(selections, publish_id)` to upsert each `SPOTLIGHT_HISTORY#{subtopic_id}` row.
- **Bucket check** before any PutObject: hard-fail with operator-friendly message if `wcmc-reciterai-artifacts` doesn't exist (Plan 06-01 prerequisite gate).

---

### `prompts/spotlight_critic_v0.md` (prompt template, static asset)

**Analog:** `prompts/spotlight_synopsis_v0.md` (already exists; same structure)

**File shape** — markdown header + ```` ```markdown ```` fenced prompt body with `<role>/<task>/<inputs>/<voice>/<constraints>` XML-style sections. The first ~10 lines are operator-facing notes (date authored, what changed since prior version, model + temperature recommendation); the fenced body is the literal prompt text rendered into the `BedrockClient.call_json()` user message.

Existing example to mirror (`prompts/spotlight_synopsis_v0.md` lines 1-10):
```markdown
# Spotlight Synopsis Lede — v0

Voice-constrained Bedrock Sonnet prompt that authors a 25-35 word editorial lede ...

Provided by user 2026-05-07. ...

---

```markdown
<role> ... </role>
<task> ... </task>
...
```

---

### `docs/spotlight.schema.json` (JSON Schema 2020-12, static asset)

**Analog:** `docs/hierarchy.schema.json`

**Header pattern** (`docs/hierarchy.schema.json:1-9` verbatim shape):
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://wcmc-reciterai-artifacts.s3.amazonaws.com/spotlight/latest/spotlight.schema.json",
  "title": "ReCiter AI Spotlight Artifact",
  "description": "JSON Schema Draft 2020-12 for the spotlight.json artifact ...",
  "$defs": {
    "_meta": {
      "schema_version": "1.0.0"
    },
    ...
  }
}
```

**Pattern guards** (Phase 5 D-11 carries over — see `docs/hierarchy-contract.md:35`):
- Do NOT set `additionalProperties: false` at any level. Additive fields are non-breaking.
- `required` array names ALL fields the consumer must rely on. New optional metadata fields are added without bumping MAJOR.
- `$id` URL must reflect the new bucket name `wcmc-reciterai-artifacts` and the `spotlight/` prefix.

**Top-level required fields** (mirror RESEARCH.md §assembler shape):
```json
"required": ["version", "generated_at", "taxonomy_version", "spotlights", "pool_snapshot"]
```

---

### `docs/spotlight-contract.md` (consumer contract doc, static asset)

**Analog:** `docs/hierarchy-contract.md`

**Section structure to mirror character-for-character** (verified `grep ^## docs/hierarchy-contract.md`):
```
## URL Pattern
## Schema
## Manifest
## Cadence
## Breaking-Change Policy
## Integration Pattern
## ID Stability
## Changelog
## FAQ
```

**Phase 6 deltas:**
- URL pattern table uses `s3://wcmc-reciterai-artifacts/spotlight/v{ISO-date}/{spotlight,spotlight.schema,manifest}.json` (and `latest/`).
- Manifest table adds `spotlight_version` row between `schema_version` and `taxonomy_version`.
- Cadence section says **weekly** (operator-run for v1) instead of annual + ad-hoc.
- Add a new section **§Subtopic ID Stability** describing the operator action required after annual hierarchy recompute (rotate or `--reset-history`; RESEARCH.md §Pitfall 2).
- Add a new section **§Sensitive Topic Routing** documenting that flagged spotlights live in DynamoDB review queue, not in the artifact (transparency for SPS / future PM dashboard).

---

### `docs/sps-spotlight-handoff.md` (SPS handoff brief, static asset)

**Analog:** `docs/sps-integration-handoff.md`

**Section structure to mirror** (verified `grep ^## docs/sps-integration-handoff.md`):
```
## Background
## What's New Upstream
## What You Need to Build
## Pre-Adaptation Checklist
## D-19 Rule (LOCKED) — UI vs Synthesis Field Split
## Schema-Change Coordination
## Reference Script Caveats
## Out of Scope
## Cross-References
```

**Phase 6 deltas:**
- §Background: bucket migration `wcmc-reciterai-hierarchy → wcmc-reciterai-artifacts` (cross-link to Plan 06-01).
- §What You Need to Build: enumerate the SPS-side render work (interactive 2-column spotlight component on the home page, replacing or augmenting Recent Contributions per the Downloads mockup `home-spotlight-interactive.html`). Make explicit that SPS resolves `personIdentifier` → headshot via its existing photo store (no image URLs in the artifact).
- §D-19 Rule carries over verbatim — `display_name` and `short_description` may be RENDERED but were never passed to the lede LLM.
- §Reference Script Caveats: cross-link `docs/sps-spotlight-etl-reference.ts`.

---

### `docs/sps-spotlight-etl-reference.ts` (TypeScript reference, runnable as shipped)

**Analog:** `docs/sps-etl-reference.ts`

**Header preamble pattern** (`docs/sps-etl-reference.ts:1-25` — verbatim adapt):
```typescript
/**
 * sps-spotlight-etl-reference.ts — Reference fetch script for SPS spotlight ETL
 *
 * Fetches spotlight.json from S3, validates against the co-published JSON Schema,
 * and projects the 10 active spotlights into [SPS-side store choice]. SPS coding
 * agent: copy this file to etl/spotlight/index.ts, swap in your store client,
 * and adapt the upsert block. Everything else (S3 fetch, manifest sha256
 * comparison, schema validation) ships ready.
 *
 * Required env vars:
 *   AWS_ACCESS_KEY_ID
 *   AWS_SECRET_ACCESS_KEY
 *   AWS_DEFAULT_REGION       (default: us-east-1)
 *   ARTIFACTS_BUCKET         (default: wcmc-reciterai-artifacts)
 *   ARTIFACT_PREFIX          (default: spotlight)
 */
```

**Stack** (verbatim reuse from analog):
- `@aws-sdk/client-s3` for `GetObjectCommand`
- `ajv ^8.x` via `ajv/dist/2020` for Draft 2020-12 validation
- Manifest sha256 comparison loop for change detection (poll `latest/manifest.json` weekly per contract)

---

### `tests/test_spotlight_*.py` (test, pytest)

**Analog:** `test_aging_pilot_gate.py` (top-level, pytest, lazy in-test imports)

**Pattern** (`test_aging_pilot_gate.py:1-32`):
```python
"""
Tests for spotlight/<module>.py — Plan 06-NN.

Covers the behaviors specified in the PLAN.md <behavior> block.
All tests use synthetic inputs: no AWS calls, no DynamoDB, no Bedrock.
"""

import pytest


def test_<behavior>_<case>():
    """<docstring describing pre-condition → expected output>"""
    from spotlight.<module> import <function>

    # arrange
    items = [...]

    # act
    result = <function>(items)

    # assert
    assert ...
```

**Test file naming:** top-level `test_spotlight_<module>.py` (matches existing `test_aging_pilot_gate.py`, `test_eval_golden_queries.py`). Do NOT put under `tests/` (which currently only holds fixtures).

**Fixture pattern** for invalid-spotlight schema rejection (mirror `tests/fixtures/hierarchy_invalid.json`):
- `tests/fixtures/spotlight_invalid.json` — minimal violations (missing `spotlights`, wrong type for `pool_snapshot`)
- Test asserts `Draft202012Validator.iter_errors()` returns `>0` errors and the publish path returns exit code 1.

**No-AWS test posture:** mock `boto3.client(...)` calls or pass injected client mocks. The lazy-init pattern in `S3HierarchyClient` and `BedrockClient` makes this clean — instantiate the client, then patch its `_client` attribute with a `Mock()` before any method call.

---

## Shared Patterns

### Lazy boto3 client initialization
**Source:** `utils/s3_client.py:69-77` and `utils/bedrock_client.py:82-95`
**Apply to:** `pool_ranker.py`, `rotation_selector.py`, `sensitive_gate.py`, `review_queue.py`, `publish.py` — every module that touches AWS.
```python
class FooClient:
    def __init__(self, region: str = "us-east-1") -> None:
        self.region = region
        self._client = None  # Lazy init

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client("dynamodb", region_name=self.region)
        return self._client
```
**Why:** No AWS calls at import time. Imports succeed in environments without AWS credentials. Tests patch `_client` directly. Matches CLAUDE.md "module patterns" rule.

### AWS credentials posture
**Source:** `utils/s3_client.py:19-23, 41-46` and CLAUDE.md "Security" section
**Apply to:** every module that calls boto3.
- Never read `~/.zshrc`. Never log credential values, even partially.
- Never pass `aws_access_key_id=` or `aws_secret_access_key=` to `boto3.client()`. Use the default credential chain.
- Never pass `api_key=` to OpenAI / Bedrock factories. (Same posture for Bedrock — `bedrock-runtime` boto3 client uses default creds.)
- On `NoCredentialsError`, emit operator-friendly hint pointing to `~/.zshrc`:
```python
except NoCredentialsError:
    print(
        "\nABORT: AWS credentials not found. Ensure AWS_ACCESS_KEY_ID and "
        "AWS_SECRET_ACCESS_KEY are exported in your shell (from ~/.zshrc). "
        "Run: source ~/.zshrc && python backfill_spotlight.py --publish"
    )
    return 1
```

### JSON Schema validation (Draft 2020-12)
**Source:** `backfill_all.py:_run_publish()` lines 462-472 — `Draft202012Validator.iter_errors()`
**Apply to:** `publish.py` (validates `spotlight.json`); test fixtures verify rejection on invalid input.
- Library is `jsonschema>=4.23.0`, already pinned. No new install.
- Sort errors by `absolute_path` for deterministic operator output.
- Fail-fast: if any error, return exit 1 BEFORE any S3 PutObject. (Phase 5 D-16 rule — no warn-and-publish.)

### sha256 over in-memory bytes
**Source:** `backfill_all.py:_run_publish()` line 477
**Apply to:** `publish.py`
```python
sha256 = hashlib.sha256(spotlight_bytes).hexdigest()
```
- Compute on bytes after `json.dumps(..., indent=2, ensure_ascii=False).encode("utf-8")`, BEFORE upload.
- Goes into `manifest.sha256` field; consumer change-detection signal.

### ISO 8601 UTC timestamps with `Z` suffix
**Source:** `backfill_all.py:_run_publish()` line 486
**Apply to:** every place that emits a timestamp (manifest, SPOTLIGHT_HISTORY#last_shown_at, SPOTLIGHT_REVIEW#created_at/reviewed_at)
```python
datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
```
**Why:** SPS expects exactly this format (per RESEARCH.md §Don't Hand-Roll). Drop microseconds; replace `+00:00` with `Z`.

### Manifest field order (LOCKED, no `sort_keys`)
**Source:** `backfill_all.py:_run_publish()` lines 481-490 with comment "Manifest field order is LOCKED ... do NOT sort_keys"
**Apply to:** `publish.py`
- Use Python dict literal (insertion order preserved on 3.7+).
- `json.dumps(manifest, indent=2, ensure_ascii=False)` — never pass `sort_keys=True`.
- Test asserts `list(manifest.keys()) == ["schema_version", "spotlight_version", "taxonomy_version", "version", "generated_at", "sha256", "artifact_bytes"]`.

### `personIdentifier` naming + cwid_ isolation
**Source:** CLAUDE.md "Conventions §Naming"; `import_enrichment.py:126-130` (`extract_person_identifier`)
**Apply to:** `assembler.py`, `lede_generator.py` (paper author payload)
- Field name in artifact and in code is `personIdentifier`. Never `cwid`.
- The literal `"cwid_"` exists ONLY in `WCM_FACULTY_UID_PREFIX` (PM TS) and `load_dynamodb.py` (Phase 1).
- To extract: `faculty_uid.startswith("cwid_")` then `faculty_uid[len("cwid_"):]` — see `import_enrichment.py:126-130`.

### Bedrock model ID constants
**Source:** `utils/bedrock_client.py:39-40`
**Apply to:** `lede_generator.py`, `critic.py`
- Import `from utils.bedrock_client import HAIKU_MODEL, SONNET_MODEL`. NEVER type the model-ID string literal in Phase 6 code (RESEARCH.md §Pitfall 5 — silent ResourceNotFoundException on typo).
- Pinned: `HAIKU_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"`, `SONNET_MODEL = "us.anthropic.claude-sonnet-4-6"`.

### DynamoDB low-level vs Resource API
**Source:** `import_enrichment.py:210` (`boto3.client("dynamodb", region_name=REGION)`)
**Apply to:** all DynamoDB-touching modules in this phase.
- Use the **low-level client** (matches `import_enrichment.py`), NOT the resource API. Items are `{"PK": {"S": "..."}, "shown_count": {"N": "1"}}` form.
- Trade-off: more verbose at call site, but matches the existing `_flush_batch`/scan pattern verbatim and avoids two competing styles in the same repo.
- Same `TABLE_NAME = "reciterai-chatbot"` constant pattern.

### Idempotency on re-publish
**Source:** `backfill_all.py:_run_publish()` lines 510-514
**Apply to:** `publish.py`
- Before the 6 PutObjects, call `s3.key_exists(f"{prefix}/{version}/manifest.json")`.
- If True, log a WARN ("overwriting same-day re-publish"). Proceed anyway — same-day re-publish is supported per Phase 5 D-12.

### Argparse with structured `_run_*` dispatch
**Source:** `backfill_all.py:_parse_args()` (lines 815-899) and `if __name__ == "__main__"` block (lines 902-912)
**Apply to:** `backfill_spotlight.py`
- Each operator-facing flag has a long `help=` describing intent + cost ($).
- `run()` orchestrator function dispatches to `_run_pipeline()`, `_run_review_queue()`, `_run_approve()`, etc.
- `sys.exit(run(...))` at the bottom — every code path returns an int exit code.

## No Analog Found

Files with no exact analog in the codebase. Planner falls back to RESEARCH.md patterns (which already provide concrete code) for these:

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none — all 14 new files have at least a role-match analog above) | | | The phase is glue against existing patterns. |

The few sub-routines that lack a perfect analog are surfaced inside the assignments above (decay-formula math, greedy diversity loop, banned-word regex bundle). Each of those has a concrete drafted code excerpt in RESEARCH.md §Architecture Patterns that the planner copies into the relevant action.

## Ops artifacts (Plan 06-01 — bucket migration)

Plan 06-01 creates the new bucket and migrates the existing hierarchy artifact. Reuse Phase 5 IAM templates verbatim:

### `docs/aws-bucket-policy-artifacts.json` (new) — bucket policy for `wcmc-reciterai-artifacts`
**Analog:** `docs/aws-bucket-policy.json` (verbatim shape). Replace `wcmc-reciterai-hierarchy` ARN with `wcmc-reciterai-artifacts`. Same `SPSLambdaRead` Sid + `s3:GetObject` + `s3:ListBucket` actions.

### `docs/aws-iam-pipeline-policy-artifacts.json` (new) — pipeline writer policy
**Analog:** `docs/aws-iam-pipeline-policy.json`. Replace bucket name. Same `s3:PutObject` + `s3:GetObject` + `s3:HeadObject` actions for the operator's role.

### Migration runbook (Plan 06-01 prose)
**Analog:** Phase 5 D-09 30-day deprecation window pattern (referenced in `docs/hierarchy-contract.md` §Breaking-Change Policy).
**Steps:**
1. `aws s3 mb s3://wcmc-reciterai-artifacts --region us-east-1`
2. `aws s3 sync s3://wcmc-reciterai-hierarchy/ s3://wcmc-reciterai-artifacts/hierarchy/`
3. Re-publish `hierarchy.schema.json` with updated `$id` (PATCH bump per Phase 5 semver — RESEARCH.md §Bucket-migration risk surface). Run `backfill_all.py --publish` after editing `HIERARCHY_BUCKET` to the new name; commit + push.
4. Hand-off to SPS: cross-repo brief asks SPS to flip `BUCKET` env var.
5. Wait for SPS confirmation. Keep old bucket alive 30 days post-cutover (Phase 5 D-09).
6. Retire `wcmc-reciterai-hierarchy` after 30-day window.

## Metadata

**Analog search scope:** `utils/`, `prompts/`, `docs/`, `tests/`, repo root (`backfill_*.py`, `import_enrichment.py`, `score_publications.py`, all `test_*.py`).
**Files scanned:** ~30 (full repo top level, all utils, all docs, prompts, tests).
**Pattern extraction date:** 2026-05-07
**Pattern extraction confidence:** HIGH — every reused pattern is a direct lift from a single specific file/line range; no speculative analogs.
