"""Out-of-band warm of the PMC full-text cache (S3-backed).

Pre-populates s3://<artifacts bucket>/cores/fulltext/{pmid}.xml (and the local
disk cache) so a full-corpus cores run reads from a warm cache instead of
re-fetching ~10K PMIDs from NCBI on every cold host. This is the slow, network-
bound step (elink + efetch, rate-limited); doing it once, out of band, keeps the
scoring run fast and reproducible.

    python3 -m pipeline_cores.prefetch_fulltext                 # warm the whole corpus -> disk + S3
    python3 -m pipeline_cores.prefetch_fulltext --test 200      # first N publications
    python3 -m pipeline_cores.prefetch_fulltext --pmids-file pmids.txt   # explicit PMID list (no DB)
    python3 -m pipeline_cores.prefetch_fulltext --no-s3         # disk cache only (no AWS)

Negative results (PMIDs with no PMC record) are cached durably too, so a second
warm is a near-no-op. S3 is best-effort: write failures are counted and reported
but do not stop the warm (the local disk cache still fills).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("pipeline_cores.prefetch_fulltext")


def _read_pmids_file(path: str) -> list:
    pmids = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        tok = line.strip()
        if tok and not tok.startswith("#"):
            pmids.append(tok)
    return pmids


def _corpus_pmids(limit) -> list:
    """PMIDs from the same corpus the scoring run uses (lazy DB import)."""
    from pipeline_cores import ingest  # lazy
    from utils.db import get_engine  # lazy

    pubs = ingest.fetch_publications(get_engine(), limit=limit)
    return [p["pmid"] for p in pubs]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Warm the PMC full-text cache (disk + S3) for the cores pipeline")
    ap.add_argument("--test", type=int, help="limit to the first N corpus publications")
    ap.add_argument("--pmids-file", help="warm an explicit PMID list (one per line; '#' comments ok) — skips the DB")
    ap.add_argument("--no-s3", action="store_true", help="disk cache only; do not read or write S3")
    ap.add_argument("--cache-dir", help="override the local disk cache directory")
    ap.add_argument("--bucket", help="override the S3 artifacts bucket")
    ap.add_argument("--progress", type=int, default=200, help="log a progress line every N pmids (default 200)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from pipeline_cores.fulltext import PmcFullTextClient  # lazy

    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    if args.no_s3:
        client = PmcFullTextClient(cache_dir=cache_dir)
        backend = "disk only"
    else:
        client = PmcFullTextClient.with_s3(cache_dir=cache_dir, bucket=args.bucket)
        backend = f"disk + s3://{client.s3.bucket}/{client.s3_prefix}"

    pmids = _read_pmids_file(args.pmids_file) if args.pmids_file else _corpus_pmids(args.test)
    total = len(pmids)
    logger.info("warming %d pmids into %s", total, backend)

    with_fulltext = 0
    for i, pmid in enumerate(pmids, 1):
        if client.get(pmid):
            with_fulltext += 1
        if args.progress and i % args.progress == 0:
            logger.info("  %d/%d warmed (%d with full text)", i, total, with_fulltext)

    no_pmc = total - with_fulltext
    logger.info(
        "done: %d pmids -> %d with full text, %d no-PMC; S3 read failures=%d, write failures=%d",
        total, with_fulltext, no_pmc, client.s3_read_failures, client.s3_write_failures,
    )
    if not args.no_s3 and client.s3_write_failures:
        logger.warning(
            "%d/%d S3 writes failed — the shared cache is NOT fully warm (check AWS creds / bucket perms)",
            client.s3_write_failures, total,
        )


if __name__ == "__main__":
    main()
