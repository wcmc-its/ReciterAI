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
