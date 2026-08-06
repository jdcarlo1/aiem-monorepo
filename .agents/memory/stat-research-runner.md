---
name: Stat research runner — workflow wiring
description: aiem_stat_research_runner.py status — was never persistent, now has workflow + health server
---

## Status (fixed 2026-08-04)
`aiem_stat_research_runner.py` now has a registered workflow and runs continuously.

**What was done:**
- Added `_start_health_server()` to the file: `ThreadingHTTPServer` on `STAT_RESEARCH_PORT` (5057), `GET /health` → 200 OK
- Added `[[services]]` block `stat-research-runner` to `artifacts/stock-scanner/.replit-artifact/artifact.toml` via `verifyAndReplaceArtifactToml`
- Workflow `artifacts/stock-scanner: stat-research-runner` created and started
- SHA of file post-fix: e2fc7ffa5b5679a780d422bbf2a1650f2656c5a9f4339e78f7affc50d0a52529

**Why:**
Dead zone in main.py prevented any continuous loop from auto-starting there. This runner must be its own process. Without a workflow it never ran — the Jul 25 entries in `aiem_historical_pattern_grid` were from a one-time e2e test.

**How to apply:**
Any new background-only Python loop that needs to run 24/7 must be its own service in artifact.toml with a health server thread. Use `ThreadingHTTPServer` on a free port (next available after 5057 is 5058). Add `_start_health_server()` as the first call in `main()`.

**Tables written:**
- `aiem_grid_test_state` — EOD multi-day indicator signals (primary)
- `aiem_intraday_grid_state` — same-day premarket/first-candle signals
- `aiem_historical_pattern_grid` — long-horizon historical backtest results (runs at startup + weekly)
