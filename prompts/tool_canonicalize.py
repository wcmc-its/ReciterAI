"""
Static Sonnet prompts for Phase 8 / Axis 2 — A1 seed canonicalization.

Takes raw research-tool names (POC `tools_to_canonicalize.json` or the
`reciterai_tools` RDS table) and canonicalizes them into the `TOOL#` schema
defined in `docs/tools-producer-model.md` §#8: distinct canonical entries with
kind, functional_category, a curated supercategory, a provisional salience tier
(S/A/B/C), merged aliases, and provenance.

Design decisions honored here (see docs/tools-producer-model.md):
  #5  vocabulary = hybrid; resources flagged `rrid_candidate` for later RRID
      resolution, everything else `source = wcm_curated` (RRID resolver is a
      separate bounded task, not part of A1).
  #7  parent_tool_ids[] stays EMPTY in A1 — method families are discovered
      globally in a later pass (step E). A soft `method_family_hint` is allowed
      but is non-authoritative.
  #8  output fields match the canonical catalog entry.
  Salience tier in A1 is LLM-provisional (`salience_tier_basis="llm_provisional"`);
  it is recalibrated against cross-corpus frequency after A2 (deferred-with-trigger).

No boto3 calls, no AWS calls, no file I/O — safe to import anywhere.
"""

import json

# #8 schema `kind` enum.
KINDS = [
    "method",
    "instrument",
    "software",
    "reagent",
    "dataset",
    "model_system",
    "clinical_tool",
]

# Tier-1 curated supercategories (stable, institution-wide). The browser's top
# facets; ≙ the curated ~67 topics on Axis 1. A1 assigns each canonical tool to
# exactly one. Method families (tier 2) are discovered later, not here.
SUPERCATEGORIES = [
    {"id": "imaging_image_analysis", "label": "Imaging & image analysis"},
    {"id": "genomics_sequencing", "label": "Genomics & sequencing"},
    {"id": "computational_statistical", "label": "Computational & statistical methods"},
    {"id": "mass_spec_proteomics", "label": "Mass spec & proteomics"},
    {"id": "animal_cell_models", "label": "Animal & cell models"},
    {"id": "molecular_biochem_reagents", "label": "Molecular & biochemical reagents"},
    {"id": "microscopy_histology", "label": "Microscopy & histology"},
    {"id": "clinical_instruments_assays", "label": "Clinical instruments & assays"},
    {"id": "datasets_cohorts", "label": "Datasets & cohorts"},
    {"id": "software_informatics", "label": "Software & informatics platforms"},
    {"id": "other", "label": "Other / uncategorized"},
]

# Kinds that can plausibly resolve to an RRID/SciCrunch ID later (#5). A1 only
# FLAGS candidates; it does not resolve them.
RRID_CANDIDATE_KINDS = {"software", "reagent", "model_system"}


def _supercategory_block() -> str:
    return "\n".join(f"  - {s['id']}: {s['label']}" for s in SUPERCATEGORIES)


CANONICALIZE_SYSTEM_PROMPT = f"""You are an expert biomedical research-methods analyst building a CANONICAL \
catalog of the distinctive tools and methods used at an academic medical center (Weill Cornell Medicine).

You receive a batch of RAW tool/method names extracted from publications, each with a coarse \
`tool_category` and a `pub_count`. Many raw names are spelling/format/acronym variants of the SAME \
underlying capability (e.g. "Magnetic resonance imaging (MRI) scanner", "3 Tesla MRI scanner", \
"fMRI scanner" → one canonical "MRI scanner").

Your job: collapse the raw names into DISTINCT canonical entries.

DISTINCTIVENESS — the central judgement:
- A distinctive tool is a specialized capability a researcher would search for and that narrows the \
field (patch-clamp, cryo-EM, scRNA-seq, a named cohort, an invented algorithm).
- COMMODITY items that nearly every lab uses are NOT distinctive: bibliographic databases \
(PubMed/Embase/ClinicalTrials.gov), generic statistics (t-test, regression, SPSS-grade stats), \
commodity bench techniques (PCR, Western blot, ELISA, generic cell culture, routine IHC), and bare \
"microscopy"/"imaging". These still get a canonical entry but MUST be tier "C" (suppressed).

For EACH canonical entry, assign:
- canonical_tool_id: a stable lowercase slug, NO prefix (e.g. "mri_scanner", "cryo_em", "scrna_seq").
- display_name: the clean human-readable name (render-only).
- kind: one of {KINDS}.
- functional_category: a short lowercase slug naming the function (e.g. "imaging", "sequencing", \
"mass_spectrometry", "flow_cytometry", "statistics").
- supercategory: EXACTLY one id from this curated list:
{_supercategory_block()}
- salience_tier: "S" (signature/inventive, strongly identifies a lab), "A" (notable, distinctive, \
recurring), "B" (real but low-distinctiveness/generic), or "C" (commodity — see suppress list; never shown).
- aliases: the raw_name strings from this batch that map into this entry (include every variant).
- method_family_hint: OPTIONAL short free-text guess at the method family this belongs to \
(non-authoritative; families are discovered later). Use "" if unsure.
- source_confidence: your confidence 0.0-1.0 that this is a correct, distinct canonical entry.
- description: ONE short sentence, render-only.

RULES:
1. Every raw_name in the batch must appear in exactly one entry's aliases.
2. Merge variants aggressively; split only genuinely different capabilities.
3. Be conservative about tier S — reserve it for clearly inventive/signature capabilities.
4. Apply the suppress list (provided in the user message) → those are tier "C".
5. Output PURE JSON, no markdown fences, no commentary. Must parse as valid JSON unmodified.

OUTPUT FORMAT:
{{"tools": [{{"canonical_tool_id": "slug", "display_name": "...", "kind": "...", \
"functional_category": "...", "supercategory": "...", "salience_tier": "A", \
"aliases": ["raw name 1", "raw name 2"], "method_family_hint": "", \
"source_confidence": 0.9, "description": "..."}}]}}"""


