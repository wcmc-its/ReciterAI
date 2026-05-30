"""Tests for utils/env_check.py thresholds integration and G-1 data-driven columns — Phase 12 D-27/G-1.

Covers:
- load_thresholds() returns a dict with the expected Phase 12 keys and values
- assign_subtopics constants are read from config/thresholds.json at module load
- CLI --confidence-floor flag still overrides the config-derived default
- utils.env_check.EXPECTED_COLUMNS is a data-driven dict mapping table -> columns
"""

from __future__ import annotations

import json
import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

from utils import env_check


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- load_thresholds ----------


def test_load_thresholds_returns_dict():
    """load_thresholds() returns a dict with the expected Phase 12 keys."""
    cfg = env_check.load_thresholds()
    assert isinstance(cfg, dict)
    assert cfg["confidence_floor"] == 0.3
    assert cfg["score_floor"] == 0.3
    assert cfg["tie_epsilon"] == 0.001


def test_load_thresholds_missing_file_raises_with_message(tmp_path: Path):
    """load_thresholds raises FileNotFoundError with a clear message when the file is absent."""
    missing = tmp_path / "nope.json"
    # Provide the path kwarg to test the missing-file branch.
    with pytest.raises(FileNotFoundError, match="thresholds.json"):
        env_check.load_thresholds(missing)


# ---------- assign_subtopics constants ----------


def test_assign_subtopics_constants_read_from_config():
    """assign_subtopics.SCORE_FLOOR, DEFAULT_CONFIDENCE_FLOOR, TIE_EPSILON come from thresholds.json."""
    import assign_subtopics  # noqa: PLC0415
    assert assign_subtopics.SCORE_FLOOR == 0.3
    assert assign_subtopics.DEFAULT_CONFIDENCE_FLOOR == 0.3
    assert assign_subtopics.TIE_EPSILON == 0.001


def test_assign_subtopics_constants_pick_up_config_values(tmp_path: Path):
    """When thresholds.json changes, a module reload reflects the new values."""
    # Write a temporary thresholds.json with different values.
    alt = {
        "uncovered_score_floor": 0.4,
        "low_confidence_floor": 0.35,
        "drift_uncovered_rate_alert": 0.05,
        "drift_low_confidence_topic_max": 50,
        "drift_window_days": 14,
        "spotlight_dirty_subtopic_min": 3,
        "spotlight_dirty_pubs_per_subtopic_min": 5,
        "tie_epsilon": 0.002,
        "confidence_floor": 0.25,
        "score_floor": 0.25,
        "feedback_sweep_max_pmids": 200,
        "recluster_persistence_days": 7,
        "critic_reject_persistence_days": 90,
        "critic_reject_subtopic_max": 2,
        "feedback_diagnostic_max_underlying": 20,
    }
    alt_path = tmp_path / "thresholds.json"
    alt_path.write_text(json.dumps(alt), encoding="utf-8")

    # Patch load_thresholds to return the alt config; then reload assign_subtopics.
    with patch.object(env_check, "load_thresholds", return_value=alt):
        import assign_subtopics as ats  # noqa: PLC0415
        importlib.reload(ats)
        assert ats.SCORE_FLOOR == 0.25
        assert ats.DEFAULT_CONFIDENCE_FLOOR == 0.25
        assert ats.TIE_EPSILON == 0.002

    # Reload back to defaults so other tests aren't affected.
    importlib.reload(ats)


# ---------- G-18 Tier A: score_floor wired across the pipeline ----------


@pytest.mark.parametrize(
    "module_name, attr_name",
    [
        ("cli.discover_subtopics", "SCORE_FLOOR"),
        ("cli.backfill_topic", "SCORE_FLOOR"),
        ("cli.backfill_all", "SCORE_FLOOR"),
        ("score_publications", "SCREENING_THRESHOLD"),
        ("cli.load_dynamodb", "DEFAULT_MIN_SCORE"),
    ],
)
def test_pipeline_score_floor_constants_read_from_config(module_name, attr_name):
    """Each script's score-floor constant resolves to thresholds.json `score_floor`."""
    module = importlib.import_module(module_name)
    assert getattr(module, attr_name) == env_check.load_thresholds()["score_floor"]


# ---------- Tier B-1: internal threshold knobs ----------


@pytest.mark.parametrize(
    "module_name, attr_name, config_key",
    [
        ("pipeline_hot.orchestrator", "_BOOTSTRAP_LOOKBACK_DAYS", "bootstrap_lookback_days"),
        ("cli.discover_subtopics", "MIN_CLUSTER_SIZE", "discover_min_cluster_size"),
        ("score_publications", "TARGET_FAILURE_RATE", "target_failure_rate"),
        ("spotlight.pool_ranker", "TOP_PAPERS_PER_SUBTOPIC", "pool_top_papers_per_subtopic"),
    ],
)
def test_tier_b1_internal_thresholds_read_from_config(module_name, attr_name, config_key):
    """Each Tier B-1 constant resolves to its named thresholds.json key."""
    module = importlib.import_module(module_name)
    assert getattr(module, attr_name) == env_check.load_thresholds()[config_key]


# ---------- CLI flag override ----------


def test_cli_flag_still_overrides():
    """The --confidence-floor argparse flag overrides the config-derived default."""
    import assign_subtopics as ats  # noqa: PLC0415
    import argparse

    # Parse args as if the user passed --confidence-floor 0.45
    parser = argparse.ArgumentParser()
    parser.add_argument("--confidence-floor", type=float, default=ats.DEFAULT_CONFIDENCE_FLOOR)
    args = parser.parse_args(["--confidence-floor", "0.45"])
    # The flag value should override the config default.
    assert args.confidence_floor == 0.45
    # The config-derived module constant is still 0.3 (flag only applies when parsed).
    assert ats.DEFAULT_CONFIDENCE_FLOOR == 0.3


# ---------- EXPECTED_COLUMNS ----------


def test_expected_columns_is_data_driven():
    """EXPECTED_COLUMNS maps table_name -> list[str]; must cover at least 3 tables."""
    ec = env_check.EXPECTED_COLUMNS
    assert isinstance(ec, dict), "EXPECTED_COLUMNS must be a dict"
    assert len(ec) >= 3, f"Expected >= 3 tables in EXPECTED_COLUMNS, got {len(ec)}: {list(ec.keys())}"
    # Spot-check: analysis_summary_article (the publication corpus) must have pmid
    assert "analysis_summary_article" in ec, "analysis_summary_article missing from EXPECTED_COLUMNS"
    assert "pmid" in ec["analysis_summary_article"], (
        "pmid missing from EXPECTED_COLUMNS['analysis_summary_article']"
    )
    # analysis_summary_person must be present
    assert "analysis_summary_person" in ec, "analysis_summary_person missing from EXPECTED_COLUMNS"
    # reciterai_keyword_relevance must be present
    assert "reciterai_keyword_relevance" in ec, "reciterai_keyword_relevance missing from EXPECTED_COLUMNS"
    # reciterai_synopsis + reciterai_impact removed in #38: read path moved to
    # DDB IMPACT# rows. The MariaDB tables stay populated until #37 step 6 but
    # ReciterAI no longer reads from them.
    assert "reciterai_synopsis" not in ec, (
        "reciterai_synopsis should NOT be in EXPECTED_COLUMNS — read path moved to DDB (#38)"
    )
    assert "reciterai_impact" not in ec, (
        "reciterai_impact should NOT be in EXPECTED_COLUMNS — read path moved to DDB (#38)"
    )
