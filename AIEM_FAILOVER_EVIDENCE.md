# AIEM Options Pipeline — Failover Evidence Package v2
**Generated:** 2026-07-17 (updated per review response)  
**Directive reference:** "Prove Real Failover Recovery — Not Just the Idempotent Path"

---

## EXPLICIT GAPS — Stated First

| Item | Reason |
|---|---|
| Automated GH Actions run log (event=schedule) | Recovery window closed 19:00 UTC Friday; Saturday = no cron (Mon-Fri only) |
| Polygon pull log from automated recovery run | Requires the above |
| SQL PENDING→DONE from automated recovery trigger | Requires the above |

**Next opportunity:** Monday 2026-07-21, 9:50 AM ET (morning-backup schedule trigger).

---

## ITEM 1 — Script SHAs (resolved)

Scripts were updated to v1.1 to add HTTP security tests (Item 3 below).  
The SHAs in the previous document (`4f90f7cf...` / `e634d06f...`) were v1.0.  
**Canonical SHAs as of this document (v1.1):**

```
31c74ee84035e9da85bb0d14799122eb5eeafb68f3a9d3d8bc28bb9e210f3625  verified_run.sh
469edcd448283423115a845237aa7a6fe51124aa0e5ad49cab1f7d9894733ed3  verify_chain.sh
```

Verify live: `sha256sum verified_run.sh verify_chain.sh`

---

## ITEM 2 — Before/After Hashes + Exact Diffs

### 2a. Before-hashes (parent commit `5663a260`, pre-change state)

```
bde26dcca6b8c59205e03e8dfb08b0df5f82c0ffae362c61a4c63e40f7d5ee3d  main.py
2fa08393c1e4ecdefbd9fb617aa5551fdbe0a989350e481d691142a00ff61e05  aiem_backup_runner.py
2d1814f4c0581dba70459a1d134c1c48bcdb1ebf3791b745352a249910e74e3e  market-hours-watchdog.yml
ee043e34d8951fc40b4862248f6115deef379e91271414a6ee73b4a76bc72b97  morning-backup.yml
```

### 2b. After-hashes (commit `7439911e`, current state)

```
611ac97d1871635ad2d79fce2c3e358a707bed479fb0262ba20bd2a97588fd68  main.py
604481a94f1628d1c9e65aa8a01fdbc89d19cd73adaec6ecf432f14beb0c2ebc  aiem_backup_runner.py
4413176d21e8265cef519b9ff99d6f94320a488d520ede638d89c434998225c0  market-hours-watchdog.yml
b4efd24341d6f443f7d53d6d6312e245cdb167a660bdcffe5b280170adc95071  morning-backup.yml
```

Verify live: `sha256sum artifacts/stock-scanner-api/main.py artifacts/stock-scanner-api/aiem_backup_runner.py .github/workflows/market-hours-watchdog.yml .github/workflows/morning-backup.yml`

### 2c. Exact diffs (from `git show 7439911e`)

**aiem_backup_runner.py** — two changes:

```diff
--- a/artifacts/stock-scanner-api/aiem_backup_runner.py
+++ b/artifacts/stock-scanner-api/aiem_backup_runner.py

@@ -494,9 +494,10 @@ def _execute_job(conn, candidate_row, today: date) -> dict:
-                # Pre-check: avoid duplicate paper trade for same ticker/date/source
-                # (aiem_paper_trades has no unique constraint — explicit guard required)
+                # Pre-check: avoid duplicate paper trade for same ticker/date.
+                # Check on (ticker, trade_date) only — matches the DB unique constraint.
+                # A trade from any source counts as a dedup (idempotent guard).
                 cur.execute("""
-                    SELECT id FROM aiem_paper_trades
-                    WHERE trade_date = %s AND ticker = %s AND signal_source = %s
+                    SELECT id, signal_source FROM aiem_paper_trades
+                    WHERE trade_date = %s AND ticker = %s
                     LIMIT 1
-                """, (today, ticker, _TRIGGER))
+                """, (today, ticker))
                 _pt_existing = cur.fetchone()

                 if _pt_existing:
                     log.info(f"[exec] paper trade already exists id={_pt_existing[0]} "
-                             f"for {ticker}/{today}/{_TRIGGER} — skipping duplicate")
+                             f"source={_pt_existing[1]} for {ticker}/{today} — skipping duplicate")

@@ -545,13 +546,21 @@ def _execute_job(conn, candidate_row, today: date) -> dict:
     except Exception as e:
         err = str(e)[:400]
         log.error(f"[exec] FAILED job_id={job_id} {ticker}: {e}")
-        with conn.cursor() as cur:
-            cur.execute("""
-                UPDATE options_pipeline_jobs
-                SET status='FAILED', error_text=%s, completed_at=NOW()
-                WHERE id=%s
-            """, (err, job_id))
-        conn.commit()
+        try:
+            conn.rollback()
+        except Exception:
+            pass
+        try:
+            with conn.cursor() as cur:
+                cur.execute("""
+                    UPDATE options_pipeline_jobs
+                    SET status='FAILED', error_text=%s, completed_at=NOW()
+                    WHERE id=%s
+                """, (err, job_id))
+            conn.commit()
+        except Exception as upd_err:
+            log.error(f"[exec] could not update job status: {upd_err}")
```

