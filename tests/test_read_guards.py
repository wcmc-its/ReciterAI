"""#224: unit tests for utils.read_guards."""
import logging

import pytest

from utils.read_guards import DegradedReadError, guard_unexpected_empty


def test_passes_at_or_above_floor():
    rows = [1, 2, 3]
    assert guard_unexpected_empty(rows, source="x", mode="abort", floor=3) is rows
    assert guard_unexpected_empty(rows, source="x", mode="warn", floor=1) is rows


def test_abort_raises_below_floor():
    with pytest.raises(DegradedReadError) as ei:
        guard_unexpected_empty([], source="extract_x", mode="abort", floor=1)
    assert ei.value.source == "extract_x"
    assert ei.value.got == 0
    assert ei.value.floor == 1
    assert isinstance(ei.value, RuntimeError)


def test_abort_under_floor_nonempty():
    with pytest.raises(DegradedReadError):
        guard_unexpected_empty([1, 2], source="corpus", mode="abort", floor=5000)


def test_warn_logs_and_returns(caplog):
    with caplog.at_level(logging.WARNING):
        out = guard_unexpected_empty([], source="small_read", mode="warn", floor=1)
    assert out == []
    assert any("small_read" in r.getMessage() for r in caplog.records)
