"""Unit tests for pipeline_enrichment/spend_tracker (#37).

The DDB counter is simulated by `_FakeSpendTable`: the `ADD` update increments
`cumulative_usd` and returns ALL_NEW; the conditional `SET` advances
`last_notified_threshold` and raises if the condition is violated (mirroring a
ConditionalCheckFailedException).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from pipeline_enrichment import spend_tracker


@pytest.fixture(autouse=True)
def _fixed_increment(monkeypatch):
    """Pin the $increment to 100 regardless of config drift."""
    monkeypatch.setattr(spend_tracker, "_increment_usd", lambda: Decimal("100"))


class _FakeSpendTable:
    def __init__(self, cumulative="0", notified=None):
        self.cumulative = Decimal(str(cumulative))
        self.notified = notified  # None or int
        self.adds = 0
        self.sets = 0

    def update_item(self, **kw):
        ue = kw["UpdateExpression"]
        vals = kw.get("ExpressionAttributeValues", {})
        if ue.startswith("ADD"):
            self.adds += 1
            self.cumulative += vals[":c"]
            attrs = {"cumulative_usd": self.cumulative}
            if self.notified is not None:
                attrs["last_notified_threshold"] = self.notified
            return {"Attributes": attrs}
        # conditional SET last_notified_threshold
        self.sets += 1
        nm = vals[":nm"]
        if self.notified is not None and self.notified >= nm:
            raise RuntimeError("ConditionalCheckFailed")
        self.notified = nm
        return {}


def _alerts():
    captured: list[dict] = []

    def fn(severity, title, message, context=None, **kw):
        captured.append(
            {"severity": severity, "title": title, "context": context or {}}
        )
        return True

    return captured, fn


def test_crossing_first_100_boundary_alerts():
    table = _FakeSpendTable(cumulative="40")
    alerts, fn = _alerts()
    spend_tracker.record_spend(
        Decimal("70"), started_at="2026-03-01T00:00:00Z", table=table, alert_fn=fn
    )
    assert len(alerts) == 1  # 40 + 70 = 110 crosses $100
    assert alerts[0]["severity"] == "WARN"
    assert alerts[0]["context"]["threshold_crossed"] == 100
    assert table.notified == 100


def test_below_boundary_no_alert():
    table = _FakeSpendTable(cumulative="10")
    alerts, fn = _alerts()
    spend_tracker.record_spend(
        Decimal("5"), started_at="2026-03-01T00:00:00Z", table=table, alert_fn=fn
    )
    assert alerts == []
    assert table.notified is None


def test_same_band_does_not_realert():
    # Already notified at 100; 110 → 150 stays in the 100 band.
    table = _FakeSpendTable(cumulative="110", notified=100)
    alerts, fn = _alerts()
    spend_tracker.record_spend(
        Decimal("40"), started_at="2026-03-01T00:00:00Z", table=table, alert_fn=fn
    )
    assert alerts == []
    assert table.notified == 100


def test_crossing_into_next_band_alerts():
    table = _FakeSpendTable(cumulative="190", notified=100)
    alerts, fn = _alerts()
    spend_tracker.record_spend(
        Decimal("20"), started_at="2026-03-01T00:00:00Z", table=table, alert_fn=fn
    )
    assert len(alerts) == 1  # 190 + 20 = 210 crosses $200
    assert alerts[0]["context"]["threshold_crossed"] == 200
    assert table.notified == 200


def test_zero_or_negative_cost_is_noop():
    table = _FakeSpendTable()
    alerts, fn = _alerts()
    spend_tracker.record_spend(Decimal("0"), table=table, alert_fn=fn)
    spend_tracker.record_spend(Decimal("-5"), table=table, alert_fn=fn)
    assert alerts == []
    assert table.adds == 0


def test_best_effort_swallows_table_errors():
    class _Boom:
        def update_item(self, **kw):
            raise RuntimeError("ddb down")

    alerts, fn = _alerts()
    spend_tracker.record_spend(Decimal("50"), table=_Boom(), alert_fn=fn)  # must not raise
    assert alerts == []


def test_year_window_derived_from_started_at():
    table = _FakeSpendTable(cumulative="90")
    alerts, fn = _alerts()
    spend_tracker.record_spend(
        Decimal("20"), started_at="2027-01-02T00:00:00Z", table=table, alert_fn=fn
    )
    assert alerts[0]["context"]["year"] == "2027"
