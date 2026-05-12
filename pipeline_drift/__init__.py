"""Phase 10 drift path: daily DRIFT# evaluator (D-04).

Runs after the hot-path window. Reads UNCOVERED_PMID#, LOW_CONFIDENCE_ASSIGNMENT#,
and STAGE#…failed rows over a rolling `drift_window_days` window
(default 14). Writes a `DRIFT#evaluation` row carrying:

- uncovered_rate (fraction of new PMIDs that triggered UNCOVERED_PMID)
- low_confidence_max_topic + low_confidence_max_count
- triggered_thresholds[] (list of which thresholds tripped)
- cold_run_recommended (boolean)

On `cold_run_recommended: true` the evaluator emits a severity-tagged
alert via `pipeline_common.alert.dispatch` (T11). The condition-to-
severity mapping (D-11 draft) lives in `pipeline_drift.severity`.
The evaluator itself returns a structured payload + `severity` field;
the cron handler dispatches the alert.
"""

__version__ = "0.1.0"
