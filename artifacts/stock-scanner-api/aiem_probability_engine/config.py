"""
config.py - AIEM Probability Engine configuration and data-reality constants.

Every number in the "DATA REALITY" section below was verified directly
against the live DB on 2026-07-01 (see scripts/check_data_reality.py in
this package). Nothing here is an estimate.
"""
import os

DB_URL = os.environ.get("DATABASE_URL", "")

# Trading-day horizons the spec requires probabilities for.
HORIZONS = [1, 2, 3, 4]

# Trailing window (trading days) used for z-score/percentile standardization
# of each layer.
STANDARDIZATION_WINDOW_DAYS = 60

# Mirrors model_training.MIN_SAMPLES. Below this, a horizon's model is
# trained (so the pipeline runs end-to-end) but flagged is_trustworthy=False.
MIN_SAMPLES_FLOOR = 200

# Labeling threshold only (does not block training) — once a horizon
# clears this many samples, reports stop calling it "shaky."
CONFIDENT_SAMPLES_TARGET = 500

# model_training.train_model()'s internal CV (TimeSeriesSplit) windows by
# ROW COUNT, not by unique trade_date. Below this many unique dates, CV
# folds can straddle a single date's picks, so its cv_auc is "row-count
# sufficient, date-count immature" rather than a validated estimate. Rely
# on walk_forward.py's date-safe validation for anything trust-sensitive
# until real trading history accumulates past this floor.
MIN_UNIQUE_DATES_FOR_CV_TRUST = 20

# --- DATA REALITY (verified 2026-07-01) -----------------------------------
#
# TIER 1 layers — broad history, ~300-450 labeled picks since 2026-06-08:
#   vol_oi, otm_pct, days_out, conviction, rvol, gap_pct, price technicals
#   (ma20_relative, volume_trend_3d/5d). Computable from ai_short_calls_log
#   (445 rows) joined with polygon_market_daily (2024-07-08 -> present,
#   13,969 tickers, 3.3M rows — this is NOT the bottleneck).
#
# TIER 2 layers — options-positioning "conviction stack" layers. Real
# per-column population in ai_short_calls_log (445 rows total), re-verified
# 2026-07-02 — coverage is UNEVEN across columns, not a flat "13 of 445"
# as an earlier version of this comment said:
#   dark_pool_score:    438/445 (98%) — effectively full coverage
#   squeeze_score:      371/445 (83%) — strong coverage
#   gamma_score:         13/445  (3%) — column added recently, thin
#   sector_heat_score:    2/445 (<1%) — column added recently, near-empty
#   NaN-imputed per-column where absent (existing SimpleImputer pattern),
#   so dark_pool/squeeze already carry real signal today even though
#   gamma/sector_heat are still mostly imputed placeholders.
#
#   SEPARATELY: charm, oi_accum, short_int, float_pressure, sector_sympathy,
#   far_otm_sweep live in conviction_stack_watchlist.layers (jsonb) as
#   coarse bucketed scores (e.g. short_int in {1.0, 1.5, 2.0}), NOT a raw
#   short-interest-% time series. That table has only 3 snap_dates total
#   (2026-06-18, 2026-06-28, 2026-07-01) — 3 sparse point-in-time readings
#   spanning ~2 weeks, not a daily history. This is NOT enough to build a
#   trailing/rolling per-ticker feature, so it remains a SCHEMA REFERENCE
#   for the 9-layer shape only, not a usable training source yet. There is
#   no other short-interest data source anywhere in this DB (verified via
#   information_schema — no short_interest/short_int columns/tables exist
#   outside this jsonb field).
#
# LABELS — NOT history-limited. Exact forward 1/2/3/4-trading-day returns
# are computed directly from polygon_market_daily.close_price (~2 years of
# real history for liquid names). Labels are not the constraint here —
# Tier 2 feature coverage is.
#
# CONSEQUENCE: every model trained today is TIER-1-DOMINANT. Tier 2 columns
# are included as features (NaN-imputed where absent, per the existing
# model_training.py SimpleImputer pattern) so the pipeline automatically
# absorbs more signal as Tier 2 history grows — no rebuild needed. Re-run
# scripts/check_data_reality.py periodically; when Tier 2 row counts climb,
# retrain and check feature_importance reports for Tier 2 contribution.
#
# KNOWN LEAKAGE GAP (found during 2026-07-02 audit, still open) — the
# shadow-mode log (reports.py: aiem_probability_engine_predictions) always
# scores backlog rows with whatever model_horizon_{h}d.pkl is CURRENTLY on
# disk, regardless of how old that row's signal_date is. Because train.py
# always trains on the full historical dataset available at run time, a
# backfilled prediction for an old signal_date is scored by a model that
# was trained on rows dated AFTER that signal_date — i.e. the model has
# seen the future relative to that logged "prediction." This is fine for
# model-health monitoring but it means historical rows in that table
# (verified concretely: signal_date=2026-06-08 row logged with
# created_at=2026-07-01, model_version=79adb318dcbe, n_training_samples=349
# matching the FULL current dataset) must NOT be read as "what the model
# would have called in real time on that date." Only walk_forward.py's
# backtest (which enforces a real train/val date split, confirmed
# overlap=False) is point-in-time trustworthy today. Fixing this properly
# would require either (a) snapshotting/versioning a model artifact per
# historical retrain date, or (b) marking shadow-log rows whose
# created_at is far past signal_date as "monitoring-only, not PIT-safe."
# Neither is implemented yet.

TIER1_FEATURE_COLUMNS = [
    "vol_oi",
    "otm_pct",
    "days_out",
    "conviction_score",
    "rvol",
    "gap_pct",
    "day_of_week",
    "volume_trend_3d",
    "volume_trend_5d",
    "ma20_relative",
]

TIER2_FEATURE_COLUMNS = [
    "gamma_score",
    "dark_pool_score",
    "squeeze_score",
    "sector_heat_score",
]

ALL_FEATURE_COLUMNS = TIER1_FEATURE_COLUMNS + TIER2_FEATURE_COLUMNS

_PKG_DIR = os.path.dirname(__file__)
MODEL_DIR = os.path.join(_PKG_DIR, "models")
REPORT_DIR = os.path.join(_PKG_DIR, "reports")
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
