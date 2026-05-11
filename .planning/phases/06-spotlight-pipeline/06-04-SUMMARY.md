---
phase: 06-spotlight-pipeline
plan: 04
subsystem: spotlight-pipeline
tags: [spotlight, sensitive-gate, review-queue, dynamodb, security, spot-08, spot-07]
requires: [06-01]
provides:
  - spotlight.sensitive_gate.load_sensitive_tags
  - spotlight.sensitive_gate.is_sensitive
  - spotlight.sensitive_gate.SubtopicMeta
  - spotlight.review_queue.write_review_entry
  - spotlight.review_queue.list_pending
  - spotlight.review_queue.set_status
  - spotlight.review_queue.VALID_FLAG_REASONS
  - spotlight.review_queue.VALID_TARGET_STATUSES
affects:
  - 06-05  # lede generator -- calls write_review_entry on critic / sensitive_tag flag
  - 06-06  # CLI dispatcher -- calls list_pending + set_status from --review-queue / --approve / --reject
tech-stack:
  added: []
  patterns:
    - "Lazy boto3 client (mirrors spotlight/pool_ranker.py:_get_default_client)"
    - "Low-level DynamoDB AttributeValue maps (no DocumentClient wrapper)"
    - "ExpressionAttributeNames {#s: status} alias for reserved word"
    - "ConditionExpression-enforced state machine (pending -> approved|rejected)"
    - "Recursive AV converter (_to_av / _from_av) for nested Map / List shapes"
    - "Local _now_iso_z (avoids same-wave dep on history_writer)"
key-files:
  created:
    - spotlight/sensitive_gate.py
    - spotlight/review_queue.py
    - test_spotlight_sensitive_gate.py
    - test_spotlight_review_queue.py
  modified: []
decisions:
  - "Original-case pattern returned by is_sensitive (not lowercased) so callers can log the operator's exact input"
  - "Non-substring match_type values fall through to substring with warning (forward-compat with v2 glob/regex; v1 fail-safe rather than skip silently)"
  - "_now_iso_z defined locally in review_queue.py rather than imported from history_writer (parallel-wave dependency invariant)"
  - "Empty/missing pattern strings in tag list silently skipped (operator-curated list may have blanks)"
  - "Adversarial test asserts user input never appears inside expression strings (T-06-04-02 mitigation)"
metrics:
  duration: 13m
  completed: 2026-05-07
  tasks_completed: 2
  tests_added: 23
  files_created: 4
---

# Phase 6 Plan 04: Sensitive Gate + Review Queue Summary

Sensitive-topic gate (`spotlight/sensitive_gate.py`) reads
`SPOTLIGHT_CONFIG#sensitive_tags` from DynamoDB with fail-closed semantics
and exposes `is_sensitive()` for case-insensitive substring matching
against subtopic.label + description + parent_topic_label. Review queue
writer/reader (`spotlight/review_queue.py`) provides the
`SPOTLIGHT_REVIEW#{publish_id}` surface that downstream plans 06-05
(lede critic) and 06-06 (CLI `--review-queue` / `--approve` / `--reject`)
both depend on. All UpdateExpression / FilterExpression /
ConditionExpression strings are static literals; user values flow through
ExpressionAttributeValues parameter binding only.

## Files

### Created

- **`spotlight/sensitive_gate.py`** (160 lines)
  - `SubtopicMeta` NamedTuple (subtopic_id, label, description, parent_topic_label)
  - `load_sensitive_tags(client=None) -> list[dict]` -- GetItem on
    `PK=SPOTLIGHT_CONFIG#sensitive_tags, SK=CONFIG`; raises
    `RuntimeError("fail closed: ...")` on missing config record OR
    boto3 ClientError. Logs only `len(tags)`, never pattern values.
  - `is_sensitive(meta, tags) -> tuple[bool, str | None]` -- case-insensitive
    substring match against `label + " " + description + " " + parent_topic_label`.
    Returns the original-case matched pattern. Non-substring `match_type`
    rows fall through to substring match with a warning log.

