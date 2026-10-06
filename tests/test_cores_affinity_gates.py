"""Repeat-user affinity: tenure gate, base-rate shrinkage (the sliding scale), time decay
and self-exclusion (numerator only).

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


# --- (c) base-rate shrinkage: rate = (n + s*p0) / (total + s) -----------------
def test_the_rate_is_shrunk_toward_the_cores_base_rate():
    idx = build_affinity_index({"a": {"14": 3}}, {"a": 10}, prior_strength=5, base_rate=0.02)
    assert idx.rate("a", "14") == pytest.approx((3 + 5 * 0.02) / (10 + 5))


def test_the_global_default_and_the_examples_it_was_chosen_with():
    """s = 5 (signals.AFFINITY_PRIOR_STRENGTH, chosen on panel B 2026-10-06). The worked
    examples at p0 = 0.02: 1/1 ~0.18, 3/3 ~0.39, 10/10 ~0.67, 1/80 ~0.013."""
    assert signals.AFFINITY_PRIOR_STRENGTH == 5.0
    idx = build_affinity_index(
        {"one": {"14": 1}, "three": {"14": 3}, "ten": {"14": 10}, "pass": {"14": 1}},
        {"one": 1, "three": 3, "ten": 10, "pass": 80}, base_rate=0.02)
    assert idx.rate("one", "14") == pytest.approx(1.1 / 6)       # 0.183
    assert idx.rate("three", "14") == pytest.approx(3.1 / 8)     # 0.388
    assert idx.rate("ten", "14") == pytest.approx(10.1 / 15)     # 0.673
    assert idx.rate("pass", "14") == pytest.approx(1.1 / 85)     # 0.013


def test_a_short_track_record_no_longer_reaches_aff_core():
    """1-of-1 was rate 1.0 = aff:core, which cleared the confirm bar alone. (In
    production that one paper was usually the paper being scored — the self-confirmation
    loop, closed by self-exclusion below; shrinkage covers the small denominators left.)"""
    idx = build_affinity_index({"one": {"14": 1}, "two": {"14": 2}, "three": {"14": 3}},
                               {"one": 1, "two": 2, "three": 3})
    for who in ("one", "two", "three"):
        assert _feat(idx.rate(who, "14")) == ["aff:regular"]
    rec = combine("p", "14", SignalResult(author_affinity=author_affinity(idx, ["one"], "14")))
    assert rec.status != STATUS_CONFIRMED


def test_sliding_more_confirms_at_the_same_share_always_score_higher():
    """The sliding scale: no count at which an author starts or stops counting. k-of-k
    and k-of-2k both rise strictly with k, at every s > 0 and any base rate below the
    share itself."""
    for s in (1, 5, 20):
        for p0 in (0.0, 0.001, 0.02):
            for share in (1.0, 0.5):
                rates = []
                for k in range(1, 30):
                    total = int(k / share)
                    idx = build_affinity_index({"a": {"14": k}}, {"a": total},
                                               prior_strength=s, base_rate=p0)
                    rates.append(idx.rate("a", "14"))
                assert all(lo < hi for lo, hi in zip(rates, rates[1:])), (s, p0, share)
                assert rates[0] > 0.0                           # one confirmation counts


def test_two_confirmations_count_for_less_than_three_not_for_nothing():
    """What replaced the binary minimum (core 14 had 3): 2 confirms are partial
    evidence, between 1 and 3."""
    idx = build_affinity_index({"one": {"14": 1}, "two": {"14": 2}, "three": {"14": 3}},
                               {"one": 4, "two": 4, "three": 4}, base_rate=0.001)
    assert 0.0 < idx.rate("one", "14") < idx.rate("two", "14") < idx.rate("three", "14")


def test_one_of_two_beats_one_of_eighty():
    idx = build_affinity_index({"close": {"14": 1}, "passer": {"14": 1}},
                               {"close": 2, "passer": 80}, base_rate=0.001)
    assert idx.rate("close", "14") > idx.rate("passer", "14") > 0.0
    assert _feat(idx.rate("passer", "14")) == ["aff:trace"]


def test_shrinkage_barely_moves_a_large_denominator():
    idx = build_affinity_index({"big": {"2": 40}}, {"big": 50})
    assert idx.rate("big", "2") == pytest.approx(40 / 55)
    assert _feat(idx.rate("big", "2")) == ["aff:core"]


def test_prior_strength_zero_is_the_plain_rate():
    idx = build_affinity_index({"one": {"2": 1}}, {"one": 1}, prior_strength=0, base_rate=0.3)
    assert idx.rate("one", "2") == 1.0


def test_strength_and_base_rate_are_per_core():
    idx = build_affinity_index({"a": {"14": 1, "2": 1}}, {"a": 4},
                               prior_strength={"14": 10}, base_rate={"2": 0.5})
    assert idx.rate("a", "14") == pytest.approx(1 / 14)          # s=10, p0 fallback 0
    assert idx.rate("a", "2") == pytest.approx((1 + 5 * 0.5) / 9)  # global s=5, p0=0.5


def test_affinity_base_rate():
    assert signals.affinity_base_rate(79, 82203) == pytest.approx(79 / 82203)
    assert signals.AFFINITY_BASE_RATE_FALLBACK == 0.0
    assert signals.affinity_base_rate(79, None) == 0.0           # corpus size unknown
    assert signals.affinity_base_rate(79, 0) == 0.0


def test_core_14_takes_the_global_prior_strength():
    from pipeline_cores.dictionary import load_core
    # core 14 takes the global s (46 decided rows cannot justify an override)
    assert load_core("14").affinity_prior_strength is None
    assert signals.affinity_prior_strength(load_core("14")) == signals.AFFINITY_PRIOR_STRENGTH
    assert signals.affinity_prior_strength(None) == signals.AFFINITY_PRIOR_STRENGTH


def test_dictionary_reads_and_validates_prior_strength(tmp_path):
    from pipeline_cores.dictionary import load_cores
    y = tmp_path / "d.yaml"
    y.write_text('cores:\n  - {core_id: "9", name: X, aliases: [X], affinity_prior_strength: -1}\n')
    with pytest.raises(ValueError, match="affinity_prior_strength"):
        load_cores(y)
    y.write_text('cores:\n  - {core_id: "9", name: X, aliases: [X], affinity_prior_strength: 0}\n')
    assert load_cores(y)[0].affinity_prior_strength == 0.0      # 0 = no shrinkage, allowed
    y.write_text('cores:\n  - {core_id: "9", name: X, aliases: [X], affinity_prior_strength: 12}\n')
    core = load_cores(y)[0]
    assert core.affinity_prior_strength == 12.0
    assert signals.affinity_prior_strength(core) == 12.0


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
                               tenure={"left": (2015, 2021)}, prior_strength=0)
    assert author_affinity(idx, ["left"], "14", 2023) == 1.0
    assert author_affinity(idx, ["left"], "14", 2024) == 0.0
    assert author_affinity(idx, ["left"], "14", 2012) == 1.0   # start - 3
    assert author_affinity(idx, ["left"], "14", 2011) == 0.0
    assert author_affinity(idx, ["left"], "14", None) == 1.0   # undated paper: not gated


def test_the_gate_is_per_author_and_never_drops_the_paper():
    """A co-author still in tenure keeps their affinity on the same paper."""
    idx = build_affinity_index({"left": {"14": {2020: 3}}, "here": {"14": {2024: 1}}},
                               {"left": {2020: 3}, "here": {2024: 10}},
                               tenure={"left": (2010, 2020), "here": (2019, None)}, prior_strength=0)
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
                               tenure={"x": (2010, 2020)}, prior_strength=0)
    assert idx.rate("x", "14", 2019) == 0.25                   # 1 of 4, not (1+3)/(4+10)
    gone = build_affinity_index({"x": {"14": {2025: 3}}}, {"x": {2025: 10}},
                                tenure={"x": (2010, 2020)})
    assert "x" not in gone.papers                              # nothing left in tenure


def test_an_author_with_no_tenure_row_is_not_gated():
    idx = build_affinity_index({"x": {"14": {2025: 1}}}, {"x": {2025: 4}}, tenure={},
                               prior_strength=0)
    assert idx.rate("x", "14", 2025) == 0.25


# --- (b) time decay ---------------------------------------------------------
def test_decay_is_off_by_default():
    assert signals.AFFINITY_HALF_LIFE_YEARS is None
    idx = build_affinity_index({"x": {"2": {2020: 2}}}, {"x": {2020: 2, 2024: 2}}, prior_strength=0)
    assert idx.rate("x", "2", 2024) == 0.5


def test_decay_weights_numerator_and_denominator_by_distance_from_the_scored_year():
    idx = build_affinity_index({"x": {"2": {2020: 2}}}, {"x": {2020: 2, 2024: 2}},
                               prior_strength=0, half_life=2)
    # 2020 is two half-lives from 2024: num 2 * 0.25 = 0.5, den 0.5 + 2 = 2.5
    assert idx.rate("x", "2", 2024) == pytest.approx(0.2)
    # ...and recent history counts in full from its own year
    assert idx.rate("x", "2", 2020) == pytest.approx(2 / (2 + 2 * 0.25))
    assert idx.rate("x", "2", None) == 0.5                     # no reference year: undecayed


# --- plain-count callers keep working ---------------------------------------
def test_year_unknown_counts_are_neither_gated_nor_decayed():
    idx = build_affinity_index({"x": {"2": 1}}, {"x": 4}, tenure={"x": (2010, 2012)},
                               prior_strength=0, half_life=1)
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
    idx = build_affinity_index(counts, totals, tenure=tenure, prior_strength=0)
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
    # in tenure: 3 dated-2018 confirms / (3 + 1 in-window 2020 paper + s=5), p0 the
    # fallback 0 (no corpus_size); 500 is not one of the confirms, and self-exclusion
    # never touches the denominator
    assert recs["500"].signals.author_affinity == pytest.approx(3 / 9)
    assert recs["600"].signals.author_affinity == 0.0
    assert recs["600"].status == STATUS_BELOW


# --- self-exclusion (numerator only) ----------------------------------------------
def test_a_paper_whose_only_confirmation_is_itself_gets_no_affinity():
    """The loop: P was confirmed once, is re-scored, and its own row was the author's
    whole numerator — so P kept itself confirmed with its own label."""
    idx = build_affinity_index({"a": {"14": 1}}, {"a": 5}, members={"a": {"14": {"P"}}},
                               base_rate=0.02)
    assert author_affinity(idx, ["a"], "14") == pytest.approx(1.1 / 10)    # not self-scored
    # n = 0 after the exclusion: still 0, NOT the base rate s*p0 / (total + s) — the
    # prior shrinks evidence, it does not hand an author with none of it a free rate
    assert author_affinity(idx, ["a"], "14", pmid="P") == 0.0
    assert author_affinity(idx, ["a"], "14", pmid="Q") == pytest.approx(1.1 / 10)  # Q not a member


def test_a_three_of_three_author_scores_two_of_three_on_their_own_paper():
    """Numerator drops the scored paper; the denominator keeps it ((2 + s*p0) / (3 + s))."""
    idx = build_affinity_index({"a": {"14": 3}}, {"a": 3},
                               members={"a": {"14": {"P1", "P2", "P3"}}}, base_rate=0.02)
    assert author_affinity(idx, ["a"], "14") == pytest.approx(3.1 / 8)
    assert author_affinity(idx, ["a"], "14", pmid="P2") == pytest.approx(2.1 / 8)
    assert _feat(author_affinity(idx, ["a"], "14", pmid="P2")) == ["aff:regular"]


def test_self_exclusion_never_touches_the_denominator():
    """Measured on core 14's human-decided rows (2026-10-06): numerator-only AUC 0.6212,
    numerator + denominator 0.6115. The scored paper is part of its authors' output;
    only its LABEL was the leak."""
    idx = build_affinity_index({"a": {"14": 2}}, {"a": 4}, prior_strength=0,
                               members={"a": {"14": {"P", "Q"}}})
    assert idx.rate("a", "14", pmid="X") == 0.5                # a non-member: 2/4, not 2/3
    assert idx.rate("a", "14", pmid="P") == 0.25               # a member: (2-1)/4
    assert "in_corpus" not in signals.AffinityIndex.rate.__code__.co_varnames


def test_self_exclusion_is_per_author_and_per_core():
    """Only the authors whose numerator holds P lose it; another core's set is untouched."""
    idx = build_affinity_index({"a": {"14": 1, "2": 2}, "b": {"14": 2}},
                               {"a": 4, "b": 4},
                               members={"a": {"14": {"P"}, "2": {"P", "R"}},
                                        "b": {"14": {"S", "T"}}}, prior_strength=0)
    assert idx.rate("a", "14", pmid="P") == 0.0
    assert idx.rate("a", "2", pmid="P") == pytest.approx(1 / 4)
    assert idx.rate("b", "14", pmid="P") == pytest.approx(2 / 4)          # untouched
    assert author_affinity(idx, ["a", "b"], "14", pmid="P") == pytest.approx(2 / 4)


