# AIEM Pattern Engine — Completed Delivery

**Project:** StockScanner AI / AIEM  
**Delivery date:** July 17, 2026  
**Scope:** 50+ pattern engine across 5 families, full CCS integration, daily proof logs, pipeline isolation proof

---

## Verified Completed Work

### Pre-existing item closed

| Item | Verification | Result |
|------|-------------|--------|
| Item 17 resubmit | `verify_item_17_nodelete.py` — PASS=79 FAIL=0 | SHA-256 before=`559967af` after=`6589b92b`; 10 rows backfilled |

---

## New Files Built (3,997 lines total)

### 1. `candlestick_patterns.py` — 659 lines
**50 candlestick patterns across 4 sub-categories**

| Sub-category | Count | Patterns |
|---|---|---|
| Single-candle | 14 | Doji, Long-Legged Doji, Dragonfly Doji, Gravestone Doji, Marubozu (Bull/Bear), Hammer, Inverted Hammer, Hanging Man, Shooting Star, Spinning Top, High Wave, Belt Hold (Bull/Bear) |
| Two-candle | 15 | Bullish/Bearish Engulfing, Bullish/Bearish Harami, Tweezer Top/Bottom, Piercing Line, Dark Cloud Cover, Bullish/Bearish Kicker, On Neck, In Neck, Thrusting, Bullish/Bearish Separating Lines |
| Three-candle | 15 | Morning/Evening Star, Morning/Evening Doji Star, Three White Soldiers, Three Black Crows, Three Inside Up/Down, Three Outside Up/Down, Bullish/Bearish Abandoned Baby, Upside/Downside Tasuki Gap, Mat Hold |
| Multi-candle | 6 | Rising/Falling Three Methods, Stick Sandwich, Ladder Top/Bottom, Bullish/Bearish Breakaway |

Each pattern returns a structured `PatternResult` dict: `{pattern, category, direction, confidence, reason, bar_index}`.  
Data source: `polygon_market_daily` (AIEM-owned Polygon data, not website scanner).

---

### 2. `price_structure_patterns.py` — 701 lines
**30+ chart structure patterns**

Head & Shoulders, Inverse H&S, Double Top/Bottom, Triple Top/Bottom, Complex H&S, Ascending/Descending/Symmetrical Triangle, Ascending/Descending Channel, Bull/Bear Flag, Bull/Bear Pennant, Cup & Handle, Inverted Cup & Handle, Diamond Top/Bottom, Broadening Top/Bottom, Wedge (Rising/Falling), Rounded Top/Bottom, Gap (Breakaway/Runaway/Exhaustion/Common), Island Reversal (Bull/Bear), Measured Move.

Uses swing-point detection with ATR-normalized tolerance for noise resistance.

---

### 3. `aiem_harmonic_patterns.py` — 436 lines
**9 harmonic patterns with full XABCD Fibonacci ratio validation**

| Pattern | Completion Point | Key Fibonacci Ratios |
|---|---|---|
| Gartley | D | XA=0.618, AB=0.382–0.886, BC=0.382–0.886, CD=1.272–1.618 |
| Bat | D | XA=0.382–0.500, AB=0.382–0.886, BC=0.382–0.886, CD=1.618–2.618 |
| Butterfly | D | XA=0.786, AB=0.382–0.886, BC=0.382–0.886, CD=1.272–1.618 |
| Crab | D | XA=0.382–0.618, AB=0.382–0.886, BC=0.382–0.886, CD=2.618–3.618 |
| Deep Crab | D | XA=0.886, CD=2.0–3.618 |
| Shark | C | BC=1.130–1.618, CD=0.886–1.130 |
| Cypher | D | XA=0.382–0.618, BC=1.272–1.414, CD=0.786 |
| AB=CD | D | AB=CD leg equality ±10% |
| Three Drives | D | Three equal-leg drive structure with Fibonacci confluence |

Tolerance: ±10% on all ratio checks. All patterns return PRZ (Potential Reversal Zone) levels as `key_levels`.

---

