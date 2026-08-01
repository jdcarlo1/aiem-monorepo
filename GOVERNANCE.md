# Repository Governance

## Pre-Commit TLA Gate

Trading-logic files listed in `PROTECTED_PATTERNS` (see `tools/trading_logic_gate.sh`) require a
human-issued Trading-Logic Approval (TLA) record before any `git commit` touching them is allowed
to proceed. The gate runs as `.git/hooks/pre-commit` and exits 1 if no valid, unconsumed approval
is present. Approval records live in `tools/trading_logic_approvals.jsonl`.

**Gate installed:** 2026-07-30T20:34:43 UTC (commit `04c4504e`)

---

## Known Limitation — Replit Auto-Commit Bypasses Pre-Commit Hook

**Accepted as of: 2026-07-31**

### What happens

Replit's workspace auto-commit mechanism commits staged changes directly via Git internals without
invoking shell hooks. As a result, `.git/hooks/pre-commit` — and therefore the TLA gate — does
**not** fire on auto-committed changes.

This means a change to a `PROTECTED_PATTERNS` file that Replit auto-commits bypasses the
approval requirement silently. The commit lands in history with `agent@replit.com` as author and
no consumed TLA record.

### Why it is accepted

Disabling auto-commit would remove continuous workspace checkpointing with no compensating benefit:
the gate cannot be moved earlier in the Replit pipeline, and the risk from auto-committed protected
files is manageable with after-the-fact detection (see Compensating Control below).

### What is still enforced

Manual `git commit` (including all agent commits that stage files and call `git commit` explicitly)
**does** invoke the pre-commit hook normally. The gate blocks those commits in the usual way unless
`TLA_APPROVAL_ID` is set and valid.

### Known post-gate auto-commit bypasses (baseline as of 2026-07-31)

The following commits touched protected files after the gate went live
(2026-07-30T20:34:43 UTC) without a consumed TLA approval record.
They are logged in `autosync_protected_file_log` (table, row `baseline=TRUE`).

| SHA (12) | Timestamp (UTC) | Protected file(s) touched | Commit message |
|---|---|---|---|
| `a9d9863957cc` | 2026-07-31T23:29:42Z | `aiem_options_scheduler.py` | Implement new logic in aiem options scheduler |
| `ddfc9eaff855` | 2026-07-31T22:06:00Z | `main.py` | Implement subprocess execution for discovery cycle |
| `9e50f51a0efe` | 2026-07-31T19:15:37Z | `main.py`, `aiem_options_scheduler.py` | Update scheduler logic and integrate into main entry point |

Pre-gate commits (before 2026-07-30T20:34:43 UTC) are not listed here; the gate did not apply to
them. They are stored as `pre_gate=TRUE` rows in `autosync_protected_file_log` for completeness.

---

## Compensating Control — Post-Commit Autosync Audit

**Script:** `tools/autosync_protected_file_audit.py`
**Schedule:** Daily at 23:30 UTC, run by `aiem_options_scheduler.py`
**Alert channel:** Telegram (same channel as all other system alerts)

The audit script:

1. Reads the git log for commits by `agent@replit.com` since the last audit run that touch any
   file matching `PROTECTED_PATTERNS`.
2. Cross-references each commit against `tools/trading_logic_approvals.jsonl` to determine whether
   a TLA approval was consumed within 120 seconds of the commit timestamp and covering the same
   files. Commits with a matching approval are recorded as `has_tla_approval=TRUE`.
3. Inserts new findings into `autosync_protected_file_log` (deduplicated by `commit_sha`).
4. Sends a Telegram alert for any row where `has_tla_approval=FALSE` and `alerted=FALSE`.

This control is **non-blocking** — it never prevents a commit or reverts code. Its purpose is
visibility only, so that auto-committed protected-file changes are reviewed after the fact.

---

## Protected File Patterns

From `tools/trading_logic_gate.sh` (authoritative source):

```
artifacts/stock-scanner-api/main.py
artifacts/stock-scanner-api/aiem_v3_discovery.py
artifacts/stock-scanner-api/aiem_position_sizing.py
artifacts/stock-scanner-api/aiem_options_*.py
artifacts/stock-scanner-api/aiem_options_pipeline.py
artifacts/stock-scanner-api/aiem_options_scheduler.py
artifacts/stock-scanner-api/aiem_options_dpl.py
artifacts/stock-scanner-api/aiem_strat_engine/scoring.py
artifacts/stock-scanner-api/aiem_strat_scheduler.py
artifacts/stock-scanner-api/aiem_paper_*.py
```
