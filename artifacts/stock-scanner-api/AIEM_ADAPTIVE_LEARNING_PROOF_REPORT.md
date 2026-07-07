# AIEM Adaptive Learning Proof Report
Generated: 2026-07-07 03:15 UTC  
Queries: live DB, no mock data, no assumptions

---

## Part 1 — Completed Trade Trace

**Trade selected: id=147, LRCX, multi_signal, 2026-07-06**

```sql
SELECT id, trade_date, ticker, signal_source, entry_price, exit_price,
       pnl, pnl_pct, status, exit_date, exit_reason, audit_trace_id
FROM aiem_paper_trades WHERE id=147;
```
```
id:            147
trade_date:    2026-07-06
ticker:        LRCX
signal_source: multi_signal
entry_price:   358.5991
exit_price:    350.2000
pnl:           -23.42
pnl_pct:       -2.3422
status:        CLOSED_AIEM
exit_date:     2026-07-06
exit_reason:   MACD momentum fading; CMF -0.04 distribution; closed near lows (cs=0.18)
audit_trace_id: NULL
```

**Step 2 — Original AIEM intake decision:** MISSING  
`audit_trace_id = NULL` on every real paper trade. The intake decision was never logged to the DB. No table contains LRCX's original pick rationale. Steps 2, 4, and 5 from the request cannot be answered because this field was never populated.

**Step 3 — Original signal source:** `multi_signal`  
**Step 6 — Trade outcome:** entry $358.5991 → exit $350.20, pnl -$23.42 (-2.34%), closed same day (hold_days=1)

---

## Part 2 — Outcome Grading

**Step 7 — Outcome record (RL experience buffer):**

```sql
SELECT id, trade_id, ticker, signal_source, pnl_pct, reward,
       mistakes, action, market_context, created_at
FROM rl_experience_buffer WHERE trade_id='147';
```
```
id:            11
trade_id:      147
ticker:        LRCX
signal_source: multi_signal
pnl_pct:       -2.3422
roi:           -0.0234
reward:        -4.0353
action:        exit_full
created_at:    2026-07-06 20:01:38 UTC
```

There is no separate `outcome_recorded` table. The RL buffer IS the outcome record.

**Step 8 — Failure reason assigned:**
```
mistakes: ['UNDERPERFORMED_MARKET']
market_context.spy_return_benchmark: +0.044 (SPY +4.4% that day)
market_context.market_beating: False
```
AIEM classified the trade as a market-underperformer. Reward = -4.04 (scaled negative).

**Separate outcome tables exist but are empty for this signal:**
```sql
SELECT COUNT(*) FROM aiem_discovery_outcomes;  -- 134 rows (for aiem_signal_discoveries, not paper trades)
SELECT COUNT(*) FROM aiem_learning_proposals;  -- 1 row, rejected: 'insufficient graded data: 0 rows (need >=30)'
```

---

## Part 3 — Learning Update

There is no single `learning_update_applied` table. Updates flow into three separate systems.  
All three are documented below.

### System A: signal_trust_weights (EMA per-exit, main pick gate)

**How it updates (main.py line 40046):**
```python
_tw_wr = 0.95 * prior_rolling_wr + 0.05 * (1.0 if win else 0.0)
_tw_wt = max(0.2, min(2.0, _tw_wr * 2.0))
```

**Current state:**
```sql
SELECT signal_name, n_outcomes_observed, rolling_win_rate, trust_weight, last_updated_at
FROM signal_trust_weights WHERE context_bucket='PAPER_TRADING';
```
```
multi_signal   | n=31 | rolling_wr=0.0000 | trust=0.2000 | 2026-07-07 02:22:04 UTC
gap_volume     | n=37 | rolling_wr=0.3936 | trust=0.7872 | 2026-07-07 02:22:04 UTC
unusual_calls  | n=9  | rolling_wr=0.0000 | trust=0.2000 | 2026-07-07 02:22:04 UTC
aiem_ai        | n=3  | rolling_wr=0.9025 | trust=1.8050 | 2026-07-07 02:22:04 UTC
conviction_stack| n=1 | rolling_wr=1.0000 | trust=2.0000 | 2026-07-07 02:22:04 UTC
```

