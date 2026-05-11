"""Tests for spotlight/assembler.py (Plan 06-06).

Covers SPOT-09 (per-paper author payload shape) and the schema round-trip
from build_artifact() output through docs/spotlight.schema.json.

Eleven tests per Plan 06-06 <behavior> block:

  1.  build_artifact returns dict with the locked top-level keys.
  2.  version field == "spotlight_v1" by default.
  3.  generated_at matches ISO 8601 UTC Z regex.
  4.  spotlights array length == len(selected ValidatedLede list).
  5.  papers[].first_author has EXACTLY {personIdentifier, displayName, position}.
  6.  personIdentifier (camelCase JSON) == person_identifier (snake_case Python).
  7.  pool_snapshot length == len(input PoolEntry list).
  8.  pool_snapshot[].was_selected reflects selected_ids membership.
  9.  Output validates against docs/spotlight.schema.json (Draft 2020-12).
  10. ``cwid_`` literal does NOT appear in spotlight/assembler.py source.
  11. ValueError raised when any selected entry has status != "pass".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from spotlight.assembler import (
    DEFAULT_TAXONOMY_VERSION,
    SPOTLIGHT_VERSION,
    build_artifact,
)
from spotlight.critic import ValidatedLede
from spotlight.sensitive_gate import SubtopicMeta
from spotlight.types import Author, Paper, PoolEntry


# ---------------------------------------------------------------------------
# Synthetic factories
# ---------------------------------------------------------------------------


def _author(pid: str, name: str, position: str) -> Author:
    return Author(person_identifier=pid, display_name=name, position=position)


def _paper(pmid: str, title: str = "Test paper") -> Paper:
    return Paper(
        pmid=pmid,
        title=title,
        journal="Test Journal",
        year=2025,
        impact_score=80.0,
        impact_justification="synthetic",
        synopsis="synthetic synopsis text",
        first_author=_author(f"pid_first_{pmid}", "First Author", "first"),
        last_author=_author(f"pid_last_{pmid}", "Last Author", "last"),
    )


def _vlede(
    subtopic_id: str,
    parent_topic: str,
    pmids: tuple[str, ...],
    status: str = "pass",
) -> ValidatedLede:
    return ValidatedLede(
        subtopic_id=subtopic_id,
        parent_topic=parent_topic,
        lede=(
            "WCM scholars are testing minimal synthetic spotlights across two "
            "fixture papers to exercise schema acceptance at assembly time."
        ),
        status=status,
        attempts=tuple(),
        papers_used=pmids,
    )


def _pool_entry(subtopic_id: str, parent_topic: str, score: float) -> PoolEntry:
    return PoolEntry(
        subtopic_id=subtopic_id,
        pool_score=score,
        parent_topic=parent_topic,
        papers=tuple(),
    )


def _meta(subtopic_id: str, label: str, parent_topic_label: str) -> SubtopicMeta:
    return SubtopicMeta(
        subtopic_id=subtopic_id,
        label=label,
        description="Synthetic description for assembler tests.",
        parent_topic_label=parent_topic_label,
    )


def _build_minimal_inputs():
    """Return (selected, pool, subtopic_metadata, paper_metadata) for happy-path tests."""
    p1, p2 = _paper("9000001"), _paper("9000002")
    selected = [_vlede("st_001", "Aging / Geroscience", ("9000001", "9000002"))]
    pool = [
        _pool_entry("st_001", "Aging / Geroscience", 142.7),
        _pool_entry("st_002", "Cardiovascular Disease", 99.3),
    ]
    subtopic_metadata = {
        "st_001": _meta("st_001", "Synthetic Aging Subtopic", "Aging / Geroscience"),
        "st_002": _meta("st_002", "Synthetic Cardio Subtopic", "Cardiovascular Disease"),
    }
    paper_metadata = {"9000001": p1, "9000002": p2}
    return selected, pool, subtopic_metadata, paper_metadata


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_top_level_keys_present():
    """Test 1: build_artifact returns dict with the locked top-level keys."""
    selected, pool, sm, pm = _build_minimal_inputs()
    art = build_artifact(selected, pool, sm, pm)
    assert set(art.keys()) == {
        "version",
        "generated_at",
        "taxonomy_version",
        "spotlights",
        "pool_snapshot",
    }


def test_version_is_spotlight_v1():
    """Test 2: version field equals "spotlight_v1" by default."""
    selected, pool, sm, pm = _build_minimal_inputs()
    art = build_artifact(selected, pool, sm, pm)
    assert art["version"] == "spotlight_v1"
    assert SPOTLIGHT_VERSION == "spotlight_v1"
    assert DEFAULT_TAXONOMY_VERSION == "taxonomy_v2"


def test_generated_at_iso_z_format():
    """Test 3: generated_at matches ISO 8601 UTC Z regex."""
    selected, pool, sm, pm = _build_minimal_inputs()
    art = build_artifact(selected, pool, sm, pm)
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", art["generated_at"]), (
        f"generated_at must be ISO 8601 UTC Z second-precision; got {art['generated_at']!r}"
    )


def test_spotlights_length_matches_selected():
    """Test 4: spotlights length matches input ValidatedLede list."""
    p1, p2, p3, p4 = (_paper(f"900000{i}") for i in range(1, 5))
    selected = [
        _vlede("st_a", "Aging / Geroscience", ("9000001", "9000002")),
        _vlede("st_b", "Cardiovascular Disease", ("9000003", "9000004")),
    ]
    pool = [_pool_entry("st_a", "Aging / Geroscience", 99.0)]
    sm = {
        "st_a": _meta("st_a", "Aging Subtopic", "Aging / Geroscience"),
        "st_b": _meta("st_b", "Cardio Subtopic", "Cardiovascular Disease"),
    }
    pm = {"9000001": p1, "9000002": p2, "9000003": p3, "9000004": p4}
    art = build_artifact(selected, pool, sm, pm)
    assert len(art["spotlights"]) == 2


def test_first_author_keys_camelcase_only():
    """Test 5: papers[].first_author has EXACTLY {personIdentifier, displayName, position}."""
    selected, pool, sm, pm = _build_minimal_inputs()
    art = build_artifact(selected, pool, sm, pm)
    fa = art["spotlights"][0]["papers"][0]["first_author"]
    la = art["spotlights"][0]["papers"][0]["last_author"]
    assert set(fa.keys()) == {"personIdentifier", "displayName", "position"}
    assert set(la.keys()) == {"personIdentifier", "displayName", "position"}
    # snake_case Python attrs should NOT appear in JSON output
    assert "person_identifier" not in fa
    assert "display_name" not in fa


def test_personidentifier_value_matches_python_attr():
    """Test 6: personIdentifier == Python Paper.first_author.person_identifier."""
    selected, pool, sm, pm = _build_minimal_inputs()
    art = build_artifact(selected, pool, sm, pm)
    py_paper = pm["9000001"]
    json_paper = art["spotlights"][0]["papers"][0]
    assert json_paper["first_author"]["personIdentifier"] == py_paper.first_author.person_identifier
    assert json_paper["first_author"]["displayName"] == py_paper.first_author.display_name
    assert json_paper["first_author"]["position"] == "first"
    assert json_paper["last_author"]["personIdentifier"] == py_paper.last_author.person_identifier
    assert json_paper["last_author"]["position"] == "last"


def test_pool_snapshot_length_matches_input():
    """Test 7: pool_snapshot length == len(input PoolEntry list)."""
    selected, pool, sm, pm = _build_minimal_inputs()
    art = build_artifact(selected, pool, sm, pm)
    assert len(art["pool_snapshot"]) == len(pool) == 2


def test_was_selected_reflects_selected_ids():
    """Test 8: was_selected True iff subtopic_id in selected ValidatedLede ids."""
    selected, pool, sm, pm = _build_minimal_inputs()
    art = build_artifact(selected, pool, sm, pm)
    by_id = {entry["subtopic_id"]: entry for entry in art["pool_snapshot"]}
    assert by_id["st_001"]["was_selected"] is True
    assert by_id["st_002"]["was_selected"] is False


def test_artifact_validates_against_schema():
    """Test 9: build_artifact output passes Draft 2020-12 schema validation."""
    schema_path = Path(__file__).parent / "docs" / "spotlight.schema.json"
    schema = json.loads(schema_path.read_text())
    selected, pool, sm, pm = _build_minimal_inputs()
    art = build_artifact(selected, pool, sm, pm)
    errors = list(Draft202012Validator(schema).iter_errors(art))
    assert errors == [], f"assembler output must validate; got: {errors}"


def test_no_cwid_literal_in_assembler_source():
    """Test 10: ``cwid_`` literal does NOT appear in spotlight/assembler.py source."""
    src = (Path(__file__).parent / "spotlight" / "assembler.py").read_text()
    # Strip comment lines so we can be strict about the executable source.
    code_lines = [
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "cwid_" not in code, "cwid_ literal forbidden in assembler.py (CLAUDE.md naming rule)"


def test_value_error_on_non_pass_status():
    """Test 11: build_artifact raises ValueError if any entry has status != 'pass'."""
    selected, pool, sm, pm = _build_minimal_inputs()
    bad = _vlede("st_001", "Aging / Geroscience", ("9000001", "9000002"), status="needs_review")
    with pytest.raises(ValueError, match="status"):
        build_artifact([bad], pool, sm, pm)
