---
phase: 11
phase_name: Versioning, Review State, Diff Signaling
created: 2026-05-12
milestone: M2 — SPS-feeding service
---

# Phase 11 Context

## Domain

Implements spec [§3 Decision 2](../../../docs/RECITERAI-SPEC.md#3-decision-2--hierarchy_version-is-first-class-on-every-read-and-write), [§4 Decision 3](../../../docs/RECITERAI-SPEC.md#4-decision-3--review-state-is-machine-readable-pipeline-state), and [§5/§6 Decision 5](../../../docs/RECITERAI-SPEC.md#6-decision-5--structured-change-signaling). Three independent pieces of contract work:

1. `hierarchy_version` as a first-class field on activity records that reference subtopic IDs — so a hierarchy recompute can't silently dangle prior references.
2. `REVIEW#` records as a machine-readable cold-path gate — replacing the operator-memory model with structured DynamoDB rows + a CLI with a pre-write validator.
3. Structured change signaling — `diff.json` per publish, S3 write-order contract, `Cache-Control` on `latest/*`, and removal of `generated_at` from `hierarchy.json` (G-29 fix) so `manifest.sha256` is stable across content-identical publishes.

## Locked decisions

### D-01: `hierarchy_version` stamping is scoped to activity records carrying subtopic fields

Stamping `hierarchy_version` on every TOPIC#/IMPACT# row is wrong — those records reference Axis 1 (taxonomy), not the hierarchy, and already carry `taxonomy_version`. The migration population is **rows where `primary_subtopic_id` / `subtopic_ids[]` / `subtopic_confidences{}` is populated** (written by `assign_subtopics.py:642–648`).

From Phase 11 forward, every write to those fields stamps `hierarchy_version` alongside.

### D-02: Pre-Phase-11 records backfill to provenance

One-off migration script reads each affected TOPIC# row, joins to its `PROCESSING#pmid_*` sentinel for the `taxonomy_version` active at write time, and stamps the row. Provenance is preserved; mixed-version queries are honest. Orphan rows (no PROCESSING# match) get stamped with the version-shaped sentinel `0.0.0-orphan` — parses as a pre-release semver, sorts before every real version, won't choke ordered comparison in consumer code.

The migration script reports orphan count as a number (no pre-committed accept threshold). A meaningful fraction is a data-integrity signal — diagnose what wrote subtopic fields without a PROCESSING# row, don't paper over.

### D-03: `hierarchy_version` is a semver-shaped string (contract commitment)

Today: date-based (`v2026-05-06`). If the scheme ever changes (e.g., monotonic counters), it's a contract break that needs its own migration. Phase 11 commits to the semver-shape contract so future-you knows what's being broken.

### D-04: Rotation history keys on `(hierarchy_version, subtopic_id)` — rewrite in place

Current: `PK = SPOTLIGHT_HISTORY#{subtopic_id}` + `SK = "STATE"`. Phase 11: `PK = SPOTLIGHT_HISTORY#{hierarchy_version}#{subtopic_id}` + `SK = "STATE"`. One-shot migration rewrites every existing row with the inferred hierarchy_version (parsed from `last_shown_publish_id`, which carries `v{ISO-date}` per `history_writer.py:108`).

**Why not "additive new-key-prefix with fallback":** subtopic IDs are NOT stable across recomputes (`discover_subtopics.py:91` carries the explicit warning per D-06 from a prior phase — IDs are slug-derived from labels). That makes Option-3 carry-forward infeasible. Option-2 additive's rollback symmetry only protects the pre-Phase-11 → first post-Phase-11 transition; every subsequent rollback is between two versioned namespaces and the fallback path does nothing useful. The permanent two-shape read path isn't worth the one-time edge case. Option 1 is the honest choice given the unstable-ID reality: cold runs produce mostly-new subtopic_ids, so resetting rotation history per version is correct behavior.

### D-05: Orphan rotation rows split into two report buckets

Migration script reports orphans in two buckets:

- **`never_spotlighted_count`** — rows with no `last_shown_publish_id` (never selected for spotlight). Benign, probably common.
- **`malformed_publish_id_count`** — rows where the field is present but unparseable. Data integrity signal.

Both get the same `0.0.0-orphan` stamp; the split is purely for the migration report so the orphan count isn't dominated by benign never-spotlighted rows.

### D-06: Cold-path version cutover writes a `STAGE#hierarchy_version_cutover#GLOBAL` audit row

Every cold-run version mint writes:

```
PK: STAGE#hierarchy_version_cutover#GLOBAL
SK: RUN#{started_at}
{
  prev_version, new_version,
  migrated_rotation_count, orphan_count,
  started_at, completed_at,
  initiated_by  # ∈ {operator, drift_alert, scheduled} from Phase 10
}
```

Closes the loop on "what triggered this cutover" via the Phase 10 `initiated_by` flag. Cheap to write (one DDB put per cold run); expensive to reconstruct later if someone has to git-archaeology the migration code path.

### D-07: REVIEW# scope this phase is `GLOBAL` only

Spec §4 says `SK: TOPIC#{topic_id} | "GLOBAL"`. Phase 11 ships **only `GLOBAL`** — whole-artifact approval gates. Per-topic granularity (`SK: TOPIC#{topic_id}`) is YAGNI for v1 and adds complexity to `relabel_subtopics.py` (it would need to refuse running per-topic when a per-topic review is pending). Add per-topic when reviewer load actually justifies it.

### D-08: Review approval CLI surface

`python -m review approve --artifact hierarchy --version v2026-06-01` opens `$EDITOR` on a pre-populated YAML file (per spec §4 — not negotiable). Defaults:

- `reviewer_cwid` resolves from `~/.reciterai/config.yaml` (or `RECITERAI_REVIEWER_CWID` env override).
- **No new dotfile**: `~/.reciterai/config.yaml` is the canonical ReciterAI operator config. If future ReciterAI tools need operator config, fold into the same file. Do not proliferate dotfiles per-tool.
- Pre-write validator (per spec §4) runs inline on `approve` before the DynamoDB write. **Also exposed as `python -m review validate <path-to-yaml>`** for ad-hoc testing of a draft review YAML without committing — this is cheap (the validator already exists as an inline call; making it a subcommand is one argparse branch) and saves operator round-trips.

### D-09: `diff.json` is computed as a hybrid (STAGE# + byte-comparison)

Two authorities for two different things:

| Question | Source |
|---|---|
| "How many PMIDs got reassigned this run?" | STAGE# row from `assign_subtopics` (filtered by current cold-run's `run_id`) |
| "What subtopics changed in the published artifact?" | Byte-comparison: `s3://{bucket}/{prev_version}/hierarchy.json` vs new `hierarchy.json` |
| "Was `taxonomy_version` updated?" | Byte-comparison (it's an artifact property) |
| "What subtopics were added/removed/renamed?" | Byte-comparison (artifact property; STAGE# isn't structured for this) |

STAGE# = pipeline truth. Bytes = artifact truth. If anyone manually edits `hierarchy.json` between runs (not expected; the REVIEW# workflow gates edits via approval, not direct edits), the byte-comparison correctly reports the manual change; the STAGE# half stays silent (no stage wrote it). Diff stays internally consistent.

### D-10: `diff.json` written from `pipeline_hierarchy/publish.py`

Publish already owns S3 write-order. Folding the diff-write into the existing publisher is one new function, not a new stage. No new substrate ceremony.

### D-11: S3 write order inside `publish.py`

```
1. {version}/hierarchy.json
2. {version}/hierarchy.schema.json
3. {version}/diff.json          ← Phase 11 addition; before manifest
4. {version}/manifest.json
5. latest/manifest.json         ← still last, per existing D-02
```

Diff lands before manifest so a consumer polling `manifest.sha256` and seeing the flipped hash can immediately `GET diff.json` without racing the upload. If diff lands after manifest, the consumer 404s on diff (or sees stale prior-version diff in eventually-consistent reads), which evaporates the entire benefit of structured diff signaling — SPS falls back to wholesale ETL unnecessarily on every cutover.

### D-12: `diff.json` carries `diff_schema_version`

Top-level field in `diff.json`. SPS reads it, checks against its known set, falls back to wholesale ETL on unrecognized values. Diff shape can evolve additively without breaking consumers; explicit version field handles forward-compat skew.

### D-13: STAGE# queries in `diff.json` computation filter by current cold-run's `run_id`

Without `run_id` scoping, a cold run that only touches the taxonomy (no re-assignment) would pick up stale `STAGE#assign_subtopics` rows from an earlier cold run and report a reassignment count unrelated to this run.

**Phase 11 substrate addition** (prerequisite, not deliverable):

- `utils/stage_records.py` gains an optional `run_id` field on STAGE# rows. Backwards-compatible — hot-path STAGE# rows (which carry SFN `execution_arn`-derived identifiers) are unaffected.
- `pipeline_cold.run.main()` generates a UUID at start and threads it into each stage's STAGE# write call. The cold-run-level `run_id` is the natural correlation key.

### D-14: G-29 — remove `generated_at` from `hierarchy.json` via cut-over

One-line change in `pipeline_hierarchy/bundler.py`: stop writing `generated_at` into `hierarchy.json`. `manifest.json` continues to carry `generated_at` (spec-correct location). Old version directories are not rewritten — historical `manifest.sha256` ↔ `hierarchy.json` pairs remain matched as written.

**No transitional `content_sha256` field.** YAGNI for the SPS-only consumer case; adding it now would sit unused indefinitely and accrue confusion. Add it later if a second embedded timestamp ever lands and disambiguation is needed.

### D-15: G-29 cutover is paired with downstream-effects audit on SPS side (operator coordination)

The Phase 11 cutover ETL run is *correct behavior*, not a problem to suppress. What needs operator coordination is **what cascades from a hierarchy ETL run on SPS** — search reindex, faculty profile rebuilds, push notifications, cache invalidations.

This is a 30-minute conversation with the SPS hierarchy-ETL owner, not engineering work. Pre-cutover runbook captures the coordination window (e.g., "expect search reindex at time T+X; here is the rollback plan"). No flag on SPS asking it to pretend nothing changed.

### D-16: Cutover publish writes a `STAGE#g29_cutover#GLOBAL` audit row

Parallel to D-06's `hierarchy_version_cutover` row:

```
PK: STAGE#g29_cutover#GLOBAL
SK: RUN#{started_at}
{
  previous_publish_sha, new_publish_sha,
  hierarchy_version_at_cutover, run_id,
  started_at, completed_at
}
```

Six months from now, "why did the hierarchy sha flip on date X without any content changing" is answerable from a single DDB get — not git archaeology against `bundler.py`.

### D-17: Pre-Phase-11 activity-row backfill uses a single sentinel (research follow-up to D-02)

D-02 originally described a per-row inferred `hierarchy_version` via a `PROCESSING#` join. Phase 11 research confirmed `PROCESSING#` rows do not carry `hierarchy_version` at write time (they predate the field) — the join is empty for every legacy row, so the D-02 mechanism would produce 100% orphans.

Resolution: stamp every pre-Phase-11 row that carries subtopic fields with `hierarchy_version = "v0.0.0-pre-phase-11"`. Honest about the mapping gap, sorts before every real `v{ISO-date}`, preserves provenance via the row's existing `taxonomy_version`, and keeps the orphan sentinel (`0.0.0-orphan` from D-02) reserved for its narrower meaning ("legitimately missing PROCESSING# join evidence", not "pre-feature").

The migration script still reports counts (rows-stamped, rows-skipped-because-already-stamped) but skips the PROCESSING# join entirely for legacy rows.

### D-18: `reassigned_pmid_count` in `diff.json` means rows touched this run

Research surfaced two readings: "rows touched this run" (= existing `records_written` counter) vs "primary subtopic changed vs prior run" (requires per-PMID read-then-write to compare old vs new `primary_subtopic_id`).

Resolution: rows-touched semantics. Cheap (already tracked); no read-before-write penalty on the publish path; honest signal that the field name will reflect verbatim ("number of activity rows that had subtopic fields rewritten in this publish"). If downstream consumers later need primary-changed semantics, that's a follow-up phase that pays for the read-before-write cost explicitly.

## Open items for plan-phase (deliberately not locked)

### O-01: First-ever-publish edge case for `diff.json`

When `prev_version` doesn't exist (initial post-Phase-11 publish), or exists but predates the diff contract. Two defensible shapes:

- Emit `diff.json` with `from_version: null` (explicit "treat as full ETL" signal)
- Omit `diff.json` entirely (signals the same via absence per spec §6's "absent → wholesale ETL")

Both work; the choice affects whether SPS branches on `diff.from_version == null` or on `404`. Planner picks alongside the implementation work.

### O-02: REVIEW# pre-write validator implementation seam

Spec §4 specifies the validator rules (`reviewer_cwid` regex, `rationale` ≥ 40 chars, `decision ∈ {approve, reject}`, `proposed_artifact_uri` HEAD-checks). Whether the validator lives in `review/cli.py` directly, or in a `review/validator.py` module imported by both the CLI and the standalone `validate` subcommand (D-08), is an implementation seam — planner picks based on test ergonomics.

### O-03: Where does the cold path actually invoke the `STAGE#hierarchy_version_cutover` write?

Two candidate locations: at the top of `pipeline_cold.run.main()` (before stage execution, writing only `prev_version`; updated to add `new_version` + counts at end) vs at the end (single write after all stages succeed). The "atomic at end" shape is simpler but loses the audit trail on partial failures. Planner decides.

## Canonical refs

**MUST read before planning:**

- `docs/RECITERAI-SPEC.md` — §3 (Decision 2: hierarchy_version first-class), §4 (Decision 3: REVIEW# state), §5 (Decision 4: STAGE# substrate — already shipped Phase 9, Phase 11 adds `run_id` field), §6 (Decision 5: structured change signaling), §11 G-29 (generated_at placement)
- `docs/hierarchy-contract.md` — current S3 write order, manifest shape; Phase 11 modifies write order per D-11
- `.planning/phases/10-hot-cold-path-split/10-SUMMARY.md` — Phase 10 `initiated_by` flag values (D-06 reads these), cold-path CLI entry point (D-13 threads `run_id` from `pipeline_cold.run.main()`)
- `.planning/phases/10-hot-cold-path-split/10-CONTEXT.md` — D-07 (substrate build/write split — Phase 11 extends with `run_id` field), D-10 (single-file IaC pattern — Phase 11 doesn't add infra but should follow same simplicity convention)

**Code refs the planner should read:**

- `assign_subtopics.py:642–648` — `update_activity_subtopics` is where `hierarchy_version` stamping lands (D-01)
- `spotlight/history_writer.py:114–127` — rotation history writer; D-04 changes PK shape here
- `pipeline_hierarchy/bundler.py` — D-14 one-line change (stop writing `generated_at`)
- `pipeline_hierarchy/publish.py` — D-10 + D-11 + D-16 (diff write, write order, cutover audit row)
- `pipeline_cold/run.py` — D-13 (`run_id` threading from `main()`)
- `utils/stage_records.py` — D-13 substrate addition (optional `run_id` field on STAGE# rows)
- `discover_subtopics.py:88–93` — context for D-04 (the unstable-ID warning that killed the carry-forward option)

## Code context — reusable assets

- **Phase 9 STAGE# substrate** is the foundation. D-13 adds one optional field (`run_id`); does not break existing callers.
- **Phase 10 alert dispatcher** (`pipeline_common.alert`) is available if the REVIEW# pre-write validator wants to surface validation failures to operators via Slack. Probably not needed in Phase 11 — validator failures are operator-facing CLI errors, not pipeline events — but the option exists.
- **Phase 10 cold-path orchestrator** (`pipeline_cold.run.main()`) is where D-13's `run_id` generation lands. Existing argparse + `--initiated-by` flag plumbing already there.
- **Existing rotation selector** (`spotlight/rotation_selector.py`) reads `SPOTLIGHT_HISTORY#{subtopic_id}` partitions via BatchGetItem. D-04's PK change requires updating the reader too; the BatchGetItem keys construction is the change site.

## Risks

- **D-04 migration script correctness.** Misparsing `last_shown_publish_id` (e.g., a row carries a publish_id from a format that pre-dates `v{ISO-date}` convention) silently stamps the wrong `hierarchy_version` on a rotation row. Mitigation: migration script must fail loud on any unrecognized publish_id format AND emit per-row before/after diff to a log file before committing. Operator reviews the log; explicit confirm-and-commit step prevents silent corruption.
- **D-13 substrate addition is a substrate change.** Optional field, backwards-compatible, but Phase 9 substrate has a lot of callers. Phase 11 plan must include regression coverage for the existing STAGE# write paths (hot-path handlers in particular).
- **D-14 cutover ETL coordination with SPS.** Per D-15, this is a coordination cost not an engineering one — but the planner must schedule the cutover-publish + SPS downstream-effects conversation as a Phase 11 task (not a runbook entry for "later").
- **First-ever-publish edge case (O-01)** could regress to "no diff.json ever written" if planner picks "omit entirely" and then later code paths assume `diff.json` is always present. Whichever option wins, the contract must be explicit and tested.

## Deferred ideas (captured from discussion, NOT scope of Phase 11)

- **Per-topic REVIEW# granularity** (`SK: TOPIC#{topic_id}` per spec §4). Add when reviewer load justifies the `relabel_subtopics.py` complexity of refusing-to-run-when-per-topic-review-pending. YAGNI for v1.
- **Stable subtopic_ids across cold runs.** D-06 from a prior phase: subtopic IDs are slug-derived from labels (`discover_subtopics.py:91`). Fixing this enables Option-3 carry-forward in B (preserving rotation history for surviving subtopics). Separate phase — touches `discover_subtopics`, `relabel_subtopics`, the hierarchy contract, and SPS-side joins. Not Phase 11 scope.
- **`content_sha256` manifest field.** YAGNI for SPS-only consumer; add later if a second embedded timestamp ever needs disambiguating.
- **Spec §3 rewrite** — fix the `(cwid, hierarchy_version)` rotation-key framing that's a holdover from earlier per-CWID spotlight confusion. Rotation is per-subtopic; cwid never enters the rotation key. **Followup spec-hygiene task**, not Phase 11 deliverable.
- **REVIEW# dashboard** (Streamlit/Flask reading REVIEW# rows for human reviewer UX). Spec §4 explicitly defers; data shape is dashboard-ready when someone wants to build one.
- **A/B testing of hierarchy versions on the SPS side** — spec §3 quarterly-review trigger. Phase 11 prepares the producer side (versioned prefixes, stamped activity records); the consumer-side time-machine query support is deferred until the trigger fires.
