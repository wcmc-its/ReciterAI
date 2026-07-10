"""Spotlight dirty-check gate (D-03).

Threshold v1: ≥3 of the top-50 subtopics each accumulate ≥5 new
in-window publications since the last spotlight run. Below threshold
→ skip regen; at-or-above → run the full spotlight pipeline.

This module exposes pure functions so the gate can be unit-tested
without DynamoDB. The orchestrator wires real DDB queries to feed
`evaluate_gate`; the gate itself only knows about (pmid →
subtopic_ids) mappings and the top-50 set.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


@dataclass
class GateResult:
    """Outcome of the spotlight dirty-check gate.

    `should_regen` is the boolean threshold result. The counts and
    dirty_subtopics are surfaced so the orchestrator can record them
    on the STAGE#spotlight_refresh row (`skip_reason` includes "N
    dirty subtopics of which K crossed the per-subtopic threshold").
    """

    should_regen: bool
    counts_per_top_subtopic: dict[str, int]
    dirty_subtopics: list[str]  # top-50 subtopics meeting the per-subtopic floor
    new_pmid_count: int

    def reason(self, *, min_dirty: int, min_pubs_per: int) -> str:
        if self.should_regen:
            return (
                f"dirty gate triggered: {len(self.dirty_subtopics)} subtopics "
                f">= {min_pubs_per} new pubs (>= {min_dirty} required)"
            )
        return (
            f"dirty gate held: {len(self.dirty_subtopics)} subtopics >= "
            f"{min_pubs_per} new pubs ({min_dirty} required); "
            f"new_pmid_count={self.new_pmid_count}"
        )


def evaluate_gate(
    *,
    new_pmid_assignments: dict[str, Iterable[str]],
    top_subtopic_ids: Iterable[str],
    min_dirty_subtopics: int,
    min_pubs_per_subtopic: int,
) -> GateResult:
    """Decide whether to regen the spotlight pool.

    Args:
        new_pmid_assignments: {pmid: [subtopic_id, ...]} for PMIDs landed
            since the last spotlight complete row. A PMID with multiple
            assignments contributes one count to each of its subtopics.
        top_subtopic_ids: the top-50 subtopics from pool_ranker. Only
            these are eligible to drive regen — coverage in a long-tail
            subtopic does not justify monthly Opus spend.
        min_dirty_subtopics: how many top-50 subtopics must cross the
            per-subtopic pub floor (D-03 default = 3).
        min_pubs_per_subtopic: per-subtopic floor (D-03 default = 5).

    Returns a `GateResult` carrying the should_regen decision plus the
    intermediate counts so callers can record them as audit context.
    """
    top_set = set(top_subtopic_ids)
    counts: dict[str, int] = defaultdict(int)
    for pmid, subtopic_ids in new_pmid_assignments.items():
        # Dedup per PMID: a paper with several co-author TOPIC# rows sharing
        # one primary_subtopic_id is one dirty *publication*, not several.
        for sid in set(subtopic_ids):
            if sid in top_set:
                counts[sid] += 1
    dirty = sorted(
        sid for sid, n in counts.items() if n >= min_pubs_per_subtopic
    )
    should_regen = len(dirty) >= min_dirty_subtopics
    return GateResult(
        should_regen=should_regen,
        counts_per_top_subtopic=dict(counts),
        dirty_subtopics=dirty,
        new_pmid_count=len(new_pmid_assignments),
    )
