---
phase: 11
phase_name: Versioning, Review State, Diff Signaling
researched: 2026-05-12
domain: ReciterAI cold-path contract surfaces (DynamoDB schema + S3 publish + operator CLI)
confidence: HIGH (codebase is in-hand; spec is in-hand; no external library research needed)
---

# Phase 11 Research

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

D-01..D-19 are locked in `11-CONTEXT.md`. The planner MUST NOT explore alternatives to any of:

- **D-01** `hierarchy_version` stamping is scoped to activity records carrying subtopic fields (rows where `primary_subtopic_id` / `subtopic_ids[]` / `subtopic_confidences{}` is populated, written by `assign_subtopics.py:642–648`). Not all TOPIC#/IMPACT# rows.
- **D-02** One-off backfill migration joins each affected TOPIC# row to its `PROCESSING#pmid_*` sentinel for the `taxonomy_version` active at write time. Orphans get `0.0.0-orphan` sentinel. Orphan count reported as a number; no pre-committed accept threshold.
- **D-03** `hierarchy_version` is a semver-shaped string (contract commitment). Today: date-based (`v2026-05-06`).
- **D-04** Rotation history rewrite in-place — new PK shape `SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}` + `SK = "STATE"`. One-shot migration, no two-shape read path.
- **D-05** Orphan rotation rows split into two report buckets (`never_spotlighted_count`, `malformed_publish_id_count`); both get the same `0.0.0-orphan` stamp.
- **D-06** Cold-path version cutover writes a `STAGE#hierarchy_version_cutover#GLOBAL` row with `prev_version`, `new_version`, `migrated_rotation_count`, `orphan_count`, `started_at`, `completed_at`, `initiated_by`.
- **D-07** REVIEW# scope this phase is `GLOBAL` only. Per-topic granularity is deferred.
- **D-08** Review approval CLI: `python -m review approve --artifact hierarchy --version v2026-06-01` opens `$EDITOR` on a pre-populated YAML. `reviewer_cwid` from `~/.reciterai/config.yaml` (or `RECITERAI_REVIEWER_CWID` env). No new dotfile. Pre-write validator also exposed as `python -m review validate <path>`.
- **D-09** `diff.json` computed hybrid: STAGE# row from `assign_subtopics` (filtered by current cold-run's `run_id`) for "PMIDs reassigned"; byte-comparison of `s3://{bucket}/{prev_version}/hierarchy.json` vs new for taxonomy/added/removed/renamed subtopics.
- **D-10** `diff.json` written from `pipeline_hierarchy/publish.py`. One new function, not a new stage.
- **D-11** S3 write order: (1) `{version}/hierarchy.json`, (2) `{version}/hierarchy.schema.json`, (3) `{version}/diff.json`, (4) `{version}/manifest.json`, (5) `latest/manifest.json`.
- **D-12** `diff.json` carries top-level `diff_schema_version`.
- **D-13** STAGE# queries in `diff.json` computation filter by current cold-run's `run_id`. Substrate addition: optional `run_id` field on STAGE# rows (backwards-compatible); `pipeline_cold.run.main()` generates UUID at start and threads through every stage.
- **D-14** G-29 fix: stop writing `generated_at` into `hierarchy.json`. `manifest.json` still carries it. No transitional `content_sha256`. No rewrite of old version directories.
- **D-15** G-29 cutover paired with SPS downstream-effects coordination (30-min operator conversation, runbook captures coordination window). Not an engineering deliverable but a scheduled task in Phase 11.
- **D-16** Cutover publish writes a `STAGE#g29_cutover#GLOBAL` row with `previous_publish_sha`, `new_publish_sha`, `hierarchy_version_at_cutover`, `run_id`, `started_at`, `completed_at`.

### Claude's Discretion (Open items in CONTEXT.md)

- **O-01** First-ever-publish edge case for `diff.json` — either emit with `from_version: null` OR omit entirely. Planner picks alongside implementation.
- **O-02** REVIEW# pre-write validator implementation seam — inline in `review/cli.py` OR factored into `review/validator.py` imported by both `approve` and `validate` subcommands. Planner picks based on test ergonomics.
- **O-03** Where the cold path writes `STAGE#hierarchy_version_cutover` — top of `pipeline_cold.run.main()` (write `prev_version` early, update on completion) vs end-only single write. Planner decides.

### Deferred Ideas (OUT OF SCOPE)

- Per-topic REVIEW# granularity (`SK: TOPIC#{topic_id}`) — YAGNI for v1.
- Stable subtopic_ids across cold runs (would enable Option-3 carry-forward in rotation history) — separate phase; not Phase 11.
- `content_sha256` manifest field — YAGNI; add only if a second embedded timestamp lands.
- Spec §3 rewrite (`(cwid, hierarchy_version)` framing is a holdover — rotation is per-subtopic, cwid never enters the key). Followup spec-hygiene task.
- REVIEW# dashboard (Streamlit/Flask).
- A/B testing of hierarchy versions on the SPS side — producer side is prepared; consumer-side time-machine support is deferred until a trigger fires.
</user_constraints>

<phase_requirements>
## Phase Requirements

No REQUIREMENTS.md exists for this project. Plans derive must-haves directly from the 19 locked CONTEXT decisions (D-01..D-19) and the four roadmap deliverables in `.planning/ROADMAP.md` §Phase 11. No requirement IDs are mapped because none exist.

| Roadmap deliverable | Supporting CONTEXT decisions | Research support |
|---------------------|------------------------------|------------------|
| `hierarchy_version` stamped on every activity record carrying subtopic fields | D-01, D-02, D-03 | Surface 1 (write-sites, backfill mechanics) |
| Rotation state keyed by `(hierarchy_version, subtopic_id)` | D-04, D-05, D-06 | Surface 1 (rotation history rewrite + read paths) |
| `REVIEW#` records + `python -m review approve` CLI with pre-write validator | D-07, D-08 | Surface 2 (validator gates, row shape, CLI surface) |
| `diff.json` + S3 write-order + read-tolerance + `Cache-Control` on `latest/*` | D-09..D-13, D-16 | Surface 3 (current publish flow, write-order delta, diff producer) |
| Move `generated_at` out of `hierarchy.json` (G-29) | D-14, D-15 | Surface 3 (G-29 fix scope) |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **No AI attribution** in commit messages, PR descriptions, or code comments. Write commits as if the user authored them.
- **No hardcoded credentials.** All AWS calls go through default credential chain. REVIEW CLI must not read or display credential values.
- **Decimal coercion mandatory** for all numeric DynamoDB writes (Phase 1 Pitfall 1 — DynamoDB rejects Python floats). Use `utils.dynamodb_helpers.to_decimal` for any numeric stamped onto activity records (this mostly affects `hierarchy_version` writes if they live alongside `subtopic_confidences`, which they do).
- **Never commit `.env` files** or `.planning/CLAUDE.md`. Check before every commit.
- **Project location:** flat layout at repo root (`assign_subtopics.py`, `pipeline_hierarchy/`, `utils/`, `spotlight/`, `gates/`) — NOT `src/reciterai/`. The phase prompt's filesystem hints (`src/reciterai/`) were wrong; use the actual layout.
- **Test convention:** `pytest` + `unittest.mock.MagicMock` + `botocore.stub.Stubber` for boto3 (see `tests/test_pipeline_hot_orchestrator.py`, `tests/test_stage_records.py`). No `moto` in the dep tree. **Adding `moto` is a decision** — current codebase does NOT use it; tests stub boto3 directly.

## Overview

