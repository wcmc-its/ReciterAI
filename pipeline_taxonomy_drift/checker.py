"""ADR D5 layer 2 — taxonomy/data drift.

Artifact checks (layer 1) catch the cause; this catches the effect, including
causes nobody enumerated. The 2026-07-10 incident manifested here, in DynamoDB,
and was found here: `hematology_medical_oncology` was removed from the taxonomy
on 07-10 and the weekly hot run — still carrying the old bundled taxonomy —
re-minted its rows on 07-13, 07-20 and 07-27. Symmetrically, four topics added
by #339 had no rows at all because the deployed taxonomy had never heard of them.

Two independent replicas are checked against the taxonomy — the `TOPIC#`
partition space and the `TAXONOMY#{version}/META` catalog record:

  ORPHAN   a `TOPIC#` partition whose topic is not in the taxonomy.
           Something is scoring against a taxonomy we no longer ship. ERROR.

  UNSCORED a taxonomy topic with no `TOPIC#` partition.
           Expected briefly after a topic is added, so WARN rather than ERROR —
           but it is the exact shape of "the new topics never reached prod".

  CATALOG  `TAXONOMY#{version}/META` disagreeing with the taxonomy in either
           direction, or being unreadable. ERROR — downstream consumers
           enumerate topics from that record, so a stale one reaches users
           immediately.

The catalog check exists because omitting it made this checker useless against
the incident it should have caught first. Run against prod on 2026-08-03 it
reported a clean `OK — 69 partitions vs 69 taxonomy ids, 0 orphans` while the
catalog record said 70 and SPS was serving `neuroscience_neurology`, retired
two days earlier (#355). Partition drift and catalog drift are independent:
`retire_topic` used to delete the rows without refreshing the record, so the
partitions were correct and the catalog was not. Checking one replica says
nothing about the other, and a green report on a partial check is worse than
no check — it was read as evidence the taxonomy had propagated.

The ADR's original second check — "every partition's `topic_scores_version`
corresponds to the current taxonomy hash" — is deliberately NOT implemented.
`topic_scores_version` is stamped from `taxonomy["taxonomy_version"]`, a static
family label; all 113,605 `TOPIC#` rows measured 2026-08-03 carry the identical
value `"taxonomy_v2"`, unchanged across all seven post-creation edits to the
file. That check would pass on 100% of rows by construction. A check that can
only return "clean" is worse than no check, so it is omitted rather than faked;
see the ADR's D5 section for the amendment and the forward-only path.

The same Scan also measures per-topic field coverage (#406) and writes it to a
separate `DRIFT#topic_fields` row. `author_position` went missing on 69% of
`TOPIC#` rows for four months (#405) with nothing noticing, because SPS stores a
missing attribute as `""` without complaint. Floors and exemptions live in
`config/thresholds.json` (`topic_field_*`; rationale in `config/thresholds.md`):

  author_position     a topic below the floor is ERROR — the builder writes it
                      on every row, so a gap means a writer regressed.
  primary_subtopic_id a topic below the floor is WARN. ~9% of rows carry no
                      subtopic in steady state (no subtopic clears the assign
                      confidence floor), so the floor catches a collapse, not the
                      normal rate. Per-topic counts land on the row so slow
                      erosion is visible there.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DRIFT_PK = "DRIFT#taxonomy"
FIELDS_PK = "DRIFT#topic_fields"
COVERAGE_FIELDS = ("author_position", "primary_subtopic_id")

# `DRIFT#` rows use OK|WARN|ERROR (docs/data-model-and-queries.md); alerting.py
# accepts INFO|WARN|ERROR and raises ValueError on anything else. "OK" is a valid
# row severity and an invalid alert severity — only WARN/ERROR are dispatched.
ALERTABLE = ("WARN", "ERROR")


def evaluate(
    partition_ids: Iterable[str],
    taxonomy_ids: Iterable[str],
    catalog_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compare the topic ids present in data against the taxonomy. Pure.

    `catalog_ids` is the `TAXONOMY#{version}/META` topic set. Pass None only
    when the record could not be read — that is itself reported, never treated
    as agreement.
    """
    present = set(partition_ids)
    expected = set(taxonomy_ids)

    orphans = sorted(present - expected)
    unscored = sorted(expected - present)

    catalog_stale: list[str] = []
    catalog_missing: list[str] = []
    catalog_read = catalog_ids is not None
    if catalog_read:
        catalog = set(catalog_ids)  # type: ignore[arg-type]
        catalog_stale = sorted(catalog - expected)
        catalog_missing = sorted(expected - catalog)

    if orphans or catalog_stale or catalog_missing or not catalog_read:
        severity = "ERROR"
    elif unscored:
        severity = "WARN"
    else:
        severity = "OK"

    return {
        "severity": severity,
        "orphan_topics": orphans,
        "unscored_topics": unscored,
        "catalog_stale_topics": catalog_stale,
        "catalog_missing_topics": catalog_missing,
        "catalog_read": catalog_read,
        "partition_count": len(present),
        "taxonomy_topic_count": len(expected),
    }


