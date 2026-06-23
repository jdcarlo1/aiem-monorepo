---
name: Tradier API setup notes
description: What's needed to wire Tradier for live market-hours option chains; authentication gotchas.
---

## The Rule
Tradier paper trading account token returns 401 on `quotes` and `options/chains` endpoints — only the public `clock` endpoint works without a real brokerage account. Need a **brokerage account** (not paper trading) to get market data via API.

**How to set up:**
1. User opens Tradier brokerage account at tradier.com (free, $0 minimum)
2. After approval: Settings → API Access → copy the **production** token
3. Add as secret `TRADIER_API_TOKEN`
4. Wire into `_scan_one` in main.py as market-hours option chain source (before Polygon)

**Why it matters:**
- Polygon Starter OI = 0 during market hours (9:30-4pm ET); we use volume-only detection as workaround
- Tradier brokerage API returns live OI all day → proper vol/OI ratios during market hours
- Combination: Tradier (market hours) + Polygon (after close) = Yahoo usage ~0% in scanner

**Endpoints to use:**
- Production: `https://api.tradier.com/v1/markets/options/chains?symbol={ticker}&expiration={date}&greeks=true`
- Headers: `Authorization: Bearer {token}`, `Accept: application/json`
- Returns: `option_type`, `strike`, `volume`, `open_interest`, `bid`, `ask`, `greeks.mid_iv`

**How to get expiration dates first:**
- `https://api.tradier.com/v1/markets/options/expirations?symbol={ticker}`
- Returns list of valid expiry dates to pass to chains endpoint

**Integration point in main.py:**
- Add `_tradier_fetch_calls(ticker, max_exp_days)` helper similar to `_polygon_fetch_calls`
- In `_scan_one`: try Tradier first during `_mkt_hours`, fall back to Polygon vol-only if Tradier fails
- After close: use Polygon with OI as normal
