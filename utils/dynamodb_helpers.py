"""
DynamoDB helpers for ReCiter AI Chatbot pipeline.

Provides:
- Table creation with both GSIs (FacultyTopicsIndex, ProcessingByVersionIndex)
- Batch write helper (chunks items into groups of 25)
- Score sort key builder (zero-padded for lexicographic descending sort)
- Processing tracker read/write for checkpoint/resume (D-09)

Security (T-01-04, T-01-05):
- Table creation requires dynamodb:CreateTable IAM permission.
  Appropriate for offline pipeline under developer credentials.
- DynamoDB writes use AWS server-side encryption at rest.
- No PII beyond faculty names (public directory information).
"""

import os
import logging
from decimal import Decimal

import boto3

from utils.iso_clock import now_iso

logger = logging.getLogger(__name__)

# DynamoDB table name (DB-01)
TABLE_NAME = "reciterai"


def get_dynamo_client(region: str = None):
    """
    Get a boto3 DynamoDB client.

    Args:
        region: AWS region. If None, reads AWS_DEFAULT_REGION env var,
                defaults to 'us-east-1'.

    Returns:
        boto3 DynamoDB client.
    """
    resolved_region = region or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
    return boto3.client('dynamodb', region_name=resolved_region)


def get_table(table_name: str = TABLE_NAME, region: str = None):
    """
    Get a boto3 DynamoDB Table resource (DocumentClient-style).

    Unlike the low-level client, the Table resource accepts plain Python dicts
    for Item / Key / ExpressionAttributeValues and handles type-descriptor
    serialization (subject to the Decimal-only numeric rule). Preferred for
    single-item UpdateItem / PutItem operations.

    Args:
        table_name: DynamoDB table name (default TABLE_NAME).
        region: AWS region. If None, reads AWS_DEFAULT_REGION env var,
                defaults to 'us-east-1'.

    Returns:
        boto3 DynamoDB Table resource bound to `table_name`.
    """
    resolved_region = region or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
    resource = boto3.resource('dynamodb', region_name=resolved_region)
    return resource.Table(table_name)


def create_chatbot_table(client, table_name: str = TABLE_NAME):
    """
    Create the reciterai DynamoDB table with both GSIs if it doesn't exist.

    Table design:
    - Primary key: PK (HASH, String) + SK (RANGE, String)
    - GSI 1 "FacultyIndex": faculty_uid (HASH) + PK (RANGE), Projection: ALL
      Access: "What does Dr. X work on?" / "What tools does Dr. X use?"
    - GSI 2 "ProcessingByVersionIndex": taxonomy_version (HASH) + status (RANGE), Projection: KEYS_ONLY
      Access: "Which pubs were scored with taxonomy_v2?"
    - GSI 3 "PmidIndex": pmid (HASH) + PK (RANGE), Projection: ALL
      Access: "Get all data for PMID X" (topics, tools, impact)
    - BillingMode: PAY_PER_REQUEST (on-demand)

    Args:
        client: boto3 DynamoDB client.
        table_name: DynamoDB table name (default TABLE_NAME).

    Note:
        If table already exists (ResourceInUseException), silently passes.
        Waits for table to become ACTIVE before returning.
    """
    try:
        client.create_table(
            TableName=table_name,
            KeySchema=[
                {'AttributeName': 'PK', 'KeyType': 'HASH'},
                {'AttributeName': 'SK', 'KeyType': 'RANGE'},
            ],
            AttributeDefinitions=[
                {'AttributeName': 'PK', 'AttributeType': 'S'},
                {'AttributeName': 'SK', 'AttributeType': 'S'},
                {'AttributeName': 'faculty_uid', 'AttributeType': 'S'},
                {'AttributeName': 'taxonomy_version', 'AttributeType': 'S'},
                {'AttributeName': 'status', 'AttributeType': 'S'},
                {'AttributeName': 'pmid', 'AttributeType': 'S'},
            ],
            BillingMode='PAY_PER_REQUEST',
            GlobalSecondaryIndexes=[
                {
                    # GSI 1: Faculty lookup
                    # "What does Dr. X work on?" / "What tools does Dr. X use?"
                    # faculty_uid → all TOPIC#, TOOL#, FACULTY# records
                    'IndexName': 'FacultyIndex',
                    'KeySchema': [
                        {'AttributeName': 'faculty_uid', 'KeyType': 'HASH'},
                        {'AttributeName': 'PK', 'KeyType': 'RANGE'},
                    ],
                    'Projection': {'ProjectionType': 'ALL'},
                },
                {
                    # GSI 2: Processing tracker by taxonomy version
                    # "Which publications were scored with taxonomy_v2?"
                    'IndexName': 'ProcessingByVersionIndex',
                    'KeySchema': [
                        {'AttributeName': 'taxonomy_version', 'KeyType': 'HASH'},
                        {'AttributeName': 'status', 'KeyType': 'RANGE'},
                    ],
                    'Projection': {'ProjectionType': 'KEYS_ONLY'},
                },
                {
                    # GSI 3: PMID lookup
                    # "Get all data for PMID X" — topics, tools, impact in one query
                    'IndexName': 'PmidIndex',
                    'KeySchema': [
                        {'AttributeName': 'pmid', 'KeyType': 'HASH'},
                        {'AttributeName': 'PK', 'KeyType': 'RANGE'},
                    ],
                    'Projection': {'ProjectionType': 'ALL'},
                },
            ],
        )
        logger.info(f"Created DynamoDB table: {table_name}")
        print(f"[DynamoDB] Created table '{table_name}'. Waiting for it to become ACTIVE...")
    except client.exceptions.ResourceInUseException:
        logger.info(f"DynamoDB table '{table_name}' already exists.")
        print(f"[DynamoDB] Table '{table_name}' already exists.")

    # Wait until table is ACTIVE before returning
    wait_for_table(client, table_name)
    print(f"[DynamoDB] Table '{table_name}' is ACTIVE.")


