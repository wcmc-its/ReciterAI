"""Repeat-user affinity: tenure gate, small-denominator shrinkage, time decay.

Pure tests on signals.build_affinity_index / author_affinity, the ingest helpers that
feed them (against an in-memory SQLite stand-in for reciterdb), and run_core's wiring.
"""
from __future__ import annotations

import pytest

from pipeline_cores import ingest, signals
from pipeline_cores.combine import DEFAULT_CONFIRM_THRESHOLD, combine, evidence_features
from pipeline_cores.models import STATUS_BELOW, STATUS_CANDIDATE, STATUS_CONFIRMED, SignalResult
from pipeline_cores.signals import author_affinity, build_affinity_index, tenure_window


def _feat(rate):
    return evidence_features(SignalResult(author_affinity=rate))


# --- (c) small-denominator shrinkage ---------------------------------------
def test_a_one_of_one_author_no_longer_reaches_aff_core():
    """Shrinkage on its own terms: one corpus paper confirmed once was rate 1.0 =
    aff:core, which cleared the confirm bar alone. (In production that one paper was
    usually the paper being scored — the self-confirmation loop, closed by
    self-exclusion below; shrinkage covers the small denominators that remain.)"""
    idx = build_affinity_index({"one": {"14": 1}, "two": {"14": 2}, "three": {"14": 3}},
                               {"one": 1, "two": 2, "three": 3})
    assert signals.AFFINITY_SHRINK_K == 1.0
    assert idx.rate("one", "14") == 0.5                       # 1 / (1 + 1)
    assert _feat(idx.rate("one", "14")) == ["aff:regular"]
    assert _feat(idx.rate("two", "14")) == ["aff:regular"]    # 2/3
    assert _feat(idx.rate("three", "14")) == ["aff:core"]     # 3/4: a real track record
    rec = combine("p", "14", SignalResult(author_affinity=author_affinity(idx, ["one"], "14")))
    assert rec.status != STATUS_CONFIRMED


def test_shrinkage_barely_moves_a_large_denominator():
    idx = build_affinity_index({"big": {"2": 40}}, {"big": 50})
    assert idx.rate("big", "2") == pytest.approx(40 / 51)
    assert _feat(idx.rate("big", "2")) == ["aff:core"]


def test_shrink_k_zero_is_the_plain_rate():
    idx = build_affinity_index({"one": {"2": 1}}, {"one": 1}, shrink_k=0)
    assert idx.rate("one", "2") == 1.0


def test_aff_core_alone_does_not_confirm_but_reaches_the_queue():
    rec = combine("p", "2", SignalResult(author_affinity=0.9))
    assert rec.status == STATUS_CANDIDATE
    assert rec.likelihood < DEFAULT_CONFIRM_THRESHOLD


# --- (a) tenure gate --------------------------------------------------------
def test_tenure_window_lags():
    lo, hi = signals.TENURE_LAG_BEFORE, signals.TENURE_LAG_AFTER
    assert (lo, hi) == (3, 2)
    assert tenure_window((2015, 2021)) == (2012, 2023)
    assert tenure_window((2015, None)) == (2012, None)        # still here: open-ended
    assert tenure_window(None) is None                         # unknown: not gated
    assert tenure_window((None, 2021)) is None


def test_a_departed_author_lends_nothing_to_a_paper_after_the_lag():
    """Per AUTHOR, per scored paper: gone in 2021 -> speaks for papers through 2023."""
    idx = build_affinity_index({"left": {"14": {2020: 3}}}, {"left": {2020: 3}},
                               tenure={"left": (2015, 2021)}, shrink_k=0)
    assert author_affinity(idx, ["left"], "14", 2023) == 1.0
    assert author_affinity(idx, ["left"], "14", 2024) == 0.0
    assert author_affinity(idx, ["left"], "14", 2012) == 1.0   # start - 3
    assert author_affinity(idx, ["left"], "14", 2011) == 0.0
    assert author_affinity(idx, ["left"], "14", None) == 1.0   # undated paper: not gated


