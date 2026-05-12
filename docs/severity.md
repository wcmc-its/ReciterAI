# Pipeline alert severity (draft D-11)

Human-readable companion to `pipeline_drift/severity.py`. Kept in sync
with the table in `.planning/phases/10-hot-cold-path-split/10-PLAN.md`
§T11. T12 finalizes the table via code review.

## Severities

- **WARN** — Slack notification only. Operator awareness, no action
  required this cycle. Threshold is tunable in `config/thresholds.json`.
- **ERROR** — Slack notification AND a GitHub issue (labelled
  `drift-alert`). On the daily evaluator path, ERROR also sets
  `cold_run_recommended: true` on the `DRIFT#evaluation` row, which is
  the operator's signal to schedule a manual cold-path run via
  `python -m pipeline_cold.run --initiated-by drift_alert`.

The dispatcher deduplicates: a daily evaluator run that fires ERROR
comments on the existing open `drift-alert` issue rather than creating
a duplicate. Operators close the issue when the underlying cause is
addressed.

## Table

| Condition | Severity | Cold-run? | Rationale |
|---|---|---|---|
| Bedrock single-PMID parse fail | WARN | no | Retried automatically on next run; per-PMID `STAGE#…#pmid:*#failed` row is the queryable trail. |
| Bedrock throttle (recovered via retry) | WARN | no | Self-healing; alert only so operators notice systemic throttling trends. |
| Bedrock service outage halting hot path | ERROR | no | AWS-side incident; operators must reroute or pause cron until restored. |
| uncovered_PMID 14-day rate 3–5% | WARN | no | Approaching the cold-run threshold; surface early so operators can investigate corpus changes. |
| uncovered_PMID 14-day rate ≥5% | **ERROR** | **yes** | Per spec §9: a high rate of uncovered PMIDs implies the taxonomy is drifting from the corpus; cold-path mint cycle expected. |
| low_confidence count per topic 30–50 | WARN | no | Topic boundary may be fuzzy; informational. |
| low_confidence count per topic >50 | **ERROR** | **yes** | Subtopic split or merge likely needed; cold-run regenerates subtopic assignments. |
| Schema-validation failure on publish | ERROR | no | Paired with the Phase 9 block gate — publish refused; operator must triage the failing payload before next cron tick. |
| `STAGE#` failed with retry exhausted | ERROR | no | Stage cannot make progress on next run; needs operator intervention. |
| Hot-path lock collision (prior run still running) | WARN | no | Open Q5 resolution: skip-with-warn is conservative. Repeated collisions suggest the prior run is hanging — escalate manually. |

## Where this is enforced

- **Evaluator → severity name**: `pipeline_drift/severity.py`
  (`classify_drift_evaluation`, `severity_for`, `cold_run_recommended`).
- **Severity → transport routing**: `pipeline_common/alert.py`
  (`dispatch`).
- **Thresholds**: `config/thresholds.json` — keys
  `drift_uncovered_rate_alert`, `drift_low_confidence_topic_max`,
  `drift_window_days`.
- **Slack channel**: `#reciterai-pipeline`. Webhook URL is read from
  env var `RECITERAI_SLACK_WEBHOOK_URL` (Open Q 10.2 resolution).
- **GitHub issue label**: `drift-alert`. The dispatcher comments on the
  most-recent open issue with that label rather than creating duplicates.

## Tuning

Severity floors are deliberately conservative for the first quarter of
production drift data. Once a baseline is established, revisit the WARN
floors (`uncovered_warn_floor`, `low_confidence_warn_floor`) so the
WARN band catches *unusual* days rather than *every* day. The ERROR
floors are spec §9 limits and should not be relaxed without a written
ADR.
