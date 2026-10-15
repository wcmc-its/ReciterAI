"""Synopsis prompts for grant and trial area signals.

Ported verbatim from the 2026-10-14 area-signals pilot, which validated them:

- ``GRANT_SYNOPSIS_SYSTEM`` is the POC ``synopsis-grant`` prompt ("ReCiterAI -
  Active Prompts"), character for character.
- ``TRIAL_SYNOPSIS_SYSTEM`` is the one new prompt the pilot wrote. The POC's
  ``synopsis-clinical-trial`` was a copy of the publication prompt, so the pilot
  modeled this one on the grant prompt instead.

The user-content builders are the pilot's too. Each is passed to
``pipeline_enrichment.synopsis.generate_synopsis`` as ``user_content_builder``,
which calls it with ``title``, ``journal``, ``year`` and ``abstract``; the
``journal`` slot carries the grant's funder and mechanism or the trial's study
type and phase.

Changing any text here changes what the pilot measured. Bump
``PROMPT_VERSION`` with it: every ``scored`` AREAINPUT item records the
``prompt_version`` it was scored under, ``drain.due_reason`` makes an item whose
stored version differs due again (``prompt_changed``), and because the version
also feeds the input hash, the stored AREASIGNAL row no longer matches and the
item is re-scored rather than reused. Expect a full re-score's cost (the whole
queue, subject to the per-run ceiling).
"""
from __future__ import annotations

PROMPT_VERSION = "2026-10-14"

GRANT_SYNOPSIS_SYSTEM = """You are an expert research writer creating concise grant synopses.
Given grant information, create a 40-95 character summary that captures:

- Primary research objective or hypothesis
- Key methodology or approach being funded
- Target condition, population, or scientific domain
- Significance of potential outcomes

Focus on the most scientifically significant aspects. Use professional terminology.
Be precise and informative within the character limit.

Return a JSON object with "synopsis" field containing the summary."""

TRIAL_SYNOPSIS_SYSTEM = """You are an expert research writer creating concise clinical trial synopses.
Given clinical trial information, create a 40-95 character summary that captures:

- Intervention or exposure being studied
- Target condition or population
- Study design or phase, when informative
- Primary aim of the study

Focus on the most clinically significant aspects. Use professional terminology.
Be precise and informative within the character limit.

Return a JSON object with "synopsis" field containing the summary."""


def build_grant_user_content(*, title, journal, year, abstract):
    """Grant synopsis user content; ``journal`` carries funder and mechanism."""
    return (f"Title: {title}\nFunder and mechanism: {journal or 'N/A'}\n"
            f"Start year: {year if year is not None else 'N/A'}\nAbstract: {abstract}\n\nReturn JSON only.")


def build_trial_user_content(*, title, journal, year, abstract):
    """Trial synopsis user content; ``journal`` carries study type and phase."""
    return f"Title: {title}\n{journal or ''}\nStart year: {year}\nBrief summary: {abstract}\n\nReturn JSON only."


# --- Subarea assignment (subareas.py) ----------------------------------------
#
# The publication classifier's system prompt (prompts/subtopic_assignment.py
# ASSIGNMENT_SYSTEM_PROMPT) says "a scientific publication" and asks for an
# integer pmid. Grant and trial texts reuse its user message and its rules
# through assign_subtopics.classify_activity with the wording below instead;
# the publication prompt is untouched. The activity's `pmid` slot carries
# "<kind>:<text_key>" (a string), and the reply's pmid is never read: only
# `assignments` is.
#
# Bump SUBAREA_PROMPT_VERSION with any change here, and with any change to the
# classifier call in subareas.py (SUBAREA_MODEL and its request fields): it is
# the stamp that marks a stored assignment stale. It is separate from
# PROMPT_VERSION on purpose: a new subarea prompt or model re-runs only the
# assignment calls (the drain's assign-only path), never the synopsis and area
# scoring. 2026-10-15: Haiku 4.5 -> Haiku 5.5 with reasoning off (prompt text
# unchanged).
SUBAREA_PROMPT_VERSION = "2026-10-15"

