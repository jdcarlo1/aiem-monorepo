---
name: AIEM Barchart-parity indicators
description: Full technical indicator suite added to AIEM — matches Barchart.com coverage; two tools registered in all tool maps and schemas
---

## Tools added

### mkt_compute_indicators(ticker, start_date, end_date)
Computes EVERY standard technical indicator from polygon_market_daily OHLCV data.

**Moving Averages**: SMA 5/10/20/50/100/200 day, SMA 200-week (~1000 trading days), EMA 5/10/20/50/100/200
**% from each MA**: pct_from_sma5/10/20/50/100/200/200wk, pct_from_ema20/50/200, above_sma20/50/200/ema200/200wk
**Oscillators**: RSI(14), Stochastic %K/%D(14,3), Williams %R(14), CCI(20)
**Trend**: MACD(12,26,9) + signal + histogram, ADX(14) + +DI/-DI, Parabolic SAR
**Volatility**: Bollinger Bands(20,2σ) + bb_pct, Keltner Channels(20,1.5×ATR), ATR(14) + atr_pct
**Volume**: OBV, MFI(14), CMF(20)
**Price**: ROC(12), Momentum(10), 52/26/13-week high/low, pct_from_52w_high/low
**Signal summary**: Barchart-style buy/sell/neutral count across 14 signals → overall rating

Returns: snapshot (latest values) + 60-day time series of all indicators.

### mkt_screen_by_indicator(indicator, operator, threshold, min_price, min_volume, end_date, top_n)
Screens all 11,000+ stocks in polygon_market_daily by any indicator.
38 supported indicators (see VALID set in function).
Batch-fetches history in groups of 500 tickers for efficiency.

**Why**: Users/AIEM need to ask "find all oversold stocks", "what's above the 200-day MA", "screen for RSI < 30 in Jan 2024" across the full market.

## Wiring
- Both functions registered in focused-session tool map AND main research tool map
- Both schemas added to _AIEM_AGENT_TOOLS with full enum/description
- 200-week MA requires ≥1000 trading days; available once 2024 backfill completes (~2.2 hrs)

## Data dependency
polygon_market_daily backfill (2024-01-01 → present) must complete for full indicator history.
Trigger: POST /stock-api/admin/run-historical-backfill (X-Admin-Token header).
Backfill sleeps 30s after server start, then fetches ~600 trading days at 13s/day ≈ 2.2 hours.
