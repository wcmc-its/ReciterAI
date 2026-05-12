# Phase 9 — Execution Substrate (Summary)

**Status:** Complete 2026-05-12
**Plan:** [09-PLAN.md](09-PLAN.md)
**Spec source:** [docs/RECITERAI-SPEC.md §5 + §7](../../../docs/RECITERAI-SPEC.md)

## Outcome

Phase 9 shipped two pieces of substrate plus one proof-of-substrate integration. The remaining pipeline stages (`score_publications`, `discover_subtopics`, `assign_subtopics`, `rollup_by_cwid`, `backfill_spotlight`) plug into this substrate during Phase 10. Pre-decomposing those integrations now would have been guessing — they get scoped when Phase 10 opens.

| Deliverable | Status | Tests | Commit |
|---|---|---|---|
| `utils.bedrock_client.MODEL_IDS_BY_STAGE` (stage-keyed view) | ✓ | covered transitively | `0aead4d` |
| `utils.stage_records` (CRUD + compute_input_hash + should_skip) | ✓ | 19 | `9a379f4` |
| `docs/data-model-and-queries.md` STAGE# schema docs | ✓ | n/a | `6a8811d` |
| `gates.registry` (decorator + GateResult + run_gates + any_blocked) | ✓ | 19 | `6a8811d` |
| `gates.schema_validation` | ✓ | 5 | `4476172` |
| `gates.parent_prefix` | ✓ | 6 | `4476172` |
| `gates.pii_scan` (with zero-false-positive guard on `v2026-05-12`) | ✓ | 10 | `4476172` |
| `gates.schema_roundtrip` (publish_post, warn severity) | ✓ | 8 | `4476172` |
| `pipeline_hierarchy.publish` wired to both substrates | ✓ | 11 | `ed1de16` |
| `python -m gates` ad-hoc CLI | ✓ | 10 | (this commit) |
| `docs/stage-records-and-gates.md` author guide | ✓ | n/a | (this commit) |

**Test total**: 115 passing.

## What changed for the operator

Running `python -m pipeline_hierarchy.publish` now:

1. Computes a content-addressed `input_hash`. If a prior `complete` row with the same hash exists in DynamoDB, writes a `skipped` row and exits 0 without re-uploading. Same inputs → no work.
2. Runs three block-severity gates (`schema_validation`, `parent_prefix`, `pii_scan`) before the S3 upload. Any failure writes a `failed` row and exits 3. Override: `--force --force-reason "<audit note>"`.
3. After upload, runs the `schema_roundtrip` warn-severity gate which re-validates the just-published bytes against the just-published schema. Failures appear in stderr but do not halt.
4. Writes a `STAGE#publish_hierarchy#GLOBAL` `complete` row with `output_pointer`, `records_written`, `model_ids_snapshot`, and (when applicable) `force_reason`.

Ad-hoc gate runs without affecting the substrate ledger:

```bash
python -m gates --stage publish --hierarchy v2026-05-12
python -m gates --list
```

## Verification (goal-backward)

The plan listed three verification scenarios. All three are exercised by `tests/test_publish_integration.py`:

| Scenario | Test | Result |
|---|---|---|
| Re-run on unchanged inputs → skipped row, no S3 PutObject, exit 0 | `test_skip_on_matching_prior_complete_row` | ✓ |
| Hand-edited violation → blocked, failed row written, no S3 PutObject, exit 3 | `test_block_severity_gate_failure_writes_failed_row_and_skips_upload` | ✓ |
| `--force --force-reason "..."` → complete row written with `force_reason` populated | `test_force_with_reason_overrides_gate_block` | ✓ |

**Manual end-to-end DynamoDB exercise (operator step)**: deferred to the operator's discretion. The mocked tests cover the substrate API surface; the real round-trip lands the first time `publish.py` runs with the substrate active against the real `reciterai-chatbot` table. Recommended approach: invoke `python -m pipeline_hierarchy.publish` against a content-identical bundle (relabel skipped); confirm the second invocation writes a `skipped` row. Then `aws dynamodb query --table-name reciterai-chatbot --key-condition-expression "PK = :pk" --expression-attribute-values '{":pk": {"S": "STAGE#publish_hierarchy#GLOBAL"}}'` to inspect the row.

## Plan deviations

- **Task 1 (G-35 centralization)** turned out to be additive only — the model IDs were already centralized in `utils/bedrock_client.py`. Phase 9 added `MODEL_IDS_BY_STAGE` as a stage-keyed view; no rename, no migration, no `STAGE#` invalidation story. Spec §11 G-35 entry rewritten to reflect actual state.
- **Task 3 (DynamoDB table provision)** collapsed to documentation: the existing single `reciterai-chatbot` table accommodates `STAGE#` items under the same composite-PK/SK convention as other record types. No new table created.
- **`--no-rebuild` flag removed in task 9** rather than deferred to #14. Keeping a debugging fallback pointing at a static bundle was incoherent with substrate hashing the live bundled dict. #14 is still open for the remaining `SOURCE_HIERARCHY` cleanup in `generator.py` and the two equivalence tests.
- **`schema_roundtrip` gate bug fix**: the gate caught `JSONDecodeError` but not `TypeError`/`ValueError`. A non-bytes input would have raised through the runner. Fixed during task 9's integration testing.

## Slip checkpoint

The plan named "end of working-day 5 of Phase 9 execution" as the slip checkpoint, with the off-track action being to branch a degenerate-timestamp Phase 10 path.

**Actual**: all 11 tasks shipped in a single session (~6 hours wall clock). Checkpoint did not fire. The plan's 5–8 day estimate was conservative — task 1's "centralize already-centralized constants" framing collapsed it to a 20-line additive change, and tasks 3 and 11 turned out to be docs-only.

## Open follow-ups

- **#14** (open) — Static `hierarchy_full.json` deletion + remaining `SOURCE_HIERARCHY` cleanup in `generator.py` + two equivalence tests. The `--no-rebuild` portion of #14's DoD landed in `ed1de16`.
- **Phase 10 prerequisite**: provision the `reciterai-chatbot` table in production if not already. Existing scripts use it (PROCESSING# tracker, TAXONOMY#, etc.), so this is likely a no-op — verify before Phase 10 starts.
- **GSI on `STAGE#.input_hash`**: deferred. Reassess when score/assignment/rollup/spotlight stages start writing.

## Owner

This phase was executed in a single session 2026-05-11–2026-05-12. The slip-decision owner role (`gsd-resume-work` convention) is unset — the phase landed before the checkpoint, so no owner-driven slip decision occurred. Future phase plans should still name the owner when execution begins.
