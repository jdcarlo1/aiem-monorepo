-- Migration: Drop aiem_supervisor_signal_health from dev
-- Authorized by Joel (schema drift remediation Group 1A)
-- Reason: Table was created in prod directly by Python app at runtime, never tracked
--         by Replit's migration system. Bootstrap created an empty duplicate in dev.
--         Dropping from dev stops Replit from generating a CREATE that hard-blocks
--         deploy (table already exists in prod with 5 rows, untouched).
-- Dev rows at time of drop: 0
-- Prod rows: 5 (unaffected)
DROP TABLE IF EXISTS aiem_supervisor_signal_health;