def wait_for_table(client, table_name: str = TABLE_NAME):
    """
    Poll until the DynamoDB table status is ACTIVE.

    Uses boto3 built-in waiter with default retry settings.

    Args:
        client: boto3 DynamoDB client.
        table_name: DynamoDB table name.
    """
    waiter = client.get_waiter('table_exists')
    waiter.wait(TableName=table_name)


def batch_write(client, table_name: str, items: list):
    """
    Write items to DynamoDB in chunks of 25 using batch_write_item.

    DynamoDB's batch_write_item API accepts a maximum of 25 items per request.

    Args:
        client: boto3 DynamoDB client.
        table_name: DynamoDB table name.
        items: List of item dicts in DynamoDB attribute format
               (e.g., {"PK": {"S": "..."}, "SK": {"S": "..."}, ...}).

    Note:
        Each item must already be in DynamoDB attribute format with type descriptors.
        Use to_dynamodb_item() helper if starting from plain Python dicts.
    """
    chunk_size = 25
    total = len(items)
    written = 0

    for i in range(0, total, chunk_size):
        chunk = items[i:i + chunk_size]
        request_items = {
            table_name: [
                {'PutRequest': {'Item': item}}
                for item in chunk
            ]
        }
        response = client.batch_write_item(RequestItems=request_items)

        # Handle unprocessed items (retry if any were not written)
        unprocessed = response.get('UnprocessedItems', {})
        retry_count = 0
        while unprocessed and retry_count < 3:
            import time
            time.sleep(min(2 ** retry_count, 10))
            retry_response = client.batch_write_item(RequestItems=unprocessed)
            unprocessed = retry_response.get('UnprocessedItems', {})
            retry_count += 1

        if unprocessed:
            logger.warning(
                f"batch_write: {len(unprocessed.get(table_name, []))} items "
                f"still unprocessed after retries in chunk starting at index {i}"
            )

        written += len(chunk)
        logger.debug(f"batch_write: wrote {written}/{total} items to '{table_name}'")

    logger.info(f"batch_write: completed — {total} items written to '{table_name}'")


def make_score_sk(score: float, pmid: str) -> str:
    """
    Build the DynamoDB sort key for a topic/tool score record.

    Encodes score as 4-digit zero-padded integer for lexicographic descending sort.
    DynamoDB's lexicographic sort means higher score strings sort first.

    Examples:
        make_score_sk(0.95, '12345')  -> 'SCORE#0950#ACTIVITY#pmid_12345'
        make_score_sk(0.40, '67890')  -> 'SCORE#0400#ACTIVITY#pmid_67890'
        make_score_sk(1.0, '99999')   -> 'SCORE#0999#ACTIVITY#pmid_99999'  (clamped)

    Per Research Pattern 2 from RESEARCH.md:
    - Score 0.95 -> int(0.95 * 1000) = 950 -> zero-padded to 4 digits: '0950'
    - Score 1.0 -> int(1.0 * 1000) = 1000, clamped by min(..., 9999) -> 999 -> '0999'
      Wait: min(1000, 9999) = 1000 but we want 4-digit. Actually 1000 is 4 digits already.
      Plan spec says "min(int(score * 1000), 9999)" so 1.0 -> min(1000, 9999) = 1000 -> '1000'
      But to keep it 4-digit safe use zfill(4): '1000' is already 4 chars.

    Args:
        score: Float score in [0.0, 1.0] range.
        pmid: Publication PMID string.

    Returns:
        Sort key string in format 'SCORE#NNNN#ACTIVITY#pmid_{pmid}'.
    """
    padded = str(min(int(score * 1000), 9999)).zfill(4)
    return f"SCORE#{padded}#ACTIVITY#pmid_{pmid}"