**Before/After for LRCX specifically:**
```
multi_signal trades closed before LRCX (id=147): 26 trades, all losses
Prior rolling_wr before LRCX: ~0.0 (EMA of 26 losses converges to zero)
trust_weight before LRCX:     0.200 (floor already reached)

After LRCX loss:
  new_wr = 0.95 * 0.0 + 0.05 * 0.0 = 0.0
  new_trust = max(0.2, min(2.0, 0.0 * 2.0)) = 0.200 (floor unchanged)

BEFORE: trust=0.200   AFTER: trust=0.200   (no marginal change — already floored)
```

**Audit trail gap:** `signal_trust_history` has **0 rows**. The EMA overwrites in-place. There is no per-update history. The before/after for LRCX's specific update was computed mathematically from the sequence of 26 prior losses, not read from a log.

---

### System B: rl_strategy_weights (EWC, sequential log)

This table appends each update and is the only system with a real before/after trail.

**Trace for LRCX loss:**

```sql
SELECT id, weights, performance_snapshot, is_live, n_updates, created_at
FROM rl_strategy_weights
WHERE id IN (21, 22)
ORDER BY id;
```
```
Row 21 — snapshot just BEFORE LRCX EWC update:
  weights: {layer9: 2.1481, aiem_picks: 1.0595, unusual_calls: 0.8694, ...}
  performance_snapshot: {last_signal: 'multi_signal', last_pnl_pct: -2.3422}
  created_at: 2026-07-06 20:01:38.716 UTC

Row 22 — snapshot AFTER LRCX EWC consolidation:
  weights: {layer9: 2.069, aiem_picks: 1.0675, unusual_calls: 0.8926, ...}
  performance_snapshot: {source: 'multi_signal', ewc_consolidation: True}
  created_at: 2026-07-06 20:01:38.721 UTC
```

**Concrete before/after (LRCX loss → EWC update):**
```
layer9 strategy weight:   2.1481  →  2.069   (−0.079, decay after multi_signal loss)
aiem_picks weight:        1.0595  →  1.0675  (+0.008)
unusual_calls weight:     0.8694  →  0.8926  (+0.023)
```

Note: multi_signal is not a key in rl_strategy_weights. The EWC updates all tracked strategies collectively when any source has an outcome, pulling weight toward aiem_picks and away from the loss source's cluster.

**Current live policy (is_live=True):**
```
Row 28: {layer9: 1.9292, aiem_picks: 1.0816, unusual_calls: 0.9335, ...}
n_updates: 28
```

---

### System C: rl_confidence_history (calibration)

```sql
SELECT signal_source, predicted_prob, actual_outcome, recorded_at
FROM rl_confidence_history WHERE signal_source='multi_signal';
```
```
multi_signal | 0.55 | False | 2026-07-06 20:01:38 UTC  (KLAC)
multi_signal | 0.55 | False | 2026-07-06 20:01:38 UTC  (LRCX) ← trade 147
multi_signal | 0.55 | False | 2026-07-06 20:01:38 UTC  (MRVL)
multi_signal | 0.55 | False | 2026-07-06 20:01:38 UTC  (WOLF)
multi_signal | 0.55 | False | 2026-07-06 20:01:38 UTC  (SMCI)
```
All 5 multi_signal Jul 6 trades recorded as misses against a 0.55 predicted probability. This feeds the Brier score calibration (not yet connected to pick-time confidence display).

---

## Part 4 — Before/After Summary Table

| Parameter | System | Before LRCX | After LRCX | Stored as history? |
|---|---|---|---|---|
| multi_signal trust_weight | signal_trust_weights | 0.200 | 0.200 (floored) | No — overwrite only |
| multi_signal rolling_wr | signal_trust_weights | ~0.0 | ~0.0 | No — overwrite only |
| layer9 strategy weight | rl_strategy_weights | 2.1481 | 2.069 | Yes — row 21→22 |
| aiem_picks strategy weight | rl_strategy_weights | 1.0595 | 1.0675 | Yes — row 21→22 |
| unusual_calls strategy weight | rl_strategy_weights | 0.8694 | 0.8926 | Yes — row 21→22 |
| drift_mult for multi_signal | drift_check_log | 1.0 (no gate) | 0.35 (ALERT) | Yes — drift_check_log |
| RL reward buffer | rl_experience_buffer | (no row) | -4.04 | Yes — row id=11 |

---

## Part 5 — Later Decision Using Updated Value

**The only paper trade opened after the Jul 6 20:01 UTC learning updates:**

