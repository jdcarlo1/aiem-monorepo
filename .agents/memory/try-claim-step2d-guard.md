---
name: try_claim Step 2d recovery guard
description: aiem_paper_recovery.py try_claim now has a Step 2d that prevents recovery triggers from claiming a ledger row that scheduled_942 already completed with real trades.
---

## The rule
Recovery triggers (`startup_recovery`, `internal_watchdog`, `external_watchdog`, `admin`) must not steal a `paper_trade_job_ledger` row where `trigger_source='scheduled_942'` AND `status IN ('COMPLETED','SKIPPED')` AND `picks_count > 0`.

**Why:** After a VM restart late in the trading day, `paper_startup_reconciler` runs at T+45s and may find the ledger PENDING (if the restart happened before 9:42) or SKIPPED (if 9:42 already ran with 0 picks).  Without this guard there was no explicit protection preventing a recovery trigger from re-executing and polluting the ledger's `trigger_source` attribution.

**Existing behavior preserved:**
- Steps 2a/2b (steal stale CLAIMED/EXECUTING) still fire for crash-recovery — these are always legitimate.
- Step 2c (scheduled_942 overrides a zero-picks SKIPPED/COMPLETED row) still works as designed.
- Step 2d only blocks if picks_count > 0, ensuring a real successful run is never overwritten.

## How to apply
- Confirmed 4/4 PASS in live negative-control test (test date 1900-01-01, cleaned up).
- sha256 of aiem_paper_recovery.py before=`b94944a4…` after=`e0cbb7ee…`.
- Any new recovery-class trigger source must be added to `_RECOVERY_TRIGGERS` set in Step 2d.
