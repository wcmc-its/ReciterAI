"""Tests for gates.registry — Phase 9 task 4 substrate."""

from __future__ import annotations

import pytest

from gates import registry
from gates.registry import (
    GateResult,
    SEVERITY_BLOCK,
    SEVERITY_WARN,
    any_blocked,
    list_gates,
    register_gate,
    run_gates,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test starts with an empty registry and restores afterward.

    Without this, decorators from other test files (or from
    production gates that get imported transitively) would leak in.
    """
    snap = registry._snapshot_registry_for_tests()
    registry._reset_registry_for_tests()
    yield
    registry._restore_registry_for_tests(snap)


# ---------- registration ----------


def test_register_gate_records_function_with_stage_and_severity():
    @register_gate(stage="publish", severity=SEVERITY_BLOCK)
    def my_gate() -> GateResult:
        return GateResult(name="my_gate", passed=True, severity=SEVERITY_BLOCK, summary="ok")

    gates = list_gates(stage="publish")
    assert len(gates) == 1
    assert gates[0]["name"] == "my_gate"
    assert gates[0]["severity"] == SEVERITY_BLOCK


def test_register_gate_defaults_to_block_severity():
    @register_gate(stage="publish")
    def default_gate() -> GateResult:
        return GateResult(name="default_gate", passed=True, severity=SEVERITY_BLOCK, summary="")

    assert list_gates(stage="publish")[0]["severity"] == SEVERITY_BLOCK


def test_register_gate_rejects_unknown_severity():
    with pytest.raises(ValueError, match="severity must be one of"):
        register_gate(stage="publish", severity="critical")(lambda: None)


def test_register_gate_returns_function_unchanged():
    """Decorated fn must remain callable directly (for unit testing gates)."""

    @register_gate(stage="publish")
    def callable_gate() -> GateResult:
        return GateResult(name="callable_gate", passed=True, severity=SEVERITY_BLOCK, summary="")

    result = callable_gate()
    assert isinstance(result, GateResult)
    assert result.passed is True


def test_register_gate_supports_explicit_name():
    @register_gate(stage="publish", name="custom_label")
    def implementation_function() -> GateResult:
        return GateResult(name="custom_label", passed=True, severity=SEVERITY_BLOCK, summary="")

    gates = list_gates(stage="publish")
    assert gates[0]["name"] == "custom_label"


# ---------- run_gates ----------


def test_run_gates_executes_only_matching_stage():
    """A gate registered against `publish` MUST NOT run for `score`."""

    @register_gate(stage="publish")
    def publish_gate() -> GateResult:
        return GateResult(name="publish_gate", passed=True, severity=SEVERITY_BLOCK, summary="")

    @register_gate(stage="score")
    def score_gate() -> GateResult:
        return GateResult(name="score_gate", passed=True, severity=SEVERITY_BLOCK, summary="")

    results = run_gates(stage="publish")
    names = [r.name for r in results]
    assert names == ["publish_gate"]


def test_run_gates_preserves_registration_order():
    @register_gate(stage="publish")
    def first() -> GateResult:
        return GateResult(name="first", passed=True, severity=SEVERITY_BLOCK, summary="")

    @register_gate(stage="publish")
    def second() -> GateResult:
        return GateResult(name="second", passed=True, severity=SEVERITY_BLOCK, summary="")

    @register_gate(stage="publish")
    def third() -> GateResult:
        return GateResult(name="third", passed=True, severity=SEVERITY_BLOCK, summary="")

    names = [r.name for r in run_gates(stage="publish")]
    assert names == ["first", "second", "third"]


def test_run_gates_passes_kwargs_through():
    @register_gate(stage="publish")
    def kwargs_gate(*, hierarchy: dict, **_: object) -> GateResult:
        return GateResult(
            name="kwargs_gate",
            passed=bool(hierarchy.get("topics")),
            severity=SEVERITY_BLOCK,
            summary="topics present" if hierarchy.get("topics") else "no topics",
        )

    results = run_gates(stage="publish", hierarchy={"topics": {"a": {}}})
    assert results[0].passed is True

    results = run_gates(stage="publish", hierarchy={"topics": {}})
    assert results[0].passed is False
    assert results[0].summary == "no topics"


def test_run_gates_returns_empty_for_unknown_stage():
    assert run_gates(stage="nonexistent") == []


def test_run_gates_propagates_gate_exceptions():
    """A gate that raises is a programming error; the runner does not
    swallow. Gates handle expected failure modes themselves."""

    @register_gate(stage="publish")
    def buggy_gate() -> GateResult:
        raise RuntimeError("intentional")

    with pytest.raises(RuntimeError, match="intentional"):
        run_gates(stage="publish")


# ---------- severity semantics ----------


def test_gate_result_blocked_is_true_only_for_failed_block():
    failed_block = GateResult(name="x", passed=False, severity=SEVERITY_BLOCK, summary="")
    assert failed_block.blocked is True

    failed_warn = GateResult(name="x", passed=False, severity=SEVERITY_WARN, summary="")
    assert failed_warn.blocked is False

    passed_block = GateResult(name="x", passed=True, severity=SEVERITY_BLOCK, summary="")
    assert passed_block.blocked is False

    passed_warn = GateResult(name="x", passed=True, severity=SEVERITY_WARN, summary="")
    assert passed_warn.blocked is False


def test_any_blocked_detects_failed_block_in_mixed_results():
    results = [
        GateResult(name="a", passed=True, severity=SEVERITY_BLOCK, summary=""),
        GateResult(name="b", passed=False, severity=SEVERITY_WARN, summary=""),
        GateResult(name="c", passed=False, severity=SEVERITY_BLOCK, summary=""),
    ]
    assert any_blocked(results) is True


def test_any_blocked_false_when_only_warns_fail():
    """warn-severity failures MUST NOT halt the stage."""
    results = [
        GateResult(name="a", passed=True, severity=SEVERITY_BLOCK, summary=""),
        GateResult(name="b", passed=False, severity=SEVERITY_WARN, summary=""),
    ]
    assert any_blocked(results) is False


def test_any_blocked_false_when_all_pass():
    results = [
        GateResult(name="a", passed=True, severity=SEVERITY_BLOCK, summary=""),
        GateResult(name="b", passed=True, severity=SEVERITY_WARN, summary=""),
    ]
    assert any_blocked(results) is False


def test_any_blocked_empty_results():
    assert any_blocked([]) is False


# ---------- list_gates ----------


def test_list_gates_no_filter_returns_all():
    @register_gate(stage="publish")
    def g1() -> GateResult:
        return GateResult(name="g1", passed=True, severity=SEVERITY_BLOCK, summary="")

    @register_gate(stage="score")
    def g2() -> GateResult:
        return GateResult(name="g2", passed=True, severity=SEVERITY_BLOCK, summary="")

    all_gates = list_gates()
    stages = {g["stage"] for g in all_gates}
    assert stages == {"publish", "score"}


def test_list_gates_filter_returns_subset():
    @register_gate(stage="publish")
    def g1() -> GateResult:
        return GateResult(name="g1", passed=True, severity=SEVERITY_BLOCK, summary="")

    @register_gate(stage="score")
    def g2() -> GateResult:
        return GateResult(name="g2", passed=True, severity=SEVERITY_BLOCK, summary="")

    publish_gates = list_gates(stage="publish")
    assert len(publish_gates) == 1
    assert publish_gates[0]["name"] == "g1"


# ---------- GateResult shape ----------


def test_gate_result_details_defaults_to_empty_dict():
    r = GateResult(name="x", passed=True, severity=SEVERITY_BLOCK, summary="ok")
    assert r.details == {}


def test_gate_result_carries_structured_details():
    r = GateResult(
        name="parent_prefix",
        passed=False,
        severity=SEVERITY_BLOCK,
        summary="3 subtopics violated parent-prefix",
        details={
            "violations": [
                {"topic_id": "microbiome_research", "subtopic_id": "x"},
                {"topic_id": "microbiome_research", "subtopic_id": "y"},
                {"topic_id": "pulmonary_critical_care", "subtopic_id": "z"},
            ]
        },
    )
    assert len(r.details["violations"]) == 3
    assert r.details["violations"][0]["topic_id"] == "microbiome_research"