### 4. `aiem_wyckoff_vpa.py` — 613 lines
**15 detectors — 7 VPA + 8 Wyckoff**

**VPA (Volume Price Analysis):**
- Volume Climax (exhaustion), Shakeout (false breakdown + recovery), No Demand (narrow bar + low vol), No Supply (narrow bar + low vol on decline), Stopping Volume (high vol + close near low), Effort vs Result (high vol + small range), Volume Dry-Up (multi-bar volume collapse)

**Wyckoff:**
- Selling Climax (SC), Buying Climax (BC), Spring (test of support on low vol), Upthrust (test of resistance on low vol), Sign of Strength (SOS), Sign of Weakness (SOW), Accumulation Phase (range-bound + declining vol), Distribution Phase (range-bound + elevated vol)

---

### 5. `aiem_elliott_wave.py` — 412 lines
**7 Elliott Wave pattern types via zigzag decomposition**

| Type | Description |
|---|---|
| Impulse (5-wave) | Waves 1-5 with 3 inviolable rules enforced |
| ABC Correction | Three-wave countertrend |
| Zigzag (5-3-5) | Sharp correction with Wave C beyond A |
| Flat (3-3-5) | Sideways correction, Wave B near Wave A origin |
| Triangle (3-3-3-3-3) | Contracting five-wave structure |
| Double Three | Two corrective patterns joined by X wave |
| Triple Three | Three corrective patterns joined by X waves |

**Three inviolable EW rules enforced:**
1. Wave 2 never retraces more than 100% of Wave 1
2. Wave 3 is never the shortest impulse wave
3. Wave 4 never overlaps Wave 1 price territory

All EW patterns are flagged `status: "forming"` (probabilistic, not confirmed) with a confidence score.

---

### 6. `aiem_pattern_registry.py` — 368 lines
**DB-backed pattern registry — table: `aiem_pattern_registry`**

- 107 patterns registered on `build_registry()` call (auto-syncs from all 5 family modules)
- SHA-256 fingerprint per pattern function — detects if implementation changes
- Status lifecycle: `UNTESTED` → `PASS` / `FAIL`
- Fields: `pattern_name`, `family`, `direction`, `enabled`, `status`, `precision`, `recall`, `false_positives`, `false_negatives`, `notes`, `function_sha256`
- **Only `PASS`-status patterns contribute to `pattern_score` in CCS**
- `UNTESTED` patterns are detected and logged but treated as neutral (score=0.5)
- `FAIL` or `disabled` patterns are skipped entirely

---

### 7. `aiem_pattern_engine.py` — 244 lines
**Unified coordinator — single import for `aiem_strat_scheduler.py`**

```
detect_for_ticker(ticker, thesis, lookback=60)
  → fetch_ohlcv_bars()        pulls polygon_market_daily
  → candlestick_patterns      50 patterns
  → price_structure_patterns  30+ patterns
  → aiem_harmonic_patterns    9 patterns
  → aiem_wyckoff_vpa          15 patterns
  → aiem_elliott_wave         7 patterns
  → _compute_pattern_score()  PASS-only weighted aggregate
```

Returns: `{pattern_score, pass_only_score, all_patterns, candlestick, chart_structure, harmonic, wyckoff_vpa, elliott_wave, bars_used, ticker, thesis, detected_at}`

**`pattern_score` scale:**
- `0.0` = strong contra-thesis patterns confirmed
- `0.5` = neutral / no confirmed PASS patterns
- `1.0` = strong thesis-confirming patterns confirmed

**PASS-pattern cache:** 5-minute TTL to avoid a DB hit on every job.

---

### 8. `aiem_pipeline_proof.py` — 246 lines
**Daily proof logger — table: `aiem_pipeline_proof_log`**

Automatically records 4 structured stages per job:

