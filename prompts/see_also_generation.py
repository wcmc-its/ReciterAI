"""
Static Sonnet prompt for Phase 4 See-Also Generation.

Design decisions honored here:
  D-05  Subtopics are scoped to their parent topic only (no shared nodes)
  D-08  See-also is Sonnet batch after Pass 1 completes; regenerated per recompute
  D-09  Bidirectionality filter retains only (A,B) links where (B,A) also proposed
        in the same run. This prompt asks Sonnet to EMIT both directions for every
        intended link so that the downstream filter retains them.
  D-10  See-also used for Tier 2 router expansion AND topic_decompose navigation;
        NOT used in retrieval scoring.

Research §See-Also Generation:
  - Single-pass Sonnet at temperature=0 (caller enforces).
  - Cross-topic only: intra-topic (same parent_topic_id on both sides) links are
    already implicit in the hierarchy — do not emit them.
  - Strict JSON output, no markdown fences.

No boto3 calls, no AWS calls, no file I/O — safe to import anywhere.
"""


SEE_ALSO_SYSTEM_PROMPT = """You are an expert biomedical research librarian tasked with identifying
CROSS-TOPIC relationships between research subtopics in a faculty-expertise taxonomy.

You will receive a flat list of subtopics across MULTIPLE parent topics. Each subtopic line
has the form:

  - {parent_topic_id}/{subtopic_id}: {label} — {description}

Your job is to propose pairs of subtopics from DIFFERENT parent topics that a research dean,
grants officer, or navigator would find useful to connect. Typical examples of useful
cross-topic connections:

  - aging_geroscience/aging_cardiovascular_disease <-> cardiovascular_disease/cvd_heart_failure
  - cancer_research/cancer_breast <-> womens_health/whs_breast_health
  - infectious_disease/id_hiv <-> aging_geroscience/aging_hiv_older_adults

OUTPUT FORMAT — return ONLY a JSON object with NO markdown fences, NO commentary, NO prose:

{
  "links": [
    {"from": "<subtopic_id>", "to": "<subtopic_id>", "reason": "<one sentence, <=200 chars>"}
  ]
}

RULES:
1. CROSS-TOPIC ONLY. Do NOT propose links where the two subtopics share the same parent_topic_id.
   Intra-topic relationships are already implicit in the hierarchy and are not see_also links.
2. BIDIRECTIONAL. Whenever you propose a link from A to B, you MUST ALSO propose the reverse
   link from B to A in the SAME output. The reasons may be phrased similarly or identically;
   both directions must be present. This is a strict requirement: the downstream
   bidirectionality filter drops any (A,B) whose reverse (B,A) is missing.
3. NO SELF-LOOPS. Never emit a link where `from` == `to`.
4. REASON LENGTH. Each `reason` is one sentence, 200 characters or fewer, stating why a
   research dean would navigate between these two subtopics.
5. NO DUPLICATES. Do not emit the same (from, to) pair more than once.
6. EMPTY IS VALID. If fewer than 2 distinct parent topics are present in the input, or no
   meaningful cross-topic relationships exist, return {"links": []}.
7. STRICT JSON. No markdown fences, no code blocks, no commentary outside the JSON object.

Favor precision over recall: only propose links that reflect genuine thematic, methodological,
or population overlap that would help a user navigating the taxonomy. Avoid speculative or
tenuous connections."""


def BUILD_SEE_ALSO_USER_MESSAGE(hierarchy_json: dict) -> str:
    """
    Flatten a hierarchy JSON into a compact user message for the see-also prompt.

    Accepts two shapes:
      (a) Full hierarchy: {"topics": {"<parent_topic_id>": {"subtopics": [...]}, ...}}
      (b) Single-topic draft: {"topic_id": "<id>", "subtopics": [...]}

    Shape (b) is the output of Pass 1 Discovery (discover_subtopics.py) before the full
    hierarchy has been assembled. On shape (b), only one parent topic is present, which
    means generate_see_also.py will short-circuit to empty output (by design).

    Output lines are of the form:

      - {parent_topic_id}/{subtopic_id}: {label} — {description}

    Args:
        hierarchy_json: dict loaded from a hierarchy.json file (full or single-topic draft).

    Returns:
        A newline-joined string suitable for use as the user message body. Includes a short
        preamble stating the number of parent topics and total subtopics.

    Raises:
        ValueError: If the input is neither shape (a) nor shape (b).
    """
    lines: list = []
    parent_count = 0
    subtopic_count = 0

    if isinstance(hierarchy_json, dict) and "topics" in hierarchy_json and isinstance(hierarchy_json["topics"], dict):
        # Full-hierarchy shape
        for parent_topic_id, topic_entry in hierarchy_json["topics"].items():
            subtopics = topic_entry.get("subtopics", []) if isinstance(topic_entry, dict) else []
            if not subtopics:
                continue
            parent_count += 1
            for st in subtopics:
                sid = st.get("id", "")
                label = st.get("label", "")
                desc = st.get("description", "")
                lines.append(f"- {parent_topic_id}/{sid}: {label} \u2014 {desc}")
                subtopic_count += 1
    elif isinstance(hierarchy_json, dict) and "topic_id" in hierarchy_json and "subtopics" in hierarchy_json:
        # Single-topic Pass 1 draft shape
        parent_topic_id = hierarchy_json["topic_id"]
        subtopics = hierarchy_json.get("subtopics", [])
        if subtopics:
            parent_count = 1
            for st in subtopics:
                sid = st.get("id", "")
                label = st.get("label", "")
                desc = st.get("description", "")
                lines.append(f"- {parent_topic_id}/{sid}: {label} \u2014 {desc}")
                subtopic_count += 1
    else:
        raise ValueError(
            "hierarchy_json must have either a top-level 'topics' dict "
            "or 'topic_id' + 'subtopics' keys"
        )

    preamble = (
        f"Input: {parent_count} parent topic(s), {subtopic_count} subtopic(s) total.\n\n"
        f"Propose cross-topic see-also links per the rules in the system prompt. "
        f"Remember: every intended link must appear in BOTH directions (from A to B AND from B to A).\n\n"
        f"Subtopics:\n"
    )
    return preamble + "\n".join(lines)