- **`spotlight/review_queue.py`** (264 lines)
  - `VALID_FLAG_REASONS = {"critic", "sensitive_tag", "both"}`
  - `VALID_TARGET_STATUSES = {"approved", "rejected"}`
  - `_now_iso_z()` -- local ISO 8601 UTC-Z timestamp (not imported from
    history_writer; same-wave dependency invariant)
  - `_to_av(value)` / `_from_av(av)` / `_flatten_item(item)` -- recursive
    conversion between Python native types and DynamoDB AttributeValue maps
  - `write_review_entry(client, entry)` -- PutItem with PK/SK composite,
    forces `status="pending"`, validates `flag_reason`, persists optional
    `critic_verdict` (Map), `sensitive_tag_matched` (S), `attempts` (List of M)
  - `list_pending(client, publish_id)` -- Query with `#s` alias for the
    reserved word `status`; returns flattened native-type dicts
  - `set_status(client, publish_id, subtopic_id, target_status, reviewer="cli")`
    -- UpdateItem with `ConditionExpression="#s = :pending"`; raises
    ValueError on invalid `target_status`; boto3
    `ConditionalCheckFailedException` propagates on stale state

- **`test_spotlight_sensitive_gate.py`** (262 lines, 9 tests)
  - Fail-closed semantics on missing config + ClientError
  - Multi-field haystack (label / description / parent_topic_label)
  - Case-insensitive substring match
  - Empty tag list -> (False, None)
  - Signature guard: no `lede` / `lede_text` parameter
  - Synthetic non-sensitive patterns only (`alpha`, `beta`, `gamma`, `delta`)

- **`test_spotlight_review_queue.py`** (377 lines, 14 tests)
  - put_item PK/SK composite shape
  - All required + optional attributes with low-level type assertions
  - flag_reason validation rejects invalid values without calling DynamoDB
  - status forced to `"pending"` regardless of caller input
  - list_pending Query call shape with `#s` alias and parameterized values
  - list_pending flattens nested AttributeValues (Map / List) into native types
  - set_status UpdateExpression / ConditionExpression / EAV shape
  - target_status validation rejects invalid values without calling DynamoDB
  - Adversarial test: user input never appears inside expression strings
  - Module-level invariants (VALID_FLAG_REASONS, VALID_TARGET_STATUSES,
    set_status signature)

## Test Fixture Notes

The synthetic SPOTLIGHT_CONFIG#sensitive_tags item shape used in tests
(`_config_item` helper) mirrors the canonical schema doc -- a single
`Item` map with `PK`, `SK`, and a `tags` `L` of `M` entries each having
`pattern`, `match_type`, `reason` `S` fields. Real operator tag patterns
(vaccine policy, abortion access, gender-affirming care, gun violence,
climate-and-health -- per docs/spotlight-dynamodb-schema.md operator
notes) NEVER appear in any source or test file in this plan; tests
exclusively use synthetic Greek-letter placeholders (`alpha`, `beta`,
`gamma`, `delta`). T-06-04-08 satisfied.

## Test Results

```
$ python3 -m pytest test_spotlight_sensitive_gate.py test_spotlight_review_queue.py -v
============================== 23 passed in 0.09s ==============================
```

| Suite                                | Count | Status |
| ------------------------------------ | ----- | ------ |
| `test_spotlight_sensitive_gate.py`   | 9     | PASS   |
| `test_spotlight_review_queue.py`     | 14    | PASS   |
| **Total**                            | **23**| **PASS**|

## Verification (per `<verification>` block)

| Check                                                                           | Result |
| ------------------------------------------------------------------------------- | ------ |
| `from spotlight.sensitive_gate import is_sensitive, load_sensitive_tags, SubtopicMeta` | PASS   |
| `from spotlight.review_queue import write_review_entry, list_pending, set_status` | PASS   |
| `grep -cE 'fail closed' spotlight/sensitive_gate.py` >= 2                       | 5      |
| `grep -cE '(UpdateExpression\|FilterExpression\|ConditionExpression)\s*=\s*f"\|\.format\(' spotlight/review_queue.py` returns 0 | 0      |
| `grep -cE '"vaccine"\|"abortion"\|"gun violence"' spotlight/sensitive_gate.py` returns 0 | 0      |
| 23/23 tests pass                                                                | PASS   |

## Commits

| Type   | Hash    | Message                                                              |
| ------ | ------- | -------------------------------------------------------------------- |
| test   | 87f207a | test(06-04): add failing tests for sensitive_gate (RED)              |
| feat   | 546af68 | feat(06-04): implement spotlight/sensitive_gate.py with fail-closed semantics |
| test   | 4b49465 | test(06-04): add failing tests for review_queue (RED)                |
| feat   | 65ae4ce | feat(06-04): implement spotlight/review_queue.py write/read surface  |

TDD gate sequence verified for both modules:
- sensitive_gate: RED (87f207a) -> GREEN (546af68)
- review_queue: RED (4b49465) -> GREEN (65ae4ce)

## Deviations from Plan

