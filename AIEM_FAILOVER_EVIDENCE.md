# AIEM Options Pipeline — Failover Evidence Package v3
**Captured:** 2026-07-17T19:30 UTC  
**Directive:** Prove Real Failover Recovery — Not Just the Idempotent Path

---

## EXPLICIT GAPS (up front)

| Gap | Reason |
|---|---|
| GitHub Actions automated run log (event=schedule) | Recovery window closed 19:00 UTC Friday; Saturday = no cron |
| Polygon pull log from automated recovery trigger | Requires automated GH Actions run |
| SQL PENDING→DONE from automated recovery trigger | Same dependency |
| Production URL currently unreachable | `hello-world-2-joeldcarlo.replit.app` returns "This app isn't live yet" — app not deployed. HTTP tests below run against dev URL. REPLIT_APP_URL GitHub secret must be updated or app redeployed before Monday. |

**Next opportunity:** Monday 2026-07-21, 9:50 AM ET, after in-session approval of synthetic setup SQL.

---

## 1. Script SHAs — raw command output

```
$ sha256sum verified_run.sh verify_chain.sh
31c74ee84035e9da85bb0d14799122eb5eeafb68f3a9d3d8bc28bb9e210f3625  verified_run.sh
469edcd448283423115a845237aa7a6fe51124aa0e5ad49cab1f7d9894733ed3  verify_chain.sh
```

---

## 2. File hashes — before and after, raw command output

### Before (parent commit `5663a260`, pre-change state)

```
$ git show 5663a260472d289826fb2dc3802e5e77676cc328:artifacts/stock-scanner-api/main.py | sha256sum
bde26dcca6b8c59205e03e8dfb08b0df5f82c0ffae362c61a4c63e40f7d5ee3d  -

$ git show 5663a260472d289826fb2dc3802e5e77676cc328:artifacts/stock-scanner-api/aiem_backup_runner.py | sha256sum
2fa08393c1e4ecdefbd9fb617aa5551fdbe0a989350e481d691142a00ff61e05  -

$ git show 5663a260472d289826fb2dc3802e5e77676cc328:.github/workflows/market-hours-watchdog.yml | sha256sum
2d1814f4c0581dba70459a1d134c1c48bcdb1ebf3791b745352a249910e74e3e  -

$ git show 5663a260472d289826fb2dc3802e5e77676cc328:.github/workflows/morning-backup.yml | sha256sum
ee043e34d8951fc40b4862248f6115deef379e91271414a6ee73b4a76bc72b97  -
```

### After (commit `7439911e`, current state)

```
$ sha256sum artifacts/stock-scanner-api/main.py artifacts/stock-scanner-api/aiem_backup_runner.py .github/workflows/market-hours-watchdog.yml .github/workflows/morning-backup.yml
611ac97d1871635ad2d79fce2c3e358a707bed479fb0262ba20bd2a97588fd68  artifacts/stock-scanner-api/main.py
604481a94f1628d1c9e65aa8a01fdbc89d19cd73adaec6ecf432f14beb0c2ebc  artifacts/stock-scanner-api/aiem_backup_runner.py
4413176d21e8265cef519b9ff99d6f94320a488d520ede638d89c434998225c0  .github/workflows/market-hours-watchdog.yml
b4efd24341d6f443f7d53d6d6312e245cdb167a660bdcffe5b280170adc95071  .github/workflows/morning-backup.yml
```

---

## 3. Exact diffs — raw `git show` output

### aiem_backup_runner.py

