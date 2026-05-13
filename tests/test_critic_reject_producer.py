"""Tests for CritReasonCode StrEnum + CRITIC_REJECT# dual-write at critic exhaustion.

Phase 12 D-08/D-30/D-31: run_critic_loop writes CRITIC_REJECT# alongside (not
replacing) the existing SPOTLIGHT_REVIEW# write after MAX_RETRIES + 1 attempts.

Covers:
- CritReasonCode enum: five members, exact values, StrEnum subclass
- Exhaustion writes both SPOTLIGHT_REVIEW# (write_review_entry) and CRITIC_REJECT# (put_item)
- write_review_entry payload is unchanged by Phase 12 (additive-not-replacing regression)
- PK uses publish_id and subtopic_id already in scope (D-08 shape, no cwid threading)
- author_cwids derived from selected_papers first/last author person_identifiers
- Post-LLM known constraint passes through verbatim; no extra fields
- Post-LLM unknown constraint emits warning (dispatch WARN) + reason_code='unknown'
- Pre-LLM gate path: reason_code='pre_llm_gate' + pre_llm_constraint
- Pre-LLM gate with no deterministic detail: pre_llm_constraint='unknown'
- CRITIC_REJECT# row carries no lede content (Pattern H PII boundary)
- SPOTLIGHT_REVIEW# write still carries lede_text (regression: additive only)
- Passing critic does not write CRITIC_REJECT#
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from spotlight.critic import (
    CritReasonCode,
    MAX_RETRIES,
    run_critic_loop,
)
from spotlight.types import Author, Paper


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

PUBLISH_ID = "2026-05-12-001"


def _make_author(cwid: str, position: str = "first") -> Author:
    return Author(person_identifier=cwid, display_name=f"Name {cwid}", position=position)


def _make_paper(pmid: str, first_cwid: str = "cwid1", last_cwid: str = "cwid2") -> Paper:
    return Paper(
        pmid=pmid,
        title=f"Title {pmid}",
        journal="Test Journal",
        year=2024,
        impact_score=5.0,
        impact_justification="cited well",
        synopsis=f"Synopsis {pmid}",
        first_author=_make_author(first_cwid, "first"),
        last_author=_make_author(last_cwid, "last"),
    )


def _make_meta(subtopic_id: str = "aging_geroscience") -> Any:
    """Build a SubtopicMeta-like object with just the fields critic uses."""
    from spotlight.sensitive_gate import SubtopicMeta

    return SubtopicMeta(
        subtopic_id=subtopic_id,
        label="Aging and Geroscience",
        description="Research on aging and longevity.",
        parent_topic_label="Aging",
    )


def _make_lede_client(lede: str = "WCM scholars are advancing aging research.") -> MagicMock:
    """Fake BedrockClient that always returns a fixed lede."""
    client = MagicMock()
    client.call.return_value = lede
    return client


def _make_failing_llm_client(failed_constraint: str) -> MagicMock:
    """Fake BedrockClient that always returns a failing LLM verdict JSON."""
    import json

    client = MagicMock()
    client.call.return_value = json.dumps(
        {"verdict": "fail", "failed_constraint": failed_constraint, "reason": f"reason for {failed_constraint}"}
    )
    return client


def _make_passing_llm_client() -> MagicMock:
    """Fake BedrockClient that always returns a passing LLM verdict JSON."""
    import json

    client = MagicMock()
    client.call.return_value = json.dumps({"verdict": "pass", "failed_constraint": "", "reason": ""})
    return client


# ---------------------------------------------------------------------------
# CritReasonCode enum
# ---------------------------------------------------------------------------


def test_critreasoncode_enum_values():
    """CritReasonCode is a StrEnum with exactly five members (D-31)."""
    from enum import StrEnum

    assert issubclass(CritReasonCode, StrEnum)
    assert len(list(CritReasonCode)) == 5
    assert CritReasonCode.ACTIVE_VERB == "active_verb"
    assert CritReasonCode.ANCHORED_IN_SYNOPSES == "anchored_in_synopses"
    assert CritReasonCode.NO_FACULTY_NAMED == "no_faculty_named"
    assert CritReasonCode.INSTITUTIONAL_VOICE == "institutional_voice"
    assert CritReasonCode.PRE_LLM_GATE == "pre_llm_gate"


# ---------------------------------------------------------------------------
# Exhaustion dual-write: both SPOTLIGHT_REVIEW# and CRITIC_REJECT# written
# ---------------------------------------------------------------------------


def test_exhaustion_writes_both_review_and_critic_reject():
    """After MAX_RETRIES+1 failing attempts, both review and critic_reject are written.

    Order: write_review_entry (SPOTLIGHT_REVIEW#) then write_critic_reject (CRITIC_REJECT#).
    """
    meta = _make_meta()
    papers = [_make_paper("111"), _make_paper("222")]

    lede_client = _make_lede_client("WCM scholars are advancing aging research.")
    critic_client = _make_failing_llm_client("active_verb")

    # stage_table mock: captures put_item calls for CRITIC_REJECT#
    stage_table = MagicMock()
    put_items: list[dict] = []
    stage_table.put_item.side_effect = lambda Item: put_items.append(Item)

    with patch("spotlight.critic.write_review_entry") as mock_write_review:
        result = run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=PUBLISH_ID,
            parent_topic="aging",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=MagicMock(),
            stage_table=stage_table,
        )

    assert result.status == "needs_review"
    # SPOTLIGHT_REVIEW# write happened
    assert mock_write_review.call_count == 1
    # CRITIC_REJECT# write happened via stage_table.put_item
    assert len(put_items) == 1
    cr_item = put_items[0]
    assert cr_item["PK"].startswith(f"CRITIC_REJECT#{PUBLISH_ID}#{meta.subtopic_id}#")
    assert cr_item["record_type"] == "CRITIC_REJECT"


# ---------------------------------------------------------------------------
# SPOTLIGHT_REVIEW# payload is unchanged (regression: additive only)
# ---------------------------------------------------------------------------


def test_review_queue_write_unchanged():
    """The existing write_review_entry call receives the same payload as before Phase 12.

    Phase 12 adds CRITIC_REJECT# additively; it must NOT inject Phase-12-specific
    fields into the SPOTLIGHT_REVIEW# review_entry dict.
    """
    meta = _make_meta(subtopic_id="aging_geroscience")
    papers = [_make_paper("111", first_cwid="cwid1", last_cwid="cwid2")]
    lede = "WCM scholars are advancing aging research."
    lede_client = _make_lede_client(lede)
    critic_client = _make_failing_llm_client("active_verb")

    captured_entries: list[dict] = []

    def _capture_review(client, entry):
        captured_entries.append(dict(entry))

    stage_table = MagicMock()

    with patch("spotlight.critic.write_review_entry", side_effect=_capture_review):
        run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=PUBLISH_ID,
            parent_topic="aging",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=MagicMock(),
            stage_table=stage_table,
        )

    assert len(captured_entries) == 1
    entry = captured_entries[0]

    # Phase-12-specific keys must NOT appear in the SPOTLIGHT_REVIEW# entry
    assert "reason_code" not in entry
    assert "pmid_set_hash" not in entry
    assert "author_cwids" not in entry
    assert "raw_failed_constraint" not in entry
    assert "pre_llm_constraint" not in entry
    assert "CRITIC_REJECT" not in str(entry.get("PK", ""))

    # Pre-Phase-12 fields must still be present
    assert entry["publish_id"] == PUBLISH_ID
    assert entry["subtopic_id"] == "aging_geroscience"
    assert entry["flag_reason"] == "critic"
    assert entry["regen_count"] == MAX_RETRIES


# ---------------------------------------------------------------------------
# PK shape: publish_id + subtopic_id from scope (D-08)
# ---------------------------------------------------------------------------


def test_pk_uses_publish_id_and_subtopic_id_in_scope():
    """CRITIC_REJECT# PK starts with CRITIC_REJECT#<publish_id>#<subtopic_id># (D-08)."""
    expected_publish_id = "2026-05-12-MYPUB"
    expected_subtopic_id = "cardio_afib"
    meta = _make_meta(subtopic_id=expected_subtopic_id)
    papers = [_make_paper("999")]
    lede_client = _make_lede_client()
    critic_client = _make_failing_llm_client("active_verb")

    stage_table = MagicMock()
    put_items: list[dict] = []
    stage_table.put_item.side_effect = lambda Item: put_items.append(Item)

    with patch("spotlight.critic.write_review_entry"):
        run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=expected_publish_id,
            parent_topic="cardio",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=MagicMock(),
            stage_table=stage_table,
        )

    assert len(put_items) == 1
    pk = put_items[0]["PK"]
    assert pk.startswith(f"CRITIC_REJECT#{expected_publish_id}#{expected_subtopic_id}#")
    assert pk.count("#") == 3


