# Candlestick Confluence — pattern correctness fix

Date: 2026-08-06  
Branch: `cursor/stockscanner-full-tabs-live-5ace`

## Verdict

The **11 pattern formulas match the reference port** (spot-checked hammers, engulfing, morning star, three white soldiers, three inside/outside up against OHLC). The tab was **not scanning the market correctly**.

## Root cause

`_scan_candlestick_confluence` pulled history with:

```sql
ORDER BY ticker, scan_date DESC
LIMIT (batch_size * lookback)
```

Postgres applies that `LIMIT` to the whole result set, so almost only early-alphabet tickers received bars. Neon check: **896 / 901** historical signals were tickers **A–C**.

## Fixes

1. Per-ticker history via `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY scan_date DESC)`.
2. Drop micro-range signal bars (&lt; 0.5% H–L) so T-bill “marubozu” noise is excluded.
3. Always refresh `candlestick_confluence_signals` on scan (Telegram still deduped).
4. GET serves the latest scan day’s ranked list; `stale` if behind `polygon_market_daily`.

## Live re-scan (2026-08-04, prod Neon)

- Universe: 4,463 tickers (≥$2, vol≥200k); history for all 4,463 (avg ~44 bars).
- Matches: **807** (62 micro-range skipped); top **100** persisted.
- Alphabet (top 100): A–C 21 · D–L 33 · M–S 34 · T–Z 12 (was 77/0/0/0).
- Example: **NEE** bullish engulfing confirmed on Aug 3 red → Aug 4 green engulfing body.

Single-environment check: prod Neon only.
