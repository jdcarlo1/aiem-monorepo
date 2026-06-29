---
name: Flask thread saturation from background scan storm
description: How concurrent endpoint calls on fresh startup can exhaust OS thread limit and hang all subsequent requests — even pure DB endpoints.
---

## The Rule
Never run smoke tests or hit multiple scan-triggering endpoints simultaneously right after a server restart before the startup preload has had time to complete. Thread count >~80 will starve new Flask requests.

## Why
Flask `threaded=True` creates one OS thread per request. Scan endpoints (unusual-calls, convergence, etc.) spawn their own background ThreadPoolExecutor threads (for Tradier/yfinance). When cache is empty (fresh restart), every call to a scan endpoint spawns a full background scan. With many concurrent scans, each using ThreadPoolExecutor with their own worker pool, the OS thread count can reach 100-120+ quickly. At that point, Flask cannot create new threads for incoming requests — they queue in the TCP backlog and curl reports `000` (connection accepted but no response ever arrives). Even pure DB endpoints (eod-sweeps, etf-calls, gamma-pressure, outcomes) appear to "hang" when they're actually just waiting for a thread slot.

## How to Apply
- Always wait 8-10s after server restart for startup preload to warm caches from DB
- Run smoke tests sequentially (not in parallel bursts) to avoid triggering multiple scan storms
- If thread count > 80 (check with `ls /proc/$PID/task | wc -l`), restart the server before testing
- Diagnostic: `000` HTTP codes on pure-DB endpoints = thread exhaustion (not DB problem); confirmed by thread count check
- Root fix: all scan-spawning endpoints should check `_intraday_scan_allowed()` before starting background ThreadPoolExecutor scans (weekends = DB-only mode, no scans)
