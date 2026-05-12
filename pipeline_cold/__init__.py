"""Phase 10 cold path: operator-driven full re-runs.

Per D-02 the cold path runs from an operator workstation (or one-off
EKS task), not under Step Functions. Step Functions ceremony for a
human-gated workflow is overhead; multi-hour wall time and Phase-11
review gates favor a plain Python orchestrator.

Stages (per CONTEXT stage-assignment table):
  score → assign → discover → relabel → rollup → backfill_spotlight
  → publish_hierarchy

Each stage uses direct STAGE# writes (no envelope mode) since the cold
path runs in a single Python process. The orchestrator wraps the whole
run with a STAGE#cold_run#GLOBAL row that carries `initiated_by` ∈
{"operator", "drift_alert", "scheduled"} so drift-triggered runs are
distinguishable from operator-initiated ones in audit queries.
"""

__version__ = "0.1.0"
