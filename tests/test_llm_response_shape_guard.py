"""LLM response robustness: the JSON-retry role fix and the empty-score-map guard.

Two failure modes, one theme — the model gave us something we could parse but
could not use, and the pipeline treated it as a fact about the publication.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import score_publications as sp
from utils.bedrock_client import HAIKU_MODEL, JSON_RETRY_HINT, _build_json_retry_messages


# --------------------------------------------------------------------------- #
# call_json's one-shot retry must keep Converse's role alternation
# --------------------------------------------------------------------------- #


def _roles(msgs: list) -> list[str]:
    return [m["role"] for m in msgs if m["role"] != "system"]


def _has_consecutive_same_role(msgs: list) -> bool:
    roles = _roles(msgs)
    return any(a == b for a, b in zip(roles, roles[1:]))


def test_retry_echoes_bad_output_as_assistant_turn():
    messages = [{"role": "user", "content": "score this"}]

    out = _build_json_retry_messages(messages, "Sure! Here is the JSON:")

    assert _roles(out) == ["user", "assistant", "user"]
    assert out[1]["content"] == "Sure! Here is the JSON:"
    assert out[2]["content"] == JSON_RETRY_HINT
    assert not _has_consecutive_same_role(out)


def test_retry_never_sends_two_consecutive_user_turns():
    """The original bug: [user, user] -> ValidationException, not retryable."""
    out = _build_json_retry_messages([{"role": "user", "content": "score this"}], "prose")
    assert not _has_consecutive_same_role(out)


def test_blank_response_folds_hint_into_the_user_turn():
    """Converse rejects an empty text block, so the bad output can't be echoed."""
    messages = [{"role": "user", "content": "score this"}]

    out = _build_json_retry_messages(messages, "   ")

    assert _roles(out) == ["user"]
    assert out[0]["content"] == f"score this\n\n{JSON_RETRY_HINT}"
    assert not _has_consecutive_same_role(out)
    assert messages[0]["content"] == "score this"  # input not mutated


def test_system_messages_do_not_break_alternation_detection():
    messages = [
        {"role": "system", "content": "you are a scorer"},
        {"role": "user", "content": "score this"},
    ]

    out = _build_json_retry_messages(messages, "prose")

    assert _roles(out) == ["user", "assistant", "user"]
    assert not _has_consecutive_same_role(out)


def test_conversation_ending_on_assistant_just_appends_the_hint():
    messages = [
        {"role": "user", "content": "score this"},
        {"role": "assistant", "content": "{}"},
    ]

    out = _build_json_retry_messages(messages, "prose")

    assert _roles(out) == ["user", "assistant", "user"]
    assert out[-1]["content"] == JSON_RETRY_HINT
    assert not _has_consecutive_same_role(out)


# --------------------------------------------------------------------------- #
# wrong-shape (but parseable) scoring responses must fail, not read as "no topics"
# --------------------------------------------------------------------------- #


_TAXONOMY = {
    "taxonomy_version": "taxonomy_v2",
    "topics": [{"id": "t1", "label": "T1", "description": "d1"}],
}
_INT_TO_ID = {"0": "t1"}
_ID_TO_INT = {"t1": "0"}
_PUB = {"pmid": "12345", "synopsis": "syn", "abstract": "abs", "title": "A Title"}
_AUTHORS = [{"cwid": "abc1234", "position": "first"}]


def _ddb_client():
    client = MagicMock()
    client.query.return_value = {"Items": []}
    client.batch_write_item.return_value = {}
    client.delete_item.return_value = {}
    return client


class _FakeBedrock:
    """Returns a caller-supplied raw response for each pass."""

    def __init__(self, screening, dense=None):
        self._screening = screening
        self._dense = dense if dense is not None else {"0": {"score": 0.9, "rationale": "r"}}

    def call_json(self, *, model, messages, **kwargs):
        # **kwargs: the screening call also passes system= and cache_system=.
        return self._screening if model == HAIKU_MODEL else self._dense


def test_shape_error_is_a_valueerror_so_the_per_pmid_handler_catches_it():
    assert issubclass(sp.LLMResponseShapeError, ValueError)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"scores": {"0": 0.9}}, id="wrapper-envelope"),
        pytest.param({"Cardiology": 0.9}, id="topic-labels-not-int-ids"),
        pytest.param({}, id="empty-object"),
    ],
)
def test_wrong_shape_screening_marks_pmid_failed_not_complete(raw, monkeypatch):
    """Each of these used to checkpoint the PMID 'complete' with zero scores."""
    statuses: list[str] = []
    monkeypatch.setattr(
        sp, "mark_processing",
        lambda client, table, pmid, status, tax, **kw: statuses.append(status),
    )
    monkeypatch.setattr(sp, "mark_processing_failed", lambda *a, **k: None)

    result = sp.score_one_publication(
        _PUB, _FakeBedrock(screening=raw), _TAXONOMY, _ddb_client(), "reciterai",
        _INT_TO_ID, _ID_TO_INT,
    )

    assert result.status == "failed"
    assert "complete" not in statuses


def test_wrong_shape_dense_fails_before_topic_rows_are_deleted(monkeypatch):
    """The delete-then-write in _materialize_topic_rows must never run on a bad response."""
    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    monkeypatch.setattr(sp, "mark_processing_failed", lambda *a, **k: None)
    delete_spy = MagicMock()
    monkeypatch.setattr(sp, "_delete_topic_rows_for_pmid", delete_spy)

    result = sp.score_one_publication(
        _PUB,
        _FakeBedrock(screening={"0": 0.9}, dense={"scores": {"0": 0.9}}),
        _TAXONOMY, _ddb_client(), "reciterai", _INT_TO_ID, _ID_TO_INT,
        authors=_AUTHORS, persist_topic_rows=True,
    )

    assert result.status == "failed"
    delete_spy.assert_not_called()


def test_a_genuinely_irrelevant_publication_still_completes(monkeypatch):
    """Low scores are a fact about the paper; an empty map is a fact about the model."""
    statuses: list[str] = []
    monkeypatch.setattr(
        sp, "mark_processing",
        lambda client, table, pmid, status, tax, **kw: statuses.append(status),
    )

    result = sp.score_one_publication(
        _PUB, _FakeBedrock(screening={"0": 0.02}), _TAXONOMY, _ddb_client(), "reciterai",
        _INT_TO_ID, _ID_TO_INT,
    )

    assert result.status == "complete"  # guard does NOT fire on a scored-but-low response
    assert "complete" in statuses