def to_decimal(value) -> Decimal:
    """
    Convert a numeric value to Decimal for DynamoDB storage.

    DynamoDB rejects Python float types — all numbers must be Decimal.
    Rounds to 4 decimal places to avoid floating-point precision issues.

    Per Pitfall 1 from RESEARCH.md: float type is rejected by DynamoDB.

    Args:
        value: Numeric value (int, float, or str).

    Returns:
        Decimal with up to 4 decimal places.

    Example:
        to_decimal(0.85) -> Decimal('0.85')
        to_decimal(0.123456789) -> Decimal('0.1235')
    """
    return Decimal(str(round(value, 4)))


def mark_processing(
    client,
    table_name: str,
    pmid: str,
    status: str,
    taxonomy_version: str,
    **kwargs,
):
    """
    Write a PROCESSING# record to the DynamoDB table.

    Used for checkpoint/resume (D-09): records the processing state of each
    publication so the pipeline can skip already-processed items on restart.

    Args:
        client: boto3 DynamoDB client.
        table_name: DynamoDB table name.
        pmid: Publication PMID (numeric string).
        status: Processing status ('pending', 'complete', 'failed', etc.).
        taxonomy_version: Taxonomy version string (e.g., 'taxonomy_v1').
        **kwargs: Additional attributes to store (e.g., screened_at, scored_at,
                  screening_passed_topics, retry_count, error).

    Record format:
        PK: PROCESSING#pmid_{pmid}
        SK: STATUS
        status: {status}
        taxonomy_version: {taxonomy_version}
        + any kwargs as string or numeric attributes
    """
    item = {
        'PK': {'S': f'PROCESSING#pmid_{pmid}'},
        'SK': {'S': 'STATUS'},
        'status': {'S': status},
        'taxonomy_version': {'S': taxonomy_version},
    }

    # Add any extra attributes from kwargs
    for key, value in kwargs.items():
        if isinstance(value, str):
            item[key] = {'S': value}
        elif isinstance(value, bool):
            item[key] = {'BOOL': value}
        elif isinstance(value, (int, float, Decimal)):
            item[key] = {'N': str(value)}
        elif isinstance(value, list):
            # Assume list of strings (e.g., screening_passed_topics)
            item[key] = {'L': [{'S': str(v)} for v in value]}
        elif value is not None:
            item[key] = {'S': str(value)}

    client.put_item(TableName=table_name, Item=item)


def get_processing_status(client, table_name: str, pmids: list) -> dict:
    """
    Batch-get processing status for a list of PMIDs.

    Used for checkpoint/resume (D-09): check which publications have already
    been processed so they can be skipped on restart.

    Args:
        client: boto3 DynamoDB client.
        table_name: DynamoDB table name.
        pmids: List of PMID strings to check.

    Returns:
        Dict mapping pmid -> status_string for pmids that have records.
        PMIDs without records are not included in the result dict.
    """
    if not pmids:
        return {}

    # DynamoDB BatchGetItem supports up to 100 keys per request
    chunk_size = 100
    result = {}

    for i in range(0, len(pmids), chunk_size):
        chunk = pmids[i:i + chunk_size]
        keys = [
            {
                'PK': {'S': f'PROCESSING#pmid_{pmid}'},
                'SK': {'S': 'STATUS'},
            }
            for pmid in chunk
        ]

        response = client.batch_get_item(
            RequestItems={
                table_name: {
                    'Keys': keys,
                    'ProjectionExpression': 'PK, #s',
                    'ExpressionAttributeNames': {'#s': 'status'},
                }
            }
        )

        items = response.get('Responses', {}).get(table_name, [])
        for item in items:
            pk = item.get('PK', {}).get('S', '')
            status_val = item.get('status', {}).get('S', '')
            # Extract pmid from PK: "PROCESSING#pmid_{pmid}"
            if pk.startswith('PROCESSING#pmid_'):
                pmid_key = pk[len('PROCESSING#pmid_'):]
                result[pmid_key] = status_val

    return result


