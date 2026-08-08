# Backtest Realistic-Fill Re-run — 2026-08-08 (v2)

**Generated (UTC):** 2026-08-08T15:43:25.078344+00:00
**Reproduce:** `cd artifacts/stock-scanner-api && python3 scripts/rerun_backtest_realistic_fills_2026_08_08.py`
**Paper engine reference:** `c3edc5fd`

## Scope — strategies actually evaluated

### Live on Pattern Lab + OE Strategies (full set ran — none silently dropped)
- `GAP_FILL` — Gap Fill (equity; surface: Pattern Lab (AIEM))
- `ORB` — Opening Range Breakout (equity; surface: Pattern Lab (AIEM))
- `F3_SPY_0DTE` — F3 SPY 0DTE (options; surface: Pattern Lab (AIEM) + OE Strategies)

### Directive-named strategies not currently live on those surfaces
- `long_call_condor` → catalog `LONG_CALL_CONDOR` — **CATALOG_ONLY_NOT_LIVE** / Version B **CANNOT_VERIFY**
- `long_put_condor` → catalog `LONG_PUT_CONDOR` — **CATALOG_ONLY_NOT_LIVE** / Version B **CANNOT_VERIFY**
- `narrow_wing_butterfly` → catalog `None` — **NOT_IN_CATALOG** / Version B **CANNOT_VERIFY**
- `call_butterfly` → catalog `LONG_CALL_BUTTERFLY` — **CATALOG_ONLY_NOT_LIVE** / Version B **CANNOT_VERIFY**
- `put_butterfly` → catalog `LONG_PUT_BUTTERFLY` — **CATALOG_ONLY_NOT_LIVE** / Version B **CANNOT_VERIFY**
- `put_ladder` → catalog `None` — **NOT_IN_CATALOG** / Version B **CANNOT_VERIFY**
- `bullish_risk_reversal` → catalog `RISK_REVERSAL` — **CATALOG_ONLY_NOT_LIVE** / Version B **CANNOT_VERIFY**

### Additional OE catalog strategies also not live (37 of 42 catalog ids — listed for completeness)
- `LONG_CALL`
- `LONG_PUT`
- `SHORT_CALL`
- `SHORT_PUT`
- `COVERED_CALL`
- `CSP`
- `PROTECTIVE_PUT`
- `COLLAR`
- `BULL_CALL_SPREAD`
- `BEAR_PUT_SPREAD`
- `BEAR_CALL_SPREAD`
- `BULL_PUT_SPREAD`
- `CALENDAR_CALL`
- `CALENDAR_PUT`
- `DIAGONAL_CALL`
- `DIAGONAL_PUT`
- `LONG_STRADDLE`
- `SHORT_STRADDLE`
- `LONG_STRANGLE`
- `SHORT_STRANGLE`
- `IRON_CONDOR`
- `IRON_BUTTERFLY`
- `RATIO_CALL_SPREAD`
- `RATIO_PUT_SPREAD`
- `CALL_BACKSPREAD`
- `PUT_BACKSPREAD`
- `REVERSE_RISK_REVERSAL`
- `JADE_LIZARD`
- `BIG_LIZARD`
- `SYNTHETIC_LONG`
- `SYNTHETIC_SHORT`
- `CONVERSION`
- `REVERSAL`
- `BOX_SPREAD`
- `CUSTOM_MULTI_LEG`
- `STOCK_LONG_CALL`
- `STOCK_LONG_PUT`

## Version definitions

- **Version A** = as-published backtest assumptions / stored `real_dollar` (F3) or pre-c3edc5fd OE cost model (proof fixture).
- **Version B** = live paper engine after c3edc5fd: ask entry, bid exit, `0.65*legs*qty*2` fees, dual half-spread slippage with `quantity=qty*n_legs`, intrinsic only at true expiry settle.
- **Hard rule:** if real historical bid/ask is unavailable → **CANNOT VERIFY**. No synthetic spreads.

## Comparison table — live site strategies

| Strategy | Trades | A P&L | B P&L | $ Diff | % Overstated | A WR | B WR | A avg $/trade | B avg $/trade | A max DD | B max DD | A PF | B PF | Still profitable under B? |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| GAP_FILL | — | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | — | — | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | N/A |
| ORB | — | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | — | — | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | NO_PUBLISHED_TRADE_BOOK | N/A_EQUITY | N/A |
| F3_SPY_0DTE | 178 | 11899.26 | CANNOT_VERIFY | — | — | 42.7 | CANNOT_VERIFY | 66.85 | CANNOT_VERIFY | 2039.77 | CANNOT_VERIFY | 1.698 | CANNOT_VERIFY | CANNOT VERIFY |

