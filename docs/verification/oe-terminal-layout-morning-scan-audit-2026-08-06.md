# Options Engine Terminal + Morning Scan — Audit (2026-08-06)

**Production:** `https://nclexai.org`  
**Surfaces reviewed:** AIEM Command Center (`/aiem/command`) Live Job Heartbeats; Options Engine Terminal (`/oe-dashboard/`)

---

## 1. Why `aiem_morning_scan` failed this morning

| Field | Value |
|---|---|
| Job | `aiem_morning_scan` |
| Last attempt | `2026-08-06 13:07:00 UTC` (= **9:07 AM ET**) |
| Consecutive failures | **5** |
| Last success | `2026-07-30 13:07:00 UTC` |
| `morning_scan_runs` today | `succeeded_count: 0` |

**Exact `last_error` from `job_heartbeats`:**

```
column "score" does not exist
LINE 2:                 SELECT ticker, score, confirmed_2d, high_con...
                                       ^
```

### Root cause

`_aiem_tool_scan_market_for_setups` (called by `_run_aiem_morning_scan`) was still querying **phantom columns** on Neon:

| Code asked for | Neon actually has |
|---|---|
| `score`, `confirmed_2d`, `high_conviction`, … | `total_pts`, `conviction_pct`, `layers`, `meta` |
| `call_sweep_log` | empty / wrong schema — live flow is `unusual_calls_log` |

That aborts Loop B before predictions are saved → zero AIEM morning auto-picks. Watchdog correctly flagged STALE.

### Fix status

- **Code fix already merged** in PR #19 (`f20868cf`, 2026-08-05) — local `main.py` uses `total_pts` / `unusual_calls_log`.
- **Production is still running the pre-fix binary.** This morning’s 9:07 AM ET fire reproduced the old SQL error.
- **Action required after this PR merges:** Replit **Publish / redeploy** the stock-api Reserved VM so the Aug 5 schema fix is live. Optionally `POST /stock-api/admin/run-aiem-morning-scan` (with `ADMIN_TOKEN`) once to clear the heartbeat after deploy.

Regression guard already exists: `artifacts/stock-scanner-api/tests/test_aiem_morning_scan_schema.py`.

---

## 2. Why the screen looked “jammed together”

### AIEM Command Center — Live Job Heartbeats

- Grid was `grid-cols-2 … xl:grid-cols-6 gap-2` with `text-[9px]`/`text-[10px]` and `p-2.5`.
- On phone that is a 2-column wall of truncated names; errors only lived in the hover `title`.
- **Fix in this PR:** 1→4 column responsive grid, larger cards, full job names, failing jobs sorted first, **`last_error` shown in the red banner and on each failing card**.

### Options Engine Terminal (`/oe-dashboard/`)

- No real app shell (`min-h` flex without `shrink-0` / `min-w-0` / max width) → sidebar + dense tables competed for width.
- Positions page forced **two 10-column tables side-by-side** (`grid-cols-2`) — the main “jammed” feel.
- Summary cards used non-responsive `grid-cols-4`.
- Table cells used `p-2` / `px-2`.
- Duplicate `/strategies` route in `App.tsx`.
- Live Decisions used keyless Fragments around table rows.

**Fixes in this PR:** shared `AppShell`, responsive stacking, larger table padding, duplicate route removed, Fragment keys, Strategies spacing / design-token cleanup.

---

## 3. Deploy checklist (single merge)

1. Merge this PR (layout + heartbeat error surfacing).
2. **Publish / redeploy** production so PR #19 morning-scan SQL is actually running.
3. Confirm Command Center no longer shows `column "score" does not exist`.
4. Optional: manual morning-scan trigger once post-deploy; verify `job_heartbeats.aiem_morning_scan.consecutive_failures = 0`.