def test_self_exclusion_with_years_and_tenure():
    idx = build_affinity_index({"a": {"14": {2021: 2}}}, {"a": {2021: 2, 2022: 2}},
                               tenure={"a": (2015, None)}, prior_strength=0,
                               members={"a": {"14": {"P", "Q"}}})
    assert idx.rate("a", "14", 2021, pmid="P") == pytest.approx(1 / 4)


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
    assert recs["800"].signals.author_affinity == pytest.approx(1 / 7)    # 1 / (2 + s=5)


# --- run_core: the core's base rate and prior strength ---------------------------
def test_run_core_computes_the_base_rate_from_corpus_size(monkeypatch, capsys):
    """p0 = the core's confirmed/claimed papers (prior + this run, staff's included:
    p0 describes the core) / corpus_size; s = the core's own or the global."""
    import dataclasses

    from pipeline_cores import run
    from pipeline_cores.dictionary import load_core

    pubs = [{"pmid": "900", "title": "t", "abstract": "", "year": 2022}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda *a, **k: {})
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {"900": ["cli0001"]})
    monkeypatch.setattr(ingest, "fetch_pub_years", lambda e, p: {str(x): 2022 for x in p})
    monkeypatch.setattr(ingest, "fetch_author_tenure", lambda e, c: {})
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {"cli0001": {2022: 9}})
    prior = {"cli0001": {"2": {"1", "2"}}, "someone": {"2": {"3", "4"}}}

    def score(core, corpus_size):
        (rec,) = run.run_core(core, pubs, bedrock=None, threshold=0.30, scored_at="t",
                              engine=object(), dry_run=True, prior_user_pmids=prior,
                              corpus_size=corpus_size)
        return rec.signals.author_affinity

    core = load_core("2")
    assert score(core, 100) == pytest.approx((2 + 5 * 4 / 100) / (9 + 5))   # p0 = 4/100
    assert "p0=0.04000 (4 core papers / 100 corpus)" in capsys.readouterr().out
    assert score(core, None) == pytest.approx(2 / 14)                       # fallback p0 = 0
    strong = dataclasses.replace(core, affinity_prior_strength=20.0)
    assert score(strong, 100) == pytest.approx((2 + 20 * 0.04) / (9 + 20))