## Side table — only quote-complete options trade (proof fixture)

> Not a live Pattern Lab / OE Strategies card. Included because it is the only archived closed options trade with real two-sided bid/ask at both entry and exit, so Version A vs B can be computed on the **same** quotes.

| Strategy | Trades | A P&L | B P&L | $ Diff | % Overstated | A WR | B WR | A avg | B avg | A max DD | B max DD | A PF | B PF | Still profitable under B? |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| IRON_CONDOR_PROOF_FIXTURE | 1 | 39.35 | -35.2 | 74.55 | 211.8 | 100.0 | 0.0 | 39.35 | -35.2 | 0.0 | 35.2 | inf | 0.0 | False |

- **FLIP:** Version A profitable (39.35) → Version B unprofitable (-35.2).
- Quotes: entry 1.2/1.4, exit 1.9/2.1; 4 legs × qty 1.
- Version A costs: fees $0.65, entry_slip $10.0, exit_slip $0.
- Version B costs: fees $5.2, entry_slip $40.0, exit_slip $40.0 (round-trip cost $85.20 vs gross move $50.00).

### Plain-language verdicts (live strategies)

**GAP_FILL**
- Pricing source: Equity OHLC (when backtested) — not options NBBO
- Version A: **NO_PUBLISHED_TRADE_BOOK** — backtest_pattern_lab.py references docs/verification/pattern-lab-backtest-6mo.md but that artifact was never committed. Live paper snapshots exist (pattern-lab-FINAL.md single-day) but are not a multi-trade historical book. POLYGON_API_KEY absent in this environment — refusing to invent equity fills.
- Version B: **N/A_EQUITY** (options fill model does not apply).

**ORB**
- Pricing source: Equity OHLC (when backtested) — not options NBBO
- Version A: **NO_PUBLISHED_TRADE_BOOK** — backtest_pattern_lab.py references docs/verification/pattern-lab-backtest-6mo.md but that artifact was never committed. Live paper snapshots exist (pattern-lab-FINAL.md single-day) but are not a multi-trade historical book. POLYGON_API_KEY absent in this environment — refusing to invent equity fills.
- Version B: **N/A_EQUITY** (options fill model does not apply).

**F3_SPY_0DTE**
- Pricing source: Polygon 1-min option bar CLOSE (MODELED as fill; not NBBO). Source file: artifacts/stock-scanner-api/f3_trade_comparison.csv
- Version A (as-published): trades=178, P&L=11899.26, WR=42.7%, avg=$66.85, maxDD=2039.77, PF=1.698, profitable=True
- Version B: **CANNOT_VERIFY** — f3_trade_comparison.csv stores Polygon 1-min bar CLOSE as entry_premium/exit_premium only. No historical bid/ask per contract. Per directive: do not substitute synthetic spreads.
- Flip profitable→unprofitable under B: **UNKNOWN — Version B CANNOT VERIFY**
- Version A note: As-published real_dollar = (exit_premium-entry_premium)/entry_premium*200 (=$200 notional). No paper_round_trip_fees. No dual half-spread slippage. No ask-entry / bid-exit differentiation — bar close used both sides.

### Directive-named / not live

