"""Cross-source dedup of the GRANT# corpus (drop-one — losers deleted, never merged).

The deterministic ``opportunity_id`` (``<source>:<slug-or-id>``) only collapses exact
re-ingests within one source. The same opportunity listed under a second source
(``grants_gov`` vs ``spin`` vs the curated funding-DB scrape), or re-listed with
slightly different title text, survives as a true duplicate — measured on the
2026-06-28 scrape this hit the matcher's gold-standard grants (Hartwell, WorldQuant,
Keck). See docs/funding-db-ingest-runbook.md ("Overlap" / "Residual").

Key: normalized (title, sponsor) — lowercase, punctuation-split, stopword-stripped
token sets. Records whose sponsor is blank (common on wcm_curated rows) group by
token-identical title alone; when one title has SEVERAL distinct sponsored clusters,
a blank-sponsor record is ambiguous and stays un-grouped (conservative — it still
dedups against other blank-sponsor records, it never guesses a sponsor).

Winner per cluster: highest source priority ``grants_gov > spin > wcm_curated >
manual_url`` (an unknown source ranks below all four); ties keep the most recently
ingested. Losers are deleted whole — no attribute merging.

Two consumers:
  * the batch CLI below (a backfill pass over the existing corpus), and
  * ``CorpusKeyIndex`` — the ingest-time guard: skip an incoming opportunity whose
    key is already held by an equal-or-higher-priority source, so the corpus does
    not re-grow the duplicates the batch pass removes.

Dry-run is the DEFAULT and prints every loser with its winner:
    python -m pipeline_grants.dedupe             # report only, no writes
    python -m pipeline_grants.dedupe --apply     # DELETE the losing GRANT# items

Review the dry-run before ``--apply`` — the measured overlap includes the matcher's
gold-standard grants. Deleting a GRANT# row does NOT remove an already-projected
SPS Opportunity/OpenSearch row (the SPS projection is upsert-only); the operator
must sweep those on the SPS side after an ``--apply``.
"""
import argparse
import logging
import re

from utils.dynamodb_helpers import TABLE_NAME, get_dynamo_client

log = logging.getLogger("pipeline_grants.dedupe")

# Base articles/prepositions plus funding-generic terms (the runbook's residual-dup
# normalization), so "Hartwell Foundation - Individual Biomedical Research Award"
# and "The Hartwell Foundation Individual Biomedical Research Award" share one key.
_STOPWORDS = frozenset(
    "a an and for in of on the to with "
    "research award awards grant grants program fellowship foundation fund".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Higher wins. Priority reflects record richness/authority: grants_gov carries the
# fullest structured metadata; manual_url is a staff-pasted page. Unknown sources
# rank below all four (they lose every cluster — dry-run review catches surprises).
SOURCE_PRIORITY = {"grants_gov": 3, "spin": 2, "wcm_curated": 1, "manual_url": 0}


def source_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source or "", -1)


def norm_tokens(text: str) -> frozenset:
    """Lowercased, punctuation-split, stopword-stripped token set."""
    return frozenset(_TOKEN_RE.findall((text or "").lower())) - _STOPWORDS


# ---------------------------------------------------------------------------
# Batch pass (pure clustering + the scan/delete plumbing around it)
# ---------------------------------------------------------------------------


def scan_grants(client, *, table_name: str = TABLE_NAME) -> list:
    """Every GRANT#/META row's dedup-relevant fields (paged scan)."""
    records = []
    pages = client.get_paginator("scan").paginate(
        TableName=table_name,
        FilterExpression="begins_with(PK, :g) AND SK = :m",
        ExpressionAttributeValues={":g": {"S": "GRANT#"}, ":m": {"S": "META"}},
        ProjectionExpression="#oid, #src, #t, sponsor, ingested_at",
        ExpressionAttributeNames={"#oid": "opportunity_id", "#src": "source", "#t": "title"},
    )
    for page in pages:
        for item in page.get("Items", []):
            oid = item.get("opportunity_id", {}).get("S", "")
            if not oid:
                continue
            records.append({
                "opportunity_id": oid,
                "source": item.get("source", {}).get("S", ""),
                "title": item.get("title", {}).get("S", ""),
                "sponsor": item.get("sponsor", {}).get("S", ""),
                "ingested_at": item.get("ingested_at", {}).get("S", ""),
            })
    return records


def _sponsor_buckets(title_group: list) -> list:
    """Split one same-title group into duplicate clusters by sponsor tokens.

    Blank-sponsor records fold into the sole sponsored cluster when there is exactly
    one (unambiguous); with several sponsored clusters they stay a cluster of their
    own — deduping blank-vs-blank without ever guessing a sponsor.
    """
    sponsored, blank = {}, []
    for rec in title_group:
        tokens = norm_tokens(rec.get("sponsor", ""))
        (sponsored.setdefault(tokens, []) if tokens else blank).append(rec)
    buckets = list(sponsored.values())
    if blank:
        if len(buckets) == 1:
            buckets[0].extend(blank)
        else:
            buckets.append(blank)
    return buckets


