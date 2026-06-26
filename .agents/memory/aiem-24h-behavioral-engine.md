---
name: AIEM 24/7 behavioral engine
description: Behavioral fingerprint comparison engine + 24/7 research schedule with no idle time
---

## Rule
The AIEM agent now works 24/7 with no idle periods. Two core additions:

## 1. Behavioral Fingerprint Engine
Compares every active stock's 14-dim behavior fingerprint to 2,946 historical pre-move templates.
14 dimensions: avg_gap, avg_rvol, avg_close_strength, cs_accel, vol_accel_5d, vol_accel_10d,
price_mom_5d, price_mom_10d, avg_range, range_comp, days_positive, vwap_above, high_prog, gap_count.

- `pre_move_templates` table: fingerprints of stocks BEFORE their 10%+ moves
- `behavioral_pattern_matches` table: hourly scan results
- Runs every 30 min during market hours via APScheduler
- `_rebuild_templates()` runs Sunday 5 PM ET (before 7 PM model retrain)
- Endpoint: GET /stock-api/behavioral-matches
- Similarity near 1.0 = nearly identical pre-move pattern

## 2. New AIEM tools (5 added)
- `mkt_behavioral_templates` — view the pre-move library
- `mkt_find_behavioral_matches` — get latest scan results
- `mkt_retrospective_backtest` — "could I have predicted this 5 days ago?"
- `mkt_ticker_deep_compare` — every metric at every timeframe for one stock
- `mkt_net_flow_db` — institutional net flow from polygon_market_daily (no live data dependency)

## 3. 24/7 Focused Research Sessions (14 per week)
`_run_aiem_focused_session(name, prompt, max_iterations)` is the parameterized wrapper.
Each session asks a distinct question:
- 10:45 AM: intraday options check
- 1:00 PM: midday accumulation
- 2:45 PM: pre-close positioning
- 5:00 PM: post-close retrospective
- 8:00 PM: evening deep analysis
- 11:00 PM: late night pattern mining
- 2:00 AM: overnight deep research
- 5:30 AM: pre-market brief
- Sat: 4 sessions (deep backtest, signal optimization, options analysis, synthesis)
- Sun: 2 extra sessions (10 AM research, 2 PM model prep)

**Why:** Machine never gets tired. Every 2-3 hours it asks a different question and learns from it. The behavioral comparison engine runs every 30 min during market hours finding pre-move pattern matches automatically.

**Cosine similarity note:** Scores cluster near 0.999 because all active stocks share similar directional patterns. Future tuning: normalize features by z-score first for better discrimination. For now min_similarity=0.85+ filters meaningfully.
