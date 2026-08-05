# Directive_PatternLabBacktest_EvidenceCheck_2026-08-05

Evidence-only verification of PR #22. No VWAP-live build work.

---

## 1. Raw invocation + console output

**Command (literal):**
```bash
export POLYGON_API_KEY=$(cat /tmp/.polygon_key) && cd /workspace/artifacts/stock-scanner-api && python3 backtest_pattern_lab.py --symbol SPY --start 2026-02-05 --end 2026-08-05
```

**`--start` / `--end`:** `2026-02-05` → `2026-08-05` (confirmed).

**Full raw stdout** (from `/tmp/backtest_6mo.log`, captured at run time):
```
Fetching REAL 1-min bars for SPY 2026-02-05 -> 2026-08-05 from Polygon...
  fetching SPY 2026-02-05 → 2026-03-01…
  fetching SPY 2026-03-01 → 2026-04-01…
  fetching SPY 2026-04-01 → 2026-05-01…
  fetching SPY 2026-05-01 → 2026-06-01…
  fetching SPY 2026-06-01 → 2026-07-01…
  fetching SPY 2026-07-01 → 2026-08-01…
  rate-limited, sleep 1s…
  rate-limited, sleep 2s…
  rate-limited, sleep 4s…
  rate-limited, sleep 8s…
  rate-limited, sleep 16s…
  rate-limited, sleep 32s…
  fetching SPY 2026-08-01 → 2026-08-05…
Fetched 113812 real bars across 124 sessions.

======================================================================
PATTERN             TRADES    WIN%       NET P&L     AVG/TRADE
======================================================================
VWAP_REVERSION         587   49.2%     18918.87$        32.23$
ORB                    431   46.9%      1392.59$         3.23$
WEEKLY_MACRO_ORB       390   48.5%      1309.19$         3.36$
HIGH_BETA_ORB          384   46.4%       956.50$         2.49$
LIQUIDITY_SWEEP        367   46.6%       624.03$         1.70$
GAP_FILL                39   23.1%       357.21$         9.16$
======================================================================
Ranked by real net P&L over the actual test period, $100 fixed risk/trade, no compounding.
```

---

## 2. Real Polygon pull vs cache

### Baseline run (item 1) — **Polygon HTTP, no local cache**

- `fetch_polygon_minute_bars` in `backtest_pattern_lab.py` only hits
  `https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/minute/{start}/{end}`.
- There is **no** read of `td_intraday_cache`, parquet, or any other local bar store in that function.
- Console shows month-chunk fetches + HTTP **429 rate-limit backoff** — that path only exists in the Polygon HTTP client.

### Live probe (re-confirmed this evidence pass)

```
REQUEST GET https://api.polygon.io/v2/aggs/ticker/SPY/range/1/minute/2026-02-05/2026-02-06?adjusted=true&sort=asc&limit=5&apiKey=***REDACTED***
HTTP status=200 elapsed_ms=186
body: status=OK ticker=SPY resultsCount=5 request_id=1d5ff99d37b220ba46fdc6f2c1df2a9a
first bar: o=686.03 h=686.03 l=685.52 c=685.82 v=5045 t=1770282000000
```

### Cache disclosure (later sessions only)

| Run | Data source |
|-----|-------------|
| Baseline table in PR #22 (`/tmp/backtest_6mo.log`) | Polygon HTTP only |
| Ad-hoc R-sweep (not in committed script) | Polygon HTTP, then wrote `/tmp/spy_6mo_bars.pkl` |
| This evidence pass IS trade-log dump | **Disclosed pickle** `/tmp/spy_6mo_bars.pkl` (same 113812 bars previously fetched from Polygon) |
| This evidence pass OOS window | Polygon HTTP only (110553 bars / 128 sessions) |

---

## 3. R-multiple sweep — code reality

### Verdict: **R-sweep was NOT in the committed product script**

PR #22 commit `1d84080e` adds:
- `backtest_pattern_lab.py` (+368) — **fixed R only**
- `pattern-lab-backtest-6mo.md` / `.json` — narrative + stored sweep *results*

Committed `main()` hardcodes:
```python
run_orb(..., range_minutes=15, target_r=2.0)          # ORB
run_orb(..., range_minutes=30, target_r=1.5, ...)     # WEEKLY_MACRO_ORB
run_liquidity_sweep(...)                              # default target_r=1.5
```

There is **no** `for tr in [1.0, 1.5, 2.0, 2.5, 3.0]` loop, no `--sweep` flag, and no R-sweep diff in the script.

### What really happened

After the baseline run, an **ad-hoc stdin Python session** (not committed) called the same `run_orb` / `run_liquidity_sweep` / `run_vwap_reversion` functions in loops over R / SD grids, wrote results into the markdown/JSON, and claimed “ORB prefers 3.0R / Weekly Macro prefers 2.5R.”

So the PR file diff is **not** enough code to have swept R inside the shipped script — the sweep existed only as a one-off operator session. The numbers in the markdown are reproducible by calling those functions with alternate `target_r` (reconfirmed below on IS pickle), but they were **not** produced by committed automation.

---

## 4. Out-of-sample check (best R locked, not re-optimized)

**OOS window:** 2025-08-05 → 2026-02-05  
**Source:** Polygon HTTP (`Fetched 110553 bars / 128 sessions`)  
**Locked from IS claims:** ORB `target_r=3.0`, Weekly Macro `target_r=2.5`, Liquidity Sweep `target_r=1.5`