def test_fetch_corpus_size_counts_the_corpus_query(monkeypatch):
    """Counts PUBLICATION_EXTRACTION_SQL itself (ORDER BY stripped), like the other
    corpus-gated readers, so p0 and every author's total share one universe."""
    sqlalchemy = pytest.importorskip("sqlalchemy")
    import utils.sql_queries as q
    eng = sqlalchemy.create_engine("sqlite://")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE t (pmid INT)")
        c.exec_driver_sql("INSERT INTO t VALUES (1), (2), (2), (3)")
    monkeypatch.setattr(q, "PUBLICATION_EXTRACTION_SQL",
                        "SELECT a1.pmid AS pmid FROM t a1 ORDER BY a1.pmid DESC")
    assert ingest.fetch_corpus_size(eng) == 3


# --- the fit: an EMPTY top bucket is priced as the bucket below --------------------
def _fit_script():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "fit_evidence_weights.py"
    spec = importlib.util.spec_from_file_location("fit_evidence_weights_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_empty_aff_core_cell_is_pooled_with_aff_regular():
    """At s = 5 no panel-B rate reaches 0.70: fit() refuses the 0/0 cell at 0.00, which
    would price the strongest rates BELOW aff:regular. Pooled, it is regular's number."""
    fit = _fit_script()
    table = {"aff:trace": (1.18, ""), "aff:regular": (3.49, ""),
             "aff:core": (0.0, "REFUSED: never observed on either side")}
    out = fit.pool_empty_affinity(dict(table))
    assert out["aff:core"][0] == 3.49 and out["aff:core"][1].startswith("POOLED")
    assert out["aff:trace"] == table["aff:trace"] and out["aff:regular"] == table["aff:regular"]


def test_a_populated_cell_is_never_pooled():
    fit = _fit_script()
    table = {"aff:trace": (1.22, ""), "aff:regular": (3.42, ""), "aff:core": (3.01, "")}
    assert fit.pool_empty_affinity(dict(table)) == table      # non-monotone stays visible


def test_shipped_aff_weights_are_monotone():
    from pipeline_cores.combine import WEIGHTS
    assert WEIGHTS["aff:trace"] < WEIGHTS["aff:regular"] <= WEIGHTS["aff:core"]


# --- per-core repeat-user MINIMUM (core 14 = 3; global default 1) ------------------
def test_the_global_default_minimum_is_one_and_changes_nothing():
    from pipeline_cores.dictionary import load_core
    assert signals.AFFINITY_MIN_CONFIRMS == 1
    assert signals.affinity_min_confirms(None) == 1
    assert load_core("2").affinity_min_confirms is None          # no key: global default
    assert signals.affinity_min_confirms(load_core("2")) == 1
    plain = build_affinity_index({"a": {"2": 1}}, {"a": 4})
    gated = build_affinity_index({"a": {"2": 1}}, {"a": 4}, min_confirms={"2": 1})
    assert plain.rate("a", "2") == gated.rate("a", "2") == pytest.approx(1 / 9)  # s=5, p0 0


def test_core_14_requires_three_confirmations():
    """The soft threshold c=3, h=2 failed its pre-registered bar on core 14 (bucket AUC
    0.5981 < 0.66, 2026-10-06), so the decision rule restored the hard minimum of 3 as a
    core-14 setting (core_dictionary.yaml carries the measurement)."""
    from pipeline_cores.dictionary import load_core
    core = load_core("14")
    assert core.affinity_min_confirms == 3
    assert signals.affinity_min_confirms(core) == 3


def test_below_the_minimum_an_author_lends_nothing():
    idx = build_affinity_index({"two": {"14": 2}, "three": {"14": 3}},
                               {"two": 4, "three": 4}, prior_strength=0, min_confirms={"14": 3})
    assert idx.rate("two", "14") == 0.0                        # 2 < 3: not a repeat user
    assert idx.rate("three", "14") == pytest.approx(3 / 4)     # rate itself unchanged


def test_the_minimum_counts_after_self_exclusion():
    """'>= 3 confirmed publications, self excluded': an author with exactly 3, scoring
    one of them, has 2 OTHER confirmations and lends that paper nothing — while still
    lending a paper that is not one of the three."""
    idx = build_affinity_index({"a": {"14": 3}}, {"a": 10}, prior_strength=0, min_confirms={"14": 3},
                               members={"a": {"14": {"P1", "P2", "P3"}}})
    assert idx.rate("a", "14", pmid="P1") == 0.0
    assert idx.rate("a", "14", pmid="X") == pytest.approx(3 / 10)
    four = build_affinity_index({"a": {"14": 4}}, {"a": 10}, prior_strength=0, min_confirms={"14": 3},
                                members={"a": {"14": {"P1", "P2", "P3", "P4"}}})
    assert four.rate("a", "14", pmid="P1") == pytest.approx(3 / 10)


def test_the_minimum_is_per_core():
    idx = build_affinity_index({"a": {"14": 1, "2": 1}}, {"a": 4}, prior_strength=0,
                               min_confirms={"14": 3})
    assert idx.rate("a", "14") == 0.0
    assert idx.rate("a", "2") == 0.25                          # core 2: the global 1
    every = build_affinity_index({"a": {"14": 2, "2": 2}}, {"a": 4}, min_confirms=3)
    assert every.rate("a", "14") == every.rate("a", "2") == 0.0  # an int applies to all


def test_the_minimum_counts_in_tenure_and_undecayed():
    """Out-of-tenure confirmations are already gone from the count; decay never shrinks
    a confirmation below 'one confirmation'."""
    idx = build_affinity_index({"x": {"14": {2010: 2, 2022: 2}}}, {"x": {2010: 2, 2022: 8}},
                               tenure={"x": (2018, None)}, prior_strength=0, min_confirms={"14": 3})
    assert idx.rate("x", "14", 2023) == 0.0                    # only 2 in tenure
    old = build_affinity_index({"x": {"14": {2015: 3}}}, {"x": {2015: 3, 2025: 3}},
                               prior_strength=0, half_life=1, min_confirms={"14": 3})
    assert old.rate("x", "14", 2025) > 0.0                     # 3 decayed confirms are 3


@pytest.mark.parametrize("bad", ["0", "-2", "2.5", "true", '"3"'])
def test_dictionary_rejects_a_bad_minimum(tmp_path, bad):
    from pipeline_cores.dictionary import load_cores
    y = tmp_path / "d.yaml"
    y.write_text('cores:\n  - {core_id: "9", name: X, aliases: [X], affinity_min_confirms: '
                 + bad + '}\n')
    with pytest.raises(ValueError, match="affinity_min_confirms"):
        load_cores(y)


def test_dictionary_reads_a_minimum(tmp_path):
    from pipeline_cores.dictionary import load_cores
    y = tmp_path / "d.yaml"
    y.write_text('cores:\n  - {core_id: "9", name: X, aliases: [X], affinity_min_confirms: 2}\n')
    assert load_cores(y)[0].affinity_min_confirms == 2


def test_run_core_applies_the_cores_minimum(monkeypatch):
    """Core 14 (minimum 3): an author with 2 prior confirmations lends nothing, one with 3
    does. The same history on core 2 (global default 1) lends in both cases."""
    from pipeline_cores import run
    from pipeline_cores.dictionary import load_core

    pubs = [{"pmid": "900", "title": "t", "abstract": "", "year": 2022}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda *a, **k: {})
    monkeypatch.setattr(ingest, "fetch_pub_years", lambda e, p: {str(x): 2022 for x in p})
    monkeypatch.setattr(ingest, "fetch_author_tenure", lambda e, c: {})
    monkeypatch.setattr(ingest, "fetch_author_totals",
                        lambda e, c=None: {"two0001": {2022: 9}, "thr0001": {2022: 9}})

    def score(core_id, cwid, n):
        core = load_core(core_id)
        monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {"900": [cwid]})
        prior = {cwid: {core_id: {str(i) for i in range(1, n + 1)}}}
        (rec,) = run.run_core(core, pubs, bedrock=None, threshold=0.30, scored_at="t",
                              engine=object(), dry_run=True, prior_user_pmids=prior)
        return rec.signals.author_affinity

    assert score("14", "two0001", 2) == 0.0
    assert score("14", "thr0001", 3) == pytest.approx(3 / 14)        # s=5, p0 fallback 0
    assert score("2", "two0001", 2) == pytest.approx(2 / 14)


