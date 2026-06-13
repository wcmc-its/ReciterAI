"""Dataclasses shared across the spotlight/ package.

All frozen for hashability and to prevent accidental mutation between
pipeline stages (pool_ranker -> rotation_selector -> lede_generator ->
critic -> assembler -> publish).

Naming: ``person_identifier`` is the canonical Python attribute name for the
WCM faculty UID (snake_case per CLAUDE.md convention). JSON-side camelCase
serialization is the assembler's responsibility (Plan 06-06) and is not a
Python attribute here. See CLAUDE.md "Conventions / Naming" for the legacy
prefix that is forbidden in Phase 6 code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Author:
    """First or last author of a publication.

    Frozen so PoolEntry remains hashable. ``position`` is restricted to
    {"first", "last"} because the spotlight pipeline only models corner
    authors (middle authors are out of scope for v1).
    """

    person_identifier: str
    display_name: str
    position: str

    def __post_init__(self) -> None:
        if self.position not in ("first", "last"):
            raise ValueError(
                f"Author.position must be 'first' or 'last', got {self.position!r}"
            )


@dataclass(frozen=True)
class Paper:
    """A single publication carried through the spotlight pipeline.

    No ``__post_init__`` validation: pool_ranker may emit Paper instances
    with empty author payloads when the source TOPIC# row predates the
    Phase 6 author-fanout enrichment. Plan 06-05 lede generator filters
    out papers with no author identity.

    ``relevance_score`` is the publication's dense topic-relevance for the
    subtopic this Paper was pooled under (the ``score`` attribute on the
    ``TOPIC#`` row). Combined with ``impact_score`` via
    ``utils.scoring.article_score`` to rank papers *for a topic* rather than
    on raw prominence. Defaults to 0.0 for reconstruction paths (e.g. the
    lede-reuse rebuild from a published artifact, where per-paper scores are
    dropped) that never re-rank.
    """

    pmid: str
    title: str
    journal: str
    year: int
    impact_score: float
    impact_justification: str
    synopsis: str
    first_author: Author
    last_author: Author
    relevance_score: float = 0.0


@dataclass(frozen=True)
class PoolEntry:
    """One row of the top-50 pool ranker output.

    ``papers`` is a tuple (not a list) so the frozen dataclass remains
    hashable — required so downstream stages can use PoolEntry as a dict
    key or set member.

    ``full_pmids`` is the subtopic's complete author-resolved membership for
    the window (every PMID, not just the top-K in ``papers``). The spotlight
    near-clone gate uses it to compute cross-subtopic article overlap (#164),
    which catches equivalent subtopics whose descriptions are worded too
    differently to trip the cosine gate. Defaults to empty for callers/tests
    that don't populate it (the overlap gate then contributes no edges).
    """

    subtopic_id: str
    pool_score: float
    parent_topic: str
    papers: tuple[Paper, ...]
    full_pmids: frozenset[str] = frozenset()
