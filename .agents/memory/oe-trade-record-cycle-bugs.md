---
name: Options Engine trade-record cycle bugs
description: Two bugs that prevented oe_trade_records from ever populating; datetime fix pattern
---

## Bug 1 — update_trade_record_exit timezone mismatch
`datetime.utcnow()` (naive) subtracted from `entry_ts` fetched as TIMESTAMPTZ (aware) → TypeError.
**Why:** PostgreSQL TIMESTAMPTZ columns come back as offset-aware in psycopg2; `datetime.utcnow()` is naive.
**Fix applied:** `_ets_naive = entry_ts.replace(tzinfo=None) if entry_ts.tzinfo else entry_ts`
**How to apply:** Any code doing arithmetic with TIMESTAMPTZ values and `datetime.utcnow()` needs this pattern.

## Bug 2 — _p2_ready silent suppression
`bootstrap_phase2()` failures inside `_execute_job` are caught and logged only at `log.debug` level; if it fails,
`capture_trade_record()` is never called. This was the primary reason for 0 rows in oe_trade_records.
**Why:** Non-fatal guard was overly silent — debug level hides operational failures.
**How to apply:** Phase init failures in scheduler job functions should use `log.warning`, not `log.debug`, to stay visible.

## Task 2: realized_pnl net-of-costs
`update_trade_record_exit()` at aiem_options_phase2.py now deducts `fees_est + slippage_est` from `pnl_abs`.
SELECT extended: `SELECT entry_price, entry_ts, fees_est, slippage_est FROM oe_trade_records`.

## Backfill
25 oe_trade_records inserted (was 0) for all existing aiem_options_alerts via capture_trade_record().
2 rows closed (PSX alert_id=1,2): exit_ts set, realized_pnl=-13.43 (net), fill_quality=MARKET_ON_EXPIRY.
Evidence sealed at SEQ=27 (entry_hash=a5b93cbb46a6714f49cc39db3dd66100f887b1ce4cd8b4753015eede6ca578d8).
