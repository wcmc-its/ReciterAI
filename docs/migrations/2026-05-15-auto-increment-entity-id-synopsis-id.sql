-- =====================================================================
-- Issue:        #42 — Schema: reciterai_entities.entity_id and
--               reciterai_synopsis.id should be AUTO_INCREMENT
-- Authored:     2026-05-15
-- Tables:       reciterai_entities, reciterai_synopsis
--
-- Motivation
-- ----------
-- Both PK columns currently declared `NOT NULL DEFAULT 0` with no
-- AUTO_INCREMENT. A naive `INSERT ... ON DUPLICATE KEY UPDATE` without
-- supplying `id` would land every new row at PK=0 and collide with the
-- first row inserted, silently overwriting it. The daily enrichment job
-- (pipeline_enrichment/mariadb_writer.py) works around this by
-- allocating the next id via `SELECT COALESCE(MAX(id), 0) + 1 FOR UPDATE`
-- inside a transaction. That works under the single-writer assumption
-- but adds a round-trip per insert and breaks if a second writer ever
-- arrives. reciterai_impact.id is already AUTO_INCREMENT — these two
-- tables are the inconsistency.
--
-- Blast radius
-- ------------
-- - reciterai_entities at time of authoring: ~7,100 rows.
-- - reciterai_synopsis at time of authoring: ~9,800 rows.
-- - ALTER TABLE locks each table briefly (small row counts; expect
--   sub-second lock window). The daily enrichment cron is the only
--   regular writer; the operator should still run during a low-activity
--   window to avoid colliding with ad-hoc reads.
-- - Existing rows retain their current IDs. AUTO_INCREMENT starts at
--   MAX(existing_id) + 1 automatically.
-- - No application code changes required to land this migration. The
--   MAX+1 workaround in mariadb_writer.py keeps working post-migration
--   (writers that supply an explicit `id` continue to insert at that id);
--   removal is tracked separately, after this migration is applied and
--   verified in production.
--
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Pre-flight checks (run these first; un-comment to execute)
-- ---------------------------------------------------------------------

-- Confirm the columns are currently NOT AUTO_INCREMENT:
--   SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, EXTRA, COLUMN_DEFAULT
--   FROM INFORMATION_SCHEMA.COLUMNS
--   WHERE TABLE_SCHEMA = DATABASE()
--     AND ((TABLE_NAME = 'reciterai_entities' AND COLUMN_NAME = 'entity_id')
--       OR (TABLE_NAME = 'reciterai_synopsis'  AND COLUMN_NAME = 'id'));
--
-- Expected: EXTRA is empty (no `auto_increment`); COLUMN_DEFAULT = '0'.

-- Snapshot row counts and max-id so post-flight can verify nothing was lost:
--   SELECT 'reciterai_entities' AS tbl, COUNT(*) AS row_count, MAX(entity_id) AS max_id
--     FROM reciterai_entities
--   UNION ALL
--   SELECT 'reciterai_synopsis',         COUNT(*),             MAX(id)
--     FROM reciterai_synopsis;

-- Confirm no row currently has the placeholder PK=0 (would survive the
-- ALTER but break uniqueness on the next AUTO_INCREMENT insert):
--   SELECT 'reciterai_entities.entity_id=0' AS check_name, COUNT(*) AS hits
--     FROM reciterai_entities WHERE entity_id = 0
--   UNION ALL
--   SELECT 'reciterai_synopsis.id=0',                       COUNT(*)
--     FROM reciterai_synopsis WHERE id = 0;
--
-- Expected: hits = 0 for both. If non-zero, STOP and triage before
-- continuing — those rows must be re-keyed first or the ALTER will
-- collide on the next insert.

-- ---------------------------------------------------------------------
-- 2. Forward migration
-- ---------------------------------------------------------------------

ALTER TABLE reciterai_entities
    MODIFY COLUMN entity_id BIGINT(20) NOT NULL AUTO_INCREMENT;

ALTER TABLE reciterai_synopsis
    MODIFY COLUMN id INT(11) NOT NULL AUTO_INCREMENT;

-- ---------------------------------------------------------------------
-- 3. Post-flight verification
-- ---------------------------------------------------------------------

-- Confirm EXTRA now contains `auto_increment`:
--   SELECT TABLE_NAME, COLUMN_NAME, EXTRA
--   FROM INFORMATION_SCHEMA.COLUMNS
--   WHERE TABLE_SCHEMA = DATABASE()
--     AND ((TABLE_NAME = 'reciterai_entities' AND COLUMN_NAME = 'entity_id')
--       OR (TABLE_NAME = 'reciterai_synopsis'  AND COLUMN_NAME = 'id'));
--
-- Expected: EXTRA = 'auto_increment' for both rows.

-- Confirm row counts unchanged from the pre-flight snapshot:
--   SELECT 'reciterai_entities' AS tbl, COUNT(*) AS row_count, MAX(entity_id) AS max_id
--     FROM reciterai_entities
--   UNION ALL
--   SELECT 'reciterai_synopsis',         COUNT(*),             MAX(id)
--     FROM reciterai_synopsis;

-- Confirm AUTO_INCREMENT seeded past the existing max:
--   SELECT TABLE_NAME, AUTO_INCREMENT
--   FROM INFORMATION_SCHEMA.TABLES
--   WHERE TABLE_SCHEMA = DATABASE()
--     AND TABLE_NAME IN ('reciterai_entities', 'reciterai_synopsis');
--
-- Expected: AUTO_INCREMENT > the corresponding MAX(id) from above.

-- ---------------------------------------------------------------------
-- ROLLBACK (commented out; copy out and run if needed)
-- ---------------------------------------------------------------------
--
-- ALTER TABLE reciterai_entities
--     MODIFY COLUMN entity_id BIGINT(20) NOT NULL DEFAULT 0;
--
-- ALTER TABLE reciterai_synopsis
--     MODIFY COLUMN id INT(11) NOT NULL DEFAULT 0;
--
-- Note: existing IDs retain their values. New inserts after rollback
-- must again supply an explicit id (the mariadb_writer.py MAX+1
-- allocation does this).
