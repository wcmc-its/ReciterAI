"""Frozen vocabulary for the tool/method classifier (docs/tool-classifier-spec.md).

Single source of truth for the spec's CLOSED sets. Classify *into* these; never
invent a value (spec preamble). Only the family registry (§7) and the canonical-
tool registry (§8) grow — and both by match-or-mint, never per-batch from scratch.

What is frozen here (§10):
  - disposition gate           §0.5  — 3 values
  - supercategories            §1    — 13 values
  - kind enum                  §2    — 8 values
  - attribute schema           §3    — delivery / provenance / license / consumable / rrid_candidate
  - salience tiers + basis     §5
  - legacy tool_category → kind weak prior   §4

The legacy projection (§4) is a WEAK PRIOR ONLY. The cardinal rule (spec preamble):
read ``raw_name`` + ``context`` and decide independently — do NOT route off the
incoming ``tool_category`` tag; it is an inconsistent grab-bag.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# §0.5 Disposition — the entry gate (run FIRST). Replaces the v1 is_tool boolean.
# ---------------------------------------------------------------------------

DISPOSITIONS: tuple[str, ...] = (
    "method_tool",     # a genuine methodological capability — the default; ONLY these get supercategory + family
    "infrastructure",  # real & pub-attested but organizational, not methodological — canonicalized (§8), never a supercategory/family/profile
    "excluded",        # not a research tool at all — dropped; alias kept on the §8 denylist so it isn't re-triaged
)
DEFAULT_DISPOSITION = "method_tool"

# Only method_tool records carry a supercategory / kind / family / salience.
CAPABILITY_DISPOSITION = "method_tool"


# ---------------------------------------------------------------------------
# §1 Supercategories — CLOSED SET OF 14. Assign exactly one to method_tool records.
# Order and ids are stable (browse facet; never on the profile). Route by
# CAPABILITY from name + context, not by device/service/kit (orthogonal; §2-§3).
# NOTE: `computational_statistical` is the study-AND-analysis methodology axis — it
# holds RCT/observational/survey *design* and qualitative/mixed/implementation
# methods alongside the stats/ML, so its LABEL is "Research & analysis methods"
# (the id is kept stable to avoid churning classify cache + persisted records). A
# quant/qual/mixed/implementation split, if ever wanted, is a methodology-tradition
# *view* over this one bucket — NOT a 15th supercategory (those traditions share
# study designs; a hard boundary would be brittle).
# ---------------------------------------------------------------------------

SUPERCATEGORIES: tuple[dict[str, str], ...] = (
    {"id": "imaging_image_analysis",   "label": "Imaging & image analysis"},        # 1
    {"id": "microscopy_histology",     "label": "Microscopy & histology"},          # 2
    {"id": "genomics_sequencing",      "label": "Genomics & sequencing"},           # 3
    {"id": "mass_spec_proteomics",     "label": "Mass spec & proteomics"},          # 4
    {"id": "computational_statistical","label": "Research & analysis methods"},      # 5 (computational, statistical, qualitative, implementation, study design)
    {"id": "clinical_instruments_assays","label": "Clinical instruments & assays"}, # 6
    {"id": "animal_cell_models",       "label": "Animal & cell models"},            # 7
    {"id": "molecular_biochem_reagents","label": "Molecular & biochemical reagents"},   # 8
    {"id": "therapeutics_interventions","label": "Therapeutics & interventions"},   # 9
    {"id": "datasets_cohorts",         "label": "Datasets & cohorts"},              # 10
    {"id": "software_informatics",     "label": "Software & informatics platforms"},# 11
    {"id": "structural_biophysical",   "label": "Structural & biophysical methods"},# 12 (new in v3)
    {"id": "functional_metabolic_cellular_assays", "label": "Functional & metabolic cellular assays"},  # 14 (spine-rule mint: Seahorse-XF/mito/metabolic community)
    {"id": "other",                    "label": "Other / uncategorized"},           # 13 (gated remainder, NOT a catch-all)
)
SUPERCATEGORY_IDS: frozenset[str] = frozenset(s["id"] for s in SUPERCATEGORIES)
SUPERCATEGORY_LABELS: dict[str, str] = {s["id"]: s["label"] for s in SUPERCATEGORIES}
OTHER_SUPERCATEGORY = "other"  # §6.4 — receives only method_tool records fitting none of #1-#12,#14


# ---------------------------------------------------------------------------
# §2 kind — CLOSED ENUM OF 8. Orthogonal to supercategory; method_tool records only.
# NOTE: `service` is NOT a kind (§6.2 — strip the wrapper; the wrapper is the
# `delivery` attribute, the kind is whatever is underneath).
# ---------------------------------------------------------------------------

KINDS: tuple[str, ...] = (
    "instrument",         # physical measuring/operating device (incl. therapy devices; capability routes to #9)
    "reagent",            # any administered/applied agent: chemicals, drugs, antibodies, cytokines, tracers, vectors, cell products
    "organism_or_cells",  # animals, cell lines, primary cultures, strains
    "assay",              # a measurement protocol/kit producing a readout
    "dataset",            # a body of data
    "software",           # runnable code/platforms that are NOT a packaged model artifact
    "method",             # procedures, algorithms-in-the-abstract, study/analysis designs
    "model",              # a named, loadable/runnable ML artifact (GPT-4, BERT, Llama 3.1-8B, CSPDarkNet)
)
KIND_SET: frozenset[str] = frozenset(KINDS)


# ---------------------------------------------------------------------------
# §3 Attributes — fields, NOT buckets. functional_category is RETIRED (never persisted).
# ---------------------------------------------------------------------------

DELIVERY_VALUES: frozenset[str] = frozenset({"in_house", "shared_core", "vendor"})   # service/facility wrapper -> attribute
PROVENANCE_VALUES: frozenset[str] = frozenset({"public", "proprietary"})             # dataset_public/proprietary -> attribute
LICENSE_VALUES: frozenset[str] = frozenset({"open_source", "commercial"})            # software_* -> attribute

# Default attribute block — null where a value is unknown/not-applicable.
def default_attributes() -> dict:
    """A fresh attribute block (§3) with all-null/false defaults."""
    return {
        "delivery": None,        # in_house | shared_core | vendor | None
        "provenance": None,      # public | proprietary | None
        "license": None,         # open_source | commercial | None
        "consumable": False,     # bool
        "rrid_candidate": False, # bool — durable external-identity signal; gates page-ELIGIBILITY (§5), not S-tier
    }


# ---------------------------------------------------------------------------
# §5 Salience — institution-level tier + how it was derived.
# ---------------------------------------------------------------------------

SALIENCE_TIERS: tuple[str, ...] = ("S", "A", "B", "C")
SALIENCE_TIER_SET: frozenset[str] = frozenset(SALIENCE_TIERS)
# `grounded` once computed from real signals (post-A2); else `llm_provisional`
# (seed-time), flagged for regrounding. `suppress_list` marks a deterministic
# force-to-C from the stopword denylist.
SALIENCE_BASES: frozenset[str] = frozenset({"grounded", "llm_provisional", "suppress_list"})
DEMOTED_TIER = "C"  # never a family, never an exemplar, excluded from ranking counts (§5/§8)


# ---------------------------------------------------------------------------
# §4 Projection: legacy tool_category -> kind (WEAK PRIOR ONLY; cardinal rule applies).
# `kind` is the prior; `attrs` are deterministic attribute hints; `note` carries
# the renormalization rule. A value of None for `kind` means "de-mix / strip the
# wrapper — the LLM must decide the kind from name+context" (§6.2/§6.3).
# ---------------------------------------------------------------------------

LEGACY_CATEGORY_PRIOR: dict[str, dict] = {
    "instrument":                {"kind": "instrument",        "attrs": {},                          "note": "by modality; therapy beams -> #9; crystallography/SPR/ITC/EPR -> #12"},
    "instrument_consumable":     {"kind": "instrument",        "attrs": {"consumable": True},         "note": "consumable=true"},
    "reagent":                   {"kind": "reagent",           "attrs": {},                          "note": "supercategory BY USE (§6.1)"},
    "cell_product":              {"kind": "reagent",           "attrs": {},                          "note": "by use: CAR-T/oncolytic -> #9; AAV/vector-as-tool -> #8"},
    "assay_kit":                 {"kind": "assay",             "attrs": {},                          "note": "by what it measures"},
    "animal_model":              {"kind": "organism_or_cells", "attrs": {},                          "note": "-> #7"},
    "invitro_biological_model":  {"kind": "organism_or_cells", "attrs": {},                          "note": "-> #7"},
    "dataset_public":            {"kind": "dataset",           "attrs": {"provenance": "public"},      "note": "-> #10"},
    "dataset_proprietary":       {"kind": "dataset",           "attrs": {"provenance": "proprietary"}, "note": "-> #10"},
    "software_commercial":       {"kind": "software",          "attrs": {"license": "commercial"},     "note": "by function"},
    "software_open_source":      {"kind": None,                "attrs": {"license": "open_source"},    "note": "software OR model — ML weights -> model"},
    "computational_method":      {"kind": None,                "attrs": {},                          "note": "de-mix (§6.3): method | model; modality-specific -> its modality"},
    "service":                   {"kind": None,                "attrs": {},                          "note": "strip wrapper (§6.2); kind = thing underneath; set delivery"},
    "facility":                  {"kind": None,                "attrs": {},                          "note": "strip wrapper (§6.2); kind = thing underneath; set delivery"},
    "core_facility":             {"kind": None,                "attrs": {},                          "note": "named as a core -> disposition=infrastructure; named as its tool -> route by capability + delivery"},
}


# ---------------------------------------------------------------------------
# Validators — fail loud on out-of-vocabulary values (closed sets).
# ---------------------------------------------------------------------------

def is_valid_disposition(value: str) -> bool:
    return value in DISPOSITIONS


def is_valid_supercategory(value: str) -> bool:
    return value in SUPERCATEGORY_IDS


def is_valid_kind(value: str) -> bool:
    return value in KIND_SET


def is_valid_tier(value: str) -> bool:
    return value in SALIENCE_TIER_SET


def validate_attributes(attrs: dict) -> list[str]:
    """Return a list of human-readable problems with an attribute block (§3); empty == valid."""
    problems: list[str] = []
    delivery = attrs.get("delivery")
    if delivery is not None and delivery not in DELIVERY_VALUES:
        problems.append(f"delivery={delivery!r} not in {sorted(DELIVERY_VALUES)} or null")
    provenance = attrs.get("provenance")
    if provenance is not None and provenance not in PROVENANCE_VALUES:
        problems.append(f"provenance={provenance!r} not in {sorted(PROVENANCE_VALUES)} or null")
    license_ = attrs.get("license")
    if license_ is not None and license_ not in LICENSE_VALUES:
        problems.append(f"license={license_!r} not in {sorted(LICENSE_VALUES)} or null")
    if not isinstance(attrs.get("consumable", False), bool):
        problems.append("consumable must be a bool")
    if not isinstance(attrs.get("rrid_candidate", False), bool):
        problems.append("rrid_candidate must be a bool")
    return problems


def validate_method_tool_record(rec: dict) -> list[str]:
    """Validate the closed-vocabulary fields of a method_tool record (§9); empty == valid.

    Only method_tool records carry supercategory/kind/salience (§0.5). Records with
    disposition infrastructure/excluded are validated separately (no capability fields).
    """
    problems: list[str] = []
    disp = rec.get("disposition")
    if not is_valid_disposition(disp):
        problems.append(f"disposition={disp!r} not in {list(DISPOSITIONS)}")
    if disp != CAPABILITY_DISPOSITION:
        # infrastructure/excluded must NOT carry capability fields (§9).
        for field in ("supercategory", "kind", "salience_tier"):
            if rec.get(field) is not None:
                problems.append(f"{disp} record must not carry {field} (got {rec.get(field)!r})")
        return problems
    if not is_valid_supercategory(rec.get("supercategory")):
        problems.append(f"supercategory={rec.get('supercategory')!r} not in the 13 closed values")
    if not is_valid_kind(rec.get("kind")):
        problems.append(f"kind={rec.get('kind')!r} not in the 8 closed values")
    if not is_valid_tier(rec.get("salience_tier")):
        problems.append(f"salience_tier={rec.get('salience_tier')!r} not in {list(SALIENCE_TIERS)}")
    problems.extend(validate_attributes(rec.get("attributes") or {}))
    return problems


def legacy_prior(tool_category: str | None) -> dict:
    """Return the §4 weak-prior block for a legacy tool_category tag.

    Unknown/blank tags get an empty prior (kind=None, no attrs) — the classifier
    then decides entirely from name+context (cardinal rule). Never a hard route.
    """
    if not tool_category:
        return {"kind": None, "attrs": {}, "note": "no legacy tag — decide from name+context"}
    return LEGACY_CATEGORY_PRIOR.get(
        tool_category.strip().lower(),
        {"kind": None, "attrs": {}, "note": f"unknown legacy tag {tool_category!r} — decide from name+context"},
    )
