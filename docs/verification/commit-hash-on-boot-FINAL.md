# Commit-Hash-on-Boot — Permanent Verification Record
**All 5 evidence-gap items closed:** 2026-07-30T14:50Z UTC / 10:50 ET  
**Directive file:** `attached_assets/Pasted--Directive-CommitHashOnBoot-EvidenceGaps-2026-07-30-...txt`  
**Implementation commit:** `79806a729c627f7aeb688f6092a95d74f41edf6a`

---

## One-shot query: "Is this process running what's on disk?"

```bash
bash tools/check_scheduler_drift.sh
# exit 0 = MATCH, exit 1 = STALE, exit 2 = error

# Or directly:
curl -sf http://localhost:5053/health | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d["boot_commit"], d["disk_commit"], d["commit_match"])'
```

**DB query (no live process needed):**
```sql
SELECT id, process_name, pid, git_sha, started_at
FROM process_lifecycle_log
WHERE process_name = 'aiem_options_scheduler'
ORDER BY id DESC LIMIT 5;
```

---

## Item 1 — Raw code for every behavior claim

### `if _dsk != _BOOT_COMMIT:` — lines 487–522

```python
    # ── Commit-drift alert (Step 3) ──────────────────────────────────────────
    # Fires at most once per process lifetime, only after a 15-min grace period
    # so normal deploy+immediate-restart cycles don't produce noise.
    global _DRIFT_ALERT_SENT
    if not _DRIFT_ALERT_SENT and _BOOT_COMMIT != "UNKNOWN":
        try:
            _dsk = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if _dsk != _BOOT_COMMIT:
                _drift_secs = (datetime.utcnow() - _BOOT_TIME).total_seconds()
                if _drift_secs >= 900:  # 15-minute grace
                    _tg(
                        f"🔴 <b>SCHEDULER RUNNING STALE CODE</b>\n"
                        f"Running : <code>{_BOOT_COMMIT[:12]}</code>\n"
                        f"On-disk : <code>{_dsk[:12]}</code>\n"
                        f"Process started {round(_drift_secs / 60)}m ago — code on disk has changed.\n"
                        f"⚠️ <b>Restart the options-pipeline-scheduler workflow to load new code.</b>"
                    )
                    _DRIFT_ALERT_SENT = True
                    log.warning(
                        f"[drift] STALE: running={_BOOT_COMMIT[:12]} "
                        f"disk={_dsk[:12]} drift={round(_drift_secs/60)}m "
                        f"— Telegram alert sent"
                    )
                else:
                    log.info(
                        f"[drift] mismatch detected (running={_BOOT_COMMIT[:12]} "
                        f"disk={_dsk[:12]}) drift={round(_drift_secs/60,1)}m "
                        f"— within 15-min grace, no alert yet"
                    )
        except Exception as _da_e:
            log.debug(f"[drift] check failed: {_da_e}")
```

### `_DRIFT_ALERT_SENT` declaration — line 3036

```python
_DRIFT_ALERT_SENT = False   # fires at most once per process lifetime (Step 3)
```

Flag is set `True` at line 509 inside the `_drift_secs >= 900` branch, after `_tg()` returns. The outer `if not _DRIFT_ALERT_SENT` at line 491 gates the entire block on every subsequent call for the process lifetime.

---

## Item 2 — Both checks run through `verified_run.sh`

### STALE run (SEQ=162, exit_code=1)

