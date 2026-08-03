# ADR — How a taxonomy change reaches production

**Status:** Accepted (2026-08-03; revised twice after review, then amended against measurement — see "Amendments")
**Fills the gap between:** `docs/taxonomy-methodology.md` (how the taxonomy is designed), `docs/research-area-split-criteria.md` (when a topic warrants splitting), `docs/hot-cold-paths.md` (names taxonomy evolution as a cold-path trigger and stops there).
**Related:** #352 (drift incident), #344 (`excluded_topics.json` never enforced), #307, #339, #204 (image-baked config).

## Context

### The taxonomy changes often, and every change is a manual slog

Eight edits to `taxonomy_v2.json` in 90 days, four inside a two-day window:

```
2026-08-01  retire neuroscience_neurology (#348)
2026-08-01  add basic_neuroscience + clinical_neurology (#346)
2026-08-01  retire pain_management_anesthesiology (#343)
2026-07-31  add pain_medicine + anesthesiology (#342)
2026-07-10  retire hematology_medical_oncology (#331)
2026-06-13  add hematology_medical_oncology (#228)
```

Not a settling cadence. #339 is an ongoing multi-step split programme.

### The taxonomy is a replicated artifact with no propagation mechanism

`taxonomy_v2.json` is read from a repo-relative path (`Path(__file__).parent / "taxonomy_v2.json"`), so it is baked into every artifact that reads it. All drift below is measured, not inferred:

| Site | How it gets there | Drift as of 2026-08-03 |
|---|---|---|
| `reciterai-hot-score` zip | `scripts/build_lambda_zips.sh` | was 3 weeks stale; fixed today |
| `reciterai-hot-assign` zip | same | same |
| `reciterai-hot-orchestrator` zip | same | same |
| Docker image (`reciterai-enrichment:db04a79`) | `Dockerfile` `COPY . .` | **stale by 5 taxonomy commits** |
| `reciterai-taxonomy-drift` zip | `scripts/build_lambda_zips.sh` | new in this ADR — see D5 |
| `hierarchy.json` on S3 | cold run publish | derived |
| `TOPIC#` partitions | scoring output | derived |
| SPS Aurora | `etl:dynamodb` | downstream |

Editing the repo changes none of them. There is no Lambda deploy script (`infra/README.md`: deployment "stays manual `aws lambda create-function` / `update-function-code`"), no drift check, and no runbook step tying a taxonomy edit to a redeploy.

The Docker row is the most dangerous, but not by the mechanism first written here. The staleness is real and precise: `db04a79` is a 2026-07-10 commit SHA, it is the newest tag in the ECR repo, it is what the **daily** enrichment task runs (`reciterai-enrichment` rev 8), and its bundled taxonomy carries 68 topics against `main`'s 69 — missing `anesthesiology`, `basic_neuroscience`, `clinical_neurology`, `pain_medicine` and still carrying the three retired ones.

*Revised 2026-08-03 after measurement.* The original text said a cold run launched today would publish that 68-topic set. **It could not, because no cold run can launch at all.** The `reciterai-cold` task-definition family has zero `ACTIVE` revisions — both revisions were deregistered on 2026-06-19 per the runbook's own "disarm between runs" hygiene — and the image they pinned (`204cold-127b9b0`) has been deleted from ECR. Further, no cold run has ever executed on Fargate: `/ecs/reciterai-cold` holds exactly one log stream, 1,843 bytes, ending `[--dry-run] no stages invoked.`, and log retention is unset so this is not an expiry artifact.

The real exposure is one step earlier, and survives that finding intact. Because every cold run must be built from scratch, the taxonomy it carries is whatever `TAG=cold-$(git rev-parse --short HEAD)` resolves to — and the runbook's preceding step instructs the operator to `git checkout <the commit with the intended flag state>`. The operator is told to select a commit by its **flag** state, with nothing checking its **taxonomy**. That is a live, unenforced path to publishing a wrong topic set, and it is not fixed by rebuilding the enrichment image. It is what D3's cold-run preflight closes, and it is why that preflight must validate against an external baseline rather than a peer handshake.

