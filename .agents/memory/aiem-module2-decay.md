---
name: AIEM Module 2 — Decay & Failure Analyzer
description: Covers design decisions, evaluation_status 4-value system, classification order, wiring points, and current state per signal as of 2026-07-02.
---

## What Module 2 does

Evaluates every row in `aiem_signal_discoveries` and assigns one of four explicit
`evaluation_status` values. No signal is silently skipped. No verdict is issued
without genuine OOS evidence.

## File and wiring

- `artifacts/stock-scanner-api/aiem_module2_decay.py` — standalone module
- `main.py` import: `import aiem_module2_decay as _m2` (try/except, line ~122)
- `POST /stock-api/admin/run-module2-decay` — triggers evaluation, returns all 9 results
- `GET /stock-api/aiem/module2-status` — returns last stored evaluations from DB
- `aiem_module2_evaluations` table — upserted on every run, UNIQUE(discovery_id)
- APScheduler job `id="module2_decay_check"` — Sunday 2:30 AM ET (added 2026-07-02)

## Module 1 dependency

Module 2 reads from `aiem_discovery_outcomes` (written by Module 1).
Module 1 must run first (Sunday 2:00 AM ET) before Module 2 (2:30 AM ET).
Module 2's outcome-exists shortcut (step 2) only fires on `retestable=True` rows.

**Critical:** Module 1 must use `WHERE status IN ('validated','hypothesis','retired')`
(not just 'validated') or ids 2/3/5/7/8/9 never get an outcome row.
This was fixed 2026-07-02 at main.py line ~24413.

## Classification order (critical — do not change)

1. Structural check → `unevaluable_structural` (before any other check)
2. **Outcome-exists shortcut** → if `retestable=True` outcome exists, skip condition-key
   analysis and go directly to verdict (this is why id=1 reaches `evaluable_now` even though
   its condition keys `vol_lookback/vol_ratio_min/price_range_max_pct` aren't in the single-row
   parser — Module 1's standard V2 adapter ran and produced real OOS data)
3. Genuinely unmapped keys → `evaluable_pending_columns` (no known evaluation path)
4. All keys have known paths (direct/V2 CTE/alias/chain adapter) → `evaluable_pending_time`
5. n >= 30 → `evaluable_now` + decay verdict

**Why step 2 must come before step 3:** Without it, signals with a working adapter that
already produced retestable=True results get wrongly blocked as `evaluable_pending_columns`.

## Two-tier condition-key classification (fixed 2026-07-02)

`evaluable_pending_columns` = no known evaluation path (no adapter, no column, no approx).
`evaluable_pending_time` = path exists (direct col / V2 CTE / indicator alias / chain adapter);
block is purely forward-time accumulation.

Key sets in module:
- `_DIRECT_MAPPABLE_STEMS` — direct polygon_market_daily / polygon_indicators_daily cols
- `_V2_CTE_MAPPABLE_STEMS` — lag/delta/rolling V2 CTE derived columns
- `_INDICATOR_ALIAS_MAP` — lagdelta adapter aliases (cmf20→cmf_20, rsi14→rsi_14, etc.)
- `_CHAIN_ADAPTER_KNOWN_STEMS` — chain adapter stems (gap_up_pct, inside_day_range,
  prior_day_move_pct, prior_day_close_strength, avg_vol, price_range, gap_abs, volume_ratio, etc.)
- `_TRULY_UNMAPPED_KEYS` — currently `frozenset()` — no signal has a genuinely unmapped key

`_classify_condition_keys()` returns 5-tuple:
`(is_structural, is_evaluable_direct, truly_unmapped_keys, alias_keys, chain_adapter_keys)`
Only `truly_unmapped_keys` triggers `evaluable_pending_columns`.

## What Module 2 does NOT do

- Does NOT retire signals (that is Module 4 — human approval gate, not yet built)
- Does NOT promote signals
- Does NOT modify `aiem_signal_discoveries.status`

**id=1 has `decay_verdict=failing` (n=905, wr=46.96%, p=0.0) but still fires in live**
**contexts because Module 4 doesn't exist yet. This is the known gap.**

## Build sequencing rule

Module 4 (human approval gate, kill switch) must be built before Module 3 (hypothesis
promotion). Without Module 4, verdicts from Module 2 have no downstream action.

## Current state of all 9 signals (as of 2026-07-02 end of session)

| id | db_status  | evaluation_status      | decay_verdict   | n    | notes                                 |
|----|------------|------------------------|-----------------|------|---------------------------------------|
| 1  | validated  | evaluable_now          | **failing**     | 905  | wr=46.96% vs 52.35% disc, p=0.0      |
| 2  | hypothesis | evaluable_pending_time | —               | —    | chain adapter ready; waiting fwd data |
| 3  | hypothesis | evaluable_pending_time | —               | —    | chain adapter ready; waiting fwd data |
| 4  | hypothesis | evaluable_pending_time | insufficient_n  | 9    | adapter ran, needs more fwd days      |
| 5  | retired    | evaluable_pending_time | —               | —    | chain adapter ready; waiting fwd data |
| 6  | validated  | evaluable_pending_time | insufficient_n  | 25   | wr=36% vs 55.35% disc, p=0.29        |
| 7  | retired    | evaluable_pending_time | —               | —    | lagdelta adapter ready; disc today    |
| 8  | retired    | evaluable_pending_time | —               | —    | lagdelta adapter ready; disc today    |
| 9  | hypothesis | unevaluable_structural | —               | —    | ~31/yr fires, est 1.6yr to n=200     |

## When ids 2/3/5/7/8 will auto-advance

- ids 7,8: discovered 2026-07-02; retestable=True outcomes appear 2026-07-03 AM
- ids 2,3: horizon=3d from 2026-06-27; needs today's close in polygon_market_daily; 2026-07-03 AM
- id=5: horizon=5d from 2026-06-27; needs close on/after 2026-07-07 (Mon, Jul 4 holiday)
- All auto-advance via Module 1 2:00 AM ET → Module 2 2:30 AM ET; no code changes needed
