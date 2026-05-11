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
    )
    return _engine_singleton
