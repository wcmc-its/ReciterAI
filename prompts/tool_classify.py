"""Prompts for the tool/method classifier (docs/tool-classifier-spec.md §0.5-§6).

One LLM pass per batch produces, for each mention: a ``disposition`` (§0.5 gate,
run FIRST), and for ``method_tool`` records a ``kind`` (§2), ``supercategory``
(§1), and ``attributes`` (§3) — following the §6 routing rules. The closed-set
*values* are imported from ``pipeline_tools.vocab`` so the prompt can never drift
from the frozen vocabulary; the routing *cues* are editorial and live here.

Cardinal rule (spec preamble, the source of every past failure): read
``raw_name`` + ``context`` and decide independently. The incoming
``tool_category`` tag is an inconsistent grab-bag — a weak prior only (§4),
never the route.
"""

from __future__ import annotations

import json

from pipeline_tools import vocab


def _supercategory_menu() -> str:
    """The 13 closed supercategory ids + labels + one routing cue each (§1/§6)."""
    cues = {
        "imaging_image_analysis": "clinical/macro-scale image as primary output + imaging analysis (MRI/CT/PET/echo/X-ray, OCT, QSM). NOT therapy beams (#9), NOT structural instruments (#12).",
        "microscopy_histology": "cellular/tissue micro-scale imaging & staining (confocal, two-photon, MIBI, Opal IF, FISH, cellular EM). cryo-EM for structure determination -> #12.",
        "genomics_sequencing": "sequencing platforms + delivery services (10x Chromium, Nanopore, scRNA/ATAC/Ribo-seq, CRISPR screen libraries).",
        "mass_spec_proteomics": "LC-MS/MS, ICP-MS, SILAC, proteomic workflows.",
        "computational_statistical": "GENERAL algorithms/models/stats/study designs (CNN/SVM/boosting, MD simulation, regression, causal inference, named ML models). Modality-specific computation routes to its modality (QSM -> #1).",
        "clinical_instruments_assays": "bedside/clinical measurement & lab diagnostics (ECG/EEG/EMG, echo, endoscopes, ELISA/PSA/HCV assays, clinical rating scales UPDRS/mRS, stents/sheaths/catheters).",
        "animal_cell_models": "transgenic/KO mice, zebrafish, NHP, iPSC lines, patient-derived cultures, cell lines, commensal strains.",
        "molecular_biochem_reagents": "agents used as research PROBES/substrates (antibodies/stains, cytokines, tracers, recombinant proteins, CRISPR/Cas9 systems, AAV-as-tool).",
        "therapeutics_interventions": "role is to TREAT/intervene — drugs, biologics, cell therapies (CAR-T), oncolytic viruses, AND therapy devices/procedures (linac, therapeutic US, ablation). Placebo/comparators land here (forced to C).",
        "datasets_cohorts": "bodies of data — claims/EHR (MarketScan, INSIGHT, Premier, Epic Cosmos), cohorts/registries (TCGA, SRTR, GWAS sumstats), bibliographic DBs (PubMed, EMBASE).",
        "software_informatics": "GENERAL-function software with no modality home (REDCap, R, dashboards, planning/analytics).",
        "structural_biophysical": "macromolecular structure determination & molecular-interaction biophysics — X-ray crystallography, cryo-EM (structure), NMR (structural), SPR, ITC, EPR.",
        "other": "GATED REMAINDER — only an in-domain method_tool fitting none of the above. Not a catch-all (the gate already removed errors/infrastructure).",
    }
    lines = []
    for s in vocab.SUPERCATEGORIES:
        lines.append(f"  - {s['id']}: {s['label']} — {cues.get(s['id'], '')}")
    return "\n".join(lines)


def _kind_menu() -> str:
    cues = {
        "instrument": "physical measuring/operating device (incl. therapy devices).",
        "reagent": "any administered/applied agent: chemicals, drugs, antibodies, cytokines, tracers, vectors, cell products.",
        "organism_or_cells": "animals, cell lines, primary cultures, strains.",
        "assay": "a measurement protocol/kit producing a readout.",
        "dataset": "a body of data.",
        "software": "runnable code/platforms that are NOT a packaged model artifact.",
        "method": "procedures, algorithms-in-the-abstract, study/analysis designs (bare 'CNN').",
        "model": "a NAMED loadable/runnable ML artifact (GPT-4, BERT, Llama 3.1-8B, CSPDarkNet).",
    }
    return "\n".join(f"  - {k}: {cues.get(k, '')}" for k in vocab.KINDS)


