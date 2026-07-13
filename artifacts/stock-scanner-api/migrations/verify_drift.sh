#!/usr/bin/env bash
# verify_drift.sh — Full dev vs prod schema drift check
# Run before every publish attempt. Report result to Joel (even if empty).
# Part 2 of Schema Drift Remediation directive, authorized 2026-07-13.
set -euo pipefail

DB_URL="${DATABASE_URL:-}"
if [ -z "$DB_URL" ]; then
  echo "ERROR: DATABASE_URL not set" >&2; exit 1
fi

echo "=== SCHEMA DRIFT CHECK $(date -u +"%Y-%m-%d %H:%M:%S UTC") ==="
echo ""

# ── 1. Tables only in dev (will CREATE in prod) ──────────────────────────────
echo "── Tables only in dev (CREATE candidates) ──"
psql "$DB_URL" -t -A -F'|' -c "
SELECT table_name
FROM information_schema.tables
WHERE table_schema='public' AND table_type='BASE TABLE'
  AND table_name NOT IN (
    SELECT relname FROM pg_stat_user_tables
  )
ORDER BY table_name;" 2>/dev/null || echo "(query failed)"

# ── 2. Column count differences ───────────────────────────────────────────────
echo ""
echo "── Dev column counts per table (compare to prod baseline) ──"
psql "$DB_URL" -c "
SELECT table_name, COUNT(*) AS dev_col_count
FROM information_schema.columns
WHERE table_schema='public'
GROUP BY table_name ORDER BY table_name;" 2>/dev/null | head -100

# ── 3. Missing PKs in dev (high risk) ────────────────────────────────────────
echo ""
echo "── Tables with NO primary key in dev ──"
psql "$DB_URL" -t -A -c "
SELECT t.table_name
FROM information_schema.tables t
LEFT JOIN information_schema.table_constraints tc
  ON t.table_name=tc.table_name AND t.table_schema=tc.table_schema
  AND tc.constraint_type='PRIMARY KEY'
WHERE t.table_schema='public' AND t.table_type='BASE TABLE'
  AND tc.constraint_name IS NULL
ORDER BY t.table_name;" 2>/dev/null

# ── 4. Migration files pending ────────────────────────────────────────────────
echo ""
echo "── Migration artifacts in applied/ ──"
ls -1t artifacts/stock-scanner-api/migrations/applied/ 2>/dev/null || echo "(none)"

echo ""
echo "=== DRIFT CHECK COMPLETE ==="