None. Plan executed exactly as written. No bugs encountered, no missing
critical functionality auto-added (Rules 1-3 not invoked). No architectural
decisions required (Rule 4 not invoked). No authentication gates -- all
DynamoDB calls use injected stub clients in tests.

Two minor implementation choices worth noting (already in `decisions:`
frontmatter):

1. **Non-substring `match_type` rows fall through to substring** rather
   than being silently skipped (RESEARCH §"Match strategy (v1)" reads:
   "non-`substring` rows are ignored with a warning log"). The plan's
   action text says: "log warning ... and proceed with substring match".
   We followed the plan's action text (proceed) rather than RESEARCH's
   prose (skip), which is the fail-safe choice for forward-compat tags
   accidentally written with `match_type="glob"`. If the operator
   intends to disable a tag, they should remove it from the list rather
   than rely on a non-substring `match_type` to no-op it.

2. **is_sensitive returns the original-case pattern** (not lowercased)
   on match. The plan's example shows `(True, "vaccine")` using a
   lowercase pattern; our test 5 explicitly verifies case preservation
   so callers logging `flag_reason="sensitive_tag" + sensitive_tag_matched`
   to DynamoDB record the operator's input verbatim.

## Threat-Model Mitigation Verification

| Threat ID    | Mitigation                                                              | Verified by                                  |
| ------------ | ----------------------------------------------------------------------- | -------------------------------------------- |
| T-06-04-01   | Fail-closed on missing config or ClientError                            | tests 2 + 3 in `test_spotlight_sensitive_gate.py` |
| T-06-04-02   | All UpdateExpression / FilterExpression / ConditionExpression strings are static literals | grep returns 0; test 11 in `test_spotlight_review_queue.py` |
| T-06-04-03   | ConditionExpression `#s = :pending` enforces state machine              | test 10 in `test_spotlight_review_queue.py`  |
| T-06-04-04   | Logger emits only `len(tags)`, never pattern values                     | source review (`logger.info("Loaded %d sensitive tags", ...)`) |
| T-06-04-06   | flag_reason / target_status validation raises ValueError on mismatch    | tests 4 + 9 in `test_spotlight_review_queue.py` |
| T-06-04-08   | No tag patterns hardcoded in source or tests                            | grep `vaccine\|abortion\|gun violence` returns 0; only synthetic Greek-letter patterns used |

## Known Stubs

None. Both modules wire directly to DynamoDB via injected boto3 clients;
no placeholder data flows. The CLI surface (`--review-queue`, `--approve`,
`--reject`) that consumes `list_pending` and `set_status` will be wired
in Plan 06-06.

## Forward References (for Plans 06-05 and 06-06)

Plan 06-05 (lede generator + critic) calls:
```python
from spotlight.sensitive_gate import is_sensitive, load_sensitive_tags, SubtopicMeta
from spotlight.review_queue import write_review_entry
```
Tags are loaded once at the top of the publish run (one GetItem call).
On critic-fail-after-N-regens or `is_sensitive() == True`, the lede
generator constructs an entry dict and calls `write_review_entry(client, entry)`.

Plan 06-06 (CLI dispatcher) calls:
```python
from spotlight.review_queue import list_pending, set_status
```
`--review-queue [--publish-id <id>]` -> `list_pending`; `--approve <subtopic_id>`
and `--reject <subtopic_id>` -> `set_status` with `reviewer="cli"`.

## Self-Check

```
$ test -f spotlight/sensitive_gate.py && echo "FOUND: spotlight/sensitive_gate.py"
FOUND: spotlight/sensitive_gate.py
$ test -f spotlight/review_queue.py && echo "FOUND: spotlight/review_queue.py"
FOUND: spotlight/review_queue.py
$ test -f test_spotlight_sensitive_gate.py && echo "FOUND: test_spotlight_sensitive_gate.py"
FOUND: test_spotlight_sensitive_gate.py
$ test -f test_spotlight_review_queue.py && echo "FOUND: test_spotlight_review_queue.py"
FOUND: test_spotlight_review_queue.py
$ git log --oneline --all | grep -E "87f207a|546af68|4b49465|65ae4ce"
65ae4ce feat(06-04): implement spotlight/review_queue.py write/read surface
4b49465 test(06-04): add failing tests for review_queue (RED)
546af68 feat(06-04): implement spotlight/sensitive_gate.py with fail-closed semantics
87f207a test(06-04): add failing tests for sensitive_gate (RED)
```

## Self-Check: PASSED

All four files exist on disk; all four task commits exist in git history;
all 23 tests pass.