def test_batch_screen_applies_the_cores_minimum(monkeypatch):
    """batch_screen's 'does the prior fire' set honours the minimum too."""
    from pipeline_cores import batch_screen, persist
    from pipeline_cores.dictionary import load_core

    monkeypatch.setattr(persist, "scan_prior_core_usage", lambda core_id: [
        {"pmid": p, "core_id": core_id} for p in ("1", "2", "3", "4", "5")])
    monkeypatch.setattr(ingest, "filter_corpus_pmids", lambda e, p: set(p))
    monkeypatch.setattr(ingest, "fetch_author_totals",
                        lambda e, c=None: {"two0001": 10, "thr0001": 10})
    bylines = {"1": ["two0001"], "2": ["two0001"],
               "3": ["thr0001"], "4": ["thr0001"], "5": ["thr0001"],
               "A": ["two0001"], "B": ["thr0001"]}
    _confirmed, author = batch_screen._prior_signals(load_core("14"), ["A", "B"], bylines, None)
    assert author == {"B"}
    _confirmed, author = batch_screen._prior_signals(load_core("2"), ["A", "B"], bylines, None)
    assert author == {"A", "B"}     # the same history on core 2: global minimum 1


# --- soft threshold g(n) = n^h / (n^h + c^h) (measured; global default OFF) ----------
def test_soft_threshold_is_off_globally_and_on_no_core():
    from pipeline_cores.dictionary import load_cores
    assert signals.AFFINITY_SOFT_THRESHOLD is None
    assert signals.soft_threshold_gate(1, None) == 1.0
    assert all(signals.affinity_soft_threshold(c) is None for c in load_cores())
    plain = build_affinity_index({"a": {"2": 1}}, {"a": 4})
    off = build_affinity_index({"a": {"2": 1}}, {"a": 4}, soft_threshold={"2": None})
    assert plain.rate("a", "2") == off.rate("a", "2") == pytest.approx(1 / 9)


