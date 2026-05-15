"""MariaDB writer for the daily enrichment job (#37 step 2).

Three-table contract:
    reciterai_entities       (registry: external_id ↔ internal entity_id)
        ↓
    reciterai_synopsis  +  reciterai_impact  (sinks for the LLM outputs)

The daily job owns entity registration for new PMIDs (decision 1a in design
discussion): when a pmid does not yet exist in reciterai_entities, this
module inserts it before writing the synopsis + impact rows. Existing
entities are reused.

Schema oddity: reciterai_entities.entity_id and reciterai_synopsis.id are
non-auto-increment PKs (NOT NULL DEFAULT 0). Tracked at #42. Workaround
here: SELECT COALESCE(MAX(id), 0) + 1 ... FOR UPDATE inside a transaction
to allocate the next id under single-writer assumption (the daily cron is
the only expected writer to these tables). The ALTER TABLE migration
lives at docs/migrations/2026-05-15-auto-increment-entity-id-synopsis-id.sql
and is applied by the operator; remove this allocation in a follow-up PR
once the migration has been applied and verified in production.

Synopsis and impact are written by separate functions, not bundled in one
transaction (design decision 3). Synopsis is the upstream signal; if it
fails, the orchestrator can skip impact for that pmid without leaving a
half-row state.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

ENTITY_TYPE_PUBLICATION = "publication"


def ensure_entity(engine: Engine, pmid: str) -> int:
    """Look up or create a reciterai_entities row for the given pmid.

    Returns the entity_id (bigint). For an existing pmid, returns the
    registry id. For a new pmid, allocates MAX(entity_id) + 1 under
    FOR UPDATE and inserts. Always wrapped in a single transaction so
    the read-modify-write is atomic.

    Idempotent: safe to call repeatedly for the same pmid.
    """
    with engine.begin() as conn:
        existing = conn.execute(
            text(
                "SELECT entity_id FROM reciterai_entities "
                "WHERE entity_type = :et AND external_id = :ext"
            ),
            {"et": ENTITY_TYPE_PUBLICATION, "ext": str(pmid)},
        ).fetchone()
        if existing is not None:
            return int(existing[0])

        next_id = conn.execute(
            text(
                "SELECT COALESCE(MAX(entity_id), 0) + 1 "
                "FROM reciterai_entities FOR UPDATE"
            )
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO reciterai_entities "
                "(entity_id, entity_type, external_id, is_active) "
                "VALUES (:eid, :et, :ext, 1)"
            ),
            {
                "eid": int(next_id),
                "et": ENTITY_TYPE_PUBLICATION,
                "ext": str(pmid),
            },
        )
        return int(next_id)


def upsert_synopsis(
    engine: Engine,
    pmid: str,
    entity_id: int,
    synopsis: str,
    model: str,
) -> None:
    """Upsert a reciterai_synopsis row keyed on (entity_type, external_id).

    Existing row: UPDATE synopsis + model (modifyTimestamp auto-bumps).
    New row: allocate MAX(id) + 1 under FOR UPDATE and INSERT.

    The caller must have already obtained `entity_id` from ensure_entity().
    """
    with engine.begin() as conn:
        existing = conn.execute(
            text(
                "SELECT id FROM reciterai_synopsis "
                "WHERE entity_type = :et AND external_id = :ext"
            ),
            {"et": ENTITY_TYPE_PUBLICATION, "ext": str(pmid)},
        ).fetchone()

        if existing is not None:
            conn.execute(
                text(
                    "UPDATE reciterai_synopsis "
                    "SET synopsis = :syn, model = :model "
                    "WHERE id = :id"
                ),
                {"syn": synopsis, "model": model, "id": int(existing[0])},
            )
            return

        next_id = conn.execute(
            text(
                "SELECT COALESCE(MAX(id), 0) + 1 "
                "FROM reciterai_synopsis FOR UPDATE"
            )
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO reciterai_synopsis "
                "(id, entity_type, entity_id, external_id, synopsis, model) "
                "VALUES (:id, :et, :eid, :ext, :syn, :model)"
            ),
            {
                "id": int(next_id),
                "et": ENTITY_TYPE_PUBLICATION,
                "eid": int(entity_id),
                "ext": str(pmid),
                "syn": synopsis,
                "model": model,
            },
        )


def upsert_impact(
    engine: Engine,
    pmid: str,
    entity_id: int,
    impact_score: float,
    justification: str,
    model: str,
) -> None:
    """Upsert a reciterai_impact row keyed on (entity_type, entity_id).

    impact's `id` IS auto_increment, so a single INSERT ... ON DUPLICATE
    KEY UPDATE is safe. impactScore is decimal(5,2); we quantize before
    sending to avoid lossy float→decimal round-trips at the DB.
    """
    score = Decimal(str(impact_score)).quantize(Decimal("0.01"))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reciterai_impact "
                "(entity_type, entity_id, external_id, impactScore, justification, model) "
                "VALUES (:et, :eid, :ext, :score, :just, :model) "
                "ON DUPLICATE KEY UPDATE "
                "    impactScore = VALUES(impactScore), "
                "    justification = VALUES(justification), "
                "    model = VALUES(model)"
            ),
            {
                "et": ENTITY_TYPE_PUBLICATION,
                "eid": int(entity_id),
                "ext": str(pmid),
                "score": score,
                "just": justification,
                "model": model,
            },
        )
