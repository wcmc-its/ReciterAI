"""Backfill the DENSE relevance map (``match_rel``) on existing GRANT# items (§3).

``match_rel`` = ``{pmid: cosine∈[0,1]}`` (Bedrock Titan v2, the validated dense scratch
``embed_rel.py`` promoted) — a PRECOMPUTED dense alternative to the matcher's live BM25 relevance
boost, for the SAME ``{pmid: rel}`` seam (``variantB × (1 + REL_BOOST·rel)``). Dense rescues
lexically-distant on-target pubs and demotes shallow-lexical hits; the SPS matcher prefers it
when present and falls back to BM25 when absent.

NOT compiled at ingest: drawing a grant's pool needs the corpus-wide subtopic→pmid index, which
is cheap to build ONCE here (one query per topic, ~68 TOPIC# partitions — not a table scan) but
absurd per-grant. So this is backfill-only and additive: an ``UpdateItem`` of just ``match_rel``
on grants that already carry ``match_dsl`` (rel draws its pool from the DSL's ``require``).

Idempotent + resumable: items already carrying ``match_rel`` are skipped (``--overwrite``
recompiles). ``--dry-run`` builds the index and reports candidate grants + total pool pmids (the
embedding estimate) with NO Bedrock and NO writes. ``--limit N`` is a canary.

Run: python -m pipeline_grants.backfill_rel [--dry-run] [--limit N] [--overwrite]
"""
import argparse
import json
import logging
from pathlib import Path

from boto3.dynamodb.conditions import Key

from pipeline_grants.match_compile import compile_rel, pool_pmids, rel_attr
from pipeline_tools.embeddings import EmbeddingCache
from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client, get_table

log = logging.getLogger("pipeline_grants.backfill_rel")

TAXONOMY_FILE = Path(__file__).parent.parent / "taxonomy_v2.json"
# Safety cap on pmids embedded per grant. ponytail: arbitrary ceiling that almost never trips
# (a `require` set is distinctive, so pools are modest); the rel-floor already bounds STORAGE,
# this only bounds EMBED cost on a pathological broad-require grant. Raise if a real pool exceeds it.
MAX_POOL = 5000


def _s(item, key):
    """Read a DynamoDB String attribute (low-level client format); '' when absent."""
    return (item.get(key) or {}).get("S", "")


def _topic_ids() -> list:
    with open(TAXONOMY_FILE) as f:
        return [t["id"] for t in json.load(f).get("topics", [])]


def load_subtopic_pmid_index(topic_ids=None) -> dict:
    """``{pmid: primary_subtopic_id}`` across the whole corpus, from the ``TOPIC#<t>`` ``SCORE#``
    partitions (one paginated query per topic). A pmid keeps the first primary seen — its subtopic
    assignment is per-pmid, so every SCORE# row for it carries the same ``primary_subtopic_id``."""
    topic_ids = topic_ids if topic_ids is not None else _topic_ids()
    table = get_table(TABLE_NAME)
    index: dict = {}
    for tid in topic_ids:
        last = None
        while True:
            kwargs = {
                "KeyConditionExpression": Key("PK").eq(f"TOPIC#{tid}") & Key("SK").begins_with("SCORE#"),
                "ProjectionExpression": "pmid, primary_subtopic_id",
                "Limit": 1000,
            }
            if last:
                kwargs["ExclusiveStartKey"] = last
            resp = table.query(**kwargs)
            for r in resp.get("Items", []):
                pmid, sid = r.get("pmid"), r.get("primary_subtopic_id")
                if pmid and sid:
                    index.setdefault(str(pmid), sid)
            last = resp.get("LastEvaluatedKey")
            if not last:
                break
    log.info("subtopic→pmid index: %d pmids across %d topics", len(index), len(topic_ids))
    return index


def fetch_pool_abstracts(pmids: list) -> dict:
    """``{pmid: "title. abstract"}`` for pmids with a non-empty abstract, from ReciterDB
    (``analysis_summary_article`` ⋈ ``reporting_abstracts``). Title is prefixed when present
    (mirrors the validated scratch embed text); abstract-less pmids are dropped (Titan needs text)."""
    out: dict = {}
    if not pmids:
        return out
    from utils.sql_queries import get_raw_db_connection

    conn = get_raw_db_connection()
    try:
        cur = conn.cursor()
        for i in range(0, len(pmids), 1000):
            chunk = pmids[i : i + 1000]
            fmt = ",".join(["%s"] * len(chunk))
            cur.execute(
                "SELECT a.pmid, a.articleTitle, r.abstractVarchar "
                "FROM analysis_summary_article a "
                "LEFT JOIN reporting_abstracts r ON r.pmid = a.pmid "
                f"WHERE a.pmid IN ({fmt})",
                chunk,
            )
            for row in cur.fetchall():
                abstract = (row.get("abstractVarchar") or "").strip()
                if not abstract:
                    continue
                title = (row.get("articleTitle") or "").strip()
                out[str(row["pmid"])] = f"{title}. {abstract}" if title else abstract
    finally:
        conn.close()
    return out


