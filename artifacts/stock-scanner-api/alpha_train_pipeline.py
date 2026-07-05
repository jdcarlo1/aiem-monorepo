"""
alpha_train_pipeline.py

AIEM alpha-prediction model: trains XGBoost to find stocks that beat SPY.

Target variable:
  alpha_vs_spy = stock_return_pct - spy_return_same_period
  outcome = 1 if alpha_vs_spy >= 2.0 else 0

Data sources (in priority order):
  1. aiem_paper_trades  — real paper picks with pnl_pct outcomes
  2. conviction_stack_watchlist — historical snapshots with w1/w2/w3/w4 outcomes

SPY returns fetched from sector_etf_daily (backfilled) or spy_daily_cache.

Model saved to: aiem_alpha_model.pkl
Retrain log:    aiem_alpha_retrain_log
Walk-forward validation included.

Run via:
  from alpha_train_pipeline import run_alpha_retrain_cycle
  result = run_alpha_retrain_cycle()
"""

import io
import json
import os
import pickle
import psycopg2
import numpy as np
import pandas as pd
from datetime import date, timedelta
from dataclasses import dataclass
from typing import List, Optional

from alpha_feature_engineering import ALPHA_FEATURE_COLUMNS, build_alpha_feature_row
from sector_etf_data import get_spy_return

_DB_URL      = os.environ.get("DATABASE_URL", "")
_MODEL_PATH  = os.path.join(os.path.dirname(__file__), "aiem_alpha_model.pkl")
_MIN_SAMPLES = 50
_ALPHA_THRESHOLD = 2.0

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS aiem_alpha_retrain_log (
    id              SERIAL PRIMARY KEY,
    retrain_date    DATE NOT NULL,
    n_samples       INTEGER,
    n_positive      INTEGER,
    candidate_auc   FLOAT,
    candidate_brier FLOAT,
    prod_auc        FLOAT,
    prod_brier      FLOAT,
    promoted        BOOLEAN,
    reason          TEXT,
    metrics_json    JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE aiem_paper_trades
    ADD COLUMN IF NOT EXISTS alpha_vs_spy   NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS spy_return_pct NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS alpha_graded_at TIMESTAMPTZ;
"""


def _init_tables():
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(_INIT_SQL)
            conn.commit()
    except Exception as e:
        print(f"[alpha_train] init error: {e}")


_init_tables()


def backfill_alpha_labels() -> dict:
    """
    For every closed aiem_paper_trade with pnl_pct, compute alpha_vs_spy
    and write it back.  Safe to run multiple times (idempotent).
    """
    if not _DB_URL:
        return {"error": "no DB"}
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, trade_date, exit_date, pnl_pct
                FROM aiem_paper_trades
                WHERE pnl_pct IS NOT NULL
                  AND alpha_vs_spy IS NULL
                ORDER BY trade_date
            """)
            rows = cur.fetchall()

        updated = 0
        skipped = 0
        for row_id, trade_date, exit_date, pnl_pct in rows:
            if not exit_date:
                exit_date = trade_date + timedelta(days=11)
            spy_ret = get_spy_return(trade_date, exit_date)
            if spy_ret is None:
                skipped += 1
                continue
            alpha = round(float(pnl_pct) - spy_ret, 4)
            with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                cur.execute("""
                    UPDATE aiem_paper_trades
                    SET alpha_vs_spy   = %s,
                        spy_return_pct = %s,
                        alpha_graded_at = NOW()
                    WHERE id = %s
                """, (alpha, round(spy_ret, 4), row_id))
                conn.commit()
            updated += 1

        return {"updated": updated, "skipped_no_spy": skipped, "total_eligible": len(rows)}
    except Exception as e:
        print(f"[alpha_train] backfill_alpha_labels error: {e}")
        return {"error": str(e)}


