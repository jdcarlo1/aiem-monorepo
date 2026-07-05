---
name: Polygon SI wiring — Module B Short Squeeze
description: Real FINRA short-interest data via Polygon; backtest findings; API quirks; borrow cost status
---

## API endpoint
`GET /stocks/v1/short-interest?ticker={ticker}&settlement_date.gte={from}&limit=30`
Returns ascending order (oldest first) regardless of `order=desc` parameter.
Use `settlement_date.gte` filter to get recent records.
Rate limit: ~1 req/5s safe; bursts of 5 then 429; 35s cooldown recovers.

## Fields available
- `short_interest` — shares sold short
- `avg_daily_volume` — average daily vol at reporting date
- `days_to_cover` — pre-computed DTC = short_interest / avg_daily_volume

## Fields NOT available (confirmed NOT_IMPLEMENTED)
- borrow_cost / utilization — not in this API endpoint, period.

## SI% derivation
si_pct = short_interest / weighted_shares_outstanding × 100
weighted_shares_outstanding from `GET /vX/reference/tickers/{ticker}`.
Requires a separate API call per ticker; computed in live scan, not stored in backtest.

## Staleness policy
- Use most recent settlement_date ≤ signal_date
- Max tolerated gap: 45 days (≈ 3 bi-monthly periods)
- Gap > 45d → si_status = 'TOO_STALE', excluded from cohorts B/C

## Data coverage gap (as of 2026-07-05)
Most small-cap tickers: Polygon FINRA data frozen at 2026-03-31.
Only handful (CARL, AGMB, IREZ, NAKA, FOFO, ASTX, ASBP, ADBG, BLLN, BTGO)
have 2026-05 or 2026-06 settlements.
For April 2026 signals: staleness ~7-37 days → AVAILABLE ✓
For May-June 2026 signals: mostly TOO_STALE (64+ days) ✗

## Honest backtest results (v2, 2026-07-05)
Backtest universe: 251 candidates, 138 gate-passed (Module F)
  CohortA (proxy-only, n=138): WR_3d=40.58%, avg=-1.86%, p=0.9999
  CohortB (SI available, n=19): WR_3d=36.84%, avg=-0.22%, p=0.9999
  CohortC (SI + DTC≥3.0, n=11): WR_3d=36.36%, avg=+0.52%, p=0.9999

**Key finding**: DTC filter does NOT improve win rate.
WR actually worsened slightly vs baseline. Avg return marginal improvement
(+0.52% vs -1.86%) driven by 2 outliers (BLLN +16.35%, APPN +9.28%).
Signal remains a loser. Status: hypothesis only.

## Coverage achieved
55 tickers fetched to polygon_short_interest (2025-01-01 onward).
19/138 gated rows: AVAILABLE; 15: TOO_STALE; 104: NOT_AVAILABLE.
fetch_si_background.py runs at 8s/req; bash tool kills background processes
between calls — must run inline or write to a persistent daemon.

**Why:** borrow_cost NOT_IMPLEMENTED must never be implied as covered.
**How to apply:** Always tag `borrow_cost_status='NOT_IMPLEMENTED'` on every
row; document the derivation formula for si_pct; keep status='hypothesis'
until WR > 50% p < 0.05 in cohort C with n ≥ 30.
