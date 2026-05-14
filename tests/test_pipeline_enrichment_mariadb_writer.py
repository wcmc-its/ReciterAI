"""Unit tests for pipeline_enrichment.mariadb_writer.

Mocks the sqlalchemy Engine/Connection. Verifies the SQL contract — which
table is written, which key the upsert keys on, whether new vs. existing
paths fire. Behavioral correctness against the real MariaDB schema is
covered by a follow-up integration test (filed as a separate issue).
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from pipeline_enrichment.mariadb_writer import (
    ENTITY_TYPE_PUBLICATION,
    ensure_entity,
    upsert_impact,
    upsert_synopsis,
)


def _make_engine_with_conn():
    """Return (engine_mock, conn_mock) where engine.begin() yields conn."""
    conn = MagicMock()
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = conn
    engine.begin.return_value.__exit__.return_value = False
    return engine, conn


def _result_with_row(value):
    """Build a MagicMock for an execute result whose fetchone() returns the row."""
    result = MagicMock()
    result.fetchone.return_value = value
    return result


def _result_with_scalar(scalar):
    result = MagicMock()
    result.scalar.return_value = scalar
    return result


def _sql_of(call) -> str:
    """Extract the SQL text from a Connection.execute(text(...), {...}) call."""
    return str(call.args[0])


# ---------------------------------------------------------------------------
# ensure_entity
# ---------------------------------------------------------------------------

def test_ensure_entity_returns_existing_id_without_insert():
    engine, conn = _make_engine_with_conn()
    # SELECT returns an existing entity_id
    conn.execute.return_value = _result_with_row((42,))

    eid = ensure_entity(engine, pmid="40927852")

    assert eid == 42
    assert conn.execute.call_count == 1
    sql = _sql_of(conn.execute.call_args_list[0])
    assert "SELECT entity_id FROM reciterai_entities" in sql
    assert conn.execute.call_args_list[0].args[1] == {
        "et": ENTITY_TYPE_PUBLICATION,
        "ext": "40927852",
    }


def test_ensure_entity_allocates_max_plus_one_and_inserts_when_missing():
    engine, conn = _make_engine_with_conn()
    # 1st execute (SELECT existing) → no row
    # 2nd execute (SELECT MAX+1) → 7104
    # 3rd execute (INSERT) → ignored
    conn.execute.side_effect = [
        _result_with_row(None),
        _result_with_scalar(7104),
        MagicMock(),
    ]

    eid = ensure_entity(engine, pmid="40927852")

    assert eid == 7104
    assert conn.execute.call_count == 3

    select_max_sql = _sql_of(conn.execute.call_args_list[1])
    assert "COALESCE(MAX(entity_id), 0) + 1" in select_max_sql
    assert "FOR UPDATE" in select_max_sql

    insert_sql = _sql_of(conn.execute.call_args_list[2])
    assert "INSERT INTO reciterai_entities" in insert_sql
    insert_params = conn.execute.call_args_list[2].args[1]
    assert insert_params == {
        "eid": 7104,
        "et": ENTITY_TYPE_PUBLICATION,
        "ext": "40927852",
    }


def test_ensure_entity_handles_empty_table():
    """First-ever entity row: COALESCE returns 0+1 = 1."""
    engine, conn = _make_engine_with_conn()
    conn.execute.side_effect = [
        _result_with_row(None),
        _result_with_scalar(1),
        MagicMock(),
    ]
    eid = ensure_entity(engine, pmid="12345")
    assert eid == 1


def test_ensure_entity_coerces_pmid_to_string():
    engine, conn = _make_engine_with_conn()
    conn.execute.return_value = _result_with_row((9,))
    ensure_entity(engine, pmid=40927852)  # passed as int
    assert conn.execute.call_args_list[0].args[1]["ext"] == "40927852"


# ---------------------------------------------------------------------------
# upsert_synopsis
# ---------------------------------------------------------------------------

def test_upsert_synopsis_updates_when_row_exists():
    engine, conn = _make_engine_with_conn()
    conn.execute.side_effect = [
        _result_with_row((193,)),  # existing id
        MagicMock(),                # UPDATE result
    ]

    upsert_synopsis(
        engine,
        pmid="40927852",
        entity_id=7104,
        synopsis="LIFE-BTK 2-year results — resorbable scaffold beats PTA.",
        model="gpt-5.1",
    )

    assert conn.execute.call_count == 2
    update_sql = _sql_of(conn.execute.call_args_list[1])
    assert "UPDATE reciterai_synopsis" in update_sql
    assert "id = :id" in update_sql
    params = conn.execute.call_args_list[1].args[1]
    assert params == {
        "syn": "LIFE-BTK 2-year results — resorbable scaffold beats PTA.",
        "model": "gpt-5.1",
        "id": 193,
    }
    # Must NOT have allocated a new id via MAX+1.
    for call in conn.execute.call_args_list:
        assert "MAX(id)" not in _sql_of(call)


def test_upsert_synopsis_inserts_with_max_plus_one_when_new():
    engine, conn = _make_engine_with_conn()
    conn.execute.side_effect = [
        _result_with_row(None),      # no existing row
        _result_with_scalar(12588),  # MAX+1
        MagicMock(),                  # INSERT
    ]

    upsert_synopsis(
        engine,
        pmid="40999999",
        entity_id=7104,
        synopsis="Fresh synopsis text.",
        model="gpt-5.1",
    )

    assert conn.execute.call_count == 3
    insert_sql = _sql_of(conn.execute.call_args_list[2])
    assert "INSERT INTO reciterai_synopsis" in insert_sql
    insert_params = conn.execute.call_args_list[2].args[1]
    assert insert_params == {
        "id": 12588,
        "et": ENTITY_TYPE_PUBLICATION,
        "eid": 7104,
        "ext": "40999999",
        "syn": "Fresh synopsis text.",
        "model": "gpt-5.1",
    }


# ---------------------------------------------------------------------------
# upsert_impact
# ---------------------------------------------------------------------------

def test_upsert_impact_uses_single_on_duplicate_key_update():
    """impact.id is auto_increment — no MAX+1 dance needed."""
    engine, conn = _make_engine_with_conn()
    conn.execute.return_value = MagicMock()

    upsert_impact(
        engine,
        pmid="40927852",
        entity_id=7104,
        impact_score=61.0,
        justification="Multicenter RCT, novel scaffold, strong efficacy.",
        model="gpt-5.1",
    )

    assert conn.execute.call_count == 1
    sql = _sql_of(conn.execute.call_args_list[0])
    assert "INSERT INTO reciterai_impact" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    # Must NOT touch reciterai_synopsis or reciterai_entities.
    assert "reciterai_synopsis" not in sql
    assert "reciterai_entities" not in sql


def test_upsert_impact_quantizes_score_to_two_decimals():
    """impactScore is decimal(5,2). Send Decimal, not float, to the DB."""
    engine, conn = _make_engine_with_conn()
    conn.execute.return_value = MagicMock()

    upsert_impact(
        engine,
        pmid="40927852",
        entity_id=7104,
        impact_score=61.0049,
        justification="ok",
        model="gpt-5.1",
    )
    params = conn.execute.call_args_list[0].args[1]
    assert params["score"] == Decimal("61.00")
    assert isinstance(params["score"], Decimal)


def test_upsert_impact_rounds_correctly_at_two_decimals():
    engine, conn = _make_engine_with_conn()
    conn.execute.return_value = MagicMock()
    upsert_impact(
        engine,
        pmid="x",
        entity_id=1,
        impact_score=56.999,
        justification="ok",
        model="gpt-5.1",
    )
    # Decimal.quantize uses ROUND_HALF_EVEN by default; 56.999 → 57.00.
    assert conn.execute.call_args_list[0].args[1]["score"] == Decimal("57.00")


def test_upsert_impact_coerces_pmid_and_entity_id_types():
    engine, conn = _make_engine_with_conn()
    conn.execute.return_value = MagicMock()
    upsert_impact(
        engine,
        pmid=40927852,        # int, not str
        entity_id="7104",     # str, not int
        impact_score=50.0,
        justification="ok",
        model="gpt-5.1",
    )
    params = conn.execute.call_args_list[0].args[1]
    assert params["ext"] == "40927852"
    assert params["eid"] == 7104
    assert isinstance(params["eid"], int)


# ---------------------------------------------------------------------------
# transaction discipline
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fn,kwargs",
    [
        (ensure_entity, {"pmid": "1"}),
        (upsert_synopsis, {"pmid": "1", "entity_id": 1, "synopsis": "s", "model": "m"}),
        (upsert_impact, {"pmid": "1", "entity_id": 1, "impact_score": 1.0, "justification": "j", "model": "m"}),
    ],
)
def test_each_writer_call_uses_one_transaction(fn, kwargs):
    """Each public function must open exactly one engine.begin() block.

    This guards against accidentally splitting a read-modify-write across
    transactions (which would defeat the FOR UPDATE lock).
    """
    engine, conn = _make_engine_with_conn()
    # Make ensure_entity / upsert_synopsis take their "existing row" fast path.
    conn.execute.return_value = _result_with_row((1,))
    fn(engine, **kwargs)
    assert engine.begin.call_count == 1