- `long_call_condor` (CATALOG_ONLY_NOT_LIVE): Registered as `LONG_CALL_CONDOR` in OE strategy catalog/registry, but not currently saved/live as a Pattern Lab or OE Strategies card with an archived historical trade list + bid/ask. → Version A = NO_PUBLISHED_TRADE_BOOK; Version B = **CANNOT_VERIFY**.
- `long_put_condor` (CATALOG_ONLY_NOT_LIVE): Registered as `LONG_PUT_CONDOR` in OE strategy catalog/registry, but not currently saved/live as a Pattern Lab or OE Strategies card with an archived historical trade list + bid/ask. → Version A = NO_PUBLISHED_TRADE_BOOK; Version B = **CANNOT_VERIFY**.
- `narrow_wing_butterfly` (NOT_IN_CATALOG): Name `narrow_wing_butterfly` appears in the directive but is not present in _STRATEGY_CATALOG and is not a live Pattern Lab / OE Strategies card. → Version A = NO_PUBLISHED_TRADE_BOOK; Version B = **CANNOT_VERIFY**.
- `call_butterfly` (CATALOG_ONLY_NOT_LIVE): Registered as `LONG_CALL_BUTTERFLY` in OE strategy catalog/registry, but not currently saved/live as a Pattern Lab or OE Strategies card with an archived historical trade list + bid/ask. → Version A = NO_PUBLISHED_TRADE_BOOK; Version B = **CANNOT_VERIFY**.
- `put_butterfly` (CATALOG_ONLY_NOT_LIVE): Registered as `LONG_PUT_BUTTERFLY` in OE strategy catalog/registry, but not currently saved/live as a Pattern Lab or OE Strategies card with an archived historical trade list + bid/ask. → Version A = NO_PUBLISHED_TRADE_BOOK; Version B = **CANNOT_VERIFY**.
- `put_ladder` (NOT_IN_CATALOG): Name `put_ladder` appears in the directive but is not present in _STRATEGY_CATALOG and is not a live Pattern Lab / OE Strategies card. → Version A = NO_PUBLISHED_TRADE_BOOK; Version B = **CANNOT_VERIFY**.
- `bullish_risk_reversal` (CATALOG_ONLY_NOT_LIVE): Registered as `RISK_REVERSAL` in OE strategy catalog/registry, but not currently saved/live as a Pattern Lab or OE Strategies card with an archived historical trade list + bid/ask. → Version A = NO_PUBLISHED_TRADE_BOOK; Version B = **CANNOT_VERIFY**.

## Spread / liquidity census

### 1–3. Historical contracts traded by live strategies

- Pricing label: **Polygon 1-min CLOSE premiums in f3_trade_comparison.csv — MODELED/SYNTHESIZED as fills; NOT historical NBBO**
- F3 published trades examined: 178 (['2025-08-08', '2026-08-04'])
- Entry premium median / p75 / p90: 1.09 / 1.44 / 1.92
- Bid-ask spread (abs $) median / p75 / p90: **CANNOT VERIFY — no historical bid/ask**
- Bid-ask spread (% of mid) median / p75 / p90: **CANNOT VERIFY**
- % trades with real two-sided quotes at BOTH entry and exit: **CANNOT VERIFY — no quote-quality archive**
- Round-trip cost as % of max theoretical gain: **CANNOT VERIFY without bid/ask**

### 4. Is entry_slippage_est=40.00 / exit_slippage_est=40.00 representative?

**Verdict: OUTLIER / multi-leg formula artifact — NOT a representative single-contract percent-of-premium**

On the proof IRON_CONDOR, entry_slippage_est=40.00 equals half-spread $0.10 × 100 × (qty*n_legs=4). It is NOT '29% of a $1.40 premium'. A $0.20 package bid-ask on a 4-leg structure becomes $40 dollar slippage per side by construction of quantity=qty*n_legs. Treat as thin-quote × multi-leg multiplier effect, not a typical single-option spread percentage for SPY 0DTE.

Proof quotes: spread abs $0.20 (15.38% of mid $1.30). Half-spread $0.10 × 100 × 4 legs = $40.00/side.

### Tradeability finding (distinct from edge)

From archived Pattern Lab / OE Strategies backtests: CANNOT conclude whether round-trip cost routinely exceeds average gross gain — historical bid/ask is not archived for F3 (or for catalog multi-legs, which are not live). From the live IRON_CONDOR proof fixture: round-trip cost (fees $5.20 + dual slippage $80.00 = $85.20) EXCEEDED the favorable gross move ($50.00 on a +0.50 ask→bid package mark), producing realized_pnl=-35.20. That is a TRADEABILITY finding for thin multi-leg package quotes under the leg-multiplied slippage formula — distinct from 'the strategy has no edge.'

## Data honesty

- Version B computable for any live options strategy with current archives: **False**
- Blocking reason: No historical NBBO/bid-ask archive tied to published backtest trade lists. F3 uses Polygon bar closes. Equity Pattern Lab multi-month book not archived. Directive-named multi-legs are catalog-only with zero trade books.
- Unblock path: Archive bid, ask, mid, quote_quality at signal time and exit time per leg (or per package) for every backtest fill, then re-run Version B over the same trade IDs via version_b_live_engine().

## What this script does / does not do

- Does: inventory every live Pattern Lab / OE Strategies strategy; load as-published Version A for F3; attempt Version B only when real bid/ask exists; document the c3edc5fd proof cost structure; archive census.
- Does not: invent synthetic spreads; re-tune any strategy parameter; claim Version B P&L for F3 or catalog multi-legs without NBBO history.
