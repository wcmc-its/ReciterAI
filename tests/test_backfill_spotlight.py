"""Unit tests for pure helpers in cli/backfill_spotlight (#167).

The full `_run_pipeline` is integration-only (DDB + Bedrock), so the
publish-count behavior is tested through the extracted `_top_publishable`
helper.
"""
from types import SimpleNamespace
from unittest.mock import patch

from cli.backfill_spotlight import _top_publishable, _try_generate_lede


def _pv(subtopic_id: str, sel_score: float):
    """A (selection, validated_lede) pair as `publishable` holds it. The vlede
    is opaque to `_top_publishable`, so a sentinel is fine."""
    sel = SimpleNamespace(
        sel_score=sel_score,
        entry=SimpleNamespace(subtopic_id=subtopic_id),
    )
    return (sel, object())


def test_top_publishable_keeps_best_n_by_sel_score():
    """Publish the best PUBLISH_TARGET by selection score, not the first N that
    happened to pass the critic (#167: openers no longer distort the choice)."""
    pubs = [_pv("a", 1.0), _pv("b", 9.0), _pv("c", 5.0), _pv("d", 7.0)]
    top = _top_publishable(pubs, 2)
    assert [pv[0].entry.subtopic_id for pv in top] == ["b", "d"]


def test_top_publishable_returns_all_when_fewer_than_target():
    """A thin pool publishes however many cleared — the target is a ceiling."""
    pubs = [_pv("a", 3.0), _pv("b", 1.0)]
    assert len(_top_publishable(pubs, 9)) == 2


def test_top_publishable_deterministic_tiebreak_on_subtopic_id():
    pubs = [_pv("z", 5.0), _pv("a", 5.0)]
    top = _top_publishable(pubs, 1)
    assert top[0][0].entry.subtopic_id == "a"  # tie broken by subtopic_id ASC


def test_top_publishable_does_not_mutate_input():
    pubs = [_pv("a", 1.0), _pv("b", 9.0)]
    before = list(pubs)
    _top_publishable(pubs, 1)
    assert pubs == before


# ---------------------------------------------------------------------------
# Scholar-coverage downweight (docs/spotlight-scholar-coverage-selection.md):
# at the publish-N step, downweight a card whose lead authors already front the
# page so one lab cannot monopolize. Soft — never a hard exclusion.
# ---------------------------------------------------------------------------

def _paper(first_pid: str, last_pid: str):
    return SimpleNamespace(
        first_author=SimpleNamespace(person_identifier=first_pid),
        last_author=SimpleNamespace(person_identifier=last_pid),
    )


def _pv_authors(subtopic_id: str, sel_score: float, papers: list):
    """A publishable pair carrying lead-author papers, as scholar-coverage mode
    reads them off ``sel.entry.papers``."""
    sel = SimpleNamespace(
        sel_score=sel_score,
        entry=SimpleNamespace(subtopic_id=subtopic_id, papers=tuple(papers)),
    )
    return (sel, object())


def test_scholar_lambda_zero_is_pure_sel_score_truncation():
    """lambda=0 must reproduce the #167 sel_score truncation exactly, even with
    author payloads present — the safe-by-default / regression guard."""
    pubs = [
        _pv_authors("a", 1.0, [_paper("s", "s")]),
        _pv_authors("b", 9.0, [_paper("s", "s")]),
        _pv_authors("c", 5.0, [_paper("s", "s")]),
    ]
    top = _top_publishable(pubs, 2, scholar_penalty_lambda=0.0)
    assert [pv[0].entry.subtopic_id for pv in top] == ["b", "c"]