def test_the_gate_is_per_author_and_never_drops_the_paper():
    """A co-author still in tenure keeps their affinity on the same paper."""
    idx = build_affinity_index({"left": {"14": {2020: 3}}, "here": {"14": {2024: 1}}},
                               {"left": {2020: 3}, "here": {2024: 10}},
                               tenure={"left": (2010, 2020), "here": (2019, None)}, shrink_k=0)
    assert author_affinity(idx, ["left", "here"], "14", 2025) == 0.1
    # the acknowledgement/staff signals never pass through the index: a gated paper
    # still confirms on its acknowledgement
    sig = SignalResult(ack_matched=True, ack_alias_hits=1, ack_institution="home",
                       author_affinity=author_affinity(idx, ["left"], "14", 2025))
    assert sig.author_affinity == 0.0
    assert combine("p", "14", sig).status == STATUS_CONFIRMED


def test_out_of_tenure_confirmations_do_not_build_the_rate():
    """Numerator AND denominator are gated: both sides of the ratio must mean the same
    thing (the author's in-tenure output)."""
    idx = build_affinity_index({"x": {"14": {2019: 1, 2025: 3}}},
                               {"x": {2019: 4, 2025: 10}},
                               tenure={"x": (2010, 2020)}, shrink_k=0)
    assert idx.rate("x", "14", 2019) == 0.25                   # 1 of 4, not (1+3)/(4+10)
    gone = build_affinity_index({"x": {"14": {2025: 3}}}, {"x": {2025: 10}},
                                tenure={"x": (2010, 2020)})
    assert "x" not in gone.papers                              # nothing left in tenure


def test_an_author_with_no_tenure_row_is_not_gated():
    idx = build_affinity_index({"x": {"14": {2025: 1}}}, {"x": {2025: 4}}, tenure={},
                               shrink_k=0)
    assert idx.rate("x", "14", 2025) == 0.25


# --- (b) time decay ---------------------------------------------------------
def test_decay_is_off_by_default():
    assert signals.AFFINITY_HALF_LIFE_YEARS is None
    idx = build_affinity_index({"x": {"2": {2020: 2}}}, {"x": {2020: 2, 2024: 2}}, shrink_k=0)
    assert idx.rate("x", "2", 2024) == 0.5


def test_decay_weights_numerator_and_denominator_by_distance_from_the_scored_year():
    idx = build_affinity_index({"x": {"2": {2020: 2}}}, {"x": {2020: 2, 2024: 2}},
                               shrink_k=0, half_life=2)
    # 2020 is two half-lives from 2024: num 2 * 0.25 = 0.5, den 0.5 + 2 = 2.5
    assert idx.rate("x", "2", 2024) == pytest.approx(0.2)
    # ...and recent history counts in full from its own year
    assert idx.rate("x", "2", 2020) == pytest.approx(2 / (2 + 2 * 0.25))
    assert idx.rate("x", "2", None) == 0.5                     # no reference year: undecayed


# --- plain-count callers keep working ---------------------------------------
def test_year_unknown_counts_are_neither_gated_nor_decayed():
    idx = build_affinity_index({"x": {"2": 1}}, {"x": 4}, tenure={"x": (2010, 2012)},
                               shrink_k=0, half_life=1)
    assert idx.rate("x", "2", 2025) == 0.0                     # the PAPER is out of tenure
    assert idx.rate("x", "2", 2011) == 0.25                    # counts themselves undated


# --- ingest helpers against a SQLite stand-in for reciterdb ------------------
@pytest.fixture
def engine():
    sqlalchemy = pytest.importorskip("sqlalchemy")
    eng = sqlalchemy.create_engine("sqlite://")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE identity (cwid TEXT, startDateWCMFaculty INT, "
                          "endDateWCMFaculty INT, startDateWCMStudent INT, endDateWCMStudent INT)")
        c.exec_driver_sql("INSERT INTO identity VALUES "
                          "('fac', 2015, 2021, NULL, NULL), ('stu2fac', 2018, 2027, 2010, 2016), "
                          "('open', 2019, NULL, NULL, NULL), ('nodates', NULL, NULL, NULL, NULL), "
                          "('stu2open', 2018, NULL, 2010, 2016), ('openstu', NULL, NULL, 2022, NULL)")
        c.exec_driver_sql("CREATE TABLE analysis_summary_article (pmid INT, articleYear INT)")
        c.exec_driver_sql("INSERT INTO analysis_summary_article VALUES (1, 2021), (2, 2024), (3, NULL)")
    return eng