### What the missing mechanism cost

The 2026-07-10 retirement removed `hematology_medical_oncology` from the taxonomy and deleted its rows. Nobody redeployed. The Monday `cron(0 12 ? * MON *)` re-created it weekly for three weeks:

```
hematology_medical_oncology:  122 rows -> 07-13(5), 07-20(54), 07-27(52), 08-03(11)
neuroscience_neurology:        30 rows -> 08-03(30)      # retired 08-01, back on 08-03
pain_management_anesthesiology: 1 row  -> 08-03(1)
```

Symmetrically, #339's four new topics were absent from the deployed taxonomy, so the weekly path had never scored into them. This surfaced only because the Scholars Profile System noticed a missing `year` attribute on those exact five partitions and asked. Our own tooling reported nothing.

### Six traps, each independently confirmed

1. **Partial deploy splits the taxonomy.** Three separately-deployed Lambdas each carry a copy. On 2026-08-03 one `update-function-code` succeeded and two were refused, leaving `hot-score` at 69 topics and `hot-assign` at 68. Had the weekly run fired then, `assign_subtopics.py` would have `sys.exit(3)`d on the four unknown topic ids.

2. **`retire_topic.py` teaches a wrong causal model.** Its docstring names only the cold run as the re-mint vector. The words *Lambda*, *zip*, *deploy* appear nowhere in the file; *hot* nowhere in the docstring. `--help` shows only the docstring's first line. Its terminal output is `"action": "deleted"` with no next step.

3. **Order matters and is written down nowhere.** Delete rows before redeploying and they return. Republish before rescoring and the artifact reflects the old topic set.

