"""Method-family DEFINE prompt (#879 pivot).

After §7.2 relabeling gives each family one controlled, capability-framed label, this
pass generates a short (1-2 sentence) human-readable DEFINITION for the family — "what
this method family is", in how-space — for downstream display (SPS Methods pages,
chatbot). It is the ReciterAI-owned replacement for the abandoned families->MeSH
scope-note mapping (which capped at ~52% coverage and produced adjacent-concept
definitions): ReciterAI owns the family taxonomy and already labels it with an LLM, so
the definition belongs next to the source of truth. Methods stays deliberately
orthogonal to MeSH — this prompt never maps to a MeSH heading.

Like the relabel prompt this is a pure-text LLM pass over family metadata (label,
supercategory, dominant_kind, member display names) — no abstracts, no per-tool calls —
behind the same injected ``call_json`` seam (Bedrock Sonnet -> OpenAI fallback). The
member display names are highly informative (MRgFUS / FUS-BBBO -> "focused ultrasound
therapy"), so they carry most of the grounding. The vocabulary is imported from
``pipeline_tools.vocab`` so the prompt can never drift from the frozen supercategory set.

D-19 LOCKED: the generated definition is RENDER-ONLY. Nothing downstream re-consumes it as
LLM/embedding/retrieval context — this pass produces it and stops.
"""

from __future__ import annotations

import json

from pipeline_tools import vocab

# Members shown per family — enough to convey the cluster's identity without paying for
# the long tail of a large family. Mirrors the relabel prompt's cap exactly.
MAX_MEMBERS_SHOWN = 12

# Soft word budget named in the prompt; the validator (define_families) enforces a hard cap.
MAX_DEFINITION_WORDS = 40

_SUPERCATEGORY_LINES = "\n".join(
    f"  - {s['id']}: {s['label']}" for s in vocab.SUPERCATEGORIES
)


DEFINE_SYSTEM_PROMPT = f"""You write one short DEFINITION for each METHOD FAMILY in an institutional tool/method \
taxonomy. A family is a cluster of related research tools/methods within one supercategory; it already has a \
controlled capability label. Your job: say WHAT THE FAMILY IS in plain language — the capability or technique \
it represents — so a reader on a "Methods" page understands it at a glance.

Why this matters: these definitions sit in a "Methods" lens shown next to a separate MeSH-based "Subjects" \
lens. A definition that describes a disease, organ, or biological phenomenon DUPLICATES the Subjects lens and \
is wrong here. Stay in how-space (capability / technique / agent class / modality), never the biology studied.

The {len(vocab.SUPERCATEGORIES)} supercategories:
{_SUPERCATEGORY_LINES}

KIND-AWARE definition rules (use the family's dominant_kind):
  - method / model / software       -> name the TASK the family performs and how (e.g. "Computational methods \
that screen candidate molecules against a protein structure to rank likely binders.").
  - reagent / organism_or_cells     -> name the AGENT or MATERIAL CLASS and its role as a research tool, NEVER \
the biology it is used to study (e.g. "A class of inhalational anesthetic agents used to induce and maintain \
anesthesia in experimental and clinical models.").
  - therapeutics_interventions      -> the DRUG CLASS or THERAPY MODALITY and how it is delivered, never the \
disease treated.
  - structural_biophysical          -> the experimental TECHNIQUE and what it measures.

CONSTRAINTS:
  - 1-2 sentences, at most ~{MAX_DEFINITION_WORDS} words. A neutral, institutional voice.
  - Describe the WHOLE cluster (use the members and label), not just the first member.
  - NO clinical-efficacy or outcome claims ("improves survival", "effective for"). NO marketing language \
("cutting-edge", "powerful", "state-of-the-art", "gold standard"). NO disease/organ as the subject of the \
sentence. Do not name a specific MeSH heading.
  - Plain prose only — no markdown, no bullet, no trailing label.

Respond with VALID JSON ONLY, no markdown:
{{"families": [{{"family_id": "<id verbatim>", "definition": "<1-2 sentence definition>", \
"confidence": "high|low"}}]}}
Return one object per input family, family_id verbatim. Use "low" confidence when the members are too \
heterogeneous or sparse to define the capability confidently — it flags the family for review."""


def _family_payload(fam: dict) -> dict:
    members = fam.get("member_display_names", []) or []
    extra = len(members) - MAX_MEMBERS_SHOWN
    shown = members[:MAX_MEMBERS_SHOWN]
    return {
        "family_id": fam["family_id"],
        "supercategory": fam.get("supercategory"),
        "dominant_kind": fam.get("dominant_kind"),
        "label": fam.get("label"),
        "members": shown + ([f"... (+{extra} more)"] if extra > 0 else []),
    }


def build_define_user_message(families: list[dict], *, prior_failures: dict | None = None) -> str:
    """Render a batch of family records as the user turn for a define call.

    ``prior_failures`` ({family_id: reason}) re-prompts families whose first definition
    failed validation, naming the specific defect so the model can correct it.
    """
    items = [_family_payload(f) for f in families]
    payload = json.dumps(items, ensure_ascii=False, separators=(", ", ": "))
    head = (
        f"Define these {len(items)} method families. Say what each family IS in how-space, "
        f"kind-aware, 1-2 sentences, <= ~{MAX_DEFINITION_WORDS} words, no efficacy/marketing claims, "
        f"never the disease/organ studied. Return one object per family, family_id verbatim:"
    )
    if prior_failures:
        defects = "; ".join(f"{fid}: {reason}" for fid, reason in prior_failures.items())
        head += (
            "\nYour previous definitions for these families were rejected — fix exactly these defects "
            f"and keep within the constraints: {defects}."
        )
    return f"{head}\n{payload}"
