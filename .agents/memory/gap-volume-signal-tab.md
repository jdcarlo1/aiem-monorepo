---
name: Gap+Volume Signal Tab
description: OOS-validated signal tab built from Polygon RVOL scan data; validation results and architecture
---

## Signal
- **S2: Gap≥1% + RVOL≥2x** — only signal that survived tight-baseline OOS test
- Discovery: June 2026 (60,765 stock-day pairs)
- OOS holdout: Apr-May 2026 (155,415 pairs)
- Edge vs all stocks: +8.7pp (p=0.000)
- Edge vs tight baseline (other gappers): +2.5pp (p=0.002) — shrinks but survives
- **S1: RVOL+Range** — FAILED tight-baseline OOS (-3.2pp); range drives return, not RVOL

## Architecture
- Data source: `polygon_rvol_scan` table (populated 8:35 AM ET daily, 11K+ stocks)
- Endpoint: `/stock-api/gap-volume-signal` — pure DB query, no live yfinance
- Score = gap_pct×0.35 + rvol×0.40 + close_strength×100×0.25
- Filters: gap≥1%, rvol≥2x, price≥$2, most-recent scan_date

## Key insight
60% of biggest daily movers are catalyst-driven (FDA/M&A/earnings). Signal targets
the technical 40%. AI agent compounds this weekly as pick history accumulates.

**Why:** Tight-baseline test (volatile vs volatile) is the correct comparison;
broad-baseline (volatile vs calm) inflates apparent edge.