**main.py** — 112 lines added at line 22544 (new endpoints):

```diff
--- a/artifacts/stock-scanner-api/main.py
+++ b/artifacts/stock-scanner-api/main.py

@@ +22544 @@
+# Rate limit state — module-level, shared across all requests
+import threading as _er_threading
+_emergency_run_calls = []
+_emergency_run_lock  = _er_threading.Lock()
+
+@app.route("/stock-api/admin/pipeline-checkpoint", methods=["GET"])
+def admin_pipeline_checkpoint():
+    """Read-only: returns today's pipeline status and needs_recovery flag. No auth."""
+    import psycopg2
+    from datetime import date, timezone, datetime
+    try:
+        today = date.today().isoformat()
+        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
+            cur.execute(
+                "SELECT ticker, status FROM options_pipeline_jobs "
+                "WHERE scan_date = %s ORDER BY ticker", (today,))
+            jobs = [{"ticker": r[0], "status": r[1]} for r in cur.fetchall()]
+            cur.execute(
+                "SELECT status, trigger_source FROM daily_pipeline_runs "
+                "WHERE run_date = %s LIMIT 1", (today,))
+            row = cur.fetchone()
+            dpr = {"status": row[0], "trigger_source": row[1]} if row else None
+        pending = sum(1 for j in jobs if j["status"] == "PENDING")
+        done    = sum(1 for j in jobs if j["status"] == "DONE")
+        return jsonify({
+            "date":           today,
+            "jobs":           jobs,
+            "pending":        pending,
+            "done":           done,
+            "pipeline_run":   dpr,
+            "needs_recovery": pending > 0 and (dpr is None or dpr.get("status") != "COMPLETED"),
+        })
+    except Exception as e:
+        return jsonify({"error": str(e)}), 500
+
+@app.route("/stock-api/admin/emergency-run", methods=["POST"])
+def admin_emergency_run():
+    import hmac, time, subprocess, sys as _sys, os as _os
+    from datetime import datetime, timezone
+    got  = (request.headers.get("X-Admin-Token") or "").encode()
+    want = (_os.environ.get("ADMIN_TOKEN") or "").encode()
+    ip   = (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()
+    def _log(result, detail=""):
+        print(f"[emergency-run] ip={ip} time={datetime.now(timezone.utc).isoformat()} "
+              f"result={result}{' ' + str(detail) if detail else ''}", flush=True)
+    # 1. constant-time auth
+    if not got or not want or not hmac.compare_digest(got, want):
+        _log("UNAUTHORIZED")
+        return jsonify({"error": "unauthorized"}), 403
+    # 2. replay protection (±60s)
+    try:
+        body   = request.get_json(force=True) or {}
+        ts_req = float(body.get("ts", 0))
+        skew   = abs(time.time() - ts_req)
+        if skew > 60:
+            _log("REPLAY_REJECTED", f"skew={skew:.1f}s")
+            return jsonify({"error": "timestamp out of range", "skew_s": round(skew, 1)}), 400
+    except Exception:
+        _log("BAD_REQUEST")
+        return jsonify({"error": "body must be JSON with ts (unix epoch)"}), 400
+    # 3. rate limit: 3/hour globally
+    now = time.time()
+    with _emergency_run_lock:
+        _emergency_run_calls[:] = [t for t in _emergency_run_calls if now - t < 3600]
+        if len(_emergency_run_calls) >= 3:
+            _log("RATE_LIMITED", f"calls_this_hour={len(_emergency_run_calls)}")
+            return jsonify({"error": "rate limit exceeded: max 3 calls/hour"}), 429
+        _emergency_run_calls.append(now)
+    # 4. run backup pipeline
+    _log("ACCEPTED")
+    runner = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "aiem_backup_runner.py")
+    env    = {**_os.environ, "TRIGGER_SOURCE": "emergency_run_endpoint"}
+    try:
+        result  = subprocess.run(
+            [_sys.executable, runner],
+            capture_output=True, text=True, timeout=240, env=env)
+        lines   = (result.stdout + result.stderr).strip().splitlines()
+        status  = "COMPLETED" if result.returncode == 0 else "FAILED"
+        summary = next(
+            (l for l in reversed(lines) if "ALL DONE" in l or "NO_ACTION" in l or "ERROR" in l.upper()),
+            lines[-1] if lines else "")
+        _log(status, summary[:120])
+        return jsonify({
+            "status":    status,
+            "exit_code": result.returncode,
+            "summary":   summary,
+            "log_tail":  lines[-20:],
+        })
+    except subprocess.TimeoutExpired:
+        _log("TIMEOUT")
+        return jsonify({"error": "runner timed out after 240s"}), 504
+    except Exception as e:
+        _log("EXCEPTION", str(e)[:80])
+        return jsonify({"error": str(e)}), 500
```