def evaluate_field_coverage(
    counts: dict[str, dict[str, int]],
    *,
    author_position_min: float,
    subtopic_min: float,
    subtopic_exempt: Iterable[str] = (),
) -> dict[str, Any]:
    """Per-topic coverage against the floors. Pure.

    `counts` is `{topic: {"rows": n, "author_position": k, "primary_subtopic_id": j}}`
    as filled by `scan_topic_partitions(field_counts=...)`.
    """
    exempt = set(subtopic_exempt)
    violations: list[dict] = []
    for topic in sorted(counts):
        c = counts[topic]
        rows = c.get("rows", 0)
        if not rows:
            continue
        checks = [("author_position", author_position_min, "ERROR")]
        if topic not in exempt:
            checks.append(("primary_subtopic_id", subtopic_min, "WARN"))
        for field, floor, level in checks:
            covered = c.get(field, 0)
            if covered < floor * rows:
                violations.append(
                    {"topic": topic, "field": field, "covered": covered, "rows": rows, "level": level}
                )
    levels = {v["level"] for v in violations}
    severity = "ERROR" if "ERROR" in levels else "WARN" if levels else "OK"
    return {
        "severity": severity,
        "violations": violations,
        "coverage": {t: dict(c) for t, c in sorted(counts.items())},
        "rows": sum(c.get("rows", 0) for c in counts.values()),
    }


def read_catalog_topic_ids(table: Any, taxonomy_version: str) -> set[str] | None:
    """Return the topic ids listed in `TAXONOMY#{version}/META`, or None.

    This record is a SEPARATE replica of the taxonomy from the `TOPIC#`
    partitions, and it is the one downstream consumers enumerate topics from —
    SPS builds its `topic` catalog off it nightly.

    Omitting it is why the first version of this checker reported a clean
    `OK — 69 partitions vs 69 taxonomy ids, 0 orphans` on 2026-08-03 while this
    record said 70 and SPS was serving a research area retired two days
    earlier (#355). Partition drift and catalog drift are independent failures;
    checking one says nothing about the other.

    Returns None when the record cannot be read. The caller reports that as a
    failure, never as agreement — an unreadable catalog is not a clean one.
    """
    from boto3.dynamodb.conditions import Key  # noqa: F401  (parity with house style)

    try:
        resp = table.get_item(
            Key={"PK": f"TAXONOMY#{taxonomy_version}", "SK": "META"},
            ProjectionExpression="topics",
        )
    except Exception:
        logger.exception("could not read TAXONOMY#%s/META", taxonomy_version)
        return None

    item = resp.get("Item")
    if not item:
        logger.error("TAXONOMY#%s/META does not exist", taxonomy_version)
        return None

    ids = {t["id"] for t in item.get("topics", []) if isinstance(t, dict) and t.get("id")}
    return ids or None


def scan_topic_partitions(
    table: Any, field_counts: dict[str, dict[str, int]] | None = None
) -> dict[str, str]:
    """Return {topic_id: newest created_at seen} for every `TOPIC#` partition.

    A full Scan is unavoidable: the check must *discover* unexpected partitions,
    and no GSI hashes on the `TOPIC#` PK space. ~136 MB / 133 pages as of
    2026-08-03, which is acceptable on a daily cadence.

    `created_at` is absent on rows predating the field. It is reported as
    evidence of *when* an orphan was minted, never used to decide whether one
    exists — absence must not read as a violation.

    When `field_counts` is passed it is filled in the same pass with
    `{topic: {"rows", "author_position", "primary_subtopic_id"}}` — counts of
    rows carrying a non-empty value — so #406's coverage check costs no second
    Scan.
    """
    newest: dict[str, str] = {}
    kwargs: dict[str, Any] = {
        "FilterExpression": "begins_with(#pk, :p)",
        "ExpressionAttributeNames": {"#pk": "PK"},
        "ExpressionAttributeValues": {":p": "TOPIC#"},
        "ProjectionExpression": "#pk, created_at",
    }
    if field_counts is not None:
        kwargs["ProjectionExpression"] += ", " + ", ".join(COVERAGE_FIELDS)
    last_key = None
    while True:
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            topic = item["PK"].split("#", 1)[1]
            if not topic:
                # A bare "TOPIC#" row. utils/topic_records.py builds the PK from
                # dense-scoring output keys with no non-empty guard, so this is
                # reachable. It must not become a dict key below: DynamoDB
                # rejects an empty map key with ValidationException, which would
                # abort the whole check.
                logger.warning("skipping TOPIC# row with an empty topic id: %s", item.get("SK"))
                continue
            created = item.get("created_at")
            if created and created > newest.get(topic, ""):
                newest[topic] = created
            newest.setdefault(topic, "")
            if field_counts is not None:
                c = field_counts.setdefault(topic, {"rows": 0, **{f: 0 for f in COVERAGE_FIELDS}})
                c["rows"] += 1
                for f in COVERAGE_FIELDS:
                    c[f] += bool(item.get(f))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return newest