def mark_processing_failed(client, table_name: str, pmid: str, *,
                           error: str, taxonomy_version: str):
    """
    Record a failed scoring attempt for `pmid`, incrementing retry_count.

    Unlike `mark_processing(..., 'failed', ...)` — a full PutItem that
    overwrites the whole item — this is an UpdateItem with
    `ADD retry_count :one`, so the retry counter survives across re-score
    attempts instead of being reset to 0 on every failure. Without this the
    hot-path retry sweep could never quarantine an un-fixable PMID; the
    counter would read 0 forever.

    Also stamps `failed_at` (ISO8601) so the sweep can age-filter failed
    rows. `ADD` treats a missing retry_count as 0, and UpdateItem creates
    the row when the PMID has never been processed.

    Args:
        client: boto3 DynamoDB client.
        table_name: DynamoDB table name.
        pmid: Publication PMID (numeric string).
        error: Error message; truncated to 1000 chars for the DDB attribute.
        taxonomy_version: Taxonomy version string (GSI hash key).
    """
    client.update_item(
        TableName=table_name,
        Key={'PK': {'S': f'PROCESSING#pmid_{pmid}'}, 'SK': {'S': 'STATUS'}},
        UpdateExpression=(
            'SET #s = :failed, #e = :err, failed_at = :ts, '
            'taxonomy_version = :tv ADD retry_count :one'
        ),
        ExpressionAttributeNames={'#s': 'status', '#e': 'error'},
        ExpressionAttributeValues={
            ':failed': {'S': 'failed'},
            ':err': {'S': str(error)[:1000]},
            ':ts': {'S': now_iso()},
            ':tv': {'S': taxonomy_version},
            ':one': {'N': '1'},
        },
    )


def query_pmids_by_status(
    client, table_name: str, taxonomy_version: str, status: str
) -> list:
    """
    Return PMIDs of every PROCESSING# row with the given `status` under
    `taxonomy_version`.

    Uses the `ProcessingByVersionIndex` GSI (taxonomy_version HASH + status
    RANGE). The GSI projection is KEYS_ONLY, so callers that need
    retry_count / failed_at must follow up with `get_processing_rows`.
    Paginates on LastEvaluatedKey. `status` is e.g. 'failed' (retry sweep),
    'complete'/'quarantined' (eligibility sweep, #150 1b).
    """
    pmids: list = []
    kwargs: dict = {
        'TableName': table_name,
        'IndexName': 'ProcessingByVersionIndex',
        'KeyConditionExpression': 'taxonomy_version = :tv AND #s = :st',
        'ExpressionAttributeNames': {'#s': 'status'},
        'ExpressionAttributeValues': {
            ':tv': {'S': taxonomy_version},
            ':st': {'S': status},
        },
    }
    while True:
        resp = client.query(**kwargs)
        for item in resp.get('Items', []):
            pk = item.get('PK', {}).get('S', '')
            if pk.startswith('PROCESSING#pmid_'):
                pmids.append(pk[len('PROCESSING#pmid_'):])
        lek = resp.get('LastEvaluatedKey')
        if not lek:
            break
        kwargs['ExclusiveStartKey'] = lek
    return pmids


def query_failed_pmids(client, table_name: str, taxonomy_version: str) -> list:
    """PMIDs of every PROCESSING# row with status='failed' under
    `taxonomy_version` (the retry sweep's recovery set). Thin wrapper over
    `query_pmids_by_status`."""
    return query_pmids_by_status(client, table_name, taxonomy_version, 'failed')


def scan_impact_pmids_with_synopsis(client, table_name: str) -> list:
    """
    Return the PMIDs of every IMPACT# row that carries a `synopsis` attribute.

    `IMPACT#pmid_{pmid}` rows are the authoritative synopsis source (#38); the
    `synopsis` attribute is present post-#138. Used by the hot-path eligibility
    sweep (#150 1b) to find *enriched* PMIDs (which the scorer should reach).

    A full-table Scan — there is no GSI keyed on the synopsis attribute — with
    a server-side FilterExpression so only IMPACT#-with-synopsis rows return.
    Bounded weekly cost, the same Scan pattern the drift evaluator uses daily.
    Paginates on LastEvaluatedKey.
    """
    pmids: list = []
    kwargs: dict = {
        'TableName': table_name,
        'ProjectionExpression': 'PK',
        'FilterExpression': 'begins_with(PK, :p) AND attribute_exists(synopsis)',
        'ExpressionAttributeValues': {':p': {'S': 'IMPACT#pmid_'}},
    }
    while True:
        resp = client.scan(**kwargs)
        for item in resp.get('Items', []):
            pk = item.get('PK', {}).get('S', '')
            if pk.startswith('IMPACT#pmid_'):
                pmids.append(pk[len('IMPACT#pmid_'):])
        lek = resp.get('LastEvaluatedKey')
        if not lek:
            break
        kwargs['ExclusiveStartKey'] = lek
    return pmids


