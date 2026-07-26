# 5-Day No-Trade Fix Verification — FINAL
**Closed:** 2026-07-26  
**Files changed:** `artifacts/stock-scanner-api/aiem_options_scheduler.py`, `artifacts/stock-scanner-api/main.py`

---

## SHA-256 File Hashes

| File | Before | After |
|------|--------|-------|
| `aiem_options_scheduler.py` | `249612ba2b206dec16beb320d713e303219c337ba74bbd96e78170d2423d07de` | `3a2ce733de0f3a46237a518d23ff697e918cf1c08b0b0a48f6059f6daf13ac65` |
| `main.py` | `d0eb76dba31c9a9f32a18527df9431cfa2a81308d525c6759480b23e2ad830e3` | `01fb5060ca16ef38b787097f029e30d2cc65751617f9758bb25fc34d3139f799` |

## git diff --stat

```
 .../stock-scanner-api/aiem_options_scheduler.py    | 74 ++++++++++++++++++----
 artifacts/stock-scanner-api/main.py                | 21 +++++-
 2 files changed, 82 insertions(+), 13 deletions(-)
```

---

## Item 1 — OE Zombie RUNNING Record (Jul 24) — CLEARED

### BEFORE (raw SELECT)
```
id|run_date|status|started_et|completed_et|candidates_seeded|candidates_failed|error_text
124|2026-07-24|RUNNING|2026-07-24 10:17:12||0||
```

### SQL Executed
```sql
UPDATE daily_pipeline_runs
SET status      = 'FAILED',
    error_text  = 'Zombie: process died without setting completed_at (stuck RUNNING since
                   2026-07-24 10:17 ET, 54+ hours; 0 candidates_seeded; manually cleared 2026-07-26)',
    completed_at = NOW()
WHERE run_date = '2026-07-24'
  AND status   = 'RUNNING'
  AND id       = 124;
```

### Result
```
UPDATE 1
```

### AFTER (raw SELECT)
```
id|run_date|status|started_et|completed_et|candidates_seeded|candidates_failed|error_text
124|2026-07-24|FAILED|2026-07-24 10:17:12|2026-07-26 16:53:59|0||Zombie: process died without
setting completed_at (stuck RUNNING since 2026-07-24 10:17 ET, 54+ hours; 0 candidates_seeded;
manually cleared 2026-07-26)
```

**Status: CLEARED.** The OE pipeline can now create a new run record for Jul 24 or any subsequent date without the zombie blocking it.

---

## Item 2 — OE seed_daily_candidates Query Bug

### Root Cause

Two interacting bugs:

1. **`date.today()` (UTC server time) vs. ET date**: `seed_daily_candidates` and `run_pipeline_worker` used bare `date.today()` which returns the UTC server date. At nightly restart time (3 AM ET = 7 AM UTC), `date.today()` matches the ET date. However, on the Jul 22 failure, the actual proximate cause was the "init-before-query" pattern:

2. **Init-before-query pattern**: The startup missed-seed detector or Step 2b backfill pre-seeds jobs from `options_structure_scan` before the 09:40 CronTrigger fires. When the 09:40 seed job runs, all candidates are already in `options_pipeline_jobs` → all `DO NOTHING` (duplicates) → `seeded=0` → `daily_pipeline_runs.candidates_seeded=0`. This creates a false "no candidates seeded" signal in monitoring. Additionally, if `options_structure_scan` has no rows for today's exact date at seed time (because the OSS scan runs after 09:40), the seed returns 0 candidates with no fallback.

### Fix Applied (aiem_options_scheduler.py)

Three changes in `seed_daily_candidates`:

**Change 1 — ET-aware date in seed and worker:**
```python
# Before:
scan_date = scan_date or date.today()

# After:
scan_date = scan_date or datetime.now(_ET).date()
```
Applied in both `seed_daily_candidates` (line ~442) and `run_pipeline_worker` (line ~2653).

**Change 2 — MAX(scan_date) fallback when today's OSS has 0 rows:**
```python
if not candidates:
    log.warning(f"[seed] 0 eligible OSS rows for scan_date={scan_date}; "
                f"retrying with MAX(scan_date) fallback")
    cur.execute("""
        SELECT o.ticker, o.scan_date, o.pc_skew_pp, o.gex_regime, o.pc_skew_tag
        FROM options_structure_scan o
        JOIN polygon_market_daily p
            ON p.ticker = o.ticker
           AND p.scan_date = (SELECT MAX(scan_date) FROM polygon_market_daily)
        WHERE o.scan_date = (SELECT MAX(scan_date) FROM options_structure_scan)
          AND o.pc_skew_pp IS NOT NULL AND o.front_iv > 0 AND o.spot > 10
        ORDER BY o.pc_skew_pp DESC LIMIT %s
    """, (limit,))
    candidates = cur.fetchall()
```

