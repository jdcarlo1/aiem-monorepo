---
name: AIEM Full-Market Research System (Loop A + Loop B)
description: 24-tool autonomous quant research system using polygon_market_daily (12K stocks/day); 9 enhancements merged from reviewer recommendations
---

## Architecture

**polygon_market_daily** table — stores ALL US stocks (price >= $0.50, vol >= 30K) every trading day.
- Filled by `_polygon_full_market_scan` (daily 8:35 AM ET) — now wrapped in `try/finally` so lock always releases
- Historical backfill: `_polygon_backfill_historical()` launches in daemon thread at startup (30s delay)
  - Fetches missing dates from Apr 1, 2026 → today via Polygon grouped-daily (13s/call = 5/min rate)
  - gap_pct backfill UPDATE query runs after all dates are fetched
- Forward returns computed via SQL self-join on (ticker, next scan_date) — NOT stored
- Indexes: scan_date, ticker, (ticker,scan_date), gap_pct, rvol, close_strength

**aiem_signal_discoveries** table — stores validated signals from the AIEM
- columns: conditions_json, hypothesis_text, signal_n, signal_win_rate, edge_broad, edge_tight, p_value, oos_edge, status, invented_indicator

**aiem_test_ledger** table (new) — logs every statistical test call for real Bonferroni tracking
- columns: session_date, tool_name, conditions (JSONB), p_value, n, logged_at
- Used by `mkt_required_pvalue` to return real threshold (not self-reported count)

**ticker_meta** table (new) — real sector + market cap from Polygon reference API
- columns: ticker, sector (SIC description), market_cap, cap_tier (nano/small/mid/large), shares_float
- Populated weekly Sunday 10 PM ET by `_mkt_refresh_ticker_meta_bg` (background thread, 0.25s/ticker)
- Unlocks: LAW 9 (real cap segmentation), LAW 42/45 (real sector), LAW 46 (peer group), LAW 59/60 (float)

**vix_daily** table (new) — real VIX close from Polygon Indices API (I:VIX)
- Fetched daily 4:15 PM ET via `_mkt_fetch_and_store_vix`
- Requires Indices access on Polygon plan; gracefully handles 403 (Starter plan limitation)
- Unlocks: LAW 12 (volatility normalization with real VIX), LAW 35 (4-quadrant regime with real VIX)

## 24 AIEM Market Tools (mkt_* prefix)

All tools are wired into `_run_aiem_research_agent` tool_map and `_AIEM_AGENT_TOOLS` schemas.

| Tool | Purpose |
|------|---------|
| mkt_explore_dimensions | FIRST call: dataset summary, factor distributions, baseline win rate |
| mkt_test_signal | Core workhorse: test any conditions against 12K universe; logs to test_ledger |
| mkt_test_inverse | Confirm signal is directional (mandatory after p<0.05) |
| mkt_find_thresholds | Grid-search 20 thresholds for any single factor |
| mkt_analyze_top_movers | Profile stocks that moved 5%+ the day BEFORE they moved |
| mkt_analyze_false_signals | Separate winners from false positives within a signal |
| mkt_regime_filter | Test signal in bull/bear/flat regime (SPY-based classification) |
| mkt_validate_oos | MANDATORY: 60/40 train/test split before saving |
| mkt_generate_hypotheses | GPT-4o generates 8 novel hypotheses from first principles |
| mkt_save_discovery | Save to aiem_signal_discoveries; redundancy gate blocks >70% Jaccard overlap |
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
| **mkt_required_pvalue** | Returns REAL Bonferroni threshold from test_ledger DB (not self-reported) |
| **mkt_segment_by_cap_tier** | Tests signal in nano/small/mid/large using real ticker_meta cap data |
| **mkt_segment_by_sector** | Tests signal per SIC sector using real ticker_meta sector data |
| **mkt_check_redundancy** | Jaccard overlap vs all validated discoveries; blocks save if >70% overlap |

## Condition Format
All signal testing uses `{factor}_min` / `{factor}_max` keys:
```python
{"gap_pct_min": 2.0, "rvol_min": 3.0, "close_strength_min": 0.6}
```
Whitelist: gap_pct, rvol, close_strength, range_pct, close_price, volume, open_price, high_price, low_price, vwap

## Scheduled Jobs (AIEM)
- 4:15 PM ET Mon-Fri: VIX fetch (`aiem_vix_daily`)
- Sunday 6 PM ET: Auto-retire decaying signals (`aiem_auto_retire_weekly`) — checks recent vs historical edge, retires if >3pp decay
- Sunday 10 PM ET: Ticker meta refresh (`aiem_ticker_meta_weekly`) — background thread, slow, runs overnight

## Endpoints
- `GET /stock-api/aiem/discoveries` — all validated signal discoveries (sorted by oos_edge)
- `GET /stock-api/aiem-research-status` — existing AIEM weekly model status

## Key Decisions
**Why:** The prior AIEM only queried scanner pick history (unusual_calls_log, signal_outcomes) —
      a tiny biased sample. Full-market research on 12K stocks/day allows discovering signals
      that are never correlated with what the scanner was already looking at.
**Why 13s sleep:** Polygon Starter = 5 req/min; 12s would sometimes trip rate limit; 13s is safe.
**Why 30s startup delay for backfill:** Avoids racing warm-up jobs (Yahoo circuit breaker).
**Forward return approach:** SQL self-join on next trading date (not stored) keeps table lean.
**Why redundancy gate is enforced in code (not just laws):** LLMs skip/forget instructions but
      can't bypass the gate in mkt_save_discovery — prevents duplicate signal accumulation.
**Why try/finally in _polygon_full_market_scan:** Any unhandled exception was leaving
      _POLYGON_RVOL_LOCK acquired forever, blocking all future scans until restart.
**LAW 61 fix:** Removed references to conditions_2/conditions_3 phantom params that never existed.
**Laws now using real data (updated):** 9, 12, 35, 42, 45, 46, 59, 60
