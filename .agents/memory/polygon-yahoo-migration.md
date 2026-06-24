---
name: Polygon vs Yahoo migration plan
description: What Polygon Starter can/cannot replace for the score; migration status
---

## What Polygon Starter Covers (verified)
- Reference ticker: float/shares outstanding via `/v3/reference/tickers/{ticker}`
- Historical OHLCV: `/v2/aggs/prev` and grouped-daily snapshot
- Full-market RVOL scanner via grouped-daily (11,000+ tickers, 5 API calls)

## What Polygon Starter Does NOT Have
- Real-time intraday snapshots (Starter = delayed/EOD only)
- Short interest / shortPercentOfFloat / shortRatio
- Options chains (use Tradier)

## What Yahoo Is Still Used For (intentionally kept)
- `^VIX`, `^VIX3M` index prices (no Tradier/Polygon feed for indices)
- Short interest (`shortPercentOfFloat`, `shortRatio`, `floatShares` from `.info`)
  - Located at main.py ~L10815, ~L14110, ~L21400+, ~L22474+
  - `_fetch_fi_q()` in nano ranking uses Polygon for float, Yahoo for short_pct
- Earnings calendar / earnings history / analyst upgrades (yf.Ticker.info)
- `_TdTicker.fast_info` wraps yfinance fast_info for fundamentals

## Migration Status (June 2026)
- main.py: all OHLCV batch calls → Tradier (_td_history); quotes → _td_quotes()
- multiday_runner.py: all yf.download() → inline Tradier helpers; no yfinance imports
- holy_grail.py: _fetch_1m, _fetch_daily, _premarket_volume → Tradier
- eod_swing.py: _score_swing() → Tradier
- signal_outcomes.py: yf.download() → Tradier inline

## Key Conventions
- `_td_quotes(tickers)` at main.py L464 — uses TRADIER_API_TOKEN_2
- `_td_history(ticker, days=N)` at main.py L508 — daily OHLCV
- `_get_float_shares(ticker)` at main.py L10129 — Polygon primary, Yahoo fallback
- Satellite files cannot import from main.py (circular) — inline Tradier helpers added directly