def test_soft_threshold_values_at_the_pre_registered_c3_h2():
    g = [signals.soft_threshold_gate(n, (3, 2)) for n in (1, 2, 3, 5, 10)]
    assert g == pytest.approx([0.1, 4 / 13, 0.5, 25 / 34, 100 / 109])  # .10 .31 .50 .74 .92
    assert signals.soft_threshold_gate(0, (3, 2)) == 0.0
    assert signals.soft_threshold_gate(-1, (3, 2)) == 0.0


@pytest.mark.parametrize("c", [2, 3, 4])
@pytest.mark.parametrize("h", [1, 2, 4, 8, 64])
def test_soft_threshold_is_monotone_and_bounded(c, h):
    g = [signals.soft_threshold_gate(n, (c, h)) for n in range(0, 40)]
    assert g[0] == 0.0
    assert all(0.0 < x < 1.0 or x == pytest.approx(1.0) for x in g[1:])
    assert all(lo <= hi for lo, hi in zip(g, g[1:]))
    assert signals.soft_threshold_gate(c, (c, h)) == pytest.approx(0.5)


def test_large_h_approaches_a_hard_minimum():
    assert signals.soft_threshold_gate(2, (3, 64)) < 1e-10
    assert signals.soft_threshold_gate(4, (3, 64)) > 1 - 1e-7


