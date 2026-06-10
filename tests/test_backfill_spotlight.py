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