# ---------------------------------------------------------------------------
# author_cwids derived from selected_papers
# ---------------------------------------------------------------------------


def test_author_cwids_derived_from_selected_papers():
    """author_cwids is the sorted union of first/last author cwids across papers."""
    # first_cwid ∈ {"a","b"}, last_cwid ∈ {"b","c"} → sorted union = ["a","b","c"]
    papers = [
        _make_paper("1", first_cwid="a", last_cwid="b"),
        _make_paper("2", first_cwid="b", last_cwid="c"),
    ]
    meta = _make_meta()
    lede_client = _make_lede_client()
    critic_client = _make_failing_llm_client("active_verb")

    stage_table = MagicMock()
    put_items: list[dict] = []
    stage_table.put_item.side_effect = lambda Item: put_items.append(Item)

    with patch("spotlight.critic.write_review_entry"):
        run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=PUBLISH_ID,
            parent_topic="aging",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=MagicMock(),
            stage_table=stage_table,
        )

    assert len(put_items) == 1
    assert put_items[0]["author_cwids"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Post-LLM known constraint passes through (no extra fields)
# ---------------------------------------------------------------------------


def test_post_llm_known_constraint_passes_through():
    """LLM failed_constraint='institutional_voice' → reason_code='institutional_voice', no raw_failed_constraint."""
    meta = _make_meta()
    papers = [_make_paper("1")]
    lede_client = _make_lede_client()
    critic_client = _make_failing_llm_client("institutional_voice")

    stage_table = MagicMock()
    put_items: list[dict] = []
    stage_table.put_item.side_effect = lambda Item: put_items.append(Item)

    with patch("spotlight.critic.write_review_entry"):
        run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=PUBLISH_ID,
            parent_topic="aging",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=MagicMock(),
            stage_table=stage_table,
        )

    assert len(put_items) == 1
    item = put_items[0]
    assert item["reason_code"] == "institutional_voice"
    assert "raw_failed_constraint" not in item
    assert "pre_llm_constraint" not in item