def scan_invalid_pmids(client, table_name: str) -> list:
    """
    Return the PMIDs of every `INVALID#pmid_{pmid}` row (the invalid-PMID
    exclude list, #150 item 3).

    These are PMIDs ReciterDB flagged as corrupt/disjoint/empty (verdicts in
    `invalid_pmids.txt`, loaded into DDB by `scripts/load_invalid_pmids.py`).
    DDB is the runtime source of truth because it is the only invalid signal
    that survives in the Lambda/Fargate environment — the txt file lives in the
    ReciterDB repo, and the upstream `DELETE FROM reporting_abstracts` does not
    touch the `IMPACT#` rows the eligibility sweep scans. Consumed by the
    hot-path eligibility sweep (#150 1b) and the scorer, which cull these so the
    pipeline never spends a Bedrock call on a known-invalid PMID.

    A full-table Scan (no GSI), server-side FilterExpression, paginated — the
    same pattern as `scan_impact_pmids_with_synopsis`.
    """
    pmids: list = []
    kwargs: dict = {
        'TableName': table_name,
        'ProjectionExpression': 'PK',
        'FilterExpression': 'begins_with(PK, :p)',
        'ExpressionAttributeValues': {':p': {'S': 'INVALID#pmid_'}},
    }
    while True:
        resp = client.scan(**kwargs)
        for item in resp.get('Items', []):
            pk = item.get('PK', {}).get('S', '')
            if pk.startswith('INVALID#pmid_'):
                pmids.append(pk[len('INVALID#pmid_'):])
        lek = resp.get('LastEvaluatedKey')
        if not lek:
            break
        kwargs['ExclusiveStartKey'] = lek
    return pmids


def fetch_scored_provenance(client, table_name: str, pmids: list) -> dict:
    """Return ``{pmid: {"scored_enriched_at", "scored_synopsis_model"}}`` for the
    given PMIDs' ``PROCESSING#`` rows (#150 item 2 drift sweep).

    The synopsis provenance the score was based on — stamped by the scorer
    (``score_one_publication``) and baselined for old scores by
    ``scripts/backfill_score_provenance.py``. The drift sweep compares this
    against the current ``IMPACT#`` provenance to find scores whose synopsis was
    regenerated after scoring. PMIDs absent from DDB are omitted; an un-stamped
    row yields empty-string fields. BatchGetItem in 100-key chunks, re-queuing
    ``UnprocessedKeys``.
    """
    if not pmids:
        return {}
    unique = sorted({str(p) for p in pmids if str(p).strip()})
    if not unique:
        return {}

    chunk_size = 100
    result: dict = {}
    for i in range(0, len(unique), chunk_size):
        chunk = unique[i:i + chunk_size]
        request = {
            table_name: {
                "Keys": [
                    {"PK": {"S": f"PROCESSING#pmid_{p}"}, "SK": {"S": "STATUS"}}
                    for p in chunk
                ],
                "ProjectionExpression": "PK, scored_enriched_at, scored_synopsis_model",
            }
        }
        while request:
            response = client.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(table_name, []):
                pk = item.get("PK", {}).get("S", "")
                if not pk.startswith("PROCESSING#pmid_"):
                    continue
                pmid = pk[len("PROCESSING#pmid_"):]
                result[pmid] = {
                    "scored_enriched_at":
                        item.get("scored_enriched_at", {}).get("S", ""),
                    "scored_synopsis_model":
                        item.get("scored_synopsis_model", {}).get("S", ""),
                }
            request = response.get("UnprocessedKeys") or None
    return result


