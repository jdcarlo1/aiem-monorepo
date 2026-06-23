---
name: Tradier live data integration (options + quotes + history + intraday)
description: Tradier fully wired as primary data source for options chains, quotes, daily OHLCV, and intraday bars — replacing Yahoo Finance everywhere it can.
---

# Tradier live data integration

## Status
**Phase 3 complete as of June 23 2026. All option-chain call sites migrated.**
Token: `TRADIER_API_TOKEN_2` (28-char brokerage token).
`TRADIER_API_TOKEN` fallback (old paper-account token — 401 on market data, ignore).

## Why brokerage account required
Paper trading token returns 401 on all market data endpoints. Only `clock`
works without a real brokerage account. `TRADIER_API_TOKEN_2` is real brokerage.

## Three shared module-level helpers + three option-chain helpers (main.py)

### `_td_quotes(symbols: list) → dict`
Batch real-time quote (up to 200 tickers per call). Returns:
`{SYM: {last, prevclose, open, high, low, volume, avg_volume, change_pct, week_52_high, week_52_low, bid, ask}}`
Endpoint: `GET /v1/markets/quotes?symbols=SYM1,SYM2,...`

### `_td_history(ticker, days=40, start_date=None) → pd.DataFrame`
Daily OHLCV. Same column schema as `yfinance.tk.history(interval='1d')`.
Returns DataFrame[Open, High, Low, Close, Volume] with DatetimeIndex.
Endpoint: `GET /v1/markets/history?symbol=X&interval=daily&start=...&end=...`

### `_td_intraday(ticker, interval='1min') → pd.DataFrame`
Intraday bars (1min/5min). Returns ET-localized DatetimeIndex.
Endpoint: `GET /v1/markets/timesales?symbol=X&interval=1min&session_filter=open`

### `_td_expiries(ticker, max_days=365) → list[str]`
Returns sorted list of expiry date strings (YYYY-MM-DD). Cached 10 min.
Endpoint: `GET /v1/markets/options/expirations?symbol=X&includeAllRoots=false`
Edge case: single-expiry tickers return `str` not `list` — guarded with `isinstance(..., str)`.

### `_td_chain(ticker, expiry) → SimpleNamespace(calls=df, puts=df)`
Full option chain for one expiry. Uses **`greeks=true`** (critical — see below).
Maps to Yahoo-compatible columns: strike, lastPrice, bid, ask, volume,
openInterest, impliedVolatility, inTheMoney, contractSymbol, expiration.
Cached 10 min per (ticker, expiry) pair.
Endpoint: `GET /v1/markets/options/chains?symbol=X&expiration=DATE&greeks=true`

### `_TdTicker(ticker)` — drop-in yf.Ticker replacement
Routes `.options` → `_td_expiries()`, `.option_chain(exp)` → `_td_chain()`,
`.fast_info` → `_TdFastInfo` (Tradier quotes), everything else (`.info`,
`.calendar`) → real `yf.Ticker` via `__getattr__`.

## CRITICAL: greeks=true is required for implied volatility
`greeks=false` (Tradier default) returns `implied_volatility: null` for ALL
options — the field is entirely absent from the response schema.
`greeks=true` returns a `greeks` sub-object per option with `mid_iv` (0-1 scale).
Mapping: `(o.get("greeks") or {}).get("mid_iv") or 0`
Verified: AAPL ATM $295 call July 24 = mid_iv=0.2366 (23.7%) — correct scale.
**Never revert to greeks=false or you silently break all vol-crush/iv-rank scanners.**

## inTheMoney field
Tradier does not return `in_the_money` in the chain response. No downstream
scanner reads this column, so `bool(o.get("in_the_money") or False) = False`
is safe. Do NOT try to compute it from strike vs spot (added complexity, no benefit).

## What Yahoo Finance STILL handles (Tradier can't)
- `fast_info.market_cap` — not in Tradier
- `fast_info.float_shares`, `.shares` — not in Tradier (use Finviz)
- `tk.info` — sector, industry, short interest (`shortPercentOfFloat`, `shortRatio`)
- `tk.calendar` — earnings dates (falls through `_TdTicker.__getattr__`)
- Option chain Yahoo fallback — labeled `# Yahoo fallback` in _scan_one and microcap scanner

## Remaining intentional Yahoo yf.Ticker calls (do NOT replace)
- L9992: microcap scanner `# Yahoo fallback`
- L14087: short-squeeze `.info` for shortPercentOfFloat/shortRatio
- L19186: unusual-calls `_scan_one` `# Yahoo fallback`
- L21359: squeeze-scan `.info` for short interest

## API base URL and headers
```
base = "https://api.tradier.com/v1/markets"
headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
```

## Rate limits
Free brokerage account. No documented hard limit for market data. Options scanner
capped at 3 expirations per ticker (5 HTTP calls max). If 429s appear, add a
rate limiter similar to `_POLYGON_RATE_LIMITER`.

## Key edge cases
- Single-expiry tickers return `str` not `list` from expirations endpoint — guard with `isinstance(_exp_raw, str)`
- `_td_history` timeseries has no timezone — DatetimeIndex is tz-naive (fine for daily bars)
- `_td_intraday` must localize as `America/New_York` (not UTC) — Tradier times are already ET
- `greeks=true` returns inflated IV for deep ITM options (delta≈1.0) — this is mathematically correct, not a bug