def _load_training_data() -> pd.DataFrame:
    """
    Load all settled picks with alpha labels.
    Merges aiem_paper_trades + conviction_stack_watchlist.
    """
    rows = []

    if _DB_URL:
        try:
            with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        ticker, trade_date, exit_date, pnl_pct,
                        alpha_vs_spy, signal_source,
                        CAST(NULL AS NUMERIC) AS total_pts,
                        CAST(NULL AS NUMERIC) AS rvol,
                        CAST(NULL AS NUMERIC) AS gap_pct,
                        CAST(NULL AS TEXT)    AS conviction
                    FROM aiem_paper_trades
                    WHERE alpha_vs_spy IS NOT NULL
                    ORDER BY trade_date
                """)
                for r in cur.fetchall():
                    rows.append({
                        "ticker":       r[0],
                        "trade_date":   r[1],
                        "exit_date":    r[2],
                        "pnl_pct":      float(r[3]) if r[3] else None,
                        "alpha_vs_spy": float(r[4]) if r[4] else None,
                        "source":       r[5] or "paper_trade",
                        "total_pts":    float(r[6]) if r[6] else None,
                        "rvol":         float(r[7]) if r[7] else None,
                        "gap_pct":      float(r[8]) if r[8] else None,
                        "conviction":   r[9],
                    })
        except Exception as e:
            print(f"[alpha_train] load paper trades error: {e}")

        try:
            with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                cur.execute("""
                    SELECT ticker, snap_date, entry_date, entry_open,
                           w1_pct, w2_pct, w3_pct, w4_pct,
                           total_pts, conviction_pct
                    FROM conviction_stack_watchlist
                    WHERE w1_pct IS NOT NULL
                    ORDER BY snap_date
                """)
                for r in cur.fetchall():
                    ticker     = r[0]
                    trade_date = r[2] or r[1]
                    pnl_pct    = float(r[4]) if r[4] is not None else None
                    exit_date  = (trade_date + timedelta(days=5)) if trade_date else None
                    if pnl_pct is None or trade_date is None:
                        continue
                    spy_ret = get_spy_return(trade_date, exit_date) if exit_date else None
                    alpha   = round(pnl_pct - spy_ret, 4) if spy_ret is not None else None
                    rows.append({
                        "ticker":       ticker,
                        "trade_date":   trade_date,
                        "exit_date":    exit_date,
                        "pnl_pct":      pnl_pct,
                        "alpha_vs_spy": alpha,
                        "source":       "conviction_stack",
                        "total_pts":    float(r[8]) if r[8] else None,
                        "rvol":         None,
                        "gap_pct":      None,
                        "conviction":   None,
                    })
        except Exception as e:
            print(f"[alpha_train] load conviction_stack error: {e}")

    return pd.DataFrame(rows)


def _build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Build feature rows for each pick in df."""
    feat_rows = []
    for _, row in df.iterrows():
        pick = row.to_dict()
        feat = build_alpha_feature_row(pick)
        feat["outcome"]    = 1 if (row.get("alpha_vs_spy") or 0) >= _ALPHA_THRESHOLD else 0
        feat["alpha_vs_spy"] = row.get("alpha_vs_spy", np.nan)
        feat["trade_date"] = row["trade_date"]
        feat_rows.append(feat)
    return pd.DataFrame(feat_rows)


def _build_model_pipeline():
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    try:
        from xgboost import XGBClassifier
        clf = XGBClassifier(
            n_estimators=150,
            max_depth=3,
            min_child_weight=3,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="auc",
            use_label_encoder=False,
        )
        return Pipeline([("impute", SimpleImputer(strategy="median")), ("clf", clf)])
    except ImportError:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        clf = LogisticRegression(penalty="l2", C=0.5, max_iter=1000)
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("clf", clf),
        ])


def _score_pipeline(pipeline, feature_cols, X_val, y_val) -> dict:
    from sklearn.metrics import roc_auc_score, brier_score_loss
    try:
        proba = pipeline.predict_proba(X_val)[:, 1]
        auc   = roc_auc_score(y_val, proba) if len(np.unique(y_val)) > 1 else np.nan
        brier = brier_score_loss(y_val, proba)
        return {"auc": round(auc, 4), "brier": round(brier, 4)}
    except Exception as e:
        return {"auc": np.nan, "brier": np.nan, "error": str(e)}


def run_walk_forward_validation(feat_df: pd.DataFrame) -> dict:
    """
    Expanding-window walk-forward validation.
    Returns average AUC across all windows.
    """
    from data_prep import walk_forward_splits
    from sklearn.metrics import roc_auc_score

    aucs = []
    for train_df, val_df in walk_forward_splits(
        feat_df,
        date_col="trade_date",
        initial_train_size=30,
        val_window_size=15,
        step_size=15,
    ):
        if len(train_df) < 10 or len(val_df) < 5:
            continue
        if len(np.unique(train_df["outcome"])) < 2:
            continue
        if len(np.unique(val_df["outcome"])) < 2:
            continue
        try:
            pipe = _build_model_pipeline()
            pipe.fit(train_df[ALPHA_FEATURE_COLUMNS], train_df["outcome"])
            proba = pipe.predict_proba(val_df[ALPHA_FEATURE_COLUMNS])[:, 1]
            aucs.append(roc_auc_score(val_df["outcome"], proba))
        except Exception:
            continue

    if not aucs:
        return {"wf_auc_mean": None, "wf_auc_std": None, "wf_windows": 0}
    return {
        "wf_auc_mean":  round(float(np.mean(aucs)), 4),
        "wf_auc_std":   round(float(np.std(aucs)),  4),
        "wf_windows":   len(aucs),
    }