**Change 3 — Accurate candidates_seeded when all are pre-seeded duplicates:**
```python
if seeded == 0 and dupes > 0:
    cur.execute(
        "SELECT COUNT(*) FROM options_pipeline_jobs "
        "WHERE scan_date=%s AND status='PENDING'", (scan_date,)
    )
    _pre_pending = cur.fetchone()[0] or 0
    if _pre_pending > 0:
        log.info(f"[seed] all {dupes} were duplicates; {_pre_pending} PENDING already exist")
        seeded = _pre_pending
```

### Evidence — Fallback Query Returns Non-Zero Rows on Known-Good Data

```sql
SELECT o.ticker, o.scan_date, o.pc_skew_pp, o.gex_regime
FROM options_structure_scan o
JOIN polygon_market_daily p
    ON p.ticker = o.ticker
   AND p.scan_date = (SELECT MAX(scan_date) FROM polygon_market_daily)
WHERE o.scan_date = (SELECT MAX(scan_date) FROM options_structure_scan)
  AND o.pc_skew_pp IS NOT NULL AND o.front_iv > 0 AND o.spot > 10
ORDER BY o.pc_skew_pp DESC LIMIT 8;
```

```
DG|2026-07-23|165.51|LONG_GAMMA
UPS|2026-07-23|108.68|SHORT_GAMMA
HUM|2026-07-23|91.99|NEAR_FLIP
DOCU|2026-07-23|79.67|NEAR_FLIP
DUOL|2026-07-23|51.96|LONG_GAMMA
GDDY|2026-07-23|48.76|NEAR_FLIP
BMY|2026-07-23|42.14|SHORT_GAMMA
PG|2026-07-23|39.56|SHORT_GAMMA
```

8 rows returned. The fallback resolves the empty-seed failure. If today's OSS scan hasn't run yet at 09:40, the seed now falls back to the most recent available scan date rather than seeding 0 candidates.

---

## Item 3 — OE Weekend SCHEDULED Bug

### Fix Applied (aiem_options_scheduler.py, Step 0 of main())

```python
# Before:
_today_et = date.today()
with psycopg2.connect(...) as _sc0, ...:
    _cu0.execute("""INSERT INTO daily_pipeline_runs ...""", (_today_et,))
    _sc0.commit()

# After:
_now_et0  = datetime.now(_ET)
_today_et = _now_et0.date()
if _now_et0.weekday() >= 5:  # 5=Sat, 6=Sun
    log.info(f"[startup] daily_pipeline_runs: skipping SCHEDULED insert — "
             f"today is {'Saturday' if _now_et0.weekday()==5 else 'Sunday'} "
             f"(not a trading day)")
else:
    with psycopg2.connect(...) as _sc0, ...:
        _cu0.execute("""INSERT INTO daily_pipeline_runs ...""", (_today_et,))
        _sc0.commit()
    log.info(f"[startup] daily_pipeline_runs: SCHEDULED registered for {_today_et}")
```

### Test — Saturday and Sunday No Longer Create SCHEDULED Rows

```python
import pytz
from datetime import datetime
_ET = pytz.timezone('America/New_York')

test_cases = [
    ('2026-07-25 09:40', 'Saturday'),
    ('2026-07-26 09:40', 'Sunday'),
    ('2026-07-28 09:40', 'Monday'),
    ('2026-07-27 23:50', 'Monday-late'),
]
for ts, label in test_cases:
    dt = _ET.localize(datetime.strptime(ts, '%Y-%m-%d %H:%M'))
    would_insert = dt.weekday() < 5
    print(f'{label} ({ts}) weekday={dt.weekday()} would_insert_SCHEDULED={would_insert}')
```

```
Saturday (2026-07-25 09:40) weekday=5 would_insert_SCHEDULED=False
Sunday (2026-07-26 09:40) weekday=6 would_insert_SCHEDULED=False
Monday (2026-07-28 09:40) weekday=1 would_insert_SCHEDULED=True
Monday-late (2026-07-27 23:50) weekday=0 would_insert_SCHEDULED=True
```

### Live Confirmation — Scheduler Startup on Sunday 2026-07-26

From `options-pipeline-scheduler` workflow log (20:56:33 UTC):
```
[2026-07-26T20:56:33Z INFO] [startup] daily_pipeline_runs: skipping SCHEDULED insert —
    today is Sunday (not a trading day)
```

Saturday and Sunday no longer create erroneous SCHEDULED rows. The erroneously created Jul-25 SCHEDULED row remains in the DB (data immutability — no deletes without approval); it will have no operational effect since the pipeline's CronTriggers are already mon-fri guarded.

---

## Item 4 — Stage 11 (aiem_v3_discovery) Silent Exception Swallow

### Fix Applied (main.py, `_aiem_paper_pick_candidates`)

