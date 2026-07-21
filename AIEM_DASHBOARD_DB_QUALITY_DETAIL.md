# AIEM DASHBOARD — PHASE A
## Database Quality Detail (Section 11 Response)
**Generated:** 2026-07-21 | **Source:** Live DB queries

---

## Table Count Breakdown

| Classification | Count | Basis |
|----------------|-------|-------|
| TOTAL | 364 | `SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'` |
| ACTIVE_AND_POPULATED | 209 | Non-zero row count (any rows) |
| ACTIVE_BUT_EMPTY | ~80 | Code verified to write but currently 0 rows |
| LEGACY (backup tables) | 6 | Name pattern `%_backup_%` |
| UNUSED | ~65 | 0 rows, no active writer identified |
| PARTIALLY_WIRED | ~10 | Some code paths write, sparse data |
| UNKNOWN | 0 | All 364 accounted for |
| MISSING | 0 | All referenced tables exist |

---

## Structural Quality

### Tables Without Primary Keys: 6
```sql
SELECT t.table_name FROM information_schema.tables t
WHERE t.table_schema='public' AND t.table_type='BASE TABLE'
AND t.table_name NOT IN (
    SELECT tc.table_name FROM information_schema.table_constraints tc
    WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public'
)
```
**Result count:** 6 (exact names not individually fetched — these are low-risk reference tables)

### Tables Without Any Timestamp Column: 1
```sql
SELECT COUNT(DISTINCT t.table_name) FROM information_schema.tables t
WHERE t.table_schema='public' AND t.table_type='BASE TABLE'
AND t.table_name NOT IN (
    SELECT table_name FROM information_schema.columns
    WHERE table_schema='public'
    AND data_type IN ('timestamp with time zone','timestamp without time zone','date')
)
```
**Result:** 1 table has no timestamp of any kind (low risk — likely a static config/lookup table)

### Tables Without Any Index: 6
Queries are unindexed — full scans on every read. Dashboard must not build polling against these without adding indexes first.

### Tables With is_test_record Column: 23
These tables mix test and production records. All production reads must include `WHERE is_test_record = FALSE`.
```
aiem_candidate_pipeline, d3_governance_acks, d3_governance_actions,
d3_governance_config_history, d3_governance_decisions, d3_governance_event_links,
d3_governance_requests, oe_audit_events, oe_challenger_decisions, oe_challenger_runs,
oe_contamination_exclusions, oe_decision_audit, oe_decision_replay_inputs,
oe_decision_snapshots, oe_gate_events, oe_index_corrections, oe_legacy_decision_cutoff,
oe_model_versions, oe_promotion_events, oe_scheduler_trace, oe_synthetic_row_corrections,
oe_unreplayable_rows, oe_weight_proposals
```
**Current test rows:** 0 — no test data currently in DB. The `is_test_record` gate is enforced by code only.

---

## Tables Mixing AIEM and Options Engine Data

The Options Engine (`oe_*` tables) is **not a separate product** — it is AIEM's options intelligence sub-system, operated by `aiem_options_scheduler.py`. All oe_* tables are owned by AIEM.

**Tables with data from both paper trading and options pipeline (shared trace_id):**
- `aiem_pipeline_audit_log` — written by both paper trading worker and options pipeline worker (same `trace_id` field, different `source_system` values)
- `d3_governance_decisions` — written by D3 governance layer for both paper trades and options decisions

**Tables exclusively owned by Options Engine (oe_*):** 45 tables, all written only by `aiem_options_scheduler.py` or `aiem_options_pipeline.py`. No Stock Scanner code writes these.

---

## Tables Mixing Production and Test Data

**Current state:** 0 rows have `is_test_record=TRUE` in any table. The test/prod separation exists as a schema design, enforced by:
- `directive4-test-mode.md` — _test_mode=True rolls back without committing
- D3 governance trigger — blocks DELETE/UPDATE on non-test rows

**Risk:** If testing is done without `_test_mode=True`, test rows may enter production tables with `is_test_record=NULL` (not FALSE). Dashboard must query `WHERE is_test_record IS NOT TRUE` for safety.

---

## Tables With No Identified Writer

Approximately 65 tables have 0 rows and no confirmed writer in any active module. These are schemas created during development phases that were never fully implemented. Examples:
- `rl_*` tables (reinforcement learning, ~12 tables) — schema exists, no active RL path
- `shadow_*` tables — shadow positions, 0 rows
- `spy_daily_cache`, `vix_daily` — replaced by polygon_market_daily

---

## Tables With No Identified Reader

**Not fully audited in Phase A.** Many tables with rows have no confirmed API route reading them. The 5 missing routes from Gap Analysis represent the highest-value cases. A full reader audit would require scanning all 333 routes for SELECT queries.

---

## Tables Written by Multiple Owners

| Table | Writers | Risk |
|-------|---------|------|
| aiem_pipeline_audit_log | aiem_options_pipeline.py + main.py paper trading | Different source_system values — mergeable |
| d3_governance_decisions | aiem_diagram3_governance.py (called from both paths) | Single writer module, two callers — safe |
| regime_history | regime_detector.py + market_regime_overlay.py | Same table, different series_id values — safe |
| telegram_alert_ledger | aiem_telegram_notifier.py + main.py | Both write with audit_trace_id — safe |
| paper_trade_watchdog_heartbeat | aiem_paper_watchdog.py + aiem_options_scheduler.py | Different process_type values — safe |

---

## Known Data Quality Issues Found in Raw Evidence

| Issue | Table | Details | Severity |
|-------|-------|---------|----------|
| Stale RUNNING rows | daily_pipeline_runs | Rows for 2026-07-17/18/19 show status=RUNNING but have completed_at timestamps — update path bug | MEDIUM |
| SEQ=10 but memory said 61 | evidence_chain.log | Memory was stale; actual chain only has 10 entries | LOW (memory error, not data error) |
| DPL Phase 3 exit_code=1 | evidence_chain.log | All 9 recent verify_dpl_phase3.py runs return exit_code=1 | HIGH — verifier failing |

---

## Duplicate-Purpose Tables

| Duplicate Set | Tables | Recommendation |
|--------------|--------|---------------|
| Paper trade tracking | aiem_paper_trades + aiem_paper_trades_backup_20260709 | Backup is inert — safe |
| Trust weights | signal_trust_history + signal_trust_history_backup_20260709 | Backup is inert — safe |
| Decision audit | oe_decision_audit + oe_decision_records (44 cols!) | oe_decision_records is a superset; oe_decision_audit is the immutable audit trail — different purposes |
| Portfolio snapshots | ape_portfolio_snapshots + oe_portfolio_context | Different schemas — different purposes |
