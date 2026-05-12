"""Quality gate framework — Phase 9 Decision 6 substrate.

Gates are checks that run between produce and publish. Each gate is
registered against a stage by name with a severity (`block` or `warn`).
A stage running the substrate calls `run_gates(stage="...")` and
respects the result: any `block`-severity failure halts the stage
unless an explicit `--force --force-reason "..."` override is in play.

Adding a new gate: write a function in a sibling module that takes
keyword arguments (whatever the gate needs) and returns a
`GateResult`. Decorate with `@register_gate(stage=..., severity=...)`.
Import the module from `gates/__init__.py` or `gates/cli.py` so the
decorator runs at import time.

The framework lives in `gates.registry`. Individual gates land in
`gates.parent_prefix`, `gates.pii`, `gates.schema_validation`,
`gates.schema_roundtrip`. Phase 10+ adds `gates.coverage`,
`gates.rollup_reconciliation`, `gates.critic_rejection`.
"""

from gates.registry import (
    GateResult,
    any_blocked,
    list_gates,
    register_gate,
    run_gates,
)

__all__ = [
    "GateResult",
    "any_blocked",
    "list_gates",
    "register_gate",
    "run_gates",
]
