"""
pit_correction.py - one-time, disclosed re-scoring of the shadow-log rows
contaminated by the pre-2026-07-02 leakage bug (see config.py "LEAKAGE GAP"
and reports.py's module docstring for the bug itself; model_registry.py +
predict.load_models_as_of() for the ongoing structural fix applied to all
NEW predictions going forward).

WHAT THIS DOES: for every aiem_probability_engine_predictions row with
pit_status='leaked' (213 rows as of the 2026-07-02 audit), retrains a
FRESH, EMBARGOED model per horizon using ONLY data a real point-in-time
system could have had on that row's signal_date, then rescores that
specific (ticker, signal_date) with it. Writes the result to a SEPARATE
table (aiem_probability_engine_pit_corrections), never as new rows in the
original table - that keeps get_logged_keys()'s UNIQUE(signal_date, ticker,
model_version) constraint and its idempotency untouched, and keeps the
"what the leaked run originally said" record intact for side-by-side
comparison (see pit_metrics.py).

THE EMBARGO (the part a naive "train on trade_date < signal_date" rule
gets wrong): a horizon-h model trained on a row dated X has, via that row's
own label, absorbed the closing price X + h TRADING days later. To score
signal_date D without leaking, training must stop h trading days BEFORE D,
not merely before D - i.e. allowed training dates = the sorted list of
every unique trade_date in the full dataset, sliced at index (i - h) where
i is D's own index. This is per-horizon: the 1d model's embargo window
ends 1 date later than the 4d model's, for the exact same target date D.

FLOOR: below MIN_PRIOR_DATES unique dates or MIN_PRIOR_ROWS rows of
embargoed history for a given (horizon, date), there is not enough data to
fit anything meaningful - that (horizon, date) combination is marked
uncorrectable for that horizon, never silently skipped or guessed. This
dataset spans only ~3.5 weeks (9-15 unique trade_dates total as of
2026-07-02) - EXPECT MOST of the 213 rows to end up uncorrectable, since
the earliest weeks have almost no prior history to embargo-train on. An
honest "this can't be PIT-safely rescored yet" beats a fabricated number.

Models trained here are used in-memory ONLY, for this one-shot correction -
they are never written to model_registry.py's permanent registry (that
registry is for the live, ongoing system) and never use calibration
(is_selected/calibration gating is irrelevant to a raw honest-probability
re-score).

Run directly:
    python pit_correction.py
"""
import os
import sys

import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_URL, HORIZONS
from data_snapshot import build_dataset
from features import add_standardized_features, STANDARDIZED_FEATURE_COLUMNS
from model_training import train_model

TABLE = "aiem_probability_engine_pit_corrections"
SOURCE_TABLE = "aiem_probability_engine_predictions"

# Deliberately the same floor philosophy as train.py's "n_samples < 5"
# skip, but stricter - a CORRECTION claiming to replace a real number needs
# more than the bare minimum to fit at all.
MIN_PRIOR_DATES = 5
MIN_PRIOR_ROWS = 50

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    original_prediction_id BIGINT NOT NULL UNIQUE REFERENCES {SOURCE_TABLE}(id),
    signal_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    correction_status TEXT NOT NULL,
    corrected_prob_up_1d DOUBLE PRECISION,
    corrected_prob_up_2d DOUBLE PRECISION,
    corrected_prob_up_3d DOUBLE PRECISION,
    corrected_prob_up_4d DOUBLE PRECISION,
    training_cutoff_1d DATE,
    training_cutoff_2d DATE,
    training_cutoff_3d DATE,
    training_cutoff_4d DATE,
    n_training_samples_1d INTEGER,
    n_training_samples_2d INTEGER,
    n_training_samples_3d INTEGER,
    n_training_samples_4d INTEGER,
    n_training_dates_1d INTEGER,
    n_training_dates_2d INTEGER,
    n_training_dates_3d INTEGER,
    n_training_dates_4d INTEGER
)
"""


def ensure_table() -> None:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
        conn.commit()


def _load_leaked_rows() -> list:
    """Only rows not already corrected - safe to re-run incrementally."""
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT p.id, p.signal_date, p.ticker
                FROM {SOURCE_TABLE} p
                LEFT JOIN {TABLE} c ON c.original_prediction_id = p.id
                WHERE p.pit_status = 'leaked' AND c.id IS NULL
                ORDER BY p.signal_date ASC, p.ticker ASC
            """)
            return cur.fetchall()


def _train_embargoed(std_df: pd.DataFrame, horizon: int, allowed_dates: set):
    """
    Trains one horizon's model using ONLY rows whose trade_date is in
    allowed_dates (already embargo-sliced by the caller - see module
    docstring). Returns (TrainedModel_or_None, cutoff_date_or_None,
    n_samples, n_dates). None model means the floor wasn't met.
    """
    label_col = f"label_{horizon}d"
    pool = std_df[std_df["trade_date"].isin(allowed_dates)]
    sub = pool.dropna(subset=[label_col]).copy()
    n_dates = sub["trade_date"].nunique()
    n_samples = len(sub)

    if n_dates < MIN_PRIOR_DATES or n_samples < MIN_PRIOR_ROWS:
        return None, None, n_samples, n_dates

    sub = sub.rename(columns={label_col: "outcome"})
    usable_cols = [c for c in STANDARDIZED_FEATURE_COLUMNS if sub[c].notna().any()]
    trained = train_model(sub, feature_columns=usable_cols)
    cutoff_date = pd.Timestamp(sub["trade_date"].max()).date()
    return trained, cutoff_date, n_samples, n_dates


