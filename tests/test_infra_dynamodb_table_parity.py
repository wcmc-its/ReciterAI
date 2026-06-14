"""#223: keep infra/dynamodb_table.json byte-faithful to the table the code
actually creates, and assert the deletion-protection hardening is present in BOTH
the IaC spec and create_chatbot_table.

The IaC file is only a trustworthy rebuild spec if it cannot silently drift from
the creator. If create_chatbot_table's KeySchema / GSIs change without the JSON
being updated (or vice versa), these tests fail.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

from utils import dynamodb_helpers

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "infra" / "dynamodb_table.json"


def _capture_create_table_kwargs():
    """Call create_chatbot_table with a mock client and return the kwargs it
    passed to create_table."""
    client = MagicMock()
    # create_chatbot_table references client.exceptions.ResourceInUseException in
    # an `except` clause, which must be a real exception class, not a Mock.
    client.exceptions.ResourceInUseException = type(
        "ResourceInUseException", (Exception,), {}
    )
    dynamodb_helpers.create_chatbot_table(client, table_name="reciterai")
    assert client.create_table.called, "create_chatbot_table did not call create_table"
    return client.create_table.call_args.kwargs


def test_iac_spec_matches_create_table():
    kwargs = _capture_create_table_kwargs()
    spec = json.loads(SPEC_PATH.read_text())

    assert spec["TableName"] == "reciterai"
    assert spec["BillingMode"] == kwargs["BillingMode"]
    assert spec["KeySchema"] == kwargs["KeySchema"]
    assert spec["AttributeDefinitions"] == kwargs["AttributeDefinitions"]

    spec_gsis = {g["IndexName"]: g for g in spec["GlobalSecondaryIndexes"]}
    code_gsis = {g["IndexName"]: g for g in kwargs["GlobalSecondaryIndexes"]}
    assert spec_gsis.keys() == code_gsis.keys()
    for name, code_gsi in code_gsis.items():
        assert spec_gsis[name]["KeySchema"] == code_gsi["KeySchema"]
        assert spec_gsis[name]["Projection"] == code_gsi["Projection"]


def test_deletion_protection_set_in_both():
    kwargs = _capture_create_table_kwargs()
    spec = json.loads(SPEC_PATH.read_text())
    assert kwargs.get("DeletionProtectionEnabled") is True, (
        "create_chatbot_table must set DeletionProtectionEnabled=True (#223)"
    )
    assert spec.get("DeletionProtectionEnabled") is True


def test_pitr_intent_declared_in_spec():
    # PITR is not a create-table attribute; it is declared here and applied by
    # scripts/apply_backup_config.sh. Guard that the intent stays recorded.
    spec = json.loads(SPEC_PATH.read_text())
    assert spec["_pitr"]["PointInTimeRecoveryEnabled"] is True
