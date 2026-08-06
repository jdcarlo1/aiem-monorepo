# Stock Scanner — full live tab pass (2026-08-05)

**Branch:** `cursor/stockscanner-full-tabs-live-5ace`  
**DB:** Neon prod `neondb` @ spring-flower  
**Live host probed:** `https://nclexai.org/stock-api/*` (pre-deploy baseline)

## Probe baseline (pre-deploy)

82 endpoints probed. **35 non-OK** on production (undeployed fixes + real gaps).

| Class | Examples |
|---|---|
| Auth 401 | `aiem-paper-portfolio`, `gamma-wall` (prod still gated) |
| 404 | `morning-brief`, `unusual-puts-log` (not on prod yet) |
| Yahoo EMPTY/STALE | composite, convergence, earnings, eod-accum, multi-signal, morning-inflows, market overview, squeeze, iv-rank, short-squeeze |
| POST-only 405 on GET probe | bull-flow/top10, prop/scan, smart-money, squeeze/detector, breakout/radar, net-flow/microcap, analytics/historical — **FE uses POST; not real tab bugs** |
| Empty track | insider-outcomes (0 graded rows), unusual-puts (0 log rows), persistence (prod empty; Neon has data) |
| Paper | Aug 5 ledger `FAILED` (`connection already closed`); `try_claim` did not reclaim FAILED |

## Fixes in this pass

| Area | Change |
|---|---|
| Paper Money GET | Public read — FE no longer 401s. Force-execute/mtm stay admin. |
| Paper FAILED reclaim | `try_claim` Step 2e steals `FAILED` (≤5 attempts, zero picks). |
| Paper conn drop | Keepalives + reconnect on closed socket during insert loop. |
| Neon EMPTY/STALE | Cherry-picked prior audit: `_load_scan_cache` 60d, composite/earnings/convergence/eod/squeeze/insider/nano fallbacks. |
| Morning inflows | DB lookback 5→**60** days; breaker uses DB. |
| EOD accumulation | Yahoo-breaker path reads `eod_accum_picks` (90d); returns both `hits` and `candidates`. |
| Unusual puts | Off-hours empty log → thin Tradier CORE backfill; real upsert (delete+insert) instead of useless `ON CONFLICT DO NOTHING`. |
| Persistence | Fallback from multi-day `unusual_calls_log` when `signal_outcomes` sparse. |
| Insider outcomes | Provisional alert→polygon price outcomes when grader table empty. |
| Market overview | Sync Tradier sector/index snapshot when cache cold. |
| Short squeeze | `aiem_squeeze_signals` fallback on empty universe + Yahoo breaker. |
| Conviction stack | Live approx from unusual_calls + Layer9 when snaps >7d stale. |
| Morning brief / gamma / puts-log | Routes present (from prior cherry-picks). |

## Still gated / cannot invent

| Tab | Why |
|---|---|
| Gas Board live | BYOK `subscriber_token` POST |
| Quant Agent | BYOK |
| Congress | No congress table in Neon |
| Portfolio (local) | Separate in-memory paper; empty until user buys |
| My trades / watchlist | Per-user; empty until user saves |
| Aug 5 paper picks | Already FAILED; reclaim enables **retry**, does not invent missed morning fills |

## Verify after deploy

```bash
curl -s "$HOST/stock-api/aiem-paper-portfolio?days=30" | jq '.open_count,.win_rate,.total_closed'
curl -s "$HOST/stock-api/morning-brief" | jq 'keys'
curl -s "$HOST/stock-api/gamma-wall" | jq '.results|length // .walls|length // keys'
curl -s "$HOST/stock-api/unusual-puts-log" | jq '.total'
curl -s "$HOST/stock-api/composite-score" | jq '.results|length,.stale,.note'
curl -s "$HOST/stock-api/earnings-calendar" | jq '.count,.stale'
curl -s "$HOST/stock-api/morning-inflows" | jq '.standouts|length,.stale'
curl -s "$HOST/stock-api/eod-accumulation" | jq '.candidates|length // .hits|length,.stale'
curl -s "$HOST/stock-api/conviction-stack" | jq '.results|length,.source'
curl -s "$HOST/stock-api/insider-outcomes" | jq '.total,.accuracy_pct'
curl -s "$HOST/stock-api/bull-flow/persistence" | jq '.count'
curl -s "$HOST/stock-api/short-squeeze" | jq '.candidates|length,.note'
curl -s "$HOST/stock-api/market/overview" | jq '.indices|length,.sectors|length,.note'
```
