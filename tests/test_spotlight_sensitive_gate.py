"""Tests for spotlight/sensitive_gate.py — Plan 06-04 (SPOT-08).

Behaviors per PLAN.md ``<behavior>`` block (9 tests):
  1. load_sensitive_tags GetItem call shape + return parsed list
  2. load_sensitive_tags raises (fail-closed) on missing config record
  3. load_sensitive_tags raises (fail-closed) on boto3 ClientError
  4. is_sensitive matches subtopic.label substring
  5. is_sensitive case-insensitive match
  6. is_sensitive matches against parent_topic_label
  7. is_sensitive returns (False, None) on empty tag list
  8. is_sensitive matches against description (multi-field haystack)
  9. is_sensitive signature has NO lede / lede_text parameter

All tests use a synthetic injected boto3 stub; no AWS calls. Tag-pattern
strings used here are non-sensitive synthetic values ("alpha", "beta",
"gamma") — real operator tag patterns NEVER appear in source or tests.
"""

from __future__ import annotations

import inspect

import pytest
from botocore.exceptions import ClientError

from spotlight.sensitive_gate import (
    SubtopicMeta,
    is_sensitive,
    load_sensitive_tags,
)


# ---------------------------------------------------------------------------
# Synthetic DynamoDB client stubs
# ---------------------------------------------------------------------------


class StubGetItemClient:
    """Minimal stub: returns a configured response from ``get_item``."""

    def __init__(self, response=None, raise_error=None):
        self._response = response if response is not None else {}
        self._raise_error = raise_error
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_error is not None:
            raise self._raise_error
        return self._response


def _config_item(tag_dicts):
    """Build a SPOTLIGHT_CONFIG#sensitive_tags Item in DynamoDB low-level shape.

    tag_dicts: list of {"pattern": str, "match_type": str, "reason": str}.
    """
    return {
        "Item": {
            "PK": {"S": "SPOTLIGHT_CONFIG#sensitive_tags"},
            "SK": {"S": "CONFIG"},
            "tags": {
                "L": [
                    {
                        "M": {
                            "pattern": {"S": t["pattern"]},
                            "match_type": {"S": t.get("match_type", "substring")},
                            "reason": {"S": t.get("reason", "")},
                        }
                    }
                    for t in tag_dicts
                ]
            },
        }
    }


# ---------------------------------------------------------------------------
# Test 1: load_sensitive_tags GetItem shape + parsing
# ---------------------------------------------------------------------------


def test_load_sensitive_tags_returns_parsed_list():
    """GetItem call hits PK=SPOTLIGHT_CONFIG#sensitive_tags, SK=CONFIG; returns list of dicts."""
    response = _config_item(
        [
            {"pattern": "alpha", "match_type": "substring", "reason": "test reason 1"},
            {"pattern": "beta", "match_type": "substring", "reason": "test reason 2"},
        ]
    )
    client = StubGetItemClient(response=response)

    tags = load_sensitive_tags(client=client)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["Key"] == {
        "PK": {"S": "SPOTLIGHT_CONFIG#sensitive_tags"},
        "SK": {"S": "CONFIG"},
    }
    assert tags == [
        {"pattern": "alpha", "match_type": "substring", "reason": "test reason 1"},
        {"pattern": "beta", "match_type": "substring", "reason": "test reason 2"},
    ]


# ---------------------------------------------------------------------------
# Test 2: missing config record → fail-closed RuntimeError
# ---------------------------------------------------------------------------


def test_load_sensitive_tags_raises_on_missing_config():
    """No 'Item' in GetItem response means the config row is absent → fail closed."""
    client = StubGetItemClient(response={})  # No "Item" key

    with pytest.raises(RuntimeError) as exc_info:
        load_sensitive_tags(client=client)

    msg = str(exc_info.value).lower()
    assert "fail closed" in msg or "fail_closed" in msg
    assert "missing" in msg or "config" in msg


# ---------------------------------------------------------------------------
# Test 3: boto3 ClientError → fail-closed RuntimeError
# ---------------------------------------------------------------------------


