"""
backfill_sha256_returning.py
Prints the exact SQL UPDATE statement, executes it, and prints the full RETURNING result set.
UPDATE-only — no DELETEs.
"""
import os, sys, subprocess
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

from aiem_strat_engine.reporting import _normalize_for_hash, _sha256, _HASH_EXCLUDE_COLS
from aiem_strat_engine.db import get_conn
import psycopg2.extras

with get_conn() as conn:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM ase_performance_reports ORDER BY created_at")
        rows = [dict(r) for r in cur.fetchall()]

values = []
for row in rows:
    rid = row["report_id"]
    reduced = {k: v for k, v in row.items() if k not in _HASH_EXCLUDE_COLS}
    normalized = _normalize_for_hash(reduced)
    correct_sha = _sha256(normalized)
    values.append((rid, correct_sha))

vals_clause = ",\n  ".join(f"('{rid}', '{sha}')" for rid, sha in values)
sql = (
    "UPDATE ase_performance_reports apr\n"
    "SET report_sha256 = vals.sha\n"
    "FROM (VALUES\n"
    f"  {vals_clause}\n"
    ") AS vals(rid, sha)\n"
    "WHERE apr.report_id = vals.rid\n"
    "RETURNING apr.report_id, apr.period_type, apr.period_start, apr.report_sha256;"
)

print("=== SQL STATEMENT ===")
print(sql)
print()
print("=== PSQL RESULT ===")
sys.stdout.flush()

result = subprocess.run(
    ["psql", os.environ["DATABASE_URL"], "-c", sql],
    capture_output=True, text=True
)
print(result.stdout)
if result.stderr.strip():
    print("STDERR:", result.stderr)
sys.exit(result.returncode)
