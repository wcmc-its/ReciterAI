"""Unit tests for opaque id minting (pipeline_tools.ids).

The D-06 invariant: ids are durable, opaque, monotonic, and seeded from the
registry high-water mark — NEVER derived from name or membership.
"""

from pipeline_tools import ids
from pipeline_tools.ids import IdMinter


def test_tool_ids_are_monotonic_and_zero_padded():
    m = IdMinter.for_tools([])
    assert m.mint() == "tool_000001"
    assert m.mint() == "tool_000002"
    assert ids.is_tool_id("tool_000002")
    assert not ids.is_tool_id("mri_scanner")  # a name-derived id is NOT a valid opaque id


def test_family_ids_are_monotonic():
    m = IdMinter.for_families([])
    assert m.mint() == "fam_0001"
    assert m.mint() == "fam_0002"
    assert ids.is_family_id("fam_0002")


def test_minter_seeds_from_existing_high_water_mark():
    # A reload that re-seeds from existing ids must NOT collide with them.
    m = IdMinter.for_tools(["tool_000001", "tool_000007", "tool_000003"])
    assert m.high_water == 7
    assert m.mint() == "tool_000008"


def test_minter_ignores_foreign_id_shapes():
    # Stray non-conforming ids (e.g. a legacy slug) don't perturb the counter.
    m = IdMinter.for_tools(["mri_scanner", "tool_000002"])
    assert m.high_water == 2
    assert m.mint() == "tool_000003"


def test_two_minters_same_seed_same_sequence():
    # Reproducible without a clock/RNG (neither available in replay).
    a = IdMinter.for_families(["fam_0005"])
    b = IdMinter.for_families(["fam_0005"])
    assert [a.mint(), a.mint()] == [b.mint(), b.mint()] == ["fam_0006", "fam_0007"]
