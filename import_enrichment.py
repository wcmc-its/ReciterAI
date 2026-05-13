#!/usr/bin/env python3
"""
One-off import: enrich DynamoDB TOPIC# records with article metadata from ReciterDB.

Adds 7 attributes to each TOPIC# record:
  - synopsis          (from reciterai_synopsis)
  - impact_score      (from reciterai_impact, 0-100 scale)
  - impact_justification (from reciterai_impact)
  - title             (from analysis_summary_article)
  - journal           (from analysis_summary_article)
  - year              (from analysis_summary_article)
  - author_position   (from analysis_summary_author, matched by pmid + personIdentifier)

Going forward, new scores will be computed directly in DynamoDB.

Usage:
    python3 import_enrichment.py [--dry-run]
"""

import argparse
import os
import sys
from decimal import Decimal

import boto3
from sqlalchemy import create_engine, text


TABLE_NAME = "reciterai"
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def get_db_engine():
    return create_engine(
        f"mysql+pymysql://{os.environ['DB_USERNAME']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}/{os.environ['DB_NAME']}"
    )


def load_impact_scores(engine) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT external_id, impactScore, justification "
            "FROM reciterai_impact "
            "WHERE entity_type = 'publication' AND external_id IS NOT NULL"
        )).fetchall()
    result = {}
    for r in rows:
        pmid_str = str(r[0])
        result[pmid_str] = {
            "impact_score": float(r[1]) if r[1] is not None else 0.0,
            "justification": str(r[2] or ""),
        }
    print(f"Loaded {len(result)} impact scores from reciterai_impact")
    return result


def load_synopses(engine) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT external_id, synopsis "
            "FROM reciterai_synopsis "
            "WHERE synopsis IS NOT NULL AND synopsis != ''"
        )).fetchall()
    result = {}
    for r in rows:
        result[str(r[0])] = str(r[1])
    print(f"Loaded {len(result)} synopses from reciterai_synopsis")
    return result


def load_article_metadata(engine) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT pmid, articleTitle, journalTitleVerbose, articleYear "
            "FROM analysis_summary_article"
        )).fetchall()
    result = {}
    for r in rows:
        result[str(r[0])] = {
            "title": str(r[1] or ""),
            "journal": str(r[2] or ""),
            "year": int(r[3]) if r[3] is not None else 0,
        }
    print(f"Loaded {len(result)} articles from analysis_summary_article")
    return result


def load_author_positions(engine) -> dict:
    """Returns {(pmid_str, personIdentifier): authorPosition}"""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT pmid, personIdentifier, authorPosition "
            "FROM analysis_summary_author"
        )).fetchall()
    result = {}
    for r in rows:
        key = (str(r[0]), str(r[1]))
        result[key] = str(r[2] or "")
    print(f"Loaded {len(result)} author-position records from analysis_summary_author")
    return result


def scan_topic_records(dynamo_client) -> list:
    records = []
    params = {
        "TableName": TABLE_NAME,
        "FilterExpression": "begins_with(PK, :prefix)",
        "ExpressionAttributeValues": {":prefix": {"S": "TOPIC#"}},
    }
    page = 0
    while True:
        resp = dynamo_client.scan(**params)
        items = resp.get("Items", [])
        records.extend(items)
        page += 1
        if page % 5 == 0:
            print(f"  Scanned {len(records)} TOPIC# records so far...")
        if "LastEvaluatedKey" not in resp:
            break
        params["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    print(f"Total TOPIC# records scanned: {len(records)}")
    return records


def extract_person_identifier(item: dict) -> str:
    faculty_uid = item.get("faculty_uid", {}).get("S", "")
    if faculty_uid.startswith("cwid_"):
        return faculty_uid[len("cwid_"):]
    return faculty_uid


def extract_pmid_from_item(item: dict) -> str:
    return item.get("pmid", {}).get("S", "")


def enrich_and_write(dynamo_client, records, impact_data, synopsis_data,
                     article_data, author_pos_data, dry_run=False):
    enriched = 0
    skipped = 0
    batch = []

    for item in records:
        pmid = extract_pmid_from_item(item)
        if not pmid:
            skipped += 1
            continue

        person_id = extract_person_identifier(item)
        impact = impact_data.get(pmid, {})
        synopsis = synopsis_data.get(pmid, "")
        article = article_data.get(pmid, {})
        author_pos = author_pos_data.get((pmid, person_id), "")

        enriched_item = dict(item)
        enriched_item["synopsis"] = {"S": synopsis}
        enriched_item["impact_score"] = {"N": str(Decimal(str(round(impact.get("impact_score", 0), 2))))}
        enriched_item["impact_justification"] = {"S": impact.get("justification", "")}
        enriched_item["title"] = {"S": article.get("title", "")}
        enriched_item["journal"] = {"S": article.get("journal", "")}
        enriched_item["year"] = {"N": str(article.get("year", 0))}
        enriched_item["author_position"] = {"S": author_pos}

        batch.append({"PutRequest": {"Item": enriched_item}})
        enriched += 1

        if len(batch) >= 25:
            if not dry_run:
                _flush_batch(dynamo_client, batch)
            batch = []
            if enriched % 5000 == 0:
                print(f"  Written {enriched} enriched records...")

    if batch and not dry_run:
        _flush_batch(dynamo_client, batch)

    print(f"Enriched: {enriched}, Skipped (no pmid): {skipped}")
    if dry_run:
        print("DRY RUN — no writes made")


def _flush_batch(dynamo_client, batch):
    request = {TABLE_NAME: batch}
    resp = dynamo_client.batch_write_item(RequestItems=request)
    unprocessed = resp.get("UnprocessedItems", {}).get(TABLE_NAME, [])
    retries = 0
    while unprocessed and retries < 5:
        import time
        time.sleep(2 ** retries * 0.5)
        resp = dynamo_client.batch_write_item(RequestItems={TABLE_NAME: unprocessed})
        unprocessed = resp.get("UnprocessedItems", {}).get(TABLE_NAME, [])
        retries += 1
    if unprocessed:
        print(f"WARNING: {len(unprocessed)} items still unprocessed after retries")


def main():
    parser = argparse.ArgumentParser(description="Enrich DynamoDB TOPIC# records with ReciterDB metadata")
    parser.add_argument("--dry-run", action="store_true", help="Scan and match but don't write")
    args = parser.parse_args()

    print("=== ReciterAI DynamoDB Enrichment Import ===\n")

    engine = get_db_engine()
    impact_data = load_impact_scores(engine)
    synopsis_data = load_synopses(engine)
    article_data = load_article_metadata(engine)
    author_pos_data = load_author_positions(engine)

    dynamo_client = boto3.client("dynamodb", region_name=REGION)

    print(f"\nScanning DynamoDB table '{TABLE_NAME}'...")
    records = scan_topic_records(dynamo_client)

    print(f"\nEnriching records...")
    enrich_and_write(dynamo_client, records, impact_data, synopsis_data,
                     article_data, author_pos_data, dry_run=args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
