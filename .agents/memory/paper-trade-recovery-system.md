---
name: Paper trade recovery system
description: Architecture and critical rules for the 9:42 AM paper trade exactly-once recovery system built July 2026
---

# Paper Trade Recovery System

## Core architecture
- `aiem_paper_recovery.py` — DB ledger module: `try_claim`, `mark_started`, `mark_completed/skipped/failed`, `start_internal_watchdog`, `log_evidence`
- Two DB tables: `paper_trade_job_ledger` (UNIQUE business_date + UNIQUE execution_id) and `paper_trade_watchdog_heartbeat`
- Durable evidence log at `.local/paper_trade_evidence.log` (JSON lines, not /tmp/)

## try_claim() — three-step dedup sequence
1. INSERT ON CONFLICT DO NOTHING — fresh claim for the day
2. UPDATE WHERE status='CLAIMED' AND claimed_at < NOW()-600s — steal stale CLAIMED (crash before execution started)
3. UPDATE WHERE status='EXECUTING' AND started_at < NOW()-5min AND heartbeat_at IS NULL — steal stale EXECUTING (crash mid-execution)
4. Deny — active row owned by another process

**Why:** A restart kills the running process, leaving EXECUTING with no heartbeat. Without step 3, the ledger stays stuck EXECUTING forever.

## Duplicate execution gate
`COUNT(*) FROM aiem_paper_trades WHERE trade_date = %s >= 1` (changed from >=20)

**Why:** With the ledger as primary dedup, >=20 was dangerously permissive. >=1 means "any pick exists → skip". This correctly handles the case where a prior execution placed picks but the ledger row was missing (e.g., old code pre-migration).

## External watchdog (Protection #5)
Lives as a daemon thread in `aiem_telegram_notifier.py` (separate process from stock-api). Polls DB ledger every 2 min after 9:46 AM ET. POSTs to `/stock-api/admin/run-paper-today` if status is not terminal. Uses `psycopg2.connect(DATABASE_URL)` directly — `_conn` in notifier is a context-manager variable, not a callable function.

**How to apply:** Any new watchdog in aiem_telegram_notifier.py must use `psycopg2.connect(DATABASE_URL, connect_timeout=5)` not `_conn()`.

## Forward-reference lambda pattern (main.py)
Functions referenced in lambdas captured at startup time (line ~7635) but defined later in main.py (line ~16853) will raise NameError.

**Fix:** `lambda d: globals().get('_is_trading_day', lambda x: True)(d)` — defers symbol lookup to call time.

**Why:** Python evaluates `_is_trading_day` when the enclosing scope runs sequentially (line 7643). If the function is defined at line 16853, it doesn't exist yet. The globals().get() call is deferred into the lambda body, which runs later when the module is fully loaded.

## Live proof (July 15, 2026)
VM restart at 9:42 AM ET caused missed execution. Internal watchdog fired at 10:24 ET, detected stale EXECUTING (10 min old), stole it via step 3, hit >=1 picks check, marked SKIPPED. Full chain in `.local/paper_trade_evidence.log`. Verification: `tools/verify_paper_recovery.sh` — PASS, 0 failures.
