---
name: AIEM Full Learning Loop (Real Model + Signal Bridge + Predictable Events)
description: All five AIEM upgrade blocks integrated; new DB tables, scheduler jobs, admin routes, tool map entries
---

## What is wired
Five file blocks inserted into main.py before `_AIEM_AGENT_TOOLS = [`:

1. **Real Model** (`_ensure_model_registry`): logistic regression on graded outcomes.
   - Table: `model_registry` (id, feature_names, coefficients, intercept, n_train, n_test, train_auc, test_auc, is_deployed).
   - `_train_and_register_model()` — won't deploy if new AUC < current deployed AUC (regression guard).
   - `_score_candidate(features_dict)` — pure Python at serve time, no sklearn.
   - `_AIEM_FEATURE_COLUMNS = ["gap_pct","rvol","close_strength","range_pct","confidence_score","rank","pre_squeeze_warning_active","accumulation_breakout_active"]`.

2. **Accumulation-to-Distribution** (`_ensure_watched_positions_table`): lifecycle tracking.
   - Table: `watched_positions` (id, ticker, entry/exit dates/prices/reasons, status).
   - `detect_accumulation_breakout()` — band compression + breakout on vol from polygon_market_daily.
   - `detect_distribution_signature(ticker, entry_date, entry_price)` — exhaustion_score 0-4.
   - `run_daily_exit_check()` — scans all open watched positions daily.

3. **Pre-Squeeze Early Warning** (`_mkt_tool_pre_squeeze_warning`): fires BEFORE squeeze is confirmed.
   - Band width declining for slope_window days + flat/declining volume.
   - `_validate_pre_squeeze_lead_time()` — backtests whether it gives real lead time.

4. **Signal-to-Model Bridge** (`_ensure_signal_fire_log`): wires detectors into the learning loop.
   - Table: `signal_fire_log` (signal_name, ticker, fire_date, fire_price, metadata, graded, fwd_ret_3/5/10d).
   - `log_signal_fired(signal_name, ticker, fire_date, fire_price)` — call every time a detector fires.
   - `grade_pending_signals()` — grades fires ≥12 days old against real forward returns.
   - `_run_daily_signal_jobs()` — runs pre-squeeze + accum breakout, logs all fires, grades, exit-check.

5. **Additional Predictable Events** (A–D live, E–I stubs):
   - A: `_mkt_extreme_move_reversion(threshold, horizon)` — extreme-day reversion, up vs down.
   - B: `_mkt_gap_fill_probability()` — fill rate by gap-size bucket.
   - C: `_detect_capitulation_signature()` / `_validate_capitulation_signature()`.
   - D: `_mkt_52week_high_momentum()` — needs ≥126 days of history per ticker to trust.
   - E: `_refresh_dividend_calendar(ticker)` — Polygon reference API; table: `dividend_calendar`.
   - F-I: Skeleton tables only: `insider_transactions`, `buyback_announcements`, `index_membership_changes`, `ipo_calendar`.

## Scheduler jobs added
- Sunday 7 PM ET: `aiem_model_retrain_weekly` — calls `_train_and_register_model()` in daemon thread.
- Daily 4:50 PM Mon-Fri: `signal_bridge_daily` — calls `_run_daily_signal_jobs()` in daemon thread.

## Tool map + schema
6 new entries in `_tool_map` and `_AIEM_AGENT_TOOLS`:
`mkt_pre_squeeze_warning`, `mkt_extreme_move_reversion`, `mkt_gap_fill_probability`,
`mkt_capitulation_detector`, `mkt_52week_momentum`
(accumulation/distribution functions accessible via admin endpoints, not directly as AIEM tools)

## Admin endpoints (17 new routes)
- `/admin/model/train` POST, `/admin/model/history` GET, `/admin/model/current` GET
- `/admin/accumulation/breakouts` GET, `/admin/accumulation/positions` GET, `/admin/accumulation/exit-check` POST
- `/admin/signal-bridge/performance?signal=` GET, `/admin/signal-bridge/grade` POST
- `/admin/pre-squeeze/validate` GET
- `/admin/predictable-events/extreme-move` GET, `/admin/predictable-events/gap-fill` GET
- `/admin/predictable-events/capitulation` GET + `/validate` GET
- `/admin/predictable-events/52week-momentum` GET
- `/admin/predictable-events/lockup-expirations` GET, `/admin/predictable-events/dividends/<ticker>` GET

**Why:** Without the Signal-to-Model Bridge, new detectors generate output that never feeds back into the learning loop. log_signal_fired + grade_pending_signals + the SQL LEFT JOIN in _extract_training_data is what makes model coefficients actually reflect whether pre-squeeze and accumulation-breakout signals are predictive.

## Model will not train until ≥200 graded outcomes exist
The `_train_and_register_model` function returns `{"status":"skip"}` until that threshold is met — this is intentional. The model needs real graded data before it can displace LLM-based scoring.
