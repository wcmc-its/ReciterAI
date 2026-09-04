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


def filter_corpus_pmids(engine, pmids) -> set:
    """Which of `pmids` are in the scoreable corpus PUBLICATION_EXTRACTION_SQL defines.

    The affinity rate's NUMERATOR gate, and the twin of fetch_author_totals below. The
    denominator is corpus-restricted by construction; the numerator is NOT, because it
    comes from DynamoDB, which holds confirmed/claimed rows for papers this pipeline does
    not score (pre-2020, Letter, Preprint, Erratum, Case Report, Comment — 19 of core 14's
    43 prior rows). Counting those against a corpus-restricted denominator yields rates
    above 1 that floor into the STRONGEST bucket: cwid `ccole` measured 8/11 = 0.727 ->
    `aff:core`, which alone clears the confirm bar, where the same-corpus rate is
    2/11 = 0.182 -> `aff:regular`. Both sides of the ratio have to mean the same thing.

    This is also the universe the weights were fitted on — every core-2 fit confirm is
    in-corpus — so production must gate the numerator the same way or it is scoring
    against weights fitted on a different feature.
    """
    from sqlalchemy import bindparam, text  # lazy
    from utils.sql_queries import PUBLICATION_EXTRACTION_SQL  # lazy

    pmids = [int(p) for p in pmids]
    if not pmids:
        return set()
    corpus = PUBLICATION_EXTRACTION_SQL.replace("ORDER BY a1.pmid DESC", "")
    stmt = text(f"SELECT corpus.pmid AS pmid FROM ({corpus}) corpus WHERE corpus.pmid IN :pmids"
                ).bindparams(bindparam("pmids", expanding=True))
    with engine.connect() as conn:
        return {str(row.pmid) for row in conn.execute(stmt, {"pmids": pmids})}


def fetch_author_totals(engine, cwids: list = None) -> dict:
    """Map cwid -> that author's TOTAL publications in the scoreable corpus.

    The DENOMINATOR of the author x core affinity rate (signals.build_affinity_index):
    a cwid with 3 confirmed core papers out of 4 is a core regular, one with 3 out of
    300 passed through. Restricted to the corpus PUBLICATION_EXTRACTION_SQL defines —
    the same publications this pipeline scores, 80,203 pubs / 13,960 resolved authors —
    by joining that query itself rather than restating its predicate, so the numerator
    and the denominator can never drift onto different corpora. Counting all of
    analysis_summary_author instead (392,769 pmids) deflates every rate by ~4x.

    `cwids` restricts to a set (pass the authors that actually have confirmations);
    None counts every resolved author in the corpus. An EMPTY list means empty, not
    "everyone", and costs no query. Only resolved authors (personIdentifier NOT NULL)
    are counted, matching fetch_author_bylines.
    """
    from sqlalchemy import bindparam, text  # lazy
    from utils.sql_queries import PUBLICATION_EXTRACTION_SQL  # lazy

    if cwids is not None and not cwids:
        return {}
    corpus = PUBLICATION_EXTRACTION_SQL.replace("ORDER BY a1.pmid DESC", "")
    sql = (
        "SELECT a.personIdentifier AS cwid, COUNT(DISTINCT a.pmid) AS n "
        "FROM analysis_summary_author a "
        f"JOIN ({corpus}) corpus ON corpus.pmid = a.pmid "
        "WHERE a.personIdentifier IS NOT NULL AND a.personIdentifier <> ''"
    )
    params: dict = {}
    binds = []
    if cwids:
        sql += " AND a.personIdentifier IN :cwids"
        binds.append(bindparam("cwids", expanding=True))
        params["cwids"] = list(cwids)
    stmt = text(sql + " GROUP BY a.personIdentifier")
    if binds:
        stmt = stmt.bindparams(*binds)
    with engine.connect() as conn:
        return {row.cwid: int(row.n) for row in conn.execute(stmt, params)}
