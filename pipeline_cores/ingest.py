"""Read candidate publications from ReciterDB (read-only).

Reuses the same corpus the topic scorer uses (analysis_summary_article +
reporting_abstracts via PUBLICATION_EXTRACTION_SQL) so cores inference scores
the same publication set as topics.
"""
from __future__ import annotations


def fetch_publications(engine, pmids: list = None, limit: int = None) -> list:
    """Return [{pmid, title, abstract}] for the scoreable corpus.

    `pmids` restricts to a set (e.g. a core's candidate pool); `limit` caps the
    row count for test runs.
    """
    from sqlalchemy import bindparam, text  # lazy
    from utils.sql_queries import PUBLICATION_EXTRACTION_SQL  # lazy

    sql = PUBLICATION_EXTRACTION_SQL
    params: dict = {}
    if pmids:
        # Splice a pmid filter ahead of ORDER BY without disturbing the shared query.
        sql = sql.replace(
            "ORDER BY a1.pmid DESC",
            "AND a1.pmid IN :pmid_list ORDER BY a1.pmid DESC",
        )
        params["pmid_list"] = tuple(int(p) for p in pmids)
    if limit:
        sql += f"\nLIMIT {int(limit)}"

    stmt = text(sql)
    if pmids:
        stmt = stmt.bindparams(bindparam("pmid_list", expanding=True))

    with engine.connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()
    return [
        {"pmid": str(r["pmid"]), "title": r["title"] or "", "abstract": r["abstract"] or ""}
        for r in rows
    ]


def fetch_author_bylines(engine, pmids: list) -> dict:
    """Map pmid -> [resolved WCM-author CWIDs], from analysis_summary_author.

    Feeds the author-affinity signal (repeat-user prior): a confirmed (cwid, core)
    lights up every other pub that cwid authored. Only resolved authors
    (personIdentifier NOT NULL) are returned. Matched on personIdentifier, never
    on name.
    """
    from sqlalchemy import bindparam, text  # lazy

    if not pmids:
        return {}
    stmt = text(
        "SELECT pmid, personIdentifier FROM analysis_summary_author "
        "WHERE personIdentifier IS NOT NULL AND personIdentifier <> '' AND pmid IN :pmids"
    ).bindparams(bindparam("pmids", expanding=True))
    out: dict = {}
    with engine.connect() as conn:
        for row in conn.execute(stmt, {"pmids": [int(p) for p in pmids]}):
            out.setdefault(str(row.pmid), []).append(row.personIdentifier)
    return out