def _load_prod_model():
    if not os.path.exists(_MODEL_PATH):
        return None
    try:
        with open(_MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _save_prod_model(obj):
    try:
        with open(_MODEL_PATH, "wb") as f:
            pickle.dump(obj, f)
    except Exception as e:
        print(f"[alpha_train] save model error: {e}")


def _log_retrain(n, n_pos, cand_auc, cand_brier, prod_auc, prod_brier, promoted, reason, metrics):
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_alpha_retrain_log
                    (retrain_date, n_samples, n_positive, candidate_auc, candidate_brier,
                     prod_auc, prod_brier, promoted, reason, metrics_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                date.today(), n, n_pos,
                None if (cand_auc is None or np.isnan(cand_auc)) else cand_auc,
                None if (cand_brier is None or np.isnan(cand_brier)) else cand_brier,
                None if (prod_auc is None or (isinstance(prod_auc, float) and np.isnan(prod_auc))) else prod_auc,
                None if (prod_brier is None or (isinstance(prod_brier, float) and np.isnan(prod_brier))) else prod_brier,
                promoted, reason, json.dumps(metrics, default=str),
            ))
            conn.commit()
    except Exception as e:
        print(f"[alpha_train] log_retrain error: {e}")


def score_ticker_now(ticker: str, pick_meta: dict = None) -> dict:
    """
    Score a single ticker against the current alpha model.
    Returns probability of generating alpha > 2% vs SPY.
    """
    prod = _load_prod_model()
    if prod is None:
        return {"error": "no alpha model trained yet", "ticker": ticker}

    pick = pick_meta or {}
    pick["ticker"]     = ticker
    pick["trade_date"] = pick.get("trade_date") or date.today()

    feat = build_alpha_feature_row(pick)
    feat_df = pd.DataFrame([feat])[ALPHA_FEATURE_COLUMNS]

    try:
        pipeline = prod["pipeline"]
        proba = pipeline.predict_proba(feat_df)[0, 1]
        return {
            "ticker":          ticker,
            "alpha_prob":      round(float(proba), 4),
            "alpha_pct_label": f"{proba*100:.1f}%",
            "signal":          "STRONG" if proba >= 0.65 else ("MODERATE" if proba >= 0.50 else "WEAK"),
            "features":        {k: (None if (isinstance(v, float) and np.isnan(v)) else v)
                                for k, v in feat.items()},
            "model_date":      str(prod.get("trained_date", "unknown")),
            "n_samples":       prod.get("n_samples", 0),
        }
    except Exception as e:
        return {"error": str(e), "ticker": ticker}


