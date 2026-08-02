---
name: Autosync TLA gate bypass — accepted limitation + compensating control
description: Replit auto-commit bypasses pre-commit hook; daily audit script detects and alerts on unprotected commits.
---

## Rule
Replit auto-commit does NOT invoke `.git/hooks/pre-commit` → TLA gate silently bypassed for auto-committed protected files. Manual `git commit` still enforces the gate normally.

**Accepted as of:** 2026-07-31

## Compensating control
- Script: `tools/autosync_protected_file_audit.py`
- Schedule: `aiem_options_scheduler.py` CronTrigger hour=23 minute=30 UTC, id=`autosync_protected_file_audit`
- DB table: `autosync_protected_file_log` (commit_sha UNIQUE, has_tla_approval, pre_gate, baseline, alerted)
- Telegram alert sent for any post-gate commit with `has_tla_approval=FALSE`
- Baseline populated 2026-07-31: 26 rows (10 PRE-GATE, 13 TLA-OK, 3 NO-TLA alerted)

## Detection logic
Cross-reference `tools/trading_logic_approvals.jsonl` (used=True records) by timestamp proximity (±120s) and file overlap. No match → `has_tla_approval=FALSE`.

## Gate install date
`2026-07-30T20:34:43Z` commit `04c4504e` — commits before this are `pre_gate=TRUE`, not violations.

**Why:** The script must use `--since=48h ago` (not just 24h) to cover a missed daily run without double-inserting (ON CONFLICT DO NOTHING handles dedup).
