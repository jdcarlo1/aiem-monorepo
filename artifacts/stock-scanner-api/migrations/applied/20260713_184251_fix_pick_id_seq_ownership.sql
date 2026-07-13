-- Fix: ai_short_calls_log_pick_id_seq orphaned ownership in dev
-- Root cause: Group 2 migration used CREATE SEQUENCE + ADD COLUMN DEFAULT nextval()
-- which does NOT create the pg_depend OWNED BY relationship.
-- Prod had this sequence via BIGSERIAL (which auto-creates OWNED BY).
-- Replit's migration system detected the structural mismatch and generated
-- CREATE SEQUENCE (without IF NOT EXISTS) → failed because sequence already exists in prod.
-- Fix: set OWNED BY to match prod's BIGSERIAL structure exactly.
--
-- Dev-only DDL. No prod changes. Authorized under schema remediation directive.

ALTER SEQUENCE ai_short_calls_log_pick_id_seq OWNED BY ai_short_calls_log.pick_id;
