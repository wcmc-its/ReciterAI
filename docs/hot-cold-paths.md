# Hot / Cold / Spotlight / Drift — operator guide

Phase 10 split the ReciterAI pipeline into four lanes with different
invocation models. This guide is the runbook: how each lane is
triggered, what to do when an alert fires, and how to recover from
common failures.

| Lane | Cadence | Trigger | Mints versions? | What it produces |
|---|---|---|---|---|
| **Hot path** | Mondays 12:00 UTC | EventBridge → Step Functions | No | Per-PMID topic scores, subtopic assignments, CWID rollups for new PMIDs since the last successful tick. |
| **Cold path** | Manual / on demand | Operator runs `python -m pipeline_cold.run` | **Yes** — taxonomy + hierarchy versions | Full pipeline mint: rescore, reassign, rediscover subtopics, relabel, full rollup, spotlight backfill, hierarchy publish. |
| **Spotlight refresh** | 1st of each month, 13:00 UTC | EventBridge → Lambda | No | Dirty-check gate; if dirty enough, regenerates the spotlight; otherwise writes a `skipped` STAGE# row. |
| **Drift evaluator** | Daily 14:00 UTC | EventBridge → Lambda | No | Writes one `DRIFT#evaluation` row; dispatches WARN/ERROR alerts. |

Cron schedules are defined in `infra/eventbridge.json` and applied by
`scripts/deploy_cron.sh`. All schedules are tunable in JSON without
touching code.

---

## Hot path — `pipeline_hot`

**Cadence**: weekly, Mondays 12:00 UTC (locked 2026-05-12 per Open Q
10.1; ReciterDB confirmed daily refresh).

**Invocation**: EventBridge rule `reciterai-hot-weekly` starts the
`reciterai-hot-path` Step Functions execution. No manual invocation is
required in normal operation.

**State machine** (`pipeline_hot/state_machine.asl.json`):

```
orchestrator → score → assign → rollup → write-hot-run-row → end
```

Each handler is an envelope-mode Lambda invocation of the matching
script (`score_publications.py`, `assign_subtopics.py`,
`rollup_by_cwid.py`). The state machine `Catch`-es every Task and
writes a `STAGE#…#failed` row plus a Notify state (alert dispatch).

**Locking** (Open Q 10.5): at start, the orchestrator does a
`ListExecutions` on the state machine. If any execution is `RUNNING`,
it writes a `STAGE#hot_run#GLOBAL` `skipped` row with
`skip_reason: "prior_run_in_progress"` and exits success. The next
weekly tick picks up where we left off; the WARN alert lets operators
notice if collisions are frequent.

**Manual invocation** (rarely needed; use cold path for systemic
issues):

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:$AWS_ACCOUNT_ID:stateMachine:reciterai-hot-path \
  --name "manual-$(date -u +%Y%m%dT%H%M%S)" \
  --input '{"initiated_by":"operator"}'
```

**Verifying a hot run**:

```bash
aws dynamodb query \
  --table-name reciterai \
  --key-condition-expression "PK = :pk" \
  --expression-attribute-values '{":pk":{"S":"STAGE#hot_run#GLOBAL"}}' \
  --no-scan-index-forward --limit 5
```

The top row is the latest tick; `status == complete` confirms success.

---

## Cold path — `pipeline_cold.run`

**Cadence**: on demand. Triggered manually by an operator when:

- A drift alert recommends it (`cold_run_recommended: true` in the
  latest `DRIFT#evaluation` row, **or** an ERROR-severity Slack/issue
  fires for `uncovered_rate_alert` or `low_confidence_topic_max`).
- The taxonomy is intentionally evolved (new topics added, splits/merges).
- A model upgrade (a Bedrock model ID changes in
  `MODEL_IDS_BY_STAGE`) requires a full rescore.

**Invocation**:

```bash
# Full mint:
python -m pipeline_cold.run --initiated-by drift_alert

# Dry-run (lists the seven stages without invoking them):
python -m pipeline_cold.run --dry-run

# Resume from a specific stage:
python -m pipeline_cold.run --from-stage rollup_by_cwid
```

**Stages** (sequential):

1. `score_publications` — full rescore of all in-scope PMIDs.
2. `assign_subtopics` — full subtopic reassignment.
3. `discover_subtopics` — propose new subtopics from clustering.
4. `relabel_subtopics` — refine subtopic labels.
5. `rollup_by_cwid` — full faculty rollup.
6. `backfill_spotlight` — regenerate spotlight from scratch.
7. `publish_hierarchy` — mint new hierarchy version + version-stamped artifacts.

Each stage writes its own STAGE# row via the direct-write path (no
envelope/handoff — cold path runs in a single Python process).

`--initiated-by` must be one of `operator`, `drift_alert`, `scheduled`
(spec §9 — the drift evaluator surfaces *recommended*, but the
operator decides whether to run).

**Important**: the cold path mints `hierarchy_version` and
`taxonomy_version`. The hot path explicitly does NOT. Downstream
consumers (SPS, dashboards) pin to version strings; the cold-path mint
is what gives them a new pin to adopt.

---

## Spotlight refresh — `pipeline_spotlight`

**Cadence**: monthly, 1st of the month at 13:00 UTC.

**Invocation**: EventBridge rule `reciterai-spotlight-monthly` invokes
the `reciterai-spotlight-orchestrator` Lambda.

