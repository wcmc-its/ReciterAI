"""ADR D5 layer 2 — taxonomy/data drift.

Artifact checks (layer 1) catch the cause; this catches the effect, including
causes nobody enumerated. The 2026-07-10 incident manifested here, in DynamoDB,
and was found here: `hematology_medical_oncology` was removed from the taxonomy
on 07-10 and the weekly hot run — still carrying the old bundled taxonomy —
re-minted its rows on 07-13, 07-20 and 07-27. Symmetrically, four topics added
by #339 had no rows at all because the deployed taxonomy had never heard of them.

Both directions are checked:

  ORPHAN   a `TOPIC#` partition whose topic is not in the taxonomy.
           Something is scoring against a taxonomy we no longer ship. ERROR.

  UNSCORED a taxonomy topic with no `TOPIC#` partition.
           Expected briefly after a topic is added, so WARN rather than ERROR —
           but it is the exact shape of "the new topics never reached prod".

The ADR's original second check — "every partition's `topic_scores_version`
corresponds to the current taxonomy hash" — is deliberately NOT implemented.
`topic_scores_version` is stamped from `taxonomy["taxonomy_version"]`, a static
family label; all 113,605 `TOPIC#` rows measured 2026-08-03 carry the identical
value `"taxonomy_v2"`, unchanged across all seven taxonomy edits. That check
would pass on 100% of rows by construction. A check that can only return "clean"
is worse than no check, so it is omitted rather than faked; see the ADR's D5
section for the amendment and the forward-only path.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DRIFT_PK = "DRIFT#taxonomy"

# `DRIFT#` rows use OK|WARN|ERROR (docs/data-model-and-queries.md); alerting.py
# accepts INFO|WARN|ERROR and raises ValueError on anything else. "OK" is a valid
# row severity and an invalid alert severity — only WARN/ERROR are dispatched.
ALERTABLE = ("WARN", "ERROR")


def evaluate(partition_ids: Iterable[str], taxonomy_ids: Iterable[str]) -> dict[str, Any]:
    """Compare the topic ids present in data against the taxonomy. Pure."""
    present = set(partition_ids)
    expected = set(taxonomy_ids)

    orphans = sorted(present - expected)
    unscored = sorted(expected - present)

    if orphans:
        severity = "ERROR"
    elif unscored:
        severity = "WARN"
    else:
        severity = "OK"

    return {
        "severity": severity,
        "orphan_topics": orphans,
        "unscored_topics": unscored,
        "partition_count": len(present),
        "taxonomy_topic_count": len(expected),
    }


def scan_topic_partitions(table: Any) -> dict[str, str]:
    """Return {topic_id: newest created_at seen} for every `TOPIC#` partition.

    A full Scan is unavoidable: the check must *discover* unexpected partitions,
    and no GSI hashes on the `TOPIC#` PK space. ~136 MB / 133 pages as of
    2026-08-03, which is acceptable on a daily cadence.

    `created_at` is absent on rows predating the field. It is reported as
    evidence of *when* an orphan was minted, never used to decide whether one
    exists — absence must not read as a violation.
    """
    newest: dict[str, str] = {}
    kwargs: dict[str, Any] = {
        "FilterExpression": "begins_with(#pk, :p)",
        "ExpressionAttributeNames": {"#pk": "PK"},
        "ExpressionAttributeValues": {":p": "TOPIC#"},
        "ProjectionExpression": "#pk, created_at",
    }
    last_key = None
    while True:
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            topic = item["PK"].split("#", 1)[1]
            created = item.get("created_at")
            if created and created > newest.get(topic, ""):
                newest[topic] = created
            newest.setdefault(topic, "")
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return newest


def run_check(table: Any, taxonomy_ids: Iterable[str], *, taxonomy_hash: str, day: str) -> dict:
    """Scan, evaluate, persist a `DRIFT#taxonomy` row, alert if actionable."""
    newest_by_topic = scan_topic_partitions(table)
    result = evaluate(newest_by_topic.keys(), taxonomy_ids)
    result["taxonomy_hash"] = taxonomy_hash
    # Minting dates for orphans only — this is what made the 07-10 incident legible.
    result["orphan_last_written"] = {t: newest_by_topic[t] for t in result["orphan_topics"]}

    table.put_item(
        Item={
            "PK": DRIFT_PK,
            "SK": f"DAY#{day}",
            "checked_at": day,
            **result,
        }
    )

    if result["severity"] in ALERTABLE:
        from pipeline_enrichment import alerting

        orphans = result["orphan_topics"]
        unscored = result["unscored_topics"]
        if orphans:
            title = "Taxonomy drift: retired topics still being scored"
            message = (
                f"{len(orphans)} topic(s) hold TOPIC# rows but are absent from the "
                f"taxonomy: {', '.join(orphans)}. Something is scoring against a "
                f"taxonomy that is no longer shipped — check for a stale deployed "
                f"artifact before the next weekly run."
            )
        else:
            title = "Taxonomy drift: topics added but never scored"
            message = (
                f"{len(unscored)} taxonomy topic(s) have no TOPIC# rows: "
                f"{', '.join(unscored)}. Expected briefly after a topic is added; "
                f"if it persists past a weekly run, the new taxonomy has not reached prod."
            )

        alerting.alert(
            result["severity"],
            title,
            message,
            {
                "source": "pipeline_taxonomy_drift.checker",
                "orphan_topics": orphans,
                "unscored_topics": unscored,
                "orphan_last_written": result["orphan_last_written"],
                "taxonomy_hash": taxonomy_hash,
                "partition_count": result["partition_count"],
                "taxonomy_topic_count": result["taxonomy_topic_count"],
            },
            mention=bool(orphans),
        )

    return result


def handler(event, context):  # pragma: no cover - thin Lambda entrypoint
    from utils.dynamodb_helpers import get_table
    from utils.iso_clock import now_iso
    from utils.taxonomy import current_content_hash, topic_ids

    result = run_check(
        get_table(),
        topic_ids(),
        taxonomy_hash=current_content_hash(),
        day=now_iso()[:10],
    )
    logger.info("taxonomy drift check: %s", result["severity"])
    return result
