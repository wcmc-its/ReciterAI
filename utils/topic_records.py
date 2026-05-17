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

The optional ``synopsis`` / ``title`` attributes are written for
onboarding — the Assign stage's subtopic classifier reads them. The
cold loader omits both, preserving its historical 7-attribute row shape.
"""

from __future__ import annotations

from utils.dynamodb_helpers import make_score_sk, to_decimal


def build_topic_rows_for_pmid(
    *,
    pmid: str,
    dense_scores: dict,
    authors: list,
    taxonomy_version: str,
    min_score: float,
    synopsis: str = "",
    title: str = "",
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

    A PMID with no faculty authors yields no rows — ``TOPIC#`` rows are
    faculty-scoped (the ``FacultyIndex`` GSI keys on ``faculty_uid``).
    """
    pmid = str(pmid)
    rows: list[dict] = []
    seen_keys: set = set()

    for topic_id, score_data in (dense_scores or {}).items():
        if isinstance(score_data, dict):
            score = score_data.get("score")
            rationale = score_data.get("rationale", "")
        else:
            score = score_data
            rationale = ""
        if score is None or score < min_score:
            continue

        for author in authors:
            cwid = author["cwid"]
            pk = f"TOPIC#{topic_id}"
            sk = f"{make_score_sk(score, pmid)}#cwid_{cwid}"
            if (pk, sk) in seen_keys:
                continue
            seen_keys.add((pk, sk))

            item = {
                "PK": {"S": pk},
                "SK": {"S": sk},
                "faculty_uid": {"S": f"cwid_{cwid}"},
                "score": {"N": str(to_decimal(score))},
                "rationale": {"S": str(rationale or "")},
                "topic_scores_version": {"S": taxonomy_version},
                "pmid": {"S": pmid},
            }
            if synopsis:
                item["synopsis"] = {"S": str(synopsis)}
            if title:
                item["title"] = {"S": str(title)}
            rows.append(item)

    return rows