# ---------------------------------------------------------------------------
# Vocabulary drift: unknown constraint → warning + 'unknown' bucket
# ---------------------------------------------------------------------------


def test_post_llm_unknown_constraint_emits_warning_and_unknown_bucket():
    """LLM returns an unknown code → reason_code='unknown', raw_failed_constraint set, alert dispatched."""
    meta = _make_meta()
    papers = [_make_paper("1")]
    lede_client = _make_lede_client()
    critic_client = _make_failing_llm_client("some_new_code_we_havent_seen")

    stage_table = MagicMock()
    put_items: list[dict] = []
    stage_table.put_item.side_effect = lambda Item: put_items.append(Item)

    with patch("spotlight.critic.write_review_entry"):
        with patch("pipeline_common.alert.dispatch") as mock_dispatch:
            run_critic_loop(
                meta=meta,
                papers=papers,
                publish_id=PUBLISH_ID,
                parent_topic="aging",
                lede_client=lede_client,
                critic_client=critic_client,
                dynamo_client=MagicMock(),
                stage_table=stage_table,
            )

    assert len(put_items) == 1
    item = put_items[0]
    assert item["reason_code"] == "unknown"
    assert item["raw_failed_constraint"] == "some_new_code_we_havent_seen"
    # Alert dispatched with WARN severity
    assert mock_dispatch.call_count == 1
    call_args = mock_dispatch.call_args
    assert call_args[0][0] == "WARN" or call_args[1].get("severity") == "WARN"


# ---------------------------------------------------------------------------
# Pre-LLM gate path
# ---------------------------------------------------------------------------


def test_pre_llm_gate_path():
    """When LLM never reached (all failures are deterministic), emit PRE_LLM_GATE."""
    meta = _make_meta()
    papers = [_make_paper("1")]
    # Use a lede that fails deterministically (contains an em dash)
    lede_client = _make_lede_client("WCM scholars are advancing — aging research today.")
    critic_client = _make_passing_llm_client()  # LLM would pass, but deterministic blocks

    stage_table = MagicMock()
    put_items: list[dict] = []
    stage_table.put_item.side_effect = lambda Item: put_items.append(Item)

    with patch("spotlight.critic.write_review_entry"):
        run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=PUBLISH_ID,
            parent_topic="aging",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=MagicMock(),
            stage_table=stage_table,
        )

    assert len(put_items) == 1
    item = put_items[0]
    assert item["reason_code"] == "pre_llm_gate"
    assert "pre_llm_constraint" in item
    assert "raw_failed_constraint" not in item


# ---------------------------------------------------------------------------
# Pre-LLM gate with no deterministic detail
# ---------------------------------------------------------------------------


