"""
Gate registry — decorator + runner.

Two severities:

- `block` — failure halts the integrating stage. `STAGE#` row is written
  with `status=failed`, no downstream side effects (no S3 upload, no
  DynamoDB mutation). Override only via `python -m gates --force
  --force-reason "..."`, which records the reason into the next
  `STAGE#` row's `force_reason` field.
- `warn` — failure is surfaced but does NOT halt the stage. Result
  flows into the stage's `STAGE#` row as `gate_warnings` for audit.

Gates are pure functions of their inputs — they do not mutate state,
write to DynamoDB, or call out to S3. The integrating stage handles
all I/O around them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

SEVERITY_BLOCK = "block"
SEVERITY_WARN = "warn"
_VALID_SEVERITIES = (SEVERITY_BLOCK, SEVERITY_WARN)


@dataclass
class GateResult:
    """Result of a single gate evaluation.

    `passed` is the only field a stage NEEDS to check for control flow.
    `details` exists so failed gates can carry structured context (e.g.,
    the list of subtopics that violated `parent_prefix`) into the
    `STAGE#` row's `failure_details`.
    """

    name: str
    passed: bool
    severity: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """A failed `block`-severity gate is what halts a stage."""
        return self.severity == SEVERITY_BLOCK and not self.passed


@dataclass
class _RegisteredGate:
    name: str
    stage: str
    severity: str
    fn: Callable[..., GateResult]


_REGISTRY: list[_RegisteredGate] = []


def register_gate(
    *,
    stage: str,
    severity: str = SEVERITY_BLOCK,
    name: str | None = None,
) -> Callable[[Callable[..., GateResult]], Callable[..., GateResult]]:
    """Register `fn` as a gate against `stage` at `severity`.

    The decorator returns the original function unchanged, so the
    decorated function is still callable directly (handy for tests).

    `name` defaults to the wrapped function's `__name__`; pass an
    explicit string when you want a different label in reports.
    """
    if severity not in _VALID_SEVERITIES:
        raise ValueError(
            f"gate severity must be one of {_VALID_SEVERITIES!r}, got {severity!r}"
        )

    def decorator(fn: Callable[..., GateResult]) -> Callable[..., GateResult]:
        gate_name = name or fn.__name__
        _REGISTRY.append(
            _RegisteredGate(name=gate_name, stage=stage, severity=severity, fn=fn)
        )
        return fn

    return decorator


def _gates_for(stage: str) -> list[_RegisteredGate]:
    return [g for g in _REGISTRY if g.stage == stage]


def list_gates(stage: str | None = None) -> list[dict[str, str]]:
    """Introspection helper — `python -m gates --list` uses this."""
    pool = _REGISTRY if stage is None else _gates_for(stage)
    return [{"name": g.name, "stage": g.stage, "severity": g.severity} for g in pool]


def run_gates(*, stage: str, **kwargs: Any) -> list[GateResult]:
    """Run every gate registered against `stage`, in registration order.

    `kwargs` are passed verbatim to each gate. Gates should ignore
    unknown kwargs (use `**_` or named parameters with explicit
    defaults); the runner does not filter.

    A gate that raises propagates the exception — gates are not
    sandboxed. If a gate needs to handle expected failure modes
    internally, it should catch and return `GateResult(passed=False,
    ...)` itself rather than relying on the runner.
    """
    return [g.fn(**kwargs) for g in _gates_for(stage)]


def any_blocked(results: list[GateResult]) -> bool:
    """True iff any result is a failed `block`-severity gate."""
    return any(r.blocked for r in results)


# ---------- test-only helpers ----------


def _reset_registry_for_tests() -> None:
    """Wipe the registry. Test-only. Production code MUST NOT call."""
    _REGISTRY.clear()


def _snapshot_registry_for_tests() -> list[_RegisteredGate]:
    """Return a copy of the registry. Test-only."""
    return list(_REGISTRY)


def _restore_registry_for_tests(snapshot: list[_RegisteredGate]) -> None:
    """Replace the registry with `snapshot`. Test-only."""
    _REGISTRY.clear()
    _REGISTRY.extend(snapshot)
