"""Pure validation logic for REVIEW# approval workflow (Phase 11).

No I/O: no boto3, no os.environ, no filesystem access. All inputs are
injected as plain Python values. This module is unit-testable without
any infrastructure mocks.

Usage (from review.cli):
    from review.validator import RunSignals, validate
    signals = RunSignals(failed_stages=(...), ...)
    result = validate(yaml_dict, signals)
    if not result.ok:
        for err in result.errors:
            print(err)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

_CWID_PATTERN = re.compile(r"^cwid_[a-z]+\d+$")
_MIN_RATIONALE_LEN = 40


@dataclass(frozen=True)
class RunSignals:
    """Signals about the current cold-run quality gates.

    Injected at construction time; production wires this via
    review.store.read_run_signals. Tests construct it directly.

    Attributes:
        failed_stages: Stage names (e.g. "publish_hierarchy") where
            status="failed" was recorded for the current run.
        gate_block_errors: error_code values starting with "GATE_BLOCK_"
            from STAGE# publish rows for the current run.
        artifact_uri_exists: True when S3 HEAD on proposed_artifact_uri
            returns 200. Pre-resolved by the CLI before calling validate().
        low_confidence_count: Count of LOW_CONFIDENCE_ASSIGNMENT# rows
            for the current run (informational; not a hard gate here).
        uncovered_pmid_count: Count of uncovered PMID rows for the
            current run (informational; not a hard gate here).
    """

    failed_stages: tuple[str, ...] = ()
    gate_block_errors: tuple[str, ...] = ()  # error_codes starting with GATE_BLOCK_
    artifact_uri_exists: bool = True
    low_confidence_count: int = 0
    uncovered_pmid_count: int = 0


@dataclass(frozen=True)
class ValidationResult:
    """Result of a validate() call.

    Attributes:
        ok: True when all rules passed; False when any rule failed.
        errors: Tuple of human-readable error strings (empty when ok=True).
    """

    ok: bool
    errors: tuple[str, ...] = ()


def validate(yaml_dict: dict, signals: RunSignals) -> ValidationResult:
    """Validate a REVIEW# YAML dict against all approval rules.

    Rules (spec §4 + D-08):
      1. reviewer_cwid matches ^cwid_[a-z]+\\d+$
      2. rationale.strip() length >= 40 characters
      3. decision is exactly "approve" or "reject"
      4. proposed_artifact_uri resolves (signals.artifact_uri_exists=True)
      5. If decision == "approve": no failed_stages and no gate_block_errors

    Args:
        yaml_dict: Parsed YAML content from the operator-edited template.
        signals: Pre-fetched run signals from DDB + S3 (injected by caller).

    Returns:
        ValidationResult with ok=True and empty errors on success, or
        ok=False with a non-empty errors tuple describing each failure.
    """
    errors: list[str] = []

    # Rule 1: reviewer_cwid format
    cwid = yaml_dict.get("reviewer_cwid") or ""
    if not isinstance(cwid, str) or not _CWID_PATTERN.match(cwid):
        errors.append(
            r"reviewer_cwid must match ^cwid_[a-z]+\d+$ "
            f"(got {cwid!r})"
        )

    # Rule 2: rationale length (strip whitespace first)
    rationale = yaml_dict.get("rationale") or ""
    if not isinstance(rationale, str) or len(rationale.strip()) < _MIN_RATIONALE_LEN:
        errors.append(
            f"rationale must be ≥ {_MIN_RATIONALE_LEN} chars after whitespace strip "
            f"(got {len((rationale or '').strip())} chars)"
        )

    # Rule 3: decision enum
    decision = yaml_dict.get("decision")
    if decision not in ("approve", "reject"):
        errors.append(
            f"decision must be 'approve' or 'reject' (got {decision!r})"
        )

    # Rule 4: artifact URI resolves
    if not signals.artifact_uri_exists:
        uri = yaml_dict.get("proposed_artifact_uri", "<unset>")
        errors.append(
            f"proposed_artifact_uri does not resolve (S3 HEAD 404): {uri}"
        )

    # Rule 5: gate-failure refusal — only blocks approve, not reject
    if decision == "approve":
        if signals.failed_stages:
            errors.append(
                f"cannot approve — failed stages: {list(signals.failed_stages)}"
            )
        if signals.gate_block_errors:
            errors.append(
                f"cannot approve — gate blocks: {list(signals.gate_block_errors)}"
            )

    return ValidationResult(ok=not errors, errors=tuple(errors))
