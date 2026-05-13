# Spotlight DynamoDB Schema (Phase 6)

## Overview

Phase 6 introduces three new partitions on the existing `reciterai`
DynamoDB table. All three use the same low-level `boto3.client("dynamodb")`
shape established in Phase 1 (no DocumentClient wrapper, raw AttributeValue
maps). Storage cost is negligible: roughly 10K rows max combined across the
three partitions over the lifetime of the system. No GSIs are required for
v1; forward-compat GSIs proposed for the v2 Publication Manager dashboard
are documented but **NOT** created in v1.

Cross-references:
- Plan 06-03 reads `SPOTLIGHT_HISTORY#` at rotation-selector time.
- Plan 06-04 reads `SPOTLIGHT_CONFIG#sensitive_tags` (sensitive gate) and
  writes `SPOTLIGHT_REVIEW#` rows on critic / sensitive-tag flag.
- Plan 06-06 (publish) writes `SPOTLIGHT_HISTORY#` after a successful publish
  for each of the 10 selected subtopics.

Reference: `.planning/phases/06-spotlight-pipeline/06-RESEARCH.md`
§"DynamoDB Schema Design (proposed)" (lines 586-675) is the authoritative
source; this doc is a stable distillation suitable for code-time reference.

---

## Partition: `SPOTLIGHT_HISTORY#{subtopic_id}` -- rotation state

One row per subtopic. Tracks what was last shown when, so the rotation
selector in Plan 06-03 can apply the recency-decay multiplier.

| Attribute | Type | Required | Notes |
|-----------|------|----------|-------|
| `PK` | S | yes | `SPOTLIGHT_HISTORY#{subtopic_id}` |
| `SK` | S | yes | `STATE` (single-row partition; one row per subtopic) |
| `subtopic_id` | S | yes | duplicate of partition for query clarity |
| `last_shown_at` | S | yes (after first publish) | ISO 8601 UTC; absent on cold-start |
| `shown_count` | N | yes | integer; starts at 0 (initial UpdateItem with `ADD shown_count :one`) |
| `last_shown_publish_id` | S | optional | `v{ISO-date}` value of the publish that most recently included this subtopic |
| `last_shown_lede` | S | optional | the actual lede text for analytics / dedup-against-recent-output |

### Access patterns

- **Read** at rotation-selector time (Plan 06-03):
  `BatchGetItem` for the top-50 candidate subtopic IDs (2 BatchGet calls of
  25 keys each, since BatchGetItem caps at 100 items per request and 25 is
  a safe per-call default).
- **Write** after publish (Plan 06-06): one `UpdateItem` per selected
  subtopic with the expression
  `ADD shown_count :one SET last_shown_at = :now, last_shown_publish_id = :pid, last_shown_lede = :lede`.

```python
# boto3 BatchGetItem read shape (Plan 06-03)
client.batch_get_item(
    RequestItems={
        "reciterai": {
            "Keys": [
                {"PK": {"S": f"SPOTLIGHT_HISTORY#{sid}"}, "SK": {"S": "STATE"}}
                for sid in top_50_ids[:25]
            ]
        }
    }
)
```

### Cold-start

A missing item means the subtopic has never been spotlighted. The rotation
selector treats `last_shown_at = None` as multiplier `1.0` in the
selection-score formula. There is **no init step**; do **NOT** pre-seed
the partition with placeholder rows. Cold-start is a feature, not a bug
(see Pitfall 6 in 06-RESEARCH.md).

### Subtopic-ID stability (operator action required)

Per RESEARCH.md Pitfall 2 (lines 856-864) and CONTEXT decision D-06: Phase 4
re-runs Pass 1/2/3 subtopic discovery annually. **Subtopic IDs are NOT
stable across recomputes** -- hierarchy regeneration is wholesale and ID
assignment is data-driven. After an annual recompute:

- `SPOTLIGHT_HISTORY#` rows for retired subtopic IDs become orphaned (no
  new updates write to them; they age out silently).
- Newly-introduced subtopic IDs have no history row, so cold-start kicks in
  and they are eligible for an early spotlight.

The operator has two options on each recompute:
1. **Silent age-out** (default): do nothing. Orphaned rows stay in DynamoDB
   indefinitely (storage is negligible) and the new IDs cold-start naturally.
2. **`--reset-history`** (planned for Plan 06-03): truncate the entire
   `SPOTLIGHT_HISTORY#` partition. Use only on a wholesale ID rotation
   (annual recompute), not on incremental edits.