**morning-backup.yml** — workflow simplified to pure curl HTTP call:  
Removed: `actions/checkout`, `setup-python`, `pip install psycopg2-binary`, `python aiem_backup_runner.py`  
Added: single `curl -X POST .../emergency-run` with `REPLIT_APP_URL` + `ADMIN_TOKEN` secrets  
(Full diff available via `git show 7439911e -- .github/workflows/morning-backup.yml`)

**market-hours-watchdog.yml** — rewritten from DB-direct Python to curl HTTP:  
Removed: DB checkout + psycopg2 + aiem_watchdog.py execution  
Added: bash window check (13:55–19:00 UTC) + `curl /pipeline-checkpoint` + conditional `curl /emergency-run`  
(Full diff available via `git show 7439911e -- .github/workflows/market-hours-watchdog.yml`)

---

## ITEM 3 — HTTP Tests Now Inside verified_run.sh

All four HTTP security tests run as section `[5/7]` of `verified_run.sh` and are stored in the `http_tests` key of the evidence bundle. They are **not** a separate manual curl step.

`verify_chain.sh` validates all four results in section `[G]`. A bundle without `http_tests` (pre-v1.1) will print `SKIP` on section G.

The four tests captured by the script:

| Key | What it tests | Expected result |
|---|---|---|
| `t1_wrong_token` | Wrong token → 403 | `PASS` when `actual_http=403` |
| `t2_stale_timestamp` | Correct token, `ts=1` → 400 | `PASS` when `actual_http=400` |
| `t3_valid_auth` | Correct token, fresh ts → 200 COMPLETED | `PASS` when status is `COMPLETED` or `NO_ACTION` |
| `t4_checkpoint_noauth` | No auth GET → 200 with date field | `PASS` when `actual_date=<today>` |

---

## ITEM 4 — Monday Data-Immutability: Explicit Approval Required

The synthetic setup SQL in Section 5 below is documented here for reference only.  
**It is not pre-approved by this document existing.**  
Do not execute `UPDATE ... PENDING`, `DELETE FROM daily_pipeline_runs`, or  
`DELETE FROM aiem_paper_trades` on Monday without fresh in-session explicit sign-off that morning.

---

## SECTION 4 — Line Numbers (grep -n, run 2026-07-17T19:08 UTC)

```
$ grep -n '@app.route.*pipeline-checkpoint\|@app.route.*emergency-run' \
    artifacts/stock-scanner-api/main.py

22548:@app.route("/stock-api/admin/pipeline-checkpoint", methods=["GET"])
22579:@app.route("/stock-api/admin/emergency-run", methods=["POST"])
```

```
$ grep -n 'def admin_pipeline_checkpoint\|def admin_emergency_run\|_emergency_run_calls\|_emergency_run_lock' \
    artifacts/stock-scanner-api/main.py

22544:_emergency_run_calls = []
22545:_emergency_run_lock  = _er_threading.Lock()
22549:def admin_pipeline_checkpoint():
22580:def admin_emergency_run():
22618:    with _emergency_run_lock:
```

