"""Unit tests for pipeline_tools.context_quality — the shared snippet guards (#238).

Covers the three contracts (verbatim / names-the-tool / one-sentence) and the
specific false positives the live calibration surfaced, so they stay fixed.
"""
from __future__ import annotations

from pipeline_tools.context_quality import (
    MAX_SENTENCE_CHARS,
    accept_snippet,
    is_sentence_complete,
    is_single_sentence,
    is_verbatim,
    names_tool,
)

ABSTRACT = (
    "We used patch-clamp recording to measure currents. Magnetic resonance imaging "
    "revealed a scalpel sign in the cohort."
)


# --- verbatim -------------------------------------------------------------

def test_is_verbatim_matches_whitespace_and_case_insensitively():
    assert is_verbatim("patch-clamp  recording", ABSTRACT)        # collapsed spaces
    assert is_verbatim("MAGNETIC RESONANCE IMAGING", ABSTRACT)    # case-insensitive
    assert not is_verbatim("electron microscopy", ABSTRACT)       # not in abstract
    assert not is_verbatim("", ABSTRACT)


# --- names the tool -------------------------------------------------------

def test_names_tool_shares_salient_token():
    assert names_tool("We used patch-clamp recording to measure currents.", "patch-clamp recording")
    assert not names_tool("Imaging was categorized by 3 masked graders.", "fundus photography")  # #10


def test_names_tool_recognizes_short_and_parenthetical_acronyms():
    assert names_tool("the lesion was modeled in VR for planning", "VR modeling")          # bare 2-char
    assert names_tool("characterized through NGS in the cohort", "Next-generation sequencing (NGS) platform")


def test_names_tool_tolerates_inflection_via_stem():
    assert names_tool("the eyes were cryosectioned for staining", "cryosectioning")


def test_names_tool_accepts_when_name_is_pure_generic():
    assert names_tool("anything at all here", "the system")  # no salient token => cannot judge => accept


# --- one sentence ---------------------------------------------------------

def test_single_sentence_accepts_normal_sentence():
    assert is_single_sentence("Magnetic resonance imaging revealed a scalpel sign in the cohort.")


def test_single_sentence_rejects_punctuated_merge():
    assert not is_single_sentence("We ran the assay. Results were significant across all arms.")


def test_single_sentence_rejects_missing_period_merge():
    # The #3 ClinicalBERT case: a starter word capitalized mid-clause, no period.
    assert not is_single_sentence(
        "our tool reidentified 9% of clinical notes Our study highlights significant weaknesses."
    )


def test_single_sentence_false_positives_stay_accepted():
    # IMRAD section nouns mid-sentence are NOT a merge.
    assert is_single_sentence(
        "Annotators annotated the Results and Conclusions of 500 PubMed abstracts (n=140)."
    )
    # Multi-initial company name with internal periods is one sentence.
    assert is_single_sentence(
        "A Viabahn VBX stent (W.L. Gore, Flagstaff, AZ) is advanced into position and deployed."
    )
    # Parenthetical trial number abbreviation "(no. NCT...)".
    assert is_single_sentence(
        "A phase II, open-label trial (no. NCT04445987) was conducted in 30 patients with SD."
    )


# --- sentence-complete (#254) ---------------------------------------------

def test_sentence_complete_accepts_whole_sentences():
    assert is_sentence_complete("Nav1.3 was heterologously expressed in HEK293T cells.")
    assert is_sentence_complete("HEK293T cells are widely used in manufacturing facilities!")
    # opens with a digit (a cell-line name) — must NOT be flagged a fragment.
    assert is_sentence_complete("3T3-L1 adipocytes were treated with metformin for 8 days.")
    # tolerates a trailing closing paren / quote after the terminator.
    assert is_sentence_complete("The construct was expressed in HEK293 cells (Clontech).")
    assert is_sentence_complete('It was described as "a robust line."')


def test_sentence_complete_rejects_the_live_254_fragment():
    # The exact SPS-staging defect: starts lowercase mid-clause, no terminal punctuation.
    frag = ("they both dimerize in the plasma membrane of HEK293 cells and FRET experiments "
            "with SNAP-tagged wild-type and F220C opsin expressed in HEK293 cells")
    assert not is_sentence_complete(frag)


def test_sentence_complete_rejects_each_failure_mode_independently():
    assert not is_sentence_complete("they both dimerize in the plasma membrane.")  # lowercase start
    assert not is_sentence_complete("HEK293T cells are widely used in GMP facilities")  # no terminal punct
    assert not is_sentence_complete("")
    assert not is_sentence_complete("   ")


# --- composite + length ---------------------------------------------------

def test_accept_snippet_requires_all_three_plus_length():
    abs = "We applied patch-clamp recording to CA1 neurons in acute slices."
    assert accept_snippet("We applied patch-clamp recording to CA1 neurons in acute slices.", abs, "patch-clamp recording")
    assert not accept_snippet("electron microscopy was used widely", abs, "patch-clamp recording")  # not verbatim
    assert not accept_snippet("tiny", abs, "patch-clamp recording")                                  # too short
    assert not accept_snippet("x" * (MAX_SENTENCE_CHARS + 1), abs, "patch-clamp recording")          # over ceiling
