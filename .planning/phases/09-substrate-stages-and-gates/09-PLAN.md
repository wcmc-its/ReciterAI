# Phase 9 — Execution substrate: content-addressed stages + gates framework

**Milestone:** M2 — SPS-feeding service
**Spec source:** [docs/RECITERAI-SPEC.md §5 (Decision 4) + §7 (Decision 6)](../../../docs/RECITERAI-SPEC.md)
**Estimated effort:** 5–8 working days
**Status:** Plan — awaiting approval

## Goal

Build the two pieces of substrate that everything downstream stands on:

1. **Content-addressed `STAGE#` records** — every pipeline stage writes a DynamoDB completion record keyed by `input_hash`. A subsequent run with the same `input_hash` skips and writes a `skipped` row that still carries `duration_ms` and `cost_estimate_usd`. Memoization, not Bazel-strict reproducibility (spec §5).
2. **Registered quality gates** — a `gates/` package where checks are registered against stages by name, with severity (`block` vs `warn`), runnable both inline (during a stage) and ad-hoc (`python -m gates --stage publish --hierarchy v2026-05-12`). Spec §7.

Phase 9 ships the substrate plus a **single proof-of-substrate integration**: wire `pipeline_hierarchy.publish` into `STAGE#` records and convert its current schema-validation into a registered gate. Other stages (scoring, subtopic assignment, rollups, spotlight) integrate in Phase 10+12; pre-decomposing them now is guessing per the spec's sequencing rule.

## Non-goals

- Integrating every pipeline stage with `STAGE#` records (Phase 10 hot/cold split work).
- Building a dashboard for `STAGE#` or gate results (deferred to operator load).
- Implementing the diff_evaluator stage that produces `DRIFT#` sentinels (Phase 10).
- The `diff.json` change-signal artifact (Phase 11, Decision 5).
- Migrating the existing `find_parent_prefix_violation` to a registered gate (low value — it already runs inline in `relabel_subtopics.py`; revisit when `gates/` has multiple consumers).

## Why now

Spec §12 sequencing: substrate before Phase 10 (hot/cold split). Phase 10's hot path is what needs `STAGE#` records to mean "what changed since last run." Without substrate, Phase 10 either uses a degenerate `last_successful_run_at` timestamp (slip path) or stalls. The end-of-week-2 slip checkpoint below is what decides between substrate and degenerate.

## Approach

### Files (new)

```
utils/
├── stage_records.py        # STAGE# DynamoDB CRUD, input_hash compute, skip helper
└── bedrock_client.py       # MODEL_IDS centralization (G-35) — touch existing file

gates/
├── __init__.py
├── registry.py             # @register_gate decorator, GateResult, run_gates runner
├── parent_prefix.py        # gate wrapper over existing relabel_subtopics.find_parent_prefix_violation
├── pii.py                  # PII scan (no cwid_*, no email-shaped strings) on hierarchy artifact
├── schema_roundtrip.py     # post-publish: fetch latest/manifest.json + version, re-validate
└── cli.py                  # python -m gates --stage <name> [args]

tests/
├── test_stage_records.py   # STAGE# CRUD, skip-on-hash-match, skip rows carry cost+duration
├── test_gates_registry.py  # registration, severity dispatch, --force semantics
├── test_gate_parent_prefix.py
├── test_gate_pii.py
└── test_gate_schema_roundtrip.py
```

### Files (modified)

```
pipeline_hierarchy/publish.py
  - Compute input_hash from the bundled hierarchy bytes + bundler version + model IDs
  - Query stage_records.should_skip; if hit, write skipped row and exit 0
  - Run gates registered for stage="publish" before S3 upload (block on any block-severity gate)
  - Write STAGE#publish_hierarchy complete row on success
  - Existing --dry-run still skips S3 upload AND skips writing the STAGE# row

pipeline_hierarchy/generator.py
  - No behavioral change; jsonschema validation stays inline (it's already correct)
    — but ALSO becomes a registered schema_validation gate so the round-trip gate
    (Decision 6) can reuse the same check post-publish
```

### DynamoDB table

Name: `reciterai_stage_records`. Provisioned via existing infra patterns (see `utils/dynamodb_helpers.py`); add the table create to that module's `ensure_tables()` helper if one exists, otherwise document the manual `aws dynamodb create-table` call in `docs/data-model-and-queries.md`.

```
PK: "STAGE#{stage_name}#{scope}"     # e.g. STAGE#publish_hierarchy#GLOBAL
SK: "RUN#{started_at}"               # ISO8601 sort key, lexicographic = chronological
Attributes:
  input_hash:           string (the content-addressed key)
  status:               "complete" | "skipped" | "failed"
  skip_reason:          string | null     (set only when status=skipped)
  started_at:           ISO8601
  completed_at:         ISO8601
  duration_ms:          number
  cost_estimate_usd:    number     (constant 0.0000003 on skip per spec §5)
  output_pointer:       "s3://..." | "ddb://..." | null
  records_written:      integer | null
  model_ids_snapshot:   list[string]      (the model IDs that were part of input_hash)
  force_reason:         string | null     (when a gate was overridden)
```

