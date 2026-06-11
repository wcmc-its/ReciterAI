"""Durable-subtopic reconcile arbiter prompt (brick B, #191 — Stage 3).

The reconcile stage matches each freshly-discovered cluster against the prior
published snapshot so a re-cluster keeps stable IDs. Stage 1 (paper-membership
overlap) and Stage 2 (label-embedding cosine) resolve the large majority
deterministically. This LLM arbiter runs ONLY on the residual ambiguous tail —
clusters whose membership overlap and label similarity are both inconclusive —
and answers one question: is this new cluster the SAME subtopic as one of a small
set of candidate prior subtopics, or genuinely new?

The bias is asymmetric on purpose: a FALSE attach makes the new cluster inherit
the wrong durable ID and its rotation/CTR history — unrecoverable. An over-mint
(treating a true continuation as new) is cheap and sweepable by a later brick. So
the arbiter keeps clusters SEPARATE whenever unsure.

`ARBITER_PROMPT_VERSION` is part of the verdict-cache input hash — bump it on any
prompt change so cached verdicts re-resolve.
"""

from __future__ import annotations

import json

ARBITER_PROMPT_VERSION = "v1"


RECONCILE_SYSTEM_PROMPT = """You decide whether a newly re-clustered research subtopic is the SAME subtopic as \
one already in the catalog, so it can keep its permanent ID across a re-cluster (a relabel must stay a rename, \
not a delete+add).

You are given, within ONE parent topic: a NEW cluster (its label + a sample of its seed publication PMIDs) and \
a small set of CANDIDATE prior subtopics (each with an ID, label, and a sample of ITS seed PMIDs). For the new \
cluster decide: is it a CONTINUATION of exactly one candidate (the same line of work, possibly relabeled or with \
some membership drift), or is it a genuinely DISTINCT/new subtopic?

SAME (continuation — reuse that candidate's ID) when:
  - the labels name the same line of work differing only by wording, synonyms, or breadth ("tumor \
microenvironment" / "the tumor immune microenvironment"); AND/OR
  - the seed PMIDs substantially overlap or are the recognizable core of one candidate that simply shed or \
gained some members.

DISTINCT (mint a new ID) when:
  - the new cluster names a different mechanism, target, modality, population, or data type than every candidate \
("anti-PD-1 response" vs "anti-PD-L1 resistance"; "single-cell RNA-seq of X" vs "bulk RNA-seq of X"); OR
  - it spans/merges two candidates rather than continuing one (do NOT pick a "closest" — that is a merge, \
which is handled separately); OR
  - the overlap is incidental (a few shared high-volume papers) rather than a shared core.

When you are UNSURE whether the new cluster continues a specific candidate, answer DISTINCT. A redundant new \
subtopic is cheap to reconcile later; a wrong ID inheritance silently corrupts identity and history.

Respond with VALID JSON ONLY, no markdown:
{"verdict": "same", "durable_id": "<the chosen candidate id>"}  OR  {"verdict": "distinct", "durable_id": null}
Choose at most ONE candidate id, and only one that appears in the candidates list."""


def build_reconcile_user_message(
    *,
    new_label: str,
    new_seed_pmids: list[int],
    candidates: list[dict],
    topic_id: str,
) -> str:
    """Render the arbiter user message.

    Args:
        new_label: the new cluster's display label.
        new_seed_pmids: a bounded sample of the new cluster's seed PMIDs.
        candidates: list of {"durable_id", "label", "seed_pmids"} (bounded samples),
            one per candidate prior subtopic.
        topic_id: the shared parent topic.
    """
    cand_lines = [
        json.dumps(
            {
                "durable_id": c["durable_id"],
                "label": c["label"],
                "sample_seed_pmids": c["seed_pmids"],
            },
            ensure_ascii=False,
            separators=(", ", ": "),
        )
        for c in candidates
    ]
    candidates_block = "\n".join(cand_lines) if cand_lines else "(none)"
    new_block = json.dumps(
        {"label": new_label, "sample_seed_pmids": new_seed_pmids},
        ensure_ascii=False,
        separators=(", ", ": "),
    )
    return (
        f"Parent topic: {topic_id}.\n\n"
        f"NEW cluster:\n{new_block}\n\n"
        f"CANDIDATE prior subtopics (choose at most one to continue, or none):\n"
        f"{candidates_block}"
    )