def test_pre_llm_gate_no_deterministic_detail():
    """Pre-LLM path with empty failed_constraints → pre_llm_constraint='unknown'."""
    # This tests the fallback when failed_constraints is empty (shouldn't happen in
    # production, but the code must not crash).
    meta = _make_meta()
    papers = [_make_paper("1")]
    # Lede that fails deterministic checks with em_dash_present
    lede_client = _make_lede_client("WCM scholars are advancing — aging research today.")
    critic_client = _make_passing_llm_client()

    stage_table = MagicMock()
    put_items: list[dict] = []
    stage_table.put_item.side_effect = lambda Item: put_items.append(Item)

    # Patch run_deterministic_checks to return empty failed_constraints
    from spotlight.critic import DeterministicVerdict
    empty_det = DeterministicVerdict(passed=False, failed_constraints=(), word_count=7)

    with patch("spotlight.critic.write_review_entry"):
        with patch("spotlight.critic.run_deterministic_checks", return_value=empty_det):
            run_critic_loop(
                meta=meta,
                papers=papers,
                publish_id=PUBLISH_ID,
                parent_topic="aging",
                lede_client=lede_client,
                critic_client=critic_client,
                dynamo_client=MagicMock(),
                stage_table=stage_table,
            )

    assert len(put_items) == 1
    item = put_items[0]
    assert item["reason_code"] == "pre_llm_gate"
    assert item["pre_llm_constraint"] == "unknown"


# ---------------------------------------------------------------------------
# PII boundary on CRITIC_REJECT# row
# ---------------------------------------------------------------------------


def test_critic_reject_carries_no_lede():
    """CRITIC_REJECT# put_item dict must have no lede_text or any key with 'lede'."""
    meta = _make_meta()
    papers = [_make_paper("1")]
    lede_client = _make_lede_client()
    critic_client = _make_failing_llm_client("active_verb")

    stage_table = MagicMock()
    put_items: list[dict] = []
    stage_table.put_item.side_effect = lambda Item: put_items.append(Item)

    with patch("spotlight.critic.write_review_entry"):
        run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=PUBLISH_ID,
            parent_topic="aging",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=MagicMock(),
            stage_table=stage_table,
        )

    assert len(put_items) == 1
    item = put_items[0]
    assert "lede_text" not in item
    assert not any("lede" in k.lower() for k in item.keys())


# ---------------------------------------------------------------------------
# Regression: SPOTLIGHT_REVIEW# still carries lede_text
# ---------------------------------------------------------------------------


def test_review_entry_still_carries_lede():
    """After Phase 12, write_review_entry STILL receives lede_text (no regression)."""
    meta = _make_meta()
    papers = [_make_paper("1")]
    expected_lede = "WCM scholars are advancing aging research in key domains."
    lede_client = _make_lede_client(expected_lede)
    critic_client = _make_failing_llm_client("active_verb")

    captured_entries: list[dict] = []

    def _capture(client, entry):
        captured_entries.append(dict(entry))

    stage_table = MagicMock()

    with patch("spotlight.critic.write_review_entry", side_effect=_capture):
        run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=PUBLISH_ID,
            parent_topic="aging",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=MagicMock(),
            stage_table=stage_table,
        )

    assert len(captured_entries) == 1
    assert "lede_text" in captured_entries[0]
    assert captured_entries[0]["lede_text"] == expected_lede


# ---------------------------------------------------------------------------
# Passing critic does not write CRITIC_REJECT#
# ---------------------------------------------------------------------------


def test_passing_critic_does_not_write_critic_reject():
    """When critic passes within MAX_RETRIES, no CRITIC_REJECT# is written."""
    meta = _make_meta()
    papers = [_make_paper("1")]
    # Lede that passes deterministic: correct length, WCM tic, no forbidden patterns
    lede_client = _make_lede_client(
        "WCM scholars are advancing aging research and longevity science in key domains today."
    )
    critic_client = _make_passing_llm_client()

    stage_table = MagicMock()

    with patch("spotlight.critic.write_review_entry") as mock_write_review:
        result = run_critic_loop(
            meta=meta,
            papers=papers,
            publish_id=PUBLISH_ID,
            parent_topic="aging",
            lede_client=lede_client,
            critic_client=critic_client,
            dynamo_client=MagicMock(),
            stage_table=stage_table,
        )

    assert result.status == "pass"
    # Neither write should be called when the critic passes
    assert mock_write_review.call_count == 0
    assert stage_table.put_item.call_count == 0
