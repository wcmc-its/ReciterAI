"""Drift-condition → severity mapping (Phase 10 T11 draft D-11 table).

Single source of truth for severity classification across the pipeline.
The evaluator's threshold logic in `pipeline_drift.evaluator.evaluate`
computes the structural facts (rates, max counts, presence of failed
rows); this module names them as conditions and maps each to a severity
the alert dispatcher can route.

The mapping intentionally mirrors the table in `.planning/phases/
10-hot-cold-path-split/10-PLAN.md` §T11 and is duplicated in human-
readable form at `docs/severity.md` (T13). Edits should keep all three
in sync.

T12 finalizes the table via code review; until then `severity.md`
records the draft assumption per condition.
"""

from __future__ import annotations

from enum import Enum

from pipeline_drift.evaluator import DriftEvaluation


class Condition(str, Enum):
    """Named drift / pipeline conditions."""

    # Bedrock / model layer
    BEDROCK_PMID_PARSE_FAIL = "bedrock_pmid_parse_fail"
    BEDROCK_THROTTLE_RECOVERED = "bedrock_throttle_recovered"
    BEDROCK_SERVICE_OUTAGE = "bedrock_service_outage"

    # Drift evaluator
    UNCOVERED_RATE_WARN = "uncovered_rate_warn"   # 3–5%
    UNCOVERED_RATE_ALERT = "uncovered_rate_alert"  # ≥5%
    LOW_CONFIDENCE_TOPIC_WARN = "low_confidence_topic_warn"   # 30–50
    LOW_CONFIDENCE_TOPIC_MAX = "low_confidence_topic_max"     # >50

    # Pipeline / infra
    STAGE_FAILED_RETRY_EXHAUSTED = "stage_failed_retry_exhausted"
    SCHEMA_VALIDATION_FAIL = "schema_validation_fail"
    HOT_PATH_LOCK_COLLISION = "hot_path_lock_collision"


# D-11 draft severity table. Keys MUST cover every Condition.
SEVERITY_TABLE: dict[Condition, str] = {
    Condition.BEDROCK_PMID_PARSE_FAIL: "WARN",
    Condition.BEDROCK_THROTTLE_RECOVERED: "WARN",
    Condition.BEDROCK_SERVICE_OUTAGE: "ERROR",

    Condition.UNCOVERED_RATE_WARN: "WARN",
    Condition.UNCOVERED_RATE_ALERT: "ERROR",
    Condition.LOW_CONFIDENCE_TOPIC_WARN: "WARN",
    Condition.LOW_CONFIDENCE_TOPIC_MAX: "ERROR",

    Condition.STAGE_FAILED_RETRY_EXHAUSTED: "ERROR",
    Condition.SCHEMA_VALIDATION_FAIL: "ERROR",
    Condition.HOT_PATH_LOCK_COLLISION: "WARN",
}

# Conditions whose severity implies a cold-run is recommended.
COLD_RUN_TRIGGERS: frozenset[Condition] = frozenset(
    {
        Condition.UNCOVERED_RATE_ALERT,
        Condition.LOW_CONFIDENCE_TOPIC_MAX,
    }
)


def severity_for(condition: Condition) -> str:
    """Return the WARN/ERROR severity for a named condition."""
    return SEVERITY_TABLE[condition]


def cold_run_recommended(conditions: list[Condition]) -> bool:
    """True iff any condition in the list is a cold-run trigger."""
    return any(c in COLD_RUN_TRIGGERS for c in conditions)


def classify_drift_evaluation(
    evaluation: DriftEvaluation,
    *,
    uncovered_warn_floor: float = 0.03,
    low_confidence_warn_floor: int = 30,
) -> list[Condition]:
    """Map a DriftEvaluation's structural facts to named conditions.

    Mirrors the threshold ladder in `evaluator.evaluate` but enumerates
    the *named* conditions for alerting, including the warn-band
    distinctions (3–5%, 30–50) that the evaluator collapses into a
    single WARN severity. Callers feed the returned list to
    `severity_for` to get the dispatcher severity, or to
    `cold_run_recommended` to confirm whether to surface
    `cold_run_recommended: true` to operators.
    """
    out: list[Condition] = []

    rate = evaluation.uncovered_rate
    # Alert floor read from triggered_thresholds so we don't re-derive
    # the threshold here — single source of truth is the evaluator.
    if "uncovered_rate_alert" in evaluation.triggered_thresholds:
        out.append(Condition.UNCOVERED_RATE_ALERT)
    elif rate >= uncovered_warn_floor:
        out.append(Condition.UNCOVERED_RATE_WARN)

    max_count = evaluation.low_confidence_max_count
    if "low_confidence_topic_max" in evaluation.triggered_thresholds:
        out.append(Condition.LOW_CONFIDENCE_TOPIC_MAX)
    elif max_count >= low_confidence_warn_floor:
        out.append(Condition.LOW_CONFIDENCE_TOPIC_WARN)

    if "stage_failures_in_window" in evaluation.triggered_thresholds:
        out.append(Condition.STAGE_FAILED_RETRY_EXHAUSTED)

    return out
