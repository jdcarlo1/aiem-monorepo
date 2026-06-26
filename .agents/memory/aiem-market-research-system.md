---
name: AIEM Full-Market Research System (Loop A + Loop B)
description: 20-tool autonomous quant research system using polygon_market_daily (12K stocks/day)
---

## Architecture

**polygon_market_daily** table — stores ALL US stocks (price >= $0.50, vol >= 30K) every trading day.
- Filled by `_polygon_full_market_scan` (daily 8:35 AM ET) — now saves ALL stocks, not just top 40
- Historical backfill: `_polygon_backfill_historical()` launches in daemon thread at startup (30s delay)
  - Fetches missing dates from Apr 1, 2026 → today via Polygon grouped-daily (13s/call = 5/min rate)
  - gap_pct backfill UPDATE query runs after all dates are fetched
- Forward returns computed via SQL self-join on (ticker, next scan_date) — NOT stored
- Indexes: scan_date, ticker, (ticker,scan_date), gap_pct, rvol, close_strength

**aiem_signal_discoveries** table — stores validated signals from the AIEM
- columns: conditions_json, hypothesis_text, signal_n, signal_win_rate, edge_broad, edge_tight, p_value, oos_edge, status, invented_indicator

## 20 AIEM Market Tools (mkt_* prefix)

All tools are wired into `_run_aiem_research_agent` tool_map and `_AIEM_AGENT_TOOLS` schemas.

| Tool | Purpose |
|------|---------|
| mkt_explore_dimensions | FIRST call: dataset summary, factor distributions, baseline win rate |
| mkt_test_signal | Core workhorse: test any conditions against 12K universe |
| mkt_test_inverse | Confirm signal is directional (mandatory after p<0.05) |
| mkt_find_thresholds | Grid-search 20 thresholds for any single factor |
| mkt_analyze_top_movers | Profile stocks that moved 5%+ the day BEFORE they moved |
| mkt_analyze_false_signals | Separate winners from false positives within a signal |
| mkt_regime_filter | Test signal in bull/bear/flat regime (SPY-based classification) |
| mkt_validate_oos | MANDATORY: 60/40 train/test split before saving |
| mkt_generate_hypotheses | GPT-4o generates 8 novel hypotheses from first principles |
| mkt_save_discovery | Save to aiem_signal_discoveries (only after oos_validated=True) |
| mkt_load_discoveries | Load prior validated signals at session start |
| mkt_factor_correlations | Pearson corr between each factor and next-day return |
| mkt_discover_interactions | 3×3 grid of two-factor combinations |
| mkt_signal_drift | Detect decaying signals (recent vs historical edge) |
| mkt_volume_patterns | Accumulation/distribution/dry-up pattern win rates |
| mkt_price_patterns | Strong/weak close, range compression, breakout patterns |
| mkt_compute_momentum | Multi-day momentum continuation vs mean-reversion |
| mkt_invent_indicator | GPT-4o invents new SQL indicator + tests it live |
| mkt_compare_signals | A vs B head-to-head + intersection synergy |
| mkt_build_composite | Combine multiple discoveries into weighted composite |

## Condition Format
All signal testing uses `{factor}_min` / `{factor}_max` keys:
```python
{"gap_pct_min": 2.0, "rvol_min": 3.0, "close_strength_min": 0.6}
```
Whitelist: gap_pct, rvol, close_strength, range_pct, close_price, volume, open_price, high_price, low_price, vwap

## Endpoints
- `GET /stock-api/aiem/discoveries` — all validated signal discoveries (sorted by oos_edge)
- `GET /stock-api/aiem-research-status` — existing AIEM weekly model status

## AIEM System Prompt
Updated to include 20-step mandatory workflow for Loop A/B market research.
Standards: never save without p<0.05 AND oos_validated=True. Always test inverse.

## Key Decisions
**Why:** The prior AIEM only queried scanner pick history (unusual_calls_log, signal_outcomes) — 
      a tiny biased sample. Full-market research on 12K stocks/day allows discovering signals
      that are never correlated with what the scanner was already looking at.
**Why 13s sleep:** Polygon Starter = 5 req/min; 12s would sometimes trip rate limit; 13s is safe.
**Why 30s startup delay for backfill:** Avoids racing warm-up jobs (Yahoo circuit breaker).
**Forward return approach:** SQL self-join on next trading date (not stored) keeps table lean.