```sql
SELECT id, trade_date, ticker, signal_source, created_at
FROM aiem_paper_trades
WHERE created_at > '2026-07-06 20:01:39+00'
ORDER BY created_at;
```
```
id=157 | 2026-07-06 | SPY | aiem_ai | 2026-07-07 02:23:50 UTC
```

**How learning affected this pick:**

SPY was selected via `aiem_ai`. At pick time, the trust gate loaded:
```
aiem_ai  → trust_weight=1.805 (n=3, rolling_wr=0.9025)   → score × 1.805
multi_signal → trust_weight=0.200 (n=31, rolling_wr=0.0)  → score × 0.200
             + drift gate ALERT_UNDERPERFORMING             → score × 0.35
             combined: score × 0.070
```

The learning outcome is visible in the differential: `aiem_ai` got a 1.805× boost from its 3 winning outcomes; `multi_signal` got a 0.07× penalty from its 31 losing outcomes. The SPY trade via `aiem_ai` ranked above any hypothetical multi_signal candidate by a factor of ~26× at equal raw score.

**Direct trace from LRCX outcome to SPY decision:**
- LRCX (multi_signal) closed → pnl_pct=-2.34% → RL buffer written → MTM ran EMA update → multi_signal trust_weight confirmed at floor → drift check confirmed ALERT_UNDERPERFORMING → drift_mult=0.35 loaded into pick candidates at SPY pick time → aiem_ai selected instead.

The exact step not traceable from DB: whether a multi_signal LRCX re-entry was in the raw candidate list for the 02:23 pick run and was suppressed. That intermediate ranking list is computed in-memory and not stored.

---

## Part 6 — Missing Modules / Gaps

| Gap | Impact |
|---|---|
| `audit_trace_id = NULL` on all real trades | Steps 2, 4, 5 (intake decision, AIEM decision log) are permanently missing for all 120 trades |
| `signal_trust_history` has 0 rows | Cannot audit which specific trade caused which EMA update. Only the current overwritten value exists |
| `dc_template_feedback` has 0 rows | Thompson sampler (template variation selector) is not functioning |
| `aiem_learning_proposals` has 1 row, rejected | Model weight promotion has never executed (needs ≥30 graded rows) |
| PPO policy not updated from RL buffer | 14 experience rows exist; no PPO gradient step has run yet |
| `rl_strategy_weights` does not include `multi_signal` | The EWC system and the paper trade trust system do not share signal names — two separate learning loops, not connected |
| Intermediate candidate ranking not stored | Cannot prove from DB that a specific multi_signal ticker was ranked lower in a specific pick run |

---

## Part 7 — Verdict

**PARTIAL**

**What is proven:**
1. AIEM records outcomes into the RL buffer per-trade (14 real trade records, each with reward, mistakes, state vector)
2. AIEM updates signal_trust_weights per-exit (EMA formula verified from source code; current values reflect 31 real multi_signal losses)
3. The drift gate fired for multi_signal (ALERT_UNDERPERFORMING, telegram_sent=True) and is wired into pick candidate scoring via `_eff = score * drift_mult` (code lines 38791, 38806)
4. The rl_strategy_weights has real sequential before/after for LRCX's loss (rows 21→22, layer9 2.1481→2.069)
5. A later trade (SPY, id=157, 02:23 UTC Jul 7) was selected via aiem_ai, which carried a 1.805× trust boost — the inverse of multi_signal's 0.200 floor — both values driven by accumulated outcome history

**What is not proven from DB alone:**
1. No intake decision is logged (audit_trace_id=NULL everywhere) — the pick-time decision for LRCX is unrecoverable
2. No per-update audit trail for signal_trust_weights — LRCX's specific EMA contribution cannot be isolated
3. Cannot prove from DB that a specific multi_signal LRCX re-entry was in a candidate list and ranked below the threshold — that ranking exists only in-memory
4. The PPO policy has not yet been trained from the 14 RL buffer rows — that learning loop is populated but not yet closed

**The boundary:**  
Trust weights, drift gate, and rl_strategy_weights update on real outcomes and feed real pick decisions. The mechanism is wired. The audit trail to prove *which specific trade caused which specific later block* is absent because `audit_trace_id` was never populated and `signal_trust_history` records 0 rows.

The system is learning and changing behavior. The evidence trail to prove it trade-by-trade is incomplete.
