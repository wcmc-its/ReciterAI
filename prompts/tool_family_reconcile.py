"""Family-class reconciliation prompt (docs/tool-classifier-spec.md §7.2 — vocabulary canonicalization).

The broaden pass (``tool_family_broaden``) runs in many independent parallel
batches with no shared vocabulary, so it mints near-unique labels for the SAME
capability across batches ("transgenic mouse models" / "genetically engineered
mouse models"; "anti-PD-1 immunotherapy" / "anti-PD-1 checkpoint immunotherapy").
A fixed embedding-cosine consolidation cannot fix this: the wording variants that
SHOULD merge and the sibling classes that must NOT ("anti-PD-1" vs "anti-PD-L1",
"anti-CD20" vs "anti-CD38") differ by the same one-token distance, so no threshold
separates them.

Deciding whether two labels name the same CAPABILITY is a semantic judgment, so
this is an LLM pass. Given the canonical classes already established for a
supercategory plus a batch of new labels, the model maps each new label to an
existing canonical (same capability) or mints a new one (genuinely distinct) —
match-or-mint, with the LLM as the arbiter the embedding threshold could not be.
"""

from __future__ import annotations

import json

from pipeline_tools import vocab

_SUPERCATEGORY_LABELS = vocab.SUPERCATEGORY_LABELS


RECONCILE_SYSTEM_PROMPT = """You canonicalize method/tool FAMILY-CLASS labels for an institutional Methods lens \
(shown beside a separate MeSH "Subjects" lens, so stay in how-space — a class names a method or capability, \
never a disease, organ, or studied phenomenon).

You are given a SUPERCATEGORY, the canonical classes ALREADY established for it, and a batch of new candidate \
labels. For EACH new label decide: does it name the SAME capability class as an existing canonical (then reuse \
that canonical VERBATIM), or a genuinely DIFFERENT capability (then mint a new canonical)?

MERGE (reuse an existing canonical) when labels differ only by:
  - wording, word order, hyphenation, or plurals ("coculture" / "co-culture"; "human immortalized cell lines" \
/ "immortalized human cell lines");
  - synonyms for the same thing ("mouse" / "murine" / "rodent"; "engineered" / "modified" / "transgenic"; \
"therapy" / "therapeutics" / "treatment" / "agents");
  - generic qualifiers that do not change the capability ("preclinical animal models" / "in vivo animal \
models"; "anti-PD-1 immunotherapy" / "anti-PD-1 checkpoint immunotherapy"; "PARP inhibitor therapy" / "PARP \
inhibitor therapeutics");
  - a broader phrasing that already covers the narrower one at this lens's altitude.

KEEP SEPARATE (mint a new canonical) when the labels name a genuinely different capability:
  - different molecular target or analyte ("anti-PD-1" != "anti-PD-L1"; "anti-CD20" != "anti-CD38"; "IL-6 \
inhibition" != "IL-17A inhibition");
  - different technique, modality, or mechanism ("CAR T-cell immunotherapy" != "adoptive T-cell immunotherapy"; \
"gene knockout" != "gene knock-in"; "bulk RNA sequencing" != "single-cell RNA sequencing");
  - different data type or material class.

When you are UNSURE whether two labels name the same capability, KEEP THEM SEPARATE — a redundant family is \
cheap, a false merge corrupts the lens.

Canonical-label form: when matching, return the existing canonical EXACTLY. When minting, use the clearest \
standard name for the class (usually one of the input labels, lightly normalized) — 1-5 words plus an optional \
parenthetical, lower-case except proper nouns/acronyms, no trailing punctuation, never the word \
"service"/"facility"/"core".

Respond with VALID JSON ONLY, no markdown:
{"assignments": [{"label": "<verbatim input label>", "canonical": "<existing-or-new canonical class>"}]}
Return one object per input label, label verbatim."""


def build_reconcile_user_message(new_labels: list[str], canonical_vocab: list[str], supercategory: str) -> str:
    """Render one accretion batch: the established canonical classes + new labels to assign."""
    sc_label = _SUPERCATEGORY_LABELS.get(supercategory, supercategory)
    if canonical_vocab:
        established = json.dumps(canonical_vocab, ensure_ascii=False, separators=(", ", ": "))
        vocab_block = f"Canonical classes already established (reuse one VERBATIM when a new label names the \
same capability):\n{established}"
    else:
        vocab_block = "Canonical classes already established: (none yet — you are establishing them)."
    payload = json.dumps(new_labels, ensure_ascii=False, separators=(", ", ": "))
    return (
        f"Supercategory: {sc_label}.\n"
        f"{vocab_block}\n\n"
        f"Assign each of these {len(new_labels)} new label(s) to an existing canonical or mint a new one:\n"
        f"{payload}"
    )
