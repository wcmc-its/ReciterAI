"""DynamoDB writer for the daily enrichment job (#37 step 3, PR 3.1).

Composes IMPACT#pmid_{pmid} / SK SCORE items carrying both impact and
synopsis attributes, then batch-writes them at end-of-run alongside the
existing MariaDB writes (dual-write).

Item shape — locked in `~/Dropbox/Projects/ReciterAI - Planning/
37-step-3-ddb-dual-write-PLAN.md` §"SPS reader contract":

    PK              IMPACT#pmid_{pmid}
    SK              SCORE
    pmid            string
    impact_score    int 0–100
    justification   string  — bounded by IMPACT_SCHEMA.justification.maxLength
    model           string  — model actually used for the impact call (the
                              Bedrock Sonnet model ID on the happy path, or
                              `gpt-5.1` when the content-filter fallback fired)
    synopsis        string  — bounded by SYNOPSIS_SCHEMA.synopsis.maxLength
    synopsis_model  string  — model actually used for the synopsis call
                              (same rule as `model` — Sonnet primary, gpt-5.1
                              fallback)
    enriched_at     string  — ISO 8601 (UTC, "Z"); single timestamp for
                              both attributes since they're computed within
                              seconds of each other in the same per-pmid
                              call

`hierarchy_version` is intentionally omitted: daily_enrichment has no
hierarchy concept (that lives in pipeline_hot's topic-scoring world). The
plan's earlier contract sketch listed it; dropped during 3.1 because no
consumer reads it and the daily job has no source to populate it.

Failure model: this module raises on a write batch that fails to drain
UnprocessedItems within the underlying Table.batch_writer's retry budget.
The orchestrator treats that as a run failure (watermark stays unadvanced;
next run re-attempts the same delta — DDB writes are idempotent upserts).
"""

from __future__ import annotations

import logging
from typing import Any

from boto3.dynamodb.conditions import Attr

logger = logging.getLogger(__name__)

IMPACT_PK_PREFIX = "IMPACT#pmid_"
IMPACT_SK = "SCORE"


def build_impact_item(
    *,
    pmid: str,
    impact_score: int,
    justification: str,
    impact_model: str,
    synopsis: str,
    synopsis_model: str,
    enriched_at: str,
) -> dict[str, Any]:
    """Pure builder for one IMPACT# row. No I/O.

    Returns a plain Python dict suitable for `table.put_item(Item=...)`
    or `table.batch_writer().put_item(Item=...)`. The DDB Table resource
    serializes Python types automatically; impact_score stays `int` (DDB
    Number) — no Decimal coercion needed because the value is whole.
    """
    return {
        "PK": f"{IMPACT_PK_PREFIX}{pmid}",
        "SK": IMPACT_SK,
        "pmid": str(pmid),
        "impact_score": int(impact_score),
        "justification": justification,
        "model": impact_model,
        "synopsis": synopsis,
        "synopsis_model": synopsis_model,
        "enriched_at": enriched_at,
    }


def write_impact_batch(table: Any, items: list[dict[str, Any]]) -> int:
    """Batch-write IMPACT# items via the Table resource's batch_writer.

    `table.batch_writer()` handles 25-item chunking and unprocessed-items
    retry automatically (see boto3 docs). Items are upserts: re-writing
    the same PK/SK is idempotent.

    Returns the number of items written. Raises whatever boto3 raises if
    the batch fails to drain — caller decides how to surface the error.
    """
    if not items:
        return 0
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    logger.info("ddb_writer: wrote %d IMPACT# items", len(items))
    return len(items)


def propagate_impact_to_topic_rows(table: Any, items: list[dict[str, Any]]) -> int:
    """Back-propagate the freshly-enriched ``impact_score`` onto every existing
    ``TOPIC#.../SCORE#...`` row for each enriched PMID (#212 Part B).

    Closes the daily / pre-existing ordering: when a PMID's ``TOPIC#`` rows
    were minted *before* its impact enrichment landed, those rows carry no
    ``impact_score`` and downstream representative-paper ranking coalesces the
    absent attribute to 0 — collapsing ``article_score`` and dropping a
    genuinely on-topic paper from the spotlight pool. Part A covers the reverse
    ordering (IMPACT# already exists at Score time) at build time; this hook
    covers TOPIC#-before-enrichment when enrichment finally lands.

    Reuses ``compute_top_topic.fetch_activity_rows_for_pmid`` — the proven
    ``PmidIndex`` GSI query that already filters to ``TOPIC#`` PKs with
    ``SCORE#`` SKs, so faculty / PROCESSING# / STAGE# rows the GSI also
    surfaces are never touched, and pagination is handled.

    Write policy — **overwrite-if-different**, NOT ``attribute_not_exists``-only.
    The freshly-enriched value is authoritative-latest: an annual ``--full``
    rescore re-computes ``impact_score`` for already-enriched PMIDs, and an
    exists-only guard would silently keep the stale copy on populated rows.
    The equal-value skip keeps the steady state cheap (zero writes when nothing
    changed → idempotent re-runs); the ``not_exists() | ne()`` condition makes
    the write self-cancel under a concurrent race.

    ``impact_justification`` is set only when the IMPACT# row carries a
    non-empty justification — mirroring the stopgap backfill.

    Idempotent and **best-effort**: the caller runs this AFTER the watermark
    commits and must not fail the run on a propagation error — ``impact_score``
    on ``TOPIC#`` is a derived copy, the ``IMPACT#`` row is the source of truth.

    Args:
        table: a boto3 DynamoDB Table *resource* (not the low-level client).
            ``fetch_activity_rows_for_pmid`` and ``update_item`` use the
            resource idiom (Key/Attr, Decimal-typed values).
        items: the IMPACT# items already built this run (the
            ``build_impact_item`` output). Each must carry ``pmid`` and
            ``impact_score``; ``justification`` is optional.

    Returns:
        The number of ``TOPIC#`` rows written (rows already at the correct
        value are skipped and not counted).
    """
    # Imported lazily so importing ddb_writer (a leaf module) never pulls in
    # compute_top_topic's boto3 / Config module-level setup.
    from compute_top_topic import fetch_activity_rows_for_pmid

    written = 0
    for it in items:
        pmid = str(it["pmid"])
        score = float(it["impact_score"])  # Table resource → Decimal → float, matches consumers
        just = it.get("justification") or ""
        for row in fetch_activity_rows_for_pmid(table, pmid):
            # Skip rows already at the correct value — compare with float() so a
            # Decimal("50")/int(50)/50.0 copy all read equal (matches consumers).
            existing = row.get("impact_score")
            if existing is not None and float(existing) == score:
                continue
            expr = "SET impact_score = :s"
            vals: dict[str, Any] = {":s": it["impact_score"]}
            if just:
                expr += ", impact_justification = :j"
                vals[":j"] = just
            table.update_item(
                Key={"PK": row["PK"], "SK": row["SK"]},
                UpdateExpression=expr,
                ConditionExpression=(
                    Attr("impact_score").not_exists()
                    | Attr("impact_score").ne(it["impact_score"])
                ),
                ExpressionAttributeValues=vals,
            )
            written += 1
    if written:
        logger.info(
            "ddb_writer: back-propagated impact_score to %d TOPIC# row(s) "
            "across %d PMID(s)",
            written, len(items),
        )
    return written
