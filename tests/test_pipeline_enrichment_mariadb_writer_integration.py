"""Live-MariaDB integration tests for pipeline_enrichment.mariadb_writer (#43).

Runs against the real ReciterDB MariaDB instance. Gated on
`@pytest.mark.mariadb`; skipped by default. To execute:

    pytest -m mariadb tests/test_pipeline_enrichment_mariadb_writer_integration.py

Requires:
- ReciterDB env vars set (DB_HOST, DB_USERNAME, DB_PASSWORD, DB_NAME) —
  same shape `utils.db.get_engine` reads.
- The three writer-managed tables exist:
  `reciterai_entities`, `reciterai_synopsis`, `reciterai_impact`.

Isolation strategy:
- Every test row's `external_id` carries the prefix
  `INTEGRATION_TEST_` plus a per-test UUID4 — guaranteed not to collide
  with any production PMID (which are numeric strings).
- A module-level fixture deletes all rows matching that prefix before
  and after every test, so a crashed test doesn't pollute the tables for
  the next run.
- Tests run against the same tables as production. Pointing this at a
  dev/test ReciterDB is preferred; pointing it at prod is recommended
  only with the prefix isolation in mind.

Covers behaviors that only manifest against real MariaDB:
- ensure_entity returns the same entity_id on a second call (no
  duplicate insert under MAX+1 allocation)
- upsert_synopsis updates the existing row on (entity_type, external_id)
  collision (the SELECT-then-UPDATE branch)
- upsert_synopsis allocates a fresh id when no prior row exists (the
  SELECT-MAX+1-then-INSERT branch)
- upsert_impact's ON DUPLICATE KEY UPDATE keys on (entity_type, entity_id)
  and replaces the right fields
- decimal(5,2) quantization survives the round-trip
- modifyTimestamp auto-bumps on update (DB-level trigger / column attribute)
"""
from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal

import pytest

pytestmark = pytest.mark.mariadb

sqlalchemy = pytest.importorskip("sqlalchemy")
pymysql = pytest.importorskip("pymysql")  # noqa: F841 — surfaces missing driver

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from pipeline_enrichment.mariadb_writer import (  # noqa: E402
    ENTITY_TYPE_PUBLICATION,
    ensure_entity,
    upsert_impact,
    upsert_synopsis,
)


TEST_PREFIX = "INTEGRATION_TEST_"


def _get_test_engine():
    """Return a SQLAlchemy engine, or skip if env not configured / DB unreachable."""
    if not all(os.environ.get(k) for k in ("DB_HOST", "DB_USERNAME", "DB_NAME")):
        pytest.skip("DB_HOST / DB_USERNAME / DB_NAME not configured; skipping live-MariaDB tests")
    from utils.db import get_engine

    try:
        engine = get_engine()
        # Probe the connection so a failed pool_pre_ping surfaces as a skip
        # rather than a test failure.
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as e:
        pytest.skip(f"ReciterDB unreachable: {e}")
    return engine


def _purge_test_rows(engine) -> None:
    """Delete every row carrying the INTEGRATION_TEST_ prefix.

    Order matters: child rows (synopsis, impact) referencing entity_id
    must be removed before the parent entities row to avoid FK errors
    on schemas that declare them. The schemas in question do NOT declare
    FKs today, but the delete order is correct regardless.
    """
    with engine.begin() as conn:
        for tbl in ("reciterai_synopsis", "reciterai_impact", "reciterai_entities"):
            conn.execute(
                text(f"DELETE FROM {tbl} WHERE external_id LIKE :p"),
                {"p": TEST_PREFIX + "%"},
            )


@pytest.fixture(scope="module")
def engine():
    eng = _get_test_engine()
    _purge_test_rows(eng)
    yield eng
    _purge_test_rows(eng)


@pytest.fixture
def fresh_pmid(engine):
    """A guaranteed-unique external_id with the test prefix.

    Function-scoped so each test gets a new external_id, and rows touched
    by one test cannot bleed into another.
    """
    pmid = f"{TEST_PREFIX}{uuid.uuid4().hex}"
    yield pmid
    with engine.begin() as conn:
        for tbl in ("reciterai_synopsis", "reciterai_impact", "reciterai_entities"):
            conn.execute(
                text(f"DELETE FROM {tbl} WHERE external_id = :ext"),
                {"ext": pmid},
            )


def _select_one(engine, sql: str, params: dict):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).fetchone()


def test_ensure_entity_idempotent(engine, fresh_pmid):
    """Second call with the same external_id must return the same entity_id —
    not allocate a fresh MAX+1."""
    first = ensure_entity(engine, fresh_pmid)
    second = ensure_entity(engine, fresh_pmid)
    assert first == second
    # Exactly one row exists for this external_id.
    row = _select_one(
        engine,
        "SELECT COUNT(*) FROM reciterai_entities "
        "WHERE entity_type = :et AND external_id = :ext",
        {"et": ENTITY_TYPE_PUBLICATION, "ext": fresh_pmid},
    )
    assert row[0] == 1


