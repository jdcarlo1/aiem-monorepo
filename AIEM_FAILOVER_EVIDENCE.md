# AIEM Options Pipeline — Failover Evidence Package
**Generated:** 2026-07-17T19:10 UTC (3:10 PM ET, Friday)  
**Directive reference:** "Prove Real Failover Recovery — Not Just the Idempotent Path"

---

## EXPLICIT GAPS — Stated First, Not Buried

The following items from the directive **cannot be produced today**:

| Item | Reason |
|---|---|
| Automated GitHub Actions run log (schedule trigger, not workflow_dispatch) | Recovery window closed at 19:00 UTC (3:00 PM ET). Now 19:10 UTC. |
| Polygon data pull log from an automated recovery run | Requires the above GH Actions run |
| Raw SQL showing PENDING → DONE from an automated recovery trigger | Same dependency |
| Full Diagram 1 → Diagram 2 → paper trade cycle under real failover | Same dependency |

**Why today specifically failed:**
- The watchdog recovery window is `13:55–19:00 UTC` (9:55 AM–3:00 PM ET)
- It is now 19:10 UTC — 10 minutes past the window
- The cron still fires every minute but exits immediately (`"Outside recovery window"`)
- Tomorrow is **Saturday** — the cron runs only `Mon–Fri` (`* 13-19 * * 1-5`)
- The gh auth token from the earlier session expired, preventing a window extension upload

**Next opportunity:** Monday 2026-07-21, 9:50 AM ET — morning-backup fires automatically.  
The Monday execution plan is in Section 4.

---

## SECTION 1 — What IS Proven: File Changes

### 1a. sha256sum of all changed files

```
611ac97d1871635ad2d79fce2c3e358a707bed479fb0262ba20bd2a97588fd68  artifacts/stock-scanner-api/main.py
604481a94f1628d1c9e65aa8a01fdbc89d19cd73adaec6ecf432f14beb0c2ebc  artifacts/stock-scanner-api/aiem_backup_runner.py
4413176d21e8265cef519b9ff99d6f94320a488d520ede638d89c434998225c0  .github/workflows/market-hours-watchdog.yml
b4efd24341d6f443f7d53d6d6312e245cdb167a660bdcffe5b280170adc95071  .github/workflows/morning-backup.yml
4f90f7cf88aefd9081b3a591873c3a21c7abbc97e923406fcc54066e48bb33a8  verified_run.sh
e634d06f5c7f54a15501e55b466c56d6f8a561c470cac92d95898c4516052283  verify_chain.sh
```

To verify live: `sha256sum artifacts/stock-scanner-api/main.py` — must match the first hash.

### 1b. Git commit record

```
commit 7439911e18ccd5e258e91ced446d87c89c3d741a
Date:   Fri Jul 17 18:21:29 2026 +0000

    Update backup runner and GitHub actions for automated pipeline recovery

 .github/workflows/market-hours-watchdog.yml        | 161 lines changed
 .github/workflows/morning-backup.yml               |  76 lines changed
 artifacts/stock-scanner-api/aiem_backup_runner.py  |  35 lines changed
 artifacts/stock-scanner-api/main.py                | 112 lines added
```

---

## SECTION 2 — What IS Proven: Line Numbers (grep -n output)

All line numbers backed by raw `grep -n` output run at 19:08 UTC today.

### main.py — new route decorators
```
$ grep -n '@app.route.*pipeline-checkpoint\|@app.route.*emergency-run' artifacts/stock-scanner-api/main.py

22548:@app.route("/stock-api/admin/pipeline-checkpoint", methods=["GET"])
22579:@app.route("/stock-api/admin/emergency-run", methods=["POST"])
```

### main.py — function definitions and rate-limit state
```
$ grep -n 'def admin_pipeline_checkpoint\|def admin_emergency_run\|_emergency_run_calls = \[\]\|_emergency_run_lock' artifacts/stock-scanner-api/main.py

22544:_emergency_run_calls = []
22545:_emergency_run_lock  = _er_threading.Lock()
22549:def admin_pipeline_checkpoint():
22580:def admin_emergency_run():
22618:    with _emergency_run_lock:
```

### main.py — hmac.compare_digest (security critical)
```
$ grep -n 'hmac.compare_digest' artifacts/stock-scanner-api/main.py | grep 22[0-9][0-9][0-9]:

22600:    if not got or not want or not hmac.compare_digest(got, want):
```

