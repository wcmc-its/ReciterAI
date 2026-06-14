"""shrink_guard gate (#224) — block a publish that catastrophically shrank.

`removed_subtopics` is computed by the hierarchy diff but never gated. This gate
BLOCKS when the new subtopic count fell below ``prev * (1 - max_shrink_fraction)``
vs the prior published artifact — a cheap last line of defense against a
degraded/partial upstream silently shipping a truncated hierarchy to SPS.

Pure function of the injected counts (no S3/DDB), like the other publish gates,
so it is trivially unit-testable and reusable (the #222 reconcile pass uses the
same shrink-vs-prior shape for its mapping-snapshot guard). First publish (prev
is None/0) passes. Override an intentional large recluster via
``python -m gates --force --force-reason "..."`` (or the caller's --force).
"""
from __future__ import annotations

from typing import Optional

from gates.registry import GateResult, SEVERITY_BLOCK, register_gate

_DEFAULT_MAX_SHRINK_FRACTION = 0.20


def _default_fraction() -> float:
    try:
        from utils.env_check import load_thresholds

        return float(
            load_thresholds().get(
                "hierarchy_publish_shrink_max_fraction", _DEFAULT_MAX_SHRINK_FRACTION
            )
        )
    except Exception:
        return _DEFAULT_MAX_SHRINK_FRACTION


@register_gate(stage="publish", severity=SEVERITY_BLOCK, name="shrink_guard")
def shrink_guard_gate(
    *,
    prev_subtopic_count: Optional[int] = None,
    new_subtopic_count: Optional[int] = None,
    max_shrink_fraction: Optional[float] = None,
    **_: object,
) -> GateResult:
    """Block a catastrophic shrink in the published subtopic count.

    Pass when there is no prior to compare (``prev_subtopic_count`` is None or 0,
    or ``new_subtopic_count`` is None) — first publish / unavailable prev is
    fail-open by design. Otherwise block when ``new < prev * (1 - fraction)``.
    """
    frac = (
        _default_fraction()
        if max_shrink_fraction is None
        else float(max_shrink_fraction)
    )

    if not prev_subtopic_count or new_subtopic_count is None:
        return GateResult(
            name="shrink_guard",
            passed=True,
            severity=SEVERITY_BLOCK,
            summary="no prior subtopic count to compare (first publish / prev unavailable)",
            details={"prev": prev_subtopic_count, "new": new_subtopic_count},
        )

    min_allowed = prev_subtopic_count * (1.0 - frac)
    if new_subtopic_count >= min_allowed:
        return GateResult(
            name="shrink_guard",
            passed=True,
            severity=SEVERITY_BLOCK,
            summary=(
                f"subtopic count {new_subtopic_count} within {frac:.0%} of prior "
                f"{prev_subtopic_count}"
            ),
            details={
                "prev": prev_subtopic_count,
                "new": new_subtopic_count,
                "max_shrink_fraction": frac,
            },
        )

    observed = 1.0 - (new_subtopic_count / prev_subtopic_count)
    return GateResult(
        name="shrink_guard",
        passed=False,
        severity=SEVERITY_BLOCK,
        summary=(
            f"subtopic count shrank {observed:.0%} ({prev_subtopic_count} -> "
            f"{new_subtopic_count}), exceeds the {frac:.0%} limit"
        ),
        details={
            "prev": prev_subtopic_count,
            "new": new_subtopic_count,
            "observed_shrink_fraction": round(observed, 4),
            "max_shrink_fraction": frac,
        },
    )
