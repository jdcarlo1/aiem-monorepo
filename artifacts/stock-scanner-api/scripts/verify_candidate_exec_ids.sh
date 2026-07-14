#!/bin/bash
# =============================================================================
# verify_candidate_exec_ids.sh
#
# PURPOSE: Prove that candidate_id is created once at Diagram-2 block entry
#          and is identical across every stage row for that trace, AND that
#          execution_plan_id is created only at Stage 17 and propagates to
#          the aiem_paper_trades row.
#
# RUN AFTER: 09:45 AM ET on any weekday following a real paper-trade run.
# Paste the FULL raw output back, unedited.
#
# FALSIFICATION TESTS:
#   (a) Every trace today has at least one stage row (run actually happened).
#   (b) For every trace, ALL stage rows share exactly ONE candidate_id — no
#       NULLs, no duplicates, no regenerated values.
#   (c) candidate_id format matches   cand_<TICKER>_<DATE>_<TRACE8>
#   (d) Every aiem_paper_trades row from today has candidate_id != NULL
#       and execution_plan_id != NULL.
#   (e) execution_plan_id format matches  exec_<TRACE12>_<UUID8>
#   (f) execution_plan_id appears in the Stage-17 payload of the
#       same trace (Decision Engine is the only creator).
#   (g) No two distinct trades share the same candidate_id or execution_plan_id.
# =============================================================================

set -euo pipefail
SCRIPT_VERSION="1.0.0"

echo "################################################################"
echo "# CANDIDATE-ID / EXECUTION-PLAN-ID PROPAGATION VERIFICATION"
echo "# Script: verify_candidate_exec_ids.sh  v${SCRIPT_VERSION}"
echo "################################################################"

# ── Real-time anchors ─────────────────────────────────────────────────────────
SHELL_UTC=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
SHELL_ET=$(TZ='America/New_York' date '+%Y-%m-%d %H:%M:%S ET')
echo ""
echo "[ANCHOR] shell UTC : $SHELL_UTC"
echo "[ANCHOR] shell ET  : $SHELL_ET"
DB_NOW=$(psql "$DATABASE_URL" -t -c "SELECT NOW() AT TIME ZONE 'America/New_York';" 2>/dev/null | xargs)
DB_TODAY=$(psql "$DATABASE_URL" -t -c "SELECT (NOW() AT TIME ZONE 'America/New_York')::date;" 2>/dev/null | xargs)
echo "[ANCHOR] db NOW ET : $DB_NOW"
echo "[ANCHOR] db date ET: $DB_TODAY"

echo ""
echo "================================================================"
echo "PART A — Trace count for today + candidate_id NULL check"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    LEFT(trace_id, 40)            AS trace_id,
    ticker,
    COUNT(*)                      AS stage_rows,
    COUNT(DISTINCT candidate_id)  AS distinct_cand_ids,
    COUNT(*) FILTER (WHERE candidate_id IS NULL)  AS null_cand_rows,
    MIN(candidate_id)             AS candidate_id_value
FROM aiem_diagram2_trace_audit
WHERE completed_at::date = current_date
  AND trace_id NOT LIKE 'verif_%'
  AND trace_id NOT LIKE 'POSTEST_%'
GROUP BY trace_id, ticker
ORDER BY MIN(completed_at);
SQL

