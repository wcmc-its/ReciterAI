"""TOPIC# DynamoDB row builder.

A ``TOPIC#`` row is one (topic, pmid, author-CWID) activity triple: a
publication's dense relevance score for a topic, attributed to one
co-authoring faculty member. One PMID yields one row per
(topic scoring >= ``min_score``) x (co-authoring CWID).

This builder is the single source of truth for that row shape. It is
used by:

- ``load_dynamodb.build_topic_records`` — the cold-path batch loader.
- ``score_publications.score_one_publication`` — the #80 onboarding
  Score stage, which materializes a CWID's TOPIC# rows directly so the
  downstream Assign / TopTopic / Rollup stages have data to read (PR 4a;
  before it, only the manual cold loader ever wrote TOPIC# rows).

Every row carries ``created_at``: the spotlight dirty gate filters on it
to find activity that landed since the last publish, and a DynamoDB
comparison against a missing attribute matches nothing.

The optional ``synopsis`` / ``title`` attributes are written for
onboarding — the Assign stage's subtopic classifier reads them. The
cold loader omits both.

``year`` is written by both paths. ``spotlight.pool_ranker.rank_pool``
filters on it with a 0 default, so a row missing ``year`` sorts below the
recency cutoff and never reaches spotlight ranking. Before this attribute
existed on the builder, the only rows carrying it were those repaired by
``scripts/debug/repopulate_topic_text.py`` (a 2026-05 one-off for a
different bug), which is why coverage was partial and arbitrary.

The optional ``impact_score`` / ``impact_justification`` attributes are
the #212 Part A build-time join: when the PMID's ``IMPACT#`` row already
carries an enriched score at materialization time (the onboarding
ordering — Enrich runs before Score), that value is joined onto every
``TOPIC#`` row at birth so it can never be missing for downstream
representative-paper ranking. When the IMPACT# row has no score yet
(``TOPIC#``-before-enrichment), the keys are simply omitted and the daily
back-propagation hook (#212 Part B) fills them when enrichment lands.

``author_position`` (``first`` / ``middle`` / ``last``) is written on every
row. SPS ranks "Scholars in this area" on first/senior authorships and
``spotlight.pool_ranker`` prefers them for the lede; both read this attribute.
``analysis_summary_author.authorPosition`` is {first, last, NULL} and NULL is a
middle author, so the value is always known and a missing attribute means the
row predates this field (``cli/backfill_topic_author_position.py``).
"""

from __future__ import annotations

from utils.dynamodb_helpers import make_score_sk, to_decimal
from utils.iso_clock import now_iso


def normalize_author_position(raw) -> str:
    """Map an ``authorPosition`` value to first/middle/last.

    The source column is {first, last, NULL}; NULL (read back as ``""`` or the
    string ``"None"``) is a middle author.
    """
    p = str(raw or "").strip().lower()
    return p if p in ("first", "last") else "middle"


def author_positions_by_cwid(authors: list) -> dict:
    """``{cwid: position}`` in first-seen CWID order. A CWID listed at more than
    one position (co-first/co-last) keeps first/last over middle."""
    out: dict = {}
    for author in authors:
        cwid = author["cwid"]
        pos = normalize_author_position(author.get("position"))
        if out.get(cwid, "middle") == "middle":
            out[cwid] = pos
    return out