def run_correction() -> dict:
    ensure_table()
    leaked = _load_leaked_rows()
    if not leaked:
        print("[pit_correction] nothing to correct - no un-corrected 'leaked' rows found")
        return {"corrected": 0, "partially_corrected": 0, "uncorrectable": 0, "total": 0}

    print(f"[pit_correction] {len(leaked)} leaked rows to attempt correction on")

    raw = build_dataset()
    if raw.empty:
        raise RuntimeError("no dataset available - cannot correct anything")
    std_df = add_standardized_features(raw)
    # Normalize to plain date objects for set membership / DB-value
    # comparisons throughout this file (features.py leaves this column as
    # pandas Timestamp, which reports.py handles differently - this file is
    # self-contained so it picks one representation and uses it throughout).
    std_df["trade_date"] = std_df["trade_date"].apply(lambda d: pd.Timestamp(d).date())

    all_dates = sorted(std_df["trade_date"].unique())
    date_index = {d: i for i, d in enumerate(all_dates)}

    # Cache trained models per (horizon, target_date) - many leaked tickers
    # share the same signal_date, and the embargo depends only on
    # (horizon, date), never on which ticker is being scored.
    _model_cache = {}

    def _get_for(horizon: int, target_date):
        key = (horizon, target_date)
        if key in _model_cache:
            return _model_cache[key]
        i = date_index.get(target_date)
        if i is None:
            result = (None, None, 0, 0)
        else:
            allowed = set(all_dates[: max(0, i - horizon)])
            result = _train_embargoed(std_df, horizon, allowed)
        _model_cache[key] = result
        return result

    counts = {"corrected": 0, "partially_corrected": 0, "uncorrectable": 0}
    rows_to_insert = []

    for row in leaked:
        pred_id, D, ticker = row["id"], row["signal_date"], row["ticker"]
        feat_row = std_df[(std_df["ticker"] == ticker) & (std_df["trade_date"] == D)]
        if feat_row.empty:
            print(f"[pit_correction] WARNING: could not rebuild a feature row for "
                  f"{ticker} {D} (pred_id={pred_id}) - marking uncorrectable")
            feat_row = None

        out = {"original_prediction_id": pred_id, "signal_date": D, "ticker": ticker}
        n_corrected_horizons = 0
        for h in HORIZONS:
            trained, cutoff_date, n_samples, n_dates = _get_for(h, D)
            out[f"n_training_samples_{h}d"] = n_samples
            out[f"n_training_dates_{h}d"] = n_dates
            if trained is None or feat_row is None:
                out[f"corrected_prob_up_{h}d"] = None
                out[f"training_cutoff_{h}d"] = None
                continue
            X = feat_row[trained.feature_columns]
            prob = float(trained.model.predict_proba(X)[:, 1][0])
            out[f"corrected_prob_up_{h}d"] = prob
            out[f"training_cutoff_{h}d"] = cutoff_date
            n_corrected_horizons += 1

        if n_corrected_horizons == 0:
            out["correction_status"] = "uncorrectable"
            counts["uncorrectable"] += 1
        elif n_corrected_horizons == len(HORIZONS):
            out["correction_status"] = "corrected"
            counts["corrected"] += 1
        else:
            out["correction_status"] = "partially_corrected"
            counts["partially_corrected"] += 1

        rows_to_insert.append(out)

    insert_sql = f"""
        INSERT INTO {TABLE} (
            original_prediction_id, signal_date, ticker, correction_status,
            corrected_prob_up_1d, corrected_prob_up_2d, corrected_prob_up_3d, corrected_prob_up_4d,
            training_cutoff_1d, training_cutoff_2d, training_cutoff_3d, training_cutoff_4d,
            n_training_samples_1d, n_training_samples_2d, n_training_samples_3d, n_training_samples_4d,
            n_training_dates_1d, n_training_dates_2d, n_training_dates_3d, n_training_dates_4d
        ) VALUES (
            %(original_prediction_id)s, %(signal_date)s, %(ticker)s, %(correction_status)s,
            %(corrected_prob_up_1d)s, %(corrected_prob_up_2d)s, %(corrected_prob_up_3d)s, %(corrected_prob_up_4d)s,
            %(training_cutoff_1d)s, %(training_cutoff_2d)s, %(training_cutoff_3d)s, %(training_cutoff_4d)s,
            %(n_training_samples_1d)s, %(n_training_samples_2d)s, %(n_training_samples_3d)s, %(n_training_samples_4d)s,
            %(n_training_dates_1d)s, %(n_training_dates_2d)s, %(n_training_dates_3d)s, %(n_training_dates_4d)s
        )
        ON CONFLICT (original_prediction_id) DO NOTHING
    """
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, insert_sql, rows_to_insert)
        conn.commit()

    total = len(leaked)
    print(f"\n[pit_correction] DONE: {total} leaked rows processed")
    print(f"  corrected (all {len(HORIZONS)} horizons):     {counts['corrected']}")
    print(f"  partially_corrected (some horizons):   {counts['partially_corrected']}")
    print(f"  uncorrectable (0 horizons, <{MIN_PRIOR_DATES} prior dates or "
          f"<{MIN_PRIOR_ROWS} prior rows): {counts['uncorrectable']}")
    counts["total"] = total
    return counts


if __name__ == "__main__":
    run_correction()