**Warning sign:** the first publish after a hierarchy recompute spotlights
ALL never-shown subtopics; the rotation curve looks like a step function.
This is expected.

### v2 forward-compat (NOT created in v1)

For the deferred PM operator dashboard ("show me what was spotlighted in
the last N weeks / what's been quiet for >12 weeks"), a GSI keyed on
`last_shown_at` is recommended:

| GSI Name | HASH | RANGE | Projection | Purpose |
|----------|------|-------|------------|---------|
| `HistoryTimeline` | `"SPOTLIGHT_HISTORY"` (constant) | `last_shown_at` | KEYS_ONLY or ALL | Dashboard timeline view |

Add the constant string `"SPOTLIGHT_HISTORY"` to a new attribute on each
write so the GSI partition key has a value. Not created in v1.

---

## Partition: `SPOTLIGHT_REVIEW#{publish_id}` + SK=`SUBTOPIC#{subtopic_id}` -- review queue

Composite SK so a single `Query` lists all flagged entries for a publish
run. Holds rows for ledes that failed the deterministic+critic pipeline
(`flag_reason = critic`), tripped the sensitive-topic gate
(`flag_reason = sensitive_tag`), or both.

| Attribute | Type | Required | Notes |
|-----------|------|----------|-------|
| `PK` | S | yes | `SPOTLIGHT_REVIEW#{publish_id}` (e.g. `SPOTLIGHT_REVIEW#v2026-05-14`) |
| `SK` | S | yes | `SUBTOPIC#{subtopic_id}` |
| `publish_id` | S | yes | duplicate for clarity |
| `subtopic_id` | S | yes | duplicate for clarity |
| `parent_topic` | S | yes | rendering convenience |
| `lede_text` | S | yes | the candidate (final attempt's) lede |
| `flag_reason` | S | yes | one of `critic`, `sensitive_tag`, `both` |
| `critic_verdict` | M (Map) | conditional | structured verdict from final critic call: `{deterministic_failed: [...], llm_verdict: ..., llm_reason: ...}` |
| `sensitive_tag_matched` | S | conditional | the matching tag pattern, when `flag_reason` is `sensitive_tag` or `both` |
| `papers_used` | L (List of S) | yes | PMIDs used as grounding |
| `regen_count` | N | yes | integer 0-3 |
| `attempts` | L (List of M) | optional | full attempt log for v2 dashboard |
| `status` | S | yes | one of `pending`, `approved`, `rejected` (default: `pending`) |
| `reviewer` | S | optional | filled by `--approve`/`--reject`; could be operator login or `cli` |
| `reviewed_at` | S | optional | ISO 8601 UTC |
| `created_at` | S | yes | ISO 8601 UTC |

### Access patterns

- **`--review-queue [--publish-id <id>]`** (Plan 06-04 + a CLI surface):
  `Query` with `KeyConditionExpression = PK = SPOTLIGHT_REVIEW#{publish_id}`,
  `FilterExpression = status = pending`. Without `--publish-id`, default to
  the most recent publish_id (read from
  `s3://wcmc-reciterai-artifacts/spotlight/latest/manifest.json` then
  construct the PK).
- **`--approve <subtopic_id>`** / **`--reject <subtopic_id>`**: requires a
  publish_id context; `UpdateItem` with
  `SET status = 'approved'|'rejected', reviewer = ..., reviewed_at = ...`.
- **Subsequent `--publish`** reads only `status = approved` rows when
  joining the review queue with the assembler step.

### Status state machine

```
created (status=pending)
        |
        +-- --approve --> approved (terminal; included in next --publish)
        |
        +-- --reject  --> rejected (terminal; excluded from next --publish)
```

`pending -> approved` and `pending -> rejected` are the **only** transitions.
Re-publish reads only `approved` entries. There is no `approved -> rejected`
transition; if the reviewer changes their mind they must rerun the pipeline,
not mutate the row.

### Security note (T-06-01-06)

The review queue holds candidate lede text that may be PII-adjacent if a
sensitive-topic match misfired (e.g. a lede that mentions a faculty member
adjacent to a sensitive subtopic). Per the root `CLAUDE.md` security rules,
review records stay in DynamoDB and are **NOT** serialized into git
artifacts. The earlier (deprecated) "manual review queue file" pattern from
the discuss-phase has been replaced by DynamoDB writes per CONTEXT decision
Q4.3.

### v2 forward-compat (NOT created in v1)

The PM dashboard surface (deferred to v2) needs cross-publish-run views
("show me everything pending across publishes" or "rejected reasons over
the last 6 weeks"). Recommended GSI:

| GSI Name | HASH | RANGE | Projection | Purpose |
|----------|------|-------|------------|---------|
| `ReviewByStatus` | `status` | `created_at` | ALL | Cross-publish review dashboard |

The schema is forward-compat by design: `attempts`, `critic_verdict`, and
`sensitive_tag_matched` are all optional/nullable, so the v1 CLI can write
minimal records and the v2 dashboard can write richer records without a
schema migration.

---

## Partition: `SPOTLIGHT_CONFIG#sensitive_tags` + SK=`CONFIG` -- sensitive-topic tag list

Single-row partition holding the active sensitive-topic tag list. The
sensitive gate in Plan 06-04 reads this row at the start of each publish run.

| Attribute | Type | Required | Notes |
|-----------|------|----------|-------|
| `PK` | S | yes | `SPOTLIGHT_CONFIG#sensitive_tags` |
| `SK` | S | yes | `CONFIG` |
| `tags` | L (List of M) | yes | each entry: `{pattern: "...", match_type: "substring"\|"glob"\|"regex", reason: "..."}` |
| `last_updated_at` | S | yes | ISO 8601 UTC |
| `last_updated_by` | S | optional | operator login |

### Match strategy (v1)

**Substring with case-insensitive comparison** against the concatenation
`subtopic.label + " " + subtopic.description + " " + parent_topic.label`.
The LLM-canonical fields (label, description) are matched per CONTEXT D-19
-- UI fields are not used for matching. Adding `parent_topic.label` ensures
e.g. "vaccine" fires on subtopics under an Infectious Disease parent
regardless of the subtopic's own name.

The `match_type` field is **forward-compat** -- v2 may introduce
`glob` / `regex` without a schema migration. v1 only honors `substring`;
non-`substring` rows are ignored with a warning log.

### First-pass tag list (operator-seeded out-of-band)

Per the discuss-phase prompt v0 operational notes, the operator (Paul)
seeds these patterns manually via `aws dynamodb put-item` (or a one-off
Python script) before the first publish:

- vaccine policy
- abortion access
- gender-affirming care
- gun violence
- climate-and-health

These are **operator notes only**, recorded here so future maintainers
understand what the active list looked like at v1 launch. They are **NOT**
authoritative -- the live list lives in DynamoDB and may be edited at any
time by the operator without a code change.

### Tag list location (T-06-01-03)

The tag list lives in **DynamoDB only**. It is **NOT in this repo** and
**NOT committed** to git. This document describes the **schema** the tag
list uses, not the active tag values themselves. To inspect the live list:

```bash
aws dynamodb get-item \
  --table-name reciterai \
  --key '{"PK":{"S":"SPOTLIGHT_CONFIG#sensitive_tags"},"SK":{"S":"CONFIG"}}'
```

To update the list, the operator runs an out-of-band `put-item` or
`update-item`. The schema doc never enumerates the active values -- avoiding
checked-in sensitive-topic patterns is a deliberate disclosure-prevention
measure (CLAUDE.md security rule).

---

## Operator workflow notes

| Plan | Module | Reads | Writes |
|------|--------|-------|--------|
| 06-03 | `rotation_selector.py` | `SPOTLIGHT_HISTORY#` (BatchGetItem) | (none) |
| 06-04 | `sensitive_gate.py` | `SPOTLIGHT_CONFIG#sensitive_tags` (GetItem) | (none) |
| 06-04 | `review_queue.py` | (none) | `SPOTLIGHT_REVIEW#` (PutItem on flag) |
| 06-04 | review CLI (`--review-queue`, `--approve`, `--reject`) | `SPOTLIGHT_REVIEW#` (Query, UpdateItem) | `SPOTLIGHT_REVIEW#` (UpdateItem) |
| 06-06 | `publish.py` | `SPOTLIGHT_REVIEW#` (Query for `status=approved`) | `SPOTLIGHT_HISTORY#` (UpdateItem per selected) |

All boto3 calls follow the existing Phase 1 conventions:
parameterized `ExpressionAttributeValues` only; never interpolate
user-controlled strings into key conditions or expressions.

