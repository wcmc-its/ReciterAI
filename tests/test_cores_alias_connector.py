"""Alias matching treats "and" and "&" as one connector (signals._alias_pattern).

Before this, `re.escape(alias)` saw only the dictionary's spelling, so 6 aliases on
3 cores (10, 11, 13) missed every paper that wrote the other one. These pin the
equivalence, the cases that must NOT newly match, and that an alias with no
connector (all of core 14's, every acronym) compiles to exactly the old regex.
"""
from __future__ import annotations

import re

import pytest

from pipeline_cores import pmc_search
from pipeline_cores.dictionary import load_cores
from pipeline_cores.models import CoreDefinition
from pipeline_cores.signals import (
    _ACRONYM,
    _alias_pattern,
    acknowledgement_signal,
    alias_variants,
    match_section,
)

_MIAC = CoreDefinition(core_id="11", name="Microscopy and Image Analysis",
                       aliases=["Microscopy and Image Analysis Core"],
                       alias_hits={"Microscopy and Image Analysis Core": 290})
_PROT = CoreDefinition(core_id="13", name="Proteomics and Metabolomics",
                       aliases=["Proteomics & Metabolomics Core Facility"])


def _old_pattern(name: str):
    """The matcher as it was before the connector rule — the regression baseline."""
    if _ACRONYM.match(name):
        return re.compile(rf"\b{re.escape(name)}\b")
    return re.compile(re.escape(name), re.IGNORECASE)


# --- the equivalence ---------------------------------------------------------
@pytest.mark.parametrize("text", [
    "imaged at the Microscopy & Image Analysis Core of Weill Cornell",
    "imaged at the Microscopy &amp; Image Analysis Core of Weill Cornell",   # double-escaped JATS
    "imaged at the Microscopy&Image Analysis Core of Weill Cornell",         # no spaces
    "imaged at the Microscopy & Image Analysis Core",               # nbsp
    "imaged at the Microscopy\nand  Image Analysis Core",                    # line break, 2 spaces
    "imaged at the MICROSCOPY AND IMAGE ANALYSIS CORE",                      # case, as before
    "imaged at the Microscopy and Image Analysis Core",                      # the dictionary form
])
def test_an_and_alias_matches_every_connector_spelling(text):
    r = acknowledgement_signal(text, _MIAC)
    assert r.ack_matched and r.ack_alias == "Microscopy and Image Analysis Core", text
    assert r.ack_alias_hits == 290       # the dictionary alias's count rides along unchanged


@pytest.mark.parametrize("text", [
    "Proteomics and Metabolomics Core Facility",
    "Proteomics & Metabolomics Core Facility",
    "Proteomics &amp; Metabolomics Core Facility",
])
def test_an_ampersand_alias_matches_the_word_and(text):
    assert acknowledgement_signal(f"run by the {text}.", _PROT).ack_matched, text


# --- no new false matches ----------------------------------------------------
@pytest.mark.parametrize("text", [
    "the Microscopy Image Analysis Core",            # no connector at all
    "the Microscopy, Image Analysis Core",           # a comma is not a connector
    "the Microscopy / Image Analysis Core",
    "the Microscopy or Image Analysis Core",
    "the Microscopy andImage Analysis Core",         # "and" needs whitespace both sides
    "the Microscopyand Image Analysis Core",
    "the Microscopy and the Image Analysis Core",    # an extra word is a different name
    "the Microscopy && Image Analysis Core",         # two connectors are not one
    "the Microscopy and & Image Analysis Core",
])
def test_near_misses_still_do_not_match(text):
    assert not acknowledgement_signal(text, _MIAC).ack_matched, text


def test_and_inside_a_word_in_the_alias_is_not_a_connector():
    """'Andrology' / 'Bandwidth' carry the letters, not the word."""
    for alias in ("Andrology Core", "Bandwidth Analysis Core", "Brand Imaging Core"):
        assert alias_variants(alias) == [alias]
        assert _alias_pattern(alias).pattern == _old_pattern(alias).pattern


def test_acronym_path_is_untouched():
    """ARCH-style aliases keep the case-sensitive word boundary: 'arch' and an ARCH
    econometric model inside a longer token stay out."""
    for alias in ("ARCH", "CBIC", "GRCF", "ABAC"):
        assert _alias_pattern(alias).pattern == _old_pattern(alias).pattern
        assert _alias_pattern(alias).flags & re.IGNORECASE == 0
        assert alias_variants(alias) == [alias]
    p = _alias_pattern("ARCH")
    assert p.search("data from ARCH were") and not p.search("the aortic arch") \
        and not p.search("GARCH(1,1)")


