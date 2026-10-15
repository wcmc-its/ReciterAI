"""The area-signals image carries every covered hierarchy draft.

`pipeline_area_signals.subareas` reads
`.planning/phases/04-subtopic-system/hierarchy_draft_<topic>.json` for every
topic in `config/hierarchy_draft_coverage.json`. `.planning` is dockerignored,
so `.dockerignore` re-includes the drafts. This test evaluates `.dockerignore`
(last match wins, a pattern excludes a directory's whole subtree, `*` never
crosses `/`) for each covered draft path, so dropping or misordering the
re-include fails here instead of shipping an image that assigns nothing. It
needs no drafts on disk (they are gitignored); mirrors
`tests/test_hierarchy_draft_coverage_manifest.py`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline_area_signals import subareas
from utils.taxonomy import load_taxonomy

REPO_ROOT = Path(__file__).resolve().parent.parent
DRAFT_REL = ".planning/phases/04-subtopic-system"


def _patterns() -> list[tuple[bool, re.Pattern]]:
    """(is_exception, regex) per .dockerignore line, in file order."""
    out = []
    for line in (REPO_ROOT / ".dockerignore").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pat = line[1:] if negate else line
        pat = pat.strip("/")
        rx = ""
        i = 0
        while i < len(pat):
            if pat.startswith("**", i):
                rx += ".*"
                i += 2
            elif pat[i] == "*":
                rx += "[^/]*"
                i += 1
            elif pat[i] == "?":
                rx += "[^/]"
                i += 1
            else:
                rx += re.escape(pat[i])
                i += 1
        out.append((negate, re.compile(rx)))
    return out


def in_build_context(path: str) -> bool:
    """Docker's rule: the last pattern matching the path or any parent decides."""
    parts = path.split("/")
    candidates = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    included = True
    for negate, rx in _patterns():
        if any(rx.fullmatch(c) for c in candidates):
            included = negate
    return included


def _covered() -> list[str]:
    return json.loads((REPO_ROOT / "config" / "hierarchy_draft_coverage.json").read_text())[
        "draft_covered_topics"]


def test_every_covered_draft_is_in_the_image():
    missing = [t for t in _covered()
               if not in_build_context(f"{DRAFT_REL}/hierarchy_draft_{t}.json")]
    assert not missing, f".dockerignore drops these covered drafts from the image: {missing}"


@pytest.mark.parametrize("path", [
    f"{DRAFT_REL}/04-01-PLAN.md",
    f"{DRAFT_REL}/hierarchy_draft_implementation_science.raw.txt",
    ".planning/POLICIES.md",
    ".planning/phases/05-other/hierarchy_draft_x.json",
])
def test_the_rest_of_planning_stays_out(path):
    assert not in_build_context(path)


def test_the_matcher_agrees_with_known_entries():
    assert not in_build_context("tests/test_x.py")
    assert not in_build_context("docs/x.md")
    assert in_build_context("docs/spotlight.schema.json")
    assert in_build_context("pipeline_area_signals/subareas.py")
    assert in_build_context("config/hierarchy_draft_coverage.json")


def test_subareas_reads_the_paths_the_image_carries():
    assert subareas.DRAFT_DIR == REPO_ROOT / DRAFT_REL
    assert subareas.COVERAGE_PATH == REPO_ROOT / "config" / "hierarchy_draft_coverage.json"


@pytest.mark.skipif(
    not any((REPO_ROOT / DRAFT_REL).glob("hierarchy_draft_*.json")),
    reason="needs the approved hierarchy drafts under gitignored .planning/",
)
def test_every_covered_draft_loads():
    taxonomy = load_taxonomy()
    ctx = subareas.load_context(taxonomy)
    assert ctx.missing == []
    in_taxonomy = {t["id"] for t in taxonomy["topics"]}
    assert sorted(ctx.hierarchies) == sorted(set(_covered()) & in_taxonomy)
