"""pipeline_area_signals.signals: one AREAINPUT item -> one AREASIGNAL row.

The Bedrock fake sits at the boto3 seam (`converse`), under the real
MeteredBedrockClient, so these tests run the production synopsis generator and
the production two-pass scorer end to end and check what reached the model,
what was persisted, and what was metered. Fixtures are synthetic.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from botocore.exceptions import ClientError, EndpointConnectionError

import score_publications as sp
from pipeline_area_signals import prompts, signals
from pipeline_enrichment.synopsis import SynopsisResult
from utils.openai_client import GPT5_MODEL
from utils.bedrock_client import HAIKU_MODEL, SONNET_MODEL
from utils.taxonomy import load_taxonomy

TAXONOMY = load_taxonomy()
INT_TO_ID, ID_TO_INT = sp.build_topic_index(TAXONOMY)
T0, T1, T2 = (INT_TO_ID[str(i)] for i in range(3))
TV = signals.current_taxonomy_version(TAXONOMY)


class FakeConverse:
    """boto3 bedrock-runtime stand-in: answers by prompt shape, records calls."""

    def __init__(self, *, synopsis="Synthetic synopsis of a study of widgets",
                 screening=None, dense=None):
        self.synopsis = synopsis
        self.screening = screening if screening is not None else {"0": 0.9, "1": 0.5, "2": 0.1}
        self.dense = dense if dense is not None else {
            "0": {"score": 0.82, "rationale": "r"}, "1": {"score": 0.25, "rationale": "r"}}
        self.calls = []

    def converse(self, **kw):
        system = " ".join(s.get("text", "") for s in kw.get("system", []))
        user = kw["messages"][0]["content"][0]["text"]
        if "synopses" in system:
            kind, body, usage = "synopsis", {"synopsis": self.synopsis}, (500, 30)
        elif system.startswith("Score this publication"):
            kind, body, usage = "screening", self.screening, (3000, 60)
        else:
            kind, body, usage = "dense", self.dense, (2000, 200)
        self.calls.append({"kind": kind, "model": kw["modelId"], "system": system, "user": user})
        return {
            "output": {"message": {"content": [{"text": json.dumps(body)}]}},
            "usage": {"inputTokens": usage[0], "outputTokens": usage[1]},
            "stopReason": "end_turn",
        }


def metered(fake: FakeConverse) -> signals.MeteredBedrockClient:
    client = signals.MeteredBedrockClient()
    client._client = fake  # the boto3 seam; no AWS
    return client


def grant_item(**over):
    item = {
        "PK": "AREAINPUT", "SK": "grant#g1", "kind": "grant", "text_key": "g1", "mode": "full",
        "title": "Synthetic widget grant", "text": "An abstract about widgets. " * 10,
        "funder": "NIH NIGMS", "mechanism": "R01", "start_year": Decimal("2023"),
        "status": "pending", "created_at": "2026-10-14T07:00:00Z",
    }
    item.update(over)
    return item


def trial_item(**over):
    item = {
        "PK": "AREAINPUT", "SK": "trial#t1", "kind": "trial", "text_key": "t1", "mode": "full",
        "title": "Synthetic widget trial", "text": "A brief summary of a widget trial.",
        "phase": "PHASE2", "study_type": "INTERVENTIONAL",
        "conditions": ["Condition A", "Condition B"], "interventions": ["Drug: X", "Drug: Placebo"],
        "start_year": Decimal("2022"), "status": "pending", "created_at": "2026-10-14T07:00:00Z",
    }
    item.update(over)
    return item


def score(item, fake):
    return signals.score_input(signals.AreaInput.from_item(item), bedrock=metered(fake),
                               taxonomy=TAXONOMY, int_to_id=INT_TO_ID, id_to_int=ID_TO_INT,
                               taxonomy_version=TV)


# --- input validation -------------------------------------------------------

@pytest.mark.parametrize("over, msg", [
    ({"kind": "patent"}, "unknown kind"),
    ({"mode": "abstract"}, "unknown mode"),
    ({"title": "  "}, "missing title"),
    ({"text_key": ""}, "missing text_key"),
    ({"start_year": "soon"}, "bad start_year"),
])
def test_invalid_inputs_are_rejected(over, msg):
    with pytest.raises(signals.InvalidInput, match=msg):
        signals.AreaInput.from_item(grant_item(**over))


def test_missing_optional_fields_are_tolerated():
    inp = signals.AreaInput.from_item({"kind": "trial", "text_key": "t", "mode": "title",
                                       "title": "T"})
    assert inp.start_year is None and inp.conditions == () and inp.text == ""


# --- grant, full text ---------------------------------------------------------

def test_full_grant_uses_the_grant_prompt_and_scores_synopsis_plus_abstract():
    fake = FakeConverse()
    result = score(grant_item(), fake)

    assert [c["kind"] for c in fake.calls] == ["synopsis", "screening", "dense"]
    syn = fake.calls[0]
    assert syn["system"] == prompts.GRANT_SYNOPSIS_SYSTEM
    assert syn["user"] == prompts.build_grant_user_content(
        title="Synthetic widget grant", journal="NIH NIGMS R01", year=2023,
        abstract=grant_item()["text"].strip())
    screening_user = fake.calls[1]["user"]
    assert "Synopsis: Synthetic synopsis of a study of widgets" in screening_user
    assert "Abstract: An abstract about widgets." in screening_user
    assert fake.calls[1]["model"] == HAIKU_MODEL and fake.calls[2]["model"] == SONNET_MODEL

    item = result.item
    assert item["PK"] == "AREASIGNAL#grant:g1" and item["SK"] == "META"
    assert item["synopsis"] == "Synthetic synopsis of a study of widgets"
    assert item["synopsis_model"] == SONNET_MODEL
    assert item["taxonomy_version"] == TV
    assert item["scorer_fallback_model"] is None
    assert item["input_hash"] == signals.AreaInput.from_item(grant_item()).input_hash()
    # Only dense scores >= 0.3, as Decimals, best first.
    assert item["topic_scores"] == [{"topic_id": T0, "score": Decimal("0.82")}]


def test_persisted_row_has_the_contract_fields():
    item = score(grant_item(), FakeConverse()).item
    for key in ("PK", "SK", "synopsis", "synopsis_model", "topic_scores", "taxonomy_version",
                "input_hash", "scored_at", "scorer_fallback_model"):
        assert key in item, key
    # No float anywhere: the resource Table rejects floats.
    assert all(isinstance(t["score"], Decimal) for t in item["topic_scores"])


def test_all_qualifying_topics_are_kept_including_low_ranked_ones():
    fake = FakeConverse(screening={"0": 0.9, "1": 0.6, "2": 0.4},
                        dense={"0": {"score": 0.3}, "1": {"score": 0.95}, "2": {"score": 0.29}})
    item = score(grant_item(), fake).item
    assert item["topic_scores"] == [{"topic_id": T1, "score": Decimal("0.95")},
                                    {"topic_id": T0, "score": Decimal("0.3")}]


# --- title-only path ------------------------------------------------------------

@pytest.mark.parametrize("over", [{"mode": "title", "text": "Funder purpose sentence."},
                                  {"mode": "full", "text": ""}])
def test_title_only_grant_skips_synopsis_and_puts_title_in_synopsis_slot(over):
    fake = FakeConverse()
    item = score(grant_item(**over), fake).item

    assert [c["kind"] for c in fake.calls] == ["screening", "dense"]
    assert "Synopsis: Synthetic widget grant" in fake.calls[0]["user"]
    assert f"Abstract: {over['text']}" in fake.calls[0]["user"]
    assert item["synopsis"] == "Synthetic widget grant"
    assert item["synopsis_model"] == signals.TITLE_ONLY_SYNOPSIS_MODEL


# --- trials --------------------------------------------------------------------

def test_trial_uses_trial_prompt_and_summary_conditions_interventions():
    fake = FakeConverse()
    item = score(trial_item(), fake).item

    syn = fake.calls[0]
    assert syn["system"] == prompts.TRIAL_SYNOPSIS_SYSTEM
    assert syn["user"] == (
        "Title: Synthetic widget trial\nStudy type: INTERVENTIONAL; Phase: PHASE2\n"
        "Start year: 2022\nBrief summary: A brief summary of a widget trial.\n\nReturn JSON only.")
    assert ("Abstract: A brief summary of a widget trial.\nConditions: Condition A; Condition B\n"
            "Interventions: Drug: X; Drug: Placebo") in fake.calls[1]["user"]
    assert item["PK"] == "AREASIGNAL#trial:t1"


def test_title_only_trial_still_carries_conditions_to_the_scorer():
    fake = FakeConverse()
    score(trial_item(mode="title", text=""), fake)
    assert [c["kind"] for c in fake.calls] == ["screening", "dense"]
    assert "Abstract: Conditions: Condition A; Condition B" in fake.calls[0]["user"]


# --- metering, failures, hashing -----------------------------------------------------

def test_every_bedrock_call_is_metered_from_its_response():
    result = score(grant_item(), FakeConverse())
    assert result.usage == [(SONNET_MODEL, 500, 30), (HAIKU_MODEL, 3000, 60),
                            (SONNET_MODEL, 2000, 200)]
    assert result.estimated_usd == Decimal("0")


def test_title_only_item_meters_only_the_scorer():
    result = score(grant_item(mode="title"), FakeConverse())
    assert [m for m, _, _ in result.usage] == [HAIKU_MODEL, SONNET_MODEL]


def test_empty_grant_synopsis_is_a_transient_failure_not_a_title_fallback():
    with pytest.raises(signals.ScoringFailed, match="synopsis"):
        score(grant_item(), FakeConverse(synopsis=""))


def test_empty_trial_synopsis_scores_on_the_title_as_the_pilot_did():
    fake = FakeConverse(synopsis="")
    result = score(trial_item(), fake)
    item = result.item
    assert item["synopsis"] == "Synthetic widget trial"
    assert item["synopsis_model"] == signals.TITLE_ONLY_SYNOPSIS_MODEL
    assert "scored on title" in item["synopsis_error"]
    # The scorer read the title in the synopsis slot.
    assert "Synthetic widget trial" in fake.calls[-1]["user"]
    assert item["topic_scores"]


class SynopsisDenied(FakeConverse):
    """The synopsis call fails with a system-wide error; scoring would work."""

    def __init__(self, code="UnrecognizedClientException", **kw):
        super().__init__(**kw)
        self.code = code

    def converse(self, **kw):
        system = " ".join(s.get("text", "") for s in kw.get("system", []))
        if "synopses" in system:
            raise ClientError({"Error": {"Code": self.code, "Message": "denied"}}, "Converse")
        return super().converse(**kw)


def test_trial_synopsis_lost_to_a_system_wide_error_is_not_title_scored():
    client = metered(SynopsisDenied())
    with pytest.raises(signals.ScoringFailed, match="synopsis"):
        signals.score_input(signals.AreaInput.from_item(trial_item()), bedrock=client,
                            taxonomy=TAXONOMY, int_to_id=INT_TO_ID, id_to_int=ID_TO_INT,
                            taxonomy_version=TV)
    assert signals.systemic_error(client.errors) == "Bedrock UnrecognizedClientException"


@pytest.mark.parametrize("exc, expected", [
    (ClientError({"Error": {"Code": "ExpiredTokenException"}}, "Converse"),
     "Bedrock ExpiredTokenException"),
    (ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse"),
     "Bedrock ThrottlingException"),
    (EndpointConnectionError(endpoint_url="https://bedrock.invalid"),
     "Bedrock EndpointConnectionError"),
    (ClientError({"Error": {"Code": "ValidationException"}}, "Converse"), None),
    (ValueError("bad json"), None),
])
def test_systemic_error_classification(exc, expected):
    assert signals.systemic_error([exc]) == expected


def test_openai_synopsis_fallback_tokens_are_in_the_usage(monkeypatch):
    def gpt_synopsis(**kw):
        return SynopsisResult(pmid=kw["pmid"], synopsis="Synthetic synopsis", model=GPT5_MODEL,
                              input_tokens=1234, output_tokens=56, error=None)
    monkeypatch.setattr(signals, "generate_synopsis", gpt_synopsis)
    result = score(grant_item(), FakeConverse())
    assert (GPT5_MODEL, 1234, 56) in result.usage
    assert result.item["synopsis_model"] == GPT5_MODEL


def test_failed_attempt_carries_the_openai_spend_the_meter_cannot_see(monkeypatch):
    def gpt_empty(**kw):
        return SynopsisResult(pmid=kw["pmid"], synopsis=None, model=GPT5_MODEL,
                              input_tokens=900, output_tokens=0, error="filtered")
    monkeypatch.setattr(signals, "generate_synopsis", gpt_empty)
    with pytest.raises(signals.ScoringFailed) as info:
        score(grant_item(), FakeConverse())
    assert info.value.extra_usage == [(GPT5_MODEL, 900, 0)]


def test_scorer_failure_raises_and_leaves_spend_on_the_client():
    fake = FakeConverse(screening={"999": 0.9})  # no recognized topic ids
    client = metered(fake)
    with pytest.raises(signals.ScoringFailed, match="area scores"):
        signals.score_input(signals.AreaInput.from_item(grant_item()), bedrock=client,
                            taxonomy=TAXONOMY, int_to_id=INT_TO_ID, id_to_int=ID_TO_INT,
                            taxonomy_version=TV)
    assert [m for m, _, _ in client.usage] == [SONNET_MODEL, HAIKU_MODEL]


def test_input_hash_moves_with_text_but_not_with_status():
    base = signals.AreaInput.from_item(grant_item()).input_hash()
    assert signals.AreaInput.from_item(grant_item(status="scored")).input_hash() == base
    assert signals.AreaInput.from_item(grant_item(text="other")).input_hash() != base
    assert signals.AreaInput.from_item(grant_item(mechanism="R21")).input_hash() != base


def test_taxonomy_version_moves_with_topic_content():
    edited = json.loads(json.dumps(TAXONOMY))
    edited["topics"][0]["description"] += " Edited."
    assert signals.current_taxonomy_version(edited) != TV
    assert TV.startswith(f"{TAXONOMY['taxonomy_version']}@")
