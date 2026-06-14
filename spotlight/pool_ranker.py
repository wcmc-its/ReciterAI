"""Pool ranker — top-N subtopics by Sigma article_score over last-24-months
publications.

Implements SPOT-01 (pool ranking) and SPOT-02 (determinism via tuple sort
key). Reads TOPIC# enrichment from DynamoDB via Scan + paginator with
``begins_with(PK, "TOPIC#")``, filters publications older than the
24-month window, sums the blended ``article_score`` (impact x topic-relevance,
see ``utils.scoring``) of each subtopic's top-K papers per
``primary_subtopic_id``, and returns the top-N PoolEntry objects sorted
``(score DESC, subtopic_id ASC)``.

Lazy boto3 initialization follows ``utils/bedrock_client.py:_get_client``.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date

import boto3

from spotlight.types import Author, Paper, PoolEntry
from utils.env_check import load_thresholds
from utils.scoring import article_score

logger = logging.getLogger(__name__)


# Module constants
TABLE_NAME = "reciterai"
REGION = "us-east-1"
POOL_SIZE = 150  # #164: widened 50->150 so the clone-free set can reach ~25 distinct parents
WINDOW_MONTHS = 24
# pool_score = sum of top-N impact_scores per subtopic. Lifted to
# config/thresholds.json `pool_top_papers_per_subtopic` (#57 Tier B-1).
TOP_PAPERS_PER_SUBTOPIC = int(load_thresholds()["pool_top_papers_per_subtopic"])


# ---------------------------------------------------------------------------
# Lazy boto3 client (mirrors utils/bedrock_client.py:_get_client pattern)
# ---------------------------------------------------------------------------

_default_client = None


def _get_default_client():
    """Get or create the module-level default DynamoDB client.

    No AWS calls happen at import time. Tests inject their own client and
    never reach this path.
    """
    global _default_client
    if _default_client is None:
        _default_client = boto3.client("dynamodb", region_name=REGION)
    return _default_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_PARENT_RE = re.compile(r"_\d{3}$")


def _author_rank(paper: Paper) -> int:
    """Rank a Paper by the strength of its author payload for lede grounding.

    The lede generator filters out papers with no author identity and prefers
    papers where first/last author is known. Used by ``rank_pool``'s per-PMID
    dedup to keep the strongest row when the same PMID appears under
    multiple faculty (TOPIC# is emitted per co-author).

    Order: both first+last set (2) > one of first/last set (1) > neither (0).
    """
    has_first = bool(paper.first_author.person_identifier)
    has_last = bool(paper.last_author.person_identifier)
    return int(has_first) + int(has_last)


def _parent_of(subtopic_id: str) -> str:
    """Strip a trailing ``_NNN`` (3-digit) segment to recover the parent topic.

    v1 convention: subtopic IDs follow the shape ``<parent>_NNN`` where
    ``<parent>`` matches a hierarchy.json top-level key. If a future
    hierarchy schema canonicalizes parent IDs differently, Plan 06-03 may
    load ``hierarchy.json`` and override this helper.
    """
    return _PARENT_RE.sub("", subtopic_id)


def _extract_paper(item: dict) -> Paper | None:
    """Build a Paper from a TOPIC# DynamoDB low-level item.

    Returns None if ``pmid`` is missing. Empty-string fallbacks are used
    for all other string fields.

    NOTE: First/last author fan-out attributes
    (``first_author_person_identifier``, ``first_author_display_name``,
    ``last_author_person_identifier``, ``last_author_display_name``) are
    Phase 6 additions. If these attributes are not present on legacy
    TOPIC# rows, the empty-string fallback flows through to the Plan
    06-05 lede generator, which filters out papers with no author identity.
    See Plan 06-02 SUMMARY for follow-up tracking on author-fanout
    enrichment (originally scoped to ``import_enrichment.py``, retired
    in #143; the new home for that backfill is open).
    """
    pmid = item.get("pmid", {}).get("S")
    if not pmid:
        return None

    title = item.get("title", {}).get("S", "")
    journal = item.get("journal", {}).get("S", "")
    year = int(item.get("year", {}).get("N", "0"))
    impact_score = float(item.get("impact_score", {}).get("N", "0"))
    impact_justification = item.get("impact_justification", {}).get("S", "")
    synopsis = item.get("synopsis", {}).get("S", "")
    # ``score`` is the dense topic-relevance for this TOPIC# row — the second
    # ranking signal alongside impact_score (see utils.scoring.article_score).
    relevance_score = float(item.get("score", {}).get("N", "0"))

    first_pid = item.get("first_author_person_identifier", {}).get("S", "")
    first_name = item.get("first_author_display_name", {}).get("S", "")
    last_pid = item.get("last_author_person_identifier", {}).get("S", "")
    last_name = item.get("last_author_display_name", {}).get("S", "")

    # Author-fanout fields are absent on legacy TOPIC# rows; fall back to
    # the per-row faculty_uid + author_position pair, which carries one
    # author per row (the faculty whose score this row represents). This
    # gives partial author identity (one of first/last only) until the
    # planned author-fanout backfill lands (see _extract_paper docstring).
    if not (first_pid or last_pid):
        faculty_uid = item.get("faculty_uid", {}).get("S", "")
        position = item.get("author_position", {}).get("S", "")
        # cwid_ prefix is the WCM storage convention on faculty_uid; the
        # canonical personIdentifier value strips it. See CLAUDE.md.
        person_id = faculty_uid[5:] if faculty_uid.startswith("cwid_") else faculty_uid
        if position == "first":
            first_pid = person_id
        elif position == "last":
            last_pid = person_id

    first_author = Author(
        person_identifier=first_pid,
        display_name=first_name,
        position="first",
    )
    last_author = Author(
        person_identifier=last_pid,
        display_name=last_name,
        position="last",
    )

    return Paper(
        pmid=pmid,
        title=title,
        journal=journal,
        year=year,
        impact_score=impact_score,
        impact_justification=impact_justification,
        synopsis=synopsis,
        first_author=first_author,
        last_author=last_author,
        relevance_score=relevance_score,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rank_pool(
    client=None,
    table_name: str = TABLE_NAME,
    window_months: int = WINDOW_MONTHS,
    pool_size: int = POOL_SIZE,
    parent_lookup: dict[str, str] | None = None,
    top_papers_per_subtopic: int = TOP_PAPERS_PER_SUBTOPIC,
    author_resolver=None,
) -> list[PoolEntry]:
    """Return the top-N PoolEntry objects ranked by sum of top-K paper scores.

    SPOT-01: 24-month hard cutoff (CONTEXT supersedes the SPOT-01 wording
    of "recency_weight(year)" — no within-window decay in v1).
    SPOT-02: deterministic ordering via tuple sort key
    ``(-score, subtopic_id)`` — score DESC, subtopic_id ASC.

    pool_score is the sum of the **top-K papers'** ``article_score`` per
    subtopic (default K=7), where ``article_score`` blends impact with the
    paper's topic-relevance (``utils.scoring.article_score``). This anchors
    ranking to on-topic paper quality rather than subtopic volume or raw
    prominence; large subtopics with many mediocre papers — and papers that
    are prominent but off-topic — no longer dominate the pool.

    Args:
        client: optional boto3 DynamoDB client (test seam). If None, uses
            the lazy default-credential client.
        table_name: DynamoDB table name; defaults to ``reciterai``.
        window_months: recency window. Cutoff = today.year - (window // 12).
        pool_size: maximum number of PoolEntry to return.
        parent_lookup: optional dict mapping ``subtopic_id -> parent_topic``,
            sourced from hierarchy.json. If None or a subtopic_id is not
            in the dict, falls back to ``_parent_of`` (regex on _NNN suffix).
        top_papers_per_subtopic: K. Sum the K highest article_scores per
            subtopic into pool_score. Pass ``len(papers)`` worth via a high
            value if you want the legacy "sum-all" behavior.

    Returns:
        List of PoolEntry, sorted (score DESC, subtopic_id ASC), capped at
        pool_size. Each PoolEntry contains the top-K papers contributing to
        its score (PMIDs deduped per subtopic, sorted by article_score DESC).
    """
    client = client or _get_default_client()
    cutoff_year = date.today().year - (window_months // 12)

    paginator = client.get_paginator("scan")
    pages = paginator.paginate(
        TableName=table_name,
        FilterExpression="begins_with(PK, :prefix)",
        ExpressionAttributeValues={":prefix": {"S": "TOPIC#"}},
    )

    # Per (subtopic, pmid), keep the Paper with the strongest author identity.
    # TOPIC# rows are emitted per (paper, faculty), so the same PMID appears
    # multiple times in a subtopic — once per co-author. We prefer rows with
    # author_position in {"first","last"} over middle/empty, so the lede
    # generator's author-payload filter has the strongest signal available.
    by_subtopic_pmid: dict[str, dict[str, Paper]] = defaultdict(dict)

    for page in pages:
        for item in page.get("Items", []):
            year = int(item.get("year", {}).get("N", "0"))
            if year < cutoff_year:
                continue

            subtopic_id = item.get("primary_subtopic_id", {}).get("S")
            if not subtopic_id:
                continue

            paper = _extract_paper(item)
            if paper is None:
                continue
            if paper.impact_score == 0.0:
                continue

            existing = by_subtopic_pmid[subtopic_id].get(paper.pmid)
            if existing is None or _author_rank(paper) > _author_rank(existing):
                by_subtopic_pmid[subtopic_id][paper.pmid] = paper

    # B1 author resolution. The resolver applies one of two rules per the
    # spotlight_b1_faculty_or_enabled flag: default strict-AND (both leads
    # present, 2026-05-07) or, when enabled, the #231 fulltime-faculty OR rule
    # (first OR last author is identity.fullTimeFaculty='yes'). Either way,
    # papers that don't pass are dropped from the pool BEFORE the top-N cut so
    # subtopics surface their strongest qualifying papers rather than burning the
    # budget on papers we'll drop anyway. The DDB substrate rows are untouched —
    # this is an in-memory skip (the OR path also logs skips as
    # no_fulltime_faculty_lead).
    if author_resolver is not None:
        import dataclasses
        all_pmids = sorted(
            {pmid for pmid_map in by_subtopic_pmid.values() for pmid in pmid_map}
        )
        resolved = author_resolver(all_pmids)
        for sid in list(by_subtopic_pmid):
            keep: dict[str, Paper] = {}
            for pmid, paper in by_subtopic_pmid[sid].items():
                pair = resolved.get(pmid)
                if pair is None:
                    continue
                first_author = Author(
                    person_identifier=pair.first_person_identifier,
                    display_name=pair.first_display_name,
                    position="first",
                )
                last_author = Author(
                    person_identifier=pair.last_person_identifier,
                    display_name=pair.last_display_name,
                    position="last",
                )
                keep[pmid] = dataclasses.replace(
                    paper, first_author=first_author, last_author=last_author
                )
            if keep:
                by_subtopic_pmid[sid] = keep
            else:
                del by_subtopic_pmid[sid]

    by_subtopic: dict[str, list[Paper]] = {
        sid: list(pmid_map.values()) for sid, pmid_map in by_subtopic_pmid.items()
    }

    parent_lookup = parent_lookup or {}

    # Tuple sort key: article_score DESC, then PMID-as-int ASC, then year DESC.
    # article_score blends impact with the paper's topic-relevance (the TOPIC#
    # ``score``) so a high-impact paper that only grazes the subtopic can't
    # outrank an on-topic one — the representative papers a card surfaces must
    # fit the subtopic, not merely be prominent. The PMID cast surfaces
    # non-digit PMIDs as a TypeError at sort time, which is the right failure
    # mode — the schema bans non-digit PMIDs.
    scored: list[tuple[str, float, tuple[Paper, ...]]] = []
    for sid, papers in by_subtopic.items():
        top = sorted(
            papers,
            key=lambda p: (
                -article_score(p.impact_score, p.relevance_score),
                int(p.pmid),
                -p.year,
            ),
        )[:top_papers_per_subtopic]
        score = sum(article_score(p.impact_score, p.relevance_score) for p in top)
        scored.append((sid, score, tuple(top)))

    # SPOT-02: tuple sort key — score DESC, subtopic_id ASC.
    ranked = sorted(scored, key=lambda t: (-t[1], t[0]))

    result = [
        PoolEntry(
            subtopic_id=sid,
            pool_score=score,
            parent_topic=parent_lookup.get(sid) or _parent_of(sid),
            papers=top,
            full_pmids=frozenset(by_subtopic_pmid[sid]),
        )
        for sid, score, top in ranked[:pool_size]
    ]

    top_score = result[0].pool_score if result else 0.0
    logger.info(
        "Pool ranker: %d subtopics, cutoff_year=%d, top_score=%.2f",
        len(result),
        cutoff_year,
        top_score,
    )
    return result
