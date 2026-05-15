"""Live-DDB integration tests for pipeline_enrichment.watermark (#41).

Runs against real DynamoDB. Gated on `@pytest.mark.aws`; skipped by
default. To execute:

    pytest -m aws tests/test_pipeline_enrichment_watermark_integration.py

Requires:
- AWS credentials in the environment (the same `~/.zshrc` exports the
  pipeline uses).
- Permission to CreateTable / DescribeTable / GetItem / UpdateItem /
  DeleteItem on the test table name.

The fixture creates a dedicated test table (`reciterai-test-watermark`,
PK+SK only, PAY_PER_REQUEST) if it doesn't already exist, then cleans
the single watermark item before and after each test. The table itself
is left in place across runs — there's no cost for an empty PAY_PER_REQUEST
table, and CreateTable on every run would add ~30s of wait-for-ACTIVE.

Covers behaviors that only manifest against real DDB:
- ConditionalCheckFailedException on monotonicity guard
- Decimal-vs-int coercion on the wire
- UpdateItem semantics for a missing item (upsert)
- Round-trip of last_run_id across read/write
"""
from __future__ import annotations

import os
import time
from decimal import Decimal

import pytest

pytestmark = pytest.mark.aws

# Skip the whole module if the AWS credential probe fails. Keeps the
# error message focused on "you need creds" rather than a stack trace
# from inside boto3.
boto3 = pytest.importorskip("boto3")
botocore = pytest.importorskip("botocore")

from pipeline_enrichment.watermark import (  # noqa: E402
    PK,
    SK,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    mark_run_complete,
    mark_run_failed,
    mark_run_started,
    read_watermark,
)


TEST_TABLE_NAME = "reciterai-test-watermark"
TEST_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def _ensure_test_table() -> "boto3.resources.factory.dynamodb.Table":
    """Create the dedicated test table if absent; return its Table resource.

    Minimal schema — PK + SK strings, PAY_PER_REQUEST, no GSIs. The
    watermark module uses only GetItem and UpdateItem on the base table.
    """
    client = boto3.client("dynamodb", region_name=TEST_REGION)
    try:
        client.describe_table(TableName=TEST_TABLE_NAME)
    except client.exceptions.ResourceNotFoundException:
        client.create_table(
            TableName=TEST_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        # Wait for ACTIVE — first-run cost; subsequent invocations skip
        # this branch entirely.
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=TEST_TABLE_NAME)
        # describe_table returns ACTIVE only after a brief settle window;
        # poll the table_status to make sure UpdateItem won't 400.
        for _ in range(20):
            desc = client.describe_table(TableName=TEST_TABLE_NAME)
            if desc["Table"]["TableStatus"] == "ACTIVE":
                break
            time.sleep(1)

    resource = boto3.resource("dynamodb", region_name=TEST_REGION)
    return resource.Table(TEST_TABLE_NAME)


@pytest.fixture
def table():
    """Yield the test table with the watermark item cleaned before+after."""
    try:
        t = _ensure_test_table()
    except botocore.exceptions.NoCredentialsError:
        pytest.skip("AWS credentials not configured; skipping live-DDB tests")
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("UnrecognizedClientException", "InvalidSignatureException", "AccessDeniedException"):
            pytest.skip(f"AWS credentials present but unusable ({code}); skipping live-DDB tests")
        raise

    t.delete_item(Key={"PK": PK, "SK": SK})
    yield t
    t.delete_item(Key={"PK": PK, "SK": SK})


def test_read_watermark_returns_none_for_first_ever_run(table):
    assert read_watermark(table=table) is None


def test_mark_run_started_creates_item_when_absent(table):
    """UpdateItem with no prior item must upsert — verify the assumption
    the module relies on (no ConditionExpression on mark_run_started)."""
    run_id = mark_run_started(table=table)
    assert run_id  # UUID4 string

    wm = read_watermark(table=table)
    assert wm is not None
    assert wm.last_run_status == STATUS_IN_PROGRESS
    assert wm.last_run_id == run_id
    assert wm.last_run_started_at is not None
    # Successful watermark fields untouched on a fresh run.
    assert wm.last_successful_run_at is None
    assert wm.last_successful_max_pmid is None


def test_mark_run_complete_advances_watermark(table):
    mark_run_started(table=table)
    mark_run_complete(max_pmid=42_000_000, table=table)

    wm = read_watermark(table=table)
    assert wm is not None
    assert wm.last_run_status == STATUS_COMPLETE
    # Live DDB returns Decimal for Number-typed attrs; the module's
    # int() coercion is the contract under test here.
    assert wm.last_successful_max_pmid == 42_000_000
    assert isinstance(wm.last_successful_max_pmid, int)
    assert wm.last_successful_run_at is not None


def test_mark_run_complete_monotonicity_guard_blocks_backward_write(table):
    """A delayed retry of an older run must not move the watermark backward."""
    mark_run_started(table=table)
    mark_run_complete(max_pmid=42_000_000, table=table)

    with pytest.raises(botocore.exceptions.ClientError) as exc_info:
        mark_run_complete(max_pmid=41_000_000, table=table)
    assert exc_info.value.response["Error"]["Code"] == "ConditionalCheckFailedException"

    # Verify the watermark is unchanged after the failed write.
    wm = read_watermark(table=table)
    assert wm.last_successful_max_pmid == 42_000_000


def test_mark_run_complete_monotonicity_guard_allows_strict_advance(table):
    mark_run_started(table=table)
    mark_run_complete(max_pmid=42_000_000, table=table)
    mark_run_complete(max_pmid=43_000_000, table=table)

    wm = read_watermark(table=table)
    assert wm.last_successful_max_pmid == 43_000_000


def test_mark_run_failed_preserves_successful_watermark(table):
    mark_run_started(table=table)
    mark_run_complete(max_pmid=42_000_000, table=table)

    mark_run_started(table=table)
    mark_run_failed(table=table)

    wm = read_watermark(table=table)
    assert wm.last_run_status == STATUS_FAILED
    # Successful fields untouched — the next run retries the same delta.
    assert wm.last_successful_max_pmid == 42_000_000


def test_decimal_coercion_round_trip(table):
    """Module accepts int and DDB returns Decimal; read_watermark must
    coerce back to int for downstream callers that pass it to e.g. SQL
    parameters which choke on Decimal."""
    mark_run_started(table=table)
    mark_run_complete(max_pmid=99_999_999, table=table)

    wm = read_watermark(table=table)
    assert isinstance(wm.last_successful_max_pmid, int)
    assert not isinstance(wm.last_successful_max_pmid, Decimal)