CLASSIFY_SYSTEM_PROMPT = f"""You are an expert biomedical research-methods analyst classifying extracted \
research-tool mentions for an institutional tool/method taxonomy. Work mention by mention.

CARDINAL RULE: decide from the tool's NAME and any CONTEXT, using your domain knowledge. The \
`tool_category` tag attached to each mention is an INCONSISTENT prior only — never route off it alone.

STEP 1 — DISPOSITION (assign to every mention; one of {list(vocab.DISPOSITIONS)}):
  - method_tool   = a genuine methodological capability (instrument, reagent, organism, assay, dataset, \
software, method, model). DEFAULT.
  - infrastructure = real & publication-attested but ORGANIZATIONAL, not methodological — pure \
coordinating centers, data-management cores, trial networks (e.g. "Stroke Trials Network RCC", \
"University of Colorado DCC" named AS A CORE). Canonicalized but never given a supercategory/kind/family.
  - excluded      = not a research tool at all (Amazon Fresh, Xiaomi Mi Band, SEO tools, generic office \
software). Dropped from the taxonomy.
A real-but-undistinctive tool (a gel rig) is method_tool, NOT excluded — that demotion is salience, not disposition.

STEP 2 — only for method_tool, assign KIND (one of {list(vocab.KINDS)}):
{_kind_menu()}
  model vs method: a NAMED loadable artifact -> model; a technique in the abstract -> method.
  `service` is NOT a kind — strip the wrapper; the kind is whatever is underneath, and the wrapper \
becomes the `delivery` attribute.

STEP 3 — only for method_tool, assign SUPERCATEGORY (exactly one of the 13 by CAPABILITY):
{_supercategory_menu()}

ROUTING RULES (§6):
  - reagent/cell_product BY USE: intervention administered/studied -> therapeutics_interventions; \
a probe to study something else -> molecular_biochem_reagents. (Pembrolizumab dosed in a trial -> \
therapeutics; anti-PD-1 antibody probing a pathway -> reagents.)
  - de-mix computational tags: named ML artifact -> kind=model, computational_statistical; \
modality-specific reconstruction (QSM) -> kind=method, route to the MODALITY (imaging); clinical \
scales/criteria (UPDRS-III, Modified Rankin, WHO) -> kind=method, clinical_instruments_assays; \
study/consensus designs (Delphi, Simon two-stage, GRADE) -> computational_statistical or demote.
  - structure determination -> structural_biophysical (X-ray crystallography, SPR, ITC, EPR, cryo-EM \
for near-atomic structure); conventional cellular/tissue EM stays microscopy_histology.
  - therapy devices (linear accelerator, therapeutic ultrasound, ablation) -> kind=instrument, \
supercategory therapeutics_interventions (kind/capability are orthogonal).
  - 'other' is a gated remainder: use ONLY when nothing in #1-#12 fits.

STEP 4 — only for method_tool, assign ATTRIBUTES (fields, not buckets):
  - delivery: one of {sorted(vocab.DELIVERY_VALUES)} or null (in_house/shared_core/vendor — the service wrapper).
  - provenance: one of {sorted(vocab.PROVENANCE_VALUES)} or null (datasets).
  - license: one of {sorted(vocab.LICENSE_VALUES)} or null (software).
  - consumable: true/false.
  - rrid_candidate: true/false — does this have (or deserve) a durable external identifier (RRID)? \
True for most antibodies, cell lines, organisms, software/models, plasmids; false for generic instruments \
and one-off methods.

Set confidence="low" when the supercategory is genuinely ambiguous (it routes to a human review queue).
For infrastructure/excluded mentions, set kind/supercategory/attributes to null.

Respond with VALID JSON ONLY, no markdown:
{{"classifications": [{{"raw_name": "<verbatim>", "disposition": "...", "kind": "...|null", \
"supercategory": "...|null", "attributes": {{"delivery": null, "provenance": null, "license": null, \
"consumable": false, "rrid_candidate": false}}, "confidence": "high|low", "notes": "<short rationale>"}}]}}
"""


def build_classify_user_message(batch: list[dict]) -> str:
    """Render a batch of mentions as the user turn for one classification call.

    Each mention carries raw_name (verbatim), the weak-prior tool_category tag,
    optional context, and pub_count (a rough prominence hint, not a routing input).
    """
    items = [
        {
            "raw_name": m.get("raw_name", ""),
            "tool_category_hint": m.get("tool_category"),
            "context": m.get("context") or None,
            "pub_count": m.get("pub_count"),
        }
        for m in batch
    ]
    payload = json.dumps(items, ensure_ascii=False, separators=(", ", ": "))
    return (
        f"Classify these {len(items)} research-tool mention(s). Decide each from its name and context, "
        f"not its tool_category_hint. Return one classification object per mention, raw_name verbatim:\n{payload}"
    )
