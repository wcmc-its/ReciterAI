"""Family-label broadening prompt (docs/tool-classifier-spec.md §7.2 — altitude fix).

The first A2 family pass minted a near-singleton family per tool, so the §7.2
relabel named each by its SPECIFIC member ("anti-IL-23 monoclonal antibodies",
"3T MRI acquisition", "phase II clinical trial datasets") — the wrong altitude
for a browsable Methods lens. This pass takes those specific labels and lifts each
to the broad CAPABILITY CLASS the §7.2 rules actually call for, so families that
share a class can merge.

The capability class is semantic knowledge the LLM has but a name embedding does
not (nivolumab and pembrolizumab are both anti-PD-1 immunotherapy though their
names are dissimilar), which is why this is an LLM pass, not a clustering pass.

Inputs are the existing labels (short, cheap) plus their supercategory; outputs
are one broad class per label. Kept CONSISTENT within a batch by asking for a
small covering set; cross-batch variants are reconciled downstream by merging the
class DESCRIPTIONS (which embed well, unlike the tool names).
"""

from __future__ import annotations

import json

from pipeline_tools import vocab

_SUPERCATEGORY_LABELS = vocab.SUPERCATEGORY_LABELS


BROADEN_SYSTEM_PROMPT = """You group specific method/tool labels into BROAD method-family classes for an \
institutional Methods lens (shown beside a separate MeSH "Subjects" lens, so stay in how-space — never \
name a disease, organ, or studied phenomenon).

For EACH input label, output its broad family class — the shared CAPABILITY, one altitude up from the \
specific instance, KIND-AWARE:
  - reagents / drugs / therapeutics -> the DRUG CLASS or THERAPY MODALITY: anti-IL-6/IL-23 mAb -> \
"anti-interleukin antibody therapeutics"; nivolumab/pembrolizumab -> "anti-PD-1 immunotherapy"; \
sevoflurane -> "inhalational anesthetics"; AAV9 vector -> "AAV gene-therapy vectors".
  - organisms / cells -> the MATERIAL CLASS: "C57BL/6 mouse model" -> "inbred mouse models"; \
"SH-SY5Y" -> "neuroblastoma cell lines".
  - methods / models / software -> the TASK / TECHNIQUE CLASS: "3T MRI acquisition"/"4D MRI" -> \
"MRI acquisition"; "multivariable logistic regression" -> "regression modeling"; "phase II RCT design" -> \
"randomized trial design".
  - datasets -> the DATA-TYPE CLASS: "phase I/II/III clinical trial datasets" -> "clinical trial datasets"; \
"SEER registry" -> "cancer registry datasets".
  - instruments / assays -> the MODALITY / MEASUREMENT CLASS.

Rules:
  - Broad but DISTINCT: a class should cover several related labels, but never merge genuinely different \
capabilities (anti-PD-1 immunotherapy is NOT the same class as anti-IL-6 anti-inflammatory therapy).
  - Aim for a SMALL covering set across the batch — reuse the SAME class string for labels that share a \
class (consistency matters; identical wording lets them merge).
  - 1-5 words plus an optional parenthetical; lower-case except proper nouns/acronyms; no trailing \
punctuation; never the word "service"/"facility"/"core".

Respond with VALID JSON ONLY, no markdown:
{"assignments": [{"label": "<verbatim input label>", "family_class": "<broad class>"}]}
Return one object per input label, label verbatim."""


def build_broaden_user_message(labels: list[str], supercategory: str) -> str:
    """Render a batch of specific labels (within one supercategory) for broadening."""
    sc_label = _SUPERCATEGORY_LABELS.get(supercategory, supercategory)
    payload = json.dumps(labels, ensure_ascii=False, separators=(", ", ": "))
    return (
        f"Supercategory: {sc_label}.\n"
        f"Assign a broad capability-family class to each of these {len(labels)} labels "
        f"(reuse the same class for labels that share one):\n{payload}"
    )
