"""Tests for spotlight/review_queue.py — Plan 06-04 (SPOT-08 + SPOT-07 surface).

Behaviors per PLAN.md ``<behavior>`` block (11 tests):
  1. write_review_entry put_item PK/SK composite shape
  2. write_review_entry persists all required attributes (low-level types)
  3. write_review_entry persists optional attributes when present
  4. write_review_entry validates flag_reason ∈ {critic, sensitive_tag, both}
  5. write_review_entry forces status=pending (caller cannot override)
  6. list_pending Query KeyConditionExpression + FilterExpression + #s alias
  7. list_pending returns flattened native-type dicts
  8. set_status UpdateItem with parameterized values + #s alias
  9. set_status validates target_status ∈ {approved, rejected}
 10. set_status uses ConditionExpression "#s = :pending"
 11. No string interpolation of user input into expressions

All tests use a synthetic injected boto3 stub; no AWS calls.
"""

from __future__ import annotations

import inspect

import pytest

from spotlight.review_queue import (
    VALID_FLAG_REASONS,
    VALID_TARGET_STATUSES,
    list_pending,
    set_status,
    write_review_entry,
)


# ---------------------------------------------------------------------------
# Synthetic DynamoDB client stub (records every call)
# ---------------------------------------------------------------------------


class StubDDBClient:
    """Minimal stub: records put_item / query / update_item calls."""

    def __init__(self, query_response=None):
        self.put_calls = []
        self.query_calls = []
        self.update_calls = []
        self._query_response = query_response or {"Items": []}

    def put_item(self, **kwargs):
        self.put_calls.append(kwargs)
        return {}

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return self._query_response

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)
        return {}