| Variant | IS trades | IS win% | IS net | OOS trades | OOS win% | OOS net |
|---------|----------:|--------:|-------:|-----------:|---------:|--------:|
| ORB_r3.0 | 409 | 46.9% | +$1,811 | 399 | 42.6% | **+$2,774** |
| WM_r2.5 | 364 | 47.5% | +$1,803 | 347 | 41.8% | **+$1,151** |
| LS_r1.5 | 367 | 46.6% | +$624 | 359 | 39.8% | **−$6,147** |

OOS defaults (same fixed params as baseline script):

| Pattern | OOS trades | OOS win% | OOS net |
|---------|-----------:|---------:|--------:|
| ORB (2.0R) | 419 | 42.2% | +$2,140 |
| WEEKLY_MACRO_ORB (1.5R) | 367 | 42.2% | +$674 |
| VWAP_REVERSION | 723 | 46.5% | +$24,315 |
| LIQUIDITY_SWEEP (1.5R) | 359 | 39.8% | −$6,147 |
| GAP_FILL | 58 | 22.4% | −$1,519 |
| HIGH_BETA_ORB | 394 | 41.6% | −$704 |

**Plain reading:**
- ORB 3.0R and WM 2.5R **did not collapse** on OOS (still green; ORB 3.0R even higher net).
- Liquidity Sweep 1.5R **did collapse** (IS +$624 → OOS −$6,147) — that “best R” does **not** hold out of sample.
- VWAP still prints huge OOS P&L under the same code — but see item 5 (geometry bug).

---

## 5. VWAP Reversion trade-log spot check

Aggregate reconfirmed on disclosed IS pickle: **587 trades / 49.2% / +$18,918.87**.

### First 10 raw trade rows

| # | side | entry | stop | target | exit | pnl | reason | exit_ts |
|--:|------|------:|-----:|-------:|-----:|----:|--------|---------|
| 1 | SHORT | 686.1000 | 686.7796 | 684.4617 | 686.7796 | −99.90 | STOP | 2026-02-06 10:46 ET |
| 2 | SHORT | 686.8600 | 687.6797 | 685.1190 | 687.6797 | −99.19 | STOP | 2026-02-06 11:34 ET |
| 3 | SHORT | 688.0400 | 688.3071 | 685.5457 | 688.3071 | −99.90 | STOP | 2026-02-06 12:27 ET |
| 4 | SHORT | 688.3200 | 688.3430 | 685.5680 | 688.3430 | −99.98 | STOP | 2026-02-06 12:29 ET |
| 5 | SHORT | 688.7200 | **688.5563** | 685.7040 | 688.5563 | **+99.84** | STOP | 2026-02-06 12:47 ET |
| 6 | SHORT | 688.8700 | **688.5901** | 685.7204 | 688.5901 | **+99.91** | STOP | 2026-02-06 12:49 ET |
| 7 | SHORT | 688.8800 | **688.6302** | 685.7417 | 688.6302 | **+99.94** | STOP | 2026-02-06 12:51 ET |
| 8 | SHORT | 688.8450 | 689.0496 | 685.9479 | 689.0496 | −99.85 | STOP | 2026-02-06 13:18 ET |
| 9 | SHORT | 689.1400 | **689.0712** | 685.9602 | 689.0712 | **+99.96** | STOP | 2026-02-06 13:20 ET |
| 10 | SHORT | 689.1199 | **689.0961** | 685.9780 | 689.0961 | **+100.00** | STOP | 2026-02-06 13:22 ET |

Rows 5–7 and 9–10: **SHORT with stop below entry**, exited as `STOP` for ~+$100. For a short, stop must be above entry. This is inverted geometry.

### Cause

`run_vwap_reversion` enters whenever `|dev_sd| >= entry_sd` (default 2.0) and sets stop at `entry_sd` band `stop_sd` (default 3.0) from VWAP. If price is already **beyond** the stop band (`|dev| > stop_sd`), stop lands on the **profit** side of entry. Exit logic then hits “STOP” immediately for ~+$100. Fixed $100 risk + tiny inverted distance also allows absurd share counts (inverted median shares 540, max 59,596).

### Impact on the headline number

| Slice | Trades | Net P&L |
|-------|-------:|--------:|
| All VWAP IS | 587 | +$18,918.87 |
| Inverted-stop trades (bug) | **216 (36.8%)** | **+$21,563.87** |
| Geometrically valid trades only | 371 | **−$2,644.87** |

Exit mix: STOP 507 / TARGET 57 / EOD 23.

**Plain reading:** the +$18,919 headline is **not trustworthy as edge**. After removing inverted-stop trades, VWAP is **net negative** on the same IS window. Do **not** add VWAP Reversion live on the basis of PR #22’s aggregate table.

---

## Decision implication (evidence only)

| Claim from PR #22 | Evidence status |
|-------------------|-----------------|
| Baseline 6-mo table | **Confirmed** by raw stdout + Polygon fetch |
| Data from Polygon | **Confirmed** for baseline; pickle used only later (disclosed) |
| “ORB prefers 3.0R / WM prefers 2.5R” from committed sweep code | **False** — sweep was ad-hoc, not in script; OOS still green for those locked R values |
| LS 1.5R is robust | **Fails OOS** (−$6,147) |
| VWAP +$18,919 justifies live add | **Fails sanity check** — 36.8% of trades have inverted stops; valid-only net ≈ −$2,645 |

**Recommendation:** do not ship VWAP Reversion live until entry requires `|dev_sd| < stop_sd` (or equivalent) and the backtest is re-run clean.
