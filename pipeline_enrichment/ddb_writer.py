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
    model           string  — dated OpenAI response.model from impact call
    synopsis        string  — bounded by SYNOPSIS_SCHEMA.synopsis.maxLength
    synopsis_model  string  — dated OpenAI response.model from synopsis call
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
