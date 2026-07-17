"""
backfill_report_sha256.py — Backfill correct SHA-256 for all ase_performance_reports rows.

Root cause: pre-existing rows were written by an older version of generate_report
that had a simpler INSERT (missing many JSONB/numeric columns → stored as NULL).
The stored SHA-256 was computed over the original full report_data dict (with those
fields populated), but verify_report_integrity re-hashes the DB row (which has NULLs).
Fix: re-compute each row's hash from its current DB state using the same
_normalize_for_hash + _sha256 path that verify_report_integrity uses, then
UPDATE report_sha256 to match.

This does NOT delete any rows. It only UPDATEs the report_sha256 column.
"""
import os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

import psycopg2, psycopg2.extras
from aiem_strat_engine.reporting import _normalize_for_hash, _sha256, _HASH_EXCLUDE_COLS
from aiem_strat_engine.db import get_conn

DB_URL = os.environ["DATABASE_URL"]

def backfill():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ase_performance_reports ORDER BY created_at")
            rows = [dict(r) for r in cur.fetchall()]

    updated = 0
    skipped = 0
    errors  = 0

    with psycopg2.connect(DB_URL, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            for row in rows:
                report_id   = row["report_id"]
                stored_sha  = row["report_sha256"]
                reduced     = {k: v for k, v in row.items() if k not in _HASH_EXCLUDE_COLS}
                normalized  = _normalize_for_hash(reduced)
                correct_sha = _sha256(normalized)

                if stored_sha == correct_sha:
                    print(f"  SKIP  {report_id[:40]}  (already correct)")
                    skipped += 1
                    continue

                try:
                    cur.execute(
                        "UPDATE ase_performance_reports SET report_sha256=%s WHERE report_id=%s",
                        (correct_sha, report_id)
                    )
                    print(f"  UPD   {report_id[:40]}")
                    print(f"        old={stored_sha[:20]}...")
                    print(f"        new={correct_sha[:20]}...")
                    updated += 1
                except Exception as exc:
                    print(f"  ERR   {report_id}: {exc}")
                    errors += 1

        conn.commit()

    print(f"\nDone: updated={updated}  skipped={skipped}  errors={errors}")
    return errors == 0

if __name__ == "__main__":
    ok = backfill()
    sys.exit(0 if ok else 1)