CONSOLIDATE_SYSTEM_PROMPT = f"""You are consolidating a CANONICAL tool/method catalog assembled from \
several independent batches. Because batches were processed separately, the SAME underlying tool may \
appear as more than one canonical entry (e.g. "mri_scanner" and "magnetic_resonance_imaging").

Your job: merge true duplicates into a single canonical entry and finalize each entry's fields.

For each MERGED entry:
- Keep ONE stable canonical_tool_id (prefer the clearest slug).
- Union the aliases across the merged entries (deduplicate).
- Keep the most accurate kind / functional_category / supercategory (exactly one supercategory id from \
the same curated list used during canonicalization).
- Finalize salience_tier (S/A/B/C) using the same distinctiveness rubric; commodity items stay "C".
- Keep the highest source_confidence among merged entries.
- Keep a single short description.

RULES:
1. Do NOT drop any alias — every input alias must survive in some entry.
2. Only merge entries that are genuinely the same capability. When unsure, keep them separate.
3. Output PURE JSON, no markdown fences, no commentary.

OUTPUT FORMAT (same entry shape as the canonicalization pass):
{{"tools": [{{"canonical_tool_id": "slug", "display_name": "...", "kind": "...", \
"functional_category": "...", "supercategory": "...", "salience_tier": "A", \
"aliases": [...], "method_family_hint": "", "source_confidence": 0.9, "description": "..."}}]}}"""


def BUILD_CANONICALIZE_USER_MESSAGE(raw_tools: list, suppress_terms: list) -> str:
    """
    Build the user message for one canonicalization batch.

    Args:
        raw_tools: list of dicts, each {raw_name, tool_category, pub_count}.
        suppress_terms: list of commodity terms/categories to force to tier C.

    Returns:
        User message string. Pure function — no I/O.
    """
    raw_json = json.dumps(
        [
            {
                "raw_name": t.get("raw_name", ""),
                "tool_category": t.get("tool_category", ""),
                "pub_count": t.get("pub_count", 0),
            }
            for t in raw_tools
        ],
        indent=None,
        separators=(", ", ": "),
    )
    suppress_json = json.dumps(suppress_terms, indent=None, separators=(", ", ": "))
    return (
        f"Suppress list (force these and close variants to tier C):\n{suppress_json}\n\n"
        f"Raw tool names to canonicalize ({len(raw_tools)} in this batch):\n{raw_json}\n\n"
        f"Canonicalize now. Remember: every raw_name must appear in exactly one entry's aliases."
    )


def BUILD_CONSOLIDATE_USER_MESSAGE(entries: list) -> str:
    """
    Build the user message for the cross-batch consolidation pass.

    Args:
        entries: list of canonical entry dicts from the batch passes.

    Returns:
        User message string. Pure function — no I/O.
    """
    entries_json = json.dumps(entries, indent=None, separators=(", ", ": "))
    return (
        f"Canonical entries to consolidate ({len(entries)} total, may contain cross-batch "
        f"duplicates):\n{entries_json}\n\n"
        f"Merge true duplicates and finalize. Do not drop any alias."
    )