def test_scholar_penalty_demotes_a_monopoly_card():
    """Three cards fronting the same person 's'; a distinct-author card 'd' with
    a lower sel_score is promoted over the 3rd 's' card once the penalty bites."""
    pubs = [
        _pv_authors("a", 100.0, [_paper("s", "a1")]),
        _pv_authors("b", 98.0, [_paper("s", "b1")]),
        _pv_authors("c", 96.0, [_paper("s", "c1")]),
        _pv_authors("d", 90.0, [_paper("d1", "d2")]),
    ]
    # lambda=0: pure sel_score -> the three 's' cards win, 'd' is dropped.
    base = _top_publishable(pubs, 3, scholar_penalty_lambda=0.0)
    assert [pv[0].entry.subtopic_id for pv in base] == ["a", "b", "c"]
    # lambda>0: the 3rd 's' card ('c') is demoted past target; 'd' promoted in.
    cov = _top_publishable(pubs, 3, scholar_penalty_lambda=0.5)
    ids = [pv[0].entry.subtopic_id for pv in cov]
    assert "d" in ids and "c" not in ids
    assert ids[0] == "a"  # the lab's single strongest card is always kept


def test_scholar_penalty_is_soft_a_strong_repeat_survives():
    """A star's 2nd card is NOT forbidden — if its sel_score advantage beats the
    penalty it still publishes (soft downweight, per owner decision)."""
    pubs = [
        _pv_authors("a", 100.0, [_paper("s", "a1")]),
        _pv_authors("b", 99.0, [_paper("s", "b1")]),  # shares 's' but very strong
        _pv_authors("c", 50.0, [_paper("c1", "c2")]),  # distinct but weak
    ]
    cov = _top_publishable(pubs, 2, scholar_penalty_lambda=0.08)
    ids = [pv[0].entry.subtopic_id for pv in cov]
    assert ids == ["a", "b"]  # 's' repeats; the weak distinct card does not win


def test_scholar_penalty_tolerates_missing_papers():
    """A candidate with no author payload contributes/incurs no penalty and must
    not raise in scholar-coverage mode."""
    pubs = [_pv("a", 5.0), _pv("b", 9.0)]  # _pv has no entry.papers
    top = _top_publishable(pubs, 2, scholar_penalty_lambda=0.1)
    assert sorted(pv[0].entry.subtopic_id for pv in top) == ["a", "b"]


# ---------------------------------------------------------------------------
# _try_generate_lede — per-subtopic failure tolerance (publish resilience)
# ---------------------------------------------------------------------------

def _meta(sid="aging.molecular"):
    return SimpleNamespace(subtopic_id=sid)


def test_try_generate_lede_returns_vlede_on_success():
    sentinel = SimpleNamespace(status="pass", lede="WCM scholars are mapping things.")
    with patch("spotlight.critic.run_critic_loop", return_value=sentinel) as rc:
        out = _try_generate_lede(_meta(), [1, 2], "v2026-06-10", "parent_topic", ())
    assert out is sentinel
    rc.assert_called_once()


def test_try_generate_lede_skips_on_value_error():
    """A subtopic with <2 author-resolved papers is skipped, not fatal."""
    with patch("spotlight.critic.run_critic_loop", side_effect=ValueError("needs >=2 papers")):
        out = _try_generate_lede(_meta(), [], "v2026-06-10", "parent_topic", ())
    assert out is None


def test_try_generate_lede_skips_on_transient_bedrock_error():
    """A transient Bedrock failure (e.g. ServiceUnavailableException) on one
    subtopic must skip it, not abort the whole publish."""
    class ServiceUnavailableException(Exception):
        pass

    with patch(
        "spotlight.critic.run_critic_loop",
        side_effect=ServiceUnavailableException("Bedrock is unable to process your request"),
    ):
        out = _try_generate_lede(_meta(), [1, 2], "v2026-06-10", "parent_topic", ())
    assert out is None


# ---------------------------------------------------------------------------
# #191 brick E lede-skip: _reused_validated_lede wraps a prior lede as a PASS
# ValidatedLede whose papers_used == the CURRENT run's grounding PMIDs, so the
# assembler contract holds and lede_grounded_pmids is correct.
# ---------------------------------------------------------------------------

from cli.backfill_spotlight import _reused_validated_lede
from spotlight.lede_skip import PriorLede


def _prior_lede(lede="WCM scholars are mapping reused things across two papers.",
                grounded=("1", "2", "3")):
    return PriorLede(
        durable_id="dur_1",
        prior_slug="slug_1",
        lede=lede,
        grounded_pmids=frozenset(grounded),
        overlap_score=1.0,
    )