Phase 11 ships three independent contract surfaces against a codebase that already has the substrate (Phase 9 STAGE#, Phase 10 cold-path orchestrator with `--initiated-by`). All three are deltas, not rewrites:

1. **`hierarchy_version` stamping on activity records (D-01..D-07).** One write-site (`update_activity_subtopics` in `utils/dynamodb_subtopic_migration.py`, called from `assign_subtopics.py:642–648`) gains a fourth field. A one-shot backfill stamps existing rows by joining to `PROCESSING#pmid_{pmid}` for the taxonomy_version active at write time. Rotation history (`SPOTLIGHT_HISTORY#{subtopic_id}` in `spotlight/history_writer.py`, read in `spotlight/rotation_selector.py`) gets its PK shape changed to `SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}` and every existing row is rewritten in place. One callsite cluster: writer (history_writer.py:118), reader (rotation_selector.py:164), test-data fixtures (test_spotlight_rotation_selector.py:246), backfill-truncate (`backfill_spotlight.py:703`).

2. **`REVIEW#` records + CLI (D-07, D-08).** New module `review/` containing `cli.py` (argparse `approve` + `validate` subcommands), a YAML template generator, and a pre-write validator. New dependency: **PyYAML** (not currently in `requirements.txt`). The validator gates already exist as data — `assign_subtopics.py` writes `LOW_CONFIDENCE_ASSIGNMENT#{pmid}` rows when confidence is below `low_confidence_floor` (per Phase 10 D-13), and `STAGE#` rows from `pipeline_cold/run.py` carry `status=failed` on stage exit. The validator inspects DynamoDB state to refuse approval when these signal failure. Operator config at `~/.reciterai/config.yaml` is greenfield — no current code reads it; D-08 commits Phase 11 to that location as canonical.

3. **Structured change signaling (D-09..D-16).** Three deltas to `pipeline_hierarchy/publish.py`: (a) add a `diff.json` producer function called between hierarchy upload and manifest upload, (b) reorder the existing two-call `upload_to_s3()` to the five-step contract, (c) set `Cache-Control: max-age=60, must-revalidate` on `latest/manifest.json`. Plus one-line edit to `pipeline_hierarchy/bundler.py` (stop writing `generated_at`) and a two-line edit to `pipeline_hierarchy/generator.py` (don't re-stamp `generated_at` into `hierarchy["generated_at"]`; do still stamp `manifest["generated_at"]`). The STAGE# `run_id` substrate addition lives in `utils/stage_records.py`.

**Primary recommendation:** Three plans, one per surface. Surface 1 is the largest (write-site enforcement + backfill + rotation rewrite + cold-path audit row). Surface 2 is the smallest in lines-of-code but introduces the new `review/` package and a new dependency. Surface 3 is the most consumer-visible — coordinate with the SPS hierarchy-ETL owner per D-15 before the cutover publish.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| `hierarchy_version` field write | Cold-path Python writer (`assign_subtopics` → `dynamodb_subtopic_migration`) | DynamoDB | The cold path is the producer of all subtopic assignments; stamping happens in the same UpdateItem that writes the subtopic fields. |
| Backfill migration script | Standalone Python utility (new) | DynamoDB | One-off operator-driven; lives next to `utils/dynamodb_subtopic_migration.py` per existing convention. |
| Rotation history rewrite | Standalone Python utility (new) | DynamoDB | Same one-off pattern; reads-writes via existing helpers. |
| Rotation key shape | DynamoDB schema (PK string format) | Python readers/writers | The shape is the contract; readers (`rotation_selector.py`) and writers (`history_writer.py`) consume it. |
| `STAGE#hierarchy_version_cutover` | `pipeline_cold/run.py` orchestrator | DynamoDB | The cold-path entry point owns version provenance; STAGE# substrate already absorbs new audit rows without infra change. |
| `STAGE#g29_cutover` | `pipeline_hierarchy/publish.py` | DynamoDB | The publisher mints the new `manifest.sha256`; it's the natural author of the cutover audit row. |
| REVIEW# row write | `review/cli.py` (new) | DynamoDB | CLI is the only writer per D-08; no programmatic write surface yet. |
| Pre-write validator | `review/validator.py` (new, per O-02 lean) | DynamoDB (read-only) + S3 HEAD | Pure function over a YAML dict + DDB query results; testable without I/O when seams are right. |
| `diff.json` producer | `pipeline_hierarchy/publish.py` (new function) | DynamoDB (STAGE# read) + S3 (prev hierarchy GET) | D-10 explicit; folds into the publisher rather than a new stage. |
| S3 write order | `pipeline_hierarchy/publish.py::upload_to_s3` | S3 | One function controls the call order; no other writer exists. |
| `Cache-Control` on `latest/*` | `utils/s3_client.py::put_object` (signature change) + caller pass-through | S3 | The client's `put_object` currently doesn't accept `cache_control`; add an optional kwarg, caller passes it for `latest/*` keys only. |
| Removing `generated_at` from `hierarchy.json` | `pipeline_hierarchy/bundler.py` + `pipeline_hierarchy/generator.py` | — | Both files currently inject `generated_at`; both must stop. |

## Surface 1: `hierarchy_version` stamping

### Write-site inventory

| File:line | Function | Currently writes | Phase 11 delta |
|-----------|----------|------------------|----------------|
| `utils/dynamodb_subtopic_migration.py:37` | `update_activity_subtopics(pk, sk, subtopic_ids, primary_subtopic_id, confidences)` | `SET subtopic_ids, primary_subtopic_id, subtopic_confidences` | **Add `hierarchy_version` parameter and stamp it in the same SET expression.** This is the SOLE write site for the three subtopic fields. |
| `assign_subtopics.py:642–648` | Caller of above | Passes 5 args | Pass `hierarchy_version` as the 6th. Read it from the bundled hierarchy or threaded in via `run()` (see below). |
| `utils/test_dynamodb_subtopic_migration.py:46, 138` | Test fixtures for the writer | — | Update signature + assertions. |
| `tests/test_low_confidence_event.py:59` | Tests `dry_run=True` (writer is off) | — | No change to write contract, but if `hierarchy_version` plumbing affects `assign_subtopics.run()` signature, tests need the new arg. |

**Important:** `update_faculty_subtopic_scores()` and `clear_faculty_subtopic_scores_for_topic()` in the same module write FACULTY# rows. Per D-01 these are **out of scope** — `hierarchy_version` stamping is scoped to activity records, not faculty rollup records. Faculty scores are derived; their freshness is implied by the underlying activity stamping. Confirm with planner but research recommendation is: don't touch the two faculty functions.

**Source of `hierarchy_version` at write time:** the bundler produces a hierarchy dict that carries `taxonomy_version` but not `hierarchy_version` today (see `pipeline_hierarchy/bundler.py:189–195`). The version label is minted at publish time (`pipeline_hierarchy/publish.py:287` → `manifest["version"]`, computed as `f"v{generated_at[:10]}"` in `generator.py:111`). For `assign_subtopics` to stamp it, the version must be **decided before assign runs**. Two options for the planner:

1. **Mint the version at cold-run start** (D-06 already requires this for the `STAGE#hierarchy_version_cutover` row). `pipeline_cold/run.py` decides `new_version` upfront (e.g., `f"v{started_at[:10]}"`), threads it via env var or `--hierarchy-version` argv into each per-topic `backfill_all.py` invocation, which threads it into `assign_subtopics`. Clean but adds plumbing.
2. **Read it from the bundled draft hierarchy file**. The pre-bundled file lives at `.planning/phases/04-subtopic-system/hierarchy_full.json` (per `pipeline_hierarchy/generator.py:24`). If the bundler/operator stamps `hierarchy_version` into the draft, `assign_subtopics` reads it via the same file load path. Less plumbing but couples assign to the bundle-product file.

**Recommendation:** Option 1. It's the same path D-06's `STAGE#hierarchy_version_cutover` row uses; reusing the threading means one source of truth. The `--initiated-by` flag in `pipeline_cold/run.py:248` is the precedent for threading run-level metadata via argv.

### Backfill migration mechanics

**Join key:** `PROCESSING#pmid_{pmid}` + `SK: STATUS` (per `utils/dynamodb_helpers.py:298`). Each TOPIC# / SCORE#ACTIVITY# row carries a `pmid` (extractable from its SK pattern `SCORE#0850#ACTIVITY#pmid_12345#cwid_abc` per the `update_activity_subtopics` docstring). For each activity row with subtopic fields populated:

1. Extract `pmid` from the SK.
2. `GetItem PK=PROCESSING#pmid_{pmid}, SK=STATUS`.
3. Read `taxonomy_version` from the PROCESSING# row.
4. Map `taxonomy_version → hierarchy_version` via a small lookup table the migration script owns (per D-02: "for the `taxonomy_version` active at write time" — but the migration needs a hierarchy_version, not taxonomy_version). **Open question for planner (flag, do not decide):** the spec/CONTEXT chain says "joins to PROCESSING# for the taxonomy_version active at write time" but the stamped field is `hierarchy_version`. Either (a) the migration stamps `taxonomy_version` itself as a pre-Phase-11 proxy and the planner accepts that historical rows have `hierarchy_version == taxonomy_version` for the backfill window, or (b) the migration maintains a hardcoded `{taxonomy_version: hierarchy_version}` map for the known historical versions (taxonomy_v1, taxonomy_v2 → corresponding hierarchy version labels). See Open Questions §OQ-1.

**Orphan handling:** `0.0.0-orphan` sentinel per D-02. Semver-shape per D-03: parses as valid pre-release semver. Sorts before any real `1.x.y` or `2.x.y` real version in lexicographic AND semver order. Compatible with any future ordered comparison in consumer code.

**Migration shape (per D-02 + D-05):** the script SCANs the table for items with `attribute_exists(primary_subtopic_id)`, batches them by 25, for each row joins to PROCESSING#, stamps `hierarchy_version`, reports an orphan count broken into two buckets at run-end:

- `taxonomy_version_resolved_count` — rows where PROCESSING# join succeeded
- `orphan_count` — rows where it didn't (no PROCESSING# row OR PROCESSING# missing `taxonomy_version` attribute)

The two-bucket rotation orphan split (D-05) is a parallel concept for the rotation history rewrite, not the activity-row backfill.

### Rotation history rewrite mechanics

**Current row shape** (`spotlight/history_writer.py:117–120`):
```
PK: SPOTLIGHT_HISTORY#{subtopic_id}
SK: STATE
attrs: shown_count (N), last_shown_at (S ISO8601 Z), last_shown_publish_id (S, e.g. "v2026-05-12")
```

**Phase 11 row shape:**
```
PK: SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}
SK: STATE
(attrs unchanged)
```

**Inference of hierarchy_version from existing rows:** `last_shown_publish_id` carries `v{ISO-date}` per the format committed in `spotlight/publish.py:149` (`version = f"v{date.today().isoformat()}"`) and used as `publish_id` in `update_history` calls. So:

- Row has `last_shown_publish_id = "v2026-05-12"` → infer `hierarchy_version = "v2026-05-12"`.
- Row is missing `last_shown_publish_id` entirely → `never_spotlighted_count`, stamp `0.0.0-orphan` per D-05.
- Row has `last_shown_publish_id` but it doesn't match `^v\d{4}-\d{2}-\d{2}$` → `malformed_publish_id_count`, stamp `0.0.0-orphan`.

**Per D-04 risk mitigation (also called out in CONTEXT §Risks):** the migration script MUST fail loudly on any unrecognized publish_id format and emit a per-row before/after diff log to disk before committing. Operator reviews log + explicit confirm-and-commit. The default mode is dry-run; `--commit` is required to write. Same idempotency story as the existing `utils/dynamodb_subtopic_migration.py` — UpdateItem is safe to re-run.

**Migration mechanics:**

1. SCAN PK begins-with `SPOTLIGHT_HISTORY#` (the legacy shape — no version prefix means PK starts with `SPOTLIGHT_HISTORY#` followed directly by `{subtopic_id}`).
2. For each row: parse `last_shown_publish_id`, decide bucket (real / never / malformed), compute new PK.
3. `PutItem` to new PK with same attrs. `DeleteItem` on old PK. **Not** UpdateItem — PK changes mean a delete+put cycle. Risk: in-flight rotation reads/writes during migration could race. Mitigation: run during an operational lull (the spotlight rotation publish is monthly per Phase 10 cadence; do the migration immediately after a fresh rotation publish so no read fires for ~30 days).
4. The migration is **one row → one row**, not a fan-out. Same row count after as before.

### Read-site inventory (rotation history consumers)

| File:line | Function | Read pattern | Phase 11 delta |
|-----------|----------|--------------|----------------|
| `spotlight/rotation_selector.py:164` | `fetch_history()` | BatchGetItem keys built as `{"PK": f"SPOTLIGHT_HISTORY#{sid}", "SK": "STATE"}`, 25 per chunk | Update keys to `f"SPOTLIGHT_HISTORY#{hierarchy_version}#{sid}"`. `hierarchy_version` must reach this function — easiest via `select_with_diversity` plumbing from `spotlight/publish.py` which already owns `version`. |
| `spotlight/history_writer.py:118` | `update_history()` writer | UpdateItem on same PK shape | Same plumbing: pass `hierarchy_version` (== `publish_id` in practice today, but spec separates them — see Open Q §OQ-2). |
| `spotlight/publish.py:218` | Caller of `update_history` | Passes `publish_id=version` | Pass `hierarchy_version=version` too (or `publish_id` IS the hierarchy_version — see Open Q). |
| `backfill_spotlight.py:679–721` | `--reset-history` truncate | DeleteItem by scan filter `begins_with(PK, "SPOTLIGHT_HISTORY#")` | No code change; the prefix scan still matches the new shape (it's a longer string, still begins with the old prefix). |
| `test_spotlight_rotation_selector.py:246, 254, 276` | Test fixtures | Hardcoded `SPOTLIGHT_HISTORY#sub_a` etc. | Update to new key shape. |
| `tests/test_spotlight_rotation_selector.py:314–316` | Generation of synthetic PKs | Loop string-formats `SPOTLIGHT_HISTORY#sub_{i:03d}` | Update format. |

**Critical:** `_HISTORY_PK_PREFIX = "SPOTLIGHT_HISTORY#"` in `rotation_selector.py:50` and the `_ingest_responses()` parser at line 195 strip the prefix and treat the rest as the subtopic_id. After D-04, the "rest" is `{hierarchy_version}#{subtopic_id}` and the parser must split on `#` and take the LAST segment as `subtopic_id`. The parser is the only fragile part — it's currently `sid = pk[len(_HISTORY_PK_PREFIX):]`.

## Surface 2: REVIEW# state machine

### Current cold-path mint gates (what the validator must read)

The phrase "any quality gate failed" in CONTEXT (D-08 / D-13) maps to the following existing signals that the validator inspects:

| Signal | Where written | Where read | Failure shape |
|--------|---------------|------------|---------------|
| `STAGE#{stage}#{scope}` with `status=failed` | `utils/stage_records.py::write_failed` (called from each stage, e.g., `pipeline_hierarchy/publish.py:259`) | Validator must query `STAGE#cold_run#GLOBAL` for the in-progress / most-recent run | If any stage has `status=failed`, refuse to approve. |
| `LOW_CONFIDENCE_ASSIGNMENT#{pmid}` rows | `assign_subtopics.py:181–208` (via `_maybe_write_low_confidence_event`) | Validator can query by `begins_with(PK, "LOW_CONFIDENCE_ASSIGNMENT#")` filtered by `run_id` (Phase 10) | Above some operator-tunable threshold → refuse. Spec §4 doesn't specify a threshold; planner picks (or defers to YAGNI). |
| `UNCOVERED_PMID#{pmid}` rows (Phase 10) | Phase 10 event writer | Same query pattern | Same threshold question. |
| Gate `block` failures (parent_prefix, pii_scan, schema_validation) | `pipeline_hierarchy/publish.py:259` writes `STAGE#publish_hierarchy#GLOBAL` with `error_code=GATE_BLOCK_*` | Validator inspects the most-recent `STAGE#publish_hierarchy#GLOBAL` row's `error_code` | If error_code starts with `GATE_BLOCK_`, refuse approval. |
| Force-override on a publish | `force_reason` attribute on the STAGE# row | Validator checks if `force_reason` is set | If present, REVIEW must explicitly acknowledge — could require a longer `rationale` field, or surface in the YAML template's prompt. |

**Read seam recommendation:** validator takes a `RunSignals` dataclass injected at construction time; production fetches signals via DDB queries, tests inject synthetic dicts. Mirrors the boto3-Stubber pattern used elsewhere.

### Proposed REVIEW# row shape

Per spec §4 with D-07 narrowing (`SK = "GLOBAL"` only):

```
PK: REVIEW#{artifact_type}#{version}    e.g. REVIEW#hierarchy#v2026-06-01
SK: GLOBAL
{
  proposed_artifact_uri: "s3://wcmc-reciterai-hierarchy/v2026-06-01/hierarchy.json",
  summary_stats: {
    topics_added: 2,
    subtopics_renamed: 14,
    pmids_reassigned: 312
  },
  status: "pending" | "approved" | "rejected" | "superseded",
  reviewer_cwid: "cwid_jsmith" | null,
  reviewed_at: "2026-06-01T..." | null,
  rationale: "string" | null,
  decision: "approve" | "reject" | null
}
```

**`summary_stats` source:** the same hybrid as `diff.json` per D-09. The CLI's pre-populated YAML template fills these from a current-run query against `STAGE#assign_subtopics#topic:*` rows filtered by `run_id` + byte-compare against `s3://{bucket}/{prev_version}/hierarchy.json`. **Recommendation:** factor the diff-stats compute logic into a shared module (`pipeline_hierarchy/diff_stats.py` or similar) used by both `diff.json` producer (Surface 3) and REVIEW YAML template (Surface 2). One source of truth.

**Decimal coercion (CLAUDE.md):** `summary_stats` counts are integers → store as `N` natively, no float conversion needed. No Decimal pitfall here.

### Pre-write validator behavior (refuse-to-approve rules)

From spec §4 + D-08:

1. `reviewer_cwid` present, matches `^cwid_[a-z]+\d+$` regex.
2. `rationale` present, ≥ 40 characters after stripping whitespace.
3. `decision ∈ {"approve", "reject"}`.
4. `proposed_artifact_uri` resolves to an actual S3 object (S3 HEAD; fails if 404).
5. **Phase 11 addition (refuse approval if a quality gate failed):** when `decision == "approve"`, check each of the cold-run signals from the table above. Any failed gate → refuse with a specific error message identifying which signal failed. `decision == "reject"` skips the gate check (you can always reject regardless of gate state).
6. On any failure: exit non-zero, leave YAML on disk, write nothing to DynamoDB. Per D-08 the validator is also exposed as `python -m review validate <yaml-path>` for ad-hoc inspection without committing.

**Implementation seam (O-02 recommendation):** factor into `review/validator.py` with a pure `validate(yaml_dict, signals: RunSignals) -> ValidationResult` function. `review/cli.py::approve` calls it; `review/cli.py::validate` calls it on a YAML-loaded-from-disk. Tests target the pure function with no I/O. This is the cleaner side of O-02.

### CLI surface (`python -m review approve ...`)

Per D-08:

```
# Open $EDITOR on pre-populated YAML; validate on save; PutItem to DynamoDB
python -m review approve --artifact hierarchy --version v2026-06-01

# Validate a draft YAML without writing
python -m review validate path/to/review-draft.yaml

# (Implied by spec §4 — planner picks whether to ship these now)
python -m review list [--status pending] [--artifact hierarchy]
python -m review show --artifact hierarchy --version v2026-06-01
```

**Module structure (proposed):**
```
review/
├── __init__.py
├── __main__.py        # argparse subcommands dispatch
├── cli.py             # approve + validate entry points
├── validator.py       # pure validation logic (per O-02)
├── template.py        # YAML template generator (summary_stats fetch, prepopulate)
└── store.py           # DDB read/write wrapper (PutItem for REVIEW#, Query for summary_stats sources)
```

**Operator config (`~/.reciterai/config.yaml`) — greenfield:** D-08 commits Phase 11 to this path. Schema (proposed):

```yaml
# ~/.reciterai/config.yaml — ReciterAI operator config (Phase 11+)
reviewer_cwid: cwid_jsmith  # used by `python -m review approve` to fill in reviewer_cwid
# (future operator-config keys land here, not in new dotfiles per D-08)
```

Loader returns `None` if file missing; CLI then falls back to `RECITERAI_REVIEWER_CWID` env; finally errors with an actionable message if both are absent.

**New dependency:** PyYAML. Add to `requirements.txt` (`pyyaml>=6.0.1`). Greenfield — no other module in the repo imports yaml currently.

**`$EDITOR` invocation:** standard pattern — `subprocess.run([os.environ.get("EDITOR", "vim"), tempfile_path])`. Test seam: inject the editor command (allows tests to use `true` or a fixture-writer).

## Surface 3: Structured change signaling

### Current S3 publish flow

| Step | File:line | Behavior |
|------|-----------|----------|
| 1. Bundle | `pipeline_hierarchy/bundler.py::bundle` (`publish.py:199`) | Build hierarchy dict from per-topic augmented files. **Currently stamps `generated_at` at line 184–186 of bundler.py.** |
| 2. Compute input_hash | `pipeline_hierarchy/publish.py::compute_publish_input_hash` (line 86) | Already excludes `generated_at` from the hash (G-29 local mitigation, see line 108). |
| 3. Skip check | `should_skip` from substrate | Looks up STAGE#publish_hierarchy#GLOBAL by `input_hash`. |
| 4. Run pre-upload gates | `run_gates(stage="publish", hierarchy=...)` (line 245) | parent_prefix, pii_scan, schema_validation. |
| 5. Generate canonical bytes + manifest | `pipeline_hierarchy/generator.py::generate` (`publish.py:284`) | **Re-stamps `generated_at` into hierarchy at line 66 of generator.py.** manifest gets its own `generated_at` at line 118. |
| 6. Write local | `write_local()` (`publish.py:127`) | Writes hierarchy.json, hierarchy.schema.json, manifest.json to `out/hierarchy/{version}/`. |
| 7. Upload to S3 | `upload_to_s3()` (`publish.py:136`) | **Currently 3 PutObjects:** `{version}/hierarchy.json`, `{version}/hierarchy.schema.json`, `latest/manifest.json`. **No `{version}/manifest.json`.** **No `latest/hierarchy.json` either.** |
| 8. Post-upload warn gates | `run_gates(stage="publish_post", ...)` (line 330) | schema_roundtrip. |
| 9. Write STAGE# complete | `write_complete()` (line 351) | The audit row for substrate. |

**Discovery worth surfacing to the planner:** the current `upload_to_s3()` writes only THREE objects, and `latest/` carries only `manifest.json` (not `latest/hierarchy.json`). D-11's locked write order calls for FIVE objects including a `{version}/manifest.json`. The current code does NOT write `{version}/manifest.json` to S3 — it only writes `latest/manifest.json`. This is a delta the planner must account for: Phase 11 adds the version-pinned manifest.json upload that wasn't there before, in addition to reordering and adding diff.json.

Cross-check: `docs/hierarchy-contract.md` per CONTEXT canonical refs should document current write order. Recommend the planner read it. The Phase 5 SUMMARY indicates the contract was originally specified as version-pinned-first + latest-manifest-last; the current code is consistent with that older contract, just thinner than D-11.

### Proposed write-order contract (D-11)

```python
def upload_to_s3(version, hierarchy, schema, manifest, diff, s3_client):
    # 1. Version-pinned hierarchy + schema + diff first (read-after-write consistent for new keys)
    s3_client.put_object(f"{version}/hierarchy.json", hierarchy)
    s3_client.put_object(f"{version}/hierarchy.schema.json", schema)
    s3_client.put_object(f"{version}/diff.json", diff)                    # NEW
    # 2. Version-pinned manifest
    s3_client.put_object(f"{version}/manifest.json", manifest_bytes)      # NEW (not currently uploaded)
    # 3. latest/manifest.json LAST, with Cache-Control
    s3_client.put_object(
        "latest/manifest.json", manifest_bytes,
        cache_control="max-age=60, must-revalidate"                       # NEW
    )
```

Note: spec §6 also mentions "any other future `latest/*` objects" for Cache-Control. Today only `latest/manifest.json` lives in `latest/`. If spotlight publish (`spotlight/publish.py:202–204`) also writes `latest/`, it deserves the same Cache-Control header — but that's spotlight, not hierarchy, and out of Phase 11 scope unless the planner stretches it.

### Cache-Control proposal for `latest/*`

`utils/s3_client.py::put_object` signature:

```python
def put_object(self, key: str, body: bytes,
               content_type: str = "application/json",
               cache_control: str | None = None) -> None:
    kwargs = {"Bucket": self.bucket, "Key": key, "Body": body, "ContentType": content_type}
    if cache_control is not None:
        kwargs["CacheControl"] = cache_control
    self._get_client().put_object(**kwargs)
```

Backwards-compatible (optional kwarg, default None). Affects every caller only if they pass it; current callers don't. Per spec §6 the value is `max-age=60, must-revalidate`.

### `diff.json` content + producer location

Per spec §6 + D-09 + D-12:

```json
{
  "diff_schema_version": "1.0.0",
  "from_version": "v2026-05-06",
  "to_version": "v2026-06-01",
  "taxonomy_version_changed": false,
  "added_subtopics": [],
  "removed_subtopics": [],
  "renamed_subtopics": [
    {"id": "...", "old_display_name": "...", "new_display_name": "..."}
  ],
  "reassigned_pmid_count": 312,
  "editorial_only": false
}
```

**Producer location:** new function in `pipeline_hierarchy/publish.py`, e.g., `compute_diff(prev_version, new_hierarchy, run_id, table, s3_client) -> dict`. Called between gate evaluation and `generate(...)` (or between `generate` and `upload_to_s3` — both work; the bytes don't depend on each other). The diff dict is then `json.dumps()`-encoded and passed into `upload_to_s3()`.

**Hybrid data sources (D-09):**

| Field | Source |
|-------|--------|
| `from_version` | The current `latest/manifest.json` → `version` (S3 GET before publish). |
| `to_version` | Current run's version (mint). |
| `taxonomy_version_changed` | Byte-compare prev `hierarchy.json`'s `taxonomy_version` vs new. |
| `added_subtopics` / `removed_subtopics` / `renamed_subtopics` | Byte-compare structural diff on the two hierarchy dicts. |
| `reassigned_pmid_count` | Query `STAGE#assign_subtopics#topic:*` rows where `run_id == current_run_id` (per D-13) and sum a `pmids_reassigned` field. **Open question:** does `assign_subtopics` currently emit a `pmids_reassigned` field on its STAGE# row? Search shows the assign-stage STAGE# row carries `records_written` but not specifically "reassigned vs first-time-assigned." Planner must decide whether to (a) add `pmids_reassigned` to the STAGE# row, or (b) define `reassigned_pmid_count` as `records_written` (looser but already available). See Open Q §OQ-3. |
| `editorial_only` | Derived: true iff only `renamed_subtopics` is non-empty AND no add/remove AND no taxonomy change AND `reassigned_pmid_count == 0`. |

**STAGE# `run_id` substrate addition (D-13):**

- `utils/stage_records.py` gains optional `run_id` kwarg on `build_complete_record`, `build_skipped_record`, `build_failed_record`. Backwards-compatible — no existing caller passes it; the field is omitted from the row if not provided.
- `pipeline_cold/run.py::main()` generates `run_id = str(uuid.uuid4())` at top, threads it via env var or argv into per-stage subprocess invocations. Each stage's STAGE# writer (the `score_publications.py`, `assign_subtopics.py`, etc. that wraps `write_complete`) reads it and passes through.
- Hot-path STAGE# rows (which carry SFN `execution_arn`) are unaffected — they pass nothing, field is omitted.

### G-29 fix scope

**Files to change:**

1. **`pipeline_hierarchy/bundler.py:183–186`** — remove the `generated_at` stamp from the returned bundle dict. The `generated_at` parameter on `bundle()` becomes unused; either drop it entirely (breaking the public function signature) or keep it as a no-op-but-accepted kwarg for backwards-compat. **Recommendation:** drop it entirely. There are no in-repo callers other than `publish.py`, and Phase 11 is the right moment for a clean break.

2. **`pipeline_hierarchy/generator.py:60–67`** — currently `build_hierarchy()` re-stamps `hierarchy["generated_at"]`. Change to: don't write `generated_at` into the hierarchy dict. Keep the `generated_at` argument because it's used to derive the default `version` label at line 111. Just don't propagate it into the hierarchy structure.

3. **`pipeline_hierarchy/generator.py:114–121`** — manifest construction. **Keep** `"generated_at": resolved_generated_at` in the manifest (per D-14, manifest.json continues to carry it; that's spec-correct).

4. **`docs/hierarchy.schema.json`** — if `generated_at` is currently a required field on hierarchy.json, mark it optional (or remove from schema). Otherwise `schema_validation` gate will block every Phase 11 publish.

5. **`pipeline_hierarchy/publish.py::compute_publish_input_hash`** (line 86) — currently strips `generated_at` defensively (lines 108, 115 — "G-29 local mitigation"). After D-14, the field doesn't exist in the bundle so the strip is a no-op. Comment can be updated to reflect that G-29 is now fixed upstream. The strip itself can stay (defensive belt-and-suspenders) or be removed. Recommendation: remove to keep the code honest.

**Other nondeterminism in the hashed bytes (audit):** searched `generator.py`, `bundler.py`, `publish.py`, generator output. Other potential sources:

- Topic dict insertion order: `_sort_topics` at `generator.py:32` sorts topic keys explicitly — deterministic.
- `excluded_topics`: read in deterministic order from `config/excluded_topics.json` (file order, then stored as list).
- Per-subtopic field order: from `_build_subtopic()` in `bundler.py:80`, iteration follows `_SUBTOPIC_REQUIRED` tuple order — deterministic.
- `see_also`: defaults to `[]` per generator.py:67. If `see_also` ever gets populated dynamically (it doesn't today; `generate_see_also.py` is a separate tool), it'd need stable ordering.

**Conclusion:** `generated_at` is the only embedded nondeterminism. After D-14, `hierarchy.json` is bit-stable across runs with identical inputs.

**`STAGE#g29_cutover#GLOBAL` audit row (D-16):**

```python
# Written from pipeline_hierarchy/publish.py once, on the first publish after D-14 lands
{
  "PK": "STAGE#g29_cutover#GLOBAL",
  "SK": f"RUN#{started_at}",
  "previous_publish_sha": "<sha256 of last pre-G29 hierarchy.json>",
  "new_publish_sha": "<sha256 of first post-G29 hierarchy.json>",
  "hierarchy_version_at_cutover": "<the version label of this publish>",
  "run_id": "<cold-run UUID>",
  "started_at": "...",
  "completed_at": "...",
}
```

Detection of "this is the first post-G29 publish" — either (a) a code-side flag the operator passes (`--g29-cutover` argv to publish.py, used exactly once), or (b) infer from absence of any prior `STAGE#g29_cutover#GLOBAL` row. Recommendation: option (a). It's an operator-known one-time event; relying on row absence couples the audit row's existence to the cutover semantic, which is fragile (what if the row is deleted?).

## Validation Architecture (Nyquist)

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (version not pinned in requirements.txt — use whatever's already installed) |
| Config file | none (uses pytest defaults; tests at repo root AND under `tests/`) |
| Quick run command | `pytest tests/test_<file>.py -x` |
| Full suite command | `pytest tests/ test_*.py -x` |

**Tests live in two places:** `tests/` (substrate, pipeline, gates) and repo root `test_*.py` (spotlight pipeline). Phase 11 new tests should follow the substrate convention and land in `tests/`.

**No moto, no localstack.** Boto3 calls are stubbed with `botocore.stub.Stubber` (see `tests/test_pipeline_hot_orchestrator.py`) or `unittest.mock.MagicMock` (see `tests/test_stage_records.py`). Phase 11 should not introduce a new mocking dependency.

### Phase Requirements → Test Map

| Surface | Behavior to Test | Test Type | File (proposed) | Wave 0? |
|---------|------------------|-----------|-----------------|---------|
| S1 | `update_activity_subtopics` writes `hierarchy_version` in the SET expression | unit | `utils/test_dynamodb_subtopic_migration.py` (extend) | exists |
| S1 | `assign_subtopics.run()` threads `hierarchy_version` to the writer | unit | `tests/test_assign_subtopics_stage.py` (extend) | exists |
| S1 | Backfill: PROCESSING# join resolves taxonomy_version → hierarchy_version | unit | `utils/test_dynamodb_hierarchy_backfill.py` | ❌ Wave 0 |
| S1 | Backfill: orphan rows get `0.0.0-orphan` and two-bucket count | unit | (same as above) | ❌ Wave 0 |
| S1 | Rotation history migration: legacy PK → new PK rewrite (PutItem + DeleteItem) | unit | `tests/test_spotlight_history_migration.py` | ❌ Wave 0 |
| S1 | Rotation history migration: malformed publish_id → `0.0.0-orphan` + bucket counted | unit | (same) | ❌ Wave 0 |
| S1 | `fetch_history` builds new key shape `SPOTLIGHT_HISTORY#{ver}#{sid}` | unit | `test_spotlight_rotation_selector.py` (extend) | exists |
| S1 | `update_history` writes new key shape | unit | `test_spotlight_rotation_selector.py` (extend) | exists |
| S1 | `_ingest_responses` parser correctly extracts subtopic_id from new key shape | unit | `test_spotlight_rotation_selector.py` (extend) | exists |
| S1 | `STAGE#hierarchy_version_cutover#GLOBAL` row shape | unit | `tests/test_pipeline_cold_run.py` (extend) | exists |
| S2 | Validator: reviewer_cwid regex (positive + negative) | unit | `tests/test_review_validator.py` | ❌ Wave 0 |
| S2 | Validator: rationale length ≥ 40 | unit | (same) | ❌ Wave 0 |
| S2 | Validator: decision ∈ {approve,reject} | unit | (same) | ❌ Wave 0 |
| S2 | Validator: proposed_artifact_uri HEAD check (success + 404) | unit (S3 stubbed) | (same) | ❌ Wave 0 |
| S2 | Validator: refuses approval when any gate failed | unit (DDB stubbed) | (same) | ❌ Wave 0 |
| S2 | CLI `approve` happy path: pre-populated YAML → save → PutItem | integration | `tests/test_review_cli.py` | ❌ Wave 0 |
| S2 | CLI `validate` subcommand: pure-file validation, no DDB write | integration | (same) | ❌ Wave 0 |
| S2 | CLI: reviewer_cwid from `~/.reciterai/config.yaml` | unit | (same) | ❌ Wave 0 |
| S2 | CLI: reviewer_cwid from env override | unit | (same) | ❌ Wave 0 |
| S3 | `bundler.bundle()` no longer stamps `generated_at` into hierarchy dict | unit | `tests/test_hierarchy_bundler.py` (extend) | exists |
| S3 | `generator.build_hierarchy()` no longer writes `generated_at` to hierarchy | unit | `tests/test_hierarchy_publisher.py` (extend) | exists |
| S3 | `manifest.json` still carries `generated_at` | unit | `tests/test_hierarchy_publisher.py` (extend) | exists |
| S3 | **Reproducibility:** same inputs → identical hierarchy bytes (sha256 match across runs) | unit | `tests/test_hierarchy_reproducibility.py` | ❌ Wave 0 |
| S3 | `compute_diff()` byte-compare structural fields | unit | `tests/test_publish_diff.py` | ❌ Wave 0 |
| S3 | `compute_diff()` STAGE# query filtered by run_id | unit (DDB stubbed) | (same) | ❌ Wave 0 |
| S3 | `compute_diff()` editorial_only flag derivation | unit | (same) | ❌ Wave 0 |
| S3 | `upload_to_s3()` write order matches D-11 (assert call order on stubbed S3) | unit | `tests/test_publish_integration.py` (extend) | exists |
| S3 | `Cache-Control: max-age=60, must-revalidate` on `latest/manifest.json` | unit | (same) | exists |
| S3 | `S3HierarchyClient.put_object(cache_control=...)` passes through to boto3 | unit | `utils/test_s3_client.py` | ❌ Wave 0 |
| S3 | `STAGE#g29_cutover#GLOBAL` row shape | unit | `tests/test_publish_integration.py` (extend) | exists |
| S3 | `utils/stage_records.py` `run_id` optional kwarg is backwards-compatible | unit | `tests/test_stage_records.py` (extend) | exists |
| S3 | First-ever-publish edge case for diff.json (whichever option wins per O-01) | unit | `tests/test_publish_diff.py` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_<file>.py -x` (the one file the task touches).
- **Per wave merge:** `pytest tests/ test_*.py -x` (full suite).
- **Phase gate:** Full suite green before `/gsd-verify-work`.

### Wave 0 Gaps

- [ ] `tests/test_review_validator.py` — pure validator function tests (S2)
- [ ] `tests/test_review_cli.py` — CLI integration with $EDITOR seam (S2)
- [ ] `utils/test_dynamodb_hierarchy_backfill.py` — backfill migration logic (S1)
- [ ] `tests/test_spotlight_history_migration.py` — rotation history rewrite (S1)
- [ ] `tests/test_hierarchy_reproducibility.py` — bit-stable hierarchy across runs (S3, G-36 prerequisite)
- [ ] `tests/test_publish_diff.py` — diff.json producer (S3)
- [ ] `utils/test_s3_client.py` — put_object cache_control kwarg pass-through (S3)
- [ ] **Dep add:** `pyyaml>=6.0.1` in `requirements.txt`

## Open Questions

### OQ-1: How does the activity-row backfill map `taxonomy_version` → `hierarchy_version`?

**What we know:** Spec §3 says "every activity record stamps `hierarchy_version`". D-02 says the migration joins to PROCESSING# for the `taxonomy_version` active at write time. The mapping from one to the other is undefined.

**The gap:** taxonomy_version is e.g. `taxonomy_v2`; hierarchy_version is e.g. `v2026-05-12`. They're different vocabularies.

**Defensible options:**
- (a) Stamp `taxonomy_version` as `hierarchy_version` for the backfill window (acknowledge in code comments that pre-Phase-11 rows carry a non-canonical hierarchy_version value).
- (b) Maintain a hardcoded map `{taxonomy_v1: "v0.0.0-pre-phase-11", taxonomy_v2: "v0.0.0-pre-phase-11"}` and stamp all pre-Phase-11 rows with a single legacy sentinel. Loses precision but unambiguous.
- (c) Don't stamp — leave pre-Phase-11 activity rows without `hierarchy_version` and rely on consumers to treat `attribute_not_exists` as "pre-Phase-11" (essentially the same as option (b) without an explicit row update).

**Recommendation:** flag for discuss-phase IF unclear; otherwise planner picks (b) with a single `v0.0.0-pre-phase-11` sentinel value. The historical activity-row hierarchy_version provenance is low-value (no consumer asks "which exact hierarchy version was this PMID assigned under three months ago?") — a coarse sentinel is honest and cheap.

### OQ-2: Are `publish_id` and `hierarchy_version` the same thing in rotation history writes?

**What we know:** `publish_id` is passed into `update_history` (`spotlight/history_writer.py:94`) and currently equals the spotlight publish `version = f"v{date.today().isoformat()}"`. `hierarchy_version` per D-04 keys the new rotation PK.

**The gap:** spotlight publishes monthly (Phase 10 cadence) but hierarchy publishes can be operator-driven any time. A given spotlight publish_id corresponds to whatever `hierarchy_version` was `latest` when that spotlight ran. They're not generally identical.

**The decision needed:** When `update_history` fires, what `hierarchy_version` does the new PK carry?
- (a) The current `latest/manifest.json` version at spotlight-publish time (one S3 GET to resolve).
- (b) Thread `hierarchy_version` through the spotlight pipeline from `backfill_spotlight.py` or `spotlight/publish.py` similar to how the hierarchy is threaded.
- (c) Snapshot the hierarchy_version into the spotlight artifact itself at publish time and read it back when writing history.

**Recommendation:** flag for discuss-phase. This is a coupling decision between the spotlight pipeline and the hierarchy pipeline that CONTEXT didn't address. Spec §3 implies (b) — "rotation state is keyed by `(cwid, hierarchy_version)`" — but the mechanics of getting the version to the writer aren't locked.

### OQ-3: Does the assign-stage STAGE# row carry `pmids_reassigned` today, or only `records_written`?

**What we know:** `pipeline_hierarchy/publish.py:362` passes `records_written=manifest["artifact_bytes"]`. The assign-stage equivalent would carry `records_written=<count of activity rows updated>`. There's no distinct "reassigned vs first-time-assigned" counter in the current STAGE# write.

**The gap:** D-09's `reassigned_pmid_count` for `diff.json` needs the precise number of PMIDs whose `primary_subtopic_id` CHANGED compared to a prior run — not just "rows touched." A first-time assignment isn't a reassignment.

**Defensible options:**
- (a) Add a `pmids_reassigned` field on the assign-stage STAGE# row, computed inside `assign_subtopics.run()` by comparing the new primary_subtopic_id against whatever's already in the row before UpdateItem (read-then-write pattern). One extra GetItem per row → not free.
- (b) Define `reassigned_pmid_count := records_written` in `diff.json` (looser, available today, over-counts when a cold run also touches first-time-assigned PMIDs).
- (c) Compute reassignment via a separate pre-write query batch and accumulate the count in the stage.

**Recommendation:** Planner picks. (b) is YAGNI-honest if the looser definition is acceptable for SPS; (a) is the precise but costlier path. Flag for discuss-phase if precision is required.

### OQ-4: Cache-Control on `latest/spotlight.json` and `latest/spotlight.schema.json` too?

**What we know:** Spec §6 says "any other future `latest/*` objects" get `Cache-Control: max-age=60, must-revalidate`. Today `spotlight/publish.py:202–204` writes `latest/spotlight.json`, `latest/spotlight.schema.json`, and `latest/manifest.json` for the spotlight artifact. Phase 11 CONTEXT D-11 mentions only the hierarchy `latest/manifest.json`.

**The gap:** does Phase 11 scope include the spotlight pipeline's `latest/*` Cache-Control headers, or only hierarchy?

**Recommendation:** ask discuss-phase. If yes, it's a one-line change at `spotlight/publish.py:202–204` after the `S3HierarchyClient.put_object` signature change. If no, defer to a followup. Either is fine; flag the choice.

## Risks & Landmines

### R1: Backfill correctness on rotation history (CONTEXT-flagged)

Misparsing `last_shown_publish_id` (e.g., a row carries a publish_id from a format that pre-dates `v{ISO-date}` convention) silently stamps the wrong `hierarchy_version` on a rotation row. Per CONTEXT §Risks, mitigated by: fail-loud on unrecognized format, per-row before/after diff log, dry-run default with `--commit` required, operator review. Planner should encode this discipline in the migration script's test plan.

### R2: ID instability across recomputes (CONTEXT-flagged)

D-04 chose Option-1 (one-shot rewrite, no fallback) BECAUSE subtopic IDs are slug-derived from labels and not stable across recomputes (`discover_subtopics.py:91`). The risk this creates: after a cold-run mints `vN+1`, the migration runs against `vN`'s rotation history rows, but their subtopic_ids are mostly different from `vN+1`'s subtopic_ids. Rotation history is effectively reset per hierarchy_version for any subtopic whose ID changed (which is "most of them" per CONTEXT). This is **correct behavior** per D-04 but the planner must make sure the cold-run runbook spells out: "rotation will look like a cold start after a hierarchy cutover; this is expected."

### R3: Write-order race between diff.json and manifest (CONTEXT-relevant)

If `latest/manifest.json` is uploaded before `{version}/diff.json` is fully consistent (S3 read-after-write is consistent for new keys, so this is unlikely but a race against propagation-delay in the absence of a CDN), a consumer poll could see the new sha and miss the diff. D-11 explicitly orders diff before manifest to prevent this. The planner's write-order test (asserting boto3 call order on a stubbed S3) is the durable check.

### R4: PyYAML dependency add

New dep is small but real. Planner should add it to `requirements.txt` in the same commit as `review/cli.py` lands, and the test plan should include a smoke-import test (`import yaml` in the test module) so a missing dep gets caught at test time rather than at first operator run.

### R5: `~/.reciterai/config.yaml` is greenfield — first-run UX

The CLI must give an actionable error if neither `~/.reciterai/config.yaml` nor `RECITERAI_REVIEWER_CWID` is set. The error message should print the exact YAML template to write, and the path to write it to. Otherwise operators hit a wall on first use.

### R6: STAGE# `run_id` substrate change has many callers

D-13 substrate change is backwards-compatible (optional kwarg, omitted if not provided) but `utils/stage_records.py` is consumed by every stage script in the repo. The Phase 11 plan must include regression coverage that none of the existing stage callers regress when `run_id` is not threaded. `tests/test_stage_records.py` exists and covers builder shape; extend it for the new kwarg.

### R7: G-29 cutover ETL coordination (CONTEXT-flagged, D-15)

The first publish after G-29 lands flips `manifest.sha256` for SPS even though hierarchy CONTENT is unchanged. SPS will run a wholesale hierarchy ETL once. Per D-15 this is correct behavior, but it cascades into search reindex, faculty profile rebuilds, push notifications, cache invalidations. Planner must schedule the 30-min coordination conversation with the SPS hierarchy-ETL owner as a Phase 11 task (not deferred to runbook).

### R8: O-01 first-ever-publish risk

If the planner picks "omit diff.json entirely on first-ever-publish" and a downstream tool assumes `diff.json` is always present, it'll regress silently. Whichever option wins, the contract must be explicit in `docs/hierarchy-contract.md` and a test must cover the chosen behavior.

### R9: Phase prompt's `src/reciterai/` hint was wrong

The phase prompt suggested paths like `src/reciterai/` and `src/reciterai/spotlight/`. The actual layout is flat at repo root. Plans must use absolute repo-relative paths like `assign_subtopics.py`, `spotlight/history_writer.py`, NOT `src/reciterai/...`. Trivial but worth flagging — copy-pasting from the prompt would silently bake the wrong path.

## Sources

### Primary (HIGH confidence — read directly from codebase)

- `assign_subtopics.py:600–656` — write-site for `update_activity_subtopics` call (verified)
- `utils/dynamodb_subtopic_migration.py:37–70` — the single write function for subtopic fields (verified)
- `utils/dynamodb_helpers.py:267–320` — `mark_processing` writer, `get_processing_status` reader (verified)
- `spotlight/history_writer.py:93–127` — rotation history writer (verified)
- `spotlight/rotation_selector.py:135–202` — rotation history reader + parser (verified)
- `spotlight/publish.py:144–225` — spotlight publish flow incl. update_history call (verified)
- `pipeline_hierarchy/publish.py:86–366` — hierarchy publish flow, current write order, STAGE# substrate integration (verified)
- `pipeline_hierarchy/bundler.py:140–195` — bundler with `generated_at` stamp at lines 184–186 (verified)
- `pipeline_hierarchy/generator.py:38–122` — generator with `generated_at` stamp at line 66 and manifest construction at 114–121 (verified)
- `pipeline_cold/run.py:1–347` — cold-path orchestrator entry point for `run_id` threading (D-13) and `STAGE#hierarchy_version_cutover` write (D-06) (verified)
- `utils/stage_records.py:1–314` — STAGE# substrate; `build_*_record` builders + `write_*` writers (verified)
- `gates/registry.py:1–138` — gate registration framework (verified)
- `gates/cli.py:1–172` — gates CLI for understanding the existing CLI pattern (verified)
- `utils/s3_client.py:1–160` — S3 client; `put_object` currently doesn't accept `CacheControl` (verified)
- `discover_subtopics.py:88–93` — unstable-ID warning (CONTEXT D-06 reference) (verified)
- `docs/RECITERAI-SPEC.md` §3, §4, §5, §6 — canonical spec text for Decisions 2/3/4/5 + G-29 placement (verified)
- `.planning/phases/10-hot-cold-path-split/10-SUMMARY.md` — Phase 10 SUMMARY (verified)
- `.planning/phases/11-versioning-review-diff/11-CONTEXT.md` — locked decisions D-01..D-19 (verified)
- `requirements.txt` — no PyYAML, no moto (verified)

### Secondary (MEDIUM confidence)

- (none — all material is in-repo and directly verified)

### Tertiary (LOW confidence)

- (none)

## Metadata

**Confidence breakdown:**

- Surface 1 (hierarchy_version stamping + rotation rewrite): HIGH — every write/read site is in-repo and located. Only OQ-1 and OQ-2 are open.
- Surface 2 (REVIEW# + CLI): HIGH — spec + CONTEXT define the contract; module is greenfield, dependency add is a known pattern. No ambiguity worth flagging beyond the implementation-seam choice O-02 (already deferred to planner).
- Surface 3 (diff.json + write-order + G-29): HIGH — publish.py is in-hand; the deltas are small and well-bounded. OQ-3 (pmids_reassigned definition) and OQ-4 (Cache-Control scope for spotlight) are scoped decisions the planner needs to make.

**Research date:** 2026-05-12
**Valid until:** 2026-06-11 (30 days; the codebase is stable; spec is stable; only operational decisions can shift)

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The sole write site for `primary_subtopic_id`/`subtopic_ids`/`subtopic_confidences` is `utils/dynamodb_subtopic_migration.py::update_activity_subtopics`. | Surface 1 write-site inventory | [VERIFIED via grep `primary_subtopic_id` across the codebase — every match is either test data, doc text, or a call to/from `update_activity_subtopics`. No other writer exists.] No risk. |
| A2 | `assign_subtopics.py:642–648` is the only caller of `update_activity_subtopics`. | Surface 1 | [VERIFIED via grep — `assign_subtopics.py:642` is the only production caller; `utils/test_dynamodb_subtopic_migration.py` is the test caller.] No risk. |
| A3 | The cold path threads `run_id` via argv into subprocess stages. | Surface 3, D-13 plumbing | [ASSUMED] If the planner instead uses an env var (`RECITERAI_COLD_RUN_ID`), the test seam pattern differs slightly. Low risk; both work. |
| A4 | The activity-row backfill maps `taxonomy_version` → `hierarchy_version` via a hardcoded sentinel (`v0.0.0-pre-phase-11`) rather than a per-taxonomy-version lookup. | Surface 1 backfill | [ASSUMED — flagged as OQ-1.] Picking the wrong mapping means historical provenance is misrepresented. Coarse-sentinel option is cheapest and defensible. |
| A5 | `publish_id` in rotation history writes can be made identical to `hierarchy_version` via a small plumbing change in `spotlight/publish.py`. | Surface 1, OQ-2 | [ASSUMED — flagged as OQ-2.] Implementation depends on whether spotlight reads `latest/manifest.json` for the current hierarchy_version at publish time. |
| A6 | `assign_subtopics` STAGE# row does NOT currently carry a `pmids_reassigned` field distinct from `records_written`. | Surface 3, OQ-3 | [VERIFIED via grep `pmids_reassigned` — zero hits.] No risk. The decision is whether to add it. |
| A7 | The current `upload_to_s3` writes only 3 objects (no `{version}/manifest.json`). | Surface 3 current publish flow | [VERIFIED — `pipeline_hierarchy/publish.py:136–143` puts `{version}/hierarchy.json`, `{version}/hierarchy.schema.json`, and `latest/manifest.json` only.] No risk. |
| A8 | PyYAML is the right YAML lib (not ruamel.yaml or others). | Surface 2 | [ASSUMED] PyYAML is the dominant Python YAML library and is unambiguously the right pick for a non-roundtrip use case. Low risk. |
| A9 | The `~/.reciterai/config.yaml` file is greenfield — no current code reads it. | Surface 2 | [VERIFIED via grep `.reciterai|RECITERAI_REVIEWER_CWID|config.yaml` — zero in-repo hits.] No risk. |

## RESEARCH COMPLETE