def test_upsert_synopsis_insert_then_update(engine, fresh_pmid):
    """First call inserts; second call with same (entity_type, external_id)
    must hit the SELECT-found branch and UPDATE the existing row, not insert
    a duplicate."""
    entity_id = ensure_entity(engine, fresh_pmid)
    upsert_synopsis(engine, fresh_pmid, entity_id, synopsis="first synopsis", model="m1")
    upsert_synopsis(engine, fresh_pmid, entity_id, synopsis="second synopsis", model="m2")

    rows = _select_one(
        engine,
        "SELECT COUNT(*), MIN(synopsis), MAX(synopsis), MIN(model), MAX(model) "
        "FROM reciterai_synopsis WHERE entity_type = :et AND external_id = :ext",
        {"et": ENTITY_TYPE_PUBLICATION, "ext": fresh_pmid},
    )
    assert rows[0] == 1, "second upsert must update, not insert a duplicate"
    # MIN==MAX confirms a single distinct row carrying the latest values.
    assert rows[1] == rows[2] == "second synopsis"
    assert rows[3] == rows[4] == "m2"


def test_upsert_synopsis_modify_timestamp_bumps_on_update(engine, fresh_pmid):
    """modifyTimestamp must advance when the row is updated. Schema declares
    this column with ON UPDATE CURRENT_TIMESTAMP; the test confirms the
    declaration is in place (regression guard for a manual schema edit)."""
    entity_id = ensure_entity(engine, fresh_pmid)
    upsert_synopsis(engine, fresh_pmid, entity_id, synopsis="v1", model="m1")
    before = _select_one(
        engine,
        "SELECT modifyTimestamp FROM reciterai_synopsis "
        "WHERE entity_type = :et AND external_id = :ext",
        {"et": ENTITY_TYPE_PUBLICATION, "ext": fresh_pmid},
    )[0]

    # Sleep one second so the timestamp difference is detectable at
    # second resolution (the column is DATETIME).
    time.sleep(1.1)
    upsert_synopsis(engine, fresh_pmid, entity_id, synopsis="v2", model="m2")
    after = _select_one(
        engine,
        "SELECT modifyTimestamp FROM reciterai_synopsis "
        "WHERE entity_type = :et AND external_id = :ext",
        {"et": ENTITY_TYPE_PUBLICATION, "ext": fresh_pmid},
    )[0]
    assert after > before, "modifyTimestamp did not advance on UPDATE"


def test_upsert_impact_insert_then_update(engine, fresh_pmid):
    """impact's ON DUPLICATE KEY UPDATE must key on (entity_type, entity_id)
    and replace the score / justification / model fields on a second write."""
    entity_id = ensure_entity(engine, fresh_pmid)
    upsert_impact(engine, fresh_pmid, entity_id, impact_score=12.34, justification="first", model="m1")
    upsert_impact(engine, fresh_pmid, entity_id, impact_score=56.78, justification="second", model="m2")

    rows = _select_one(
        engine,
        "SELECT COUNT(*), MAX(impactScore), MAX(justification), MAX(model) "
        "FROM reciterai_impact WHERE entity_type = :et AND entity_id = :eid",
        {"et": ENTITY_TYPE_PUBLICATION, "eid": entity_id},
    )
    assert rows[0] == 1, "second impact upsert must update, not insert a duplicate"
    assert Decimal(str(rows[1])) == Decimal("56.78")
    assert rows[2] == "second"
    assert rows[3] == "m2"


def test_upsert_impact_decimal_quantization_round_trip(engine, fresh_pmid):
    """A float like 12.345 must land as Decimal('12.35') after quantize, and
    survive the decimal(5,2) round-trip with no precision loss."""
    entity_id = ensure_entity(engine, fresh_pmid)
    upsert_impact(engine, fresh_pmid, entity_id, impact_score=12.345, justification="q", model="m")

    row = _select_one(
        engine,
        "SELECT impactScore FROM reciterai_impact "
        "WHERE entity_type = :et AND entity_id = :eid",
        {"et": ENTITY_TYPE_PUBLICATION, "eid": entity_id},
    )
    assert Decimal(str(row[0])) == Decimal("12.35")


def test_upsert_impact_high_score_clamps_to_two_decimals(engine, fresh_pmid):
    """Boundary: a value already at two decimal places must land unchanged."""
    entity_id = ensure_entity(engine, fresh_pmid)
    upsert_impact(engine, fresh_pmid, entity_id, impact_score=99.99, justification="ceiling", model="m")

    row = _select_one(
        engine,
        "SELECT impactScore FROM reciterai_impact "
        "WHERE entity_type = :et AND entity_id = :eid",
        {"et": ENTITY_TYPE_PUBLICATION, "eid": entity_id},
    )
    assert Decimal(str(row[0])) == Decimal("99.99")