def test_soft_threshold_multiplies_the_shrunk_rate():
    idx = build_affinity_index({"a": {"14": 2}}, {"a": 4}, prior_strength=5, base_rate=0.01,
                               soft_threshold={"14": (3, 2)})
    assert idx.rate("a", "14") == pytest.approx((2 + 0.05) / 9 * 4 / 13)


def test_soft_threshold_counts_after_self_exclusion():
    """n_excl leaves the scored paper out: 3 confirmations scoring one of them is g(2)."""
    idx = build_affinity_index({"a": {"14": 3}}, {"a": 10}, prior_strength=0,
                               members={"a": {"14": {"P1", "P2", "P3"}}},
                               soft_threshold={"14": (3, 2)})
    assert idx.rate("a", "14", pmid="X") == pytest.approx(0.3 * 0.5)       # g(3)
    assert idx.rate("a", "14", pmid="P1") == pytest.approx(0.2 * 4 / 13)   # g(2)
    one = build_affinity_index({"a": {"14": 1}}, {"a": 2}, members={"a": {"14": {"P"}}},
                               soft_threshold=(3, 2))
    assert one.rate("a", "14", pmid="P") == 0.0                            # n_excl = 0


def test_soft_threshold_counts_in_tenure_and_undecayed():
    idx = build_affinity_index({"x": {"14": {2010: 5, 2022: 1}}}, {"x": {2010: 5, 2022: 4}},
                               tenure={"x": (2018, None)}, prior_strength=0,
                               soft_threshold={"14": (3, 2)})
    assert idx.rate("x", "14", 2023) == pytest.approx(0.25 * 0.1)         # g(1), not g(6)
    dec = build_affinity_index({"x": {"14": {2015: 3}}}, {"x": {2015: 3, 2025: 3}},
                               prior_strength=0, half_life=1, soft_threshold={"14": (3, 2)})
    undecayed_gate = build_affinity_index({"x": {"14": {2015: 3}}}, {"x": {2015: 3, 2025: 3}},
                                          prior_strength=0, half_life=1, soft_threshold=None)
    # 3 confirms decayed ~1000x are still n = 3: decayed rate x g(3), not g(0.003)
    assert dec.rate("x", "14", 2025) == pytest.approx(undecayed_gate.rate("x", "14", 2025) * 0.5)


