#!/usr/bin/env python3
"""R8 DB setup: oe_criterion1_exclusions + cutoff trigger comment."""
import psycopg2, os

conn = psycopg2.connect(os.environ['DATABASE_URL'])
conn.autocommit = True
cur = conn.cursor()

# R8.1 — create oe_criterion1_exclusions
cur.execute("""
CREATE TABLE IF NOT EXISTS oe_criterion1_exclusions (
    decision_id   TEXT PRIMARY KEY,
    reason        TEXT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")
print("oe_criterion1_exclusions: created (or already exists)")

cur.execute("""
CREATE OR REPLACE FUNCTION trg_fn_oe_criterion1_exclusions_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'oe_criterion1_exclusions: rows are immutable once inserted (decision_id = %)',
        OLD.decision_id;
END;
$$
""")
cur.execute("DROP TRIGGER IF EXISTS trg_oe_criterion1_exclusions_immutable ON oe_criterion1_exclusions")
cur.execute("""
CREATE TRIGGER trg_oe_criterion1_exclusions_immutable
    BEFORE UPDATE OR DELETE ON oe_criterion1_exclusions
    FOR EACH ROW EXECUTE FUNCTION trg_fn_oe_criterion1_exclusions_immutable()
""")
print("oe_criterion1_exclusions: immutability trigger created")

cur.execute("""
INSERT INTO oe_criterion1_exclusions (decision_id, reason)
VALUES (%s, %s)
ON CONFLICT (decision_id) DO NOTHING
""", (
    '2d03987f38c44c0bbb2daa73',
    'R7.2 cutoff negative control: created 2026-07-19T16:04Z post-wiring-cutoff to prove '
    'trg_oe_known_synthetic_cutoff fires; not a real scheduler decision'
))
cur.execute("SELECT decision_id, reason, registered_at FROM oe_criterion1_exclusions")
rows = cur.fetchall()
print(f"oe_criterion1_exclusions rows ({len(rows)}):")
for r in rows:
    print(f"  {r}")

# R8.7 — update cutoff trigger function body to add commit comment
cur.execute("""
CREATE OR REPLACE FUNCTION trg_fn_oe_known_synthetic_cutoff()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    _created_at TIMESTAMPTZ;
    -- source: git commit d9d6987eeab86cf60cd1b0098be08ecc22b4478d
    -- (aiem_options_scheduler.py DPL Phase 3 wiring, 2026-07-19T15:16:45Z)
    _cutoff     TIMESTAMPTZ := '2026-07-19 15:16:45+00';
BEGIN
    SELECT ri.created_at INTO _created_at
    FROM   oe_decision_replay_inputs ri
    WHERE  ri.decision_id = NEW.decision_id
    AND    ri.is_test_record = FALSE;
    IF FOUND AND _created_at > _cutoff THEN
        RAISE EXCEPTION
            'registration blocked: decision_id=% created_at=% is after scheduler-wiring cutoff (%)',
            NEW.decision_id, _created_at, _cutoff;
    END IF;
    RETURN NEW;
END;
$$
""")
print("trg_fn_oe_known_synthetic_cutoff: updated with commit comment")

# Verify tgenabled for both triggers
cur.execute("""
SELECT tgname, tgenabled
FROM pg_trigger
WHERE tgname IN (
    'trg_oe_known_synthetic_cutoff',
    'trg_oe_known_synthetic_immutable',
    'trg_oe_criterion1_exclusions_immutable'
)
ORDER BY tgname
""")
print("trigger tgenabled status:")
for r in cur.fetchall():
    print(f"  {r}")

# Show updated trigger body
cur.execute("SELECT pg_get_functiondef(oid) FROM pg_proc WHERE proname='trg_fn_oe_known_synthetic_cutoff'")
print("\nUpdated trigger DDL:")
print(cur.fetchone()[0])

conn.close()
print("\nDONE")
