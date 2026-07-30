# Item 4 — main.py SHA-256 Before/After: Sentinel Heartbeat Fix
**Date:** 2026-07-30T21:30Z UTC / 2026-07-30 17:30 ET
**Directive:** Open Items 3–7 Closeout, 2026-07-30

---

## Commit

```
f0b2375cd905e2ef4848f6efe298681addbcf9f8
2026-07-30T13:48:22Z UTC / 2026-07-30 09:48 ET

"Item 4: sentinel heartbeat before nightly os._exit(0); add shutdown_reason col"
```

Files changed: `artifacts/stock-scanner-api/main.py` (+23 insertions, 0 deletions)

---

## Raw SHA-256 Evidence

Measured with `git show <commit>:artifacts/stock-scanner-api/main.py | sha256sum`:

```
=== BEFORE (parent commit — state of main.py before f0b2375) ===
935c13a4bfc38c50358f603024e9f3c7ea2c58c2dd34e050f07bfec1a1d7d1de  -

=== AFTER (f0b2375 — sentinel heartbeat + shutdown_reason col) ===
3b764b22c5ab65d3951ace8a55ca8719fd3ce6c4db224372b2af5fce13f3c28f  -
```

---

## What Changed

Two additions to `artifacts/stock-scanner-api/main.py`:

1. **`ALTER TABLE aiem_process_heartbeat ADD COLUMN IF NOT EXISTS shutdown_reason TEXT`** —
   executed at startup (in the schema-init block). Idempotent via `IF NOT EXISTS`.

2. **Sentinel write before `os._exit(0)` in `_nightly_memory_reset()`** (near line 17506):
   ```python
   # Write sentinel row before exiting so monitors can distinguish deliberate
   # nightly reset from OOM/crash (crashes write no row).
   _pg_sentinel = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=3)
   with _pg_sentinel, _pg_sentinel.cursor() as _cur_s:
       _cur_s.execute(
           "INSERT INTO aiem_process_heartbeat (ts, pid, shutdown_reason) "
           "VALUES (NOW(), 0, 'nightly_memory_reset')",
       )
   _pg_sentinel.close()
   os._exit(0)
   ```
   `pid=0` is the sentinel marker (no real process uses pid 0). `shutdown_reason=
   'nightly_memory_reset'` distinguishes a deliberate exit from an OOM crash (which
   writes no row).

---

## Live Proof (standalone test run, same session)

```
id=3185  ts=2026-07-30T13:42:09Z  pid=0  shutdown_reason='nightly_memory_reset'
```

Row exists in `aiem_process_heartbeat` with the exact sentinel values. The test ran the
production-identical code path against the real DB. No row of this type existed before the test.
Rows are not deleted per standing Data Immutability Rule; this row remains as permanent evidence.

---

## Interpretation for Monitoring

| Row pattern | Meaning |
|---|---|
| `pid > 0` rows with ~3-minute cadence | Process is alive and running normally |
| `pid = 0, shutdown_reason = 'nightly_memory_reset'` | Deliberate 3 AM ET exit — expected |
| Gap with no sentinel row | Unexpected crash or OOM (crash wrote no row) |

The dark window between the sentinel row and the first morning heartbeat is by design: the
process is intentionally not running during that period.
