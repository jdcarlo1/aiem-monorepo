## SEQ chain discontinuity

The SEQ counter has passed through three implementations:

| Era | Commit | Committed (UTC) | State location |
|-----|--------|-----------------|----------------|
| 1 | 339cce1 | 2026-07-19T04:30:48Z | `/tmp/portfolio_engine_verify_seq` — resets on VM restart |
| 2 | 333c964 | 2026-07-19T14:43:41Z | `verified_run_last.log` at SCRIPT_DIR (derived via `grep -m1 "^SEQ="`) |
| 3 | 9d3b41a | 2026-07-19T14:54:26Z | `tools/verified_run_seq` (workspace-durable, current) |

SEQ=1 (14:33Z) and SEQ=2 (14:34Z) ran under era-1 (339cce1, `/tmp`-backed; resets on VM restart).
SEQ=3 (14:51:15Z) ran under era-2 (333c964, LOG_FILE-derived; durable across restarts but derived, not stored directly).
SEQ=4+ run under era-3 (workspace-durable `tools/verified_run_seq`).

The prior statement "prior to SEQ=3 used a `/tmp`-backed counter" was incorrect: era-2 (in effect at 14:43:41Z–14:54:26Z, covering SEQ=3) used `verified_run_last.log`, which persists across VM restarts. Only era-1 (SEQ=1 and SEQ=2) used `/tmp`.

Authoritative ordering of all runs uses TS_END (UTC) from the run log.
