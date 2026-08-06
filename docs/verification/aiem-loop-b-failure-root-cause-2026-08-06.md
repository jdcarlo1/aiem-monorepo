# Strict Investigation — Why AIEM Loop Did Not Start (2026-08-06)

**Question:** AIEM is supposed to scan its own stocks via Polygon (not Stock Scanner). Why didn’t the loop go through? Wasn’t this fixed yesterday?

**Verdict:** The loop died at **step 1** of Loop B. Code was fixed **in git** yesterday (PR #19). **Production was still running the old binary** when the 9:07 AM ET cron fired. Fix ≠ deploy.

---

## Fix status for tomorrow (2026-08-07)

| Layer | Status |
|---|---|
| Schema fix (`total_pts` / `unusual_calls_log`) | In `main` since PR #19 (Aug 5) |
| Source isolation (one bad SQL ≠ kill loop) | **Added 2026-08-06** in this PR |
| Polygon-only fallback if blended sources empty | **Added 2026-08-06** |
| Catchup window if redeploy mid-day | Extended to **16:00 ET** (was noon) |
| **Production Publish/redeploy** | **REQUIRED** — without this, tomorrow still dies on old binary |

**Action:** Merge this PR → Replit **Publish** stock-api VM **before 9:07 AM ET Friday**.  
If publish lands after 9:07 but before 16:00 ET, startup catchup will auto-run the scan.

---

## 1. There are two morning paths (easy to conflate)

| Path | Schedule | Data source | Output table | Feeds Paper Money UI? |
|---|---|---|---|---|
| **Loop B** `aiem_morning_scan` | **9:07 AM ET** | `polygon_rvol_scan` + `conviction_stack_watchlist` + `unusual_calls_log` (+ Polygon fallback) | `aiem_predictions` | Indirect / not primary |
| **Workstream D** `aiem_independent_scan` | **9:20 AM ET** | **`polygon_market_daily` only** (raw Polygon bars) | `aiem_independent_picks` | **No** — separate ledger |
| Paper Money injector | separate | **PRIMARY = `scanner_ai_trades`** (explicit in code) | `aiem_paper_trades` | **Yes** — what you see in Paper Trades |

---

## 2. Exact failure point — beginning of Loop B

```
09:07 ET CronTrigger (aiem_morning_scan)
  → _run_aiem_morning_scan()
    → FIRST CALL: _aiem_tool_scan_market_for_setups()   ← DIED HERE
```

Production error at 9:07 AM ET:

```
column "score" does not exist
```

Old binary queried phantom columns. Neon has `total_pts`, not `score`.

Live confirmation: `GET /stock-api/aiem-predictions` → `today_predictions: []`.

---

## 3. Required actions

1. Merge this PR  
2. **Publish / redeploy** stock-api **before Friday 9:07 AM ET**  
3. Confirm heartbeat `aiem_morning_scan` green + predictions non-empty  
4. Optional admin trigger once post-deploy  

**Without Publish, tomorrow fails the same way.**
