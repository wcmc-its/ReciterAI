"""Onboarding Assign fan-out — the `DeriveDirtyTopics` Task (#80 Phase 2, PR 4).

The onboarding Score stage materializes a CWID's `TOPIC#` activity rows
(PR 4a); the Assign stage must then run subtopic assignment for each topic
the CWID has activity in. `assign_subtopics` is *per-topic*, so the state
machine fans out a Step Functions `Map` — one iteration per dirty topic.

A `Map`'s `ItemsPath` can only select an array that already exists in the
execution state, so the group-by must be a Task. This module is that Task.
`DeriveDirtyTopics` runs between `WriteScoreStageRow` and the `Map`:

    derive_dirty_topics  — pure: group the CWID's TOPIC# rows by topic,
                           restrict to the run's accepted PMID set, drop
                           topics with no approved hierarchy draft.
    handler              — the I/O wrapper: the FacultyIndex query and the
                           draft-coverage manifest read.

A draft-less topic is *dropped*, not failed: `assign_subtopics` would
`sys.exit(2)` on a missing/unapproved draft and fail the whole `Map`, so a
topic absent from `config/hierarchy_draft_coverage.json` degrades to
topic-level scoring with no subtopic assignment — the same treatment an
excluded topic gets elsewhere (`oral_craniofacial_health` is the one such
topic today; see the manifest's `_comment`).

The split mirrors `orchestrator.py`: `derive_dirty_topics` is pure and
unit-tested with injected rows; `handler` does the DynamoDB / file I/O.
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Ensure repo root is importable regardless of cwd (Lambda runs from the
# zip root; tests run from the repo root) — needed for `rollup_by_cwid`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `fetch_cwid_topic_activity` is the FacultyIndex GSI query + projection,
# delivered by #90 (PR 2). Reuse it so the onboarding fan-out and the
# CWID-scoped rollup read a CWID's TOPIC# activity through one code path.
# rollup_by_cwid's module-level imports are all light (utils/* + boto3);
# its ReciterDB import is function-local, so this does not pull SQL deps.
from rollup_by_cwid import fetch_cwid_topic_activity, fetch_topic_activity_for_pmids
from utils.dynamodb_helpers import TABLE_NAME, get_table
from utils.stage_records import compute_input_hash

logger = logging.getLogger(__name__)

# Stage namespace for the fan-out input hash. `compute_input_hash` prepends
# it, so this hash never collides with another stage's hash over a
# structurally-identical input dict.
_ASSIGN_FANOUT_STAGE = "assign_fanout"

# Bundled into every Lambda zip under `config/`. The set of taxonomy topics
# with an approved `hierarchy_draft_<topic>.json` — see the file's own
# `_comment` and `tests/test_hierarchy_draft_coverage_manifest.py`.
_COVERAGE_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "hierarchy_draft_coverage.json"
)


def _load_draft_coverage() -> set[str]:
    """Load the draft-coverage manifest — topics with an approved draft."""
    data = json.loads(_COVERAGE_PATH.read_text())
    return set(data["draft_covered_topics"])


# ---------------------------------------------------------------------------
# Decision logic (pure — unit-tested with injected activity rows)
# ---------------------------------------------------------------------------


def derive_dirty_topics(
    *,
    cwid: str | None = None,
    pmids: list[str],
    topic_activity_rows: list[dict],
    draft_covered_topics: set[str],
) -> dict[str, Any]:
    """Group `TOPIC#` activity into the Assign `Map`'s work list. Pure.

    `topic_activity_rows` is the projected activity rows — one dict per row
    with `topic_id` / `pmid` / `primary_subtopic_id` — from
    `fetch_cwid_topic_activity` (onboarding, per-CWID) or
    `fetch_topic_activity_for_pmids` (hot path, per-PMID-set, #119).
    `pmids` is the run's full accepted PMID set; a topic's `delta_pmids` is
    its activity PMIDs intersected with that set.

    Passing the *full* accepted set (not just the run's net work) is
    deliberate and self-healing: `fetch_cwid_topic_activity` only returns
    `TOPIC#` rows that exist, so a partial re-run's newly-scored PMID adds a
    row, which shifts that topic's `delta_pmids`, which changes
    `assign_input_hash` — and `assign_subtopics`'s own skip cache then
    re-runs exactly that topic.

    Topics absent from `draft_covered_topics` are dropped (and reported in
    `dropped_topics`): `assign_subtopics` `sys.exit(2)`s on a missing or
    unapproved draft, which would fail the whole `Map`.

    Returns ``{"assign_topics": [{"topic_id", "delta_pmids"}, ...],
    "assign_input_hash": str, "dropped_topics": [str, ...]}``. Each
    `delta_pmids` is sorted and non-empty; `assign_topics` is ordered by
    `topic_id`.
    """
    run = {str(p) for p in pmids}

    by_topic: dict[str, set] = defaultdict(set)
    for row in topic_activity_rows:
        topic_id = row.get("topic_id")
        pmid = row.get("pmid")
        if not topic_id or pmid is None:
            continue
        pmid = str(pmid)
        if pmid in run:
            by_topic[topic_id].add(pmid)

    assign_topics: list[dict] = []
    dropped: list[str] = []
    for topic_id in sorted(by_topic):
        if topic_id not in draft_covered_topics:
            dropped.append(topic_id)
            continue
        assign_topics.append(
            {"topic_id": topic_id, "delta_pmids": sorted(by_topic[topic_id])}
        )

    # Content-addressed label for the workflow's assign-stage summary row.
    # `compute_input_hash` canonicalizes with sorted keys, so the nested
    # topic map is order-stable; the pmid lists are pre-sorted above.
    # Scope: the CWID for an onboarding run, or the run's PMID set for a
    # hot-path run (#119), which has no single CWID — so two runs with
    # structurally-identical topic work but a different scope still differ.
    scope = {"cwid": cwid} if cwid else {"pmids": sorted(run)}
    assign_input_hash = compute_input_hash(
        _ASSIGN_FANOUT_STAGE,
        {
            **scope,
            "topics": {t["topic_id"]: t["delta_pmids"] for t in assign_topics},
        },
    )
    return {
        "assign_topics": assign_topics,
        "assign_input_hash": assign_input_hash,
        "dropped_topics": sorted(dropped),
    }


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event: dict, context: Any = None) -> dict:
    """`DeriveDirtyTopics` Task — group `TOPIC#` activity for the Assign `Map`.

    Two event shapes; the `cwid` key is the discriminator.

    Onboarding (#80 Phase 2 / PR 4) — one CWID's activity:
        {"cwid": "abc1234", "pmids": ["...", ...]}
    Routes to `fetch_cwid_topic_activity` (FacultyIndex). A blank `cwid` is
    an operator error and raises.

    Hot path (#119) — the weekly delta PMID set's activity:
        {"pmids": ["...", ...]}
    Routes to `fetch_topic_activity_for_pmids` (PmidIndex). An empty `pmids`
    list is legitimate (an empty delta) — it yields an empty fan-out, which
    `CheckAssignNeeded` routes past the `Map`.

    Returns the `derive_dirty_topics` result; the state machine routes the
    `Map` over `$.assign.assign_topics` and threads
    `$.assign.assign_input_hash` onto the assign-stage summary row.
    """
    pmids = [str(p) for p in event.get("pmids") or []]
    table = get_table(TABLE_NAME)

    if "cwid" in event:
        cwid: str | None = (event.get("cwid") or "").strip()
        if not cwid:
            raise ValueError(
                "DeriveDirtyTopics: onboarding event has a blank 'cwid'; the "
                "onboarding state machine populates it from "
                "$.orchestrate.input.cwid"
            )
        rows = fetch_cwid_topic_activity(table, cwid)
        scope_label = f"cwid={cwid}"
    else:
        cwid = None
        rows = fetch_topic_activity_for_pmids(table, pmids)
        scope_label = f"pmids[{len(pmids)}]"

    result = derive_dirty_topics(
        cwid=cwid,
        pmids=pmids,
        topic_activity_rows=rows,
        draft_covered_topics=_load_draft_coverage(),
    )

    if result["dropped_topics"]:
        logger.warning(
            "DeriveDirtyTopics: %s dropped %d topic(s) with no approved "
            "hierarchy draft — their papers get topic-level scoring but no "
            "subtopic assignment: %s",
            scope_label, len(result["dropped_topics"]), result["dropped_topics"],
        )
    logger.info(
        "DeriveDirtyTopics: %s pmids=%d activity_rows=%d -> %d dirty "
        "topic(s) for the Assign fan-out",
        scope_label, len(pmids), len(rows), len(result["assign_topics"]),
    )
    return result