```
$ bash tools/verified_run.sh "bash tools/check_scheduler_drift.sh"

[dpl_chain] archive=verified_run_162.log  archive_sha256=bdc1073d8dcf26d0...
[dpl_chain] SEQ=162 entry_hash=02c0dbf025c89efe...
--- verified_run: entry #112 logged ---
command:      bash tools/check_scheduler_drift.sh
exit_code:    1
output_sha256: facfae99593f4e37ecb475ea3ce0c27cb6d2f8a849152cbf4a405e30bf8ba248
entry_hash:   352fa0f1fba81d3543b98e094fb11c9def308743551ad2050f363e2529005125

  [POST-SEAL PASS] PSV1_archive_exists
  [POST-SEAL PASS] PSV2_archive_sha_matches_index
    live_sha=bdc1073d8dcf26d002584ca5b091e711b36f4e90b158cea98748e95fe49efca6
    index_sha=bdc1073d8dcf26d002584ca5b091e711b36f4e90b158cea98748e95fe49efca6
  [POST-SEAL PASS] PSV3_chain_entry_exists_for_seq
  [POST-SEAL PASS] PSV4_archive_sha256_3way_binding
    live_archive_sha=bdc1073d8dcf26d002584ca5b091e711b36f4e90b158cea98748e95fe49efca6
    chain_archive_sha=bdc1073d8dcf26d002584ca5b091e711b36f4e90b158cea98748e95fe49efca6
  [POST-SEAL PASS] PSV5_chain_entry_hash_recomputes
  [POST-SEAL PASS] PSV6_prev_hash_continuity
  [POST-SEAL PASS] PSV7_exit_status_matches_archive
  [POST-SEAL FAIL] PSV8_pass_fail_totals_in_archive -- SUMMARY: line not found in archive
  [POST-SEAL PASS] PSV9_cmd_matches_archive

POST-SEAL SUMMARY: 8 PASS  1 FAIL
--- raw output follows ---
RUNNING : 79806a729c627f7aeb688f6092a95d74f41edf6a
ON-DISK : 5c6c8aeef8d662bbbe2b544dbeab8a6ae18a2bec
STATUS  : STALE — PROCESS IS RUNNING OLD CODE, RESTART REQUIRED
DRIFT   : 9.9 minutes since process last started
```

### MATCH run (SEQ=163, exit_code=0)

```
$ bash tools/verified_run.sh "bash tools/check_scheduler_drift.sh"

[dpl_chain] archive=verified_run_163.log  archive_sha256=1b29ca5dd0092cc5...
[dpl_chain] SEQ=163 entry_hash=87d405e479f52119...
--- verified_run: entry #113 logged ---
command:      bash tools/check_scheduler_drift.sh
exit_code:    0
output_sha256: da08f60e893344d9a0bbcb0cfb8979ec57cdf02f5bb1835c7b2a248eeff7a21b
entry_hash:   bc80bb7d9fad0a767baa245359f2d980940b7203bfee091f9985d256badd17ac

  [POST-SEAL PASS] PSV1_archive_exists
  [POST-SEAL PASS] PSV2_archive_sha_matches_index
    live_sha=1b29ca5dd0092cc5d31c28fc25a37e8f46977e3cac7988b85f3f2a5156ebfb2c
    index_sha=1b29ca5dd0092cc5d31c28fc25a37e8f46977e3cac7988b85f3f2a5156ebfb2c
  [POST-SEAL PASS] PSV3_chain_entry_exists_for_seq
  [POST-SEAL PASS] PSV4_archive_sha256_3way_binding
    live_archive_sha=1b29ca5dd0092cc5d31c28fc25a37e8f46977e3cac7988b85f3f2a5156ebfb2c
    chain_archive_sha=1b29ca5dd0092cc5d31c28fc25a37e8f46977e3cac7988b85f3f2a5156ebfb2c
  [POST-SEAL PASS] PSV5_chain_entry_hash_recomputes
  [POST-SEAL PASS] PSV6_prev_hash_continuity
  [POST-SEAL PASS] PSV7_exit_status_matches_archive
  [POST-SEAL FAIL] PSV8_pass_fail_totals_in_archive -- SUMMARY: line not found in archive
  [POST-SEAL PASS] PSV9_cmd_matches_archive

POST-SEAL SUMMARY: 8 PASS  1 FAIL
--- raw output follows ---
RUNNING : 5c6c8aeef8d662bbbe2b544dbeab8a6ae18a2bec
ON-DISK : 5c6c8aeef8d662bbbe2b544dbeab8a6ae18a2bec
STATUS  : MATCH — process is running current code
```

**PSV8 status on both runs:** `check_scheduler_drift.sh` does not output a `SUMMARY:` line. PSV8 checks for that pattern. PSV1–PSV7 and PSV9 pass on both runs.