GSI on `input_hash` for "has this exact input ever been processed" queries.

### `input_hash` schema for the publish stage

```python
input_hash = sha256(json.dumps({
    "hierarchy_full_canonical_bytes_sha256": <existing publisher sha256, but computed
                                              over a representation that EXCLUDES
                                              generated_at — see spec G-29>,
    "schema_sha256":                         hashlib.sha256 of docs/hierarchy.schema.json bytes,
    "excluded_topics_sha256":                hashlib.sha256 of config/excluded_topics.json bytes,
    "model_ids": MODEL_IDS["screening"] + MODEL_IDS["scoring"] + MODEL_IDS["lede"],
    "bundler_version":                       version constant in pipeline_hierarchy/__init__.py,
}, sort_keys=True).encode())
```

Carrying model IDs in the hash even when publish doesn't call models is intentional — keeps the input_hash schema uniform across stages and means a model swap invalidates publish-cache for free (cheap correctness).

The "excludes generated_at" sub-detail addresses G-29 partially within Phase 9 by computing the substrate's input_hash from a `generated_at`-free representation. The full G-29 fix (move `generated_at` out of `hierarchy.json` entirely, or add `content_sha256` to manifest) lands in Phase 11. Phase 9 unblocks itself without depending on that.

### Gate registry shape

```python
# gates/registry.py
from dataclasses import dataclass

@dataclass
class GateResult:
    name: str
    passed: bool
    severity: str          # "block" | "warn"
    summary: str           # human-readable one-liner
    details: dict | None   # structured per-gate

_REGISTRY: list[Gate] = []

def register_gate(*, stage: str, severity: str = "block"):
    def deco(fn): ...
    return deco

def run_gates(*, stage: str, **kwargs) -> list[GateResult]:
    """Run all gates registered for `stage`. Returns results in registration order."""

def any_blocked(results: list[GateResult]) -> bool: ...
```

`gates/cli.py` exposes the same as `python -m gates --stage publish --hierarchy v2026-05-12` for ad-hoc runs against a published version. `--force` accepted with a required `--force-reason "..."` that's logged into the next `STAGE#` write.

### Initial gates registered in Phase 9

| Gate | Stage | Severity | Notes |
|---|---|---|---|
| `schema_validation` | `publish` | `block` | Wraps existing `pipeline_hierarchy.generator.validate` |
| `parent_prefix` | `publish` | `block` | Bundle-level rescan using the existing `relabel_subtopics.find_parent_prefix_violation`; redundant with the inline relabel-time check but cheap insurance |
| `pii_scan` | `publish` | `block` | New: regex sweep for `cwid_[a-z]+\d+`, email pattern, any `personIdentifier` key |
| `schema_roundtrip` | `publish_post` | `warn` | After upload, fetch the just-published artifact + schema and re-validate (catches a torn upload) |

Other gates (coverage, rollup reconciliation, critic rejection rate) need stages that don't exist yet — they get registered when Phase 10 wires their consumer stages in.

## Tasks (ordered)

1. **G-35 centralization** — add `MODEL_IDS` dict to `utils/bedrock_client.py`; migrate the five scattered model-ID references to read from it. One commit. Tests: `test_bedrock_model_ids.py` asserts dict keys are stable and each script imports the right name.
2. **`utils/stage_records.py`** — CRUD + `compute_input_hash` helper + `should_skip` + write helpers (`write_complete`, `write_skipped`, `write_failed`). Skip rows carry `cost_estimate_usd = 0.0000003`. Tests: full CRUD round-trip, skip semantics, status enum.
3. **DynamoDB table provision** — extend `utils/dynamodb_helpers.py` to ensure `reciterai_stage_records` exists; document the schema in `docs/data-model-and-queries.md`. Provision against the dev/staging account first; production write deferred to step 5 of Phase 10 prep.
4. **`gates/registry.py` + GateResult + decorator** — minimal framework. Tests: register / run / severity dispatch / `any_blocked` semantics.
5. **First registered gate: `schema_validation`** — port the inline `validate()` call from `generator.py` into a registered gate. Both call sites coexist initially (inline + registered) so nothing regresses; remove the inline call once `publish.py` runs gates explicitly.
6. **`parent_prefix` gate** — wraps `relabel_subtopics.find_parent_prefix_violation` so it can rescan the bundled hierarchy at publish time.
7. **`pii_scan` gate** — new. Regex `cwid_[a-z]+\d+`, RFC-5322-ish email pattern, scan for the literal key `personIdentifier`. False-positive analysis on `v2026-05-12` artifact to set the regex tightness before landing.
8. **`schema_roundtrip` gate** — `warn`-severity. Runs only after a real publish (skipped under `--dry-run`). Fetches `latest/manifest.json`, then the artifact + schema at `manifest.version`, then re-validates. Catches torn uploads / S3 eventual-consistency cases.
9. **Wire `publish.py` into the substrate** — compute `input_hash` per the schema above, `should_skip` check, run `block`-severity gates pre-upload, run `warn`-severity gates post-upload, write `STAGE#publish_hierarchy` record. End-to-end test against today's `v2026-05-12` inputs: first run writes `complete`, second run writes `skipped`, both rows present in the table.
10. **`gates/cli.py`** — `python -m gates --stage publish [--hierarchy <version>]`. Reads from S3 or local `out/`; produces a JSON report; non-zero exit if any `block` gate fails (or `--force` with `--force-reason` is set).
11. **Docs** — `pipeline_hierarchy/README.md` updates to mention substrate integration; new `docs/stage-records-and-gates.md` describes the framework for future stage authors.

