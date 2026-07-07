---
name: AIEM v3 engine architecture
description: 5-file autonomous intelligence engine; key API names, scheduler times, wire points
---

## Files
- `aiem_v3_discovery.py` — Phase 3; run_discovery(db, top_n) + get_todays_discoveries(db)
- `aiem_v3_technical.py` — Phase 4; run_technical_analysis(tickers, db) + get_technical_scores(tickers, db)
- `aiem_v3_orchestrator.py` — Phase 5; run_orchestrator(discoveries, tech_scores, macro, portfolio, db)
- `aiem_v3_learning.py` — Phase 6; run_learning_cycle(db, lookback_days)
- `aiem_v3_verification.py` — Phase 8; run_full_verification(db, run_type)

## Critical API name
`aiem_macro_engine.admin_get_latest_macro()` — NOT `get_latest_macro()` (that method does not exist)

## Scheduler jobs added (main.py ~L14592)
- 8:00 AM ET Mon-Fri: `v3_discovery_premarket` (full scan + technical pre-compute)
- 4:45 PM ET Mon-Fri: `v3_learning_cycle`
- 4:55 PM ET Mon-Fri: `v3_verification_daily`

## Wire point in main.py
Source #11 in `_aiem_paper_pick_candidates()` — after the squeeze_reversion try/except, before the `_prelim = sorted(...)` line.

## Date storage rule
`discovery_date` in aiem_discovery_memory must be `date.today()` (run date), NOT the polygon scan_date from universe[0]["scan_date"]. Using scan_date causes get_todays_discoveries() to miss the cache.

## Admin endpoints
- GET `/stock-api/admin/aiem-v3/verify` — full 9-module health check
- POST `/stock-api/admin/aiem-v3/discovery?top_n=25` — manual scan trigger

## Live verification result (2026-07-07)
8/9 PASS, 1 WARN (decision_engine: 0 decisions — expected pre-market; fires at 9:42 AM ET)

**Why:** decision_engine only stores rows when _aiem_paper_execute_today() runs (9:42 AM ET). WARN on first daily verify is always expected.