| Stage | What it proves |
|---|---|
| `seed_isolation_proof` | AIEM seeded from `polygon_rvol_scan` (not website scanner); includes `_check_no_website_import()` scan of scheduler source |
| `pattern_scan` | Pattern detection ran; records `pattern_score`, all detected pattern names, bar count, family breakdown |
| `decision` | TRADE/NO_TRADE decision with reason, `pattern_score`, best CCS, market regime |
| `paper_trade` | Paper trade inserted; records trade ID, strategy, CCS, `score_pattern_confirmation` component |

Each row has a SHA-256 of its `stage_data` for tamper evidence.

`python aiem_pipeline_proof.py` prints the daily summary report at any time.

---

### 9. `verify_pattern_registry.py` — 318 lines
**Standalone verification script**

- Checks `aiem_pattern_registry` table exists and has the expected columns
- Verifies function SHA-256 values match live code (detects silent changes)
- Confirms `pattern_score` param is present in `compute_capital_compounding_score()`
- Confirms `pattern_confirmation` key is in `SCORE_WEIGHTS`
- Confirms weights still sum to 1.0
- Queries `aiem_pipeline_proof_log` and reports today's proof stages
- Reports PASS/FAIL per check with raw DB output

---

## Modified Files

### `aiem_strat_engine/config.py`
**`SCORE_WEIGHTS` updated — 11 keys, sum = exactly 1.0000**

| Key | Old | New | Change |
|---|---|---|---|
| `pop` | 0.20 | 0.18 | −0.02 |
| `ev_after_costs` | 0.20 | 0.18 | −0.02 |
| `capital_preservation` | 0.15 | 0.14 | −0.01 |
| `pattern_confirmation` | — | **0.05** | +0.05 (new) |
| All others | unchanged | unchanged | — |

---

### `aiem_strat_engine/scoring.py`
**`compute_capital_compounding_score()` updated**

- New parameter: `pattern_score: float = 0.5` (default = neutral; backward-compatible)
- New component: `sc_pattern = _clamp(float(pattern_score))`
- Added to `raw_score`: `sc_pattern * w["pattern_confirmation"]`
- New return key: `"score_pattern_confirmation"` (stored to DB alongside all other component scores)

---

### `aiem_strat_scheduler.py`
**`_run_one_job()` and `_seed_candidates()` updated**

In `_seed_candidates()`:
- After seeding jobs, calls `aiem_pipeline_proof.log_seed_proof()` — records source table, candidate count, sample tickers, isolation check result

In `_run_one_job()` (between regime detection and strategy build):
- Calls `detect_for_ticker(ticker, thesis, lookback=60)`
- Logs `pattern_scan` proof stage (pattern_score, family counts, all pattern names)
- Passes `pattern_score` to every `compute_capital_compounding_score()` call in the evaluation loop
- Logs `decision` proof stage (decision, reason, CCS, pattern_score, regime)
- Logs `paper_trade` proof stage when a trade is inserted (trade ID, strategy, CCS, `score_pattern_confirmation`)
- Telegram alert for paper trades now includes `Pattern: {pattern_score:.3f}` line

---

## Pipeline Isolation Proof

`aiem_strat_scheduler._seed_candidates()` reads exclusively from `polygon_rvol_scan`.  
`polygon_rvol_scan` is populated by AIEM's own Polygon grouped-daily scan (runs 8:35 AM ET).  
There are zero imports of `main.py`, `_mkt_gap_volume_scan`, `_mkt_nano_cap`, or `website_scanner` in `aiem_strat_scheduler.py`.  
This is verified programmatically on every seed run and written as `seed_isolation_proof` to `aiem_pipeline_proof_log`.

---

## On-Hold Item

**DELETE on `ase_rpt_weekly_2099-01-01_48fc66fb`** — held pending explicit go/no-go from Joel. No action taken.

---

## Daily Proof Query

```sql
SELECT stage, ticker, thesis, stage_data, sha256, logged_at
FROM aiem_pipeline_proof_log
WHERE scan_date = CURRENT_DATE
ORDER BY logged_at ASC;
```

```bash
# Human-readable report
python3 artifacts/stock-scanner-api/aiem_pipeline_proof.py

# Full verification
python3 artifacts/stock-scanner-api/verify_pattern_registry.py
```
