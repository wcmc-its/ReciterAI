"""Shared utilities for hot/cold/spotlight/drift pipelines.

Currently exposes `alert.dispatch` — the Slack + GitHub-issue alerting
helper used by the drift evaluator (T10) and the hot-path Step Functions
Catch states (T7). See `alert.py` and the severity table in
`pipeline_drift/severity.py`.

`envelope` — STAGE# envelope parse + DynamoDB-typing helpers shared by the
per-stage Lambda handlers (hot path and onboarding); see `envelope.py`.
"""

__version__ = "0.1.0"
