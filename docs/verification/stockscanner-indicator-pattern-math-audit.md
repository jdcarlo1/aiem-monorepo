# StockScanner indicator & pattern math audit (2026-08-05)

Full formula audit of StockScanner AI (`nclexai.org`) — every classic indicator,
options/flow metric, composite score, pattern detector, and track-record
probability definition that the site uses.

Live API probes: `https://nclexai.org/stock-api/*` (same day as this report).
Code under `artifacts/stock-scanner-api/` + `artifacts/stock-scanner/`.

---

## Executive summary

| Verdict | Count |
|---------|------:|
| CORRECT | 40+ |
| WRONG (fixed in this PR) | 5 |
| WRONG (needs schema approval — not migrated) | 1 |
| SUSPECT / policy | 6 |

**Fixed here:** AEIM v3 MACD stub, live-path MFI sign, Sweep Track `0.0→null`,
0DTE stop UI (−20% → −6%), Bear Flow score-trace field (`pct_change` →
`close_strength`).

**Not migrated (needs Joel approval):** `unusual_puts_log` unique constraint —
current `ON CONFLICT DO NOTHING` is a no-op (table has no unique key besides `id`).

---

## A. Classic technical indicators

| Name | Path | Formula | Verdict |
|------|------|---------|---------|
| RSI-14 | `indicators.py`, `main.py` snapshot + backfill | Wilder | **CORRECT** |
| RSI-14 | `aiem_v3_technical._rsi` | SMA of last 14 gains/losses (not Wilder) | **SUSPECT** (mild; different from main) |
| MACD(12,26,9) | `indicators.py`, `main.py` | EMA12−EMA26; signal EMA9; hist=diff | **CORRECT** |
| MACD(12,26,9) | `aiem_v3_technical._macd` | Was `signal=0`, `hist=0.9×macd` | **WRONG → FIXED** |
| Bollinger(20,2) | `main.py` / `indicators.py` | SMA ± 2σ | **CORRECT** |
| BB %B scale | snapshot ×100 vs v3 0–1 | Inconsistent scale | **SUSPECT** |
| BB std ddof | `np.std` (0) vs `rolling.std` (1) | Band width differs slightly | **SUSPECT** |
| ATR-14 | Wilder TR | **CORRECT** |
| Stochastic %K/%D | 14 / SMA3 | **CORRECT** |
| Williams %R | standard | **CORRECT** |
| CCI-20 | TP / 0.015·MAD | **CORRECT** |
| ADX/DMI-14 | Wilder (main) | **CORRECT**; v3 returns `None` | **SUSPECT** (v3 incomplete) |
| Parabolic SAR | AF 0.02→0.20 | **CORRECT** |
| Keltner | EMA20 ± 1.5·ATR | **CORRECT** |
| OBV | close-up/down volume | **CORRECT** |
| MFI-14 live snapshot | signed by **close** Δ | **WRONG → FIXED** (now TP Δ) |
| MFI-14 backfill | signed by TP Δ | **CORRECT** |
| CMF-20 | CLV·V / ΣV | **CORRECT** |
| EMA / SMA | standard | **CORRECT** |
| VWAP | Σ(TP·V)/ΣV, TP=(H+L+C)/3 | **CORRECT** (`vwap_indicators.py`) |
| Hurst | R/S on log-returns | **CORRECT** |
| VPIN | period buckets (`len//50`), not fixed-volume | **SUSPECT** vs Easley |
| close_strength | `(C−L)/(H−L)` ∈ [0,1] | **CORRECT** |
| RVOL | vol / avg prior (20d or 30d by tab) | **CORRECT** (document window per tab) |
| Ichimoku | — | **N/A** (not implemented) |

---

## B. Options / flow metrics

| Name | Formula | Verdict |
|------|---------|---------|
| Vol/OI | `vol / max(oi, 1)` | **CORRECT** |
| Premium ($) | `vol × mid × 100` | **CORRECT** |
| Call OTM% | `(strike−price)/price×100` | **CORRECT** |
| Put OTM% | `(price−strike)/price×100` (+ = OTM put) | **CORRECT** |
| Mid | `(bid+ask)/2` else last | **CORRECT** |
| Put fill-side (BUY/SALE) | last vs 0.97·ask / 1.03·bid | **SUSPECT** heuristic |
| Put unusual_score | voi/prem/urgency/otm − spread → 0–100 | **CORRECT** (custom) |

Live spot-check (MICRO/SMALL CALLS `MMED`): vol_oi=5.0 matches `80/16`; otm
`(+1.96%)` matches `(12.5−12.26)/12.26`.

---

## C. Composite / conviction scores

