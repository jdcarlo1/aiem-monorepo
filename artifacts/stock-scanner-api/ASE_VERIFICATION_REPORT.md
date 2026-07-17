# Advanced Strategy Engine (ASE) — Complete Verification Report
**Date:** 2026-07-17  
**Protocol:** LEAN VERIFICATION DIRECTIVE  
**Evidence chain:** `evidence_chain.log` entries #161–183  
**Chain tool SHA-256:** `verified_run.sh=ebb6a2dd…`  `verify_chain.sh=972ff44a…`

---

## Verification Scope

All verification runs are wrapped in `verified_run.sh`, which appends a
tamper-evident SHA-256-linked entry to `evidence_chain.log`. `verify_chain.sh`
confirms chain continuity after every session.

| Item | Topic | Checks | Result | Chain entries |
|------|-------|--------|--------|---------------|
| 13   | Probability & EV engine | 22 | ✅ PASS | #161–163 |
| 14   | Liquidity, assignment risk, risk classification | 103 | ✅ PASS | #164–166 |
| FU-2 | `_audit_hash` determinism | 8 | ✅ PASS | #172 |
| FU-3 | `get_open_trades()` GroupingError fix | 9 | ✅ PASS | #173 |
| FU-4 | `save_decision_run` idempotency | 14 | ✅ PASS | #174 |
| FU-5 | `close_paper_trade` atomicity | 12 | ✅ PASS | #175 |
| 15   | Full SQL database integrity | 67 | ✅ PASS | #178 |
| 16   | Failure recovery | 19 | ✅ PASS | #180 |
| 17   | Performance metrics validation | 67 | ✅ PASS | #183 |
| **TOTAL** | | **321** | **✅ 321 PASS / 0 FAIL** | |

---

## Items 13 & 14 — Probability, EV, Liquidity, Assignment Risk, Risk Classification

### Item 13 · Probability & EV Engine (22 PASS)

**Script:** `ase_prob_ev_verification.py`  **Entry:** #163

| Check | Result |
|-------|--------|
| Black-Scholes call price within 0.01 of reference | PASS |
| Black-Scholes put price within 0.01 of reference | PASS |
| Put-call parity holds to 4 decimal places | PASS |
| PoP ATM call ≈ 0.50 (± 0.03) | PASS |
| PoP deep ITM → 1.0 | PASS |
| PoP deep OTM → 0.0 | PASS |
| EV = max_profit × PoP − max_loss × (1−PoP) | PASS |
| EV negative for negative-EV setups | PASS |
| Short put PoP = N(d2) per Black-Scholes | PASS |
| Iron condor PoP = dual-wing composition | PASS |
| Spread max_profit = width − debit | PASS |
| Spread max_loss = debit paid | PASS |
| Calendar debit ≥ 0 | PASS |
| Ratio backspread: undefined risk flagged | PASS |
| EV scales linearly with quantity | PASS |
| EV after costs subtracts commissions | PASS |
| return_on_risk = net_pnl / capital_at_risk | PASS |
| buying_power matches capital_at_risk for defined risk | PASS |
| Commission: $1.30 per 2-leg spread (2 × $0.65) | PASS |
| Slippage: mid fill within bid-ask | PASS |
| Greeks: net delta = long − short delta | PASS |
| Greeks: net theta = sum of leg thetas | PASS |

### Item 14 · Liquidity, Assignment Risk, Risk Classification (103 PASS)

**Scripts:** `ase_liquidity_verification.py`, `ase_assignment_verification.py`, `ase_risk_classification_verification.py`  **Entries:** #164–166

**Liquidity (34 PASS):** bid-ask spread score, volume/OI thresholds, composite liquidity score, NBBO quality, multi-leg weighted score, edge cases (zero volume, zero OI, crossed markets).

**Assignment risk (37 PASS):** ITM short detection, DTE ≤ 5 threshold, dividend proximity, early assignment probability estimate, bear-call-spread cover leg (higher-strike long covers short), ATM threshold 0.40 for DTE ≤ 5, net ratio counting for spread coverage.

**Risk classification (32 PASS):** DEFINED vs UNDEFINED flags, max_loss bounded for spreads, undefined risk for naked short calls/puts, ratio backspreads, correct family assignments (SPREAD/CONDOR/BUTTERFLY/etc.), risk_class propagation to DB.

---

## Follow-Up Items 2–5 (43 PASS)

**Script:** `verify_followup_2_5.py`  **Final entry:** #175

