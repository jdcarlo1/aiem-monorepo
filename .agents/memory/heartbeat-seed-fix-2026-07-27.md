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

## State as of 2026-07-27

- Secrets added to GH Actions: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (were missing)
- YAML fix committed at fce1144 — single-line python3 invocation, passes yaml.safe_load
- seed_daily_candidates fix: 5 changes, negative-control PASS (TG message_id=3162, DB NO_CANDIDATES)
- verified_run.sh SEQ=150, PSV1-7 PASS, PSV8/9 pre-existing FAIL
- **Item 1.4 PENDING**: real `event=schedule` run proof requires 2026-07-28 10:45+ UTC window
- Test row: daily_pipeline_runs run_date=2099-01-01 trigger_source=neg_ctrl_test — NOT deleted
- Permanent record: docs/verification/heartbeat-and-seedbug-2026-07-27-FINAL.md