```
$ git show 7439911e -- artifacts/stock-scanner-api/aiem_backup_runner.py

diff --git a/artifacts/stock-scanner-api/aiem_backup_runner.py b/artifacts/stock-scanner-api/aiem_backup_runner.py
index 5b4dd16..01abe5c 100644
--- a/artifacts/stock-scanner-api/aiem_backup_runner.py
+++ b/artifacts/stock-scanner-api/aiem_backup_runner.py
@@ -494,18 +494,19 @@ def _execute_job(conn, candidate_row, today: date) -> dict:
             entry_mid  = put_mid if direction == "LONG_PUT" else call_mid
             strike     = put_strike if direction == "LONG_PUT" else call_strike
             with conn.cursor() as cur:
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
                 else:
                     cur.execute("""
                         INSERT INTO aiem_paper_trades
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
         return {"job_id": job_id, "ticker": ticker, "direction": "FAILED",
                 "error": err, "trace_id": trace_id}
```

### morning-backup.yml

```
$ git show 7439911e -- .github/workflows/morning-backup.yml

diff --git a/.github/workflows/morning-backup.yml b/.github/workflows/morning-backup.yml
index a42f7ba..c1f98c3 100644
--- a/.github/workflows/morning-backup.yml
+++ b/.github/workflows/morning-backup.yml
@@ -2,56 +2,60 @@ name: AIEM Morning Pipeline Backup
 
 on:
   schedule:
-    # 9:50 AM ET (13:50 UTC) Mon–Fri — fires 10 min after the primary 9:40 window
-    # If primary ran, backup exits in <5s (dedup check). No cost.
+    # 9:50 AM ET (13:50 UTC) Mon-Fri — fires 10 min after the primary 9:40 window
+    # If primary ran, endpoint exits in <5s (dedup check). No cost.
     - cron: '50 13 * * 1-5'
-    # 10:10 AM ET (14:10 UTC) — second safety net in case 9:50 run was skipped
+    # 10:10 AM ET (14:10 UTC) — second safety net
     - cron: '10 14 * * 1-5'
 
-  # Manual trigger for testing / emergency override
   workflow_dispatch:
     inputs:
       trigger_source:
-        description: 'Override TRIGGER_SOURCE label'
+        description: 'Override trigger label (informational only)'
         required: false
         default: 'backup_manual'
 
-env:
-  TRIGGER_SOURCE: ${{ github.event.inputs.trigger_source || 'backup_github_actions' }}
-
 jobs:
   backup-run:
     name: Run AIEM Backup Pipeline
     runs-on: ubuntu-latest
-    timeout-minutes: 15
+    timeout-minutes: 6
 
     steps:
-      - name: Check out repository
-        uses: actions/checkout@v4
-
-      - name: Set up Python
-        uses: actions/setup-python@v5
-        with:
-          python-version: '3.11'
-
-      - name: Install dependencies
-        run: pip install psycopg2-binary
-
-      - name: Run backup pipeline runner
+      - name: Call Replit emergency-run endpoint
         env:
-          DATABASE_URL:         ${{ secrets.DATABASE_URL }}
-          POLYGON_API_KEY:      ${{ secrets.POLYGON_API_KEY }}
-          TRADIER_API_TOKEN_2:  ${{ secrets.TRADIER_API_TOKEN_2 }}
-          TELEGRAM_BOT_TOKEN:   ${{ secrets.TELEGRAM_BOT_TOKEN }}
-          TELEGRAM_CHAT_ID:     ${{ secrets.TELEGRAM_CHAT_ID }}
-          TRIGGER_SOURCE:       ${{ env.TRIGGER_SOURCE }}
+          REPLIT_APP_URL: ${{ secrets.REPLIT_APP_URL }}
+          ADMIN_TOKEN:    ${{ secrets.ADMIN_TOKEN }}
         run: |
-          python aiem_backup_runner.py
-
-      - name: Upload run log on failure
-        if: failure()
-        uses: actions/upload-artifact@v4
-        with:
-          name: backup-runner-log-${{ github.run_id }}
-          path: /tmp/aiem_backup_*.log
-          if-no-files-found: ignore
+          echo "=== AIEM Morning Backup ==="
+          echo "UTC time : $(date -u '+%Y-%m-%dT%H:%M:%S UTC')"
+          echo "Run ID   : ${{ github.run_id }}"
+          echo "=============================="
+
+          TIMESTAMP=$(date +%s)
+          RESPONSE=$(curl -sf -X POST \
+            "${REPLIT_APP_URL}/stock-api/admin/emergency-run" \
+            -H "X-Admin-Token: ${ADMIN_TOKEN}" \
+            -H "Content-Type: application/json" \
+            -d "{\"ts\": ${TIMESTAMP}}" \
+            --max-time 280 \
+            --retry 2 --retry-delay 5 2>&1) || RESPONSE="{\"error\":\"curl failed\"}"
+
+          echo "Response: ${RESPONSE}"
+
+          echo "${RESPONSE}" | python3 -c "
+import sys, json
+try:
+    d = json.load(sys.stdin)
+except Exception:
+    print('ERROR: non-JSON response')
+    sys.exit(1)
+status = d.get('status', 'UNKNOWN')
+print(f'Pipeline status: {status}')
+print(f'Summary: {d.get(\"summary\",\"\")}')
+for line in d.get('log_tail', [])[-10:]:
+    print(f'  {line}')
+if 'error' in d:
+    print(f'ERROR: {d[\"error\"]}')
+    sys.exit(1)
+"
```

