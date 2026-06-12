"""Tests for the #879 family DEFINE pass (pipeline_tools/define_families.py + prompt).

Covers: the definition validator (length / sentence / voice), the happy path, the
bounded re-prompt on a validation failure, persistent-failure → left undefined,
partial-failure tolerance (batch raises), low-confidence recording, and the prompt
payload / prior-failure re-prompt note.
"""

from __future__ import annotations

import pytest

from pipeline_tools.define_families import _validate_definition, define_families
from prompts.tool_family_define import build_define_user_message


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class _FakeReg:
    def __init__(self, fams):
        self._f = {f["family_id"]: dict(f) for f in fams}

    def records(self):
        return list(self._f.values())

    def set_definition(self, fid, definition, *, confidence=None):
        self._f[fid]["definition"] = definition.strip()
        if confidence is not None:
            self._f[fid]["definition_confidence"] = confidence
        return self._f[fid]

    def get(self, fid):
        return self._f.get(fid)


def _fam(fid, label="molecular docking"):
    return {"family_id": fid, "label": label, "supercategory": "computational_modeling",
            "dominant_kind": "software", "member_display_names": ["AutoDock", "Glide"]}


def _scripted_call(attempts):
    """attempts: list of {family_id: (definition, confidence)} returned per successive call."""
    state = {"n": 0}

    def call_json(system, user):
        i = state["n"]
        state["n"] += 1
        mapping = attempts[i] if i < len(attempts) else {}
        return {
            "families": [
                {"family_id": fid, "definition": d, "confidence": c}
                for fid, (d, c) in mapping.items()
            ],
            "_model": "stub-sonnet",
        }
    return call_json, state


_GOOD = "Computational methods that screen candidate molecules against a protein structure to rank likely binders."


# --------------------------------------------------------------------------- #
# validator
# --------------------------------------------------------------------------- #


def test_validate_accepts_a_clean_definition():
    ok, reason = _validate_definition(_GOOD)
    assert ok and reason == ""


def test_validate_rejects_empty_and_too_short():
    assert _validate_definition("")[0] is False
    assert _validate_definition("docking")[0] is False


def test_validate_rejects_too_many_words():
    long = " ".join(["word"] * 60)
    ok, reason = _validate_definition(long)
    assert not ok and "too long" in reason


def test_validate_rejects_too_many_sentences():
    three = "Methods do A. Methods do B. Methods do C."
    ok, reason = _validate_definition(three)
    assert not ok and "sentences" in reason


def test_validate_rejects_marketing_and_efficacy_phrases():
    assert _validate_definition("A powerful method for docking molecules.")[0] is False
    assert _validate_definition("Methods clinically proven to dock molecules well.")[0] is False


# --------------------------------------------------------------------------- #
# define_families
# --------------------------------------------------------------------------- #


def test_happy_path_defines_every_family():
    reg = _FakeReg([_fam("fam_0001"), _fam("fam_0002")])
    call, state = _scripted_call([{
        "fam_0001": (_GOOD, "high"),
        "fam_0002": (_GOOD, "high"),
    }])
    deltas = define_families(reg, call_json=call)
    assert {d["family_id"] for d in deltas} == {"fam_0001", "fam_0002"}
    assert reg.get("fam_0001")["definition"] == _GOOD
    assert state["n"] == 1  # no re-prompt needed


def test_reprompts_once_on_validation_failure_then_succeeds():
    reg = _FakeReg([_fam("fam_0001"), _fam("fam_0002")])
    call, state = _scripted_call([
        {"fam_0001": (_GOOD, "high"), "fam_0002": ("A powerful docking suite.", "high")},  # fam_0002 banned word
        {"fam_0002": (_GOOD, "high")},  # retry fixes it
    ])
    deltas = define_families(reg, call_json=call)
    assert {d["family_id"] for d in deltas} == {"fam_0001", "fam_0002"}
    assert reg.get("fam_0002")["definition"] == _GOOD
    assert state["n"] == 2  # one re-prompt round happened


def test_persistent_failure_leaves_family_undefined():
    reg = _FakeReg([_fam("fam_0001")])
    call, _ = _scripted_call([
        {"fam_0001": ("A powerful suite.", "high")},
        {"fam_0001": ("Still a powerful suite.", "high")},  # retry still banned
    ])
    deltas = define_families(reg, call_json=call)
    assert deltas == []
    assert reg.get("fam_0001").get("definition") is None


def test_partial_failure_tolerant_when_batch_raises():
    reg = _FakeReg([_fam("fam_0001")])

    def boom(system, user):
        raise RuntimeError("bedrock throttled")

    deltas = define_families(reg, call_json=boom)  # must not raise
    assert deltas == []
    assert reg.get("fam_0001").get("definition") is None


def test_low_confidence_is_recorded():
    reg = _FakeReg([_fam("fam_0001")])
    call, _ = _scripted_call([{"fam_0001": (_GOOD, "low")}])
    deltas = define_families(reg, call_json=call)
    assert deltas[0]["confidence"] == "low"
    assert reg.get("fam_0001")["definition_confidence"] == "low"


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #


def test_user_message_carries_ids_and_members():
    msg = build_define_user_message([_fam("fam_0001")])
    assert "fam_0001" in msg and "AutoDock" in msg


def test_user_message_reprompt_names_the_defect():
    msg = build_define_user_message([_fam("fam_0001")], prior_failures={"fam_0001": "too long (60 words > 45)"})
    assert "rejected" in msg and "too long" in msg