### Dead zone boundary — insertion is at line 22548, dead zone starts at 31796
```
$ grep -n '^@app.route' artifacts/stock-scanner-api/main.py | awk -F: '$1>22500 && $1<32000'

22548:@app.route("/stock-api/admin/pipeline-checkpoint", methods=["GET"])
22579:@app.route("/stock-api/admin/emergency-run", methods=["POST"])
31796:@app.route("/stock-api/admin/backtest-candlestick-confluence", methods=["POST"])
```

The gap between line 22579 (last new endpoint) and line 31796 (first route after dead zone) confirms all new code landed safely before the dead zone. No route registration was silently dropped.

### aiem_backup_runner.py — pre-check fix
```
$ grep -n 'WHERE trade_date = %s AND ticker = %s' artifacts/stock-scanner-api/aiem_backup_runner.py

502:                    WHERE trade_date = %s AND ticker = %s
```

The previous query included `AND signal_source = %s`. Signal source is now absent — matching the DB unique constraint on `(ticker, trade_date)`.

### aiem_backup_runner.py — rollback fix
```
$ grep -n 'conn.rollback' artifacts/stock-scanner-api/aiem_backup_runner.py

551:            conn.rollback()
```

---

## SECTION 3 — What IS Proven: Live HTTP Test Results

All four tests run at 18:19–18:51 UTC against the live Replit VM.

### Test 1 — Wrong token → 403
```
POST /stock-api/admin/emergency-run
X-Admin-Token: bad_token

HTTP 403
{"error": "unauthorized"}
```

### Test 2 — Stale timestamp → 400
```
POST /stock-api/admin/emergency-run
X-Admin-Token: <valid>
body: {"ts": 1}

HTTP 400
{"error": "timestamp out of range", "skew_s": 1784314302.1}
```

### Test 3 — Valid request, idempotent (all jobs already DONE)
```
POST /stock-api/admin/emergency-run
X-Admin-Token: <valid>
body: {"ts": <current epoch>}

HTTP 200
{
  "exit_code": 0,
  "status": "COMPLETED",
  "summary": "[backup] PRIMARY ALREADY RAN — exiting cleanly (no duplicate)",
  "log_tail": [
    "[backup] AIEM backup runner starting  trigger=emergency_run_endpoint",
    "[bootstrap] schema ready",
    "[dedup] 5 jobs all DONE — primary already ran",
    "[backup] PRIMARY ALREADY RAN — exiting cleanly (no duplicate)"
  ]
}
```

**What Test 3 proves:** The endpoint is live, the auth check passes, and the backup runner's dedup logic prevents double-execution. **What it does not prove:** the full Polygon → score → paper trade cycle under a genuine failover condition.

### Test 4 — Pipeline checkpoint
```
GET /stock-api/admin/pipeline-checkpoint

HTTP 200
{
  "date": "2026-07-17",
  "done": 5,
  "needs_recovery": false,
  "pending": 0,
  "pipeline_run": {"status": "COMPLETED", "trigger_source": "emergency_run_endpoint"}
}
```

---

## SECTION 4 — Monday Execution Plan (Real Failover Test)

**Date:** Monday 2026-07-21  
**Target workflow:** `morning-backup.yml` (fires at 9:50 AM ET = 13:50 UTC on schedule)

### Why morning-backup and not the watchdog

The morning-backup fires at a fixed time (9:50 AM ET), giving a predictable window. The watchdog fires every minute but exits outside 9:55–3:00 PM ET. Since both are schedule-triggered (not workflow_dispatch), either satisfies the directive's "automated path" requirement.

### What needs to happen Sunday night / Monday morning

**Sunday night (any time before 9:30 AM ET Monday):**  
No action needed. The DB currently has no `options_pipeline_jobs` rows for 2026-07-21 — those won't be created until the primary pipeline seeds them at 9:40 AM ET Monday.

**Monday 9:40–9:48 AM ET (primary pipeline window):**  
The options_pipeline_scheduler seeds rows in `options_pipeline_jobs` for 2026-07-21. If the primary runs successfully, all rows will be `DONE` by ~9:45 AM.

