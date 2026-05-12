"""Phase 10 hot path: weekly cron, delta-only, no version mints.

Composition:
- `orchestrator.py` is the entry: resolves `last_successful_hot_run_at`
  from `STAGE#hot_run#GLOBAL` (D-06), checks the Step Functions lock
  per Open Q5, and emits the initial state-machine input.
- `handlers/` are the per-Task Lambda entry-points. Each is a thin
  wrapper that invokes its upstream script in `--emit-envelope` mode
  and returns the envelope dict for the state machine's
  DynamoDB:PutItem SDK integration to persist (D-07).
- `state_machine.asl.json` is the Step Functions definition. Co-owned
  with `infra/` for deploy wiring (T12).
"""

__version__ = "0.1.0"
