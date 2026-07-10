"""The screening pass must send its static half as a cached system prefix.

The 68-topic block plus scoring guidance is ~6.7K tokens and identical for every
publication. Sent in the user turn it is re-billed on all ~10K screening calls of
a full-corpus run; sent as a cached system prefix it is billed once per cache
window. These tests pin that contract — a refactor that folds the topics back
into the user turn, or drops cache_system, silently costs real money.
"""

from __future__ import annotations

import json
from pathlib import Path

import score_publications as sp

TAXONOMY = json.loads((Path(__file__).resolve().parents[1] / "taxonomy_v2.json").read_text())
PUB = {"pmid": "1", "synopsis": "A study of X.", "abstract": "We did Y.", "title": "T"}

# Bedrock's minimum cacheable prefix for Claude Haiku. Below this, a cachePoint
# is ignored and the prefix is billed in full on every call.
HAIKU_MIN_CACHEABLE_TOKENS = 2048


class _CapturingBedrock:
    def __init__(self):
        self.calls: list[dict] = []

    def call_json(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["model"] == sp.HAIKU_MODEL:
            return {"0": 0.9}
        return {"0": {"score": 0.9, "rationale": "r"}}


def _screening_call(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr(sp, "mark_processing", lambda *a, **k: None)
    bedrock = _CapturingBedrock()
    ddb = MagicMock()
    ddb.query.return_value = {"Items": []}
    int_to_id, id_to_int = sp.build_topic_index(TAXONOMY)

    sp.score_one_publication(
        PUB, bedrock, TAXONOMY, ddb, "reciterai", int_to_id, id_to_int,
    )
    return next(c for c in bedrock.calls if c["model"] == sp.HAIKU_MODEL)


def test_screening_call_requests_system_caching(monkeypatch):
    call = _screening_call(monkeypatch)
    assert call["cache_system"] is True
    assert call["system"] == sp.make_screening_system(TAXONOMY)


def test_the_topic_block_is_in_the_system_prefix_not_the_user_turn(monkeypatch):
    call = _screening_call(monkeypatch)
    user_text = call["messages"][0]["content"]
    a_topic_label = TAXONOMY["topics"][0]["label"]

    assert a_topic_label in call["system"]
    assert a_topic_label not in user_text
    # The user turn carries only what varies per publication.
    assert PUB["synopsis"] in user_text
    assert PUB["abstract"] in user_text


def test_system_prefix_is_identical_across_publications():
    other = {"pmid": "2", "synopsis": "different", "abstract": "different"}
    assert sp.make_screening_system(TAXONOMY) == sp.make_screening_system(TAXONOMY)
    assert sp.make_screening_user(PUB) != sp.make_screening_user(other)


def test_system_prefix_clears_the_minimum_cacheable_size():
    """A prefix under Bedrock's minimum is never cached, so the cachePoint is a no-op."""
    system = sp.make_screening_system(TAXONOMY)
    approx_tokens = len(system) / 3.7  # conservative chars-per-token for English prose
    assert approx_tokens > HAIKU_MIN_CACHEABLE_TOKENS, (
        f"screening system prefix is only ~{approx_tokens:.0f} tokens; "
        "Bedrock will not cache it"
    )


def test_legacy_single_string_prompt_still_contains_everything():
    """cli/score_new_topics.py and scripts/debug/* still compose one prompt string."""
    prompt = sp.make_screening_prompt(PUB, TAXONOMY)

    assert TAXONOMY["topics"][0]["label"] in prompt
    assert PUB["synopsis"] in prompt
    assert "Scoring guidance:" in prompt