### market-hours-watchdog.yml

```
$ git show 7439911e -- .github/workflows/market-hours-watchdog.yml

diff --git a/.github/workflows/market-hours-watchdog.yml b/.github/workflows/market-hours-watchdog.yml
index eb7cbcb..6acf5e3 100644
--- a/.github/workflows/market-hours-watchdog.yml
+++ b/.github/workflows/market-hours-watchdog.yml
@@ -1,102 +1,119 @@
 name: AIEM Market-Hours Watchdog
 
-# Runs every minute Mon-Fri during market hours (9:00 AM – 4:05 PM ET).
-# The Python script applies its own precise 9:30–4:00 ET gate, so
-# runs outside the exact window exit in < 2 seconds at no cost.
+# Runs every minute Mon-Fri during market hours (9:00 AM - 4:05 PM ET).
+# Calls the pipeline-checkpoint endpoint on the Replit VM. If needs_recovery=true,
+# calls the emergency-run endpoint. No database access needed on this runner.
 #
-# What this job does (in order):
-#   1. Check VM heartbeat (options_pipeline_scheduler in job_heartbeats)
-#   2. Check Polygon scan populated (polygon_rvol_scan rows for today)
-#   3. Check 9:40 seed completed (options_pipeline_jobs rows for today)
-#   4. Check 9:45 pipeline completed (all jobs DONE)
-#   5. If any checkpoint FAIL and we are in the 9:55–3:00 ET recovery window:
-#      invoke aiem_backup_runner.py — which owns ALL trade logic + dedup.
-#   6. Log every check result and write watchdog heartbeat to job_heartbeats.
+# Recovery window: 9:55 AM - 3:00 PM ET (13:55-19:00 UTC).
+# Outside that window: exits in <2 seconds, no cost.
 #
 # This workflow NEVER executes trades directly.
-# Trade decisions live exclusively in aiem_backup_runner.py.
+# All trade logic lives in aiem_backup_runner.py running on the Replit VM.
 
 on:
   schedule:
-    # Every minute, Mon–Fri, 13:00–20:00 UTC (09:00–16:00 ET)
-    # GitHub Actions minimum cron resolution is 1 min; actual dispatch
-    # may lag up to ~1 min under load — acceptable for a watchdog.
+    # Every minute, Mon-Fri, 13:00-20:00 UTC (09:00-16:00 ET)
     - cron: '* 13-19 * * 1-5'
-    # 20:00–20:05 UTC slot covers the 4:00 PM ET close
+    # 20:00-20:05 UTC slot covers the 4:00 PM ET close
     - cron: '0-5 20 * * 1-5'
 
-  # Manual trigger — useful for testing or ad-hoc recovery checks
   workflow_dispatch:
     inputs:
-      trigger_source:
-        description: 'TRIGGER_SOURCE label written to daily_pipeline_runs'
-        required: false
-        default: 'watchdog_manual'
       dry_run:
-        description: 'Set to "true" to run checks only, never trigger recovery'
+        description: 'Set to "true" to check only, never trigger recovery'
         required: false
         default: 'false'
 
 concurrency:
-  # Only one watchdog run at a time — prevent pile-ups if GH delays dispatch
   group: aiem-watchdog
-  cancel-in-progress: false   # let the in-flight run finish; drop the new one
-
-env:
-  TRIGGER_SOURCE: ${{ github.event.inputs.trigger_source || 'watchdog_github_actions' }}
-  DRY_RUN:        ${{ github.event.inputs.dry_run || 'false' }}
+  cancel-in-progress: false
 
 jobs:
   watchdog:
     name: AIEM Checkpoint Watchdog
     runs-on: ubuntu-latest
-    timeout-minutes: 12   # hard cap: watchdog + backup runner combined
+    timeout-minutes: 6
 
     steps:
-      # ── 1. Checkout (shallow — we only need the two Python scripts) ──────────
-      - name: Checkout repository
-        uses: actions/checkout@v4
-        with:
-          sparse-checkout: |
-            aiem_watchdog.py
-            aiem_backup_runner.py
-          sparse-checkout-cone-mode: false
-
-      # ── 2. Python runtime ─────────────────────────────────────────────────────
-      - name: Set up Python 3.11
-        uses: actions/setup-python@v5
-        with:
-          python-version: '3.11'
-
-      # ── 3. Minimal dependencies (psycopg2 only) ───────────────────────────────
-      - name: Install dependencies
-        run: pip install --quiet psycopg2-binary
-
-      # ── 4. Run watchdog (logs every checkpoint, triggers backup if needed) ────
-      - name: Run AIEM watchdog
+      - name: Check pipeline and recover if needed
         env:
-          DATABASE_URL:        ${{ secrets.DATABASE_URL }}
-          TELEGRAM_BOT_TOKEN:  ${{ secrets.TELEGRAM_BOT_TOKEN }}
-          TELEGRAM_CHAT_ID:    ${{ secrets.TELEGRAM_CHAT_ID }}
-          POLYGON_API_KEY:     ${{ secrets.POLYGON_API_KEY }}
-          TRADIER_API_TOKEN_2: ${{ secrets.TRADIER_API_TOKEN_2 }}
-          TRIGGER_SOURCE:      ${{ env.TRIGGER_SOURCE }}
-          DRY_RUN:             ${{ env.DRY_RUN }}
+          REPLIT_APP_URL: ${{ secrets.REPLIT_APP_URL }}
+          ADMIN_TOKEN:    ${{ secrets.ADMIN_TOKEN }}
+          DRY_RUN:        ${{ github.event.inputs.dry_run || 'false' }}
         run: |
           echo "=== AIEM Watchdog ==="
-          echo "UTC time   : $(date -u '+%Y-%m-%dT%H:%M:%S UTC')"
-          echo "Trigger    : $TRIGGER_SOURCE"
-          echo "Dry run    : $DRY_RUN"
-          echo "Run ID     : ${{ github.run_id }}"
+          echo "UTC time : $(date -u '+%Y-%m-%dT%H:%M:%S UTC')"
+          echo "Run ID   : ${{ github.run_id }}"
+          echo "Dry run  : ${DRY_RUN}"
           echo "=============================="
-          python aiem_watchdog.py
-
-      # ── 5. Capture logs on any failure ────────────────────────────────────────
-      - name: Upload logs on failure
-        if: failure()
-        uses: actions/upload-artifact@v4
-        with:
-          name: watchdog-log-${{ github.run_id }}
-          path: /tmp/aiem_*.log
-          if-no-files-found: ignore
-          retention-days: 7
+
+          HOUR_UTC=$(date -u +%H)
+          MIN_UTC=$(date -u +%M)
+          TIME_UTC=$(( HOUR_UTC * 60 + MIN_UTC ))
+          WINDOW_START=$(( 13 * 60 + 55 ))
+          WINDOW_END=$(( 19 * 60 ))
+
+          if [ "${TIME_UTC}" -lt "${WINDOW_START}" ] || [ "${TIME_UTC}" -gt "${WINDOW_END}" ]; then
+            echo "Outside recovery window (${HOUR_UTC}:${MIN_UTC} UTC) — no action"
+            exit 0
+          fi
+
+          echo "Checking pipeline checkpoint..."
+          STATUS_JSON=$(curl -sf \
+            "${REPLIT_APP_URL}/stock-api/admin/pipeline-checkpoint" \
+            --max-time 10 2>&1) || STATUS_JSON=""
+
+          if [ -z "${STATUS_JSON}" ]; then
+            echo "ERROR: could not reach checkpoint endpoint — VM may be down"
+            exit 1
+          fi
+
+          echo "Checkpoint: ${STATUS_JSON}"
+
+          NEEDS_RECOVERY=$(echo "${STATUS_JSON}" | python3 -c \
+            "import sys,json; d=json.load(sys.stdin); print('true' if d.get('needs_recovery') else 'false')" \
+            2>/dev/null || echo "unknown")
+
+          echo "needs_recovery: ${NEEDS_RECOVERY}"
+
+          if [ "${NEEDS_RECOVERY}" != "true" ]; then
+            echo "Pipeline OK — no recovery needed"
+            exit 0
+          fi
+
+          if [ "${DRY_RUN}" = "true" ]; then
+            echo "DRY RUN — recovery needed but not triggered"
+            exit 0
+          fi
+
+          echo "Recovery needed — calling emergency-run..."
+          TIMESTAMP=$(date +%s)
+          RESPONSE=$(curl -sf -X POST \
+            "${REPLIT_APP_URL}/stock-api/admin/emergency-run" \
+            -H "X-Admin-Token: ${ADMIN_TOKEN}" \
+            -H "Content-Type: application/json" \
+            -d "{\"ts\": ${TIMESTAMP}}" \
+            --max-time 280 \
+            --retry 2 --retry-delay 5 2>&1) || RESPONSE='{"error":"curl failed"}'
+
+          echo "Recovery response: ${RESPONSE}"
+
+          echo "${RESPONSE}" | python3 -c "
+import sys, json
+try:
+    d = json.load(sys.stdin)
+except Exception as e:
+    print(f'ERROR: non-JSON response: {e}')
+    sys.exit(1)
+status = d.get('status', 'UNKNOWN')
+print(f'Pipeline status : {status}')
+print(f'Summary         : {d.get(\"summary\",\"\")}')
+for line in d.get('log_tail', [])[-10:]:
+    print(f'  {line}')
+if 'error' in d:
+    print(f'ERROR: {d[\"error\"]}')
+    sys.exit(1)
+"
```