def invalidate_stale_score(client, table_name: str, pmid: str) -> bool:
    """Demote a drifted PMID's ``PROCESSING#`` status ``complete`` -> ``stale``
    (#150 item 2 drift sweep). Returns True if the row was demoted.

    A drifted score (synopsis regenerated after scoring) is ``complete``, so the
    cache-respecting ``--additive`` re-score path would skip it. ``stale`` is in
    none of the eligibility sweep's exclusions (complete/failed/quarantined) and
    is not skipped by ``get_unscored_publications`` (which only skips
    ``complete``), so the scorer re-runs it. The ``complete``-guard makes this a
    no-op if the row already moved on (concurrent change); retry_count / history
    are preserved.
    """
    from botocore.exceptions import ClientError
    try:
        client.update_item(
            TableName=table_name,
            Key={"PK": {"S": f"PROCESSING#pmid_{pmid}"}, "SK": {"S": "STATUS"}},
            UpdateExpression="SET #s = :stale",
            ConditionExpression="#s = :complete",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":stale": {"S": "stale"},
                ":complete": {"S": "complete"},
            },
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def get_processing_rows(client, table_name: str, pmids: list) -> dict:
    """
    Batch-get full PROCESSING# rows for `pmids`.

    Returns a dict mapping pmid -> {'status', 'retry_count' (int),
    'failed_at' (str|None), 'error' (str)}. PMIDs without a row are omitted.

    Unlike `get_processing_status` (status only), this projects the extra
    attributes the retry sweep needs to age-filter and quarantine. Retries
    BatchGetItem UnprocessedKeys so a throttled chunk is not silently lost.
    """
    if not pmids:
        return {}

    chunk_size = 100
    result: dict = {}

    for i in range(0, len(pmids), chunk_size):
        chunk = pmids[i:i + chunk_size]
        request = {
            table_name: {
                'Keys': [
                    {'PK': {'S': f'PROCESSING#pmid_{pmid}'},
                     'SK': {'S': 'STATUS'}}
                    for pmid in chunk
                ],
                'ProjectionExpression': 'PK, #s, retry_count, failed_at, #e',
                'ExpressionAttributeNames': {'#s': 'status', '#e': 'error'},
            }
        }
        while request:
            response = client.batch_get_item(RequestItems=request)
            for item in response.get('Responses', {}).get(table_name, []):
                pk = item.get('PK', {}).get('S', '')
                if not pk.startswith('PROCESSING#pmid_'):
                    continue
                pmid_key = pk[len('PROCESSING#pmid_'):]
                rc_raw = item.get('retry_count', {}).get('N')
                result[pmid_key] = {
                    'status': item.get('status', {}).get('S', ''),
                    'retry_count': int(rc_raw) if rc_raw is not None else 0,
                    'failed_at': item.get('failed_at', {}).get('S'),
                    'error': item.get('error', {}).get('S', ''),
                }
            request = response.get('UnprocessedKeys') or None

    return result


def quarantine_pmid(client, table_name: str, pmid: str, *,
                    retry_count: int, last_error: str, taxonomy_version: str):
    """
    Quarantine a PMID that has exhausted its retry budget.

    Two writes:
      1. PutItem a `QUARANTINE#pmid_{pmid}` / `STATUS` row — the
         operator-facing record (retry_count, last_error, quarantined_at,
         taxonomy_version) surfaced for manual review.
      2. UpdateItem the PROCESSING# row's status to 'quarantined' so it
         drops out of the `ProcessingByVersionIndex` status='failed'
         partition. This is the mechanism that excludes the PMID from every
         future sweep — no separate exclusion list is needed.
    """
    ts = now_iso()
    client.put_item(
        TableName=table_name,
        Item={
            'PK': {'S': f'QUARANTINE#pmid_{pmid}'},
            'SK': {'S': 'STATUS'},
            'pmid': {'S': str(pmid)},
            'status': {'S': 'quarantined'},
            'retry_count': {'N': str(retry_count)},
            'last_error': {'S': str(last_error)[:1000]},
            'taxonomy_version': {'S': taxonomy_version},
            'quarantined_at': {'S': ts},
        },
    )
    client.update_item(
        TableName=table_name,
        Key={'PK': {'S': f'PROCESSING#pmid_{pmid}'}, 'SK': {'S': 'STATUS'}},
        UpdateExpression='SET #s = :q, quarantined_at = :ts',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={
            ':q': {'S': 'quarantined'},
            ':ts': {'S': ts},
        },
    )


def release_quarantine(client, table_name: str, pmid: str) -> dict:
    """Release a quarantined PMID back into normal processing.

    The inverse of `quarantine_pmid`. A PMID counts as quarantined when a
    `QUARANTINE#pmid_{pmid}` row exists *or* its `PROCESSING#` row carries
    status='quarantined'; the second clause recovers the messy case where
    an operator hand-deleted the QUARANTINE# row but left the PROCESSING#
    row stuck at 'quarantined'. For a quarantined PMID this:

      1. deletes the `QUARANTINE#pmid_{pmid}` operator-review row, and
      2. deletes the `PROCESSING#pmid_{pmid}` checkpoint row.

    Deleting the PROCESSING# row (rather than flipping its status) clears
    retry_count, failed_at, error and quarantined_at in a single write — so
    the next hot-path run finds no checkpoint, re-extracts the PMID, and
    scores it fresh. A surviving non-zero retry_count would otherwise let
    the next failure re-quarantine the PMID immediately, with no fresh
    retry budget.

    Returns a report dict so the caller can tell the operator what
    happened: `pmid`, `was_quarantined` (bool), `retry_count` (int — the
    count being cleared) and `last_error` (str). When `was_quarantined` is
    False the PMID had no quarantine state — a typo, or an already-released
    PMID — and nothing is deleted. Idempotent: DeleteItem on an absent key
    is a no-op, so a repeated release simply reports was_quarantined=False.
    """
    quarantine_key = {
        'PK': {'S': f'QUARANTINE#pmid_{pmid}'}, 'SK': {'S': 'STATUS'},
    }
    processing_key = {
        'PK': {'S': f'PROCESSING#pmid_{pmid}'}, 'SK': {'S': 'STATUS'},
    }

    quarantine_row = client.get_item(
        TableName=table_name, Key=quarantine_key
    ).get('Item')
    processing_row = client.get_item(
        TableName=table_name, Key=processing_key
    ).get('Item')

    processing_status = (processing_row or {}).get('status', {}).get('S', '')
    was_quarantined = (
        quarantine_row is not None or processing_status == 'quarantined'
    )

    # The QUARANTINE# row is the authoritative operator record; fall back to
    # the PROCESSING# row only when it is missing (the hand-deleted case).
    source = quarantine_row or processing_row or {}
    retry_count = int(source.get('retry_count', {}).get('N', '0'))
    last_error = (
        source.get('last_error', {}).get('S')
        or source.get('error', {}).get('S', '')
    )

    if was_quarantined:
        client.delete_item(TableName=table_name, Key=quarantine_key)
        client.delete_item(TableName=table_name, Key=processing_key)

    return {
        'pmid': str(pmid),
        'was_quarantined': was_quarantined,
        'retry_count': retry_count,
        'last_error': last_error,
    }