## Verification (goal-backward)

A re-run of `python -m pipeline_hierarchy.publish` against unchanged augmented files produces:

- A `STAGE#publish_hierarchy` row with `status: "skipped"`, `skip_reason: "input_hash unchanged since <ts>"`, `duration_ms` measured, `cost_estimate_usd: 0.0000003`.
- No S3 PutObject calls.
- Exit code 0.

A run against augmented files where one subtopic's `display_name` has been hand-edited to `"Microbiome & ..."` (a parent-prefix violation) produces:

- The `parent_prefix` gate fails with severity `block`.
- A `STAGE#publish_hierarchy` row with `status: "failed"`, structured details on which subtopics violated, no S3 PutObject.
- Non-zero exit.

A run with `--force --force-reason "emergency rollback override"` produces:

- A `STAGE#publish_hierarchy` row with `status: "complete"` AND `force_reason` populated.
- S3 PutObject proceeds.

## Slip checkpoint (per spec §12)

**Watch date:** end of working-day 5 of Phase 9 execution (calendar date filled in when execution begins).

**On-track criteria at the checkpoint:**
- Tasks 1–4 complete (G-35 + stage_records + table + registry).
- Tasks 5–6 underway (first gates implemented or in PR).
- A passing test for `should_skip` exists.
- No unresolved scope creep from the non-goals list.

**Off-track action:** branch the degenerate path for Phase 10. The hot path uses `last_successful_run_at` as its skip signal, no `STAGE#` records, no gates framework. Ship Phase 10 against the degenerate substrate; eat the refactor when Phase 9 substrate later lands. Owner of the slip-decision: the operator who shipped this phase (named in the SUMMARY.md when execution begins).

## Out-of-scope and explicitly deferred

- Cost-telemetry dashboards consuming `STAGE#` records (Phase 12 hygiene).
- `DRIFT#` sentinel records, the drift evaluator stage, and Slack/GitHub alerting (Phase 10).
- The `diff.json` artifact alongside `manifest.json` (Phase 11).
- Versioned reads on the SPS side (deferred per spec §3 — revisit at the named triggers).
- Integration of `score_publications.py`, `assign_subtopics.py`, `rollup_by_cwid.py`, `backfill_spotlight.py` into `STAGE#` records (Phase 10).

## Risk register

| Risk | Mitigation |
|---|---|
| Re-stamping `generated_at` makes input_hash unstable | Per the input_hash schema above, hash is computed over a `generated_at`-free representation. Phase 11 finishes the G-29 fix. |
| `parent_prefix` gate runs redundantly with relabel-time check | Cheap insurance; remove the inline relabel-time check only after the gate has proven itself across one full cold-run cycle. |
| `pii_scan` regex too strict → false positives blocking legitimate publishes | Run pre-merge against `v2026-05-12` and any prior published artifact to verify zero false positives before flipping severity to `block`. |
| `should_skip` returns true on a stale row that shouldn't qualify | `input_hash` includes `bundler_version` (a code-version sentinel). Bumping the constant invalidates all prior skips. Bake the version-bump into any Phase 9+ commit that changes the hash inputs. |
| DynamoDB table provisioning differs across dev/staging/prod | Phase 9 provisions dev/staging only. Production provisioning is a Phase 10 prerequisite, called out explicitly in Phase 10's plan when it opens. |
| G-35 model-ID centralization breaks one of the five existing scripts subtly | Run `pytest` after step 1; smoke-test each script's `--help` / dry-run path before continuing. |

## Open questions (must resolve before execution starts, not before approval)

1. **Existing DynamoDB table conventions** — is there a single `reciterai_*` table prefix in production, and does `dynamodb_helpers.py` already manage table creation, or are tables created manually? Read `utils/dynamodb_helpers.py` and `docs/data-model-and-queries.md` at step 3.
2. **`pipeline_hierarchy/__init__.py` version constant** — does one exist, and if not, where should `bundler_version` live? Create a `__version__ = "0.1.0"` if needed; bump per change as needed.
3. **`gates/cli.py` output format** — JSON to stdout or a `gates_report.json` file? JSON to stdout is simpler and pipes well; default to that.

None of these block plan approval; they're step-1-of-execution discoveries.

---

## Next step after plan approval

Step 1 of the Tasks list (G-35 centralization) is small enough to ship as a standalone commit before the rest of Phase 9 lands. Doing it first proves the migration story documented in spec §11 and gives the substrate a clean import target.