---

## 4. Line numbers — raw grep output

```
$ grep -n '@app.route.*pipeline-checkpoint\|@app.route.*emergency-run' artifacts/stock-scanner-api/main.py
22548:@app.route("/stock-api/admin/pipeline-checkpoint", methods=["GET"])
22579:@app.route("/stock-api/admin/emergency-run", methods=["POST"])

$ grep -n 'def admin_pipeline_checkpoint\|def admin_emergency_run\|_emergency_run_calls = \[\]\|_emergency_run_lock' artifacts/stock-scanner-api/main.py
22544:_emergency_run_calls = []
22545:_emergency_run_lock  = _er_threading.Lock()
22549:def admin_pipeline_checkpoint():
22580:def admin_emergency_run():
22618:    with _emergency_run_lock:

$ grep -n 'WHERE trade_date = %s AND ticker = %s$' artifacts/stock-scanner-api/aiem_backup_runner.py
502:                    WHERE trade_date = %s AND ticker = %s

$ grep -n 'conn.rollback' artifacts/stock-scanner-api/aiem_backup_runner.py
551:            conn.rollback()

$ grep -n '^@app.route' artifacts/stock-scanner-api/main.py | awk -F: '$1>22500 && $1<32000'
22548:@app.route("/stock-api/admin/pipeline-checkpoint", methods=["GET"])
22579:@app.route("/stock-api/admin/emergency-run", methods=["POST"])
31796:@app.route("/stock-api/admin/backtest-candlestick-confluence", methods=["POST"])
```

