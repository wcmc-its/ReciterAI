"""utils/secrets_loader.py — Lambda-only DB cred fetch."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

from utils import secrets_loader as sl


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    """Each test starts with no DB_*, no OPENAI_API_KEY, and no AWS_LAMBDA_FUNCTION_NAME."""
    for k in sl.DB_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    for k in sl.OPENAI_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)


def _stub_boto3(monkeypatch, secret_value: dict):
    """Install a fake boto3 module so load_db_credentials_from_secret
    fetches the given dict instead of hitting AWS."""
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {
        "SecretString": json.dumps(secret_value)
    }
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    return fake_client


def test_populates_all_four_db_vars_when_unset(monkeypatch):
    _stub_boto3(monkeypatch, {
        "DB_HOST": "h", "DB_USERNAME": "u",
        "DB_PASSWORD": "p", "DB_NAME": "n",
    })
    assert sl.load_db_credentials_from_secret() is True
    import os
    for k, v in (("DB_HOST", "h"), ("DB_USERNAME", "u"),
                 ("DB_PASSWORD", "p"), ("DB_NAME", "n")):
        assert os.environ[k] == v


def test_short_circuits_when_all_already_set(monkeypatch):
    for k in sl.DB_ENV_KEYS:
        monkeypatch.setenv(k, "preexisting")
    fake_client = _stub_boto3(monkeypatch, {"DB_HOST": "h"})
    assert sl.load_db_credentials_from_secret() is False
    fake_client.get_secret_value.assert_not_called()


def test_does_not_overwrite_already_set_keys(monkeypatch):
    monkeypatch.setenv("DB_HOST", "preexisting")
    _stub_boto3(monkeypatch, {
        "DB_HOST": "from-secret", "DB_USERNAME": "u",
        "DB_PASSWORD": "p", "DB_NAME": "n",
    })
    sl.load_db_credentials_from_secret()
    import os
    assert os.environ["DB_HOST"] == "preexisting"
    assert os.environ["DB_USERNAME"] == "u"


def test_fetch_failure_returns_false_does_not_raise(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_secret_value.side_effect = RuntimeError("network down")
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    assert sl.load_db_credentials_from_secret() is False


def test_fetch_uses_default_secret_id(monkeypatch):
    fake_client = _stub_boto3(monkeypatch, {
        "DB_HOST": "h", "DB_USERNAME": "u",
        "DB_PASSWORD": "p", "DB_NAME": "n",
    })
    sl.load_db_credentials_from_secret()
    fake_client.get_secret_value.assert_called_once_with(
        SecretId="reciterai/reciter-analysis-db"
    )


def test_fetch_accepts_custom_secret_id(monkeypatch):
    fake_client = _stub_boto3(monkeypatch, {
        "DB_HOST": "h", "DB_USERNAME": "u",
        "DB_PASSWORD": "p", "DB_NAME": "n",
    })
    sl.load_db_credentials_from_secret(secret_id="custom/id")
    fake_client.get_secret_value.assert_called_once_with(SecretId="custom/id")


def test_partial_secret_only_populates_matching_keys(monkeypatch):
    _stub_boto3(monkeypatch, {"DB_HOST": "h", "DB_NAME": "n"})
    sl.load_db_credentials_from_secret()
    import os
    assert os.environ.get("DB_HOST") == "h"
    assert os.environ.get("DB_NAME") == "n"
    assert os.environ.get("DB_USERNAME") is None
    assert os.environ.get("DB_PASSWORD") is None


def test_secret_values_coerced_to_str(monkeypatch):
    _stub_boto3(monkeypatch, {
        "DB_HOST": "h", "DB_USERNAME": "u",
        "DB_PASSWORD": "p", "DB_NAME": 12345,  # int — must be coerced
    })
    sl.load_db_credentials_from_secret()
    import os
    assert os.environ["DB_NAME"] == "12345"


# ---------------------------------------------------------------------------
# OpenAI API key loader — mirrors the DB pattern (same shared helper)
# ---------------------------------------------------------------------------


def test_openai_loader_populates_key_when_unset(monkeypatch):
    fake_client = _stub_boto3(monkeypatch, {"OPENAI_API_KEY": "sk-from-secret"})
    assert sl.load_openai_api_key_from_secret() is True
    import os
    assert os.environ["OPENAI_API_KEY"] == "sk-from-secret"
    fake_client.get_secret_value.assert_called_once_with(
        SecretId="reciterai/openai-api-key"
    )


def test_openai_loader_short_circuits_when_already_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "preexisting")
    fake_client = _stub_boto3(monkeypatch, {"OPENAI_API_KEY": "from-secret"})
    assert sl.load_openai_api_key_from_secret() is False
    fake_client.get_secret_value.assert_not_called()
    import os
    assert os.environ["OPENAI_API_KEY"] == "preexisting"


def test_openai_loader_failure_does_not_raise(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_secret_value.side_effect = RuntimeError("network down")
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    assert sl.load_openai_api_key_from_secret() is False


def test_openai_loader_accepts_custom_secret_id(monkeypatch):
    fake_client = _stub_boto3(monkeypatch, {"OPENAI_API_KEY": "sk-x"})
    sl.load_openai_api_key_from_secret(secret_id="custom/openai")
    fake_client.get_secret_value.assert_called_once_with(SecretId="custom/openai")


def test_db_and_openai_loaders_share_short_circuit_helper(monkeypatch):
    """A single _populate_env_from_secret should handle both — verify the
    DB loader still works after the refactor by re-running its happy path."""
    _stub_boto3(monkeypatch, {
        "DB_HOST": "h", "DB_USERNAME": "u",
        "DB_PASSWORD": "p", "DB_NAME": "n",
    })
    assert sl.load_db_credentials_from_secret() is True
    import os
    assert os.environ["DB_HOST"] == "h"
