"""
Cross-topic subtopic deduplication — decision engine (issue #164, Ask A).

Subtopics are discovered per parent topic independently (D-05), so a research
theme that spans topics (e.g. spaceflight multi-omics surfacing under Genetics,
Systems Biology, AND Single-Cell & Spatial Biology) is re-discovered as
near-identical subtopics under each. This module DECIDES which cross-topic
subtopic pairs are duplicates that should merge, vs. which are parent/child
nestings to keep.

This is a PURE decision layer: it reads subtopic metadata + membership and
returns a `DedupPlan`. It does NOT mutate the hierarchy or repoint publication
assignments — that (destructive) application is deferred until the probe
quantifies real overlap, per #164's sequencing (probe -> bundle-time merge).

Two signals (#164 "Dedup signal & metric"):
  - Article-set overlap, min-cardinality coefficient |A∩B| / min(|A|,|B|)
    (decision D-23, reused from `cli.aging_pilot_gate.compute_pairwise_overlap`).
    Robust to size asymmetry: a small subtopic fully inside a large one scores
    1.0, where standard Jaccard would read ~small/large and miss it.
  - Description cosine (Titan v2 embeddings, reused from
    `spotlight.theme_dedup.find_near_clones`).

Threshold policy — UNFORGIVING (#164 "Threshold policy — unforgiving"):
  - CROSS-TOPIC pairs only. Within-topic clustering is the discovery pass's job
    (D-05), so same-parent pairs are never considered here.
  - SYMMETRIC pairs (size ratio < containment_ratio): MERGE when
    article_overlap >= article_overlap_min (0.40) OR cosine >= cosine_min (0.75).
    "OR" (either signal fires) is the unforgiving choice.
  - ASYMMETRIC pairs (size ratio >= containment_ratio, e.g. 3x): NEVER merge — a
    small subtopic contained in a large one is a finer subtopic nested in a
    broader one, and merging would destroy the granularity Ask B adds. Emit a
    parent/child FLAG for review when article_overlap >= flag_overlap_min (0.60)
    or the descriptions are near-clones.

Merged groups are transitive (union-find): if a~b and b~c both merge, {a,b,c}
collapse to one group. The canonical survivor is the highest `total_weight`
(tie-break `activity_count`, then id ascending); the rest fold into it.

No AWS calls at import time. `embed` is injectable for tests; in production it
defaults to the Bedrock Titan v2 helper inside `find_near_clones`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from cli.aging_pilot_gate import compute_pairwise_overlap
from spotlight.theme_dedup import find_near_clones

# Documented fallbacks — callers should pass the live values from
# config/thresholds.json (hierarchy_dedup_*). See load_dedup_thresholds().
DEFAULT_ARTICLE_OVERLAP_MIN = 0.40
DEFAULT_COSINE_MIN = 0.75
DEFAULT_CONTAINMENT_RATIO = 3.0
DEFAULT_FLAG_OVERLAP_MIN = 0.60


def load_dedup_thresholds(thresholds: dict | None = None) -> dict:
    """Pull the four ``hierarchy_dedup_*`` knobs from config/thresholds.json.

    Falls back to the module defaults for any key absent from the config, so an
    older thresholds.json never crashes the engine. Pass a pre-loaded dict to
    avoid re-reading the file (tests inject one directly).
    """
    if thresholds is None:
        from utils.env_check import load_thresholds

        thresholds = load_thresholds()
    return {
        "article_overlap_min": float(
            thresholds.get("hierarchy_dedup_article_overlap_min", DEFAULT_ARTICLE_OVERLAP_MIN)
        ),
        "cosine_min": float(
            thresholds.get("hierarchy_dedup_cosine_min", DEFAULT_COSINE_MIN)
        ),
        "containment_ratio": float(
            thresholds.get("hierarchy_dedup_containment_ratio", DEFAULT_CONTAINMENT_RATIO)
        ),
        "flag_overlap_min": float(
            thresholds.get("hierarchy_dedup_flag_overlap_min", DEFAULT_FLAG_OVERLAP_MIN)
        ),
    }


@dataclass(frozen=True)
class Subtopic:
    """The minimal view of a subtopic the dedup engine needs.

    Attributes:
        id: subtopic id (e.g. ``genetics_genomics_precision_medicine_spaceflight_omics``).
        topic_id: parent topic id — the cross-topic boundary. Pairs sharing a
            ``topic_id`` are skipped.
        description: ``short_description``; embedded for the cosine signal.
        pmids: membership set for the article-overlap signal.
        total_weight: primary canonical-survivor tiebreak (higher wins).
        activity_count: secondary canonical-survivor tiebreak (higher wins).
    """

    id: str
    topic_id: str
    description: str
    pmids: frozenset
    total_weight: float
    activity_count: int
    label: str = ""  # human-readable; for review output only, not a signal


@dataclass
class MergeGroup:
    """A set of cross-topic subtopics judged to be the same subtopic."""

    canonical_id: str
    member_ids: list[str]  # non-canonical subtopics folded into canonical
    topic_ids: list[str]  # parent topics spanned by the group (reporting)
    evidence: list[dict]  # one entry per merge edge: pair, overlap, cosine, signal


@dataclass
class ParentChildFlag:
    """An asymmetric containment pair — kept as hierarchy, not merged."""

    parent_id: str  # larger membership
    child_id: str  # smaller, contained membership
    parent_topic_id: str
    child_topic_id: str
    article_overlap: float
    cosine: float | None
    size_ratio: float
    reason: str


@dataclass
class DedupPlan:
    merges: list[MergeGroup]
    flags: list[ParentChildFlag]
    pairs_considered: int  # cross-topic pairs evaluated
    thresholds: dict


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        # path-halving
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _ordered_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _group_components(adj: dict[str, set[str]]) -> list[set[str]]:
    """Connected components — transitive. a~b and b~c -> {a, b, c}.

    Over-collapses under a low threshold: weakly-overlapping subtopics chain
    into one blob (probe on real data: a 100-member group across 30 topics).
    Kept available, but not the default.
    """
    uf = _UnionFind(adj.keys())
    for a, neighbors in adj.items():
        for b in neighbors:
            uf.union(a, b)
    groups: dict[str, set[str]] = {}
    for node in adj:
        groups.setdefault(uf.find(node), set()).add(node)
    return [g for g in groups.values() if len(g) >= 2]


def _group_cliques(adj: dict[str, set[str]]) -> list[set[str]]:
    """Greedy clique cover — every member is pairwise above threshold.

    Breaks the transitive chains that wreck connected-component grouping: a
    node joins a cluster only if it is similar to EVERY current member, so
    "physician burnout" cannot ride an overlap chain into an "elder
    mistreatment" cluster. Deterministic (id-sorted seeds and candidates);
    greedy, not maximum-clique, which is fine for de-duplication.
    """
    nodes = sorted(adj)
    assigned: set[str] = set()
    clusters: list[set[str]] = []
    for seed in nodes:
        if seed in assigned:
            continue
        clique = [seed]
        for cand in sorted(adj[seed]):
            if cand in assigned:
                continue
            if all(cand in adj[m] for m in clique):
                clique.append(cand)
        if len(clique) >= 2:
            assigned.update(clique)
            clusters.append(set(clique))
    return clusters


def overlap_adjacency(
    pmid_sets: dict[str, set],
    *,
    article_overlap_min: float = DEFAULT_ARTICLE_OVERLAP_MIN,
    containment_ratio: float = DEFAULT_CONTAINMENT_RATIO,
) -> dict[str, set[str]]:
    """Symmetric near-clone adjacency from article-set overlap alone (#164).

    Two subtopics are adjacent when their min-cardinality overlap (D-23) is at
    or above ``article_overlap_min`` AND they are not an asymmetric containment
    pair (size ratio < ``containment_ratio``) — a small subtopic nested in a
    large one is kept, not flagged. Pure; no embeddings, no AWS.

    Shape matches ``theme_dedup.NearClones.adjacency`` so it can be unioned
    straight into the spotlight rotation selector's near-clone gate.
    """
    adj: dict[str, set[str]] = {sid: set() for sid in pmid_sets}
    overlaps = compute_pairwise_overlap(pmid_sets)
    for (a, b), ov in overlaps.items():
        if ov < article_overlap_min:
            continue
        na, nb = len(pmid_sets[a]), len(pmid_sets[b])
        lo, hi = min(na, nb), max(na, nb)
        ratio = (hi / lo) if lo > 0 else float("inf")
        if ratio >= containment_ratio:
            continue  # containment — keep both (parent/child), do not gate
        adj[a].add(b)
        adj[b].add(a)
    return adj


def union_adjacency(*adjacencies: dict[str, set[str]]) -> dict[str, set[str]]:
    """Merge symmetric adjacency maps into one (union of edges).

    Every key from every input appears in the result; each maps to the union
    of its neighbor sets across inputs. Inputs are not mutated.
    """
    out: dict[str, set[str]] = {}
    for adj in adjacencies:
        for sid, neighbors in adj.items():
            out.setdefault(sid, set()).update(neighbors)
    return out


def decide_dedup(
    subtopics: Iterable[Subtopic],
    *,
    article_overlap_min: float = DEFAULT_ARTICLE_OVERLAP_MIN,
    cosine_min: float = DEFAULT_COSINE_MIN,
    containment_ratio: float = DEFAULT_CONTAINMENT_RATIO,
    flag_overlap_min: float = DEFAULT_FLAG_OVERLAP_MIN,
    grouping: str = "clique",
    embed: Callable[[list[str]], list[list[float]]] | None = None,
) -> DedupPlan:
    """Decide cross-topic merges and parent/child flags. Pure; mutates nothing.

    Args:
        subtopics: the full subtopic set across all topics.
        article_overlap_min: min-cardinality overlap at/above which a symmetric
            pair merges (D-23 metric).
        cosine_min: description-cosine at/above which a symmetric pair merges.
        containment_ratio: size ratio (larger/smaller membership) at/above which
            a pair is treated as containment (asymmetric) and never merged.
        flag_overlap_min: overlap at/above which an asymmetric pair is flagged
            as a parent/child relationship for review.
        grouping: how qualifying pairs become merge groups. "clique" (default)
            requires every member pairwise above threshold — breaks the
            transitive chains that over-collapse "component" grouping into
            blobs. "component" is connected-components (transitive).
        embed: list-of-texts -> list-of-vectors. Injected by tests; defaults to
            Titan v2 inside ``find_near_clones``.

    Returns:
        A ``DedupPlan`` with merge groups and parent/child flags.
    """
    if grouping not in ("clique", "component"):
        raise ValueError(f"grouping must be 'clique' or 'component', got {grouping!r}")
    subs = {s.id: s for s in subtopics}
    ids = sorted(subs)

    pmid_sets = {sid: set(subs[sid].pmids) for sid in ids}
    overlaps = compute_pairwise_overlap(pmid_sets)  # {(a,b) id-sorted: overlap}

    descriptions = {sid: subs[sid].description for sid in ids}
    near = find_near_clones(descriptions, threshold=cosine_min, embed=embed)
    cosine_value = {_ordered_pair(a, b): c for a, b, c in near.ranked_pairs}

    merge_edges: list[tuple[str, str, float, float | None, str]] = []
    flags: list[ParentChildFlag] = []
    pairs_considered = 0

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]  # a < b
            if subs[a].topic_id == subs[b].topic_id:
                continue  # within-topic is the discovery pass's job (D-05)
            pairs_considered += 1

            key = (a, b)
            overlap = overlaps.get(key, 0.0)
            cosine_above = b in near.adjacency.get(a, set())  # cosine >= cosine_min
            cosine = cosine_value.get(key)

            na, nb = len(pmid_sets[a]), len(pmid_sets[b])
            lo, hi = min(na, nb), max(na, nb)
            ratio = (hi / lo) if lo > 0 else float("inf")

            if ratio >= containment_ratio:
                # Containment: never merge. The small subtopic is nested in the
                # large one — preserve as parent/child to keep granularity.
                if overlap >= flag_overlap_min or cosine_above:
                    parent, child = (a, b) if na >= nb else (b, a)
                    flags.append(
                        ParentChildFlag(
                            parent_id=parent,
                            child_id=child,
                            parent_topic_id=subs[parent].topic_id,
                            child_topic_id=subs[child].topic_id,
                            article_overlap=round(overlap, 4),
                            cosine=(round(cosine, 4) if cosine is not None else None),
                            size_ratio=round(ratio, 2),
                            reason=(
                                f"containment (size ratio {ratio:.1f}x) — kept as "
                                f"parent/child, not merged"
                            ),
                        )
                    )
                continue

            # Symmetric pair: unforgiving OR across the two signals.
            signals: list[str] = []
            if overlap >= article_overlap_min:
                signals.append(f"article_overlap={overlap:.2f}")
            if cosine_above:
                shown = cosine if cosine is not None else cosine_min
                signals.append(f"cosine={shown:.2f}")
            if signals:
                merge_edges.append((a, b, overlap, cosine, " OR ".join(signals)))

    # Build the merge-adjacency from qualifying edges, then group per policy.
    adj: dict[str, set[str]] = {}
    edge_records: dict[tuple[str, str], dict] = {}
    for a, b, ov, cv, sig in merge_edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
        edge_records[_ordered_pair(a, b)] = {
            "pair": [a, b],
            "article_overlap": round(ov, 4),
            "cosine": (round(cv, 4) if cv is not None else None),
            "signal": sig,
        }

    clusters = (
        _group_cliques(adj) if grouping == "clique" else _group_components(adj)
    )

    merges: list[MergeGroup] = []
    for members in clusters:
        canonical = sorted(
            members,
            key=lambda s: (-subs[s].total_weight, -subs[s].activity_count, s),
        )[0]
        evidence = [
            edge_records[key]
            for key in sorted(edge_records)
            if key[0] in members and key[1] in members
        ]
        merges.append(
            MergeGroup(
                canonical_id=canonical,
                member_ids=sorted(m for m in members if m != canonical),
                topic_ids=sorted({subs[m].topic_id for m in members}),
                evidence=evidence,
            )
        )

    merges.sort(key=lambda g: g.canonical_id)
    flags.sort(key=lambda f: (f.parent_id, f.child_id))

    return DedupPlan(
        merges=merges,
        flags=flags,
        pairs_considered=pairs_considered,
        thresholds={
            "article_overlap_min": article_overlap_min,
            "cosine_min": cosine_min,
            "containment_ratio": containment_ratio,
            "flag_overlap_min": flag_overlap_min,
            "grouping": grouping,
        },
    )