_SUBAREA_SYSTEM_TEMPLATE = """You are a biomedical research classifier assigning a single research activity
({noun_phrase}) to the subtopics it belongs to within a parent research topic.

You will receive:
- ONE activity with `pmid`, `title`, and `synopsis`. For this activity `pmid` is
  not a PubMed id: it is an identifier string of the form "{kind}:<key>". The
  synopsis is a short summary of the {noun} (or its title when no summary exists).
- A parent topic with `id`, `label`, and `description`.
- A list of 8 to 30 candidate subtopics, each with `id`, `label`, and `description`.

The candidate subtopics were defined from published papers. Judge the {noun} by
the research it {verb}, as you would judge a paper by the research it reports.

Your job is to decide which of the listed subtopics the activity belongs to and
how confident you are in each assignment.

SUBSTRATE MATCHING (read carefully — this is a common failure mode):
Many subtopics are defined by a DATA SUBSTRATE — the kind of data the study actually
analyzes — not merely by an analytical method. Substrate qualifiers that may appear in a
subtopic's label or description include: "electronic health records / EHR", "clinical
notes / free text", "medical imaging / radiology / histopathology", "flow cytometry",
"genomics / -omics / sequencing / methylation", "liquid biopsy / circulating tumor cells",
"wearable sensors", "microbiome".

When a subtopic names a data substrate, the {noun} must ACTUALLY USE that substrate to
belong. Sharing only a method — machine learning, deep learning, classification,
prediction, risk modeling — is NOT enough. A {noun} that applies machine learning to
proteomic, flow-cytometry, imaging, genomic, microbiome, or survey data does NOT belong in
an electronic-health-record subtopic just because both involve prediction or classification.

If the activity's substrate matches a DIFFERENT listed subtopic (e.g. an imaging, -omics,
computational-pathology, or liquid-biopsy subtopic), assign it there instead. If none of the
listed subtopics matches the activity's actual data substrate, return an empty assignments
list rather than forcing a method-only match.

OUTPUT FORMAT — return ONLY a JSON object with NO markdown fences, NO commentary, NO prose:

{{
  "pmid": "<the input activity's pmid string>",
  "topic_id": "<parent topic id>",
  "assignments": [
    {{"subtopic_id": "<one of the provided ids>", "confidence": 0.XX}},
    ...
  ]
}}

RULES:
1. Emit zero, one, or multiple assignments. Multi-assignment is allowed when the
   activity genuinely spans more than one subtopic.
2. `confidence` is a float in [0.0, 1.0]. Rank assignments by confidence in
   descending order (highest confidence first).
3. Only assign an activity to a subtopic when you are reasonably sure it belongs
   — a confidence floor of 0.3 is applied downstream, so assignments below 0.3
   will be dropped. If no subtopic fits at confidence >= 0.3, return an empty
   `assignments: []` list — an unassigned activity still counts toward the
   parent topic and is NOT an error.
4. You MUST NOT propose a `subtopic_id` that is not in the provided candidate
   list. Do not invent new ids, do not use parent topic ids, do not return
   top-level taxonomy ids.
5. The `pmid` field in the output MUST be the input activity's pmid string,
   copied exactly.
6. The `topic_id` field MUST match the provided parent topic's id.
7. No markdown fences, no code blocks, no commentary. Pure JSON only — the
   response must parse as valid JSON with no modifications.
8. Do NOT assign the same subtopic_id twice in the `assignments` array.
"""

GRANT_SUBAREA_SYSTEM = _SUBAREA_SYSTEM_TEMPLATE.format(
    noun_phrase="a funded research grant", kind="grant", noun="grant", verb="proposes")
TRIAL_SUBAREA_SYSTEM = _SUBAREA_SYSTEM_TEMPLATE.format(
    noun_phrase="a registered clinical trial", kind="trial", noun="trial", verb="studies")


def subarea_system_prompt(kind: str) -> str:
    """The assignment system prompt for a grant or trial text."""
    return GRANT_SUBAREA_SYSTEM if kind == "grant" else TRIAL_SUBAREA_SYSTEM
