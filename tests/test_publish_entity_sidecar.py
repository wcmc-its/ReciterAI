"""Tests for the entity-sidecar publisher's fragment-regression guard (#308-2).

The sidecar publisher re-runs the corpus, which projects entity_context from the RAW
(pre-#239) tool_context. Run after a #254 alignment, that would regress the live
sentence-aligned usage_sentence values to fragments — the guard aborts instead.
Pure logic only; no AWS.
"""
from __future__ import annotations

import pytest

from cli.publish_entity_sidecar import assert_no_fragment_regression


def _ctx(sentence: str) -> dict:
    return {"e1": {"100": [{"usage_sentence": sentence}]}}


def test_aborts_when_fresh_is_more_fragmented_than_live():
    live = _ctx("Magnetic resonance imaging revealed a scalpel sign.")  # aligned sentence
    fresh = _ctx("revealed a scalpel sign")                              # mid-clause fragment
    with pytest.raises(SystemExit):
        assert_no_fragment_regression(live, fresh)


def test_allows_first_publish_when_live_has_no_entity_layer():
    # No live entity_context to regress -> a fragmented fresh build is still allowed.
    assert_no_fragment_regression({}, _ctx("revealed a scalpel sign")) is None


def test_allows_when_fresh_is_not_more_fragmented():
    good = _ctx("Magnetic resonance imaging revealed a scalpel sign.")
    assert_no_fragment_regression(good, good) is None
