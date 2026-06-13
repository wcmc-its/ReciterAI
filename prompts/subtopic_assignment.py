"""
Static Haiku prompts for Phase 4 Pass 2 (Subtopic Assignment).

Design decisions honored here:
  D-02  Pass 2 per-activity Haiku call. Primary subtopic = highest-confidence
        assignment (picked in post-processing, not by the model). Multi-assignment
        allowed; confidence floor applied in Python, not in the prompt.
  D-04  Pure module — no side effects at import time, no boto3 calls.
  D-16  Downstream writes land on activity records (subtopic_ids, primary,
        confidences). This file only builds the envelope; the caller
        (assign_subtopics.py) handles writes.

The confidence floor of 0.3 is documented in the prompt so Haiku understands what
"worth assigning" means, but filtering to the floor is still performed in Python
post-response. This matches the review item #2 tiebreaker split — Python owns
the numeric policy; the prompt owns the semantic task.

No boto3 calls, no AWS calls, no file I/O — safe to import anywhere.
"""

from typing import Mapping, Sequence


ASSIGNMENT_SYSTEM_PROMPT = """You are a biomedical research classifier assigning a single research activity
(a scientific publication) to the subtopics it belongs to within a parent research topic.

You will receive:
- ONE activity with `pmid`, `title`, and `synopsis`.
- A parent topic with `id`, `label`, and `description`.
- A list of 8 to 30 candidate subtopics, each with `id`, `label`, and `description`.

Your job is to decide which of the listed subtopics the activity belongs to and
how confident you are in each assignment.

SUBSTRATE MATCHING (read carefully — this is a common failure mode):
Many subtopics are defined by a DATA SUBSTRATE — the kind of data the study actually
analyzes — not merely by an analytical method. Substrate qualifiers that may appear in a
subtopic's label or description include: "electronic health records / EHR", "clinical
notes / free text", "medical imaging / radiology / histopathology", "flow cytometry",
"genomics / -omics / sequencing / methylation", "liquid biopsy / circulating tumor cells",
"wearable sensors", "microbiome".

When a subtopic names a data substrate, the activity must ACTUALLY USE that substrate to
belong. Sharing only a method — machine learning, deep learning, classification,
prediction, risk modeling — is NOT enough. A study that applies machine learning to
proteomic, flow-cytometry, imaging, genomic, microbiome, or survey data does NOT belong in
an electronic-health-record subtopic just because both involve prediction or classification.

If the activity's substrate matches a DIFFERENT listed subtopic (e.g. an imaging, -omics,
computational-pathology, or liquid-biopsy subtopic), assign it there instead. If none of the
listed subtopics matches the activity's actual data substrate, return an empty assignments
list rather than forcing a method-only match.

OUTPUT FORMAT — return ONLY a JSON object with NO markdown fences, NO commentary, NO prose:

{
  "pmid": <int>,
  "topic_id": "<parent topic id>",
  "assignments": [
    {"subtopic_id": "<one of the provided ids>", "confidence": 0.XX},
    ...
  ]
}

RULES:
1. Emit zero, one, or multiple assignments. Multi-assignment is allowed when the
   activity genuinely spans more than one subtopic.
2. `confidence` is a float in [0.0, 1.0]. Rank assignments by confidence in
   descending order (highest confidence first).
3. Only assign an activity to a subtopic when you are reasonably sure it belongs
   — a confidence floor of 0.3 is applied downstream, so assignments below 0.3
   will be dropped. If no subtopic fits at confidence >= 0.3, return an empty
   `assignments: []` list — unassigned activities participate in topic-level
   retrieval (Tier 3 fallback) and are NOT an error.
4. You MUST NOT propose a `subtopic_id` that is not in the provided candidate
   list. Do not invent new ids, do not use parent topic ids, do not return
   top-level taxonomy ids.
5. The `pmid` field in the output MUST match the input activity's pmid exactly
   (integer, not string).
6. The `topic_id` field MUST match the provided parent topic's id.
7. No markdown fences, no code blocks, no commentary. Pure JSON only — the
   response must parse as valid JSON with no modifications.
8. Do NOT assign the same subtopic_id twice in the `assignments` array.
"""


def BUILD_ASSIGNMENT_USER_MESSAGE(
    activity: Mapping,
    topic_meta: Mapping,
    subtopic_defs: Sequence[Mapping],
) -> str:
    """
    Build the user message for the Pass 2 Haiku assignment call.

    Args:
        activity: Dict with keys `pmid`, `title`, `synopsis`. Extra keys are
                  ignored.
        topic_meta: Dict with keys `id`, `label`, `description` for the parent
                    topic (from taxonomy_v2.json).
        subtopic_defs: Sequence of dicts, each with `id`, `label`, `description`.
                       Rendered one per line as
                       `- {id}: {label} — {description}`.

    Returns:
        User message string ready to pass to BedrockClient.call(). Compact —
        no extra whitespace or escaping beyond what the payload contains.

    No side effects. No I/O. Pure function.
    """
    pmid = activity.get("pmid", "")
    title = str(activity.get("title", "") or "")
    synopsis = str(activity.get("synopsis", "") or "")

    topic_id = topic_meta.get("id", "")
    topic_label = topic_meta.get("label", "")
    topic_description = topic_meta.get("description", "")

    subtopic_lines = []
    for sdef in subtopic_defs:
        sid = sdef.get("id", "")
        slabel = sdef.get("label", "")
        sdesc = sdef.get("description", "")
        subtopic_lines.append(f"- {sid}: {slabel} \u2014 {sdesc}")
    subtopic_block = "\n".join(subtopic_lines)

    return (
        f"Parent topic: {topic_label} (id: {topic_id})\n"
        f"Topic description: {topic_description}\n"
        f"\n"
        f"Candidate subtopics ({len(subtopic_defs)}):\n"
        f"{subtopic_block}\n"
        f"\n"
        f"Activity:\n"
        f"- pmid: {pmid}\n"
        f"- title: {title}\n"
        f"- synopsis: {synopsis}\n"
        f"\n"
        f"Assign this activity to the appropriate subtopics now."
    )


__all__ = [
    "ASSIGNMENT_SYSTEM_PROMPT",
    "BUILD_ASSIGNMENT_USER_MESSAGE",
]
