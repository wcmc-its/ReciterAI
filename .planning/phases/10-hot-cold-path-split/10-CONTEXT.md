# Phase 10 — Hot/Cold Path Split: CONTEXT

*Discussed 2026-05-12. Hand-rolled (Phase 9 precedent — this repo skips the GSD discuss-phase tooling because ROADMAP.md doesn't satisfy the parser's `### Phase N:` detail-section requirement).*

## Domain

Implements spec [§2 Decision 1](../../../docs/RECITERAI-SPEC.md#2-decision-1--two-paths-not-one). Splits the conflated single pipeline into four independent jobs with different cadence, trigger, and substrate-write semantics. Reframes [#3](https://github.com/wcmc-its/ReciterAI/issues/3) (end-to-end orchestration).

Spec estimate: 8–12 days.

## Four jobs, not one orchestrator

The original framing ("hot path + cold path") is preserved at the spec level but expanded here based on scout findings:

| Job | Trigger | Runs under | Cadence v1 | Mints versions? |
|---|---|---|---|---|
| **Hot path** | EventBridge cron | AWS Step Functions | Weekly (daily later) | Never |
| **Cold path** | Operator | Local Python (workstation or one-off EKS) | On demand | Yes (`taxonomy_version`, `hierarchy_version`) |
| **Spotlight refresh** | EventBridge cron | Standalone Lambda | Monthly + dirty-check gate | No |
| **Drift evaluator** | EventBridge cron | Standalone Lambda | Daily, after hot path | No |

### Stage assignment

| Stage | Hot | Cold | Spotlight | Drift |
|---|---|---|---|---|
| `score_publications` | ✓ delta | ✓ full |  |  |
| `assign_subtopics` | ✓ delta | ✓ full |  |  |
| `discover_subtopics` |  | ✓ |  |  |
| `relabel_subtopics` |  | ✓ |  |  |
| `rollup_by_cwid` | ✓ incremental | ✓ full |  |  |
| `backfill_spotlight` |  | ✓ | ✓ gated |  |
| `publish_hierarchy` (Phase 9) |  | ✓ |  |  |
| `drift_evaluator` |  |  |  | ✓ |

Hot path is **three stages**: score → assign → incremental rollup. Spotlight regen and version-minting moved out.

## Locked decisions

### D-01: Hot path runs under AWS Step Functions
Lambda's 15-minute ceiling can't host a multi-hour Bedrock Batch run; a state machine can. Each stage is a Task state — either a Lambda invocation (thin orchestration) or a native SDK integration. Bedrock Batch wait is a Wait + Choice poll loop OR EventBridge Pipes pattern (decide in plan-phase). Matches the Scholars@WCM Step Functions ETL pattern; operational muscle memory carries.

### D-02: Cold path runs from operator workstation (or one-off EKS task)
No state machine. `python -m pipeline_cold.run` invoked manually. Multi-hour wall time; gated by human approval at multiple points (Phase 11 brings review gates online). Step Functions ceremony for a human-gated workflow is overhead.

### D-03: Spotlight is its own monthly cron with a dirty-check gate
**Threshold v1**: ≥3 of the top-50 subtopics each accumulate ≥5 new in-window publications since the last spotlight run.

**Implementation**: first step in the spotlight Lambda reads `STAGE#score_publications#…` + `STAGE#assign_subtopics#…` records produced since the last `STAGE#spotlight_refresh#GLOBAL` `complete` row, builds the dirty-subtopic set, applies the threshold. Below threshold → write a `skipped` STAGE# row and exit 0. At or above → run the full spotlight pipeline.

Spotlight is editorial/narrative, not real-time. Decoupling cadence from hot-path PMID-delta arrival bounds Opus cost by spotlight's own cadence. Failure isolation: spotlight breakage doesn't take down weekly scoring.

### D-04: Drift evaluator is its own daily cron
Runs after the hot-path window. Reads `UNCOVERED_PMID#…`, `LOW_CONFIDENCE_ASSIGNMENT#…`, and `STAGE#…failed` rows over a rolling 14-day window. Writes `DRIFT#evaluation` with `cold_run_recommended` boolean. Severity-driven alerting per D-11.

### D-05: Package layout — `pipeline_hot/`, `pipeline_cold/`, `pipeline_spotlight/`, `pipeline_drift/`, plus `pipeline_common/`
Mirrors `pipeline_hierarchy/` (Phase 7). `pipeline_common/` holds shared things Phase 10 introduces: dirty-set computation, run-orchestrator base classes, Step-Functions-envelope helpers. **Does not** absorb existing `utils/stage_records.py` or `gates/` — those stay where they are; moving them is churn for no gain.

### D-06: `last_successful_hot_run_at` derived from latest `STAGE#hot_run#GLOBAL` complete row
No new state shape. One DynamoDB Query at hot-path start: `PK = "STAGE#hot_run#GLOBAL"`, `SK begins_with "RUN#"`, `ScanIndexForward = false`, `Limit = 1`, filter `status = "complete"`. The hot-path orchestrator writes its own `STAGE#hot_run#GLOBAL` envelope around the whole run, in addition to per-stage rows the state machine writes for each Task.

### D-07: STAGE# write semantics split — direct (cold/spotlight/drift) vs envelope-via-state-machine (hot)
**The key idempotency move surfaced in discussion.** Today (Phase 9), Python writes STAGE# directly at the end of work. A crash between "work done" and "row written" loses the completion signal; next run does the work again.

Under Step Functions, Python returns an envelope (`records_written`, `output_pointer`, `model_ids_snapshot`, `cost_observed_usd`); the next Task state — a DynamoDB:PutItem SDK integration — writes the `complete` row. The gap between "work done" and "completion recorded" is owned by the state machine, which doesn't itself crash partway. Python's resume logic (`PROCESSING#pmid_*` sentinels) already handles re-runs from a partial state.

**API change to `utils/stage_records`**: separate the row-builder from the writer. Expose `build_complete_record(...) -> dict` (pure function) alongside the existing `write_complete(...)` (which becomes `build_complete_record + DynamoDB PutItem`). The state machine's PutItem state references the builder's output via JSONPath; cold-path Python calls the writer directly.

### D-08: Dirty-set has two shapes
- **Rollup** orchestration: dirty CWIDs (set of `personIdentifier` whose papers got new scores in this hot run). Maps to `rollup_by_cwid.py --cwids …` incremental mode.
- **Spotlight** orchestration: dirty subtopics (set of `subtopic_id` in top-50 with ≥N new in-window pubs). Maps to D-03's threshold gate.

Spec §2's "dirty-flagged CWIDs" language conflates these because of the implicit assumption that spotlight is per-faculty. Scout finding: `spotlight/pool_ranker.py` shows spotlight is global subtopic-keyed. The phase summary update (when this lands) corrects the spec wording in §2.

### D-09: `cost_estimate_usd` → `cost_observed_usd`
Existing field name is a footgun — it's populated from observed Bedrock counters, not pre-flight estimates. Six months in, someone will read "estimate" and reason wrongly about budgets. Rename to `cost_observed_usd`.

**Migration**: Phase 9 STAGE# rows are few (only `publish_hierarchy` writes today, and only since 2026-05-12). Breaking rename is acceptable; one-off script overwrites the existing field name in any rows in the prod table during Phase 10 execution. Caught by Phase 9's tests when the field name changes — pre-flight check ensures no consumers depend on the old name.

**Skipped rows MUST populate `cost_observed_usd: Decimal("0")` with `skip_reason` set, never omit the field.** Cost telemetry treats skips as first-class zeros; absence breaks aggregation queries.

### D-10: EventBridge IaC v1 = single-file + bash wrapper
`infra/eventbridge.json` holds rule definitions; `scripts/deploy_cron.sh` wraps `aws events put-rule` + `lambda update-function-code` + `iam attach-role-policy`.

**Migration threshold to CDK, written down explicitly**: when ReciterAI adds *any one* of (a) a second Step Function, (b) a second cron, (c) a fifth managed AWS resource — adopt CDK as a new phase. Reciter-CDK is the natural target if at that point the cross-repo coupling is acceptable; net-new ReciterAI-CDK repo if not.

The threshold lives in this CONTEXT and in `infra/README.md` (created in plan-phase), not in operator memory.

### D-11: Drift alerts have severity (WARN | ERROR)
- WARN → Slack only (e.g., Bedrock rate-limit retries succeeding, single-pmid Bedrock parse failures within tolerance)
- ERROR → Slack + open/comment GitHub issue with `drift-alert` label (e.g., schema-validation failure on publish, persistent uncovered-PMID rate over threshold, Bedrock service outage halting hot path)

Severity-per-condition table built in plan-phase (open question 4 below).

### D-12: Hot-path event records: `UNCOVERED_PMID#`, `LOW_CONFIDENCE_ASSIGNMENT#`
Write-only this phase. Drift evaluator (D-04) reads them. **Consumption to drive re-clustering decisions is Phase 12** (§9). Record shape per spec §9.

### D-13: Step Functions for hot path = hot path stages cannot directly invoke `python -m gates`
The gates framework (Phase 9) runs as a Python module; the state machine can shell out to Lambda but each Task state has limits. Solution: each hot-path stage Lambda imports `gates.registry.run_gates(stage=<stage_name>)` directly within its handler, surfaces any `block` result as a Lambda error, and the state machine catches and writes a `failed` STAGE# row. The `python -m gates` operator CLI (Phase 9) continues to work standalone for ad-hoc invocations.

## Out of scope (explicit)

- `hierarchy_version` stamping on activity records → Phase 11 (spec §3)
- `REVIEW#` records / cold-path review gates → Phase 11 (spec §4)
- `diff.json` + write-order contract + `Cache-Control` on `latest/*` → Phase 11 (spec §5)
- Critic-reject event records → Phase 12 (spec §9)
- Both aggregations (exclusive + inclusive) → Phase 12 (spec §8)
- Feedback-event *consumption* (UNCOVERED_PMID → Sonnet sweep, etc.) → Phase 12 (spec §9)
- Full CDK adoption → triggered by D-10 threshold
- A/B testing of hierarchy versions → spec §3 revisit triggers (Phase 11)

## Canonical refs

- `docs/RECITERAI-SPEC.md` — §2 (locked), §5 (substrate), §7 (gates), §9 (event records), §11 G-18 (thresholds cluster) — MUST read before planning
- `.planning/phases/09-substrate-stages-and-gates/09-SUMMARY.md` — substrate API and integration pattern
- `utils/stage_records.py` — Phase 9 substrate; API change per D-07
- `gates/registry.py` — Phase 9 framework; reused per D-13
- `pipeline_hierarchy/publish.py` — reference integration pattern for STAGE# wiring
- `utils/dynamodb_helpers.py` — boto3 client pattern; existing `PROCESSING#pmid_*` resume sentinels
- `spotlight/pool_ranker.py` — subtopic-keyed scope confirmation
- `backfill_all.py` — existing operator-CLI argparse shape (template for cold-path orchestrator)
- `backfill_spotlight.py` — existing operator-CLI argparse shape (template for spotlight orchestrator)

## Code context — reusable assets

- **Substrate already in place**: `utils.stage_records` has `compute_input_hash`, `should_skip`, `find_existing_complete`, `write_complete/skipped/failed`. Phase 10 splits build-vs-write per D-07.
- **Gate framework already in place**: `gates.registry.register_gate(stage=…, severity=…)` decorator + `run_gates(stage=…)` runner + `python -m gates` CLI. Extend with new gates as needed; pattern set by `gates/parent_prefix.py`, `gates/pii_scan.py`, `gates/schema_roundtrip.py`.
- **DynamoDB table**: single `reciterai-chatbot` table holds all record types (composite PK/SK). No new tables.
- **Cost counters**: existing scripts log per-call Bedrock usage to `backfill_log.md`. Plan-phase task surfaces these as a callable counter object that envelope-builders consume for `cost_observed_usd`.
- **Resume sentinels**: `PROCESSING#pmid_*` (utils/dynamodb_helpers.py lines 276–370) handle per-PMID idempotency; orthogonal to STAGE# and complementary.

## Open questions for plan-phase

These are deferrable — they affect implementation details, not architecture.

1. ~~**ReciterDB refresh cadence and timing.**~~ **Resolved 2026-05-12**: ReciterDB refreshes daily. Hot-path cron locked to `cron(0 12 ? * MON *)` (Mondays 12:00 UTC = 08:00 EDT) — after typical overnight refresh, inside US business hours for pager response. Spec §2 says "weekly v1; daily once stable" — staying weekly for v1.
2. **Slack channel + env var name.** Dedicated `#reciterai-pipeline` vs piggyback on an existing WCM ITS infra channel. Env var: `SLACK_WEBHOOK_URL` (conventional but ambiguous if a second webhook ever arrives) vs `RECITERAI_SLACK_WEBHOOK_URL` (uglier but explicit).
3. **Bedrock Batch wait mechanism.** Wait + Choice poll loop vs EventBridge Pipes pattern. Research in plan-phase; the choice affects state-machine complexity and cost (transition billing).
4. **Severity table for D-11.** Map each condition (Bedrock throttle, parse-fail, uncovered-PMID rate threshold, low-confidence threshold, schema-validation fail, infra-outage halt) to WARN vs ERROR. Document in `pipeline_drift/severity.md` during plan-phase.
5. **Bedrock Batch job locking.** If a previous weekly job is still running when the next cron fires, behavior is undefined. Lock semantics: skip-with-warn vs queue-and-defer vs hard-fail. Decide in plan-phase.

## Risks

- **Incremental rollup correctness.** Silent breakage hotspot. Phase 10 plan must include a parity test: given a non-empty CWID dirty-set, full re-rollup output `==` (incremental on dirty-set) merged with (prior rollup on undirty-set). Run on every commit touching `rollup_by_cwid.py`.
- **STAGE# namespace volume.** Per-PMID failed rows accumulate. Need either a TTL or scope-limit. Sizing estimate in plan-phase.
- **Step Functions cost.** State-machine transitions are billable. Cap retry counts; avoid unbounded poll loops.
- **Long-running Bedrock Batch + cron collision** (open question 5).
- **D-09 rename is cross-cutting.** Touches Phase 9 substrate API + tests + any downstream reader. Mitigated by Phase 9's tight test coverage (115 tests); rename surfaces failures.
- **Spotlight dirty-check threshold (D-03) is a guess.** "≥3 subtopics × ≥5 pubs" is plausible; if too tight, spotlight goes stale; if too loose, we pay Opus weekly anyway. Plan-phase makes thresholds config-driven so they're tunable without redeploy.

## Deferred ideas (captured from discussion)

- Full CDK adoption for ReciterAI IaC — trigger per D-10
- A/B testing of hierarchy versions — spec §3 revisit triggers, Phase 11+
- Feedback-loop consumption (Sonnet sweep on UNCOVERED_PMID pool; quality-signal use of CRITIC_REJECT events) — Phase 12
- Time-machine reads ("show me Dr. X's profile as of v…") — spec §3 revisit trigger #1
- Cross-repo IaC consolidation into Reciter-CDK — only if D-10 migration trigger fires AND cross-repo coupling is acceptable

---

**Next:** `/gsd-plan-phase 10` — or hand-rolled `10-PLAN.md` per Phase 9 precedent. Plan resolves the five open questions and produces a wave-able task breakdown.