def test_soft_threshold_one_of_two_still_beats_one_of_eighty():
    idx = build_affinity_index({"close": {"14": 1}, "passer": {"14": 1}},
                               {"close": 2, "passer": 80}, base_rate=0.001,
                               soft_threshold=(3, 2))
    assert idx.rate("close", "14") > idx.rate("passer", "14") > 0.0


def test_soft_threshold_more_confirms_at_the_same_share_score_higher():
    for c, h in ((2, 1), (3, 2), (4, 8)):
        for share in (1.0, 0.5):
            rates = []
            for k in range(1, 30):
                idx = build_affinity_index({"a": {"14": k}}, {"a": int(k / share)},
                                           base_rate=0.001, soft_threshold=(c, h))
                rates.append(idx.rate("a", "14"))
            assert rates[0] > 0.0
            assert all(lo < hi for lo, hi in zip(rates, rates[1:])), (c, h, share)


def test_soft_threshold_is_per_core():
    idx = build_affinity_index({"a": {"14": 1, "2": 1}}, {"a": 4}, prior_strength=0,
                               soft_threshold={"14": (3, 2)})
    assert idx.rate("a", "14") == pytest.approx(0.25 * 0.1)
    assert idx.rate("a", "2") == 0.25                                      # global: off


def test_dictionary_reads_a_soft_threshold(tmp_path):
    from pipeline_cores.dictionary import load_cores
    y = tmp_path / "d.yaml"
    y.write_text('cores:\n  - {core_id: "9", name: X, aliases: [X], '
                 'affinity_soft_threshold: {c: 3, h: 2}}\n')
    core = load_cores(y)[0]
    assert core.affinity_soft_threshold == (3.0, 2.0)
    assert signals.affinity_soft_threshold(core) == (3.0, 2.0)
    y.write_text('cores:\n  - {core_id: "9", name: X, aliases: [X], '
                 'affinity_soft_threshold: false}\n')
    core = load_cores(y)[0]
    assert core.affinity_soft_threshold is False
    assert signals.affinity_soft_threshold(core) is None                    # explicitly off
    y.write_text('cores:\n  - {core_id: "9", name: X, aliases: [X]}\n')
    assert load_cores(y)[0].affinity_soft_threshold is None                 # absent: global