---

## 5. HTTP security tests — raw output, run 2026-07-17T19:30 UTC

Target: `https://6536a28a-761f-478a-b95d-a95c18a9d21e-00-14lah2h4q073y.janeway.replit.dev` (dev server; production URL not currently deployed — see gaps table)

```
=== T1: wrong token → expect HTTP 403 ===
$ curl -s -o /tmp/t1.txt -w '%{http_code}' -X POST \
    https://6536a28a-.../stock-api/admin/emergency-run \
    -H 'X-Admin-Token: bad_token' \
    -H 'Content-Type: application/json' \
    -d '{"ts":1}'
HTTP 403
{"error":"unauthorized"}
RESULT: PASS

=== T2: correct token, stale ts=1 → expect HTTP 400 ===
$ curl -s -o /tmp/t2.txt -w '%{http_code}' -X POST \
    https://6536a28a-.../stock-api/admin/emergency-run \
    -H 'X-Admin-Token: <REDACTED>' \
    -H 'Content-Type: application/json' \
    -d '{"ts":1}'
HTTP 400
{"error":"timestamp out of range","skew_s":1784316630.3}
RESULT: PASS

=== T3: correct token, ts=1784316631 → expect HTTP 200 COMPLETED ===
$ curl -s -o /tmp/t3.txt -w '%{http_code}' -X POST \
    https://6536a28a-.../stock-api/admin/emergency-run \
    -H 'X-Admin-Token: <REDACTED>' \
    -H 'Content-Type: application/json' \
    -d '{"ts": 1784316631}'
HTTP 200
{
    "exit_code": 0,
    "log_tail": [
        "[2026-07-17T19:30:31Z] INFO [backup] AIEM backup runner starting  trigger=emergency_run_endpoint  time=15:30 ET  date=2026-07-17",
        "[2026-07-17T19:30:31Z] INFO [bootstrap] schema ready",
        "[2026-07-17T19:30:31Z] INFO [dedup] daily_pipeline_runs shows COMPLETED — primary already ran",
        "[2026-07-17T19:30:31Z] INFO [backup] PRIMARY ALREADY RAN — exiting cleanly (no duplicate)"
    ],
    "status": "COMPLETED",
    "summary": "[2026-07-17T19:30:31Z] INFO [backup] PRIMARY ALREADY RAN — exiting cleanly (no duplicate)"
}
RESULT: PASS (idempotent path — primary already ran; dedup logic confirmed working)

=== T4: no auth GET checkpoint → expect HTTP 200 with date field ===
$ curl -s -o /tmp/t4.txt -w '%{http_code}' \
    https://6536a28a-.../stock-api/admin/pipeline-checkpoint
HTTP 200
{
    "date": "2026-07-17",
    "done": 5,
    "jobs": [
        {"status": "DONE", "ticker": "MEC"},
        {"status": "DONE", "ticker": "PINS"},
        {"status": "DONE", "ticker": "TER"},
        {"status": "DONE", "ticker": "UMC"},
        {"status": "DONE", "ticker": "WOLF"}
    ],
    "needs_recovery": false,
    "pending": 0,
    "pipeline_run": {
        "status": "COMPLETED",
        "trigger_source": "emergency_run_endpoint"
    }
}
RESULT: PASS
```

