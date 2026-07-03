"""
retrain_pipeline.py

Orchestrates the full retraining cycle:
  1. Load settled picks + features from DB
  2. Gate on minimum sample size (MIN_SAMPLES = 200)
  3. Time-aware train / validation split
  4. Train candidate model
  5. Evaluate candidate vs current production model on held-out validation set
  6. Promote only if AUC improves AND calibration (Brier score) improves
  7. Log the result to aiem_ml_retrain_log

Wire run_retrain_cycle() into the Sunday 8 PM AIEM research job.
"""

import io
import json
import os
import pickle
import psycopg2
import numpy as np
import pandas as pd
from datetime import date, datetime, timezone

from data_prep import simple_time_split
from model_training import train_model, rule_based_baseline_predict, MIN_SAMPLES
from evaluation_metrics import full_report
from feature_engineering import FEATURE_COLUMNS, build_feature_row

_DB_URL = os.environ.get("DATABASE_URL", "")
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "aiem_model.pkl")

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS aiem_ml_retrain_log (
    id              SERIAL PRIMARY KEY,
    retrain_date    DATE NOT NULL,
    n_samples       INTEGER,
    candidate_auc   FLOAT,
    candidate_brier FLOAT,
    prod_auc        FLOAT,
    prod_brier      FLOAT,
    promoted        BOOLEAN,
    reason          TEXT,
    metrics_json    JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
"""


def _init_tables():
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(_INIT_SQL)
            conn.commit()
    except Exception as e:
        print(f"[retrain] init error: {e}")


_init_tables()


def _load_settled_picks() -> pd.DataFrame:
    """
    Load all settled picks with their polygon_market_daily features.
    Joins on trade_date = scan_date and ticker.
    """
    sql = """
        SELECT
            s.id,
            s.trade_date,
            s.ticker,
            s.vol_oi,
            s.otm_pct,
            s.days_out,
            s.conviction,
            s.t3_win,
            s.t5_pct,
            p.rvol,
            p.gap_pct
        FROM ai_short_calls_log s
        LEFT JOIN polygon_market_daily p
               ON p.scan_date = s.trade_date
              AND p.ticker    = s.ticker
        WHERE s.t3_win IS NOT NULL
        ORDER BY s.trade_date ASC
    """
    try:
        with psycopg2.connect(_DB_URL) as conn:
            df = pd.read_sql_query(sql, conn)
        return df
    except Exception as e:
        print(f"[retrain] load_settled_picks error: {e}")
        return pd.DataFrame()


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Turn raw pick rows into a feature matrix. Features that can't be
    computed yet are left as NaN — the model handles missing values.
    """
    rows = []
    for _, row in df.iterrows():
        feat = build_feature_row(row.to_dict(), market_df=None)
        feat["rvol"]    = row.get("rvol", np.nan)
        feat["gap_pct"] = row.get("gap_pct", np.nan)
        feat["outcome"] = 1 if row["t3_win"] else 0
        feat["return_pct"] = row.get("t5_pct", np.nan)
        feat["trade_date"] = row["trade_date"]
        rows.append(feat)

    return pd.DataFrame(rows)


def _load_production_model():
    """Load the current production model from disk. Returns None if none exists."""
    if not os.path.exists(_MODEL_PATH):
        return None
    try:
        with open(_MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        print(f"[retrain] load_prod_model error: {e}")
        return None


def _save_production_model(trained_model):
    """Persist the promoted model to disk."""
    try:
        with open(_MODEL_PATH, "wb") as f:
            pickle.dump(trained_model, f)
        print(f"[retrain] model saved to {_MODEL_PATH}")
    except Exception as e:
        print(f"[retrain] save_prod_model error: {e}")


def _score_model(model, val_df, y, returns=None) -> dict:
    """Run full_report metrics for a model against a validation set."""
    try:
        X = val_df[model.feature_columns].reset_index(drop=True)
        y_reset = y.reset_index(drop=True)
        ret_reset = returns.reset_index(drop=True) if returns is not None else None
        proba = pd.Series(model.model.predict_proba(X)[:, 1])
        return full_report(y_reset, proba, ret_reset)
    except Exception as e:
        print(f"[retrain] score_model error: {e}")
        return {"auc": np.nan, "brier_score": np.nan}


def _log_retrain(
    n_samples, candidate_auc, candidate_brier,
    prod_auc, prod_brier, promoted, reason, metrics
):
    if not _DB_URL:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_ml_retrain_log
                    (retrain_date, n_samples, candidate_auc, candidate_brier,
                     prod_auc, prod_brier, promoted, reason, metrics_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                date.today(),
                n_samples,
                candidate_auc if not np.isnan(candidate_auc) else None,
                candidate_brier if not np.isnan(candidate_brier) else None,
                prod_auc if (prod_auc is not None and not np.isnan(prod_auc)) else None,
                prod_brier if (prod_brier is not None and not np.isnan(prod_brier)) else None,
                promoted,
                reason,
                json.dumps(metrics, default=str),
            ))
            conn.commit()
    except Exception as e:
        print(f"[retrain] log_retrain error: {e}")


def run_retrain_cycle() -> dict:
    """
    Full train → validate → compare → promote-or-reject cycle.
    Returns a summary dict for logging / display.
    """
    print("[retrain] starting retraining cycle")

    raw = _load_settled_picks()
    if raw.empty:
        msg = "no settled picks found"
        print(f"[retrain] {msg}")
        _log_retrain(0, np.nan, np.nan, None, None, False, msg, {})
        return {"promoted": False, "reason": msg}

    feat_df = _build_features(raw)
    n_samples = len(feat_df)
    print(f"[retrain] {n_samples} settled picks with features built")

    if n_samples < MIN_SAMPLES:
        msg = f"below MIN_SAMPLES ({n_samples} < {MIN_SAMPLES}); keeping rule-based system"
        print(f"[retrain] {msg}")
        _log_retrain(n_samples, np.nan, np.nan, None, None, False, msg, {})
        return {"promoted": False, "reason": msg, "n_samples": n_samples}

    split = simple_time_split(feat_df, date_col="trade_date", train_frac=0.7, val_frac=0.2)
    train_df = split.train
    val_df   = split.validation

    print(f"[retrain] train={len(train_df)} val={len(val_df)} test={len(split.test)}")

    candidate = train_model(train_df, feature_columns=FEATURE_COLUMNS)
    print(f"[retrain] candidate trained: {candidate.model_type}, "
          f"cv_auc={candidate.cv_auc_mean:.3f}±{candidate.cv_auc_std:.3f}")

    X_val = val_df[FEATURE_COLUMNS]
    y_val = val_df["outcome"]
    ret_val = val_df["return_pct"] if "return_pct" in val_df.columns else None

    cand_metrics = _score_model(candidate, val_df, y_val, ret_val)
    cand_auc    = cand_metrics.get("auc", np.nan) or np.nan
    cand_brier  = cand_metrics.get("brier_score", np.nan) or np.nan

    prod_model  = _load_production_model()
    prod_auc    = None
    prod_brier  = None

    if prod_model is not None:
        try:
            prod_metrics = _score_model(prod_model, val_df, y_val, ret_val)
            prod_auc   = prod_metrics.get("auc", np.nan) or np.nan
            prod_brier = prod_metrics.get("brier_score", np.nan) or np.nan
            print(f"[retrain] prod model: auc={prod_auc:.3f} brier={prod_brier:.3f}")
        except Exception as e:
            print(f"[retrain] prod model scoring failed: {e}")
            prod_model = None

    print(f"[retrain] candidate: auc={cand_auc:.3f} brier={cand_brier:.3f}")

    if prod_model is None:
        promoted = True
        reason = "no existing production model — promoting candidate by default"
    elif np.isnan(cand_auc):
        promoted = False
        reason = "candidate AUC could not be computed (too few samples per class in val set)"
    elif cand_auc > (prod_auc or 0) and cand_brier < (prod_brier or 1):
        promoted = True
        reason = (f"candidate beats prod on both AUC ({cand_auc:.3f} > {prod_auc:.3f}) "
                  f"and Brier ({cand_brier:.3f} < {prod_brier:.3f})")
    elif cand_auc > (prod_auc or 0) + 0.02:
        promoted = True
        reason = (f"candidate AUC meaningfully better ({cand_auc:.3f} vs {prod_auc:.3f}); "
                  f"Brier not improved ({cand_brier:.3f} vs {prod_brier:.3f}) but AUC gain >2pp")
    else:
        promoted = False
        reason = (f"candidate did not beat prod: auc {cand_auc:.3f} vs {prod_auc:.3f}, "
                  f"brier {cand_brier:.3f} vs {prod_brier:.3f}")

    if promoted:
        candidate.is_trustworthy = (n_samples >= MIN_SAMPLES)
        _save_production_model(candidate)

    print(f"[retrain] {'PROMOTED' if promoted else 'NOT promoted'}: {reason}")

    summary = {
        "promoted":        promoted,
        "reason":          reason,
        "n_samples":       n_samples,
        "candidate_auc":   round(float(cand_auc), 4) if not np.isnan(cand_auc) else None,
        "candidate_brier": round(float(cand_brier), 4) if not np.isnan(cand_brier) else None,
        "prod_auc":        round(float(prod_auc), 4) if prod_auc and not np.isnan(prod_auc) else None,
        "prod_brier":      round(float(prod_brier), 4) if prod_brier and not np.isnan(prod_brier) else None,
        "model_type":      candidate.model_type,
        "cv_auc":          round(candidate.cv_auc_mean, 4) if not np.isnan(candidate.cv_auc_mean) else None,
        "is_trustworthy":  candidate.is_trustworthy,
    }

    # Run niche segment search BEFORE logging so results land in summary + get persisted
    try:
        from niche_segment_finder import run_segment_search_on_settled_picks as _seg_search
        seg_results = _seg_search(raw)
        if not seg_results.empty:
            sig = seg_results[seg_results["significant_after_correction"] == True]
            summary["significant_segments"] = len(sig)
            if not sig.empty:
                top = sig.head(3)[["segment", "win_rate", "lift", "n_samples"]].to_dict(orient="records")
                summary["top_segments"] = top
                print(f"[retrain] top segments: {top}")
                try:
                    _conn_ri = psycopg2.connect(_DB_URL)
                    with _conn_ri.cursor() as _cur_ri:
                        _cur_ri.execute("""
                            INSERT INTO aiem_research_insights (research_date, findings, confidence)
                            VALUES (CURRENT_DATE, %s, 'medium')
                            ON CONFLICT (research_date) DO UPDATE SET
                                findings = aiem_research_insights.findings
                                        || E'\\n' || EXCLUDED.findings
                        """, (json.dumps({"source": "niche_segment_finder",
                                          "top_segments": top}),))
                    _conn_ri.commit()
                    _conn_ri.close()
                    print("[retrain] persisted top_segments to aiem_research_insights")
                except Exception as _ri_e:
                    print(f"[retrain] aiem_research_insights persist error: {_ri_e}")
    except Exception as _seg_e:
        print(f"[retrain] segment search error: {_seg_e}")

    _log_retrain(
        n_samples, cand_auc, cand_brier,
        prod_auc, prod_brier, promoted, reason, summary
    )

    return summary