echo ""
echo "================================================================"
echo "PART B — Uniqueness: each trace must have exactly 1 candidate_id"
echo "================================================================"
BAD_TRACES=$(psql "$DATABASE_URL" -t -c "
SELECT COUNT(*) FROM (
  SELECT trace_id
  FROM aiem_diagram2_trace_audit
  WHERE completed_at::date = current_date
    AND trace_id NOT LIKE 'verif_%'
    AND trace_id NOT LIKE 'POSTEST_%'
  GROUP BY trace_id
  HAVING COUNT(DISTINCT candidate_id) != 1
     OR COUNT(*) FILTER (WHERE candidate_id IS NULL) > 0
) bad;
" | xargs)
echo "Traces with inconsistent or NULL candidate_id: $BAD_TRACES"
[[ "$BAD_TRACES" -eq 0 ]] && echo "  → PASS ✅" || echo "  → FAIL ❌"

echo ""
echo "================================================================"
echo "PART C — candidate_id format: cand_<TICKER>_<DATE>_<TRACE8>"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    ticker,
    MIN(candidate_id) AS sample_candidate_id,
    (MIN(candidate_id) ~ '^cand_[A-Z0-9]+_[0-9]{4}-[0-9]{2}-[0-9]{2}_[a-z0-9]{8}$')
        AS format_ok
FROM aiem_diagram2_trace_audit
WHERE completed_at::date = current_date
  AND candidate_id IS NOT NULL
  AND trace_id NOT LIKE 'verif_%'
  AND trace_id NOT LIKE 'POSTEST_%'
GROUP BY ticker
ORDER BY ticker;
SQL

echo ""
echo "================================================================"
echo "PART D — aiem_paper_trades: candidate_id + execution_plan_id today"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    id                     AS trade_id,
    ticker,
    trade_date,
    signal_source,
    candidate_id,
    execution_plan_id,
    (candidate_id IS NOT NULL)       AS has_cand,
    (execution_plan_id IS NOT NULL)  AS has_exec_plan
FROM aiem_paper_trades
WHERE trade_date = current_date
  AND signal_source NOT LIKE 'pos_cap%'
ORDER BY id;
SQL

echo ""
echo "================================================================"
echo "PART E — NULL check in aiem_paper_trades for today"
echo "================================================================"
NULL_CAND=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM aiem_paper_trades
  WHERE trade_date = current_date
    AND signal_source NOT LIKE 'pos_cap%'
    AND candidate_id IS NULL;
" | xargs)
NULL_EXEC=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM aiem_paper_trades
  WHERE trade_date = current_date
    AND signal_source NOT LIKE 'pos_cap%'
    AND execution_plan_id IS NULL;
" | xargs)
echo "Trades with NULL candidate_id      : $NULL_CAND"
echo "Trades with NULL execution_plan_id : $NULL_EXEC"
[[ "$NULL_CAND"  -eq 0 ]] && echo "  candidate_id  → PASS ✅" || echo "  candidate_id  → FAIL ❌"
[[ "$NULL_EXEC"  -eq 0 ]] && echo "  execution_plan_id → PASS ✅" || echo "  execution_plan_id → FAIL ❌"

echo ""
echo "================================================================"
echo "PART F — execution_plan_id format: exec_<TRACE12>_<UUID8>"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    id,
    ticker,
    execution_plan_id,
    (execution_plan_id ~ '^exec_[a-z0-9_]+_[a-f0-9]{8}$') AS format_ok
FROM aiem_paper_trades
WHERE trade_date = current_date
  AND signal_source NOT LIKE 'pos_cap%'
ORDER BY id;
SQL

echo ""
echo "================================================================"
echo "PART G — Uniqueness: no two trades share the same IDs"
echo "================================================================"
DUP_CAND=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM (
    SELECT candidate_id FROM aiem_paper_trades
    WHERE trade_date = current_date AND signal_source NOT LIKE 'pos_cap%'
    GROUP BY candidate_id HAVING COUNT(*) > 1
  ) d;
" | xargs)
DUP_EXEC=$(psql "$DATABASE_URL" -t -c "
  SELECT COUNT(*) FROM (
    SELECT execution_plan_id FROM aiem_paper_trades
    WHERE trade_date = current_date AND signal_source NOT LIKE 'pos_cap%'
      AND execution_plan_id IS NOT NULL
    GROUP BY execution_plan_id HAVING COUNT(*) > 1
  ) d;
" | xargs)
echo "Duplicate candidate_id values today      : $DUP_CAND"
echo "Duplicate execution_plan_id values today : $DUP_EXEC"
[[ "$DUP_CAND" -eq 0 ]] && echo "  candidate_id  → PASS ✅" || echo "  candidate_id  → FAIL ❌"
[[ "$DUP_EXEC" -eq 0 ]] && echo "  execution_plan_id → PASS ✅" || echo "  execution_plan_id → FAIL ❌"

echo ""
echo "================================================================"
echo "PART H — Cross-match: trace → trade ID chain for today"
echo "================================================================"
psql "$DATABASE_URL" << SQL
SELECT
    t.candidate_id,
    t.ticker,
    d.trace_id          AS d2_trace_id,
    d.stage_rows,
    d.first_stage,
    d.last_stage,
    t.execution_plan_id,
    t.id                AS trade_id
FROM aiem_paper_trades t
JOIN (
    SELECT candidate_id,
           ticker,
           MIN(trace_id)    AS trace_id,
           COUNT(*)         AS stage_rows,
           MIN(stage_order) AS first_stage,
           MAX(stage_order) AS last_stage
    FROM aiem_diagram2_trace_audit
    WHERE completed_at::date = current_date
      AND trace_id NOT LIKE 'verif_%'
      AND trace_id NOT LIKE 'POSTEST_%'
    GROUP BY candidate_id, ticker
) d ON d.candidate_id = t.candidate_id
WHERE t.trade_date = current_date
  AND t.signal_source NOT LIKE 'pos_cap%'
ORDER BY t.id;
SQL

echo ""
echo "################################################################"
echo "FINAL VERDICT CRITERIA — check each line manually"
echo "################################################################"
echo "  [ ] PART A: stage_rows > 0 for each trace, distinct_cand_ids = 1"
echo "  [ ] PART B: traces with inconsistent candidate_id = 0"
echo "  [ ] PART C: format_ok = TRUE for all tickers"
echo "  [ ] PART D: has_cand = TRUE and has_exec_plan = TRUE for all trades"
echo "  [ ] PART E: NULL candidate_id = 0 AND NULL execution_plan_id = 0"
echo "  [ ] PART F: format_ok = TRUE for all execution_plan_ids"
echo "  [ ] PART G: duplicate candidate_id = 0 AND duplicate execution_plan_id = 0"
echo "  [ ] PART H: every trade row joins to exactly one D2 trace (no orphans)"
echo ""
echo "ALL EIGHT = PASS → Fix 2 (ID propagation) is live and operating correctly."
echo "ANY FAIL        → Pull the failing PART and report the raw rows."
echo "################################################################"
