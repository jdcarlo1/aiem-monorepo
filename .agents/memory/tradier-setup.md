---
name: Tradier live options integration
description: Tradier API wired as primary option chain source during market hours; token env var, endpoints, data shape, and integration status
---

# Tradier live options integration

## Status
**LIVE as of June 23 2026.** Token: `TRADIER_API_TOKEN_2` (28-char brokerage token).
`TRADIER_API_TOKEN` is checked as fallback (old token, 401 on market data — ignore it).

**Why the old `TRADIER_API_TOKEN` didn't work:**
Paper trading account token returns 401 on `quotes` and `options/chains` — only
`clock` endpoint works without a real brokerage account. `TRADIER_API_TOKEN_2`
is a real brokerage account token.

## Why Tradier over Polygon during market hours
Polygon Starter: OI settles EOD only → OI=0 from 9:30-16:00 ET → fell into
volume-only detection (vol≥100 floor, no vol/OI ratio). Tradier free brokerage
API: **real-time OI** → full vol/OI ratio scoring from market open.

## Where it lives in code
Inside `_run_unusual_calls_scan` → `_tradier_fetch_calls(ticker, max_exp_days)` helper.
Wired in `_scan_one` as first branch when `_mkt_hours == True`:
- If `_td_rows and _td_price` → process hits with `"source": "tradier"`, return
- Else → fall through to Polygon (OI after hours) → Yahoo fallback

## API endpoints (production only — not sandbox)
- Quote: `GET https://api.tradier.com/v1/markets/quotes?symbols={ticker}`
- Expirations: `GET https://api.tradier.com/v1/markets/options/expirations?symbol={ticker}&includeAllRoots=false`
- Chain: `GET https://api.tradier.com/v1/markets/options/chains?symbol={ticker}&expiration={date}&greeks=false`
- Headers: `Authorization: Bearer {token}`, `Accept: application/json`

## Rate limits
Free brokerage. No documented hard limit. Capped to 3 expirations per ticker
(5 HTTP calls max). If 429s appear, add a rate limiter like `_POLYGON_RATE_LIMITER`.

## Response shape → internal row format
- `o.get("option_type")` == "call" to filter
- `volume`: `o.get("volume")`, `openInterest`: `o.get("open_interest")` (real-time)
- `impliedVolatility`: `o.get("implied_volatility")` (decimal, e.g. 0.45 = 45%)
- `lastPrice`: `(bid+ask)/2` if both > 0, else `o.get("last")`
- `underlying_price`: populated from the quote call

**Why:** `isinstance(_exp_raw, str)` guard required — single-expiry tickers
return a plain string from Tradier expirations, not a list.
