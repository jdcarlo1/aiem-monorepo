---
name: AIEM options flow tools
description: Four new AIEM agent tools giving complete options market visibility — no $500K subscriber-tab filter
---

## Rule
The AIEM agent can now see ALL call option activity — not just the $500K+ sweeps shown to subscribers. A $30K REIT sweep with 92x vol/OI is visible and queryable just like a $1M NVDA sweep.

## Four new tools in _tool_map (lines ~20879-20882)
- `mkt_ticker_options_history(ticker, days_back=30, min_premium_k=0)` — per-ticker full history from all 3 tables; min_premium_k=0 = see everything
- `mkt_options_flow_scan(days_back=7, min_premium_k=10, sort_by="vol_oi", limit=50)` — universe-wide scan across all scanners
- `mkt_options_predicts_price(days_back=90, forward_days=5, min_premium_k=10)` — backtest: does call activity precede price moves?
- `mkt_cross_confirm_options(days_back=5, min_vol_oi=3.0, min_premium_k=0)` — tickers where BOTH price/volume AND options flow confirm simultaneously

## Three source tables queried
- `call_sweep_log` — conviction-scored sweeps from the options_sweep scanner
- `unusual_calls_log` — broad daily unusual-calls scanner (9,188+ rows live)
- `unusual_calls_microcap_log` — small/micro-cap dedicated scanner

## call_sweep_log migration
Live table was missing `conviction` and `signals_fired` columns (created before they were added).
- Migration applied June 26, 2026 via psycopg2 ALTER TABLE
- ALTER TABLE IF NOT EXISTS added to `init_call_sweep_log_table()` in options_sweep.py so it's idempotent on redeploy

**Why:** Vol/OI ratio matters more than raw premium for small names. The subscriber tabs filter at $500K+ for display clarity; the agent needs the complete picture to cross-reference accumulation signals.

**How to apply:** When adding options query tools, always union all three tables and make min_premium_k default to 0 or 10 (not 500). Agent system prompt highlights these tools at the top of every research loop.