**Monday 9:48 AM ET (8-minute setup window):**  
To create a genuine `needs_recovery=true` condition before the 9:50 AM backup fires:
```sql
-- Run this at 9:48 AM ET on Monday (2 minutes before backup fires)
UPDATE options_pipeline_jobs
SET status='PENDING', completed_at=NULL
WHERE scan_date='2026-07-21';

DELETE FROM daily_pipeline_runs
WHERE run_date='2026-07-21';
```

Also delete any paper trades written by the primary so the backup runner writes fresh ones:
```sql
DELETE FROM aiem_paper_trades
WHERE trade_date='2026-07-21'
  AND signal_source NOT IN ('backup_runner','emergency_run_endpoint');
```

**Monday 9:50 AM ET — automated trigger fires:**  
`morning-backup.yml` cron `50 13 * * 1-5` fires on GitHub. No manual dispatch. The workflow calls `POST /stock-api/admin/emergency-run`. The backup runner:
1. Finds jobs PENDING → proceeds with full run
2. Pulls Polygon grouped-daily data for 2026-07-21
3. Joins against `options_structure_scan`
4. Scores candidates, writes paper trade(s)
5. Marks all jobs DONE
6. Returns `{"status":"COMPLETED","exit_code":0}`

**Evidence to capture immediately after (run verified_run.sh):**

```bash
# 1. Run the evidence capture script
bash verified_run.sh

# 2. The script will auto-detect the latest GH Actions schedule run and include its ID
# 3. Verify the chain
bash verify_chain.sh verified_evidence_2026-07-21_*.json

# 4. Run the SQL queries manually and paste output into the evidence file:
# options_pipeline_jobs: before (PENDING) and after (DONE)
# aiem_paper_trades: rows written during the backup run
# daily_pipeline_runs: trigger_source='emergency_run_endpoint'
```

**What the GitHub Actions log must show** to satisfy the directive:
- `event: schedule` (not `workflow_dispatch`)
- Step: `"Checking pipeline checkpoint..."` → `"needs_recovery: true"`
- Step: `"Recovery needed — calling emergency-run..."`
- Recovery response with `"status":"COMPLETED"` and log lines showing Polygon data pull

---

## SECTION 5 — Evidence Scripts

Two scripts are at the project root. Their hashes are in Section 1a.

### verified_run.sh
Captures: file hashes, line numbers, DB state, GitHub Actions run log, live checkpoint endpoint.  
Output: `verified_evidence_<date>_<time>.json`  
Usage: `bash verified_run.sh [optional_GH_run_id]`

### verify_chain.sh  
Validates the evidence bundle against live state.  
Checks: script integrity (sha256), file hash match, line number confirmation, DB state, GH trigger event type, live checkpoint.  
Usage: `bash verify_chain.sh verified_evidence_2026-07-21_*.json`  
Exit 0 only if ALL checks pass.

---

## SECTION 6 — DB State at Time of This Document

Current state after restoring from today's test reset (19:10 UTC):

```
options_pipeline_jobs (scan_date=2026-07-17):
  MEC   → DONE
  PINS  → DONE
  TER   → DONE
  UMC   → DONE
  WOLF  → DONE

daily_pipeline_runs (run_date=2026-07-17):
  status=COMPLETED, trigger_source=emergency_run_endpoint

aiem_paper_trades (trade_date=2026-07-17):
  id=28  ticker=MEC  signal_source=live_verification_test  status=OPEN
```

No stale PENDING rows. Monday's primary pipeline is unaffected.

---

## SECTION 7 — What the Completed Proof Will Look Like

The directive will be satisfied when all of the following are in one document:

- [ ] `verified_evidence_2026-07-21_*.json` output from `verified_run.sh`
- [ ] `verify_chain.sh` run showing all PASS, 0 FAIL
- [ ] GitHub Actions run URL with `event=schedule` in the API response (not workflow_dispatch)
- [ ] Raw GH Actions step log showing "needs_recovery: true" → "Recovery needed" → COMPLETED
- [ ] `SELECT ticker, status, completed_at FROM options_pipeline_jobs WHERE scan_date='2026-07-21'` before (PENDING) and after (DONE)
- [ ] `SELECT id, ticker, signal_source, entry_price, created_at FROM aiem_paper_trades WHERE trade_date='2026-07-21'` showing rows written during the backup run
- [ ] Polygon data fetch log lines (visible in the backup runner output returned by emergency-run)
