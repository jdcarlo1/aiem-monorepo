# Strict Investigation — Why AIEM Loop Did Not Start (2026-08-06)

**Question:** AIEM is supposed to scan its own stocks via Polygon (not Stock Scanner). Why didn’t the loop go through? Wasn’t this fixed yesterday?

**Verdict:** The loop died at **step 1** of Loop B. Code was fixed **in git** yesterday (PR #19). **Production was still running the old binary** when the 9:07 AM ET cron fired. Fix ≠ deploy.

---

## 1. There are two morning paths (easy to conflate)

| Path | Schedule | Data source | Output table | Feeds Paper Money UI? |
|---|---|---|---|---|
| **Loop B** `aiem_morning_scan` | **9:07 AM ET** | `polygon_rvol_scan` + `conviction_stack_watchlist` + `unusual_calls_log` | `aiem_predictions` | Indirect / not primary |
| **Workstream D** `aiem_independent_scan` | **9:20 AM ET** | **`polygon_market_daily` only** (raw Polygon bars) | `aiem_independent_picks` | **No** — separate ledger |
| Paper Money injector | separate | **PRIMARY = `scanner_ai_trades`** (explicit in code) | `aiem_paper_trades` | **Yes** — what you see in Paper Trades |

So: the “Polygon-only self scan” you describe is **Workstream D**.  
What failed this morning and left `today_predictions: []` is **Loop B**.  
What filled Paper Trades with MSFT/AAPL/NVDA was **`scanner_ai_trades`**, not AIEM’s independent loop.

---

## 2. Exact failure point — beginning of Loop B

```
09:07 ET CronTrigger (aiem_morning_scan)
  → _run_aiem_morning_job()
    → _run_aiem_morning_scan()
      → _morning_thread()
        → FIRST CALL: _aiem_tool_scan_market_for_setups()   ← DIED HERE
           (never reached scoring / save / paper)
```

**Production `job_heartbeats` last_error (2026-08-06 13:07 UTC = 9:07 AM ET):**

```
column "score" does not exist
LINE 2: SELECT ticker, score, confirmed_2d, high_con...
```

That SQL is the **first DB read after Polygon RVOL** inside `_aiem_tool_scan_market_for_setups`.  
Old code queried phantom columns. Neon DDL is:

| Code asked for (old) | Neon actually has |
|---|---|
| `score`, `confirmed_2d`, `high_conviction`, … | `total_pts`, `conviction_pct`, `layers`, `meta` |

Once that SELECT throws → `record_job_failure("aiem_morning_scan", …)` → **loop exits**.  
No candidates → no `_aiem_tool_save_daily_predictions` → `aiem_predictions` stays empty.

**Live confirmation now:**

```json
GET /stock-api/aiem-predictions
→ today_predictions: [], total: 0, next_scan: "Tomorrow 9:05 AM ET"
```

---

## 3. “I thought you fixed this yesterday” — what was actually fixed

| Fact | Value |
|---|---|
| Fix commit | `f20868cf` — *Fix aiem_morning_scan crash: align setup scan with Neon schema* |
| PR | [#19](https://github.com/jdcarlo1/aiem-monorepo/pull/19) merged **2026-08-05 14:03 UTC** |
| In `origin/main`? | **Yes** — uses `total_pts` / `unusual_calls_log` |
| Regression test | `tests/test_aiem_morning_scan_schema.py` |
| **On production VM at 9:07 AM ET today?** | **No** — same `column "score" does not exist` still fired |

**Code fix without Replit Publish/redeploy does not change the running process.**

---

## 4. Why an afternoon restart still didn’t recover the day

Prod process liveness:

```json
boot_ts: 2026-08-06T19:39:17Z   (= 3:39 PM ET)
```

Startup catchup for `aiem_morning_scan` only replays **09:07–12:00 ET**.  
After noon it only writes a **SKIPPED** audit — it does **not** re-run the scan (staleness guard).

So even a 3:39 PM ET restart with new code cannot auto-heal today’s Loop B.

---

## 5. Why Paper Trades still showed “picks” (this is not Loop B)

Paper injector (`_inject_scanner_ai_trades_for_paper`) is coded as **PRIMARY** with priority 100 over conviction / unusual calls / aiem_ai.

Today’s open CALL rows are `signal_source=scanner_ai_trades` (mega-caps).  
That is **Stock Scanner AI Trades → paper**, not AIEM Polygon independent loop → paper.

`aiem_independent_picks` is intentionally **separate** and is **not** wired into the Paper Money book you watch.

---

## 6. Root-cause chain (strict)

1. Loop B starts at 9:07 ET.  
2. First non-trivial query after RVOL hits `conviction_stack_watchlist` with **old column names**.  
3. Postgres raises `column "score" does not exist`.  
4. Job marked failed; predictions not saved.  
5. Downstream loop (grade / closed-loop from morning predictions) has nothing to process.  
6. Git fix exists since Aug 5; **prod binary not published**.  
7. Afternoon restart past catchup window → day stays empty for Loop B.  
8. UI paper activity comes from scanner path → looks “alive” while AIEM Loop B is dead.

---

## 7. Required actions (in order)

1. **Publish / redeploy** stock-api Reserved VM so PR #19 code is the running binary.  
2. Confirm Command Center heartbeat: `aiem_morning_scan.consecutive_failures = 0` and `last_error` clear.  
3. Optional today: `POST /stock-api/admin/run-aiem-morning-scan` (with `ADMIN_TOKEN`) after deploy.  
4. Confirm `GET /stock-api/aiem-predictions` → `today_predictions.length > 0`.  
5. Separately decide product intent: should **Workstream D** (`aiem_independent_picks`) feed Paper Money instead of / in addition to `scanner_ai_trades`? Today it does **not**.

---

## 8. One-line answer

**They didn’t go through the loop because Loop B crashed on the first setup-scan SQL (`score` column) under the old prod binary; yesterday’s fix is in GitHub `main`, not yet the live process.**
