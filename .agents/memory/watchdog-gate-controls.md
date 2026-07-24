---
name: Watchdog gate controls
description: 4 safety gates in /run-scan before threading.Thread(); atomic cap, kill switch, verification, evidence chain; PI-8 live proof deferred
---

## Rule
`_rs_gate_check(run_id, _test_date=None)` runs inside `/run-scan` BEFORE `threading.Thread(...).start()`.
All 4 gates must pass or the request is rejected (fail closed on any DB error).

## Gates
- **G1 Kill switch** — `aiem_watchdog_flags.morning_watchdog_trigger_enabled` must be `'true'`; Joel sets it, watchdog reads only, unreadable = block; returns 429
- **G2 Daily cap** — `MAX_SCAN_TRIGGERS_PER_DAY = 10`; enforced atomically: INSERT default row → SELECT FOR UPDATE → check → UPDATE (all in one transaction); cap count is incremented ONLY on accepted triggers; returns 429
- **G3a RUNNING lease** — `morning_scan_runs WHERE status='RUNNING' AND lease_expires_at > NOW()` > 0 → block; returns 409
- **G3b SUCCEEDED slot** — `morning_scan_runs WHERE status='SUCCEEDED'` > 0 → block; returns 409
- **G4 Evidence chain** — `tools/verified_run_chain.jsonl` must be a readable file; returns 429

## Retry ceiling (traced)
- Watchdog: 39 poll cycles (6:50–10:00 ET at 5-min intervals) × 3 retries = **117 POST attempts max** (only on persistent total failure)
- GH Actions: 32 cron runs × 3 retries = **96 POST attempts max**
- Combined theoretical max without cap: **213 accepted triggers/day**
- Cap (10) allows ≈5 recovery cycles from both sources combined

## DB tables
- `aiem_watchdog_flags` — kill switch; `flag_name='morning_watchdog_trigger_enabled'`, `flag_value TEXT`
- `morning_watchdog_audit` — daily cap counter; `audit_date DATE PRIMARY KEY, triggers_fired INT`; incremented atomically in _rs_gate_check only (NOT in watchdog)
- `aiem_scan_trigger_log` — full audit of every /run-scan call; `action TEXT` ('accepted'/'blocked'), `reason TEXT`, `trigger_count_at_time INT`

## Key design decisions
**Why cap is in /run-scan, not watchdog:** makes enforcement single-point and covers ALL callers (watchdog + GH Actions + admin proxy). Watchdog Gate 2 is advisory pre-check only.
**Why SELECT FOR UPDATE:** prevents two concurrent /run-scan requests from both reading count=9 and both incrementing to 10, bypassing the cap.
**Why `_test_date` param:** allows tests to target a clean future date without touching production morning_scan_runs data.

## Authorized callers (3 only, confirmed by raw grep)
- `main.py:11722` — admin proxy (`/stock-api/admin/aiem-process/run-scan`)
- `aiem_telegram_notifier.py:2420` — morning watchdog loop
- `premarket-backup.yml:129` — GH Actions every-5-min backup

## Verification
- SEQ=115, EXIT=0, 18 PASS 0 FAIL (verified_run.sh sha256=58534be5... matches canonical)
- HTTP-A: kill_switch=false → POST /run-scan → 429, body.reason=kill_switch ✓
- HTTP-B: SUCCEEDED slot exists → POST /run-scan → 409, body.reason=verification_gate:scan_already_succeeded ✓
- HTTP-C: _rs_gate_check(test_date=tomorrow) → allowed=True, trigger_count=1 ✓
- PI-8 (live watchdog poll with kill switch disabled): **deferred to 2026-07-25 AM** — watchdog fires 6:50–10:00 ET only

## How to apply
When adding new callers to /run-scan or when the cap needs adjustment: update `MAX_SCAN_TRIGGERS_PER_DAY` at line 54 of `aiem_process.py`. Do NOT add cap increment logic outside `_rs_gate_check`.
Kill switch toggle: `UPDATE aiem_watchdog_flags SET flag_value='false' WHERE flag_name='morning_watchdog_trigger_enabled';`