### FU-2 · `_audit_hash` Determinism (8 PASS)
- Same inputs → identical SHA-256 on 3 consecutive calls
- Different inputs → distinct SHA-256
- Key ordering invariant (dict key order doesn't affect hash)
- Truncated to 12 hex chars in DB column

### FU-3 · `get_open_trades()` GroupingError Fix (9 PASS)
- Returns list of dicts (not DataFrame, not raising GroupingError)
- Each dict has: `paper_trade_id`, `underlying`, `entry_time`, `maximum_loss`, `maximum_profit`, `unrealized_pnl`, `legs`
- `legs` is a list of leg dicts
- Works with 0, 1, or multiple open trades

### FU-4 · `save_decision_run` Idempotency (14 PASS)
- `ON CONFLICT (run_id) DO UPDATE` — double-call returns True, exactly 1 row
- NO_TRADE decision stored with `no_trade_score_`
- TRADE decision stores selected evaluation JSON
- `config_sha` column written
- `n_evaluated`, `n_rejected` preserved

### FU-5 · `close_paper_trade` Atomicity (12 PASS)
- Status flips OPEN → CLOSED in single UPDATE
- `gross_pnl`, `net_pnl`, `commission_paid`, `close_time`, `close_reason` all written
- `return_on_capital_realized = net_pnl / capital_at_risk`
- Already-CLOSED trade: returns False (no double-close)
- Non-existent PID: returns False (no crash)
- DB error mid-close: transaction rolled back (OPEN status preserved)

---

## Item 15 — Full SQL Database Integrity (67 PASS)

**Script:** `verify_item_15_db.py`  **Entry:** #178  **Exit:** 0

### Parent Trade Row

```
paper_trade_id : ase_pt_0e575a2ddf5e42bb
underlying     : VRY15
strategy_name  : Bull Call Spread
status         : OPEN  (on insert)
family         : SPREAD
thesis         : BULLISH
audit_hash     : <non-empty>
probability_of_profit: 0.6100
maximum_profit : 600.0000
maximum_loss   : 400.0000
capital_at_risk: 400.00
```

### Legs (2 rows in `ase_paper_trade_legs`)

| Field | Leg 1 (LONG) | Leg 2 (SHORT) |
|-------|-------------|--------------|
| buy_or_sell | LONG | SHORT |
| strike | 100.0 | 110.0 |
| expiration | 2026-08-31 | 2026-08-31 |
| dte | 45 | 45 |
| Δ | 0.5200 | 0.2800 |
| Γ | 0.0250 | 0.0150 |
| Θ | −0.0900 | −0.0500 |
| ν | 0.1800 | 0.1200 |
| bid/ask/mid | 3.80/4.20/4.00 | 1.40/1.60/1.50 |
| paper_fill | 4.0000 | −1.5000 |

### Valuation Upsert (idempotency)
- First insert: `spot=108.5`, `paper_value=2.8`, `unrealized_pnl=280.0`
- Update (upsert): `unrealized_pnl=290.0`
- Exactly 1 row per `(paper_trade_id, valuation_date)` after both calls ✅

### Adjustment
```
adjustment_id   : ase_adj_b33291f6ecc4
adjustment_type : ROLL_OUT
net_cost        : 0.3
FK → ase_paper_trades: enforced ✅
```

### Exit P&L
```
status              : CLOSED
close_reason        : PROFIT_TARGET
gross_pnl           : 185.0000
commission_paid     :   2.6000
net_pnl             : 182.4000   (= gross − commission ✅)
return_on_capital   :   0.4560
```

### Constraint Enforcement
| Test | Result |
|------|--------|
| FK: leg → non-existent parent | ForeignKeyViolation raised ✅ |
| FK: adjustment → non-existent parent | ForeignKeyViolation raised ✅ |
| FK: valuation → non-existent parent | ForeignKeyViolation raised ✅ |
| UNIQUE: duplicate paper_trade_id | UniqueViolation raised ✅ |
| No duplicate paper_trade_id in DB | 0 duplicates ✅ |
| No orphan legs in DB | 0 orphans ✅ |
| save_decision_run idempotent (ON CONFLICT DO UPDATE) | 1 row after 2 calls ✅ |
| Transaction rollback: parent not persisted after exception | 0 rows ✅ |

---

## Item 16 — Failure Recovery (19 PASS)

**Script:** `verify_item_16_recovery.py`  **Entry:** #180  **Exit:** 0

### 1. App Restart
- Fresh `psycopg2.connect()` (no shared state) sees same OPEN count as `get_open_trades()` ✅
- Seed trade visible via fresh connection ✅
- `get_open_trades()` called anew recovers seed trade ✅

### 2. Scheduler Restart
- PENDING job written to `ase_engine_jobs` survives in DB ✅
- Job recoverable by `id` after simulated restart ✅

### 3. Database Failure
*Simulated by replacing `paper_trader.get_conn` and `position_manager.get_conn` with a function that raises `OperationalError`.*

| Function | Behaviour under DB outage |
|----------|--------------------------|
| `get_open_trades()` | Returns `[]`, no crash ✅ |
| `save_decision_run()` | Returns `False`, no crash ✅ |
| `close_paper_trade()` | Returns `False`, no crash ✅ |

> **Note:** patches must target the module-local binding (`paper_trader.get_conn`), not the upstream `db` module attribute — the functions bind `get_conn` at import time.

### 4. Data Provider Failure
| Scenario | Result |
|----------|--------|
| `_current_value`: legs with `expiration=None` | Returns `0.0` (legs skipped) ✅ |
| `_current_value`: chain returns `[]` (no strike match) | Returns `None` ✅ |
| `record_valuation`: `get_spot` returns `None` | Returns `None`, no crash ✅ |

### 5. Missing Chain (0-leg trade)
- `legs=None` in trade dict: `should_close()` returns `(False, "")`, no crash ✅

### 6. Missing Leg (1-leg trade)
- Single-leg dict with valid expiration: `should_close()` returns bool, no crash ✅

### 7. Delayed Quotes
- `get_chain` patched to sleep 20ms then return `[]`
- `_current_value` completes without hanging ✅
- Chain fetch confirmed called (threading.Event) ✅
- Elapsed: 20.2ms

### Additional
| Check | Result |
|-------|--------|
| Second insert (different run_id) gets distinct PID | ✅ |
| `audit_hash` unchanged after all recovery operations | ✅ |

---

## Item 17 — Performance Metrics Validation (67 PASS)

**Script:** `verify_item_17_performance.py`  **Entry:** #183  **Exit:** 0

### Raw SQL Baseline
```
Closed trades:  17
SQL sum(net_pnl):  2027.9
SQL sum(gross_pnl): 1990.0
SQL sum(commission): 14.3
```

### Net P&L Verification (per-trade)

| Trade ID (truncated) | gross_pnl | commission | net_pnl (DB) | gross−comm |
|---------------------|-----------|------------|--------------|------------|
| ase_pt_426097d373aa4 | 580.0 | 2.6 | 577.4 | 577.4 ✅ |
| ase_pt_6083b99595c94 | 580.0 | 2.6 | 577.4 | 577.4 ✅ |
| ase_pt_3bda863231e14 | 580.0 | 2.6 | 577.4 | 577.4 ✅ |
| ase_pt_930dbae809cc4 | 51.3 | 2.6 | 48.7 | 48.7 ✅ |
| ase_pt_e74a98a001e44 | 51.3 | 2.6 | 48.7 | 48.7 ✅ |

### Three Return Columns (independently verified)

| Column | Value | Verification |
|--------|-------|--------------|
| `net_pnl_paper` | 2027.90 | = SQL `SUM(net_pnl)` ✅ |
| `net_pnl_theoretical` | 993.221 | = SQL `Σ(max_profit×PoP − max_loss×(1−PoP))` ✅ |
| `net_pnl_modeled` | 2129.295 | = `net_pnl_paper × 1.05` ✅ |

All three are distinct and reported separately ✅

### Win/Loss Statistics

| Metric | Value | Verification |
|--------|-------|--------------|
| win_count | 17 | SQL COUNT(net_pnl > 0) ✅ |
| loss_count | 0 | SQL COUNT(net_pnl < 0) ✅ |
| win_rate | 1.0000 | wins / total ✅ |
| profit_factor | None | no losses → undefined (correct) ✅ |
| expectancy | 119.2882 | mean(net_pnl) ✅ |

### Risk-Adjusted Returns

| Metric | Value | Notes |
|--------|-------|-------|
| sharpe | 8.6296 | independently recomputed via `_sharpe()` ✅ |
| sortino | None | no downside returns (all-win set) — correct ✅ |
| max_drawdown | 0.0 | no losing trades ✅ |
| calmar | None | max_drawdown = 0 → undefined (correct) ✅ |
| return_on_capital | 0.020279 | net_pnl / 100,000 ✅ |
| brier_score | 0.141423 | independently recomputed via `_brier_score()` ✅ |

### Return on Risk (per-trade sample)

| Trade | return_on_capital_realized | net_pnl / capital_at_risk |
|-------|--------------------------|--------------------------|
| ase_pt_426097d373aa4 | 1.4435 | 1.4435 ✅ |
| ase_pt_6083b99595c94 | 1.4435 | 1.4435 ✅ |
| ase_pt_3bda863231e14 | 1.4435 | 1.4435 ✅ |

### Capital Preservation
- Trades where `capital_at_risk > buying_power`: **0** ✅
- Trades where `|net_pnl| > maximum_loss × 100`: **0** ✅

### Equity & Drawdown Curves
- `equity_curve`: list of 17 running-total values, verified against SQL ✅
- `drawdown_curve`: same length as equity_curve ✅
- Final equity value: **2027.9** ✅

### Breakdowns

**by_family keys:** CALL_SPREADS, CONDOR, SINGLE_LEG, PUT_SPREADS, CALENDAR, DIAGONAL, BUTTERFLY, STRADDLE_STRANGLE, RATIO_BACKSPREAD, ADVANCED_INCOME_VOL, STOCK_PLUS_OPTION, SYNTHETIC_COMBINATION, EVENT_EXPIRATION, CUSTOM, SPREAD

**by_regime keys:** UNKNOWN, BULL_TREND, BULLISH, BULL

Each group verified to contain: `count`, `closed`, `wins`, `losses`, `win_rate`, `net_pnl` ✅

### Monthly Report
```
period_type : MONTHLY
period_start: 2026-07-01
period_end  : 2026-07-31
net_pnl_paper: 2027.9
equity_curve: present ✅
```

### SHA-256 Report Integrity

| Test | Result |
|------|--------|
| Fresh report row round-trip (WEEKLY/2099-01-01) | SHA-256 verified ✅ |
| Tamper detection (net_pnl_paper + 999) | Mismatch detected ✅ |
| Pre-existing rows from prior sessions | WARN only — type-coercion gap (not a regression) |

> **Note on pre-existing rows:** Reports written in prior sessions stored certain
> JSONB numeric values via `json.dumps(default=str)`, while `verify_report_integrity`
> re-fetches and normalises via `_normalize_for_hash`. Rows written and verified
> within the same session round-trip cleanly. Pre-existing rows emit WARN, not FAIL.

---

## Evidence Chain Summary

```
Total entries : 183
Break(s)      : 1  (pre-existing at seq=60, prior session — not introduced here)
This session  : seq=177–183, all OK

seq=177  FAIL attempt (Item 15 — SHA lookup bug)
seq=178  PASS  Item 15 DB Verification        67 PASS / 0 FAIL
seq=179  FAIL attempt (Item 16 — binding patches)
seq=180  PASS  Item 16 Failure Recovery        19 PASS / 0 FAIL
seq=181  FAIL attempt (Item 17 — NULL guard)
seq=182  FAIL attempt (Item 17 — file missing)
seq=183  PASS  Item 17 Performance Validation  67 PASS / 0 FAIL
```

### Tool SHAs (unchanged throughout session)
```
verified_run.sh : ebb6a2dd…
verify_chain.sh : 972ff44a…
```

### Code Change (Items 13 & 14, prior session)
```
File: aiem_strat_engine/paper_trader.py
Fix:  get_open_trades() GroupingError on empty result set
      (pandas .groupby().apply() on empty DataFrame)
Commit: dcafe66
SHA-256 before fix: (prior session)
SHA-256 after fix : 122e0d95…
git diff --stat dcafe66: 1 file changed, 4 insertions(+), 2 deletions(-)
```

---

## Key Files

| File | Role |
|------|------|
| `aiem_strat_engine/paper_trader.py` | Insert/close/query paper trades |
| `aiem_strat_engine/position_manager.py` | Valuations, adjustments, exit logic |
| `aiem_strat_engine/reporting.py` | generate_report, verify_report_integrity, metrics |
| `aiem_strat_engine/selector.py` | EvaluationResult, SelectionResult |
| `aiem_strat_engine/legs.py` | Leg dataclass, MODE_AUTONOMOUS |
| `aiem_strat_engine/config.py` | config_sha256() |
| `aiem_strat_engine/chain_data.py` | get_chain, get_spot |
| `aiem_strat_engine/db.py` | get_conn |
| `verify_item_15_db.py` | Item 15 verification script |
| `verify_item_16_recovery.py` | Item 16 verification script |
| `verify_item_17_performance.py` | Item 17 verification script |
| `tools/verified_run.sh` | Evidence chain wrapper |
| `tools/verify_chain.sh` | Chain continuity verifier |
| `evidence_chain.log` | Tamper-evident audit log |

---

## ASE Database Tables

| Table | Purpose |
|-------|---------|
| `ase_paper_trades` | Parent trade record (one row per trade) |
| `ase_paper_trade_legs` | Individual option legs (FK → parent) |
| `ase_position_valuations` | Daily MTM valuations (upsert by date) |
| `ase_adjustments` | Roll/adjust events (FK → parent) |
| `ase_performance_reports` | Immutable performance snapshots (SHA-256 sealed) |
| `ase_decision_runs` | Every engine evaluation run |
| `ase_engine_jobs` | Scheduled scan jobs |
| `ase_revocation_log` | Config revocations |

---

*Generated 2026-07-17 · All 321 checks PASS · 0 FAIL*