```python
# Before:
    except Exception as _v3e:
        print(f"[aiem_paper] v3_discovery source skipped: {_v3e}")

# After:
    except Exception as _v3e:
        import traceback as _v3_tb
        _v3_msg = f"{type(_v3e).__name__}: {_v3e}"
        print(f"[aiem_paper] v3_discovery FAILED: {_v3_msg}")
        print(_v3_tb.format_exc())
        # Surface in job_heartbeats so daily status check catches it
        try:
            import psycopg2 as _v3_pg2
            with _v3_pg2.connect(_DB_URL, connect_timeout=3) as _v3c, _v3c.cursor() as _v3cu:
                _v3cu.execute("""
                    INSERT INTO job_heartbeats
                        (job_name, last_attempt, last_error, consecutive_failures)
                    VALUES ('aiem_paper_v3_discovery', NOW(), %s, 1)
                    ON CONFLICT (job_name) DO UPDATE
                        SET last_attempt=NOW(),
                            last_error=EXCLUDED.last_error,
                            consecutive_failures=job_heartbeats.consecutive_failures + 1
                """, (_v3_msg[:2000],))
                _v3c.commit()
        except Exception:
            pass
```

### Forced-Failure Test — Confirmed Visible in job_heartbeats

```python
_v3_msg = 'ModuleNotFoundError: FORCED_TEST_no_such_module'
# [writes to job_heartbeats via new except block logic]

# SELECT result:
job_name: aiem_paper_v3_discovery
last_attempt_et: 2026-07-27 00:56:06
last_error: ModuleNotFoundError: FORCED_TEST_no_such_module
consecutive_failures: 1
VISIBLE_IN_JOB_HEARTBEATS: YES
```

Any future v3_discovery exception will now appear in `job_heartbeats` under `job_name='aiem_paper_v3_discovery'` with the full exception class + message and an incrementing `consecutive_failures` counter. The Telegram notifier's job health watchdog (every 30 min) polls `job_heartbeats` — it will surface this failure on the daily status check rather than silently dropping it to stdout.

---

## Item 5 — Layer9 Scorer Staleness (OPEN — No Fix This Round)

**Status:** OPEN. Fix deferred per directive.

**Finding:** All `layer9_scores` entries for Jul 21–25 were computed between 18:25 and 23:55 ET (after market close). The `_stage4_execution_revalidate` check requires `computed_at < 6h`. At 10 AM ET trading time, all scores are 10–16 hours old → always stale → `layer9_stat` source has contributed 0 candidates in all live production runs since deployment.

**Three options for Joel's decision (no recommendation — present for choice):**

**Option A — Move compute to pre-market window (5:00–8:30 AM ET)**
- Change the APScheduler CronTrigger from every 2 hours to add an explicit pre-market pass at 5:30 AM ET
- Scores would be fresh (≤5h) by 10 AM ET and pass the staleness gate
- No change to the staleness threshold or source removal required
- Risk: pre-market data coverage is lower than EOD; VPIN/Amihud computed on thinner volume

**Option B — Widen the staleness threshold from 6h to 18h**
- Change `computed_at < 6h` to `computed_at < 18h` in `_stage4_execution_revalidate`
- Previous evening's scores (19:00 ET) would be accepted at 10 AM the next trading day (13h gap)
- Zero compute schedule changes; existing 2-hour job is sufficient
- Risk: scores based on prior day's EOD are 14–16h old at execution; valid signals but not truly "fresh"

**Option C — Remove layer9_stat as a source**
- Delete the `layer9_stat` entry from `_RV_DB_SRCS` and the revalidation dispatch
- No candidates means no operational impact (source is already contributing nothing)
- The underlying layer9_scores table and bg scanner continue running for research/AI Short Calls use
- Risk: forfeits a validated signal source; recoverable later if Option A or B is implemented

---

## Real-Test Benchmark

**Tomorrow's 09:40/09:45 ET Mon-Fri run is the confirming test.** Not this document.

On Monday morning, check:
```sql
-- Was a SCHEDULED row created? (should be YES on Monday)
SELECT status, to_char(created_at AT TIME ZONE 'America/New_York','HH24:MI') AS created_et
FROM daily_pipeline_runs WHERE run_date = CURRENT_DATE;

-- Did seed produce non-zero candidates?
SELECT candidates_seeded, status, started_at AT TIME ZONE 'America/New_York'
FROM daily_pipeline_runs WHERE run_date = CURRENT_DATE;

-- Did v3_discovery produce decisions?
SELECT COUNT(*), decision FROM aiem_decision_history
WHERE decision_date = CURRENT_DATE GROUP BY 2;

-- Was job_heartbeats aiem_paper_v3_discovery clean (no failures)?
SELECT last_error, consecutive_failures FROM job_heartbeats
WHERE job_name = 'aiem_paper_v3_discovery';
```

If it still fails, report which of the 4 fixes held and which didn't. The watchdog (GH Actions, 9:45/10:30/11:15 AM ET) fires independently — it will retry even if the primary 9:35 trigger misses.
