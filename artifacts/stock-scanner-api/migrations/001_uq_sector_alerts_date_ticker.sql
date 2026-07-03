-- Migration 001: Add UNIQUE constraint on aiem_sector_alerts_log (date, sector_ticker)
-- Applied live 2026-07-03 via psycopg2; this file makes the DDL replayable.
--
-- Idempotent: safe to run against a schema that already has the constraint.
-- Run order: after the CREATE TABLE for aiem_sector_alerts_log.

DO $$
BEGIN
    -- Remove exact duplicate rows (same date + sector_ticker), keeping the
    -- lowest id in each group.  No-op if no duplicates exist.
    DELETE FROM aiem_sector_alerts_log
    WHERE id NOT IN (
        SELECT MIN(id)
        FROM aiem_sector_alerts_log
        GROUP BY date, sector_ticker
    );

    -- Add UNIQUE constraint only if it does not already exist.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name      = 'aiem_sector_alerts_log'
          AND constraint_name = 'uq_sector_alerts_date_ticker'
          AND constraint_type = 'UNIQUE'
    ) THEN
        ALTER TABLE aiem_sector_alerts_log
            ADD CONSTRAINT uq_sector_alerts_date_ticker
            UNIQUE (date, sector_ticker);
        RAISE NOTICE 'uq_sector_alerts_date_ticker created';
    ELSE
        RAISE NOTICE 'uq_sector_alerts_date_ticker already exists — skipped';
    END IF;
END $$;