def find_duplicates(records: list) -> list:
    """Cluster on the normalized key; return ``[{"winner", "losers"}]`` per cluster >= 2.

    Winner = highest source priority, then most recent ``ingested_at``, then
    ``opportunity_id`` (a pure determinism tiebreak). All-stopword/empty titles are
    un-keyable and never grouped.
    """
    by_title = {}
    for rec in records:
        tokens = norm_tokens(rec.get("title", ""))
        if tokens:
            by_title.setdefault(tokens, []).append(rec)

    groups = []
    for title_group in by_title.values():
        for cluster in _sponsor_buckets(title_group):
            if len(cluster) < 2:
                continue
            ordered = sorted(
                cluster,
                key=lambda r: (source_rank(r.get("source", "")),
                               r.get("ingested_at", ""), r.get("opportunity_id", "")),
                reverse=True,
            )
            groups.append({"winner": ordered[0], "losers": ordered[1:]})
    groups.sort(key=lambda g: g["winner"]["opportunity_id"])
    return groups


def delete_grant_items(client, opportunity_ids: list, *, table_name: str = TABLE_NAME) -> int:
    """Delete ``GRANT#{id}``/META rows one by one (idempotent; loser sets are small)."""
    for oid in opportunity_ids:
        client.delete_item(TableName=table_name,
                           Key={"PK": {"S": f"GRANT#{oid}"}, "SK": {"S": "META"}})
    return len(opportunity_ids)


def run(*, apply: bool = False, table_name: str = TABLE_NAME) -> dict:
    """Scan, cluster, report every loser with its winner; delete only under --apply."""
    client = get_dynamo_client()
    records = scan_grants(client, table_name=table_name)
    groups = find_duplicates(records)
    loser_ids = []
    for group in groups:
        winner = group["winner"]
        for loser in group["losers"]:
            loser_ids.append(loser["opportunity_id"])
            log.info("%s %s (%s) -> kept by %s (%s) title=%r",
                     "DELETE" if apply else "would-delete",
                     loser["opportunity_id"], loser["source"],
                     winner["opportunity_id"], winner["source"], winner["title"])
    deleted = delete_grant_items(client, loser_ids, table_name=table_name) if apply else 0
    summary = {"grants": len(records), "duplicate_groups": len(groups),
               "losers": len(loser_ids), "deleted": deleted, "dry_run": not apply}
    log.info("cross-source dedup summary: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Ingest-time guard
# ---------------------------------------------------------------------------


class CorpusKeyIndex:
    """Normalized-key view of the GRANT# corpus for ingest-time duplicate skipping.

    Mutable: callers ``add`` each opportunity they keep, so later items in the same
    run dedup against earlier ones exactly like the persisted corpus.
    """

    def __init__(self):
        self._by_title = {}   # title tokens -> [{"opportunity_id", "source", "sponsor_tokens"}]

    def add(self, *, opportunity_id: str, source: str, title: str, sponsor: str) -> None:
        tokens = norm_tokens(title)
        if not tokens:
            return
        self._by_title.setdefault(tokens, []).append({
            "opportunity_id": opportunity_id,
            "source": source,
            "sponsor_tokens": norm_tokens(sponsor),
        })

    def blocking_id(self, *, opportunity_id: str, source: str, title: str, sponsor: str):
        """The existing opportunity_id that makes the incoming one redundant, else None.

        Blocks on the same normalized key — blank sponsor on EITHER side matches any
        sponsor — held by an equal-or-higher-priority source. A key match on the same
        ``opportunity_id`` is a refresh of that record, never a block.
        """
        tokens = norm_tokens(title)
        if not tokens:
            return None
        sponsor_tokens = norm_tokens(sponsor)
        rank = source_rank(source)
        best = None
        for rec in self._by_title.get(tokens, []):
            if rec["opportunity_id"] == opportunity_id:
                continue
            if sponsor_tokens and rec["sponsor_tokens"] and rec["sponsor_tokens"] != sponsor_tokens:
                continue
            if source_rank(rec["source"]) < rank:
                continue
            if best is None or source_rank(rec["source"]) > source_rank(best["source"]):
                best = rec
        return best["opportunity_id"] if best else None


def load_corpus_key_index(client, table_name: str = TABLE_NAME) -> CorpusKeyIndex:
    """Build the guard index from every persisted GRANT#/META row (paged scan)."""
    index = CorpusKeyIndex()
    for rec in scan_grants(client, table_name=table_name):
        index.add(opportunity_id=rec["opportunity_id"], source=rec["source"],
                  title=rec["title"], sponsor=rec["sponsor"])
    return index


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(
        description="Cross-source GRANT# dedup — drop-one on normalized (title, sponsor); "
                    "dry-run by default")
    p.add_argument("--apply", action="store_true",
                   help="ACTUALLY delete the losing GRANT# items "
                        "(default: dry-run report of every loser with its winner)")
    args = p.parse_args(argv)
    run(apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
