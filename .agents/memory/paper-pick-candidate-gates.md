---
name: Paper pick candidate gates
description: Root causes for NO_CANDIDATES / low pick count in _aiem_paper_execute_today; fixes applied July 2026
---

## Rule
All signal sources used in `_aiem_paper_pick_candidates()` must be registered in `d3_strategy_registry` with `approval_status='approved'`, or G3 will ENFORCE-block every candidate from that source.

**Why:** G3 calls `_g3_check_strategy_approval(strategy_version=pick["source"])`. Any source not in the registry returns `UNAPPROVED_STRATEGY:<source>` and is hard-blocked. G3 runs in ENFORCE mode (not SHADOW).

**How to apply:** When adding a new signal source to `_aiem_paper_pick_candidates()`, immediately `INSERT INTO d3_strategy_registry (signal_source, status, total_trades, closed_trades, approval_status, notes) VALUES (...)`.

---

## Sources registered as of 2026-07-20
gap_volume, unusual_calls, aiem_v3_discovery, test_source, sweep,
oi_buildup, layer9_stat, aiem_ai, multi_signal, washout_ignition,
squeeze_reversion, gap_down_distribution, fear_premium_gex, conviction_stack

---

## unusual_calls query — DISTINCT ON required
The original query was `ORDER BY prem DESC LIMIT 10` on the raw rows. With SNDK appearing 5× in top-10, only 3-4 unique tickers were generated from 1,076 qualifying rows. Fix: `SELECT DISTINCT ON (ticker) ... ORDER BY ticker, prem DESC LIMIT 20`.

**Why:** `_add()` deduplicates by ticker (keeps highest score), so duplicate rows are wasted slots.

---

## polygon_rvol_scan threshold
`_polygon_full_market_scan()` used `if _rvol < 5.0: continue`. Lowered to 2.0 to capture more gap+volume stocks for the gap_volume signal source. Previous threshold produced only 5-17 rows/day.

---

## try_claim PENDING dead-state (aiem_paper_recovery.py)
`try_claim()` had no handler for `status='PENDING'`. When an admin resets a ledger row to PENDING (to allow re-run), the INSERT fails (row exists) and no UPDATE path matched PENDING → every caller gets CLAIM DENIED indefinitely.

**Fix:** Added "Step 2a-pre" UPDATE WHERE status='PENDING' handler before the stale-CLAIMED handler.

**How to apply:** If admin reset is needed, can now safely `UPDATE paper_trade_job_ledger SET status='PENDING', execution_id=NULL, claimed_at=NULL WHERE business_date=...`. The next watchdog fire will claim it successfully.

**Alternative (simpler):** DELETE the row instead of resetting to PENDING — INSERT path then creates a fresh CLAIMED row.

---

## Test positions blocked portfolio cap
12 test/junk rows (DEDUP_TEST, ZCAP01-09, ZCAPX1, live_verification_test) had status='OPEN', filling the 20-position cap and blocking all new picks. Fixed by:
1. Closing all 12 test rows (status='CLOSED', pnl=0, exit_reason='test_cleanup')
2. Updating `CorrelationGuard._load_open()` to permanently exclude test positions via `is_test_data IS NULL OR is_test_data = FALSE` AND `signal_source NOT IN (...)` filters
