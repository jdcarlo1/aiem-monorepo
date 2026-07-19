import os, psycopg2

DB = os.environ["DATABASE_URL"]
conn = psycopg2.connect(DB, connect_timeout=8, options="-c statement_timeout=15000")
cur = conn.cursor()

# DDL
cur.execute("""
    CREATE TABLE IF NOT EXISTS oe_known_synthetic_rows (
        decision_id   TEXT        PRIMARY KEY
                                  REFERENCES oe_decision_audit(decision_id),
        reason        TEXT        NOT NULL,
        registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
""")
print("DDL: CREATE TABLE IF NOT EXISTS oe_known_synthetic_rows (decision_id PK FK, reason TEXT, registered_at TIMESTAMPTZ DEFAULT now())")

# INSERT
cur.execute("""
    INSERT INTO oe_known_synthetic_rows (decision_id, reason)
    VALUES
        ('972f0ffe6ef24613b5532893', 'trigger-test row, C06-C08 verifier'),
        ('1f436a10f1024b5bb5fa2bb9', 'trigger-test row, C06-C08 verifier')
    ON CONFLICT (decision_id) DO NOTHING
    RETURNING decision_id, reason, registered_at
""")
rows = cur.fetchall()
print(f"INSERT RETURNING: {len(rows)} row(s)")
for r in rows:
    print(f"  {r[0]} | {r[1]} | {r[2]}")

conn.commit()

# SELECT *
cur.execute("SELECT decision_id, reason, registered_at FROM oe_known_synthetic_rows ORDER BY registered_at")
all_rows = cur.fetchall()
print(f"\nSELECT * FROM oe_known_synthetic_rows  ({len(all_rows)} rows):")
for r in all_rows:
    print(f"  decision_id={r[0]}  reason={r[1]!r}  registered_at={r[2]}")

# Confirm oe_decision_audit untouched
cur.execute("""
    SELECT decision_id, verification_status, LEFT(created_at::text,23)
    FROM oe_decision_audit
    WHERE decision_id IN ('972f0ffe6ef24613b5532893','1f436a10f1024b5bb5fa2bb9')
    ORDER BY decision_id
""")
audit_rows = cur.fetchall()
print(f"\noe_decision_audit rows (must be unchanged):")
for r in audit_rows:
    print(f"  decision_id={r[0]}  verification_status={r[1]}  created_at={r[2]}")

conn.close()
