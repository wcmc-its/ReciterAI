"""Extraction prompt for A2 corpus-wide tool/method mining (docs/tool-classifier-spec.md).

One Bedrock Haiku call per faculty paper: given a paper's title + abstract, name
the DISTINCTIVE research tools and methods the work used. This is the extraction
stage ONLY — it produces raw mentions (a verbatim surface form + a short grounding
context + a weak legacy-tag hint), NOT a classification. The disposition/kind/
supercategory/attributes are assigned later by the existing classifier
(``prompts.tool_classify``), which re-reads name + context independently per the
spec's cardinal rule. Keeping the two stages separate is deliberate: extraction
is cheap/Haiku/per-paper; classification is the shared, vocabulary-bound pass.

Why methods are emphasised
--------------------------
The A1 seed is resource-skewed (instruments, datasets, reagents) because it came
from a tool list, not from abstracts. A2's whole reason to exist is that wet-lab
and computational *methods* appear only in live abstract text (the probe measured
~2.3 distinctive items/paper, ~42% of them methods). So the prompt explicitly
pulls procedures, assays, algorithms, models, and study designs — not only the
named instruments/reagents the seed already over-covers.

The ``tool_category_hint`` values are the legacy §4 tags the downstream
classifier understands as a WEAK prior (``pipeline_tools.vocab.legacy_prior``);
they are imported from vocab so they can never drift from what the classifier
accepts. A wrong or absent hint costs nothing — the classifier decides from
name + context regardless.
"""

from __future__ import annotations

import json

from pipeline_tools import vocab

# The legacy tags the classifier's §4 weak prior recognises. Imported so the
# extractor's hint vocabulary tracks the classifier's, not a hand-copied list.
_HINT_TAGS = sorted(vocab.LEGACY_CATEGORY_PRIOR.keys())

# Budget for ONE complete sentence quoted from the abstract (not a re-summary).
# Sized for a typical biomedical sentence: the snippet is surfaced standalone by
# SPS ("How X is used"), so it must read as a self-contained clause, and a hard
# char cut mid-sentence is the very fragment problem this budget exists to avoid
# (#238). 300 holds the large majority of enclosing sentences; longer ones fall
# back to a clause boundary in extract._truncate, never a mid-word cut.
CONTEXT_MAX_CHARS = 300


EXTRACT_SYSTEM_PROMPT = f"""You are an expert biomedical research-methods analyst. Given ONE publication's \
title and abstract, extract the DISTINCTIVE research TOOLS and METHODS the study used or produced. \
You are MINING raw mentions for an institutional tool/method taxonomy — you are NOT classifying them.

WHAT COUNTS (extract these):
  - Methods & techniques: wet-lab procedures (e.g. single-cell RNA-seq, patch-clamp, CUT&RUN, \
two-photon imaging), computational/statistical methods & study designs (e.g. mixed-effects regression, \
Mendelian randomization, a named CNN architecture), assays producing a readout.
  - Named instruments / platforms (a specific sequencer, scanner, mass spectrometer, microscope).
  - Reagents & probes used AS tools (antibodies, tracers, CRISPR/Cas9 systems, vectors), drugs/agents \
when they are the studied intervention.
  - Organisms, cell lines, transgenic/knockout models, primary cultures, strains.
  - Datasets, cohorts, registries, and software/models the work ran on or against.

EMPHASIS — METHODS: abstracts under-name methods relative to instruments. Pull the procedures and \
analysis methods explicitly, not just the boxed hardware. A study that "used single-cell RNA-seq and a \
mixed-effects model on the SRTR registry" yields THREE mentions (a method, a method, a dataset), not one.

WHAT TO SKIP (do NOT extract):
  - Ubiquitous commodity bench gear mentioned only in passing: pipettes, generic centrifuge, vortex, \
−80 freezer, generic PCR thermocycler, water bath.
  - Bare elementary statistics with no scholar-specific signal: "t-test", "ANOVA", "chi-square", \
"p-value", "mean ± SD".
  - Generic office/computing software (Excel, Word), and anything that is not a research tool.
  - The disease, organ, or biological phenomenon STUDIED (that is the subject, not a tool). Extract \
"anti-PD-1 antibody", not "melanoma"; extract "Mendelian randomization", not "coronary disease".

Aim for the genuinely distinctive items — typically 1–5 per abstract; an abstract may have none. \
Do NOT pad with commodity items to hit a count. Prefer the specific named form over a generic category \
("10x Chromium", not "a microfluidics platform"), but extract the generic method name when that is all \
the abstract gives ("flow cytometry").

For EACH extracted mention emit:
  - raw_name: the tool/method name VERBATIM as the abstract phrases it (keep an acronym + expansion \
together if both appear, e.g. "single-cell RNA sequencing (scRNA-seq)").
  - tool_category_hint: your single best WEAK guess of the legacy category, one of {_HINT_TAGS}, \
or null if unsure. This is only a hint; downstream classification re-decides.
  - context: the COMPLETE sentence from the abstract that shows how the tool/method was used — copied \
VERBATIM and contiguous (start at the sentence's first word, end at its period), ≤ {CONTEXT_MAX_CHARS} \
chars. It MUST read correctly on its OWN: do NOT begin mid-clause (no "were compared measuring …", no \
"involving …") and do NOT leave a dangling "this"/"these"/"the latter" with no referent in the snippet. \
This both grounds the later use-context routing (e.g. drug administered vs probe) AND is surfaced \
standalone to readers, so a broken fragment is unusable. If the relevant sentence exceeds the limit, copy \
the longest leading self-contained clause of it. Never invent or summarise; quote the abstract only.
  - confidence: "high" if clearly a named research tool/method; "low" if it might be the studied subject, \
a commodity item, or ambiguous (a low-confidence mention is still emitted — it routes to review).

Respond with VALID JSON ONLY, no markdown:
{{"mentions": [{{"raw_name": "<verbatim>", "tool_category_hint": "...|null", \
"context": "<complete sentence, verbatim from the abstract>", "confidence": "high|low"}}]}}
If the abstract names no distinctive tool or method, return {{"mentions": []}}.
"""


def build_extract_user_message(pub_row: dict) -> str:
    """Render one publication as the user turn for an extraction call.

    ``pub_row`` carries the publication metadata (the same shape the enrichment
    workers consume): pmid, title, journal, year, abstract. Author/role/cwid are
    NOT sent — they are carried by the harness onto every resulting mention, not
    needed to identify tools.
    """
    payload = {
        "pmid": str(pub_row.get("pmid", "")),
        "title": pub_row.get("articleTitle") or pub_row.get("title") or "",
        "journal": pub_row.get("journalTitleVerbose") or pub_row.get("journal"),
        "year": pub_row.get("articleYear") or pub_row.get("year"),
        "abstract": pub_row.get("abstractVarchar") or pub_row.get("abstract") or "",
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(", ", ": "))
    return (
        "Extract the distinctive research tools and methods from this publication. "
        "Emphasise methods; skip commodity gear and the studied subject. "
        f"Return one mention object per tool/method, raw_name verbatim:\n{body}"
    )
