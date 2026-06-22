"""Unit tests for the batch_screen soft-prioritizer pre-filter (pure logic; no DB)."""
from pipeline_cores.prefilter import (
    CORE_MESH_TREE_PREFIXES,
    compute_priors,
    prefilter_prior,
)


# --- prefilter_prior (noisy-OR of the two cheap signals) -------------------
def test_prior_neither_signal_is_zero():
    assert prefilter_prior(False, False) == 0.0


def test_prior_author_only_outranks_mesh_only():
    assert prefilter_prior(True, False) == 0.6
    assert prefilter_prior(False, True) == 0.4
    assert prefilter_prior(True, False) > prefilter_prior(False, True)


def test_prior_both_signals_noisy_or():
    # 1 - (1-0.6)(1-0.4) = 1 - 0.24 = 0.76
    assert prefilter_prior(True, True) == 0.76
    assert prefilter_prior(True, True) > prefilter_prior(True, False)  # both beats either alone


# --- compute_priors (membership-set -> per-pmid prior) ---------------------
def test_compute_priors_maps_each_signal():
    priors = compute_priors(
        ["1", "2", "3", "4"],
        mesh_pmids={"2", "4"},
        author_pmids={"3", "4"},
    )
    assert priors == {"1": 0.0, "2": 0.4, "3": 0.6, "4": 0.76}


def test_compute_priors_coerces_pmid_to_str():
    priors = compute_priors([10, 20], mesh_pmids={"10"}, author_pmids=set())
    assert priors == {"10": 0.4, "20": 0.0}


# --- the validated E-tree map (guards against accidental edits) ------------
def test_core_mesh_tree_prefixes_cover_the_technique_cores():
    # the families the empirical probe found discriminative; others rely on author + screen
    assert CORE_MESH_TREE_PREFIXES["2"] == ["E01.370.350"]            # imaging
    assert CORE_MESH_TREE_PREFIXES["4"] == ["E05.242"]               # flow cytometry
    assert CORE_MESH_TREE_PREFIXES["12"] == ["E05.196.867"]          # NMR
    # cores with no clean MeSH technique branch are intentionally absent
    for unmapped in ("1", "6", "7", "8", "10"):
        assert unmapped not in CORE_MESH_TREE_PREFIXES