def _persist_row(table: Any, day: str, result: dict, pk: str = DRIFT_PK) -> bool:
    """Write the `DRIFT#` row for `day`. Returns False on failure.

    Callers must not let a write failure abort the check — see run_check.
    """
    try:
        table.put_item(
            Item={
                "PK": pk,
                "SK": f"DAY#{day}",
                "checked_at": day,
                **result,
            }
        )
    except Exception:
        logger.exception("failed to persist %s DAY#%s", pk, day)
        return False
    return True


def run_check(
    table: Any,
    taxonomy_ids: Iterable[str],
    *,
    taxonomy_hash: str,
    day: str,
    taxonomy_version: str = "taxonomy_v2",
    thresholds: dict | None = None,
) -> dict:
    """Scan, evaluate, persist a `DRIFT#taxonomy` row, alert if actionable.

    The field-coverage result (#406) rides back as `result["topic_fields"]`;
    it is persisted to its own `DRIFT#topic_fields` row, never onto this one.
    """
    t_start = time.monotonic()
    field_counts: dict[str, dict[str, int]] = {}
    newest_by_topic = scan_topic_partitions(table, field_counts)
    catalog_ids = read_catalog_topic_ids(table, taxonomy_version)
    result = evaluate(newest_by_topic.keys(), taxonomy_ids, catalog_ids)
    result["taxonomy_hash"] = taxonomy_hash
    # Minting dates for orphans only — this is what made the 07-10 incident legible.
    result["orphan_last_written"] = {t: newest_by_topic[t] for t in result["orphan_topics"]}
    # How long the WORK took: the full-table Scan (~136 MB / 133 pages) plus the
    # catalog read plus the comparison. Stopped here, before the put_item below, so
    # the number is not inflated by the write that carries it, and unchanged by the
    # alert-status re-put further down — the row reports the check, not the paperwork.
    # `_persist_row` spreads `**result`, so it lands on the row from here. Every
    # STAGE# row has carried a duration since Phase 9 and the DRIFT# rows did not,
    # which left SPS's producer board with an empty run-duration column for them.
    # Rows written before this field existed have none; read it as optional.
    result["duration_ms"] = max(0, int((time.monotonic() - t_start) * 1000))

    # Persist first for idempotency, but never let a write failure swallow the
    # alert: a check that has just found drift going silent because it could not
    # record the finding is the worst available outcome, and exactly the
    # "reported nothing" shape this ADR exists to prevent.
    result["row_persisted"] = _persist_row(table, day, result)

    if result["severity"] in ALERTABLE:
        from pipeline_enrichment import alerting

        orphans = result["orphan_topics"]
        unscored = result["unscored_topics"]
        stale = result["catalog_stale_topics"]
        missing = result["catalog_missing_topics"]

        if not result["catalog_read"]:
            title = "Taxonomy drift: the topic catalog could not be read"
            message = (
                f"TAXONOMY#{taxonomy_version}/META is missing, empty, or unreadable. "
                f"Downstream consumers build their topic list from it — SPS rebuilds "
                f"its catalog off it nightly — so this is reported as a failure rather "
                f"than assumed clean."
            )
        elif stale or missing:
            title = "Taxonomy drift: the topic catalog disagrees with the taxonomy"
            message = (
                f"TAXONOMY#{taxonomy_version}/META lists {len(stale)} topic(s) that are "
                f"not in the taxonomy ({', '.join(stale) or 'none'}) and is missing "
                f"{len(missing)} that are ({', '.join(missing) or 'none'}). Downstream "
                f"consumers enumerate topics from this record, so they are serving the "
                f"wrong topic set right now — this reaches users without touching any "
                f"TOPIC# row. Refresh it: cli/retire_topic.py does so on retirement, "
                f"cli/score_new_topics.py on addition."
            )
        elif orphans:
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

        result["alert_sent"] = alerting.alert(
            result["severity"],
            title,
            message,
            {
                "source": "pipeline_taxonomy_drift.checker",
                "orphan_topics": orphans,
                "unscored_topics": unscored,
                "catalog_stale_topics": stale,
                "catalog_missing_topics": missing,
                "catalog_read": result["catalog_read"],
                "orphan_last_written": result["orphan_last_written"],
                "taxonomy_hash": taxonomy_hash,
                "partition_count": result["partition_count"],
                "taxonomy_topic_count": result["taxonomy_topic_count"],
                "row_persisted": result["row_persisted"],
            },
            mention=bool(orphans or stale or missing or not result["catalog_read"]),
        )

        # Re-write so the row records whether anyone was actually notified.
        # alert() returns False for BOTH "webhook unset" and "POST failed", and
        # logs the former at INFO — so without this the row cannot distinguish a
        # delivered alert from a silent drop, and the invocation still looks
        # green either way. Same reasoning as the persist-before-dispatch
        # ordering above, applied to the half it did not cover.
        # Re-put rather than update_item: the Lambda role grants Scan/PutItem
        # but NOT UpdateItem (infra/lambda_iam_policy.json).
        # `alert_sent` is absent, not False, when severity was not alertable.
        if result["row_persisted"]:
            _persist_row(table, day, result)

    # Guarded: the taxonomy row and card are already out. An exception here would
    # fail the invocation, and EventBridge's async retries would re-Scan and
    # re-send that card while never writing the coverage row.
    try:
        result["topic_fields"] = _check_field_coverage(table, day, field_counts, thresholds)
    except Exception as exc:
        logger.exception("topic field coverage check failed")
        from pipeline_enrichment import alerting

        result["topic_fields"] = {"severity": "ERROR", "error": repr(exc)}
        result["topic_fields"]["alert_sent"] = alerting.alert(
            "ERROR",
            "TOPIC# field coverage check failed",
            f"The daily coverage check raised {exc!r}; coverage was not measured today. "
            f"The taxonomy drift check above it completed.",
            {"source": "pipeline_taxonomy_drift.checker", "day": day},
        )
    return result


