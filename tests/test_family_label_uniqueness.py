"""Tests for the family label-uniqueness invariant (pipeline_tools.family_label_uniqueness).

Mechanics on synthetic registries via ``enforce_label_uniqueness``: a cross-supercategory
label collision auto-disambiguates to distinct, self-explanatory chips; a same-supercategory
collision (a missed merge the qualifier cannot split) fails loud; config-resolved labels are a
no-op. When the live registry is present, the bundled qualifier resolves exactly the known
cross-supercategory duplicate-chip pairs and leaves zero residual duplicate labels.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_tools import vocab
from pipeline_tools.embeddings import EmbeddingCache
from pipeline_tools.family_label_uniqueness import (
    SUPERCATEGORY_QUALIFIER,
    enforce_label_uniqueness,
)
from pipeline_tools.registry import FamilyRegistry, norm_name

_STUB = lambda: EmbeddingCache(embed=lambda ts: [[0.5, 0.5] for _ in ts])  # noqa: E731

_SC_REAGENT = "molecular_biochem_reagents"
_SC_THERAPY = "therapeutics_interventions"
_SC_CLINICAL = "clinical_instruments_assays"
_SC_COMP = "computational_statistical"


def _fam(fid, label, sc, *, status="active", display="standard", members=("t",)):
    return {"family_id": fid, "label": label, "supercategory": sc, "dominant_kind": "method",
            "member_tool_ids": list(members), "exemplar_tool_ids": list(members)[:3],
            "status": status, "member_display_names": list(members), "display": display}


def _registry(*fams):
    return FamilyRegistry(list(fams), cache=_STUB())


# --- the qualifier map ---------------------------------------------------------

def test_qualifier_map_covers_every_supercategory_exactly():
    """The map must cover the frozen vocab 1:1 — a new SC without a noun fails loud at import."""
    assert set(SUPERCATEGORY_QUALIFIER) == set(vocab.SUPERCATEGORY_IDS)
    assert all(q and q.strip() for q in SUPERCATEGORY_QUALIFIER.values())


# --- cross-supercategory disambiguation (the common case) ----------------------

def test_cross_supercategory_pair_autodisambiguates():
    fr = _registry(
        _fam("fam_0612", "AAV gene-therapy vectors", _SC_REAGENT),
        _fam("fam_0777", "AAV gene-therapy vectors", _SC_THERAPY),
    )
    deltas = enforce_label_uniqueness(fr)
    assert fr.get("fam_0612")["label"] == "AAV gene-therapy vectors (research reagents)"
    assert fr.get("fam_0777")["label"] == "AAV gene-therapy vectors (therapeutics)"
    assert {d["op"] for d in deltas} == {"disambiguate"}
    assert len(deltas) == 2
    # labels are now globally unique
    labels = [norm_name(f["label"]) for f in fr.records()]
    assert len(labels) == len(set(labels))


def test_unrelated_unique_labels_are_left_untouched():
    fr = _registry(
        _fam("fam_0001", "MRI", "imaging_image_analysis"),
        _fam("fam_0002", "scRNA-seq", "genomics_sequencing"),
    )
    deltas = enforce_label_uniqueness(fr)
    assert deltas == []
    assert fr.get("fam_0001")["label"] == "MRI"
    assert fr.get("fam_0002")["label"] == "scRNA-seq"


# --- the gate: same-supercategory collisions cannot be auto-split --------------

def test_same_supercategory_collision_fails_loud():
    """Two active families, identical label AND supercategory — a missed merge, not a fork."""
    fr = _registry(
        _fam("fam_0010", "Enzyme activity assays", _SC_REAGENT),
        _fam("fam_0011", "Enzyme activity assays", _SC_REAGENT),
    )
    with pytest.raises(ValueError, match="cannot disambiguate"):
        enforce_label_uniqueness(fr)
    # fail-loud BEFORE any mutation — neither label was qualified.
    assert fr.get("fam_0010")["label"] == "Enzyme activity assays"
    assert fr.get("fam_0011")["label"] == "Enzyme activity assays"


def test_three_way_collision_with_a_same_sc_pair_gates_the_whole_group():
    """3 families share a label; two share a supercategory → that subset cannot split → gate."""
    fr = _registry(
        _fam("fam_0020", "Immunophenotyping assays", _SC_CLINICAL),
        _fam("fam_0021", "Immunophenotyping assays", _SC_REAGENT),
        _fam("fam_0022", "Immunophenotyping assays", _SC_REAGENT),  # collides with fam_0021
    )
    with pytest.raises(ValueError, match="cannot disambiguate"):
        enforce_label_uniqueness(fr)
    # whole group deferred to the gate — nothing half-applied.
    assert fr.get("fam_0020")["label"] == "Immunophenotyping assays"


# --- precedence by construction: config already disambiguated ------------------

def test_config_bespoke_relabel_already_distinct_is_a_noop():
    """A curator relabel (run upstream) made the labels differ — no collision left to resolve."""
    fr = _registry(
        _fam("fam_0118", "Diagnostic endoscopy", _SC_CLINICAL),
        _fam("fam_0763", "Therapeutic and surgical endoscopy", _SC_THERAPY),
    )
    deltas = enforce_label_uniqueness(fr)
    assert deltas == []
    assert fr.get("fam_0118")["label"] == "Diagnostic endoscopy"


# --- scoping: provisional families are not yet chips ---------------------------

def test_provisional_families_are_out_of_scope():
    """A provisional family on a placeholder label is still in the review queue, not a chip."""
    fr = _registry(
        _fam("fam_0030", "Shared label", _SC_REAGENT, status="active"),
        _fam("fam_0031", "Shared label", _SC_THERAPY, status="provisional"),
    )
    deltas = enforce_label_uniqueness(fr)
    # only one ACTIVE family carries the label → no active collision → no-op.
    assert deltas == []
    assert fr.get("fam_0030")["label"] == "Shared label"
    assert fr.get("fam_0031")["label"] == "Shared label"


def test_active_only_false_includes_provisional():
    fr = _registry(
        _fam("fam_0030", "Shared label", _SC_REAGENT, status="active"),
        _fam("fam_0031", "Shared label", _SC_THERAPY, status="provisional"),
    )
    deltas = enforce_label_uniqueness(fr, active_only=False)
    assert len(deltas) == 2  # both disambiguated when the scope is widened


# --- idempotency + re-entrancy -------------------------------------------------

def test_rerun_does_not_double_qualify():
    fr = _registry(
        _fam("fam_0612", "AAV gene-therapy vectors", _SC_REAGENT),
        _fam("fam_0777", "AAV gene-therapy vectors", _SC_THERAPY),
    )
    enforce_label_uniqueness(fr)
    first = {f["family_id"]: f["label"] for f in fr.records()}
    deltas2 = enforce_label_uniqueness(fr)
    assert deltas2 == []  # already unique, nothing to do
    assert {f["family_id"]: f["label"] for f in fr.records()} == first
    assert "(research reagents) (research reagents)" not in fr.get("fam_0612")["label"]


# --- defense in depth: a qualified label colliding outside its group -----------

def test_qualified_label_colliding_with_existing_label_gates():
    """Qualifying a fork must not silently create a NEW collision with a curator label."""
    fr = _registry(
        _fam("fam_0040", "AAV gene-therapy vectors", _SC_REAGENT),       # → "(research reagents)"
        _fam("fam_0041", "AAV gene-therapy vectors", _SC_THERAPY),       # → "(therapeutics)"
        _fam("fam_0042", "AAV gene-therapy vectors (research reagents)", _SC_CLINICAL),  # pre-existing
    )
    with pytest.raises(ValueError, match="invariant would be violated"):
        enforce_label_uniqueness(fr)
    # atomic: the gate fired in pass 1, so the forks it WOULD have qualified are untouched.
    assert fr.get("fam_0040")["label"] == "AAV gene-therapy vectors"
    assert fr.get("fam_0041")["label"] == "AAV gene-therapy vectors"


# --- the live registry: the bundled qualifier resolves the known dupe-chip pairs -

_KNOWN_PAIRS = {
    "AAV gene-therapy vectors": {
        "fam_0612": "AAV gene-therapy vectors (research reagents)",
        "fam_0777": "AAV gene-therapy vectors (therapeutics)",
    },
    "Immunophenotyping assays": {
        "fam_0136": "Immunophenotyping assays (clinical)",
        "fam_0628": "Immunophenotyping assays (research reagents)",
    },
    "Renal function estimation methods": {
        "fam_0167": "Renal function estimation methods (clinical)",
        "fam_0292": "Renal function estimation methods (computational)",
    },
    "Deep learning image analysis": {
        "fam_0212": "Deep learning image analysis (computational)",
        "fam_0480": "Deep learning image analysis (imaging)",
    },
    "Enzyme activity assays": {
        "fam_0389": "Enzyme activity assays (metabolic)",
        "fam_0581": "Enzyme activity assays (research reagents)",
    },
}


def test_live_registry_resolves_known_pairs_and_leaves_no_duplicates():
    path = Path("out/tools/a2/family_registry.json")
    if not path.exists():
        pytest.skip("live family_registry.json not present")
    fams = json.loads(path.read_text())["families"]
    fr = FamilyRegistry(fams, cache=_STUB())

    deltas = enforce_label_uniqueness(fr)

    by_id = {f["family_id"]: f for f in fr.records()}
    for _label, expected in _KNOWN_PAIRS.items():
        for fid, new_label in expected.items():
            assert by_id[fid]["label"] == new_label, (fid, by_id[fid]["label"])

    # every disambiguation was one of the known pairs (no surprise collisions on live data).
    expected_ids = {fid for m in _KNOWN_PAIRS.values() for fid in m}
    assert {d["family_id"] for d in deltas} == expected_ids

    # post-condition: no two ACTIVE families share a normalized label.
    active = [norm_name(f["label"]) for f in fr.records() if f.get("status") == "active"]
    assert len(active) == len(set(active))


def test_live_registry_same_supercategory_collision_fires_gate_atomically():
    """The build-protecting path on REAL-shaped data: a same-SC duplicate must fail the build,
    BEFORE any of the (many) unrelated cross-SC forks are mutated.

    Pins both behaviours the synthetic 2-family test cannot: that the gate fires against the
    full live registry (a grouping/ordering regression that no-ops it would be caught), and
    that pass-1 detection is atomic (no partial disambiguation reaches a gated build).
    """
    path = Path("out/tools/a2/family_registry.json")
    if not path.exists():
        pytest.skip("live family_registry.json not present")
    fams = json.loads(path.read_text())["families"]
    # Inject a same-supercategory, same-label clone of a known family (a missed merge the
    # qualifier cannot split). fam_0612 is molecular_biochem_reagents "AAV gene-therapy vectors".
    orig = next(f for f in fams if f["family_id"] == "fam_0612")
    clone = {**orig, "family_id": "fam_0999",
             "member_tool_ids": [], "exemplar_tool_ids": [], "member_display_names": []}
    fr = FamilyRegistry([*fams, clone], cache=_STUB())

    with pytest.raises(ValueError, match="cannot disambiguate"):
        enforce_label_uniqueness(fr)

    # Atomic: NO family was relabeled — the unrelated cross-SC forks keep their bare labels,
    # and the colliding pair is untouched.
    assert fr.get("fam_0612")["label"] == "AAV gene-therapy vectors"
    assert fr.get("fam_0999")["label"] == "AAV gene-therapy vectors"
    assert fr.get("fam_0480")["label"] == "Deep learning image analysis"   # an unrelated fork
    assert fr.get("fam_0389")["label"] == "Enzyme activity assays"         # another unrelated fork