def _minimal_entry(**overrides):
    """Build a minimal valid review entry dict, with optional field overrides."""
    base = {
        "publish_id": "v2026-05-14",
        "subtopic_id": "oncology_001",
        "parent_topic": "Cancer Biology",
        "lede_text": "Synthetic lede placeholder.",
        "flag_reason": "critic",
        "papers_used": ["12345678", "87654321"],
        "regen_count": 2,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1: put_item PK/SK composite shape
# ---------------------------------------------------------------------------


def test_write_review_entry_put_item_keys():
    client = StubDDBClient()
    write_review_entry(client, _minimal_entry())

    assert len(client.put_calls) == 1
    item = client.put_calls[0]["Item"]
    assert item["PK"] == {"S": "SPOTLIGHT_REVIEW#v2026-05-14"}
    assert item["SK"] == {"S": "SUBTOPIC#oncology_001"}


# ---------------------------------------------------------------------------
# Test 2: required attributes with correct low-level types
# ---------------------------------------------------------------------------


def test_write_review_entry_persists_required_attributes():
    client = StubDDBClient()
    write_review_entry(client, _minimal_entry())
    item = client.put_calls[0]["Item"]

    assert item["publish_id"] == {"S": "v2026-05-14"}
    assert item["subtopic_id"] == {"S": "oncology_001"}
    assert item["parent_topic"] == {"S": "Cancer Biology"}
    assert item["lede_text"] == {"S": "Synthetic lede placeholder."}
    assert item["flag_reason"] == {"S": "critic"}
    # papers_used as List of S
    assert item["papers_used"] == {
        "L": [{"S": "12345678"}, {"S": "87654321"}]
    }
    # regen_count as N (string-encoded)
    assert item["regen_count"] == {"N": "2"}
    # status forced to pending
    assert item["status"] == {"S": "pending"}
    # created_at present, ISO 8601 with Z
    created_at = item["created_at"]["S"]
    assert created_at.endswith("Z")
    # Looks like ISO 8601 (YYYY-MM-DDTHH:MM:SSZ)
    assert "T" in created_at and len(created_at) >= 20


# ---------------------------------------------------------------------------
# Test 3: optional attributes persisted when present
# ---------------------------------------------------------------------------


def test_write_review_entry_persists_optional_attributes():
    client = StubDDBClient()
    entry = _minimal_entry(
        flag_reason="both",
        critic_verdict={
            "deterministic_failed": ["dedup_breach"],
            "llm_verdict": "fail",
            "llm_reason": "synthetic reason",
        },
        sensitive_tag_matched="alpha",
        attempts=[
            {"attempt": 1, "verdict": "fail", "reason": "synthetic"},
            {"attempt": 2, "verdict": "fail", "reason": "synthetic-2"},
        ],
    )
    write_review_entry(client, entry)
    item = client.put_calls[0]["Item"]

    # critic_verdict as Map
    assert "critic_verdict" in item
    assert "M" in item["critic_verdict"]
    cv = item["critic_verdict"]["M"]
    assert cv["llm_verdict"] == {"S": "fail"}
    assert cv["llm_reason"] == {"S": "synthetic reason"}
    # deterministic_failed nested as List of S
    assert cv["deterministic_failed"] == {"L": [{"S": "dedup_breach"}]}

    # sensitive_tag_matched as S
    assert item["sensitive_tag_matched"] == {"S": "alpha"}

    # attempts as List of M
    assert "attempts" in item
    assert "L" in item["attempts"]
    assert len(item["attempts"]["L"]) == 2
    first_attempt = item["attempts"]["L"][0]["M"]
    assert first_attempt["attempt"] == {"N": "1"}
    assert first_attempt["verdict"] == {"S": "fail"}


# ---------------------------------------------------------------------------
# Test 4: invalid flag_reason raises ValueError
# ---------------------------------------------------------------------------


def test_write_review_entry_rejects_invalid_flag_reason():
    client = StubDDBClient()
    with pytest.raises(ValueError) as exc_info:
        write_review_entry(client, _minimal_entry(flag_reason="banana"))
    assert "flag_reason" in str(exc_info.value)
    # Must NOT have called put_item
    assert client.put_calls == []


# ---------------------------------------------------------------------------
# Test 5: status is forced to "pending" — caller cannot override
# ---------------------------------------------------------------------------


def test_write_review_entry_forces_status_pending():
    client = StubDDBClient()
    # Caller passes status=approved; the writer must overwrite it.
    entry = _minimal_entry()
    entry["status"] = "approved"
    write_review_entry(client, entry)
    item = client.put_calls[0]["Item"]
    assert item["status"] == {"S": "pending"}


# ---------------------------------------------------------------------------
# Test 6: list_pending Query call shape
# ---------------------------------------------------------------------------


def test_list_pending_query_shape():
    client = StubDDBClient(query_response={"Items": []})
    list_pending(client, "v2026-05-14")

    assert len(client.query_calls) == 1
    call = client.query_calls[0]
    assert call["KeyConditionExpression"] == "PK = :pk"
    assert call["FilterExpression"] == "#s = :pending"
    assert call["ExpressionAttributeNames"] == {"#s": "status"}
    assert call["ExpressionAttributeValues"] == {
        ":pk": {"S": "SPOTLIGHT_REVIEW#v2026-05-14"},
        ":pending": {"S": "pending"},
    }


# ---------------------------------------------------------------------------
# Test 7: list_pending flattens low-level items into native-type dicts
# ---------------------------------------------------------------------------


def test_list_pending_flattens_items():
    raw_item = {
        "PK": {"S": "SPOTLIGHT_REVIEW#v2026-05-14"},
        "SK": {"S": "SUBTOPIC#t_001"},
        "publish_id": {"S": "v2026-05-14"},
        "subtopic_id": {"S": "t_001"},
        "parent_topic": {"S": "Generic Parent"},
        "lede_text": {"S": "Synthetic lede."},
        "flag_reason": {"S": "critic"},
        "papers_used": {"L": [{"S": "111"}, {"S": "222"}]},
        "regen_count": {"N": "3"},
        "status": {"S": "pending"},
        "created_at": {"S": "2026-05-07T19:00:00Z"},
        "critic_verdict": {
            "M": {
                "llm_verdict": {"S": "fail"},
                "deterministic_failed": {"L": [{"S": "anchor_breach"}]},
            }
        },
    }
    client = StubDDBClient(query_response={"Items": [raw_item]})

    rows = list_pending(client, "v2026-05-14")

    assert len(rows) == 1
    row = rows[0]
    # Native types flattened
    assert row["publish_id"] == "v2026-05-14"
    assert row["subtopic_id"] == "t_001"
    assert row["papers_used"] == ["111", "222"]
    assert row["regen_count"] == 3
    assert row["status"] == "pending"
    assert row["critic_verdict"] == {
        "llm_verdict": "fail",
        "deterministic_failed": ["anchor_breach"],
    }


# ---------------------------------------------------------------------------
# Test 8: set_status UpdateItem call shape
# ---------------------------------------------------------------------------


def test_set_status_update_item_shape():
    client = StubDDBClient()
    set_status(client, "v2026-05-14", "oncology_001", "approved", reviewer="cli")

    assert len(client.update_calls) == 1
    call = client.update_calls[0]
    assert call["Key"] == {
        "PK": {"S": "SPOTLIGHT_REVIEW#v2026-05-14"},
        "SK": {"S": "SUBTOPIC#oncology_001"},
    }
    assert call["UpdateExpression"] == "SET #s = :s, reviewer = :r, reviewed_at = :t"
    # status is reserved word — must be aliased via ExpressionAttributeNames
    assert call["ExpressionAttributeNames"] == {"#s": "status"}
    eav = call["ExpressionAttributeValues"]
    assert eav[":s"] == {"S": "approved"}
    assert eav[":r"] == {"S": "cli"}
    assert ":t" in eav and eav[":t"]["S"].endswith("Z")
    assert eav[":pending"] == {"S": "pending"}


# ---------------------------------------------------------------------------
# Test 9: set_status rejects invalid target_status
# ---------------------------------------------------------------------------


def test_set_status_rejects_invalid_target_status():
    client = StubDDBClient()
    with pytest.raises(ValueError) as exc_info:
        set_status(client, "v2026-05-14", "t_001", "deleted", reviewer="cli")
    assert "target_status" in str(exc_info.value) or "approved" in str(exc_info.value)
    # Must NOT have called update_item
    assert client.update_calls == []


# ---------------------------------------------------------------------------
# Test 10: set_status uses ConditionExpression for state machine
# ---------------------------------------------------------------------------


def test_set_status_uses_condition_expression_pending():
    client = StubDDBClient()
    set_status(client, "v2026-05-14", "t_001", "rejected", reviewer="cli")

    call = client.update_calls[0]
    # ConditionExpression enforces pending → approved/rejected only.
    assert call["ConditionExpression"] == "#s = :pending"
    # Same #s alias as UpdateExpression
    assert call["ExpressionAttributeNames"]["#s"] == "status"


# ---------------------------------------------------------------------------
# Test 11: no string interpolation of user input into expressions
# ---------------------------------------------------------------------------


def test_no_user_input_in_expression_strings():
    """All four user-controlled values flow through ExpressionAttributeValues.

    Adversarial test: pass values that would visibly inject if naively
    f-stringed into an expression. The stub records the call args; we
    assert the user values appear ONLY inside ExpressionAttributeValues
    bindings (and the Key construction), never inside any *Expression
    string.
    """
    client = StubDDBClient()
    # Adversarial-looking values; would corrupt an expression if interpolated.
    adversarial_publish_id = "v2026-05-14:DROP_TABLE"
    adversarial_subtopic = "t_001';--"
    adversarial_reviewer = "evil ' OR 1=1"
    set_status(
        client,
        adversarial_publish_id,
        adversarial_subtopic,
        "approved",
        reviewer=adversarial_reviewer,
    )

    call = client.update_calls[0]

    # The expression strings must be exactly the static literals; no
    # user-controlled string fragments may appear inside them.
    update_expr = call["UpdateExpression"]
    cond_expr = call["ConditionExpression"]
    assert update_expr == "SET #s = :s, reviewer = :r, reviewed_at = :t"
    assert cond_expr == "#s = :pending"

    # The adversarial substrings must NOT appear in any expression string.
    for fragment in ("DROP_TABLE", "OR 1=1", "';--"):
        assert fragment not in update_expr
        assert fragment not in cond_expr

    # The reviewer/publish_id/subtopic_id MUST appear in EAV/Key bindings.
    eav = call["ExpressionAttributeValues"]
    assert eav[":r"] == {"S": adversarial_reviewer}
    assert call["Key"]["PK"] == {"S": f"SPOTLIGHT_REVIEW#{adversarial_publish_id}"}
    assert call["Key"]["SK"] == {"S": f"SUBTOPIC#{adversarial_subtopic}"}


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------


def test_valid_flag_reasons_constant():
    assert VALID_FLAG_REASONS == {"critic", "sensitive_tag", "both"}


def test_valid_target_statuses_constant():
    assert VALID_TARGET_STATUSES == {"approved", "rejected"}


def test_set_status_signature_has_target_status_param():
    """Guard against drift: signature has positional target_status."""
    sig = inspect.signature(set_status)
    assert "target_status" in sig.parameters
    assert "reviewer" in sig.parameters