def test_load_sensitive_tags_raises_on_client_error():
    """ClientError must propagate as RuntimeError (NOT silently return [])."""
    err = ClientError(
        error_response={"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
        operation_name="GetItem",
    )
    client = StubGetItemClient(raise_error=err)

    with pytest.raises(RuntimeError) as exc_info:
        load_sensitive_tags(client=client)

    msg = str(exc_info.value).lower()
    assert "fail closed" in msg or "fail_closed" in msg


# ---------------------------------------------------------------------------
# Test 4: is_sensitive matches label substring
# ---------------------------------------------------------------------------


def test_is_sensitive_matches_label_substring():
    """Pattern present in subtopic.label triggers True with that pattern."""
    meta = SubtopicMeta(
        subtopic_id="oncology_001",
        label="Alpha Studies in Cancer",
        description="Synthetic description.",
        parent_topic_label="Cancer Biology",
    )
    tags = [{"pattern": "alpha", "match_type": "substring", "reason": "synthetic"}]

    matched, pattern = is_sensitive(meta, tags)

    assert matched is True
    assert pattern == "alpha"


# ---------------------------------------------------------------------------
# Test 5: is_sensitive is case-insensitive
# ---------------------------------------------------------------------------


def test_is_sensitive_case_insensitive():
    """Pattern 'BETA' (uppercase) matches subtopic.label 'Beta Policy'."""
    meta = SubtopicMeta(
        subtopic_id="t_002",
        label="Beta Policy",
        description="",
        parent_topic_label="Generic Parent",
    )
    tags = [{"pattern": "BETA", "match_type": "substring", "reason": ""}]

    matched, pattern = is_sensitive(meta, tags)

    assert matched is True
    # Original-case pattern returned (caller may want to log the operator's input).
    assert pattern == "BETA"


# ---------------------------------------------------------------------------
# Test 6: is_sensitive matches parent_topic_label
# ---------------------------------------------------------------------------


def test_is_sensitive_matches_parent_topic_label():
    """Pattern matches against parent_topic_label even if subtopic.label is generic."""
    meta = SubtopicMeta(
        subtopic_id="generic_003",
        label="Outcomes Research",
        description="Population-level analyses.",
        parent_topic_label="Gamma Disease Research",
    )
    tags = [{"pattern": "gamma", "match_type": "substring", "reason": ""}]

    matched, pattern = is_sensitive(meta, tags)

    assert matched is True
    assert pattern == "gamma"


# ---------------------------------------------------------------------------
# Test 7: empty tag list → (False, None)
# ---------------------------------------------------------------------------


def test_is_sensitive_empty_tags_returns_false():
    meta = SubtopicMeta(
        subtopic_id="t_004",
        label="Anything",
        description="Anywhere",
        parent_topic_label="Anyparent",
    )
    matched, pattern = is_sensitive(meta, tags=[])

    assert matched is False
    assert pattern is None


# ---------------------------------------------------------------------------
# Test 8: is_sensitive matches against description
# ---------------------------------------------------------------------------


def test_is_sensitive_matches_description_field():
    """Pattern present only in description still triggers (all 3 fields searched)."""
    meta = SubtopicMeta(
        subtopic_id="t_005",
        label="Generic Title",
        description="Studies of delta phenomena in clinical settings.",
        parent_topic_label="Generic Parent",
    )
    tags = [{"pattern": "delta", "match_type": "substring", "reason": ""}]

    matched, pattern = is_sensitive(meta, tags)

    assert matched is True
    assert pattern == "delta"


# ---------------------------------------------------------------------------
# Test 9: is_sensitive signature does NOT accept lede text
# ---------------------------------------------------------------------------


def test_is_sensitive_signature_excludes_lede_text():
    """is_sensitive matches subtopic metadata only — never against lede text.

    The function signature must not have ``lede`` or ``lede_text`` parameters.
    This is a structural guard against accidentally widening the gate to
    grounded LLM output (which is constrained by anchor-in-synopses, not
    by the sensitive gate).
    """
    sig = inspect.signature(is_sensitive)
    assert "lede" not in sig.parameters
    assert "lede_text" not in sig.parameters
