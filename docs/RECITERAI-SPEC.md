# ReciterAI — Specification & Architectural Decisions

*v2, 2026-05-11. Repo state: commit `058529c`. Not a contract — `docs/hierarchy-contract.md` and `docs/spotlight-contract.md` hold those. This is the design rationale that informs them.*

v1 of this spec enumerated 37 gaps. Feedback collapsed them: most clustered around six structural decisions that, once made, dissolve the gaps automatically. v2 is organized around those decisions. A residual list of seven hygiene items survives at the end — these are independent of the architecture and need their own commits, not their own design.

---

## 0. Status

*This section is the only one expected to drift. Update it as work lands.*

**Spec status**: v2 accepted 2026-05-11. The architecture below is decided; what follows is execution.

**Execution state** (last reviewed 2026-05-11):

| Spec section | Phase | Status | Tracking issue |
|---|---|---|---|
| §11 G-32 (`excluded_topics` source) + bundler | Issue #4 | **closed 2026-05-12** — bundler proven end-to-end on first production publish (`v2026-05-12`); static `hierarchy_full.json` deletion deferred to a follow-up | [#4](https://github.com/wcmc-its/ReciterAI/issues/4) ✓ |
| §7 parent-prefix gate | Issue #2 | **closed 2026-05-12** — cleanup re-ran across all 65 topics / 1,526 subtopics; SPS `etl:hierarchy` reports `warnings: 0` against `v2026-05-12` | [#2](https://github.com/wcmc-its/ReciterAI/issues/2) ✓ |
| §5 Decision 4 (content-addressed stages) + §7 Decision 6 (gates framework) | Phase 9 | not started | not yet issued |
| §2 Decision 1 (hot/cold split) | Phase 10 | not started | [#3](https://github.com/wcmc-its/ReciterAI/issues/3) (parent) |
| §3 + §4 + §6 Decisions 2/3/5 (versioning, review, diff) | Phase 11 | not started | not yet issued |
| §8 + §9 + residual cleanup | Phase 12 | not started | not yet issued |
| §10 Axis 2 producer | Phase 8 | blocked | [#5](https://github.com/wcmc-its/ReciterAI/issues/5), [#6](https://github.com/wcmc-its/ReciterAI/issues/6), [#7](https://github.com/wcmc-its/ReciterAI/issues/7), [#8](https://github.com/wcmc-its/ReciterAI/issues/8) (`decision-deferred`) |

**Issued for grabs**: pure-hygiene items per §11 (G-1, G-18, G-24, G-34, G-37).

**Deliberately NOT issued**: architectural-small-surface items per §11 (G-35, G-36). These look small but touch substrate and want someone who has read this spec. Assign rather than make grabbable. (G-32 was reclassified as architectural-small-surface in v2, then resolved in `058529c` — option (c), frozen config-file list. The reasoning in §11 remains for the record.)

**Decision-deferred** (per §10): the four Axis 2 producer-model questions exist as tracked issues with label `decision-deferred`. Phase 8 is blocked until all four close.

**Recent slip-checkpoints fired**: none yet. (§12 names the end-of-week-2 Phase 9 checkpoint as the next watch date.)

**Production state (2026-05-12)**: `s3://wcmc-reciterai-hierarchy/latest/manifest.json` → `v2026-05-12`, sha256 `84eecdb29881…`, 65 topics / 1,526 subtopics, taxonomy `taxonomy_v2`, schema `1.0.0`. First publish via the #4 bundler. G-29 (sha churn from `generated_at` re-stamping) observed in real life — sha differed between dry-run and real publish despite identical inputs. Not a problem here; flagged for Phase 11.

---

## 1. What ReciterAI is

A batch service that produces four upstream artifacts for the Scholars Profile System:

| Artifact | Sink | Consumer |
|---|---|---|
| Axis 1 topic taxonomy (~67 topics) | `taxonomy_v2.json` in-repo + DynamoDB topic vectors | SPS reads scores |
| Axis 1.5 subtopic hierarchy (~1,526 subtopics) | S3 `wcmc-reciterai-hierarchy/{v…,latest}/` + DynamoDB | SPS `etl:hierarchy` |
| Faculty CWID rollups | DynamoDB | SPS `etl:dynamodb` |
| Per-faculty spotlight | S3 `wcmc-reciterai-artifacts/spotlight/` + DynamoDB | SPS `etl:spotlight` |

ReciterAI is upstream-only and pull-based. No runtime API. Integration is via S3 manifests and DynamoDB.

Inputs: ReciterDB (MySQL — publications, synopses, faculty metadata, keyword relevance), AWS Bedrock (Haiku 4.5 screening + subtopic classification, Sonnet 4.6 dense scoring + discovery + critic, Opus 4.7 spotlight lede only).

Three open GitHub issues drive the current work:

- **#2** — display_name parent-prefix violations (87 today)
- **#4** — bundler from per-topic augmented files → `hierarchy_full.json`
- **#3** — end-to-end orchestration

The reframing in §2 below splits #3 into two orchestrators with different semantics.

---

## 2. Decision 1 — Two paths, not one

The single highest-leverage decision. ReciterAI conflates two operationally different workloads under one pipeline. Splitting them collapses eight gaps from v1 (G-2, G-3, G-12, G-21, G-25, G-26, G-27, G-28).

### Hot path — daily, automated, scoped to deltas

| Property | Value |
|---|---|
| Trigger | Scheduled (EventBridge cron, weekly v1; daily once stable) |
| Inputs | New PMIDs in ReciterDB since `last_successful_hot_run_at` |
| Writes | New score records (DynamoDB), new subtopic assignments against `latest` hierarchy, **incremental rollup deltas for affected CWIDs only**, regenerated spotlights for dirty-flagged CWIDs |
| Never does | Mint a `hierarchy_version`, mint a `taxonomy_version`, publish to `wcmc-reciterai-hierarchy/`, re-cluster subtopics, regenerate `taxonomy_v2.json` |
| Failure mode | Idempotent per-PMID (`PROCESSING#` records); rerun-safe |
| Approval | None — runs autonomously |

The hot path *reads* the current `latest` hierarchy and treats it as immutable. New PMIDs that don't fit existing topics flow into the uncovered-publications event stream (§5) — they don't trigger a re-cluster.

### Hot path input-boundary behavior

When the hot path encounters input data the current hierarchy can't classify, **it never halts**. Halting is reserved for infrastructure failures (Bedrock unavailable, DynamoDB throttled). Classification mismatches are *data*, not errors, and the hot path's job is to keep running.

Specifically:

- A PMID whose top topic score is below 0.4 across all topics → write an `UNCOVERED_PMID` event (§9). The PMID gets zero topic-score records persisted (today's behavior; consistent).
- A PMID whose subtopic-assignment confidence is below the floor across all candidate subtopics under its top topic → write `LOW_CONFIDENCE_ASSIGNMENT` event. Empty `subtopic_ids[]`, no `primary_subtopic_id` (today's behavior; consistent).
- Bedrock returns a malformed response or unparseable JSON for a specific PMID → write a per-PMID `STAGE#` record with `status: "failed"` and a typed error code. Next hot run retries that PMID.

**Cold-path trigger threshold**: when the rolling 14-day count of `UNCOVERED_PMID` events exceeds 5% of new pubs, OR any single topic accumulates >50 `LOW_CONFIDENCE_ASSIGNMENT` events, the system emits an alert recommending a cold-path run. The alert is informational; the operator decides whether to schedule a cold path. The hot path keeps running in the meantime.

**Who computes the rolling rate.** Not the hot path itself (computing on every run is wasteful), not the dashboard (dashboard-polled means an unviewed dashboard means an unfired trigger). The cleanest shape is a `drift_evaluator` stage that runs on its own EventBridge cron (daily, after the hot path), consumes the typed event records since its last run, and writes a `DRIFT#` sentinel record:

```
PK: "DRIFT#evaluation"
SK: "RUN#{evaluated_at}"
{
  window_start, window_end,
  uncovered_rate: 0.073,        # 7.3% over 14d window
  low_confidence_max_topic: "aging_geroscience",
  low_confidence_max_count: 58,
  cold_run_recommended: true,
  triggered_thresholds: ["uncovered_rate"],
  notification_sent_at: "..."
}
```

When `cold_run_recommended: true`, the evaluator opens (or comments on) a tracked GitHub issue labeled `drift-alert` and posts to the operator Slack channel. The sentinel record is what makes this *system-triggered*, not operator-polled. Thresholds are config (§11 G-18 cluster), tunable per environment.

**Incremental rollups are required, not optional.** Without an incremental mode in `rollup_by_cwid.py` ("given new scores for CWIDs {X, Y, Z}, recompute just those CWIDs"), rollups go stale between cold runs and the whole point of an incremental hot path collapses. This is a Phase-9 implementation requirement.

### Cold path — on demand, human-gated, full recompute

| Property | Value |
|---|---|
| Trigger | Manual invocation by an operator |
| Inputs | Full corpus from ReciterDB |
| Writes | New `taxonomy_version` (optional), new staged `hierarchy_version`, full re-rollup, full spotlight refresh |
| Approval | Required at each review gate (§4) before downstream stages run |
| Cutover | Manifest swap on `wcmc-reciterai-hierarchy/latest/` after all stages approved |

Only the cold path touches version-minting operations. The hot path is structurally incapable of bumping versions.

This split makes cadence (was G-26), trigger mechanism (G-25), state tracking (G-3), propagation (G-2), and alerting (G-27) into well-defined questions on each path independently, rather than one tangled question across both.

---

## 3. Decision 2 — `hierarchy_version` is first-class on every read and write

Today's activity records carry `primary_subtopic_id` / `subtopic_ids[]` strings with no version qualifier. After a recompute, those references are dangling until the next full assignment pass.

### Producer-side change

- Every activity record stamps `hierarchy_version` alongside `subtopic_ids[]`.
- Rotation state (spotlight) is keyed by `(cwid, hierarchy_version)`, not by `cwid` alone, so a recompute can't silently reset everyone's rotation.
- The cold path stages a new `vN+1/` prefix under `wcmc-reciterai-hierarchy/` and runs its own backfill assignment job against the staged version while the hot path keeps writing against `vN` = `latest`.
- Cutover is a `latest/manifest.json` rewrite. Rollback is the inverse rewrite. Bytes never move.

### Consumer-side change

ReciterAI publishes versioned prefixes; SPS performs **atomic swap on consumption**. SPS's `etl:hierarchy` becomes a single transaction (or `TRUNCATE + bulk insert` inside one txn) so mid-ETL reads never see a half-loaded Subtopic table. Brief lock during ETL, minutes not hours.

We are explicitly *not* introducing a `hierarchy_version` column into SPS's `Subtopic` table. Full version-qualified reads on the SPS side (necessary for A/B-testing a new taxonomy, or "show me last quarter's profile" features) is premature lift — nothing on the roadmap needs it.

**Revisit trigger**: any one of the following moves this from "deferred" to "scheduled":

- First product request for a time-machine view ("show me Dr. X's profile as of v2026-05-06").
- First production incident where rolling SPS back to a prior `hierarchy_version` would have shortened MTTR.
- Three or more *operator-initiated* cold-path runs in a calendar quarter — suggesting recompute is frequent enough by human demand that A/B comparison would be useful.

The third trigger specifically excludes cold runs forced by §2's `drift_evaluator` alerts. Otherwise a noisy classifier (lots of drift triggers, lots of forced cold runs) auto-triggers the versioned-reads revisit, which is the wrong coupling — drift recompute is a *response to data*, not user demand for time-machine reads. `STAGE#cold_path` records carry `initiated_by ∈ {"operator", "drift_alert", "scheduled"}`; only the first counts toward this trigger.

In the absence of any trigger, this is reviewed at quarterly planning — not perpetually open. The producer side is already prepared and only SPS schema would change.

**Contract delta to write down**: "ReciterAI stages versioned prefixes under `vN/`; SPS performs atomic swap on consumption. SPS's `etl:hierarchy` MUST be transactional. Mid-cutover read consistency is SPS's atomicity guarantee, not ReciterAI's."

This collapses G-5, G-7 (rollback runbook), and G-22 (rotation state).

---

## 4. Decision 3 — Review state is machine-readable pipeline state

Today "human-reviewed and frozen" lives in operator memory. The cold path implicitly stalls if a review is open; the hot path has no way to know whether to wait or proceed.

### Model

A DynamoDB `REVIEW#` record per proposed artifact:

```
PK: "REVIEW#{artifact_type}#{version}"   e.g. REVIEW#hierarchy#v2026-06-01
SK: "TOPIC#{topic_id}" | "GLOBAL"        for per-topic gates vs whole-artifact gates
{
  proposed_artifact_uri: "s3://...",     pointer to the candidate
  summary_stats: {                       cheap diff stats for human/dashboard
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

### Rules

- `relabel_subtopics.py` and `assign_subtopics.py` refuse to run against a hierarchy whose review status is `pending` or `rejected` for any required topic.
- The hot path always runs against whatever is currently `approved` and `latest`. It never blocks on review.
- Approval is a CLI operation, but the input shape is structured to prevent empty/forged review records. `python -m review approve --artifact hierarchy --version v2026-06-01 --topic aging_geroscience` opens `$EDITOR` on a templated YAML file pre-populated with `proposed_artifact_uri`, `summary_stats`, and a required `rationale:` block. `reviewer_cwid` auto-fills from `~/.reciterai/config.yaml` (or `RECITERAI_REVIEWER_CWID` env). Multi-line review notes are first-class.
- **Pre-write validator** (in `review/cli.py`, runs after the operator saves the YAML and before the DynamoDB write):
  - `reviewer_cwid` present, matches `^cwid_[a-z]+\d+$`.
  - `rationale` present, length ≥ 40 characters after stripping whitespace (rough proxy for "operator typed an actual reason"; tunable in config).
  - `decision ∈ {"approve","reject"}`.
  - `proposed_artifact_uri` resolves to an actual S3 object (HEAD check; fails if the candidate doesn't exist).
  - Any validator failure exits non-zero, leaves the YAML on disk for the operator to edit, and writes nothing to DynamoDB. No partial review records.
- Per-topic approvals can be granular; a `GLOBAL` row gates whole-artifact promotion to `latest`.

### Why this shape

The record format is dashboard-ready. A future Streamlit/Flask page would render `proposed_artifact_uri` + `summary_stats` + decision form against the same DynamoDB rows the CLI reads. We are *not building* the dashboard now; we are refusing to paint into a CLI-only data shape.

This gives compliance audit trail (who approved what, when, why) for free, and makes the cold path's review gate testable.

Collapses the human-gate gaps from v1 (review gates per topic, taxonomy review, etc.).

---

## 5. Decision 4 — Content-addressed stage completion

v1's G-3 (no "what changed since last run" signal), G-17 (resume logic skips assigned records but reprocesses legitimately-unassigned ones), and the propagation-tracking gap are all the same missing abstraction: a general notion of "this stage is done."

### Model

Every stage writes a `STAGE#` record on completion:

```
PK: "STAGE#{stage_name}#{scope}"     e.g. STAGE#assign_subtopics#topic:aging_geroscience
SK: "RUN#{started_at}"
{
  input_hash: "sha256(...)",          hash of the inputs:
                                       - score stage: (taxonomy_version, pmid_set)
                                       - assign stage: (hierarchy_version, pmid_set)
                                       - rollup stage: (score_version, hierarchy_version, cwid_set)
                                       - publish stage: (hierarchy_full.json bytes)
  status: "complete" | "failed" | "skipped",
  skip_reason: "input_hash unchanged since 2026-05-04T12:00Z" | null,
  started_at, completed_at,
  output_pointer: "s3://..." | "ddb://...",
  cost_estimate_usd: number,
  records_written: integer
}
```

### Rules

- Before a stage runs, it computes its `input_hash` and queries for a `complete` record with that hash. If found, write a `skipped` record and exit zero.
- **A skip still writes a row.** `status: "skipped"`, `skip_reason` set, `duration_ms` measured (wall clock from hash compute through lookup), `cost_estimate_usd` populated. The point of distinguishing skips is observability, which collapses if skipped runs are invisible — dashboards need to split "we spent $X on real work, $Y on skips that correctly detected hash matches." A skip that emits no row is the same as no skip at all.
- **Skip cost formula** (v1, intentionally simple): `cost_estimate_usd = 0.0000003` per skip — one DynamoDB `GetItem` of <1KB at on-demand pricing (~$0.25 per million reads). No Bedrock calls happen during a skip by construction (the hash check precedes any model invocation). The constant beats wall-clock-derived estimates because skip duration is dominated by Python startup, which is not an AWS cost. Revisit the formula if/when stages start doing materially-expensive input-collection work *before* the hash check.
- Memoization, not Bazel-strict reproducibility. LLM stochasticity is its own concern; suppressing it isn't worth the engineering.
- **Model IDs are part of `input_hash`.** Centralizing the pinned Bedrock model IDs (residual item, below) is not pure hygiene — it's a precondition for `input_hash` correctness. If a stage's model ID changes, the input space changes, so the hash must change. Each stage's `input_hash` schema explicitly includes the model ID(s) it depends on.

### What this fixes

- **G-17 cleanly**: the assignment stage's `input_hash` includes `hierarchy_version`. A hierarchy change invalidates assignments naturally. The "unassigned vs unprocessed" distinction stops mattering — the question is "was this PMID processed against the current hierarchy_version yet?"
- **G-3**: "what changed since last run" is now a DynamoDB query, not operator memory.
- **G-14** (cost telemetry was unaggregated): `cost_estimate_usd` per stage rolls up cleanly per run.

---

## 6. Decision 5 — Structured change signaling

`manifest.sha256` is too coarse a signal (consumers can't tell renames from reassignments from new subtopics) and too noisy (every publish re-stamps `generated_at` inside the hashed bytes, so sha flips on every run — v1's G-29).

### Two artifacts per publish

| Artifact | Purpose | Contract status |
|---|---|---|
| `manifest.json` | Cheap poll signal. Consumers HEAD or small-GET to detect change. | **Contract.** Breaking changes require 30-day notice. |
| `diff.json` | Rich change description. Consumers GET only when sha flips. | **Advisory hint, not contract.** Shape may evolve additively. |

`diff.json` shape (advisory):

```json
{
  "diff_schema_version": "1.0.0",
  "from_version": "v2026-05-06",
  "to_version": "v2026-06-01",
  "taxonomy_version_changed": false,
  "added_subtopics": [...],
  "removed_subtopics": [...],
  "renamed_subtopics": [{"id": "...", "old_display_name": "...", "new_display_name": "..."}],
  "reassigned_pmid_count": 312,
  "editorial_only": false
}
```

### Consumer logic

```
fetch latest/manifest.json
if manifest.sha256 == last_known_sha: exit
fetch {version}/diff.json
if diff.diff_schema_version in known_versions:
    branch on diff content — additive ETL for editorial_only,
    full reload for taxonomy_version_changed, etc.
else:
    fallback to wholesale ETL
```

The `diff_schema_version` field inside `diff.json` is what lets us evolve the diff additively without coordination. Old SPS that doesn't recognize the version gracefully falls back to wholesale ETL. There is no 30-day notice on diff shape changes — they're advisory by construction.

### Write order + read tolerance (eventual consistency)

S3 PutObject is read-after-write consistent for new keys but `latest/manifest.json` is overwritten in place, and any CDN or cache in front of S3 (today: none; future: possible) could serve stale bytes briefly. The contract pins the failure modes:

**Write order (publisher MUST):**

1. Write `{version}/hierarchy.json`, `{version}/hierarchy.schema.json`, `{version}/diff.json` first.
2. Write `{version}/manifest.json`.
3. Write `latest/manifest.json` last. This mirrors the existing D-02 invariant from `pipeline_hierarchy/publish.py`.

**Read tolerance (consumer SHOULD, documented in contract):**

- If `manifest.sha256` flipped but `diff.json` is absent, malformed, or its `from_version` doesn't match the consumer's last-known version, fall back to wholesale ETL. Do not error, do not retry-poll, do not block on diff availability.
- The `diff_schema_version` field handles forward-compat (version skew); the fallback-on-absent rule handles eventual-consistency races. Both are needed — `diff_schema_version` alone doesn't help when the file is missing.

This means a publish that fails between step 2 and step 3 leaves consumers on the prior version (manifest hasn't flipped). A publish that fails between step 1 and step 2 leaves orphan version-pinned objects but no consumer-visible change. Both are recoverable by re-running publish; neither corrupts a consumer.

### Stale `latest/manifest.json` on the read side

A separate failure mode: consumer reads stale `latest/manifest.json` (CDN or cache serving the old version), sees no sha flip, skips an ETL run that should have happened. Different from the "diff missing" race — the manifest itself is the stale object.

Mitigations, primary and defense-in-depth:

1. **Primary — short cache TTL on `latest/*`.** Publisher sets `Cache-Control: max-age=60, must-revalidate` on `latest/manifest.json` (and any other future `latest/*` objects). 60 seconds is short enough that stale reads recover within one poll cycle and long enough that S3 isn't hammered on dense polls.
2. **Defense-in-depth — periodic version-pinned probe.** Consumer occasionally (say, every Nth poll, N=10) skips `latest/manifest.json` and instead does an S3 `ListObjectsV2` on the bucket root, picks the lexically-largest `v*` prefix, and compares its `manifest.json` sha against the consumer's last-known. Catches a stuck `latest/manifest.json` that's persistently stale (CDN bug, S3 incident, ACL misconfiguration). Cheap — one list call per N polls.

Both are codified in the contract section of `docs/hierarchy-contract.md` so the SPS-side ETL implementation can rely on them. Today no CDN sits in front of the bucket, so #1 is preemptive; ship it anyway because a future CDN insertion would silently break the poll otherwise.

### Side issue — `generated_at` placement

Move `generated_at` out of `hierarchy.json` and into `manifest.json` only. Today it's inside the canonical bytes that get sha256'd, so every publish flips the sha even when content is identical. Add a `content_sha256` that excludes `generated_at` if there's a reason to keep both — but the simpler fix is to delete `generated_at` from `hierarchy.json` entirely, since `manifest.generated_at` already exists.

Collapses G-6, G-29, G-30 (URL pattern contradiction also addressed — see Decision 6 below since it's a contract-honesty fix).

---

## 7. Decision 6 — Quality gates as a registered framework

v1 had five gates dispersed across the codebase (G-11 parent-prefix, G-16 coverage, G-20 rollup reconciliation, G-31 round-trip schema, G-33 PII scan). All five are the same shape: a check that runs between produce and publish.

### Model

```python
# gates/registry.py
@register_gate(stage="publish", severity="block")
def parent_prefix_gate(hierarchy: dict) -> GateResult: ...

@register_gate(stage="publish", severity="block")
def pii_scan_gate(hierarchy: dict) -> GateResult:
    """No cwid_* prefixes, no email-shaped strings, no faculty names."""

@register_gate(stage="publish", severity="block")
def schema_roundtrip_gate(hierarchy: dict, schema: dict) -> GateResult: ...

@register_gate(stage="discovery", severity="warn")
def coverage_gate(per_topic_coverage: dict) -> GateResult: ...

@register_gate(stage="rollup", severity="warn")
def topic_subtopic_reconciliation_gate(...) -> GateResult: ...
```

Operators can run:
```
python -m gates --stage publish --hierarchy v2026-06-01
```
and see which gates pass before promoting. `--force` is allowed with a logged `force_reason` written to the stage's `STAGE#` record.

### Specific gates the framework needs at launch

- **Parent-prefix** (issue #2): no subtopic `display_name` begins with a parent-topic word.
- **PII** (was G-33): hierarchy artifact contains no `cwid_*`, no email-shaped strings, no `personIdentifier`.
- **Schema round-trip** (was G-31): post-publish, fetch the just-published artifact and re-validate against the just-published schema.
- **Coverage**: per-topic, ≥85% of activities assigned to some cluster. Warn-only (D-01 already permits accepting gaps).
- **Rollup reconciliation** (was G-20): for each CWID, sum of `papers_in_subtopic_X` (using `primary_subtopic_id` aggregation, see §8) equals count of papers above topic-X threshold.
- **Critic rejection rate** (see §9): per-faculty rejection rate >40% triggers warn, surfaces in coverage report.

Collapses G-11, G-16, G-20, G-31, G-33.

---

## 8. Aggregation — publish both, stop pretending there's one right answer

v1's G-19 noted the doc warns operators to "verify which" aggregation choice the CSVs use. That's the wrong forcing function — silent breakage risk (G-20) lives in exactly that ambiguity.

**Decision**: publish both, simultaneously, with distinct names.

- `faculty_subtopic_counts_exclusive` — primary-subtopic-only; sums clean to topic totals; for navigation rollups.
- `faculty_subtopic_counts_inclusive` — multi-label; papers double-count; for "anything related to X" queries.

Consumers pick a column. Both are cheap to compute simultaneously. The rollup reconciliation gate (§7) checks the exclusive form against topic totals.

Collapses G-19, and gives G-20's reconciliation gate something concrete to verify.

---

## 9. Feedback loops — capture rather than discard quality signal

The pipeline today is purely feed-forward. Three signals leak away that should drive next-cycle decisions:

| Signal | Today | Should be |
|---|---|---|
| Critic rejects a lede (v1 G-23) | rejection vanishes into logs | typed event `CRITIC_REJECT#{cwid}#{pmid_set_hash}` with `reason_code` |
| Publication doesn't fit any topic above 0.4 (v1 G-12) | silently force-fit to nearest | typed event `UNCOVERED_PMID#{pmid}` with top-3 closest topics + scores |
| Subtopic assignment confidence below floor across all subtopics | empty `subtopic_ids[]`, treated as benign | typed event `LOW_CONFIDENCE_ASSIGNMENT#{pmid}` |

Each event is a DynamoDB record. The cold path consumes them on each run:

- High critic rejection rate for faculty X → spotlight ranking quality issue, not lede generator issue.
- 50+ uncovered PMIDs since last run → run a Sonnet pass on that pool to surface candidate new topics.
- Persistent low-confidence assignments in topic X → flag the subtopic hierarchy under X for re-clustering.

This turns "operator vigilance" into a queryable backlog. The coverage report from §7 surfaces aggregates.

---

## 10. Axis 2 (tools) — commit to the producer model, not a date

Phase 8 in the roadmap is a schema commitment without a producer. A date commitment that slips is what got us here; what's needed is a producer-model spec answering:

1. **Data shape for `TOOL#` records.** Canonical tool ID, parent tool ID, display name, source URI, source confidence. The hierarchy schema already accommodates this — the question is how *records* are shaped, not the contract.
2. **Controlled vocabulary source.** Three options, pick one before scheduling implementation:
    - SciCrunch / RRID (RRIDs as canonical IDs; rich existing metadata; mismatch with WCM-internal tools)
    - bioregistry.io (broader registry coverage; less curation depth)
    - WCM-curated, seeded from `reciterai_keyword_relevance` (most control, most maintenance burden)
3. **Discovery process.** LLM clustering analogous to subtopics, or hand-curated to start? If LLM, what's the corpus — methods sections from synopses, or a separate extraction pass?
4. **Parent–canonical cardinality.** Is the canonical→parent edge 1:1 (every canonical tool has exactly one parent), or 1:N (RRID-resolved tools spanning multiple parent ecosystems, e.g. a Python library that ships with Bioconductor)? This is a data-modeling decision that affects downstream query shape.

Until those four are written down (1–2 pages), no Phase 8 date is honest. The middle ground v1 flagged — schema accommodation without producer — persists, but it's now an *explicit* deferred commitment rather than aspirational drift.

### Teeth, not placeholder

A `docs/tools-producer-model.md` placeholder gets missed. The deferral needs enforcement:

1. **Four GitHub issues** opened with label `decision-deferred`, one per question above. They are the canonical artifact, not a markdown placeholder. Each issue states the question, the options under consideration, and the trigger for closing (a decision, not a passage of time).
2. **`ROADMAP.md` blocks Phase 8**: under Phase 8, the entry reads *"Blocked on resolution of #5, #6, #7, #8. Do not start producer implementation until all four are closed."* This makes the dependency machine-readable to anyone running `/gsd-progress` or scanning the roadmap.
3. **CI-enforced PR check** (when Phase 8 work begins): a GitHub Actions workflow runs on PRs touching `pipeline_tools/` or any `TOOL#` producer code. The workflow `grep`s the PR body for each of the four issue numbers (`#5`, `#6`, `#7`, `#8`) and queries the GitHub API to confirm each is `state: closed`. Workflow fails on absent reference or any still-open issue. Not a checkbox on a template — a bot that blocks merge. Checkboxes get ticked; CI doesn't lie.

The point: a deferral with teeth has the same shape as a normal blocking issue — it just labels itself "we chose to defer" instead of "we haven't decided yet." Both are tracked the same way.

---

## 11. Mapping — what collapsed and what didn't

### Six structural decisions that absorbed the architectural gaps

| Decision | v1 gaps collapsed |
|---|---|
| Hot/cold path split | G-2, G-3, G-12, G-21, G-25, G-26, G-27, G-28 |
| `hierarchy_version` first-class | G-5, G-7, G-22 |
| Review state as records | review-gate gaps (per-topic and global) |
| Content-addressed stages | G-3, G-14, G-17 |
| Structured change signaling | G-6, G-29, G-30 |
| Registered quality gates | G-11, G-16, G-20, G-31, G-33 |
| Both aggregations | G-19 |
| Feedback events | G-12, G-23 |

### Residual items — split honestly

Not all "residual" items are pure hygiene. Two have architectural surface, even if small, and mislabeling them risks someone grabbing one in 30 minutes between meetings and getting it wrong.

#### Architectural — small surface, but needs context

- **G-35 — Stage-keyed Bedrock model IDs.** *Re-scoped 2026-05-12 after discovery*: the spec v1/v2 framing of "centralize the scattered model IDs" was wrong about scattering — `utils/bedrock_client.py` already owned the three pinned `*_MODEL` constants and every consumer imported them. The actual Phase 9 work was additive: introduce `MODEL_IDS_BY_STAGE: dict[str, str]` mapping stage names (`"screening"`, `"scoring"`, `"subtopic_discovery"`, etc.) to those existing constants, so `utils/stage_records.compute_input_hash` can include the relevant model ID by stage name without scripts importing the constants directly into their hash-building call sites. The earlier "migration story" (old `STAGE#` records aging out under a new hash layout) does not apply — there is no refactor and no constant rename, only an additive dict, so existing scripts and any future `STAGE#` records co-exist without invalidation.
- **G-32 — `excluded_topics` source-of-truth.** *Resolved 2026-05-11 in `058529c`*: option (c) chosen — frozen config-file list at `config/excluded_topics.json`, carrying forward the two Phase 5 entries (`implementation_science`, `oral_craniofacial_health`). A processing-tracker source can replace this later without a contract change since the bundler treats the file as opaque input. Left in this section for the record; no further action.
- **G-36 — `pipeline_hierarchy/` tests.** The reproducibility test (same inputs → same sha256) only becomes meaningful *after* §6 moves `generated_at` out of `hierarchy.json`. So G-36 ships alongside Decision 5, not as standalone cleanup.

#### Pure maintenance — independent commits, no architectural alignment needed

- **G-1** — Move ReciterDB column references into `utils/env_check.py` so upstream schema renames break at preflight, not mid-pipeline.
- **G-18** — Lift magic numbers (subtopic confidence floor 0.35, `TIE_EPSILON`, the §2 cold-path-trigger thresholds) into a config module; document each.
- **G-24** — Document the sensitive-topic exclusion list (`spotlight/sensitive_gate.py`) with rationale. Compliance-adjacent.
- **G-34** — Document the minimum IAM policy for the pipeline-runner role. `docs/aws-iam-pipeline-policy.json` already exists but isn't referenced from `GETTING_STARTED.md`.
- **G-37** — End-to-end integration test against fixtures. Independent of any specific decision; ships when fixtures are ready.

The distinction matters for triage: a contributor scanning for "good first issue" should reach for the pure-maintenance items first. The architectural-small-surface items want someone who's read this spec.

---

## 12. Implementation sequencing

Time estimates are rough, in working-days of focused effort. They exist so the "build substrate first" path doesn't win by default — see the decision below the table.

| Phase | Scope | Estimate |
|---|---|---|
| Issue #4 | Bundler (per-topic augmented → `hierarchy_full.json`). G-32 resolved as frozen config list. | **shipped `058529c`** |
| Issue #2 | Parent-prefix prevention in `relabel_subtopics.py` prompt + validator. Becomes the first registered quality gate (§7) once the gates framework exists; until then, an inline validator in `relabel_subtopics.py`. Cleanup re-run on 87 existing violations pending. | **shipped `ca3a7ed`** (cleanup pending) |
| Phase 9 | Decision 4 (content-addressed `STAGE#` records) + Decision 6 part 1 (gates framework). Substrate the hot path will lean on. Includes G-35 centralization since `input_hash` depends on it. | 5–8 days |
| Phase 10 | Decision 1 (hot/cold split). Reframed issue #3. Incremental `rollup_by_cwid.py`, EventBridge cron, hot-path failure handling, input-boundary thresholds. | 8–12 days |
| Phase 11 | Decision 2 (`hierarchy_version` first-class) + Decision 3 (review state) + Decision 5 (`diff.json` + write-order). Cold path becomes real. | 8–12 days |
| Phase 12 | Feedback events (§9), both aggregations (§8), residual maintenance items (§11). | 4–6 days |
| Phase 8 | Axis 2 producer — *blocked* on the four `decision-deferred` issues (§10). No estimate until those resolve. | n/a |

### Substrate-vs-degenerate decision

The hot path's main user-visible value is "don't rerun when nothing changed." A `last_successful_run_at` timestamp check captures most of that; content-addressed `input_hash` captures the narrower case where inputs changed but outputs would be byte-identical.

Given Phase 9 lands in ~1–2 weeks of focused work, **build the substrate first**. A month-out substrate would change this call — the throwaway from a degenerate timestamp version isn't worth the rework if substrate is close.

### Slip-detection checkpoint

A "switch to degenerate if it slips past three weeks" rule that nobody owns gets noticed at week six. The checkpoint that makes this real:

- **End of week 2 of Phase 9** (the calendar date is recorded in `STATE.md` when Phase 9 starts): review whether Phase 9 is on track to land within week 3. "On track" means substrate `STAGE#` records writing in dev, gate registry has at least the prefix gate registered, and `input_hash` schemas drafted for the three stages the hot path will touch.
- If not on track, branch the degenerate version: a hot path that uses `last_successful_run_at` as its skip signal, no `STAGE#` records, no gates framework. Ship Phase 10 against the degenerate substrate; eat the refactor when Phase 9 substrate later lands.
- Owner: whoever ships Phase 9 (named in the phase's `CONTEXT.md` when it's opened). The week-2 checkpoint is a calendar reminder, not "someone notices."

The decision is calendar-driven, not principle-driven. The estimates above plus the checkpoint are what makes it reviewable instead of aspirational.

---

*v1 of this spec is in git history if the gap enumeration is useful as a worklist. v2 is the architecture.*