def test_fetch_author_tenure_unions_faculty_and_student_spans(engine):
    got = ingest.fetch_author_tenure(engine, ["fac", "stu2fac", "open", "nodates", "absent"])
    assert got == {"fac": (2015, 2021), "stu2fac": (2010, 2027), "open": (2019, None)}


def test_fetch_author_tenure_a_null_end_is_open_not_dropped(engine):
    """A student (2010-2016) now on the faculty with no end date is still here. max()
    over the non-NULL ends alone read them as gone in 2016."""
    got = ingest.fetch_author_tenure(engine, ["stu2open", "openstu"])
    assert got == {"stu2open": (2010, None), "openstu": (2022, None)}
    assert ingest.fetch_author_tenure(engine, []) == {}


def test_fetch_pub_years(engine):
    assert ingest.fetch_pub_years(engine, ["1", 2, "3", "9"]) == {"1": 2021, "2": 2024}
    assert ingest.fetch_pub_years(engine, []) == {}


def test_affinity_inputs_dates_counts_and_reads_tenure(engine, monkeypatch):
    monkeypatch.setattr(ingest, "fetch_author_totals",
                        lambda e, c=None: {"fac": {2021: 5, 2024: 5}})
    counts, totals, tenure, years = ingest.affinity_inputs(
        engine, {"fac": {"14": {"1", "2"}}}, years={"2": 2024})
    assert counts == {"fac": {"14": {2021: 1, 2024: 1}}}
    assert tenure == {"fac": (2015, 2021)}
    assert years == {"1": 2021, "2": 2024}
    idx = build_affinity_index(counts, totals, tenure=tenure, shrink_k=0)
    assert idx.rate("fac", "14", 2022) == 0.2                  # 2024 is past end + 2


def test_affinity_inputs_without_an_engine_is_year_unknown(monkeypatch):
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {"a": 4})
    counts, totals, tenure, years = ingest.affinity_inputs(None, {"a": {"2": {"7", "8"}}})
    assert counts == {"a": {"2": {None: 2}}} and tenure == {} and years == {}


# --- run_core wiring ----------------------------------------------------------
def test_run_core_gates_a_departed_authors_later_paper(monkeypatch):
    """End to end: the prior user left in 2019, so their 2025 paper gets nothing while
    their 2020 paper keeps the prior."""
    from pipeline_cores import run
    from pipeline_cores.dictionary import load_core

    core = load_core("2")
    pubs = [{"pmid": "500", "title": "t", "abstract": "", "year": 2020},
            {"pmid": "600", "title": "t", "abstract": "", "year": 2025}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda *a, **k: {})
    monkeypatch.setattr(ingest, "fetch_author_bylines",
                        lambda e, p: {"500": ["old0001"], "600": ["old0001"]})
    monkeypatch.setattr(ingest, "fetch_pub_years", lambda e, p: {str(x): 2018 for x in p})
    monkeypatch.setattr(ingest, "fetch_author_tenure", lambda e, c: {"old0001": (2010, 2019)})
    monkeypatch.setattr(ingest, "fetch_author_totals",
                        lambda e, c=None: {"old0001": {2018: 3, 2020: 1, 2025: 1}})
    prior = {"old0001": {"2": {"11", "12", "13"}}}
    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=object(), dry_run=True,
                                            prior_user_pmids=prior)}
    # in tenure: 3 dated-2018 confirms / (3 + 1 in-window 2020 paper - 500 itself + K=1)
    assert recs["500"].signals.author_affinity == pytest.approx(3 / 4)
    assert recs["600"].signals.author_affinity == 0.0
    assert recs["600"].status == STATUS_BELOW


# --- self-exclusion -------------------------------------------------------------
def test_a_paper_whose_only_confirmation_is_itself_gets_no_affinity():
    """The loop: P was confirmed once, is re-scored, and its own row was the author's
    whole numerator — so P kept itself confirmed with its own label."""
    idx = build_affinity_index({"a": {"14": 1}}, {"a": 5}, members={"a": {"14": {"P"}}})
    assert author_affinity(idx, ["a"], "14") == pytest.approx(1 / 6)       # not self-scored
    assert author_affinity(idx, ["a"], "14", pmid="P") == 0.0
    assert author_affinity(idx, ["a"], "14", pmid="Q") == pytest.approx(1 / 5)  # Q's denom


