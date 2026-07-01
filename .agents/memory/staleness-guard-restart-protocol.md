---
name: Staleness-guard restart-on-commit protocol
description: How stock-scanner-api's hot-reload watchdog (staleness_guard.py) detects code drift and restarts safely; and the strict per-fix verification protocol used to harden main.py
---

## Staleness guard design (staleness_guard.py + main.py wiring)
- Watchdog runs on a 15s cycle inside the always-on Flask process (`main.py`, port 5050 / workflow "stock-api").
- Watched-file set is discovered **dynamically from `sys.modules`** (filtered to paths under the repo), not a hand-maintained list or glob — this automatically covers every local module actually imported (transitively) by `main.py`, and re-scans each cycle so newly-imported modules join the watch set without a code change. Third-party packages are intentionally excluded.
- Staleness has two independent signals: (1) any watched file's mtime advancing past its first-seen baseline, (2) git SHA drift (`current_git_sha` vs `process_start_git_sha`) as a second check that doesn't depend on file mtimes.
- Before restarting, the process **drains**: a lock-protected in-flight request counter (incremented in `before_request`, decremented in `teardown_request`, via `flask.g` to avoid double-decrement) is polled; new requests get HTTP 503 during the drain window (not queued). Restart proceeds once the counter hits 0, or after a **25s forced-exec timeout** — whichever comes first. The timeout fallback is deliberately fail-closed: dropping one slow request (curl sees exit 52) is preferred over serving stale code indefinitely.
- Restart mechanism is `os.execv` (same PID, replaced process image) — confirmed via `process_start_time` advancing while PID stays constant.
- Health/debug endpoint: `GET /stock-api/process-info` returns `watched_files`, `watched_file_count`, `is_stale`, `draining`, `inflight_request_count`, `current_git_sha` vs `process_start_git_sha`, etc.

**Why:** main.py imports 50+ local modules (aiem_security, scanner, scoring, ml_engine, etc.); editing any of them without a watchdog left the running process silently serving stale code until a manual restart, and the original single-file (main.py-only) watchdog plus instant-kill restart both had real gaps (missed imported-module edits; killed in-flight requests with exit 52).

## Per-fix verification protocol established for main.py hardening work
For any fix applied under this hardening effort, the required deliverable before user sign-off is a **verification package**:
1. `git diff --stat` scoped to exactly the intended files (no incidental changes).
2. Repo-root blast-radius grep for every new symbol/route the fix introduces, to prove no unexpected consumers exist.
3. Live before/after test output for every behavior claimed fixed — real logs/curl exit codes/timings, not inferred behavior. For staleness-guard specifically: (a) edit an imported-but-not-main module and confirm restart fires, (b) a slow request under the drain budget must complete normally (no exit 52), (c) a slow request over the drain budget must hit the forced-timeout warning log and still restart.
4. A unique `VERIFY-FIX-<n>-<hex>` token per fix.
5. One fix per commit; explicit user go-ahead required before finalizing — never bundle multiple fixes into one verification round.

**How to apply:** Reuse this exact package format (diff stat + blast-radius grep + live test transcripts + token) for any future fix to `artifacts/stock-scanner-api/main.py` or its watched modules, since this is the standard the user has held prior fixes to.
