"""Unit tests for the hot-path stage-skip evaluation (#121).

`pipeline_hot/handlers/stage_skip.py` classifies a stage-skip event WARN
(this run skipped a stage) or ERROR (a streak of work-present runs all
skipped). `classify_stage_skip` is pure; `resolve` wraps it with the
DynamoDB history read.
"""

from __future__ import annotations

from decimal import Decimal

import pipeline_hot.handlers.stage_skip as ss

_SKIP_A = "skipped:no-assign-topics"
_SKIP_R = "skipped:no-dirty-cwids"


def _run(*, delta=0, retry=0, assign="real-hash", rollup="real-hash", run_id="R"):
    """A `STAGE#hot_run#GLOBAL`-shaped row. Pass a `skipped:` value for
    `assign` / `rollup` to mark that stage skipped."""
    return {
        "run_id": run_id,
        "delta_size": delta,
        "retry_size": retry,
        "assign_input_hash": assign,
        "rollup_input_hash": rollup,
    }


# --- classify_stage_skip (pure) -------------------------------------------


def test_lone_skip_no_history_is_warn():
    res = ss.classify_stage_skip(current=_run(delta=5, assign=_SKIP_A), prior_runs=[])
    assert res["severity"] == "WARN"
    assert res["skipped_stages"] == ["assign"]
    assert res["assign_skip_streak"] == 1
    assert res["rollup_skip_streak"] == 0


def test_k_consecutive_assign_skips_is_error():
    prior = [_run(delta=3, assign=_SKIP_A), _run(delta=3, assign=_SKIP_A)]
    res = ss.classify_stage_skip(current=_run(delta=5, assign=_SKIP_A), prior_runs=prior)
    assert res["assign_skip_streak"] == 3
    assert res["severity"] == "ERROR"


def test_k_minus_one_streak_is_warn():
    prior = [_run(delta=3, assign=_SKIP_A)]
    res = ss.classify_stage_skip(current=_run(delta=5, assign=_SKIP_A), prior_runs=prior)
    assert res["assign_skip_streak"] == 2
    assert res["severity"] == "WARN"


def test_work_empty_runs_are_transparent_to_the_streak():
    """Work-empty runs skip a stage legitimately — they neither count
    toward the streak nor break it."""
    prior = [
        _run(delta=0, retry=0, assign=_SKIP_A),  # work-empty — transparent
        _run(delta=0, retry=0, assign=_SKIP_A),  # work-empty — transparent
        _run(delta=4, assign=_SKIP_A),           # work-present skip — counts
        _run(delta=4, assign=_SKIP_A),           # work-present skip — counts
    ]
    res = ss.classify_stage_skip(current=_run(delta=5, assign=_SKIP_A), prior_runs=prior)
    assert res["assign_skip_streak"] == 3  # current + 2 work-present
    assert res["severity"] == "ERROR"


def test_a_work_present_run_that_ran_the_stage_breaks_the_streak():
    prior = [
        _run(delta=4, assign=_SKIP_A),      # skip — streak 2
        _run(delta=4, assign="real-hash"),  # work-present, ran assign — breaks
        _run(delta=4, assign=_SKIP_A),      # beyond the break — ignored
    ]
    res = ss.classify_stage_skip(current=_run(delta=5, assign=_SKIP_A), prior_runs=prior)
    assert res["assign_skip_streak"] == 2
    assert res["severity"] == "WARN"


def test_retry_only_run_counts_as_work_present():
    prior = [_run(delta=0, retry=2, assign=_SKIP_A),
             _run(delta=0, retry=2, assign=_SKIP_A)]
    res = ss.classify_stage_skip(
        current=_run(delta=0, retry=2, assign=_SKIP_A), prior_runs=prior
    )
    assert res["assign_skip_streak"] == 3
    assert res["severity"] == "ERROR"


def test_rollup_streak_is_independent_of_assign():
    prior = [_run(delta=3, rollup=_SKIP_R), _run(delta=3, rollup=_SKIP_R)]
    res = ss.classify_stage_skip(current=_run(delta=5, rollup=_SKIP_R), prior_runs=prior)
    assert res["skipped_stages"] == ["rollup"]
    assert res["rollup_skip_streak"] == 3
    assert res["assign_skip_streak"] == 0
    assert res["severity"] == "ERROR"


def test_both_stages_skipped_are_reported():
    res = ss.classify_stage_skip(
        current=_run(delta=5, assign=_SKIP_A, rollup=_SKIP_R), prior_runs=[]
    )
    assert res["skipped_stages"] == ["assign", "rollup"]


def test_decimal_sizes_from_dynamodb_are_coerced():
    prior = [_run(delta=Decimal("2"), assign=_SKIP_A),
             _run(delta=Decimal("2"), assign=_SKIP_A)]
    res = ss.classify_stage_skip(
        current=_run(delta=Decimal("5"), assign=_SKIP_A), prior_runs=prior
    )
    assert res["assign_skip_streak"] == 3
    assert res["severity"] == "ERROR"


# --- resolve (I/O wrapper) ------------------------------------------------


def _event(hot_run):
    return {"source": "hot_path.stage_skip", "execution_arn": "arn:x", "hot_run": hot_run}


def test_resolve_warn(monkeypatch):
    monkeypatch.setattr(ss, "_recent_hot_runs", lambda **kw: [])
    out = ss.resolve(_event(_run(delta=7, assign=_SKIP_A, run_id="R1")))
    assert out["severity"] == "WARN"
    assert out["title"] == "Hot path stage skip"
    assert "delta_size=7" in out["message"]
    assert out["source"] == "hot_path.stage_skip"
    assert out["execution_arn"] == "arn:x"
    assert out["context"]["assign_skip_streak"] == 1
    assert out["context"]["streak_threshold"] == ss._STREAK_K


def test_resolve_error(monkeypatch):
    prior = [_run(delta=3, assign=_SKIP_A), _run(delta=3, assign=_SKIP_A)]
    monkeypatch.setattr(ss, "_recent_hot_runs", lambda **kw: prior)
    out = ss.resolve(_event(_run(delta=7, assign=_SKIP_A, run_id="R9")))
    assert out["severity"] == "ERROR"
    assert out["title"] == "Hot path stage-skip streak"
    assert "consecutive" in out["message"]
    assert out["context"]["assign_skip_streak"] == 3


def test_resolve_falls_back_to_warn_when_history_read_fails(monkeypatch):
    """A DynamoDB read failure must not raise — WARN is the safe floor
    (the ASL gate already established this run really skipped a stage)."""

    def boom(**kw):
        raise RuntimeError("dynamodb unavailable")

    monkeypatch.setattr(ss, "_recent_hot_runs", boom)
    out = ss.resolve(_event(_run(delta=7, assign=_SKIP_A, run_id="R1")))
    assert out["severity"] == "WARN"
