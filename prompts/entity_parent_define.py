"""Entity-parent DESCRIPTOR prompt (#1166, plan decision D-desc).

The Surface-B "all cell lines" directory nests differentiation forms of one cell
line under a shared parent (e.g. ``3T3-L1 adipocytes`` + ``3T3-L1 preadipocytes``
under ``3T3-L1``). The header needs a short human descriptor — ``"3T3-L1 · mouse
fibroblast line · 2 forms"`` — but no §7 field supplies the "mouse fibroblast
line" part. Per the locked decision, ReciterAI emits it: a pure-text LLM pass
over the parent label + its member display names (no abstracts, no per-pub
calls), behind the same injected ``call_json`` seam as ``define_families``.

The descriptor is RENDER-ONLY (the #879 D-19 posture): nothing downstream
re-consumes it as LLM/embedding/retrieval context. It is deliberately short and
TYPE-framed — the lineage/species/cell-type of the line — never the biology it is
used to study (that would duplicate the MeSH Subjects lens).
"""

from __future__ import annotations

import json

# Members shown per parent — enough to convey the lineage without paying for a
# long tail. Parents are small (differentiation forms of one line), so this is
# rarely binding.
MAX_MEMBERS_SHOWN = 8

# Soft word budget named in the prompt; the validator (entities.define_entity_parents)
# enforces a hard cap.
MAX_DESCRIPTOR_WORDS = 10


ENTITY_PARENT_DEFINE_PROMPT = f"""You write one very short DESCRIPTOR for each CELL-LINE GROUP. A group is a set of \
differentiation / passage / form variants of a single named cell line (e.g. "3T3-L1 adipocytes" and "3T3-L1 \
preadipocytes" are two forms of the line "3T3-L1"). You are given the line's name and its member forms.

Your job: in 3-10 words, say WHAT KIND OF CELL LINE this is — its species and lineage / cell type — so a reader \
scanning a directory understands the line at a glance. Examples of the target voice:
  - "3T3-L1" + [adipocytes, preadipocytes]  -> "mouse fibroblast line"
  - "HEK293T" + [cells]                      -> "human embryonic kidney line"
  - "Jurkat" + [cells]                        -> "human T-lymphocyte leukemia line"

CONSTRAINTS:
  - 3 to ~{MAX_DESCRIPTOR_WORDS} words, a noun phrase. Plain prose only — no markdown, no trailing punctuation.
  - Describe the LINE's identity (species + lineage/cell type), NOT the biology it is used to study, NOT the \
forms themselves, NOT a disease/organ as the subject.
  - Do NOT repeat the words "cell line" / "cell lines" (the UI already says that). Do NOT add marketing or \
efficacy language. Do NOT name a MeSH heading.
  - If you cannot identify the line confidently from the name + forms, return an empty descriptor "" with \
"confidence": "low" — it is left blank rather than guessed.

Respond with VALID JSON ONLY, no markdown:
{{"parents": [{{"parent_entity_id": "<id verbatim>", "descriptor": "<3-10 word noun phrase>", \
"confidence": "high|low"}}]}}
Return one object per input group, parent_entity_id verbatim."""


def _parent_payload(parent: dict) -> dict:
    members = parent.get("member_display_names", []) or []
    extra = len(members) - MAX_MEMBERS_SHOWN
    shown = members[:MAX_MEMBERS_SHOWN]
    return {
        "parent_entity_id": parent["parent_entity_id"],
        "line": parent.get("parent_label"),
        "forms": shown + ([f"... (+{extra} more)"] if extra > 0 else []),
    }


def build_entity_parent_message(parents: list[dict], *, prior_failures: dict | None = None) -> str:
    """Render a batch of parent groups as the user turn for a descriptor call."""
    items = [_parent_payload(p) for p in parents]
    payload = json.dumps(items, ensure_ascii=False, separators=(", ", ": "))
    head = (
        f"Describe these {len(items)} cell-line groups. For each, give a 3-10 word noun phrase naming the "
        f"line's species + lineage/cell type, never the biology studied. Return one object per group, "
        f"parent_entity_id verbatim:"
    )
    if prior_failures:
        defects = "; ".join(f"{pid}: {reason}" for pid, reason in prior_failures.items())
        head += (
            "\nYour previous descriptors for these groups were rejected — fix exactly these defects and keep "
            f"within the constraints: {defects}."
        )
    return f"{head}\n{payload}"