def test_reused_validated_lede_is_pass_with_current_pmids():
    meta = _meta("st_1")
    pr = _prior_lede()
    vlede = _reused_validated_lede(meta, "Parent Topic", pr, frozenset({"3", "1", "2"}))
    assert vlede.status == "pass"
    assert vlede.subtopic_id == "st_1"
    assert vlede.parent_topic == "Parent Topic"
    assert vlede.lede == pr.lede
    assert vlede.attempts == ()
    # papers_used MUST be the current run's grounding PMIDs (sorted), not the
    # prior's stored set object — so the assembler writes the right grounded pmids.
    assert vlede.papers_used == ("1", "2", "3")


def test_reused_lede_satisfies_assembler_build_artifact():
    """A reused PASS vlede flows through build_artifact: spotlights[0].lede == the
    prior lede and lede_grounded_pmids == the current PMIDs (assembler status!='pass'
    guard passes; subset-of-papers invariant holds)."""
    from spotlight.assembler import build_artifact
    from spotlight.sensitive_gate import SubtopicMeta
    from spotlight.types import Author, Paper, PoolEntry

    def _auth(pid, pos):
        return Author(person_identifier=pid, display_name="N", position=pos)

    def _pap(pmid):
        return Paper(
            pmid=pmid, title="t", journal="j", year=2025, impact_score=90.0,
            impact_justification="ij", synopsis="syn",
            first_author=_auth(f"fa_{pmid}", "first"),
            last_author=_auth(f"la_{pmid}", "last"),
        )

    meta = SubtopicMeta(
        subtopic_id="st_1", label="Lbl", description="d", parent_topic_label="Parent",
    )
    pr = _prior_lede(grounded=("1", "2"))
    vlede = _reused_validated_lede(meta, "Parent", pr, frozenset({"1", "2"}))
    pool = [PoolEntry(
        subtopic_id="st_1", pool_score=10.0, parent_topic="Parent",
        papers=(_pap("1"), _pap("2")),
    )]
    art = build_artifact([vlede], pool, {"st_1": meta})
    spot = art["spotlights"][0]
    assert spot["lede"] == pr.lede
    assert spot["lede_grounded_pmids"] == ["1", "2"]
    # lede_grounded_pmids must be a subset of papers[].pmid (schema invariant).
    assert set(spot["lede_grounded_pmids"]) <= {p["pmid"] for p in spot["papers"]}


def test_lede_reuse_short_circuits_generation():
    """Seam: lede_reuse_for + _reused_validated_lede produce a PASS vlede WITHOUT
    calling run_critic_loop. A subtopic with no map entry falls through to the
    generator. Tests the in-loop predicate directly (the full _run_pipeline is
    integration-only, per this file's header note)."""
    from spotlight.lede_skip import current_grounding_pmids, lede_reuse_for
    from spotlight.types import Author, Paper

    def _auth(pid, pos):
        return Author(person_identifier=pid, display_name="N", position=pos)

    def _pap(pmid, impact):
        return Paper(
            pmid=pmid, title="t", journal="j", year=2025, impact_score=impact,
            impact_justification="ij", synopsis="syn",
            first_author=_auth(f"fa_{pmid}", "first"),
            last_author=_auth(f"la_{pmid}", "last"),
        )

    papers = [_pap("1", 90.0), _pap("2", 80.0), _pap("3", 70.0)]
    skip_map = {"st_reuse": _prior_lede(grounded=("1", "2", "3"))}

    # The reuse subtopic: gate 2 matches -> a PriorLede comes back, and the wrapped
    # vlede is a PASS with the prior lede. run_critic_loop is asserted NOT called by
    # virtue of not being invoked on this branch.
    pr = lede_reuse_for(skip_map, "st_reuse", papers)
    assert pr is not None
    meta = _meta("st_reuse")
    vlede = _reused_validated_lede(
        meta, "Parent", pr, current_grounding_pmids(papers)
    )
    assert vlede.status == "pass"
    assert vlede.lede == pr.lede

    # A subtopic with no map entry -> None -> the loop would call the generator.
    assert lede_reuse_for(skip_map, "st_other", papers) is None