def test_a_three_of_three_author_scores_as_two_of_two_on_their_own_paper():
    idx = build_affinity_index({"a": {"14": 3}}, {"a": 3},
                               members={"a": {"14": {"P1", "P2", "P3"}}})
    assert author_affinity(idx, ["a"], "14") == pytest.approx(3 / 4)
    assert author_affinity(idx, ["a"], "14", pmid="P2") == pytest.approx(2 / 3)
    two = build_affinity_index({"b": {"14": 2}}, {"b": 2})
    assert author_affinity(idx, ["a"], "14", pmid="P2") == pytest.approx(two.rate("b", "14"))
    assert _feat(author_affinity(idx, ["a"], "14", pmid="P2")) == ["aff:regular"]


def test_self_exclusion_is_per_author_and_per_core():
    """Only the authors whose numerator holds P lose it; another core's set is untouched."""
    idx = build_affinity_index({"a": {"14": 1, "2": 2}, "b": {"14": 2}},
                               {"a": 4, "b": 4},
                               members={"a": {"14": {"P"}, "2": {"P", "R"}},
                                        "b": {"14": {"S", "T"}}}, shrink_k=0)
    assert idx.rate("a", "14", pmid="P") == 0.0
    assert idx.rate("a", "2", pmid="P") == pytest.approx(1 / 3)
    assert idx.rate("b", "14", pmid="P") == pytest.approx(2 / 3)          # denominator only
    assert author_affinity(idx, ["a", "b"], "14", pmid="P") == pytest.approx(2 / 3)


def test_out_of_corpus_paper_keeps_the_denominator():
    idx = build_affinity_index({"a": {"14": 2}}, {"a": 4}, shrink_k=0)
    assert idx.rate("a", "14", pmid="X", in_corpus=False) == 0.5
    assert idx.rate("a", "14", pmid="X") == pytest.approx(2 / 3)


def test_self_exclusion_with_years_and_tenure():
    idx = build_affinity_index({"a": {"14": {2021: 2}}}, {"a": {2021: 2, 2022: 2}},
                               tenure={"a": (2015, None)}, shrink_k=0,
                               members={"a": {"14": {"P", "Q"}}})
    assert idx.rate("a", "14", 2021, pmid="P") == pytest.approx(1 / 3)


def test_run_core_excludes_a_prior_confirmed_paper_from_its_own_prior(monkeypatch):
    """End to end: 700 was confirmed by an earlier run and is the author's only
    confirmation; re-scored, it gets nothing from it. 800, a different paper by the
    same author, still gets the prior from 700."""
    from pipeline_cores import run
    from pipeline_cores.dictionary import load_core

    core = load_core("2")
    pubs = [{"pmid": "700", "title": "t", "abstract": "", "year": 2022},
            {"pmid": "800", "title": "t", "abstract": "", "year": 2022}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda *a, **k: {})
    monkeypatch.setattr(ingest, "fetch_author_bylines",
                        lambda e, p: {"700": ["solo001"], "800": ["solo001"]})
    monkeypatch.setattr(ingest, "fetch_pub_years", lambda e, p: {str(x): 2022 for x in p})
    monkeypatch.setattr(ingest, "fetch_author_tenure", lambda e, c: {})
    monkeypatch.setattr(ingest, "fetch_author_totals",
                        lambda e, c=None: {"solo001": {2022: 2}})
    recs = {r.pmid: r for r in run.run_core(core, pubs, bedrock=None, threshold=0.30,
                                            scored_at="t", engine=object(), dry_run=True,
                                            prior_user_pmids={"solo001": {"2": {"700"}}})}
    assert recs["700"].signals.author_affinity == 0.0
    assert recs["700"].status == STATUS_BELOW
    assert recs["800"].signals.author_affinity == pytest.approx(1 / 2)    # 1 / (2 - 1 + K)
