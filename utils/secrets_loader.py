"""Lambda-only DB credential loader (Phase 10 hot-path).

Fetches the `reciterai/reciter-analysis-db` secret from AWS Secrets
Manager on import and populates `DB_HOST`, `DB_USERNAME`,
`DB_PASSWORD`, and `DB_NAME` in `os.environ`.

Runs only when both:

  1. `AWS_LAMBDA_FUNCTION_NAME` env var is set (Lambda runtime), AND
  2. At least one of the four `DB_*` env vars is unset.

This keeps local dev untouched — `~/.zshrc` populates the four vars
before any tooling runs — and keeps tests untouched (no
`AWS_LAMBDA_FUNCTION_NAME`, so no fetch).

Import this BEFORE the first touch of `utils.sql_queries` /
`utils.db` so the SQLAlchemy engine factory finds creds populated.

Failure mode: if the secret fetch fails, log the error and continue.
`utils.db.get_engine()` then raises a clear `ValueError` naming the
missing env var, which is a better operator signal than a hidden
silent fallback.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

SECRET_ID = "reciterai/reciter-analysis-db"
DB_ENV_KEYS = ("DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_NAME")


def load_db_credentials_from_secret(secret_id: str = SECRET_ID) -> bool:
    """Fetch DB creds from Secrets Manager and populate `os.environ`.

    Returns True iff env vars were actually populated; False on no-op
    (creds already present) or fetch failure. Safe to call multiple
    times — short-circuits when every `DB_*` key is already set.
    """
    if all(os.environ.get(k) for k in DB_ENV_KEYS):
        return False
    try:
        import boto3  # local import: tests can monkeypatch this module

        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=secret_id)
        secret = json.loads(resp["SecretString"])
    except Exception as exc:
        logger.warning(
            "secrets_loader: failed to fetch %s: %s. utils.db.get_engine() "
            "will raise on first use if DB_* env vars remain unset.",
            secret_id, exc,
        )
        return False
    populated = False
    for key in DB_ENV_KEYS:
        if key in secret and not os.environ.get(key):
            os.environ[key] = str(secret[key])
            populated = True
    return populated


# Auto-load only inside the Lambda runtime. Local dev / tests rely on
# DB_* env vars from `~/.zshrc` or fixtures.
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    load_db_credentials_from_secret()
