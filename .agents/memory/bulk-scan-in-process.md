---
name: Bulk scan must run in-process
description: Why long bulk yfinance scans must run inside the always-on stock-api process, not detached scripts
---

Long/bulk scans over the full ticker universe (~6,610) must run INSIDE the always-on stock-api Flask process — spawn a daemon thread from a trigger endpoint — not as a standalone/detached script.

**Why:**
- Fresh python processes get `YFRateLimitError` on bulk yfinance pulls, while the running stock-api fetches fine (steadier pacing / warmed session).
- Detached background jobs (`nohup … &`) are reaped between agent tool calls — the child is killed when the bash call returns, so the job never finishes and its in-memory buffer is lost (observed: empty log, process gone next turn).

**How to apply:**
- Pattern: `composite_scan.py` + `POST /stock-api/composite-scan/trigger` (daemon thread) + `/status` + `/composite-leaderboard`. Restart the stock-api workflow after editing, then trigger via `curl localhost:5050/...` and poll status.
- Persist incrementally with batched UPSERT on a unique key (e.g. UNIQUE(scan_date,ticker)) so partial progress is queryable and survives a restart. Never DELETE-then-rebuild (leaves an empty window mid-run).
- Single-flight guard: set `running=True` under a lock BEFORE spawning the thread so a spammed/duplicate trigger can't launch a second scan (this is also the proportionate DoS mitigation, matching the app's other unauthenticated trigger endpoints).
- Keep concurrency modest (~6 workers) + jittered backoff so the bulk job doesn't degrade the live API's own yfinance calls.
- Throughput: full 6,610 composite scan finishes in ~5 min main pass; illiquid stragglers retry serially afterward.