4. **The publish stage does not enforce its own config** (#344). `bundle()` copies `excluded_topics.json` into metadata but never filters `topics`. `implementation_science` was config-excluded from 2026-05-11 yet served as a live research area until 2026-08-01, when it was removed by manually deleting its augmented file before bundling.

5. **The build is not reproducible from a clean clone.** `reciterai-hot-assign` bundles 66 `hierarchy_draft_*.json` files from gitignored `.planning/phases/04-subtopic-system/`. A fresh worktree fails with `hierarchy draft dir missing`, quietly enough to ship a missing zip.

6. **Comparing topic-id sets is too weak.** `taxonomy_v2.json` entries carry `label`, `description`, and `display_threshold` alongside `id`. Two artifacts can agree on ids and still score differently.

### Why the image-baked stance does not settle this

#204 and `CLAUDE.md` establish that `config/thresholds.json` is image-baked on purpose: flags must be reproducible per-run. That reasoning does not extend to the taxonomy.

**Baking gives per-artifact reproducibility, not cross-artifact atomicity.** Three Lambdas plus an image carry independent copies deployed by independent commands. Trap 1 follows directly.

**Taxonomy provenance is *supposed* to be recorded downstream, and is not.** Every `TOPIC#` row carries `topic_scores_version` and every published artifact carries `taxonomy_version` — but both are stamped from the literal `taxonomy_v2.json` top-level string `"taxonomy_v2"`, a static family label that has never moved. Measured 2026-08-03: all **113,605** `TOPIC#` rows carry the single value `"taxonomy_v2"`, unchanged across all seven post-creation edits to the file; rows minted 2026-08-01 for `basic_neuroscience`, a topic that did not exist the day before, are indistinguishable from rows minted months earlier. **We cannot currently answer "which taxonomy produced this row."**

That strengthens rather than weakens the case against the image-baked stance: thresholds leave no per-row trace, and the taxonomy's apparent trace is decorative. Establishing real provenance is new work — a forward-only `taxonomy_content_sha256` attribute alongside the existing label, since nothing can retroactively date the 113,605 existing rows. `utils/taxonomy.content_hash()` computes the value; wiring it into `utils/topic_records.build_topic_rows_for_pmid` is deliberately **not** part of this ADR's first tranche.

## Decision

### D1 — One ordered pipeline, one entrypoint, pinned to a commit SHA

Every taxonomy change follows one order, because each step invalidates the next:

```
1. PIN       resolve the change to a commit SHA + taxonomy content hash; open a change record
2. EDIT      taxonomy_v2.json (+ excluded_topics.json / hierarchy_draft_coverage.json if implicated)
3. BUILD     every artifact that bundles it, from the pinned SHA
4. DEPLOY    all of them together (D4)
5. VERIFY    every artifact's bundled taxonomy hash == the pinned hash
6. RECONCILE DynamoDB: retire_topic for removed topics, score_new_topics for added
7. PUBLISH   hierarchy.json  [human gate: operator-run cold run, ~$210]
8. NOTIFY    SPS (Aurora DELETE + etl:dynamodb where a topic was removed)
```

Implemented as `scripts/apply_taxonomy_change.sh`, following `deploy_cron.sh` / `apply_backup_config.sh` conventions: idempotent, `--dry-run`, `--verify`, per-step reporting.

Steps 4–5 were missing entirely. Doing step 6 before step 5 is the specific mistake that cost three weeks.

**Everything compares content hashes, never id sets** (trap 6). The pinned hash from step 1 is the single reference value for steps 5, D3, and D5.

### D2 — A change record makes the pipeline resumable across the human gate

Step 7 routes through the operator-gated cold run, so the script **cannot** run 1→8 in one invocation. It necessarily pauses for hours or days. Without state, this ADR would trade "undocumented sequence" for "documented sequence that stalls halfway with no record" — a new silent-failure mode.

Each change opens a `TAXONOMY_CHANGE#<id>` record carrying: change id, pinned commit SHA, taxonomy content hash, topics added/removed, per-step status and timestamps, and the operator who advanced each step. The script resumes from the record. D5 reads it to report stalls: *"change #348 stuck at step 6 for 4 days."*

This record is also the scaffolding for the recovery story below.

### D3 — Runtime taxonomy handshake (the strongest control)

D4 shrinks the partial-deploy window but cannot eliminate it — three `update-function-code` calls are never atomic — and D5 is a daily check, so a deploy that goes partial Monday morning after the check but before `cron(0 12 ? * MON *)` still lets the weekly run fire against a split taxonomy.

Therefore: **each bundle carries the content hash of its `taxonomy_v2.json`, and the orchestrator performs a handshake at run start.** All taxonomy-bearing components report their hash; any mismatch aborts the run before scoring, routing through the existing `WriteHotRunFailed` → `NotifyError` path.

This makes trap 1 unrepresentable *at runtime*, independent of deploy discipline. It is the only runtime control that works when someone deploys outside the script (D5 layer 2 also survives bypass, on its daily cadence), and it is strictly stronger than "detected within 24 hours."

The cold run gets the same check as a **blocking preflight** at start — with one addition: a peer handshake passes a *uniformly* stale image (see "Decision NOT taken"), so the preflight validates against an external baseline — the D2 change record's pinned hash, or `origin/main` until D2 exists. It is operator-gated and expensive, so a preflight costs nothing relative to publishing a wrong hierarchy from a stale image — which, per the Context section, is the live risk today.

**Placement, measured 2026-08-03.** The preflight goes in `pipeline_cold/run.py::main()`, immediately after `select_stages` and before the stage loop, so it covers all three documented runtime modes (dry-run, full run, `--from-stage`). Two caveats that must not be lost:

- This is **bypass-resistant, not non-bypassable.** All eight cold stages have their own `__main__`, and the task definition's `deploy_notes.runtime_modes` establishes raw `containerOverrides` commands as a supported operator mode — so `["python","-m","pipeline_hierarchy.publish"]` skips `run.py::main()` entirely. The preflight must therefore *also* sit in `pipeline_hierarchy/publish.py::main()`, which is the stage that actually overwrites the prod `latest/` pointers SPS reads.
- An abort is **silent to the operator.** `scripts/run_cold_run.sh` polls only in dry-run mode; a real run prints `LAUNCHED` and exits 0 without waiting. The preflight still prevents the damage, but the operator learns about it from the Teams card, not the terminal.

The premise the whole handshake depends on was verified: `taxonomy_v2.json` is genuinely present in the image — `.dockerignore` excludes `docs`, `tests`, `*.md`, `out` and `.planning`, but not it, and the `Dockerfile` does `COPY . .`.

### D4 — Deploy via published versions and alias flips

Publish a new version of all taxonomy-bearing Lambdas first, then flip their aliases. The inconsistency window drops from minutes (sequential code uploads) to seconds (sequential alias updates), and rollback becomes an alias flip rather than a rebuild-and-redeploy.

If any target fails, already-flipped aliases roll back and the script exits non-zero. A partial taxonomy is an outage, not a warning. D3 remains the backstop for the residual seconds.

### D5 — Two-layer, two-way drift detection

**Layer 1 — artifact drift, two-way.** A naive "deployed vs `origin/main`" check fires during every normal change window, and alert fatigue kills drift checks fast. So compare against the change record (D2), not against main:

- *deployed artifacts vs the change record's hash* — an unexpected deployed state. Real incident, alert immediately.
- *change record vs `origin/main`* — a merged edit not yet propagated. Expected briefly; alerts after N days, and reports which step it is stuck on.

**Layer 2 — data drift.** Artifact checks catch the cause; this catches the effect, including causes nobody enumerated. Two-way, because the 2026-07-10 incident was two-way:

- **orphan** — no `TOPIC#` partition exists outside the current topic-id set. A retired topic still accumulating rows means something is scoring against a taxonomy we no longer ship. `ERROR`.
- **unscored** — no taxonomy topic lacks a `TOPIC#` partition. This is the #339 half: four added topics that the deployed taxonomy had never heard of, so nothing ever scored into them. Expected briefly after an add, so `WARN`.

The 2026-07-10 incident *manifested* here, in DynamoDB, and was found here. Both layers are cheap; run them on the daily cadence, ahead of Monday.

**Amended 2026-08-03 — the originally specified second check is withdrawn.** It read *"every partition's `topic_scores_version` corresponds to the current taxonomy hash."* That is not implementable: `topic_scores_version` is an invariant family label (see "Why the image-baked stance does not settle this"), so the comparison would pass on 100% of rows by construction. A check that can only return "clean" is worse than no check — it manufactures assurance. It is therefore omitted rather than approximated, and the orphan/unscored pair above carries layer 2 alone. Both directions of the incident are still caught; what is lost is the ability to detect a *uniformly* stale deployment where the topic set happens to match — which is D3's and layer 1's job, not a data check's.

Orphan findings additionally report each orphan partition's most recent `created_at`, which is what made the 07-10 incident legible (rows dated 07-13, 07-20, 07-27 on a topic retired 07-10). This is evidence attached to a finding, not a detection criterion: `created_at` is absent on older rows and its absence must never read as a violation.

Implemented as `pipeline_taxonomy_drift/checker.py` behind its own Lambda and EventBridge rule (`reciterai-taxonomy-drift-daily`, `cron(0 15 * * ? *)`), with `scripts/check_taxonomy_data_drift.py` as the read-only operator surface. It writes `DRIFT#taxonomy`, **not** `DRIFT#evaluation` — that partition is owned by `pipeline_drift/evaluator.py` and consumed by `pipeline_feedback/sweep.py`.

**This checker is itself a fifth taxonomy replication site.** It must bundle `taxonomy_v2.json` to know the current topic set. It is in scope for D1's build step and D3's handshake like every other taxonomy-bearing artifact — being the drift checker does not exempt it, and a stale checker is a checker that reports clean.

Note also that its severity vocabulary is not the alerting module's: `DRIFT#` rows use `OK|WARN|ERROR` while `pipeline_enrichment.alerting` accepts `INFO|WARN|ERROR` and raises `ValueError` on anything else. `OK` is a valid row value and an invalid alert severity, and the webhook is unset in CI — so passing severity straight through would pass every test and raise on the first clean deployed run. Only `WARN`/`ERROR` are dispatched.

Findings write `DRIFT#` rows and fire Teams cards via `pipeline_enrichment.alerting`, consistent with the existing model (no CloudWatch alarms here by design).

### D6 — `retire_topic.py` states the full ordering; standalone use is flagged

The docstring names the hot path as a re-mint vector alongside the cold run. On success the script prints the remaining ordered steps to stderr rather than terminating on `"deleted"`. `--help` surfaces the ordering guard.

D1 declares the pipeline the only supported path, but nothing enforced that: `retire_topic.py`, `score_new_topics.py`, and the bundle scripts all remain individually runnable, and this ADR deliberately keeps them as standalone data tools. So the pipeline exports a context token, and the tools warn loudly and require `--standalone` when run without it. Otherwise the next incident is someone with good intentions running the tools in the old order, with stderr breadcrumbs as the only defense.

The tools do **not** perform the redeploy themselves. Conflating a data tool with a deploy tool creates a worse failure mode than the one being fixed.

### D7 — Publish enforces exclusions, and "excluded" is defined

**Semantics, pinned:** *excluded* means **scored but not published**. Excluded topics remain in `taxonomy_v2.json`, continue to accumulate `TOPIC#` rows, and cost scoring spend every Monday; they are withheld only from `hierarchy.json`. Verified current state: `implementation_science` holds 4,193 rows and `oral_craniofacial_health` 94, both actively scored, neither published.

This is the defensible reading — it keeps the data available for the re-evaluation D8 requires — but it was previously ambiguous, which is exactly the class of gap this ADR exists to close.

**Enforcement (#344):** `_build_topics()` skips any `topic_id` in the excluded list when assembling `topics`, not only when assembling the `excluded_topics` metadata. Manual pre-bundle file deletion stops being the mechanism.

**One-time reconciliation:** verify no other excluded topic is currently being served. The list has two entries and both are confirmed withheld as of `v2026-08-01b`, but the check belongs in the same change that lands enforcement.

### D8 — Exclusion re-evaluation is a job, not a rule

Exclusion entries record `reason` and `activity_count` at decision time, and go stale silently. `implementation_science` was excluded for `"clustering quality fail"` at `activity_count: 1314`; it now has 4,193 activities and 15 well-formed subtopics, so the recorded reason is simply false.

Entries gain `as_of` and `evidence`. The re-test fires when a topic's activity count doubles from its recorded value, **with a floor** so small topics don't trip on 1→2.

"Re-tested when X" is a rule with nobody watching it — the same shape as trap 2. So the doubling check folds into the daily D5 job, which already touches both the config and the data, and emits the same Teams card. The re-test itself is #307's overlap measurement (share of PMIDs already covered by another topic), not a subjective quality call.

### D9 — Build inputs are tracked and consolidated

The 66 gitignored `hierarchy_draft_*.json` files move into a tracked location. The real smell is 66 loose files as a build input, so they consolidate into **one generated, versioned hierarchy-input artifact** carrying its own content hash, which D3's handshake can then cover alongside the taxonomy.

Until that lands, the build fails loudly with a message naming the canonical checkout. This is stated as a sequence, not an "or" — the loud failure is the interim state, not an accepted end state.

## Recovery story, per step

D4 gives deploy rollback. The rest needs stating, because step 6 is destructive and step 7 can fail after it succeeds — leaving fresh scores against an old published hierarchy.

| Step | Idempotent? | Recovery |
|---|---|---|
| 1–3 PIN / EDIT / BUILD | yes | re-run; artifacts are content-addressed |
| 4 DEPLOY | yes | alias flip back to prior version (D4) |
| 5 VERIFY | yes (read-only) | n/a |
| 6 RECONCILE | **no — destructive** | DynamoDB PITR, 35-day window. Deletions are not otherwise recoverable; the change record retains the deleted topic ids and row counts |
| 7 PUBLISH | yes | previous `v<date>` prefix remains in S3; repoint `latest/` |
| 8 NOTIFY | yes | re-run `etl:dynamodb` |

The ordering protects the worst case: a failure at 7 leaves correct data and a stale artifact, which is recoverable, rather than a published artifact referencing deleted topics.

**Run-failure alerting is confirmed present** and needs no addition: every state in `reciterai-hot-path` catches to `WriteHotRunFailed` (writing `STAGE#hot_run#GLOBAL` / `RUN#FAILED#<start>`) then `NotifyError` → `reciterai-alert-dispatcher` with `severity: ERROR`, `title: "Hot path failure"`. A D3 handshake abort therefore surfaces on Teams by construction.

## Decision NOT taken

**Serving the taxonomy from S3 at runtime** would eliminate steps 3–5 entirely: publish to S3, done. It is the only option that makes the pain structurally disappear rather than survivable through discipline. Not taken now because it adds a cold-start network dependency and a new failure mode to the weekly path, and D1–D5 stop the bleeding far cheaper.

*Amended 2026-08-03:* this section originally added that "the reproducibility objection is weak because `topic_scores_version` already records per-row provenance." That premise is false as measured — the attribute is an invariant label, not provenance. The reproducibility objection to S3 serving is therefore **stronger** than this ADR first credited: today there is no per-row record of which taxonomy scored a row, so moving the taxonomy off the artifact would remove the last thing pinning a run to a known topic set. This does not reverse the decision — the cold-start dependency is still the deciding reason — but the revisit trigger below should be read knowing the provenance argument does not currently support S3 serving, and would need `taxonomy_content_sha256` to exist first.

**Middle option — an S3 "expected taxonomy hash" pointer.** Keep baking the taxonomy, but publish a tiny hash pointer to S3 that every component validates at cold start. This is D3's handshake with an *external* source of truth rather than a peer-comparison: it catches the case where all components agree with each other but all are stale — which is precisely the 2026-07-10 incident, and which a peer handshake alone does **not** catch. It gets most of the S3-serving benefit for traps 1 and 3 with none of the data-fetch dependency, and it is the natural stepping stone if the revisit trigger fires.

Worth noting D3 as specified detects *disagreement*; only the hash pointer or D5 detects *uniform staleness*. If D5's daily cadence proves too slow, promote this from stepping stone to immediate work.

## Revisit trigger

A `DRIFT#` row firing within 24 hours is D5 **working**, not the architecture failing. Revisit the "not taken" section when either holds:

- drift reaches data or a downstream team — a `TOPIC#` partition outside the current topic-id set, a published artifact with the wrong topic set, or an SPS-reported inconsistency
- artifact-level `DRIFT#` rows exceed **3 per quarter** after D1–D5 ship, indicating the procedure is routinely bypassed rather than occasionally missed

Either means discipline is not the constraint and the architecture is. Reopen rather than adding more process.

## Consequences

**Easier:** one command with a dry-run and a resumable change record, instead of a seven-step sequence reconstructed from memory. The 2026-07-10 incident becomes unrepeatable silently — D3 aborts the run, D5 catches it within a day, D4 makes the near-miss partial state a rollback rather than an outage.

**Harder:** a taxonomy edit that previously "completed" in five minutes now completes in five minutes *or visibly does not complete*, and stalls at the step-7 human gate are surfaced rather than forgotten. That is the intended trade.

**Not addressed:** cost. Step 7 still routes through the ~$210 cold run (`docs/cold-run-fargate-runbook.md`). This ADR makes propagation reliable, not recompute cheap. If change frequency keeps rising, incremental hierarchy republish is the next bottleneck. Nor does it address taxonomy *design* — whether a topic should exist, split, or retire stays with `docs/research-area-split-criteria.md` and #307's overlap test.

## Registration

Registered in `.planning/POLICIES.md` (the binding-policy index) and cross-referenced from `docs/hot-cold-paths.md`, `infra/README.md`, and `cli/retire_topic.py`'s docstring — the three surfaces where the old, wrong model was documented.

This ADR stays a standalone doc rather than becoming Decision 7 in `docs/RECITERAI-SPEC.md`, following the `docs/tool-context-style-decision.md` precedent. The spec's §0 status table has been stale since 2026-05-12 on four separate rows (including one marked "blocked" whose four issues all closed 2026-06-06), so folding a live decision into it would inherit a maintenance surface nobody is tending. A pointer is added to the spec instead.

**`.planning/POLICIES.md` is gitignored** (`.gitignore:54`), so its entry cannot ship in this or any PR — it exists only in the canonical checkout. Anyone working from a fresh clone or worktree will not see the registration. That is a gap in the registration mechanism itself, not something this ADR can close from inside the repo; it is worth deciding whether the binding-policy index belongs under version control.

## Amendments

**2026-08-03, after measurement.** The ADR was written from evidence gathered during the incident investigation. A subsequent pass measured its load-bearing claims directly against AWS and DynamoDB, and three did not survive. All three are corrected in place above; they are listed here because each was used as support for a decision:

1. **`topic_scores_version` does not record taxonomy provenance.** It is an invariant family label — 113,605/113,605 `TOPIC#` rows carry `"taxonomy_v2"`. This invalidated D5 layer 2's second check (withdrawn, not approximated) and weakened, rather than strengthened, the "Decision NOT taken" reasoning about S3 serving.
2. **The cold run cannot launch today.** Zero `ACTIVE` `reciterai-cold` task-definition revisions; the pinned image is deleted from ECR; no cold run has ever executed on Fargate. The stale-image risk is real but reaches production by a different route than first described — operator commit selection at build time, not reuse of a stale tag.
3. **The tier-2 Lambda redeploy is not a *taxonomy* risk.** No tier-2 Lambda bundles `taxonomy_v2.json` — verified across all 13 deployed zips; only `hot-orchestrator`, `hot-score` and `hot-assign` carry it. The redeploy remains real work with a real threshold delta, but it is independent of this ADR rather than blocking on it.

   *This item originally also claimed the threshold delta was "34 new keys with 3 defaulting on, not 43 with 4." That correction was wrong and is withdrawn — 43/4 was right.* 34/3 is the delta for `reciterai-drift-evaluator` alone, the newest tier-2 Lambda and the only one already carrying `eligibility_sweep_enabled`. Measured per-Lambda against each deployed zip's bundled `config/thresholds.json`: drift-evaluator 34 new / 3 on, onboarding-detector and -derive-topics 38 / 4, hot-rollup 39 / 4, hot-top-topic 43 / 4. The union across all five is **43 new keys, 4 defaulting on** — simultaneously the union and the per-Lambda maximum. Generalising from one Lambda to the tier is what produced the error, and it cut the stated blast radius by nine keys and one already-on flag. Noted rather than silently corrected because it is the same failure this ADR is about: a measurement taken on a narrower scope than the claim it was used to support.

The pattern is worth naming: every claim that failed was one where a *name* implied a *semantic* nobody had checked — a field called `topic_scores_version` that versions nothing, a task-definition family that exists but cannot run. D5 layer 1 and D3 both compare content hashes for exactly this reason; the same skepticism should apply when reading this document.