def build_topic_rows_for_pmid(
    *,
    pmid: str,
    dense_scores: dict,
    authors: list,
    taxonomy_version: str,
    min_score: float,
    synopsis: str = "",
    title: str = "",
    year: str | int | None = None,
    impact_score: str | int | float | None = None,
    impact_justification: str = "",
    created_at: str | None = None,
) -> list[dict]:
    """Build the ``TOPIC#`` DynamoDB items for a single PMID.

    Returns items in DynamoDB attribute-typed format — the shape
    ``utils.dynamodb_helpers.batch_write`` consumes — one per
    (topic with dense score >= ``min_score``) x (author in ``authors``).

    Args:
        pmid: Publication PMID.
        dense_scores: ``{topic_id: {"score": float, "rationale": str}}``
            or ``{topic_id: float}`` — the Sonnet dense-scoring output.
        authors: ``[{"cwid": str, "position": str}, ...]`` — the PMID's
            faculty authors (i.e. ``author_mapping[pmid]``).
        taxonomy_version: stamped on each row as ``topic_scores_version``.
        min_score: dense-score floor; topics scoring below it are dropped.
        synopsis: article synopsis; written as the ``synopsis`` attribute
            when non-empty so the Assign subtopic classifier can read it.
        title: article title; written as the ``title`` attribute when
            non-empty, same rationale.
        year: article publication year; written as the ``year`` Number
            attribute when present. ``spotlight.pool_ranker.rank_pool``
            defaults a missing ``year`` to 0 and drops the row for falling
            below its recency cutoff, so a row without it is invisible to
            spotlight ranking. Omitted (not zero-filled) when unknown, so
            the absence stays distinguishable from a real old year.
        impact_score: enriched impact score copied from the PMID's ``IMPACT#``
            row (#212 Part A). May be the DDB string form ("N"), an int/float,
            or None. Written as the ``impact_score`` Number attribute ONLY when
            present (not None / not blank) — when absent the key is omitted, so
            the row keeps its historical shape and Part B fills it later.
        impact_justification: the enriched impact justification; written as the
            ``impact_justification`` attribute only when both it and
            ``impact_score`` are present (mirrors the stopgap backfill, which
            sets the justification only when the IMPACT# row carries one).
        created_at: ISO-8601 stamp for when this row landed; defaults to now.
            The spotlight dirty gate
            (``pipeline_spotlight.orchestrator.resolve_new_pmid_assignments``)
            filters on it to find activity that landed since the last publish —
            a DynamoDB comparison against a *missing* attribute matches nothing,
            so rows without it are invisible to the gate.

    A PMID with no faculty authors yields no rows — ``TOPIC#`` rows are
    faculty-scoped (the ``FacultyIndex`` GSI keys on ``faculty_uid``).
    """
    pmid = str(pmid)
    rows: list[dict] = []
    # A re-score deletes then rewrites this PMID's rows, so created_at means
    # "when this row landed", not "when the publication was first scored" — a
    # re-scored pub reads as dirty to the gate, which is what we want since its
    # subtopic assignment may have changed.
    row_created_at = created_at or now_iso()

    # #212 Part A — resolve the impact attributes once; every row for this
    # PMID carries the same copy. A None / blank impact_score means the
    # IMPACT# row had no enriched score at build time — omit both keys.
    impact_score_n: str | None = None
    if impact_score is not None and str(impact_score).strip() != "":
        impact_score_n = str(impact_score)

    # Same omit-when-unknown rule as impact_score. Non-numeric input is
    # dropped rather than written, so the Number attribute never holds junk.
    year_n: str | None = None
    if year is not None and str(year).strip() != "":
        try:
            year_n = str(int(str(year).strip()))
        except ValueError:
            year_n = None

    positions = author_positions_by_cwid(authors)

    for topic_id, score_data in (dense_scores or {}).items():
        if isinstance(score_data, dict):
            score = score_data.get("score")
            rationale = score_data.get("rationale", "")
        else:
            score = score_data
            rationale = ""
        if score is None or score < min_score:
            continue

        for cwid, position in positions.items():
            pk = f"TOPIC#{topic_id}"
            sk = f"{make_score_sk(score, pmid)}#cwid_{cwid}"

            item = {
                "PK": {"S": pk},
                "SK": {"S": sk},
                "faculty_uid": {"S": f"cwid_{cwid}"},
                "score": {"N": str(to_decimal(score))},
                "rationale": {"S": str(rationale or "")},
                "topic_scores_version": {"S": taxonomy_version},
                "pmid": {"S": pmid},
                "created_at": {"S": row_created_at},
                "author_position": {"S": position},
            }
            if synopsis:
                item["synopsis"] = {"S": str(synopsis)}
            if title:
                item["title"] = {"S": str(title)}
            if year_n is not None:
                item["year"] = {"N": year_n}
            if impact_score_n is not None:
                item["impact_score"] = {"N": impact_score_n}
                # Match the stopgap backfill: justification only when present.
                if impact_justification:
                    item["impact_justification"] = {"S": str(impact_justification)}
            rows.append(item)

    return rows