| Score | Formula | Verdict |
|-------|---------|---------|
| High Conviction | `ln(voi+1)×(ln(prem_M+1)+1)×iv_bonus×sweep×urgency` | **CORRECT** |
| Bear Flow | 30 put + 25 regime + 25 tech + 20 smart money | **CORRECT** weights; UI field **FIXED** |
| 8-layer conviction | Layers 0–2 pts each + regime mult | **CORRECT** as designed |
| Insider Radar | rarity≤30 + prem≤25 + voi≤25 + earnings≤20 | **CORRECT** (heuristic) |
| ICS (React checklist) | Σ weights / 120 × 100 | **CORRECT**; ≠ Call Sweep ICS |
| Call Sweep ICS | Weighted /114 + holy-grail | **CORRECT** as designed |
| Standout flow | `rvol×(chg/10)×min(flow,10)×gap` | **CORRECT** (custom) |
| Gamma FIR score | `FIR×vol_oi×(1+mom/10)`; delta=sigmoid approx | score **CORRECT**; delta **SUSPECT** |
| OI buildup pts | ≥50%→2, ≥25%→1.5 | **CORRECT** |

---

## D. Pattern detectors

| Pattern | Rule (brief) | Verdict |
|---------|--------------|---------|
| Candlesticks | Body/shadow ratios (doji/hammer/engulfing…) | **CORRECT** |
| ORB | 9:30–9:59 OR; close > ORH×1.003; RVOL filter | **CORRECT** |
| Washout complete | d2 ret + weak CS + rvol → saturating score | **CORRECT** (custom) |
| Far-OTM sweep L7 | voi≥10→2, ≥7→1.5 | **CORRECT** |
| EOD sweep grade | Same HC-style buckets | **CORRECT**; recent-row null bug **FIXED** |
| Gap+Vol / Grinder / Runner / 52wk / Squeeze | Custom scanners | **CORRECT** as designed (spot-checked) |

---

## E. Track-record / probability math

| Item | Definition | Verdict |
|------|------------|---------|
| Sweep WR | `return > 0` = win; flat = loss | **SUSPECT** policy (document) |
| Sweep horizons | Trading-day T+1 / T+3 / T+5 | **CORRECT** (UI labels match API) |
| Sweep `0.0` returns | Was coerced to `null` via `if r[i]` | **WRONG → FIXED** |
| Conviction outcomes | d1/d3/d5 settled-only WR/EV | **CORRECT** |
| 0DTE paper target/stop | +50% / **−6%** of entry premium | Code **CORRECT**; UI said −20% → **FIXED** |
| 0DTE avg loss card | Stats = all-time; trade list = days filter | **SUSPECT** (window mismatch) |
| Win rate 28/125 = 22.4% | Live `/0dte/paper-stats` | **CORRECT** |

### Live Sweep Track numbers (recomputed)

API overall: t1 WR 32.1% (n=56), t3 55.6% (n=27), t5 74.1% (n=27).
Identical 55.6% on overall/EOD/EXTREME t3 is **expected** when the graded
cohort is the same set — denominators for “signals logged” include ungraded
rows and must not be used as WR denominators.

---

## F. Persistence / data-storage issues found during math audit

| Tab | Issue | Severity |
|-----|-------|----------|
| Unusual Puts | Live scan often returns 0 hits (tight filters / Tradier); **no `/unusual-puts-log` route** (404); `ON CONFLICT` no-op | High |
| Insider Radar | Unbounded 90d dump (~38k signals); Outcomes require `earnings_date` → often empty | Medium |
| Bear Flow cards | EXTREME/HIGH/MID = 0 when all `bearish_score < 45` — math OK, easy to misread | Low |
| Many Yahoo-backed tabs | Empty/stale when Yahoo throttles (52wk, multi-signal, earnings, squeeze…) | Ops |

---

## Fixes in this PR

1. `aiem_v3_technical._macd` — real EMA9 signal + hist = macd − signal
2. `main.py` live MFI — sign on typical-price change
3. `main.py` eod-sweep-track-record recent rows — `is not None` for 0.0
4. `Dashboard.tsx` 0DTE banner — stop −6%
5. `Dashboard.tsx` + `api.ts` Bear Flow — `close_strength` not `pct_change`

## Still needs Joel approval (schema)

```sql
-- Proposed only — NOT applied
-- Deduplicate unusual_puts_log first, then:
ALTER TABLE unusual_puts_log
  ADD CONSTRAINT unusual_puts_log_uq
  UNIQUE (ticker, strike, expiry, (last_seen::date));
```

Also recommend: `/stock-api/unusual-puts-log` GET mirror of `/unusual-calls-log`.

---

## How to re-verify

```bash
# Live probes
curl -s https://nclexai.org/stock-api/0dte/paper-stats | jq .
curl -s https://nclexai.org/stock-api/eod-sweep-track-record | jq '.overall'
curl -s https://nclexai.org/stock-api/bear-flow | jq '.results[0].score_breakdown.tech_detail'
curl -s https://nclexai.org/stock-api/unusual-puts | jq .
curl -s -o /dev/null -w '%{http_code}\n' https://nclexai.org/stock-api/unusual-puts-log

# Local MACD unit check
cd artifacts/stock-scanner-api && python3 -c "from aiem_v3_technical import _macd; print(_macd([100+i*0.5 for i in range(60)]))"
```