**`verify_chain.sh` note:** `artifacts/stock-scanner-api/verify_chain.sh` (ca7896c7) verifies the options-pipeline DB hash chain for a specific trade record — it is not the `verified_run.sh` evidence chain. Its `OVERALL: FAIL` at the time of this run is a pre-existing stage 1_polygon hash mismatch in the options pipeline, unrelated to CommitHashOnBoot. The `verified_run.sh` evidence chain continuity is validated by PSV4+PSV5+PSV6 in the output above.

---

## Item 3 — sha256 cross-check

```
$ sha256sum tools/verified_run.sh artifacts/stock-scanner-api/verify_chain.sh
dce94f6e19dfc5c7952ab9eee7015b7eb10c3ff1e0ca60263279658ab166f826  tools/verified_run.sh
ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f  artifacts/stock-scanner-api/verify_chain.sh
```

Canonical on file (memory — `verified-run-canonical.md`, `pe-chain-wrapper.md`):
- `tools/verified_run.sh`: canonical = `dce94f6e…` → **MATCHES**
- `artifacts/stock-scanner-api/verify_chain.sh`: canonical = `ca7896c7c832ef53…` → **MATCHES**

---

## Item 4 — Raw SQL output for process_lifecycle_log id=35

```python
cur.execute('SELECT * FROM process_lifecycle_log WHERE id=35')
```

```
columns: ['id', 'process_name', 'pid', 'git_sha', 'started_at', 'exited_at', 'exit_code', 'exit_reason']
row: (35, 'aiem_options_scheduler', 12829, '561dd9a8132326b6d2d908fe9d2c1b71e5720ad1', datetime.datetime(2026, 7, 30, 14, 36, 41, 393923, tzinfo=datetime.timezone.utc), None, None, None)
```

---

## Item 5 — Negative control: raw log evidence of branch skip

The code at line 499 is `if _dsk != _BOOT_COMMIT:`. There is no log statement in the matching case — execution falls through to `return` at line 524. There is no affirmative "hashes match" log line in the code.

**Raw log — 14:40:00Z `recover_stale_jobs` run (hashes matched; disk=`79806a729c62`, boot=`79806a729c62`):**

```
[2026-07-30T14:40:00Z INFO] Running job "recover_stale_jobs (trigger: cron[minute='*/5'], next run at: 2026-07-30 14:40:00 UTC)" (scheduled at 2026-07-30 14:40:00+00:00)
[2026-07-30T14:40:00Z INFO] Job "recover_stale_jobs (trigger: cron[minute='*/5'], next run at: 2026-07-30 14:45:00 UTC)" executed successfully
```

No `[drift]` line appears between those two entries.

**Raw log — 14:45:00Z run (hashes mismatched; git-autosync pushed `5c6c8aeef8d6` at 14:41:23Z):**

```
[2026-07-30T14:45:00Z INFO] Running job "recover_stale_jobs (trigger: cron[minute='*/5'], next run at: 2026-07-30 14:50:00 UTC)" (scheduled at 2026-07-30 14:45:00+00:00)
[2026-07-30T14:45:00Z INFO] [drift] mismatch detected (running=79806a729c62 disk=5c6c8aeef8d6) drift=5.8m — within 15-min grace, no alert yet
[2026-07-30T14:45:00Z INFO] Job "recover_stale_jobs (trigger: cron[minute='*/5'], next run at: 2026-07-30 14:50:00 UTC)" executed successfully
```

The `[drift]` line appears in the mismatch run and is absent in the match run. There is no deeper direct evidence of the skip because the code does not log on the match path.

---

## Files

| File | sha256 |
|---|---|
| `artifacts/stock-scanner-api/aiem_options_scheduler.py` | `cb2cfb254cb1c16272db4f7201b0aacdceaa96fe3b088a8663148906fc7eb455` |
| `tools/check_scheduler_drift.sh` | `9c43eeacbfd2f2bedcf6d267a33e95352b5458c934d071dfc50dbc086970ab61` |

**verified_run.sh evidence chain entries:** SEQ=162 (STALE, exit=1), SEQ=163 (MATCH, exit=0). Both archived and chain-validated by PSV1–PSV7, PSV9.