def test_every_shipped_alias_without_a_connector_compiles_to_the_old_regex():
    """The change can only move aliases that contain "and"/"&". Every other alias in
    the dictionary — all of core 14's among them — gets byte-identical regex."""
    changed = set()
    for core in load_cores():
        for alias in core.aliases:
            if _alias_pattern(alias).pattern != _old_pattern(alias).pattern:
                changed.add((core.core_id, alias))
    assert changed == {
        ("10", "Ellen and Gary Davis Immune Monitoring Core"),
        ("11", "Microscopy and Image Analysis Core"),
        ("11", "Microscopy and Image Analysis Core Facility"),
        ("11", "Weill Cornell Medicine Microscopy and Image Analysis Core Facility"),
        ("13", "Proteomics & Metabolomics Core Facility"),
        ("13", "Proteomics and Metabolomics Core Facility"),
        ("13", "Meyer Cancer Center Proteomics and Metabolomics Core Facility"),
        ("13", "Proteomics & Metabolomics Core"),
        ("13", "Weill Cornell Proteomics & Metabolomics Core Facility"),
    }
    core14 = next(c for c in load_cores() if c.core_id == "14")
    assert all(alias_variants(a) == [a] for a in core14.aliases)


def test_match_section_finds_an_ampersand_in_raw_jats():
    xml = ("<article><body><sec><title>Methods</title><p>Imaged at the Microscopy "
           "&amp; Image Analysis Core.</p></sec></body></article>")
    assert match_section(xml, "Microscopy and Image Analysis Core") == "methods"


# --- the PMC side: search and count both spellings ---------------------------
def test_alias_variants_and_term():
    a = "Microscopy and Image Analysis Core"
    assert alias_variants(a) == [a, "Microscopy & Image Analysis Core"]
    assert alias_variants("Proteomics & Metabolomics Core") == [
        "Proteomics and Metabolomics Core", "Proteomics & Metabolomics Core"]
    assert pmc_search.alias_term(a) == \
        '"Microscopy and Image Analysis Core" OR "Microscopy & Image Analysis Core"'
    assert pmc_search.alias_term("Flow Cytometry Core") == '"Flow Cytometry Core"'   # as before


def test_esearch_pmc_searches_both_spellings_in_one_term(monkeypatch):
    """PMC drops "&" from a phrase, so the two spellings return different paper sets
    (probed 2026-10-06: 207 vs 88 ids, 5 shared). One OR term fetches both."""
    terms = []

    def fake_get(url, params, timeout):
        terms.append(params["term"])
        return {"esearchresult": {"count": "2", "idlist": ["1", "2"]}}

    monkeypatch.setattr(pmc_search, "_get_json", fake_get)
    assert pmc_search.esearch_pmc("Microscopy and Image Analysis Core") == ["1", "2"]
    assert terms == ['"Microscopy and Image Analysis Core" OR "Microscopy & Image Analysis Core"']


def _fake_counts(table):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params["term"])
        n = table[params["term"]]
        wl = {"quotedphrasesnotfound": [params["term"]]} if n is None else {}
        return {"esearchresult": {"count": str(n or 7221), "warninglist": wl}}
    return calls, fake_get


def test_esearch_count_is_the_union_of_both_spellings(monkeypatch):
    calls, fake = _fake_counts({
        '"Microscopy and Image Analysis Core"': 207,
        '"Microscopy & Image Analysis Core"': 88,
        '"Microscopy and Image Analysis Core" OR "Microscopy & Image Analysis Core"': 290,
    })
    monkeypatch.setattr(pmc_search, "_get_json", fake)
    assert pmc_search.esearch_count("Microscopy and Image Analysis Core") == 290   # not 207, not 295
    assert len(calls) == 3


def test_esearch_count_keeps_none_when_no_spelling_runs(monkeypatch):
    """An OR never reports quotedphrasesnotfound (it scores the dead phrase 0), so the
    None rule is decided on the spellings alone: none runs -> None, not a 0 that
    would read as `distinctive` (+4.35)."""
    calls, fake = _fake_counts({
        '"Ellen and Gary Davis Immune Monitoring Core"': None,
        '"Ellen & Gary Davis Immune Monitoring Core"': None,
    })
    monkeypatch.setattr(pmc_search, "_get_json", fake)
    assert pmc_search.esearch_count("Ellen and Gary Davis Immune Monitoring Core") is None
    assert len(calls) == 2                                       # no OR request wasted


def test_esearch_count_uses_the_or_when_only_one_spelling_runs(monkeypatch):
    calls, fake = _fake_counts({
        '"A and B Core"': 12,
        '"A & B Core"': None,
        '"A and B Core" OR "A & B Core"': 12,
    })
    monkeypatch.setattr(pmc_search, "_get_json", fake)
    assert pmc_search.esearch_count("A and B Core") == 12


def test_two_spellings_of_one_alias_carry_one_count():
    """Core 13 lists both "Proteomics & Metabolomics Core Facility" and its "and"
    spelling. The matcher treats them as one alias, so their cached specificity must
    be one number; a mismatch means the dictionary was refreshed before the union
    count (re-run `python3 -m pipeline_cores.refresh_alias_hits --core 13 --write`)."""
    for core in load_cores():
        by_form = {}
        for alias, n in core.alias_hits.items():
            by_form.setdefault(frozenset(alias_variants(alias)), set()).add(n)
        assert all(len(ns) == 1 for ns in by_form.values()), (core.core_id, by_form)
