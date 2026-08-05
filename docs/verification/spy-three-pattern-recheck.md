# SPY three-pattern recheck (2026-08-05)

Recheck of **VWAP Reversion** (the Pattern Lab rule with the known stop-geometry
bug) plus two adjacent screenshot patterns: **MACD Histogram Divergence** and
**Bollinger Band Exhaustion**. Symbol: **SPY**. Fixed **$100 risk/trade**, no
compounding.

## Window / data

| Item | Value |
|------|-------|
| IS window | 2026-02-05 → 2026-08-05 |
| Minute bars (VWAP) | **113,812** / **124** sessions (Polygon 1-min) |
| Daily bars (MACD + BB) | **253** from 2025-08-01 (warm-up) through 2026-08-04 |
| Source | Polygon HTTP (`POLYGON_API_KEY`); minute cache disclosed at `/tmp/spy_6mo_bars_recheck.pkl` after first pull |

Command:

```bash
export POLYGON_API_KEY=$(cat /tmp/.polygon_key)
cd artifacts/stock-scanner-api
python3 backtest_spy_three_patterns.py \
  --symbol SPY --start 2026-02-05 --end 2026-08-05 \
  --minute-cache /tmp/spy_6mo_bars_recheck.pkl \
  --out-json ../../docs/verification/spy-three-pattern-recheck.json
```

## Results (ranked by net P&L)

| Pattern | Trades | Win% | Net P&L | Avg/trade |
|---------|-------:|-----:|--------:|----------:|
| VWAP_REVERSION_BUGGY_REF | 587 | 49.2% | **+$18,918.87** | +$32.23 |
| VWAP_REVERSION_FIXED | 364 | 20.1% | **+$389.80** | +$1.07 |
| MACD_HIST_DIVERGENCE | 1 | 0.0% | −$27.44 | −$27.44 |
| BB_EXHAUSTION | 8 | 37.5% | −$292.32 | −$36.54 |

### Exit reasons

| Pattern | STOP | TARGET | EOD / MAX_HOLD |
|---------|-----:|-------:|---------------:|
| VWAP FIXED | 284 | 57 | 23 EOD |
| VWAP BUGGY | 507 | 57 | 23 EOD |
| MACD | 0 | 0 | 1 MAX_HOLD |
| BB Exhaustion | 4 | 3 | 1 MAX_HOLD |

## VWAP mistake — confirmed

Prior Pattern Lab headline (+$18,919 / 49% WR) **reproduced exactly** on the
same bars with the old rule (`VWAP_REVERSION_BUGGY_REF`).

| Audit | Count |
|-------|------:|
| Buggy inverted stops (stop on profit side of entry) | **216** |
| Fixed inverted stops | **0** |
| Entries rejected for being already beyond the 3σ stop band | 28 |

**Fix applied:** enter only when `entry_sd <= |dev_sd| < stop_sd` (default 2.0 / 3.0),
and reject any LONG/SHORT with inverted stop/target geometry in the ledger.

With valid geometry, VWAP is roughly flat (+$390 over 6 months at $100 risk) —
**not** an $18k edge. Do not promote VWAP live off the buggy number.

## Pattern definitions used

### VWAP Reversion (fixed)
- Session VWAP + expanding σ of (close − VWAP); ADX(14) &lt; 20
- SHORT when `+2σ ≤ dev &lt; +3σ` → target VWAP, stop `VWAP+3σ`
- LONG when `−3σ &lt; dev ≤ −2σ` → target VWAP, stop `VWAP−3σ`
- Intraday; flatten ≥ 15:55 ET

### MACD Histogram Divergence (daily)
- MACD(12,26,9) histogram
- Bullish: price swing lower-low + hist higher-low at those pivots
- Bearish: price swing higher-high + hist lower-high
- Entry on confirmation bar (pivot + 3); stop beyond pivot extreme; 2R target; max 10-day hold
- Sparse on SPY daily in this window (**1 trade**)

### Bollinger Band Exhaustion (daily)
- BB(20, 2σ)
- Long: prior close below lower band, current close reclaims inside
- Short: prior close above upper band, current close reclaims inside
- Stop beyond exhaustion extreme; target mid-band / 1.5R; max 8-day hold
- **8 trades**, net −$292

## Verdict

1. **VWAP** — prior result was a **stop-geometry bug**; fixed rule is ~breakeven, not a star.
2. **MACD Histogram Divergence** — too few SPY daily signals in 6 months to claim edge (n=1).
3. **Bollinger Band Exhaustion** — small sample, **negative** net on SPY daily (−$292 / 8 trades).

None of the three clears a bar for live Pattern Lab promotion on this evidence.
Machine JSON: `docs/verification/spy-three-pattern-recheck.json`.
