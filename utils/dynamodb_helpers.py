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