def run_alpha_retrain_cycle() -> dict:
    """
    Full pipeline: load data → build features → train → validate → compare → promote.
    """
    print("[alpha_train] starting alpha retrain cycle")

    raw_df = _load_training_data()
    if raw_df.empty:
        msg = "no settled picks with alpha labels found"
        _log_retrain(0, 0, np.nan, np.nan, None, None, False, msg, {})
        return {"promoted": False, "reason": msg}

    feat_df = _build_feature_matrix(raw_df)
    n = len(feat_df)
    n_pos = int(feat_df["outcome"].sum())
    print(f"[alpha_train] {n} samples, {n_pos} positive (alpha>{_ALPHA_THRESHOLD}%)")

    if n < _MIN_SAMPLES:
        msg = f"below MIN_SAMPLES ({n} < {_MIN_SAMPLES}); alpha model not trained yet"
        _log_retrain(n, n_pos, np.nan, np.nan, None, None, False, msg, {})
        return {"promoted": False, "reason": msg, "n_samples": n,
                "needed": _MIN_SAMPLES - n, "tip": "paper trades accumulating — check back weekly"}

    if n_pos < 10 or (n - n_pos) < 10:
        msg = f"too few examples in one class (pos={n_pos}, neg={n-n_pos})"
        _log_retrain(n, n_pos, np.nan, np.nan, None, None, False, msg, {})
        return {"promoted": False, "reason": msg}

    feat_df = feat_df.sort_values("trade_date").reset_index(drop=True)
    split_idx = int(len(feat_df) * 0.8)
    train_df  = feat_df.iloc[:split_idx]
    val_df    = feat_df.iloc[split_idx:]

    pipe = _build_model_pipeline()
    pipe.fit(train_df[ALPHA_FEATURE_COLUMNS], train_df["outcome"])

    cand_metrics = _score_pipeline(pipe, ALPHA_FEATURE_COLUMNS,
                                   val_df[ALPHA_FEATURE_COLUMNS], val_df["outcome"])
    cand_auc   = cand_metrics.get("auc", np.nan)
    cand_brier = cand_metrics.get("brier", np.nan)
    print(f"[alpha_train] candidate AUC={cand_auc} Brier={cand_brier}")

    wf = run_walk_forward_validation(feat_df)
    print(f"[alpha_train] walk-forward: {wf}")

    prod_obj   = _load_prod_model()
    prod_auc   = None
    prod_brier = None
    if prod_obj is not None:
        try:
            prod_pipe    = prod_obj["pipeline"]
            prod_metrics = _score_pipeline(prod_pipe, ALPHA_FEATURE_COLUMNS,
                                           val_df[ALPHA_FEATURE_COLUMNS], val_df["outcome"])
            prod_auc   = prod_metrics.get("auc")
            prod_brier = prod_metrics.get("brier")
            print(f"[alpha_train] prod AUC={prod_auc} Brier={prod_brier}")
        except Exception as e:
            print(f"[alpha_train] prod scoring failed: {e}")

    if prod_obj is None:
        promoted = True
        reason   = "first alpha model — promoting automatically"
    elif np.isnan(cand_auc):
        promoted = False
        reason   = "AUC could not be computed"
    elif cand_auc > (prod_auc or 0) and cand_brier < (prod_brier or 1):
        promoted = True
        reason   = f"beats prod AUC ({cand_auc} > {prod_auc}) and Brier ({cand_brier} < {prod_brier})"
    elif cand_auc > (prod_auc or 0) + 0.02:
        promoted = True
        reason   = f"AUC gain > 2pp ({cand_auc} vs {prod_auc})"
    else:
        promoted = False
        reason   = f"no improvement over prod (AUC {cand_auc} vs {prod_auc})"

    if promoted:
        full_pipe = _build_model_pipeline()
        full_pipe.fit(feat_df[ALPHA_FEATURE_COLUMNS], feat_df["outcome"])
        _save_prod_model({
            "pipeline":      full_pipe,
            "feature_cols":  ALPHA_FEATURE_COLUMNS,
            "trained_date":  date.today().isoformat(),
            "n_samples":     n,
            "n_positive":    n_pos,
            "alpha_threshold": _ALPHA_THRESHOLD,
            "val_auc":       cand_auc,
            "wf":            wf,
        })
        print(f"[alpha_train] model PROMOTED and saved")

    summary = {
        "promoted":      promoted,
        "reason":        reason,
        "n_samples":     n,
        "n_positive":    n_pos,
        "candidate_auc": cand_auc if not np.isnan(cand_auc) else None,
        "cand_brier":    cand_brier if not np.isnan(cand_brier) else None,
        "prod_auc":      prod_auc,
        "walk_forward":  wf,
    }

    _log_retrain(n, n_pos, cand_auc, cand_brier, prod_auc, prod_brier, promoted, reason, summary)
    return summary


def get_alpha_model_status() -> dict:
    """Return info about the current alpha model and training data."""
    prod = _load_prod_model()
    if prod is None:
        model_info = {"status": "not_trained_yet"}
    else:
        model_info = {
            "status":          "trained",
            "trained_date":    prod.get("trained_date"),
            "n_samples":       prod.get("n_samples"),
            "n_positive":      prod.get("n_positive"),
            "alpha_threshold": prod.get("alpha_threshold"),
            "val_auc":         prod.get("val_auc"),
            "walk_forward":    prod.get("wf"),
        }

    data_status = {"n_paper_trades_with_alpha": 0, "n_conviction_with_outcomes": 0}
    if _DB_URL:
        try:
            with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM aiem_paper_trades WHERE alpha_vs_spy IS NOT NULL")
                data_status["n_paper_trades_with_alpha"] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM conviction_stack_watchlist WHERE w1_pct IS NOT NULL")
                data_status["n_conviction_with_outcomes"] = cur.fetchone()[0]
        except Exception:
            pass

    return {"model": model_info, "data": data_status, "min_samples_needed": _MIN_SAMPLES}