# ---------------------------------------------------------------------------
# Synopsis lookup against IMPACT# rows (#38 read-switch)
# ---------------------------------------------------------------------------

IMPACT_PK_PREFIX = "IMPACT#pmid_"
IMPACT_SK = "SCORE"


def fetch_synopses_for_pmids(client, pmids: list[str]) -> dict[str, str]:
    """Look up the ``synopsis`` attribute on ``IMPACT#pmid_{pmid}`` rows.

    The DDB-side equivalent of the old ``JOIN reciterai_synopsis`` clause
    in ``PUBLICATION_EXTRACTION_SQL``. The legacy join INNER-joined on a
    non-empty synopsis, so this function reproduces that filter: PMIDs
    whose IMPACT# row is missing OR has an empty ``synopsis`` are absent
    from the result dict.

    Uses ``BatchGetItem`` in 100-key chunks (DDB API max) with a projection
    on ``PK + synopsis``, and re-queues ``UnprocessedKeys``.

    Args:
        client: boto3 DynamoDB client.
        pmids: PMID strings. Duplicates are de-duped; empty list returns {}.

    Returns:
        ``{pmid: synopsis}`` for every PMID with a non-empty synopsis.
    """
    if not pmids:
        return {}

    unique = sorted({str(p) for p in pmids if str(p).strip()})
    if not unique:
        return {}

    chunk_size = 100
    result: dict[str, str] = {}

    for i in range(0, len(unique), chunk_size):
        chunk = unique[i:i + chunk_size]
        request = {
            TABLE_NAME: {
                "Keys": [
                    {"PK": {"S": f"{IMPACT_PK_PREFIX}{p}"},
                     "SK": {"S": IMPACT_SK}}
                    for p in chunk
                ],
                "ProjectionExpression": "PK, synopsis",
            }
        }
        while request:
            response = client.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(TABLE_NAME, []):
                pk = item.get("PK", {}).get("S", "")
                if not pk.startswith(IMPACT_PK_PREFIX):
                    continue
                synopsis = item.get("synopsis", {}).get("S", "")
                if not synopsis:
                    continue
                pmid = pk[len(IMPACT_PK_PREFIX):]
                result[pmid] = synopsis
            request = response.get("UnprocessedKeys") or None

    return result


def fetch_synopsis_records(client, pmids: list[str]) -> dict:
    """Like ``fetch_synopses_for_pmids`` but also returns synopsis provenance
    (``synopsis_model``, ``enriched_at``) for #150 item 2.

    The scorer stamps these onto the ``PROCESSING#`` row at score time
    (``scored_synopsis_model`` / ``scored_enriched_at``) so a later drift sweep
    can tell whether a publication's synopsis was regenerated after it was
    scored — ``IMPACT#.enriched_at`` advancing past the stamped
    ``scored_enriched_at``, or a ``synopsis_model`` change.

    Returns ``{pmid: {"synopsis", "synopsis_model", "enriched_at"}}`` for every
    PMID whose ``IMPACT#`` row carries a non-empty synopsis (same inner-join
    filter as ``fetch_synopses_for_pmids``). BatchGetItem in 100-key chunks,
    re-queuing ``UnprocessedKeys``.
    """
    if not pmids:
        return {}
    unique = sorted({str(p) for p in pmids if str(p).strip()})
    if not unique:
        return {}

    chunk_size = 100
    result: dict = {}
    for i in range(0, len(unique), chunk_size):
        chunk = unique[i:i + chunk_size]
        request = {
            TABLE_NAME: {
                "Keys": [
                    {"PK": {"S": f"{IMPACT_PK_PREFIX}{p}"}, "SK": {"S": IMPACT_SK}}
                    for p in chunk
                ],
                "ProjectionExpression": "PK, synopsis, synopsis_model, enriched_at",
            }
        }
        while request:
            response = client.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(TABLE_NAME, []):
                pk = item.get("PK", {}).get("S", "")
                if not pk.startswith(IMPACT_PK_PREFIX):
                    continue
                synopsis = item.get("synopsis", {}).get("S", "")
                if not synopsis:
                    continue
                pmid = pk[len(IMPACT_PK_PREFIX):]
                result[pmid] = {
                    "synopsis": synopsis,
                    "synopsis_model": item.get("synopsis_model", {}).get("S", ""),
                    "enriched_at": item.get("enriched_at", {}).get("S", ""),
                }
            request = response.get("UnprocessedKeys") or None
    return result


