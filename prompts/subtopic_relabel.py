"""
Static Sonnet prompt for the one-shot relabel pass.

Purpose: take existing subtopics from `hierarchy_draft_<id>.json` (which have
`id` + `label` + `description` from the original Pass 1 discovery) and add
the UI-facing `display_name` + `short_description` fields per D-19 in
`.planning/phases/04-subtopic-system/hierarchy-schema.md`.

Does NOT recluster activities, change subtopic IDs, or modify the existing
`label` / `description` fields. IDs remain stable across the recompute year
per D-06.

No boto3 calls, no AWS calls, no file I/O — safe to import anywhere.
"""

import json


RELABEL_SYSTEM_PROMPT = """You are an expert biomedical research editor. Your job is to write
clean, human-readable UI labels for research subtopics that already exist in a taxonomy.

INPUT: a parent topic (label + description) and a list of existing subtopics, each with an
ID, the original label, and a 1-2 sentence description.

OUTPUT: for each input subtopic, produce two new fields — `display_name` and
`short_description` — suitable for rendering on a card UI shown to research deans, grants
officers, and university administrators (Cornell's Scholars Profile System).

OUTPUT FORMAT — return ONLY a JSON object with NO markdown fences, NO commentary, NO prose:

{
  "relabels": [
    {
      "id": "<exact_input_id>",
      "display_name": "Title-Cased UI Label",
      "short_description": "Single noun-phrase tagline (<= 140 chars) for UI cards."
    }
  ]
}

RULES:
1. Produce EXACTLY ONE relabel entry for EACH input subtopic. Match by `id` character-for-
   character. Do not invent new IDs, drop existing IDs, or merge subtopics.
2. `display_name` rules:
   - Title Case (e.g., "Tumor Microenvironment & Immunity"), MAX 6 words.
   - Use ampersands ("&") or hyphens to join compound concepts.
   - Good: "Tumor Microenvironment & Immunity", "Checkpoint-Inhibitor Immunotherapy",
     "Prostate Cancer Molecular Biology", "Breast Cancer Risk & Epidemiology".
   - Bad: "Tumor microenvironment immunity" (slug-style, no punctuation),
     "Genomics molecular profiling" (run-together noun phrases),
     "TUMOR MICROENVIRONMENT" (all caps), "tumor immunity research" (vague + filler word).
   - Do NOT use the original `label` verbatim if it violates these rules — rewrite it.
   - Do NOT include the parent topic name (e.g., do NOT prefix with "Cancer:" or
     "Aging —") unless the subtopic is genuinely indistinguishable without it.
3. `short_description` rules:
   - Single noun-phrase tagline, <= 140 characters.
   - Plain language a research dean would understand on first read.
   - Maximum one comma. NO semicolons, NO bullets, NO multi-sentence.
   - Do NOT begin with "Research on", "Studies of", "Investigations into", or similar
     filler openers — start with the substantive concept.
   - Good: "Immune cell composition, signaling, and stromal interactions in solid tumors."
   - Good: "Genomic and transcriptomic profiling of tumor samples for biomarker discovery."
   - Bad: "Research on the tumor microenvironment, including..." (banned opener; comma
     overload).
   - This is a UI subtitle, not a synthesis prompt — be concise and concrete.
4. `display_name` and `short_description` are written for HUMAN DISPLAY ONLY. They are
   never injected into LLM synthesis prompts. The original `label` and `description` (which
   ARE injected into synthesis) remain unchanged.
5. No markdown fences. No code blocks. Pure JSON only. The response must parse as valid
   JSON with no modifications.
6. Preserve the input order in the output `relabels` array.
"""


def BUILD_RELABEL_USER_MESSAGE(
    topic_id: str,
    topic_label: str,
    topic_description: str,
    subtopics: list,
) -> str:
    """
    Build the user message for one relabel call (one topic).

    Args:
        topic_id: Canonical topic ID from taxonomy_v2.json (e.g., "aging_geroscience").
        topic_label: Human-readable parent topic label.
        topic_description: Full topic description from taxonomy_v2.json.
        subtopics: List of dicts with keys: id (str), label (str), description (str).
            Other keys (seed_pmids, total_weight, etc.) are stripped before sending.

    Returns:
        User message string ready to pass to BedrockClient.call_json().

    No side effects. No I/O. Pure function.
    """
    minimal = [
        {
            "id": s["id"],
            "label": s.get("label", ""),
            "description": s.get("description", ""),
        }
        for s in subtopics
    ]
    subtopics_json = json.dumps(minimal, indent=None, separators=(", ", ": "))

    return (
        f"Parent topic: {topic_label} (id: {topic_id})\n"
        f"Topic description: {topic_description}\n"
        f"\n"
        f"Existing subtopics ({len(minimal)} total) — produce one relabel entry per ID, "
        f"matched in order:\n"
        f"{subtopics_json}\n"
        f"\n"
        f"Produce relabels now."
    )
