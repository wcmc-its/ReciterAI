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
        {"pmid": str(r["pmid"]), "title": r["title"] or "", "abstract": r["abstract"] or "",
         "year": int(r["year"]) if r["year"] else None}
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


def fetch_corpus_size(engine) -> int:
    """How many publications the scoreable corpus (PUBLICATION_EXTRACTION_SQL) holds.

    The DENOMINATOR of a core's affinity base rate p0 (signals.affinity_base_rate): the
    share of the corpus that is the core's confirmed/claimed work. Same corpus as each
    author's total (fetch_author_totals) and the numerator gate (filter_corpus_pmids),
    so p0 and every author's rate are shares of one universe."""
    from sqlalchemy import text  # lazy
    from utils.sql_queries import PUBLICATION_EXTRACTION_SQL  # lazy

    corpus = PUBLICATION_EXTRACTION_SQL.replace("ORDER BY a1.pmid DESC", "")
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(DISTINCT corpus.pmid) FROM ({corpus}) corpus"))
                   .scalar() or 0)


def fetch_author_totals(engine, cwids: list = None) -> dict:
    """Map cwid -> {publication year: n}, that author's publications in the scoreable
    corpus, by year (sum the values for the plain total).

    The DENOMINATOR of the author x core affinity rate (signals.build_affinity_index):
    a cwid with 3 confirmed core papers out of 4 is a core regular, one with 3 out of
    300 passed through. By YEAR because the tenure gate drops the years an author was
    not at WCM from both sides of that ratio (and decay, when on, weights them).
    Restricted to the corpus PUBLICATION_EXTRACTION_SQL defines — the same publications
    this pipeline scores, ~82k pubs — by joining that query itself rather than
    restating its predicate, so the numerator and the denominator can never drift onto
    different corpora. Counting all of analysis_summary_author instead (392,769 pmids)
    deflates every rate by ~4x.

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
        "SELECT a.personIdentifier AS cwid, corpus.year AS year, COUNT(DISTINCT a.pmid) AS n "
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
    stmt = text(sql + " GROUP BY a.personIdentifier, corpus.year")
    if binds:
        stmt = stmt.bindparams(*binds)
    out: dict = {}
    with engine.connect() as conn:
        for row in conn.execute(stmt, params):
            year = int(row.year) if row.year else None
            out.setdefault(row.cwid, {})[year] = int(row.n)
    return out


def fetch_pub_years(engine, pmids) -> dict:
    """Map pmid -> publication year (analysis_summary_article.articleYear, the same
    column the corpus filter reads). The affinity tenure gate and decay key on it.
    DynamoDB's CORE# rows carry no year, so prior confirmations are dated here, from
    the source of record, rather than by widening scan_prior_core_usage's projection
    onto an attribute nothing writes."""
    from sqlalchemy import bindparam, text  # lazy

    pmids = sorted({int(p) for p in pmids})
    if not pmids:
        return {}
    stmt = text("SELECT pmid, articleYear AS year FROM analysis_summary_article "
                "WHERE pmid IN :pmids").bindparams(bindparam("pmids", expanding=True))
    with engine.connect() as conn:
        return {str(row.pmid): int(row.year) for row in conn.execute(stmt, {"pmids": pmids})
                if row.year}


def fetch_author_tenure(engine, cwids) -> dict:
    """Map cwid -> (start_year, end_year) of that person's WCM appointment span.

    Source: reciterdb `identity` (the ReCiter identity mirror of the Enterprise
    Directory), startDateWCMFaculty / endDateWCMFaculty and the Student pair, all
    YEARS. The span is the union: earliest start, latest end — a student who joined
    the faculty is one stay. A pair with a start and a NULL end is open-ended, and so
    is the span it belongs to. end None = open-ended (a current appointment carries a
    future sentinel year, which reads the same). A cwid with no identity row or no
    start date is ABSENT from the result and therefore not gated.

    Known blind spot, and the reason signals.TENURE_LAG_BEFORE is generous: postdocs,
    fellows, residents and research staff carry no dates here, so a researcher's start
    is their FACULTY appointment even when they arrived years earlier.
    """
    from sqlalchemy import bindparam, text  # lazy

    cwids = sorted(set(cwids))
    if not cwids:
        return {}
    stmt = text(
        "SELECT cwid, startDateWCMFaculty AS sf, endDateWCMFaculty AS ef, "
        "startDateWCMStudent AS ss, endDateWCMStudent AS es FROM identity WHERE cwid IN :cwids"
    ).bindparams(bindparam("cwids", expanding=True))
    out: dict = {}
    with engine.connect() as conn:
        for row in conn.execute(stmt, {"cwids": cwids}):
            pairs = [(st, en) for st, en in ((row.sf, row.ef), (row.ss, row.es)) if st]
            if not pairs:
                continue
            # A started appointment with a NULL end is OPEN, and an open appointment
            # makes the whole span open. max() over the non-NULL ends alone would drop
            # it: a student who graduated in 2015 and is now open-ended faculty would
            # read as gone in 2015, and lose every paper after 2017.
            ends = [en for _st, en in pairs]
            out[row.cwid] = (min(st for st, _en in pairs),
                             None if any(not en for en in ends) else max(ends))
    return out


def affinity_inputs(engine, papers: dict, *, years: dict = None) -> tuple:
    """(counts, totals, tenure, years) for signals.build_affinity_index, from
    cwid -> {core_id: {pmid, ...}}.

    The one place the rate's inputs are assembled, so run.py and the fitting script
    cannot drift apart on them: each confirmed paper is dated (years already known are
    passed in; the rest are read), counts become {year: n}, and the denominator and
    tenure are read for exactly the authors that have confirmations. Pass the returned
    `years` on to author_affinity for the papers being scored.

    `engine` None (unit tests) skips the two reads that only date things — every count
    is then year-unknown and nothing is gated — but still asks fetch_author_totals,
    which those tests stub.
    """
    from collections import Counter

    years = dict(years or {})
    need = {p for by_core in papers.values() for pmids in by_core.values() for p in pmids}
    missing = sorted(need - set(years))
    if missing and engine is not None:
        years.update(fetch_pub_years(engine, missing))
    counts = {cwid: {cid: dict(Counter(years.get(p) for p in pmids))
                     for cid, pmids in by_core.items() if pmids}
              for cwid, by_core in papers.items()}
    totals = fetch_author_totals(engine, list(counts))
    tenure = fetch_author_tenure(engine, list(counts)) if engine is not None else {}
    return counts, totals, tenure, years
