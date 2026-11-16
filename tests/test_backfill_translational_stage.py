"""scripts/backfill_translational_stage.py — the pure pieces and the no-raise contract."""
from __future__ import annotations

import json
from unittest.mock import patch

from pipeline_enrichment.llm_call import LLMCallResult
from pipeline_enrichment.prompts import (
    STAGE_FROM_SYNOPSIS_SYSTEM,
    SYNOPSIS_SYSTEM,
    TRANSLATIONAL_STAGES,
    build_stage_user_content,
)
from scripts import backfill_translational_stage as mod
from utils.bedrock_client import SONNET_MODEL


def test_stage_prompt_offers_exactly_the_accepted_labels_and_shares_definitions():
    assert '"stage": "' + "|".join(TRANSLATIONAL_STAGES) + '"' in STAGE_FROM_SYNOPSIS_SYSTEM
    # Both prompts carry the same definitions, so a paper is judged the same way
    # whether it is labelled by the daily job or by this backfill.
    for line in ("- T0 Discovery", "- T1 Early translation", "- T2 Clinical efficacy",
                 "- T3-4 Practice and population health", "- NA: not on this spectrum"):
        assert line in STAGE_FROM_SYNOPSIS_SYSTEM and line in SYNOPSIS_SYSTEM


def test_stage_user_content_unescapes_entities():
    out = build_stage_user_content(title="A &amp; B", journal=None, synopsis="x &#x3b2; y")
    assert out == "Title: A & B\nJournal: N/A\nSynopsis: x β y"


def test_build_update_is_conditional_and_omits_missing_confidence():
    u = mod.build_update("42", "T3-4", None)
    assert u["Key"] == {"PK": {"S": "IMPACT#pmid_42"}, "SK": {"S": "SCORE"}}
    assert "attribute_not_exists(translational_stage)" in u["ConditionExpression"]
    assert "attribute_exists(PK)" in u["ConditionExpression"]
    assert "translational_stage_confidence" not in u["UpdateExpression"]
    assert u["ExpressionAttributeValues"] == {":s": {"S": "T3-4"}, ":src": {"S": "synopsis"}}

    u = mod.build_update("42", "T0", "high")
    assert u["ExpressionAttributeValues"][":c"] == {"S": "high"}
    assert "translational_stage_confidence = :c" in u["UpdateExpression"]


def _result(payload):
    return LLMCallResult(text=json.dumps(payload), input_tokens=400, output_tokens=20, model=SONNET_MODEL)


def test_classify_returns_stage_and_cost():
    with patch.object(mod, "call_with_fallback", return_value=_result({"stage": "t2", "stage_confidence": "Medium"})) as call:
        out = mod.classify({"pmid": "1", "synopsis": "S"}, "T", "J")
    assert (out["stage"], out["confidence"], out["error"]) == ("T2", "medium", "")
    assert out["usd"] > 0
    assert call.call_args.kwargs["system_prompt"] == STAGE_FROM_SYNOPSIS_SYSTEM


def test_classify_never_raises_and_leaves_stage_none():
    with patch.object(mod, "call_with_fallback", side_effect=RuntimeError("boom")):
        out = mod.classify({"pmid": "1", "synopsis": "S"}, "T", None)
    assert out["stage"] is None and "boom" in out["error"]

    with patch.object(mod, "call_with_fallback", return_value=_result({"stage": "T3"})):
        out = mod.classify({"pmid": "1", "synopsis": "S"}, "T", None)
    assert out["stage"] is None and out["error"] == "no valid stage in response"