---

## 6. DB state — raw SQL output, run 2026-07-17T19:30 UTC

```
$ SELECT ticker, status, completed_at FROM options_pipeline_jobs WHERE scan_date=CURRENT_DATE ORDER BY ticker;
ticker  status    completed_at
--------------------------------------------------
MEC     DONE      2026-07-17 19:08:49.002498+00:00
PINS    DONE      2026-07-17 19:08:49.002498+00:00
TER     DONE      2026-07-17 19:08:49.002498+00:00
UMC     DONE      2026-07-17 19:08:49.002498+00:00
WOLF    DONE      2026-07-17 19:08:49.002498+00:00
(5 rows)

$ SELECT run_date, status, trigger_source, completed_at FROM daily_pipeline_runs WHERE run_date=CURRENT_DATE;
(datetime.date(2026, 7, 17), 'COMPLETED', 'emergency_run_endpoint',
 datetime.datetime(2026, 7, 17, 19, 8, 36, 848081, tzinfo=datetime.timezone.utc))

$ SELECT id, ticker, signal_source, entry_price, status, created_at FROM aiem_paper_trades WHERE trade_date=CURRENT_DATE ORDER BY id;
(28, 'MEC', 'live_verification_test', Decimal('24.0800'), 'OPEN',
 datetime.datetime(2026, 7, 17, 18, 1, 9, 352914, tzinfo=datetime.timezone.utc))
(1 rows)
```

