"""#80 Phase 2 (PR 4) — config/hierarchy_draft_coverage.json drift guard.

The onboarding Assign fan-out's `DeriveDirtyTopics` Lambda
(`pipeline_onboarding/assign_fanout.py`) filters the per-topic Step
Functions Map to topics that have an approved hierarchy draft, using the
bundled `config/hierarchy_draft_coverage.json` manifest — the Lambda zip
cannot glob `.planning/`. This test fails if the manifest drifts from the
actual approved `hierarchy_draft_*.json` files on disk: add, remove, or
un-approve a draft and the manifest must move with it.

The contract mirrors `assign_subtopics._load_hierarchy_draft`'s gate —
a draft "covers" a topic only when its `review_status` is `approved` or
`auto_approved` (anything else `sys.exit(2)`s, which would fail the Map).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "config" / "hierarchy_draft_coverage.json"
DRAFT_DIR = REPO_ROOT / ".planning" / "phases" / "04-subtopic-system"

_PREFIX = "hierarchy_draft_"
_SUFFIX = ".json"
_APPROVED = {"approved", "auto_approved"}


def _approved_drafts_on_disk() -> set[str]:
    """Topic IDs whose on-disk draft has an approved review_status."""
    covered: set[str] = set()
    for path in DRAFT_DIR.glob(f"{_PREFIX}*{_SUFFIX}"):
        topic = path.name[len(_PREFIX):-len(_SUFFIX)]
        if json.loads(path.read_text()).get("review_status") in _APPROVED:
            covered.add(topic)
    return covered


def _manifest_topics() -> list[str]:
    return json.loads(MANIFEST_PATH.read_text())["draft_covered_topics"]


@pytest.mark.skipif(
    not any(DRAFT_DIR.glob(f"{_PREFIX}*{_SUFFIX}")),
    reason="needs the approved hierarchy drafts under gitignored .planning/",
)
def test_manifest_matches_approved_drafts_on_disk():
    on_disk = _approved_drafts_on_disk()
    manifest = set(_manifest_topics())
    assert manifest == on_disk, (
        "config/hierarchy_draft_coverage.json is out of sync with the "
        f"approved hierarchy_draft_*.json files in {DRAFT_DIR.name}/. "
        f"Only in manifest: {sorted(manifest - on_disk)}. "
        f"Only on disk: {sorted(on_disk - manifest)}."
    )


def test_manifest_is_sorted_and_unique():
    topics = _manifest_topics()
    assert topics == sorted(topics), "manifest topics must be sorted"
    assert len(topics) == len(set(topics)), "manifest has duplicate topics"


def test_oral_craniofacial_health_intentionally_absent():
    """The one taxonomy topic with no draft is an excluded topic, by design
    (config/excluded_topics.json) — it must never enter the coverage set."""
    assert "oral_craniofacial_health" not in set(_manifest_topics())
