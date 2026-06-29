---
name: Premarket-to-Open Paper Trading Module
description: How the opening_snapshot_tracker + premarket_open_trader modules work, key bugs fixed, and scheduler wiring details.
---

# Premarket-to-Open Paper Trading Module

## Files
- `artifacts/stock-scanner-api/opening_snapshot_tracker.py` — records live price/volume snapshots; `opening_snapshots` table
- `artifacts/stock-scanner-api/premarket_open_trader.py` — combines 5 modules into one decision (enter_now/wait/skip); writes paper picks to `ai_stock_picks`

## Decision flow
1. `opening_snapshot_tracker.get_todays_snapshots()` → accumulated intraday bars (self-built from 5-min scheduler)
2. `classify_from_snapshots()` → pattern + confidence (genuine_continuation, pullback_continuation, fake_breakout, etc.)
3. `pre_recommendation_synthesis.synthesize_and_log()` → confluence count (min 2 required)
4. `earnings_calendar.should_avoid_entry()` → 2-day earnings buffer
5. `regime_detector.get_current_regime()` → high_vol_downtrend blocks entry
6. Maps decision → `agent_decisions.decision_type` via `_DECISION_TYPE_MAP` (enter_now→trade, skip/wait→no_trade)

## Bugs fixed vs spec
- **CHECK constraint**: `decision_type` must be `trade/no_trade/hold/exit`, not `enter_now/skip/wait` — use `_DECISION_TYPE_MAP`
- **write_paper_pick param count**: spec had 3 `%s` with 4 params (spurious `"open"`); removed

## Scheduler wiring
- Fires Mon-Fri 9:45-9:55/5 (changed from 9:30 to avoid morning burst)
- Candidates: `DISTINCT ticker FROM unusual_calls_log WHERE created_at >= NOW() - INTERVAL '2 days'` (up to 30)
- Live price via `_td_quotes()` → `q["last"]` + `q["volume"]`; `q["change_pct"]` as premarket_gap_pct

## regime_detector caching rule
**Why:** `evaluate_ticker` calls `regime_detector.get_current_regime` per ticker. 30 tickers/slot × 2 yf.download calls = 60 yfinance calls every 5 min in the burst window.
**Fix:** Module-level 15-min cache (`_cached_result`, `_REGIME_CACHE_TTL=900`) + per-download 8s daemon-thread timeout. Falls back to stale cache on timeout, then to `_FALLBACK_REGIME`.