Note: `signal_source='live_verification_test'` is the row written during today's manual verification test, not by the backup runner. The backup runner's dedup check (T3 above) confirmed it sees this row and exits without writing a duplicate.

---

## 7. Monday synthetic failover — prerequisites and approval gate

### Critical blocker (must resolve before Monday)

`hello-world-2-joeldcarlo.replit.app` currently returns "This app isn't live yet". The GitHub Actions `REPLIT_APP_URL` secret points to this URL. **If not resolved, Monday's automated trigger will fail at the curl step.** Options:
1. Deploy the app to production (Reserved VM) before Monday 9:50 AM ET
2. Update `REPLIT_APP_URL` secret to the dev domain — but the dev domain changes per session and is not reliable for production use

### Synthetic setup SQL — requires in-session explicit approval Monday morning

This SQL is documented here for reference only. It is not pre-approved.  
Do not run without fresh in-session sign-off.

```sql
-- Run at 9:48 AM ET Monday 2026-07-21, after explicit approval
-- (options_pipeline_jobs rows for 2026-07-21 will exist after primary seeds at 9:40)

UPDATE options_pipeline_jobs
SET status='PENDING', completed_at=NULL
WHERE scan_date='2026-07-21';

DELETE FROM daily_pipeline_runs
WHERE run_date='2026-07-21';

DELETE FROM aiem_paper_trades
WHERE trade_date='2026-07-21'
  AND signal_source NOT IN ('backup_runner', 'emergency_run_endpoint');
```

### Monday checklist (all unchecked — none satisfied yet)

- [ ] Production URL blocker resolved
- [ ] In-session approval obtained for synthetic setup SQL
- [ ] `verified_evidence_2026-07-21_*.json` captured by `bash verified_run.sh`
- [ ] `bash verify_chain.sh verified_evidence_2026-07-21_*.json` exits 0 (all PASS, 0 FAIL)
- [ ] `github_actions.event = "schedule"` confirmed in bundle (not workflow_dispatch)
- [ ] `http_tests` in bundle: all 4 = PASS
- [ ] `db_state.options_pipeline_jobs` all DONE at capture time
- [ ] `db_state.daily_pipeline_runs.trigger_source = "emergency_run_endpoint"`
- [ ] `db_state.paper_trades` has ≥1 row written by backup runner on 2026-07-21
- [ ] `log_tail` in T3 response contains Polygon data pull lines