def _check_field_coverage(table: Any, day: str, field_counts: dict, thresholds: dict | None) -> dict:
    """#406 — evaluate, persist `DRIFT#topic_fields`, alert. Same persist-then-
    alert-then-record ordering as the taxonomy check above."""
    if thresholds is None:
        from utils.env_check import load_thresholds

        thresholds = load_thresholds()
    fields = evaluate_field_coverage(
        field_counts,
        author_position_min=float(thresholds["topic_field_author_position_min_coverage"]),
        subtopic_min=float(thresholds["topic_field_subtopic_min_coverage"]),
        subtopic_exempt=thresholds.get("topic_field_subtopic_exempt_topics", []),
    )
    fields["row_persisted"] = _persist_row(table, day, fields, pk=FIELDS_PK)
    if fields["severity"] not in ALERTABLE:
        return fields

    from pipeline_enrichment import alerting

    by_field: dict[str, list[str]] = {}
    for v in fields["violations"]:
        by_field.setdefault(v["field"], []).append(
            f"{v['topic']} ({v['covered']}/{v['rows']})"
        )
    missing_ap = by_field.get("author_position", [])
    missing_sub = by_field.get("primary_subtopic_id", [])
    if missing_ap:
        title = "TOPIC# rows missing author_position"
        message = (
            f"{len(missing_ap)} topic(s) are below the author_position floor: "
            f"{', '.join(missing_ap)}. SPS ranks 'Scholars in this area' on first/last "
            f"authorship and stores a missing value as blank, so those papers stop "
            f"counting. The row builder writes it on every row, so a writer has "
            f"regressed. Repair with `python -m cli.backfill_topic_author_position --all`."
        )
    else:
        title = "TOPIC# rows missing subtopic assignments"
        message = (
            f"{len(missing_sub)} topic(s) are below the primary_subtopic_id floor: "
            f"{', '.join(missing_sub)}. Those papers appear under no subarea on SPS. "
            f"Likely rows re-minted without a re-assign, or a failed Assign stage."
        )
    fields["alert_sent"] = alerting.alert(
        fields["severity"],
        title,
        message,
        {
            "source": "pipeline_taxonomy_drift.checker",
            "author_position": missing_ap,
            "primary_subtopic_id": missing_sub,
            "rows": fields["rows"],
            "row_persisted": fields["row_persisted"],
        },
        mention=bool(missing_ap),
    )
    if fields["row_persisted"]:
        _persist_row(table, day, fields, pk=FIELDS_PK)
    return fields


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
    logger.info(
        "taxonomy drift check: %s; topic field coverage: %s",
        result["severity"], result["topic_fields"]["severity"],
    )
    return result
