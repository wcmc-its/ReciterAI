"""Deterministic routing-sanity net — a post-classification FLAG layer.

The systematic alternative to per-item prompt rules (which are whack-a-mole and
overfit a non-representative seed). Rather than teaching the classifier every
edge case, this surfaces CLASSES of likely-misroute into the bounded exceptions
queue (§9c) for human/relabel review. It is **flag-only** — it never changes an
assignment. That asymmetry is the whole point: a false flag costs one review row;
a false re-route would silently corrupt the taxonomy. It also catches the
classifier's *confident-but-wrong* cases, which low-confidence flagging misses.

It generalizes to A2, where the real error distribution lives — the seed is just
where the checks were calibrated. Checks are intentionally few and high-signal;
they stay quiet when the classification is good and light up on genuine
structural signals (e.g. a distinctive tool with no home among the 13 — the
trigger to evaluate a new supercategory, not to silently mint one).
"""

from __future__ import annotations

import re

from pipeline_tools import vocab

# Flag types (feed the §9c exceptions queue).
SANITY_INDEX_MISROUTE = "sanity_index_outside_clinical_computational_dataset"
SANITY_DATASET_NAME = "sanity_dataset_named_nondataset_kind"
SANITY_PATHOGEN = "sanity_pathogen_unexpected_kind"
SANITY_KIND_SUPERCAT = "sanity_kind_supercategory_mismatch"
SANITY_OTHER_DISTINCTIVE = "sanity_other_distinctive_taxonomy_gap_candidate"

_INDEX_RE = re.compile(r"\b(index|score|scale|criteria|questionnaire|rating)\b", re.I)
# An index/scale legitimately lives in one of these: a clinical rating scale (#6),
# a metric computed on data (#5), or a distributed lookup product (#10).
_INDEX_OK_SUPERCATS = frozenset({
    "clinical_instruments_assays", "computational_statistical", "datasets_cohorts",
})

_DATASET_NAME_RE = re.compile(
    r"\b(database|registry|cohort|claims|biobank|repository|sumstats|warehouse)\b", re.I
)

_PATHOGEN_RE = re.compile(
    r"\b(virus|viral|coronavirus|sars[- ]?cov|influenza|bacteri\w*|tuberculosis|"
    r"pathogen|plasmodium|mycobacteri\w*)\b", re.I
)
_PATHOGEN_OK_KINDS = frozenset({"reagent", "organism_or_cells"})

# kind -> the supercategory(ies) it should almost always live in (soft constraint;
# reagent/instrument/method/software/assay are legitimately cross-supercategory).
_KIND_EXPECTED_SUPERCAT = {
    "organism_or_cells": frozenset({"animal_cell_models"}),
    "dataset": frozenset({"datasets_cohorts"}),
    "model": frozenset({"computational_statistical"}),
}


def routing_sanity_flags(rec: dict) -> list[dict]:
    """Return likely-misroute flags for one classified record; empty == looks fine.

    Only ``method_tool`` records are checked (infrastructure/excluded carry no
    supercategory/kind). Each flag is ``{"check": <type>, "reason": <text>}``.
    """
    if rec.get("disposition") != vocab.CAPABILITY_DISPOSITION:
        return []

    name = rec.get("display_name") or rec.get("raw_name", "")
    supercat = rec.get("supercategory")
    kind = rec.get("kind")
    tier = rec.get("salience_tier")
    flags: list[dict] = []

    if _INDEX_RE.search(name) and supercat not in _INDEX_OK_SUPERCATS:
        flags.append({"check": SANITY_INDEX_MISROUTE,
                      "reason": f"'{name}' reads as an index/scale but landed in {supercat} "
                                f"(expect clinical_instruments_assays / computational_statistical / datasets_cohorts)"})

    if _DATASET_NAME_RE.search(name) and kind != "dataset":
        flags.append({"check": SANITY_DATASET_NAME,
                      "reason": f"'{name}' is named like a dataset but kind={kind}"})

    if _PATHOGEN_RE.search(name) and kind not in _PATHOGEN_OK_KINDS:
        flags.append({"check": SANITY_PATHOGEN,
                      "reason": f"'{name}' reads as a pathogen but kind={kind} (expect reagent or organism_or_cells)"})

    expected = _KIND_EXPECTED_SUPERCAT.get(kind)
    if expected and supercat not in expected:
        flags.append({"check": SANITY_KIND_SUPERCAT,
                      "reason": f"kind={kind} usually routes to {sorted(expected)}, got {supercat}"})

    if supercat == vocab.OTHER_SUPERCATEGORY and tier != vocab.DEMOTED_TIER:
        flags.append({"check": SANITY_OTHER_DISTINCTIVE,
                      "reason": f"'{name}' is a distinctive (non-C) tool with no home in the 13 — "
                                f"taxonomy-gap candidate; evaluate a new supercategory only if A2 surfaces a cluster"})

    return flags
