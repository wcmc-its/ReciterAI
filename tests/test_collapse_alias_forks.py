"""#252 post-hoc fork-collapse — pure-logic check (re-points every consumer of a dropped id)."""
from cli.collapse_alias_forks import _selfcheck, collapse_forks, discover_clusters


def test_selfcheck():
    """End-to-end merge over a synthetic payload: keep accretion, family/hierarchy/faculty/
    tool_context/grant_signal/telemetry re-point, no orphan drop id (asserts inside _selfcheck)."""
    _selfcheck()


def test_discover_skips_singletons():
    """A class with <2 live records is not a cluster (nothing to collapse)."""
    merges = [{"class": "x", "canonical": "X cells", "variants": ["X", "X cell line"]}]
    tools = [{"canonical_tool_id": "tool_000001", "display_name": "X", "aliases": ["X"]}]
    assert discover_clusters(tools, merges) == []


def test_grant_signal_guard_no_hidden_pubs():
    """pub_count recompute fails loud if a cluster record has institution pubs not visible in faculty."""
    import pytest
    merges = [{"class": "x", "canonical": "X cells", "variants": ["X", "X cell line"]}]
    payload = {
        "tools": [
            {"canonical_tool_id": "tool_000001", "display_name": "X cell line", "aliases": ["X cell line"],
             "pub_count": 9, "salience_tier": "A", "context_evidence": [], "supercategory": "s"},
            {"canonical_tool_id": "tool_000009", "display_name": "X", "aliases": ["X"],
             "pub_count": 1, "salience_tier": "A", "context_evidence": [], "supercategory": "s"},
        ],
        "families": [{"family_id": "fam_0001", "label": "F", "supercategory": "s", "status": "active",
                      "member_tool_ids": ["tool_000001", "tool_000009"], "exemplar_tool_ids": []}],
        "hierarchy": {}, "faculty": {}, "tool_context": {}, "entities": [],
    }
    with pytest.raises(SystemExit):  # tool_000001 has 9 institution pubs, 0 faculty-visible
        collapse_forks(payload, merges, generic_terms=[])