def scan_all_synopses(client) -> dict[str, str]:
    """Return ``{pmid: synopsis}`` for every ``IMPACT#`` row with a synopsis.

    The full-table equivalent of the old ``SYNOPSIS_EXTRACTION_SQL``: used
    by ``generate_taxonomy.py`` to build the input for taxonomy generation
    (offline one-shot — not in any hot or daily path). DDB ``Scan`` with
    a filter expression; paginates on ``LastEvaluatedKey``.

    Args:
        client: boto3 DynamoDB client.

    Returns:
        ``{pmid: synopsis}`` covering every IMPACT# row with a non-empty
        synopsis attribute (post-#138 lift: ~9K PMIDs).
    """
    result: dict[str, str] = {}
    kwargs = {
        "TableName": TABLE_NAME,
        "FilterExpression": (
            "begins_with(PK, :p) AND attribute_exists(synopsis)"
        ),
        "ExpressionAttributeValues": {":p": {"S": "IMPACT#"}},
        "ProjectionExpression": "PK, synopsis",
    }
    while True:
        response = client.scan(**kwargs)
        for item in response.get("Items", []):
            pk = item.get("PK", {}).get("S", "")
            if not pk.startswith(IMPACT_PK_PREFIX):
                continue
            synopsis = item.get("synopsis", {}).get("S", "")
            if not synopsis:
                continue
            pmid = pk[len(IMPACT_PK_PREFIX):]
            result[pmid] = synopsis
        lek = response.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return result


def check_enrichment_coverage(client, pmids: list[str]) -> dict[str, list[str]]:
    """Partition ``pmids`` by enrichment completeness against IMPACT# rows (#141).

    The DDB-side replacement for the MariaDB ``reciterai_synopsis`` /
    ``reciterai_impact`` coverage check. A PMID is ``complete`` iff its
    ``IMPACT#pmid_{pmid}`` row exists AND carries BOTH a non-empty
    ``synopsis`` attribute AND an ``impact_score`` attribute;
    ``incomplete`` is missing either. ``run_enrichment_backfill`` uses
    ``incomplete`` as its idempotency cull — only those PMIDs are sent to
    the LLM (unless --force).

    Uses ``BatchGetItem`` in 100-key chunks (DDB API max) with a projection
    on ``PK + synopsis + impact_score``, and re-queues ``UnprocessedKeys``.

    Args:
        client: boto3 DynamoDB client.
        pmids: PMID strings. Duplicates are de-duped; empty list returns
            empty lists without a DDB call.

    Returns:
        ``{"complete": [...], "incomplete": [...]}`` — both lists sorted,
        stringified, de-duplicated.
    """
    wanted = sorted({str(p) for p in pmids if str(p).strip()})
    if not wanted:
        return {"complete": [], "incomplete": []}

    chunk_size = 100
    complete: set[str] = set()

    for i in range(0, len(wanted), chunk_size):
        chunk = wanted[i:i + chunk_size]
        request = {
            TABLE_NAME: {
                "Keys": [
                    {"PK": {"S": f"{IMPACT_PK_PREFIX}{p}"},
                     "SK": {"S": IMPACT_SK}}
                    for p in chunk
                ],
                "ProjectionExpression": "PK, synopsis, impact_score",
            }
        }
        while request:
            response = client.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(TABLE_NAME, []):
                pk = item.get("PK", {}).get("S", "")
                if not pk.startswith(IMPACT_PK_PREFIX):
                    continue
                synopsis = item.get("synopsis", {}).get("S", "")
                if not synopsis:
                    continue
                if "impact_score" not in item:
                    continue
                pmid = pk[len(IMPACT_PK_PREFIX):]
                complete.add(pmid)
            request = response.get("UnprocessedKeys") or None

    return {
        "complete": sorted(complete),
        "incomplete": sorted(set(wanted) - complete),
    }
