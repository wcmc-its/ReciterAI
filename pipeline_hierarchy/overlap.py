"""Min-cardinality (Szymkiewicz–Simpson) set-overlap — the one metric the D-23
dedup gate (`cli/aging_pilot_gate.py`) and the durable-ID reconcile (brick B,
`pipeline_hierarchy/subtopic_reconcile.py`) share.

Kept dependency-free (stdlib only) so both a CLI gate and a pipeline module can
import it without pulling numpy / boto3 / openai at import time.
"""

from __future__ import annotations

from typing import Hashable, Iterable, Mapping, Optional


def min_card_overlap(a: set, b: set) -> float:
    """``|a ∩ b| / min(|a|, |b|)`` — 0.0 when either set is empty.

    Deliberately stricter than Jaccard: a small set fully inside a large one reads
    1.0 (the "surviving core" signal a re-cluster needs), where Jaccard would read
    small/large. Identical formula to the D-23 dedup gate.
    """
    min_card = min(len(a), len(b))
    if min_card == 0:
        return 0.0
    return len(a & b) / min_card


def best_overlap(
    query: set,
    candidates: Mapping[Hashable, set],
    *,
    exclude: Iterable[Hashable] = frozenset(),
) -> tuple[Optional[Hashable], float]:
    """Best ``(key, overlap)`` over ``candidates`` by ``min_card_overlap``.

    Skips keys in ``exclude``. Iterates ``sorted(candidates)`` with a strict ``>``
    so the LOWEST key wins on a tie — fully deterministic. Returns ``(None, 0.0)``
    when nothing scores above 0 (empty query, all candidates empty, or all
    excluded). Callers apply the match thresholds to the returned overlap.
    """
    excluded = set(exclude)
    best_key: Optional[Hashable] = None
    best_ov = 0.0
    for key in sorted(candidates):
        if key in excluded:
            continue
        ov = min_card_overlap(query, candidates[key])
        if ov > best_ov:
            best_key, best_ov = key, ov
    return best_key, best_ov
