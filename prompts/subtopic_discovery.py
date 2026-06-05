"""
Static Sonnet prompts for Phase 4 Pass 1 (Subtopic Discovery).

Design decisions honored here:
  D-01  Temperature=0 for reproducibility — enforced by caller (discover_subtopics.py)
  D-04  No side effects at import time — this module is pure constants + one function
  D-05  Subtopics are scoped to their parent topic only (no cross-topic clustering)
  D-06  IDs are NOT stable across recomputes (wholesale replacement on recompute)
  D-15  Descriptions injected verbatim into Tier 1/2 synthesis prompts

CLAUDE.md Taxonomy Design Principle:
  Clusters must reflect what is actually in the publication data.
  Do NOT create speculative subtopics that lack publication evidence.
  Leave activities unassigned rather than forcing them into a weak cluster.

No boto3 calls, no AWS calls, no file I/O — safe to import anywhere.
"""

import json


DISCOVERY_SYSTEM_PROMPT = """You are an expert biomedical research analyst tasked with organizing a set of
research publications into thematic subtopics.

You will receive a parent topic (label and description) and a list of research activities
(publications), each with a PMID, title, synopsis, impact score, and relevance score.

Your goal is to cluster these activities into 8 to 25 coherent thematic subtopics that
reflect the actual research themes present in the data.

OUTPUT FORMAT — return ONLY a JSON object with NO markdown fences, NO commentary, NO prose:

{
  "subtopics": [
    {
      "id": "<topic_prefix>_<slug>",
      "label": "Human-Readable Label",
      "description": "One to two sentences describing the subtopic theme. This will be injected verbatim into synthesis prompts, so make it precise and informative.",
      "seed_pmids": [12345, 67890, 11122],
      "coverage_estimate": 0.18
    }
  ],
  "uncovered": [<pmid>, ...],
  "total_activities": N,
  "coverage_pct": 0.XX
}

RULES:
1. Produce between 8 and 25 subtopics. Do not go below 8 or above 25.
2. Every cluster MUST have at least 3 seed_pmids. If a potential cluster has fewer than 3
   activities, do NOT create it — leave those activities in the "uncovered" list instead.
3. Each cluster must represent a coherent, distinct theme that is actually present in the
   publication data. Do NOT invent subtopics not supported by the activities provided.
4. Leave activities unassigned (in "uncovered") rather than forcing them into a weak or
   mismatched cluster.
5. Subtopic IDs must follow the format: {topic_prefix}_{slug} where topic_prefix is
   supplied by the caller in the user message, and slug is a lowercase alphanumeric
   underscore-separated label (e.g., "aging_cellular_senescence"). IDs must not overlap
   with existing top-level taxonomy IDs.
6. Descriptions must be 1 to 2 sentences, precise, and suitable for direct injection into
   retrieval synthesis prompts. Do not use vague language.
7. Each subtopic's "coverage_estimate" is the fraction of total_activities that subtopic
   accounts for (based on seed_pmids count / total_activities).
8. "coverage_pct" is the union fraction: len(unique pmids across all seed_pmids lists) /
   total_activities.
9. No markdown fences. No code blocks. Pure JSON only. The response must parse as valid
   JSON with no modifications.
10. Clusters should be roughly balanced where the data supports it. Avoid one cluster
    capturing >40% of activities unless the topic is genuinely dominated by a single theme.
"""

DISCOVERY_EXTENSION_PROMPT = """The previous clustering pass achieved {current_coverage_pct:.0%} activity coverage
({uncovered_count} activities remain unassigned).

Your task is to propose ADDITIONAL subtopics that capture the uncovered activities listed
below. You may add up to {extension_cap} new subtopics.

CONSTRAINTS:
- Do NOT modify or rename any existing subtopic IDs. Existing subtopics are final.
- Every new cluster MUST have at least 3 seed_pmids from the uncovered set.
- Apply the same quality rules as the initial pass: real themes only, no speculative
  clusters, 1-2 sentence descriptions, pure JSON output, no markdown fences.
- The same ID format applies: {{topic_prefix}}_{{slug}}.

Existing subtopics (IDs and labels only — do not reproduce in output):
{current_subtopics_json}

Uncovered activities to cluster:
{uncovered_activities_json}

Return ONLY a JSON object:
{{
  "new_subtopics": [
    {{
      "id": "<topic_prefix>_<slug>",
      "label": "Human-Readable Label",
      "description": "One to two sentences describing the subtopic theme.",
      "seed_pmids": [<pmid>, ...],
      "coverage_estimate": 0.XX
    }}
  ],
  "still_uncovered": [<pmid>, ...]
}}
"""


def BUILD_DISCOVERY_USER_MESSAGE(
    topic_id: str,
    topic_label: str,
    topic_description: str,
    activities: list,
    topic_prefix: str,
) -> str:
    """
    Build the user message for the discovery pass Sonnet call.

    Args:
        topic_id: Canonical topic ID from taxonomy_v2.json (e.g., "aging_geroscience").
        topic_label: Human-readable topic label (e.g., "Aging & Geroscience").
        topic_description: Full topic description from taxonomy_v2.json.
        activities: List of activity dicts, each with keys:
            pmid (int), title (str), synopsis (str), impact_score (float/int),
            relevance_score (float).
        topic_prefix: Prefix to use in subtopic IDs (e.g., "aging" from "aging_geroscience").
            Passed here so Sonnet knows the correct ID prefix to use.

    Returns:
        User message string ready to pass to bedrock_client.call().

    No side effects. No I/O. Pure function.
    """
    activities_json = json.dumps(activities, indent=None, separators=(", ", ": "))

    return (
        f"Topic: {topic_label} (id: {topic_id})\n"
        f"Description: {topic_description}\n"
        f"Topic prefix for subtopic IDs: {topic_prefix}\n"
        f"\n"
        f"Activities ({len(activities)} total):\n"
        f"{activities_json}\n"
        f"\n"
        f"Produce subtopics now."
    )