def run(*, table_name=TABLE_NAME, limit=None, overwrite=False, dry_run=False) -> dict:
    """Scan GRANT#/META items and add ``match_rel`` to those with a ``match_dsl`` but no rel yet.

    Returns counts incl. ``pool_pmids_total`` — in a dry run that's the number of abstracts the
    real run will embed (the cost estimate)."""
    client = get_dynamo_client()
    log.info("building subtopic→pmid index …")
    index = load_subtopic_pmid_index()
    if not index:
        log.error("subtopic→pmid index empty — aborting (no publications scored?)")
        return {"error": "no_index"}

    # The corpus has only ~10k unique scored pmids but ~35x as many pool-memberships across grants,
    # so fetch every abstract ONCE and embed each ONCE (shared EmbeddingCache, keyed by text). A
    # per-grant fetch+embed would redo the same work ~35x — real Titan throttle + wall-time, ~no
    # memory saving (~85MB of vectors). Skipped in dry-run (that's the free count).
    abstracts_all = {} if dry_run else fetch_pool_abstracts(list(index))
    cache = EmbeddingCache()
    pool_seen: set = set()

    scanned = candidates = compiled = skipped = failed = pool_total = 0
    done = False
    pages = client.get_paginator("scan").paginate(
        TableName=table_name,
        FilterExpression="begins_with(PK, :p) AND SK = :m",
        ExpressionAttributeValues={":p": {"S": "GRANT#"}, ":m": {"S": "META"}},
    )
    for page in pages:
        for item in page.get("Items", []):
            scanned += 1
            if "match_dsl" not in item:            # rel draws its pool from the DSL's `require`
                skipped += 1
                continue
            if not overwrite and "match_rel" in item:
                skipped += 1
                continue
            try:
                dsl = json.loads(_s(item, "match_dsl") or "{}")
            except Exception:  # noqa: BLE001 - a corrupt DSL blob must not abort the backfill
                skipped += 1
                continue
            pmids = pool_pmids(index, dsl.get("require") or [])
            if not pmids:
                skipped += 1
                continue
            candidates += 1
            if len(pmids) > MAX_POOL:
                log.warning("%s: pool %d > cap %d — capping", _s(item, "PK"), len(pmids), MAX_POOL)
                pmids = pmids[:MAX_POOL]
            pool_total += len(pmids)
            pool_seen.update(pmids)
            if dry_run:
                continue
            try:
                pool_abstracts = {p: abstracts_all[p] for p in pmids if p in abstracts_all}
                solicitation = f"{_s(item, 'title')}. {_s(item, 'synopsis')}".strip()
                attrs = rel_attr(compile_rel(solicitation, pool_abstracts, embed=cache.get_many))
                if not attrs:  # no abstracts, or every cosine below floor -> nothing to write
                    skipped += 1
                    continue
                client.update_item(
                    TableName=table_name,
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    UpdateExpression="SET match_rel = :r",
                    ExpressionAttributeValues={":r": attrs["match_rel"]},
                )
                compiled += 1
            except Exception as exc:  # noqa: BLE001 - one bad item must not abort the backfill
                failed += 1
                log.warning("backfill_rel failed for %s: %s", _s(item, "PK"), exc)
            if limit is not None and compiled >= limit:
                done = True
                break
        if done:
            break

    summary = {"scanned": scanned, "candidates": candidates, "compiled": compiled,
               "skipped": skipped, "failed": failed, "pool_pmids_total": pool_total,
               "unique_pool_pmids": len(pool_seen), "dry_run": dry_run}
    log.info("backfill_rel summary: %s", summary)
    return summary


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Backfill the dense match_rel map on GRANT# items.")
    p.add_argument("--dry-run", action="store_true", help="build index + count candidates/pool pmids; no Bedrock, no writes")
    p.add_argument("--limit", type=int, default=None, help="cap the number of grants compiled (canary run)")
    p.add_argument("--overwrite", action="store_true", help="recompile items that already have match_rel")
    args = p.parse_args(argv)
    run(limit=args.limit, overwrite=args.overwrite, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
