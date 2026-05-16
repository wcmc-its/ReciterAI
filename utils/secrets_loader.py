"""Lambda-only credential loader (Phase 10 hot-path).

Fetches secrets from AWS Secrets Manager on import and populates the
corresponding env vars. Two secrets are loaded today:

- `reciterai/reciter-analysis-db` → `DB_HOST`, `DB_USERNAME`, `DB_PASSWORD`, `DB_NAME`
  (consumed by `utils.sql_queries` / `utils.db`)
- `reciterai/openai-api-key` → `OPENAI_API_KEY`
  (consumed by `utils.openai_client` for the gpt-5.1 fallback on Bedrock
  content-filter and any other OpenAI surface)

Each loader runs only when both:

  1. `AWS_LAMBDA_FUNCTION_NAME` env var is set (Lambda runtime), AND
  2. At least one of the target env vars is unset.

This keeps local dev untouched — `~/.zshrc` populates the env vars
before any tooling runs — and keeps tests untouched (no
`AWS_LAMBDA_FUNCTION_NAME`, so no fetch).

Import this BEFORE the first touch of `utils.sql_queries`,
`utils.db`, or `utils.openai_client` so downstream factories find
creds populated.

Failure mode: if a secret fetch fails, log the error and continue.
Downstream code raises a clear error on first use if the env var is
still unset, which is a better operator signal than a hidden silent
fallback.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

SECRET_ID = "reciterai/reciter-analysis-db"
DB_ENV_KEYS = ("DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_NAME")

OPENAI_SECRET_ID = "reciterai/openai-api-key"
OPENAI_ENV_KEYS = ("OPENAI_API_KEY",)


def _populate_env_from_secret(secret_id: str, env_keys: tuple[str, ...]) -> bool:
    """Fetch `secret_id` and copy any of `env_keys` into `os.environ`.

    Short-circuits when every key in `env_keys` is already present.
    Returns True iff env vars were actually populated; False on no-op
    (already present) or fetch failure. Safe to call multiple times.
    """
    if all(os.environ.get(k) for k in env_keys):
        return False
    try:
        import boto3  # local import: tests can monkeypatch this module

        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=secret_id)
        secret = json.loads(resp["SecretString"])
    except Exception as exc:
        logger.warning(
            "secrets_loader: failed to fetch %s: %s. Downstream code "
            "will raise on first use if the env vars remain unset.",
            secret_id, exc,
        )
        return False
    populated = False
    for key in env_keys:
        if key in secret and not os.environ.get(key):
            os.environ[key] = str(secret[key])
            populated = True
    return populated


def load_db_credentials_from_secret(secret_id: str = SECRET_ID) -> bool:
    """Fetch DB creds from Secrets Manager and populate `os.environ`.

    Thin wrapper around `_populate_env_from_secret` for the DB-creds
    secret; kept for backward-compatible imports.
    """
    return _populate_env_from_secret(secret_id, DB_ENV_KEYS)


def load_openai_api_key_from_secret(secret_id: str = OPENAI_SECRET_ID) -> bool:
    """Fetch OpenAI API key from Secrets Manager and populate `OPENAI_API_KEY`.

    Mirrors the DB-creds pattern. Consumed by `utils.openai_client.get_default_client`
    which calls `OpenAI()` (which reads the env var automatically).
    """
    return _populate_env_from_secret(secret_id, OPENAI_ENV_KEYS)


# Auto-load only inside the Lambda runtime. Local dev / tests rely on
# env vars from `~/.zshrc` or fixtures.
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    load_db_credentials_from_secret()
    load_openai_api_key_from_secret()
