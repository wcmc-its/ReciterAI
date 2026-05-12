"""Phase 10 drift path: daily DRIFT# evaluator (D-04).

Runs after the hot-path window. Reads UNCOVERED_PMID#, LOW_CONFIDENCE_ASSIGNMENT#,
and STAGE#…failed rows over a rolling `drift_window_days` window
(default 14). Writes a `DRIFT#evaluation` row carrying:

- uncovered_rate (fraction of new PMIDs that triggered UNCOVERED_PMID)
- low_confidence_max_topic + low_confidence_max_count
- triggered_thresholds[] (list of which thresholds tripped)
- cold_run_recommended (boolean)

On `cold_run_recommended: true` the evaluator emits a severity-tagged
alert via `pipeline_common.alert.dispatch` (T11). Until T11 lands the
evaluator returns the structured payload and a `severity` field; the
state machine / cron handler is responsible for dispatching.
"""

__version__ = "0.1.0"
