---
name: Heartbeat YAML fix + seed_daily_candidates double-zero fix
description: YAML col-0 indentation kills GH Actions schedule; seed double-zero pattern and fix
---

## Rules

**Rule 1 — YAML block scalars and GH Actions schedule registration:**
Any `run: |` block in a GitHub Actions workflow must have ALL content lines indented ≥ the
established indentation level (first non-empty content line sets it). Python code pasted at
column 0 inside a `run: |` block terminates the YAML block scalar early. Python's yaml.safe_load
raises ScannerError; GitHub's go-yaml may tolerate it silently but schedule registration fails.
Fix: use single-line `python3 -c "..."` or ensure all Python lines are indented to match.

**Rule 2 — double-zero seed pattern:**
Any function that does (1) primary DB query → 0 rows → (2) fallback DB query → 0 rows, with
only `if results: log.info(...)` and NO `else:` branch, is a silent-failure instance.
Fix pattern: `_double_zero = False` init, `else: log.warning(...); _double_zero = True` after
fallback, `elif _double_zero: _tg(...)` Telegram alert, `NO_CANDIDATES` status in run-log table,
`"error": "zero_candidates"` in return dict.

**Why:**
- YAML issue: explains 0 `event=schedule` runs across 3 days despite workflow being "active"
- Double-zero: same shape as _polygon_full_market_scan and Stage 11 bare-except silent failures
  identified this week

**How to apply:**
- Any new GH Actions workflow with a Python heredoc in `run:` → verify yaml.safe_load passes
- Any new DB query function with primary+fallback pattern → add double-zero guard before return
- Force `limit=0` to test double-zero case in standalone scripts (no production data affected)

**Rule 3 — aiem_deletion_guard + same-transaction INSERT+DELETE rollback:**
Any heartbeat/ping writer that does INSERT + DELETE (to keep only last N rows) in ONE transaction
will have BOTH operations rolled back if the DELETE triggers `aiem_deletion_guard`. The `except`
block only logs "non-fatal" — the INSERT is silently lost. Fix: INSERT-only; let rows accumulate
(ts DESC index keeps MAX(ts) queries fast). Never pair a guard-protected DELETE with an INSERT
in the same connection/transaction.

**Why:** aiem_process_heartbeat was dark for 40+ hours despite the process running fine because
every 3-minute write attempt silently rolled back the INSERT along with the blocked DELETE.

**How to apply:** Any new row-keepers (keep-last-N patterns) on a guarded table must use a
separate admin-only delete path or just INSERT without pruning.

## State as of 2026-07-27

- Secrets added to GH Actions: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (were missing)
- YAML fix committed at fce1144 — single-line python3 invocation, passes yaml.safe_load
- seed_daily_candidates fix: 5 changes, negative-control PASS (TG message_id=3162, DB NO_CANDIDATES)
- verified_run.sh SEQ=150, PSV1-7 PASS, PSV8/9 pre-existing FAIL
- **Task #55 CANCELLED** — GH cron proof no longer required; heartbeat bug fixed instead
- Test row: daily_pipeline_runs run_date=2099-01-01 trigger_source=neg_ctrl_test — NOT deleted
- Permanent record: docs/verification/heartbeat-and-seedbug-2026-07-27-FINAL.md
- **Heartbeat deletion-guard fix** committed 7066be3 (aiem_process.py DELETE removed from _heartbeat_writer)
  - sha256 before: 96b7b493  after: ef951509
  - Proof: id=1755 ts=18:58:37 UTC → id=1757 ts=19:01:38 UTC, interval 3:00.1 (on-schedule)
  - Continued proof 19:07/19:10/19:13 UTC — steady 3:00 cadence
- **daily_pipeline_runs deadman (#57)** committed 170eedf (aiem_options_scheduler.py)
  - sha256 before: 727c8585  after: 9d7ff3c6
  - Live proof: zombie id=20 (Jul 19, 9-day zombie) closed at startup 19:14:53 UTC
- **OSS startup catch-up (#58)** committed 170eedf (main.py)
  - sha256 before: d5a41562  after: aa2b296a
  - Live proof: options_structure_scan now has scan_date=2026-07-27, 80 rows written by catch-up
- **Side finding (NOT fixed):** aiem_process_predictions also guarded by aiem_deletion_guard;
  premarket_scan DELETE rejected → 0 predictions for 2026-07-27 14:58 ET; same root cause as heartbeat
