# StockScanner broken-tab fix pass

**Branch:** `cursor/stockscanner-tabs-audit-5ace`  
**Date:** 2026-08-05

## Fixed in this pass

| Tab / issue | Change |
|---|---|
| Unusual Puts `/unusual-puts-log` 404 | Added `GET /stock-api/unusual-puts-log` (DB history mirror of calls log). FE falls back to log when live scan is empty. Widened off-hours puts query (7d, no future-expiry filter). |
| Morning Brief `/api/morning-brief` 404 | Added `GET/POST /stock-api/morning-brief` (+ `/refresh`) in Flask. FE now uses `/stock-api/morning-brief` via `fetchJson`. Vite proxy rewrite for legacy `/api/morning-brief`. |
| Gamma Wall 401 | Removed `_admin_ok()` gate. Endpoint is Tradier-backed public market data; no longer blocked by Yahoo breaker. |
| Gas Board GET 405 non-JSON | Accepts GET with JSON 405 explaining POST + `subscriber_token` required. |
| Stock Lookup “405” | Probe error only — FE already uses `GET /stock/analyze?ticker=`. No code bug. |

## Intentionally not opened (auth / product gates)

| Tab | Status |
|---|---|
| Paper Money / Portfolio AIEM | `GET /aiem-paper-portfolio` stays admin-token gated (`_admin_ok`). |
| Gas Board live scores | Still requires subscriber token via POST (BYOK). |
| Quant Agent | BYOK / streaming — unchanged. |

## Not fixed yet (Yahoo EMPTY/STALE class)

Many tabs still return empty/stale when Yahoo throttles or market is closed. Fixing those needs:

1. Reachable **Neon** `DATABASE_URL` from this agent (helium hostnames do not resolve here), and/or
2. Broader Tradier/Polygon fallbacks per route (incremental).

Schema note (needs Joel approval before apply): `unusual_puts_log` still has `ON CONFLICT DO NOTHING` without a unique target — recommend unique `(ticker, strike, expiry)` upsert. **Not applied.**

## Credentials check (this agent)

- Tradier + Polygon: verified HTTP 200 in-session.
- Helium Postgres (`helium:5432`): unreachable from Cursor cloud (name resolution).
- Neon prod `DATABASE_URL`: still needed for live DB verification.