```
$ grep -n 'WHERE trade_date = %s AND ticker = %s$' \
    artifacts/stock-scanner-api/aiem_backup_runner.py
502:                    WHERE trade_date = %s AND ticker = %s

$ grep -n 'conn.rollback' artifacts/stock-scanner-api/aiem_backup_runner.py
551:            conn.rollback()
```

Dead zone boundary — new endpoints at 22548/22579, first route after dead zone at 31796:
```
$ grep -n '^@app.route' artifacts/stock-scanner-api/main.py | awk -F: '$1>22500 && $1<32000'

22548:@app.route("/stock-api/admin/pipeline-checkpoint", methods=["GET"])
22579:@app.route("/stock-api/admin/emergency-run", methods=["POST"])
31796:@app.route("/stock-api/admin/backtest-candlestick-confluence", methods=["POST"])
```

---

## SECTION 5 — Monday Synthetic Failover Test (REQUIRES IN-SESSION APPROVAL)

This is an **injected/synthetic failover** — rows are manually reset to PENDING to simulate a primary pipeline failure. It is not an organic failure. The label "synthetic failover" is used throughout.

**What cannot be tested synthetically:** a VM crash that takes the entire Replit host offline (the backup runner's primary use case). That requires waiting for an actual failure.

**What is being tested:** the GitHub Actions→HTTP endpoint→backup runner→Polygon→paper trade pathway, under controlled PENDING conditions.

### Exact SQL to set up synthetic failover (9:48 AM ET Monday, requires approval)

```sql
-- Step 1: Reset pipeline jobs to PENDING (simulates primary not completing)
UPDATE options_pipeline_jobs
SET status='PENDING', completed_at=NULL
WHERE scan_date='2026-07-21';

-- Step 2: Remove daily run record (simulates no completed run)
DELETE FROM daily_pipeline_runs
WHERE run_date='2026-07-21';

-- Step 3: Remove paper trades from primary run (so backup writes fresh ones)
-- Only run if Step 1+2 approved; adjust signal_source filter as needed
DELETE FROM aiem_paper_trades
WHERE trade_date='2026-07-21'
  AND signal_source NOT IN ('backup_runner', 'emergency_run_endpoint');
```

### Timeline

| Time (ET) | Event |
|---|---|
| 9:40 AM | Primary options_pipeline_scheduler seeds `options_pipeline_jobs` for 2026-07-21 |
| ~9:42 AM | Primary completes, all jobs DONE |
| **9:48 AM** | **In-session approval obtained; SQL above executed** |
| 9:50 AM | `morning-backup.yml` schedule trigger fires (event=schedule) |
| 9:50–9:53 AM | Backup runner: finds PENDING → pulls Polygon → scores → writes paper trades |
| 9:53 AM | GH Actions step logs: "needs_recovery: true" → "Recovery needed" → COMPLETED |
| 9:55 AM | Run `bash verified_run.sh` to capture full evidence bundle |
| 9:56 AM | Run `bash verify_chain.sh verified_evidence_2026-07-21_*.json` |

---

## SECTION 6 — What the Completed Proof Requires (Checklist)

- [ ] In-session approval for synthetic setup SQL (Monday morning)
- [ ] `verified_evidence_2026-07-21_*.json` — all 7 sections captured by `verified_run.sh`
- [ ] `verify_chain.sh` output: `PASS: N  FAIL: 0  SKIP: 0  RESULT: ALL CHECKS PASSED`
- [ ] `github_actions.event = "schedule"` in the bundle (not `workflow_dispatch`)
- [ ] `http_tests` section: all 4 results = `PASS`
- [ ] `db_state.options_pipeline_jobs` — all tickers `DONE` at capture time
- [ ] `db_state.daily_pipeline_runs.trigger_source = "emergency_run_endpoint"`
- [ ] `db_state.paper_trades` — at least 1 row written by backup runner on 2026-07-21
- [ ] GH Actions step log showing Polygon data pull lines (visible in `log_tail` of T3 response)

---

## SECTION 7 — DB State at Time of This Document (2026-07-17T19:10 UTC)

```
options_pipeline_jobs (scan_date=2026-07-17): MEC/PINS/TER/UMC/WOLF → all DONE
daily_pipeline_runs  (run_date=2026-07-17):   status=COMPLETED, trigger_source=emergency_run_endpoint
```

No stale PENDING rows. Monday's primary pipeline creates fresh rows for 2026-07-21.
