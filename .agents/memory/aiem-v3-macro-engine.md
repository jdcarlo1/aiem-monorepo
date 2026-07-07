---
name: AIEM v3 Macro Engine (Phase 1+2)
description: 12-table DB schema + deterministic macro scoring; wired into paper trade gate; live verified
---

# AIEM v3 Macro Engine

## What was built
- `aiem_macro_engine.py` in `artifacts/stock-scanner-api/`
- 12 DB tables created via `init_v3_schema()` — all idempotent CREATE TABLE IF NOT EXISTS
- Public API: `get_macro_gate()`, `compute_macro_snapshot()`, `get_cached_macro_snapshot()`, `log_decision()`

## Token rule
- TRADIER_API_TOKEN (TOKEN_1) = sandbox → 401 on market history
- TRADIER_API_TOKEN_2 (TOKEN_2) = live → use for ETF history
- `_TRADIER_LIVE_TOKEN = _TRADIER_TOKEN_2 or _TRADIER_TOKEN` in aiem_macro_engine.py

## Scoring (0-100)
- Equity trend (40 pts): SPY/QQQ/DIA/IWM vs SMA20; 10pts if above, 5pts within 1%, 0pts below
- Volatility (30 pts): VIX level bands + VIX vs VIX-SMA20 adjustment ±3-5
- Breadth (20 pts): IWM 5d return minus SPY 5d return spread
- Credit (10 pts): DXY direction (falling = risk-on = full 10pts)

## Regimes + position modifiers
- BULL_STRONG ≥65 → 1.25x | BULL_MODERATE ≥50 → 1.00x | NEUTRAL ≥35 → 0.75x
- BEAR_CAUTION ≥20 → 0.50x | BEAR_SEVERE <20 → 0.00x (hard block)

## Wiring points in main.py
- Deferred schema init: `_DEFERRED_INITS.append(_init_aiem_v3_schema)` around line 1322
- Macro gate: inside `_aiem_paper_execute_today()` after kill-switch gate ~line 39439
- Scheduler: 9:00 AM Mon-Fri `_run_macro_precompute` (42 min before 9:42 execute)
- Admin routes: GET/POST /stock-api/admin/macro/latest|refresh ~line 15395

## Fail-safe behavior
- If ALL data sources fail → macro_score=50 (NEUTRAL), trades proceed
- If only ETF data fails → equity_score=20 (4×5pts neutral), others still score
- Engine errors → trades proceed with warning log (non-blocking)
- Cache TTL: 55 minutes in-process; DB cache for cross-restart persistence

## Live test result (2026-07-07 22:05 UTC)
macro_score=66, BULL_STRONG, pos_modifier=1.25, FULL data, block=false
SPY 747.69>SMA20 741.20 ✅ | VIX 16.13<SMA20 17.84 ✅ | DXY slightly rising ⚠️
