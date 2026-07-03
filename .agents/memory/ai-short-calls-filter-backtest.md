---
name: AI Short Calls filter backtest
description: 320-pick backtest findings that raised win rate from 31.6% to projected 66-70%; exact filter thresholds that work vs. those that destroy WR.
---

# AI Short Calls — Backtest-Validated Filter Thresholds

## Winning profiles (from 320 graded picks, real T3 outcomes)

| Filter combo | WR | n | avg return |
|---|---|---|---|
| VOI 1.5–5x + prem ≥ $1M | **66.7%** | 27 | +6.54% |
| VOI 1.5–5x + prem ≥ $1M + dark_pool ≥ 50 | **75.0%** | 24 | +7.75% |
| VOI 1.5–5x + prem ≥ $750K | 60.0% | 30 | +5.65% |
| VOI 2–8x + prem ≥ $1M + OTM ≤ 15% | 62.5% | 40 | +5.24% |
| VOI 1.5–10x + prem ≥ $1M | 60.4% | 48 | +4.78% |

## Hard losers (never use these)

| Condition | WR |
|---|---|
| VOI > 30x | 22% |
| OTM > 15% | 0% (literally zero wins) |
| prem < $500K | ~28% |
| DTE 22–45 | 11% |

## Implementation (live as of 2026-07-03)

Four layers of filtering in `_bg_aisc()` in `main.py`:

1. **DB query** — `vol_oi BETWEEN 1.5 AND 30`, `prem >= 250000`, `otm_pct BETWEEN -5 AND 15`, LIMIT 40, SQL ORDER prefers VOI 1.5-5x tier. Premium gate is intentionally permissive ($250K) because dark pool ≥ 50% is the real selector — a high premium floor would block valid signals before dp can evaluate them. Historically $250K/$500K are identical (scanner never captures below $500K), but future signals could come in lower.
2. **Dark pool pre-enrichment** — before scoring, calls `app._dp_cache["results"]` (fast path, `short_pct` = off_exchange_pct) then `_get_dark_pool_convergence(missing)` (FINRA CDN) for any tickers not in cache; adds `dark_pool_pct` to each hit dict
3. **Python pre-score + dedup** — `_score_hit()` weights: VOI 1.5-5x=50pts, prem $1M+=40pts, dp≥60%=45pts, dp≥50%=35pts, OTM≤5%=20pts, DTE≤7d=15pts; dedup by ticker (best hit per ticker); top 20
4. **AI prompt** — "★★ ULTIMATE 75% WR: VOI 1.5-5x + prem ≥ $1M + dark_pool ≥ 50%"; hard disqualifiers spelled out; returns 3-5 picks

**Why:** Old filter (VOI ≥ 5x, prem ≥ $500K, OTM up to 30%) let in retail-chasing high-VOI noise and deep-OTM lottery tickets. The 75% WR sweet spot requires all three: moderate VOI (institutional sizing), large dollar premium ($1M+), and dark pool ≥ 50% (stealth accumulation in dark pools).

**Dark pool mechanics:** `dark_pool_pct` = % of total daily volume routed off-exchange per FINRA Reg SHO. ≥50% means institutions are buying shares AND calls through dark pools simultaneously — the highest-conviction setup found in the backtest.

**How to apply:** Any future recalibration of `_bg_aisc()` should start from these thresholds. The dark pool lookup has a fast cache path (instant) + FINRA CDN fallback (1-2s for a batch). If the CDN is down, `dark_pool_pct` defaults to 0 and scoring falls back to VOI+prem only.
