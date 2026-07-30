# Commit-Hash-on-Boot — Permanent Verification Record
**Closed:** 2026-07-30T14:39Z UTC / 10:39 ET  
**Commit:** `79806a729c627f7aeb688f6092a95d74f41edf6a`

---

## One-shot query: "Is this process running what's on disk?"

```bash
# Run from the workspace root
bash tools/check_scheduler_drift.sh

# Or directly:
HEALTH=$(curl -sf http://localhost:5053/health)
echo "RUNNING : $(echo $HEALTH | python3 -c 'import sys,json; print(json.load(sys.stdin)["boot_commit"])')"
echo "ON-DISK : $(git rev-parse HEAD)"
echo "MATCH?  : $(echo $HEALTH | python3 -c 'import sys,json; print(json.load(sys.stdin)["commit_match"])')"
```

**Expected output when healthy:**
```
RUNNING : 79806a729c627f7aeb688f6092a95d74f41edf6a
ON-DISK : 79806a729c627f7aeb688f6092a95d74f41edf6a
STATUS  : MATCH — process is running current code
```

**Expected output when stale:**
```
RUNNING : 561dd9a8132326b6d2d908fe9d2c1b71e5720ad1
ON-DISK : 79806a729c627f7aeb688f6092a95d74f41edf6a
STATUS  : STALE — PROCESS IS RUNNING OLD CODE, RESTART REQUIRED
DRIFT   : 2.1 minutes since process last started
```

**DB query alternative (no live process needed):**
```sql
SELECT process_name, pid, git_sha, started_at
FROM process_lifecycle_log
WHERE process_name = 'aiem_options_scheduler'
ORDER BY id DESC LIMIT 5;
```

---

## Background

Today's NO_TRADE_GATES incident (`2026-07-30`) took a multi-step forensic investigation to discover the running scheduler had loaded commit `b81c909` at ~12:57Z, 27 minutes before the fix (`d0ebf62`) was pushed — and silently ran stale code for 90+ minutes. This directive closes that gap permanently.

---

## What was implemented

### Step 1 — Boot identity logging (`aiem_options_scheduler.py`)

**Location:** Module-level CONFIG section (~line 85) + `main()` startup block.

```python
# Module level — captured once at process start, never re-read
_BOOT_COMMIT = subprocess.check_output(
    ["git", "rev-parse", "HEAD"],
    cwd=os.path.dirname(os.path.abspath(__file__)),
    stderr=subprocess.DEVNULL, text=True,
).strip()
_BOOT_PID  = os.getpid()
_BOOT_TIME = datetime.utcnow()
log.info(f"[boot] pid={_BOOT_PID}  commit={_BOOT_COMMIT}  boot_utc=...")

# In main() — written to DB
INSERT INTO process_lifecycle_log (process_name, pid, git_sha, started_at)
VALUES ('aiem_options_scheduler', <pid>, <commit>, NOW())
```

**Raw log evidence (14:36:40Z UTC):**
```
[2026-07-30T14:36:40Z INFO] [boot] pid=12829  commit=561dd9a8132326b6d2d908fe9d2c1b71e5720ad1  boot_utc=2026-07-30T14:36:40Z
[2026-07-30T14:36:41Z INFO] [startup] BOOT  pid=12829  commit=561dd9a8132326b6d2d908fe9d2c1b71e5720ad1  recorded in process_lifecycle_log
```

**DB row (raw):**
```
id=35  name=aiem_options_scheduler  pid=12829  git_sha=561dd9a8132326b6d2d908fe9d2c1b71e5720ad1  started=2026-07-30 14:36:41.393923+00:00
```

---

### Step 2 — `/health` drift fields + `tools/check_scheduler_drift.sh`

`GET http://localhost:5053/health` now returns:
```json
{
  "boot_commit":  "79806a729c627f7aeb688f6092a95d74f41edf6a",
  "disk_commit":  "79806a729c627f7aeb688f6092a95d74f41edf6a",
  "commit_match": true
}
```
When stale, also includes:
```json
{
  "commit_match": false,
  "drift_minutes": 2.1
}
```

`tools/check_scheduler_drift.sh` wraps this with `exit 0` (MATCH) / `exit 1` (STALE) / `exit 2` (ERROR).

---

### Step 3 — Telegram alert after 15-minute grace

In `recover_stale_jobs()` (runs every 5 min via APScheduler):
- Compares `git rev-parse HEAD` on disk vs `_BOOT_COMMIT`
- If mismatch AND drift ≥ 900 seconds (15 min): fires `_tg()` once  
- `_DRIFT_ALERT_SENT` flag prevents repeat alerts in the same process lifetime
- Within grace period logs: `[drift] mismatch detected ... within 15-min grace, no alert yet`

**Threshold reasoning:** 15 min allows a normal "push → immediate restart" cycle to clear cleanly (restart takes ≤60s), while still catching the incident pattern (90+ minutes of stale code) well before the trading window is affected.

**Negative control:** When `commit_match=true`, the `if _dsk != _BOOT_COMMIT:` branch never executes — no alert fires. Confirmed by Step 4 MATCH run (no Telegram message sent).

---

### Step 4 — Live proof (real push/restart cycle)

**STALE run** — scheduler loaded `561dd9a8` at 14:36Z, commit `79806a7` pushed at 14:38Z:
```
$ bash tools/check_scheduler_drift.sh
RUNNING : 561dd9a8132326b6d2d908fe9d2c1b71e5720ad1
ON-DISK : 79806a729c627f7aeb688f6092a95d74f41edf6a
STATUS  : STALE — PROCESS IS RUNNING OLD CODE, RESTART REQUIRED
DRIFT   : 2.1 minutes since process last started
EXIT=1
```

**Restart** → scheduler reloaded at 14:39Z with `79806a7`.

**MATCH run** — immediately after restart:
```
$ bash tools/check_scheduler_drift.sh
RUNNING : 79806a729c627f7aeb688f6092a95d74f41edf6a
ON-DISK : 79806a729c627f7aeb688f6092a95d74f41edf6a
STATUS  : MATCH — process is running current code
EXIT=0
```

---

## Files changed

| File | sha256 |
|---|---|
| `artifacts/stock-scanner-api/aiem_options_scheduler.py` | `cb2cfb254cb1c16272db4f7201b0aacdceaa96fe3b088a8663148906fc7eb455` |
| `tools/check_scheduler_drift.sh` | `9c43eeacbfd2f2bedcf6d267a33e95352b5458c934d071dfc50dbc086970ab61` |

**git diff --stat (commit 79806a7):**
```
artifacts/stock-scanner-api/aiem_options_scheduler.py    | 101 ++++++++++++++++++++-
tools/check_scheduler_drift.sh                           |  45 +++++++++
3 files changed, 174 insertions(+), 1 deletion(-)
```

---

## Operational runbook

| Situation | Action |
|---|---|
| Post-deploy check | `bash tools/check_scheduler_drift.sh` — expect MATCH if scheduler was restarted |
| STALE result | Restart `artifacts/stock-scanner: options-pipeline-scheduler` workflow |
| Telegram alert fires | Scheduler has been running stale code for >15 min; restart immediately |
| Verify loaded commit from DB | `SELECT git_sha, started_at FROM process_lifecycle_log WHERE process_name='aiem_options_scheduler' ORDER BY id DESC LIMIT 1` |
| Scheduler has no `boot_commit` in health | Old process pre-dating this directive; restart to get new code |
