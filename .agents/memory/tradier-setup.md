---
name: Tradier live data integration (options + quotes + history + intraday)
description: Tradier fully wired as primary data source for options chains, quotes, daily OHLCV, and intraday bars — replacing Yahoo Finance everywhere it can.
---

# Tradier live data integration

## Status
**Fully wired as of June 23 2026.**
Token: `TRADIER_API_TOKEN_2` (28-char brokerage token).
`TRADIER_API_TOKEN` fallback (old paper-account token — 401 on market data, ignore).

## Why brokerage account required
Paper trading token returns 401 on all market data endpoints. Only `clock`
works without a real brokerage account. `TRADIER_API_TOKEN_2` is real brokerage.

## Three shared module-level helpers (main.py, after curl_cffi section)

### `_td_quotes(symbols: list) → dict`
Batch real-time quote (up to 200 tickers per call). Returns:
`{SYM: {last, prevclose, open, high, low, volume, avg_volume, change_pct, week_52_high, week_52_low, bid, ask}}`
Endpoint: `GET /v1/markets/quotes?symbols=SYM1,SYM2,...`

### `_td_history(ticker, days=40, start_date=None) → pd.DataFrame`
Daily OHLCV. Same column schema as `yfinance.tk.history(interval='1d')`.
Returns DataFrame[Open, High, Low, Close, Volume] with DatetimeIndex.
`start_date` (YYYY-MM-DD string) fetches from that date to today; `days`
fetches last N sessions with 10-day weekend buffer.
Endpoint: `GET /v1/markets/history?symbol=X&interval=daily&start=...&end=...`

### `_td_intraday(ticker, interval='1min') → pd.DataFrame`
Intraday bars (1min/5min). Returns ET-localized DatetimeIndex so `between_time()`
and `tz_convert()` work without extra handling.
Endpoint: `GET /v1/markets/timesales?symbol=X&interval=1min&session_filter=open`
**Why ET localization matters:** Tradier returns naive ET strings — if you pass
them to `tz_localize("UTC")` (old yfinance pattern), 9:30 ET becomes 5:30 ET.
The helper localizes correctly to `America/New_York`.

## All wired call sites (Yahoo → Tradier)
| Call site | Old Yahoo call | Tradier replacement |
|---|---|---|
| `_scan_one` (options, market hours) | `yf.Ticker().option_chain()` | `_tradier_fetch_calls()` |
| Nano morning `_score()` | `tk.history(period="40d")` | `_td_history(ticker, days=40)` |
| SC morning `_score()` | `tk.history(period="40d")` | `_td_history(ticker, days=40)` |
| SC gap bonus | `tk.history(period="2d")` | reuses `hist` already fetched |
| Nano grading loop | `t.history(start=date)` | `_td_history(tk, start_date=date)` |
| SC grading loop | `t.history(start=date)` | `_td_history(tk, start_date=date)` |
| IWM regime gate | `yf.Ticker("IWM").fast_info` | `_td_quotes(["IWM"])` |
| Nano intraday confirm | `tk.history(period="1d", interval="1m")` | `_td_intraday(ticker, "1min")` |
| EOD accum outcomes | `yf.Ticker().history(1d, 1m)` | `_td_intraday(_sym, "1min")` |
| Conviction outcomes grading | `yf.Ticker().history(period="20d")` | `_td_history(ticker, days=20)` |
| SPY 1-year cache | `yf.download("SPY", period="1y")` | `_td_history("SPY", days=252)` |

## What Yahoo Finance STILL handles (Tradier can't)
- `fast_info.market_cap` — not in Tradier; SC scoring sets `mcap_m = 0.0` (display only)
- `fast_info.float_shares`, `.shares` — not in Tradier (use Finviz)
- `tk.info` — earnings calendar, sector, industry, short interest
- Option chain Yahoo fallback — tertiary fallback when both Tradier and Polygon fail

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
- `_td_history` timeseries has no timezone — DatetimeIndex is tz-naive (that's fine for daily bars)
- `_td_intraday` must localize as `America/New_York` (not UTC) — Tradier times are already ET