@pytest.mark.parametrize("bad", ["{c: 3}", "{h: 2}", "{c: 3, h: 2, k: 1}", "{c: 0, h: 2}",
                                 "{c: 3, h: -1}", "{c: 3, h: .inf}", "{c: true, h: 2}",
                                 '{c: "3", h: 2}', "3", "[3, 2]", "true"])
def test_dictionary_rejects_a_bad_soft_threshold(tmp_path, bad):
    from pipeline_cores.dictionary import load_cores
    y = tmp_path / "d.yaml"
    y.write_text('cores:\n  - {core_id: "9", name: X, aliases: [X], affinity_soft_threshold: '
                 + bad + '}\n')
    with pytest.raises(ValueError, match="affinity_soft_threshold"):
        load_cores(y)


def test_run_core_applies_a_cores_soft_threshold(monkeypatch, capsys):
    import dataclasses

    from pipeline_cores import run
    from pipeline_cores.dictionary import load_core

    pubs = [{"pmid": "900", "title": "t", "abstract": "", "year": 2022}]
    monkeypatch.setattr(signals, "coauthorship_index", lambda *a, **k: {})
    monkeypatch.setattr(ingest, "fetch_author_bylines", lambda e, p: {"900": ["cli0001"]})
    monkeypatch.setattr(ingest, "fetch_pub_years", lambda e, p: {str(x): 2022 for x in p})
    monkeypatch.setattr(ingest, "fetch_author_tenure", lambda e, c: {})
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {"cli0001": {2022: 9}})
    prior = {"cli0001": {"2": {"1", "2"}}}
    core = dataclasses.replace(load_core("2"), affinity_soft_threshold=(3.0, 2.0))
    (rec,) = run.run_core(core, pubs, bedrock=None, threshold=0.30, scored_at="t",
                          engine=object(), dry_run=True, prior_user_pmids=prior)
    assert rec.signals.author_affinity == pytest.approx(2 / 14 * 4 / 13)
    assert "min confirms 1, soft threshold c=3 h=2" in capsys.readouterr().out


def test_batch_screen_soft_threshold_never_changes_whether_the_prior_fires(monkeypatch):
    import dataclasses

    from pipeline_cores import batch_screen, persist
    from pipeline_cores.dictionary import load_core

    monkeypatch.setattr(persist, "scan_prior_core_usage", lambda core_id: [
        {"pmid": "1", "core_id": core_id}])
    monkeypatch.setattr(ingest, "filter_corpus_pmids", lambda e, p: set(p))
    monkeypatch.setattr(ingest, "fetch_author_totals", lambda e, c=None: {"one0001": 50})
    bylines = {"1": ["one0001"], "A": ["one0001"]}
    core = dataclasses.replace(load_core("2"), affinity_soft_threshold=(4.0, 8.0))
    _confirmed, author = batch_screen._prior_signals(core, ["A"], bylines, None)
    assert author == {"A"}
