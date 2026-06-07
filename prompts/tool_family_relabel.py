"""Method-family relabel prompt (docs/tool-classifier-spec.md §7.2).

At seed time a freshly minted family borrows its first member's raw tool name as a
placeholder label ("PET scanner", "Molecular docking"). §7.2 requires ONE
controlled, KIND-AWARE label per family that names the *capability* — the task,
the agent/material class, the drug/therapy modality, or the technique — and stays
in "how-space" so it does not collide with the MeSH-major Topics ("Subjects")
lens. A2 is where families finally have real members, so the relabel runs here.

This is a pure-text LLM pass over family metadata (id, supercategory,
dominant_kind, member display names) — no abstracts, no per-tool calls. The call
is behind the same injected ``call_json`` seam the classifier uses, so the live
seam is Bedrock Sonnet → OpenAI gpt-5.x fallback and the module unit-tests with a
stub. The vocabulary (13 supercategories, 8 kinds) is imported from
``pipeline_tools.vocab`` so the prompt can never drift from the frozen sets.
"""

from __future__ import annotations

import json

from pipeline_tools import vocab

# Members shown per family in the prompt — enough to convey the cluster's identity
# without paying for the long tail of a large family.
MAX_MEMBERS_SHOWN = 12

_SUPERCATEGORY_LINES = "\n".join(
    f"  - {s['id']}: {s['label']}" for s in vocab.SUPERCATEGORIES
)


RELABEL_SYSTEM_PROMPT = f"""You name METHOD FAMILIES for an institutional tool/method taxonomy. Each family is a \
cluster of related research tools/methods within one supercategory. Your job: give each family ONE \
short, controlled display label that names the CAPABILITY the cluster represents — the family's \
"how", not the biology it studies.

Why this matters: these labels sit in a "Methods" lens shown next to a separate MeSH-based "Subjects" \
lens. A label that names a disease or organ DUPLICATES the Subjects lens and is wrong here. Stay in \
how-space.

The 13 supercategories:
{_SUPERCATEGORY_LINES}

KIND-AWARE labeling rules (use the family's dominant_kind):
  - method / model / software  -> name the TASK: "evidence summarization", "risk prediction", \
"report generation", "LLM clinical evaluation", "structure-based virtual screening".
  - reagent / organism_or_cells -> name the AGENT or MATERIAL CLASS, NEVER the biology studied: \
sevoflurane -> "inhalational anesthetics"; SH-SY5Y -> "neuroblastoma cell models"; TDF -> \
"antiretrovirals"; PGE2 -> "lipid-mediator probes".
  - therapeutics_interventions -> the DRUG CLASS or THERAPY MODALITY, never the disease: \
"antiretrovirals", "anti-PD-1 immunotherapy", "external-beam radiotherapy", "catheter ablation".
  - structural_biophysical -> the TECHNIQUE: "macromolecular crystallography", \
"binding thermodynamics (ITC/SPR)".
  - Fall back to a modality/dataset name ONLY where a task-name would rhyme with a MeSH heading \
("clinical text models", not "biomedical NLP").

CONSTRAINTS:
  - 1-5 words, lower-case except proper nouns/acronyms; a noun phrase, not a sentence.
  - Describe the WHOLE cluster, not just the first member. If members are heterogeneous, pick the \
label that best covers the dominant capability.
  - Never name a disease, organ, or studied phenomenon. Never use the word "service"/"facility"/"core".
  - Output the label only — no trailing punctuation, no explanation in the label.

Respond with VALID JSON ONLY, no markdown:
{{"families": [{{"family_id": "<id verbatim>", "label": "<controlled label>", \
"confidence": "high|low"}}]}}
Return one object per input family, family_id verbatim. A "low" confidence flags the family for review."""


def _family_payload(fam: dict) -> dict:
    members = fam.get("member_display_names", []) or []
    extra = len(members) - MAX_MEMBERS_SHOWN
    shown = members[:MAX_MEMBERS_SHOWN]
    return {
        "family_id": fam["family_id"],
        "supercategory": fam.get("supercategory"),
        "dominant_kind": fam.get("dominant_kind"),
        "current_label": fam.get("label"),
        "members": shown + ([f"... (+{extra} more)"] if extra > 0 else []),
    }


def build_relabel_user_message(families: list[dict]) -> str:
    """Render a batch of family records as the user turn for a relabel call."""
    items = [_family_payload(f) for f in families]
    payload = json.dumps(items, ensure_ascii=False, separators=(", ", ": "))
    return (
        f"Relabel these {len(items)} method families. Name the capability (how-space), "
        f"kind-aware, never the disease/organ studied. Return one object per family, "
        f"family_id verbatim:\n{payload}"
    )
