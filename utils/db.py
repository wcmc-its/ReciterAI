"""
ReciterDB SQLAlchemy engine factory.

Reads DB_HOST, DB_USERNAME, DB_PASSWORD, DB_NAME from the environment and
returns a cached SQLAlchemy Engine for MySQL+PyMySQL. Replaces the previous
hardcoded sys.path import of ReciterAI-POC's core.db (see issue #1).
"""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


_engine_singleton: Optional[Engine] = None


def _int_env(name: str, default: int) -> int:
    """Read an int from the environment, falling back to `default` on a
    missing/blank/non-int value (connection knobs, not pipeline tunables)."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_engine() -> Engine:
    global _engine_singleton
    if _engine_singleton is not None:
        return _engine_singleton

    host = os.getenv("DB_HOST", "")
    username = os.getenv("DB_USERNAME", "")
    password = os.getenv("DB_PASSWORD", "")
    db_name = os.getenv("DB_NAME", "")

    if not host:
        raise ValueError("DB_HOST environment variable must be set")
    if not username:
        raise ValueError("DB_USERNAME environment variable must be set")
    if not db_name:
        raise ValueError("DB_NAME environment variable must be set")

    dsn = f"mysql+pymysql://{username}:{password}@{host}/{db_name}?charset=utf8mb4"
    _engine_singleton = create_engine(
        dsn,
        pool_pre_ping=True,
        pool_recycle=3600,
        future=True,
        connect_args={
            # #224: bound a wedged/half-open ReCiterDB connection. Without these
            # a MariaDB that accepts the TCP connection then stalls mid-query
            # hangs the run indefinitely on long-lived Fargate (Lambda's 15-min
            # wall is its own fail-safe; Fargate has none). pymysql honors all
            # three (seconds). read_timeout default 120 = ~8x the observed
            # full-corpus extract (74k rows in ~15.5s, 2026-06-13) so the cold
            # path never false-aborts; raise DB_READ_TIMEOUT for a slower DB.
            "connect_timeout": _int_env("DB_CONNECT_TIMEOUT", 10),
            "read_timeout": _int_env("DB_READ_TIMEOUT", 120),
            "write_timeout": _int_env("DB_WRITE_TIMEOUT", 60),
        },
    )
    return _engine_singleton