**Gate** (per D-03): the orchestrator queries STAGE# rows since the
last `STAGE#spotlight_refresh#GLOBAL` `complete`, builds dirty-subtopic
counts by joining new-PMID classifications against the top-50 from
`spotlight/pool_ranker.py`, and applies the thresholds from
`config/thresholds.json`:

```
spotlight_dirty_subtopic_min       = 3   (D-03)
spotlight_dirty_pubs_per_subtopic_min = 5   (D-03)
```

If fewer than 3 subtopics each accumulate at least 5 new in-window
pubs, the orchestrator writes a `skipped` STAGE# row and exits. At or
above threshold, it invokes `backfill_spotlight.py --publish`.

**Manual invocation** (force a regen regardless of dirty-check):

```bash
aws lambda invoke \
  --function-name reciterai-spotlight-orchestrator \
  --payload '{"initiated_by":"operator","force":true}' \
  /tmp/spotlight-response.json
```

The `force: true` flag bypasses the dirty-check gate. Use sparingly —
the gate exists because Opus reads on the spotlight pipeline aren't
free.

---

## Drift evaluator — `pipeline_drift`

**Cadence**: daily, 14:00 UTC.

**Invocation**: EventBridge rule `reciterai-drift-daily` invokes the
`reciterai-drift-evaluator` Lambda.

**What it does**:

1. Reads UNCOVERED_PMID#, LOW_CONFIDENCE_ASSIGNMENT#, and
   STAGE#…failed rows over `config.drift_window_days` (default 14).
2. Computes `uncovered_rate`, `low_confidence_max_topic`,
   `low_confidence_max_count`, `triggered_thresholds[]`.
3. Writes one `DRIFT#evaluation` row keyed on the window-end day.
4. Dispatches a severity-tagged alert via `pipeline_common.alert.dispatch`:

   - **OK**: no alert.
   - **WARN**: Slack only (`#reciterai-pipeline`).
   - **ERROR**: Slack + GitHub issue (creates a new `drift-alert`
     issue, or comments on the existing open one to dedupe).

See `docs/severity.md` for the complete D-11 severity table.

### Drift-alert response runbook

When a Slack `[ERROR]` lands or a `drift-alert` GitHub issue is opened
or commented on:

1. **Read the DRIFT# row.** Pull the most recent `DRIFT#evaluation`:

   ```bash
   aws dynamodb query --table-name reciterai \
     --key-condition-expression "PK = :pk" \
     --expression-attribute-values '{":pk":{"S":"DRIFT#evaluation"}}' \
     --no-scan-index-forward --limit 1
   ```

   Inspect `triggered_thresholds`, `uncovered_rate`,
   `low_confidence_max_topic`, `cold_run_recommended`.

2. **Investigate the offending events.** For `uncovered_rate_alert`,
   look at the UNCOVERED_PMID# rows over the window:

   ```bash
   aws dynamodb scan --table-name reciterai \
     --filter-expression "begins_with(PK, :p) AND created_at >= :since" \
     --expression-attribute-values \
       '{":p":{"S":"UNCOVERED_PMID#"},":since":{"S":"2026-04-28T00:00:00Z"}}' \
     --projection-expression "pmid,top_topics,top_topic_score"
   ```

   Aggregate `top_topics[].topic_id` to find the topics most commonly
   "almost matching" — these are candidates for taxonomy expansion.

   For `low_confidence_topic_max`, focus on the topic named in
   `low_confidence_max_topic`. Likely needs a subtopic split or merge.

3. **Decide whether to cold-run.** ERROR with `cold_run_recommended:
   true` is the system's recommendation, not a directive. If the
   underlying corpus shift looks transient (one bad PubMed batch), it
   may resolve on its own next week. If it looks structural (taxonomy
   genuinely doesn't cover an emerging area), schedule the cold path.

4. **Cold-run**:

   ```bash
   python -m pipeline_cold.run --initiated-by drift_alert
   ```

   The `--initiated-by drift_alert` flag is the audit-trail link
   between the DRIFT# row and the cold-path STAGE# rows it produced.

5. **Close the GitHub issue** once the underlying cause is addressed
   (cold path completed, taxonomy bumped, or transient signal cleared).
   Daily evaluator runs will create a new issue if the condition fires
   again; until then, repeated ERROR-severity fires comment on the
   open issue rather than spawning duplicates.

### Threshold tuning

Tunable in `config/thresholds.json` (no redeploy needed; Lambda reads
on cold start):

```json
{
  "drift_uncovered_rate_alert": 0.05,       // ERROR floor
  "drift_low_confidence_topic_max": 50,     // ERROR floor
  "drift_window_days": 14
}
```

The WARN-band floors live in
`pipeline_drift.severity.classify_drift_evaluation` (defaults 3% and
30). Revisit after the first quarter of production drift data; the
defaults are conservative.

---

## Related references

- `infra/eventbridge.json` — cron rules + targets.
- `infra/lambda_iam_policy.json` — minimum-privilege policy.
- `infra/README.md` — D-10 single-file IaC + CDK migration trigger.
- `docs/severity.md` — D-11 alert severity table.
- `docs/data-model-and-queries.md` — full DynamoDB record-type reference.
- `docs/stage-records-and-gates.md` — Phase 9 STAGE# substrate this phase builds on.
- `pipeline_hot/state_machine.asl.json` — Step Functions definition.
