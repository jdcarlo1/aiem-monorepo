"""
alpha_historical_trainer.py
────────────────────────────
Trains AIEM's "alpha leaders" XGBoost model entirely from historical market data.
Ground truth: polygon_market_daily (July 2024 → present) × sector_etf_daily (SPY returns).

Training label: does the stock beat SPY by >= 2% over the NEXT 10 trading days?
  alpha_10d = (stock_fwd_ret_10d) - (spy_fwd_ret_10d)
  label = 1 if alpha_10d >= 0.02  (outperformer)
  label = 0 if alpha_10d <= -0.02 (underperformer)
  rows between ±2% are DROPPED (they're noise)

Features built entirely from polygon_market_daily + sector_etf_daily:
  gap_pct          – open vs prior close
  rvol             – relative volume (computed or stored)
  close_strength   – where close landed in day's range
  range_pct        – intraday range as % of price
  momentum_5d      – 5-day price return
  momentum_20d     – 20-day price return
  vol_vs_20d_avg   – today's volume vs 20-day average
  price_vs_52wh    – close / 52-week high (0.0–1.0)
  sector_rs_10d    – stock 10d return vs sector ETF 10d return
  spy_rs_5d        – stock 5d return vs SPY 5d return

Admin endpoint trigger: POST /stock-api/admin/run-historical-alpha-train
"""

import os, pickle, logging, json
from datetime import datetime, timedelta, date as _date_t
import numpy as np

logger = logging.getLogger(__name__)
_DB_URL = os.environ.get("DATABASE_URL", "")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "aiem_alpha_leaders.pkl")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "aiem_alpha_leaders_report.json")

FEATURE_COLUMNS = [
    "gap_pct",
    "rvol",
    "close_strength",
    "range_pct",
    "momentum_5d",
    "momentum_20d",
    "vol_vs_20d_avg",
    "price_vs_52wh",
    "sector_rs_10d",
    "spy_rs_5d",
]

ALPHA_THRESHOLD   = 0.02   # +2% alpha → outperformer (label=1)
NOISE_BAND        = 0.02   # drop rows within ±2% of zero (too ambiguous)
FWD_DAYS          = 10     # trading days forward for return computation
MIN_PRICE         = 2.0    # drop penny stocks
MIN_VOLUME        = 100_000
MIN_TRAIN_ROWS    = 500    # minimum labeled rows before we'll train
STRONG_THRESHOLD  = 0.65   # alpha_prob >= this → STRONG BUY signal
MODERATE_THRESHOLD= 0.50   # alpha_prob >= this → MODERATE


# ─── SECTOR MAP ──────────────────────────────────────────────────────────────
# Maps rough ticker → sector ETF. Fallback = SPY if unknown.
# Expanded from TICKER_SECTOR_MAP in sector_etf_data.py
try:
    from sector_etf_data import TICKER_SECTOR_MAP as _TSM
except Exception:
    _TSM = {}

def _sector_etf(ticker: str) -> str:
    return _TSM.get(ticker, "SPY")


# ─── DATA EXTRACTION ─────────────────────────────────────────────────────────
def _build_training_dataset(conn, start_date="2024-07-01", end_date=None, progress_cb=None):
    """
    Uses SQL window functions to compute per-ticker features + forward returns
    entirely inside the database. Returns a pandas DataFrame.
    """
    import pandas as pd
    cur = conn.cursor()

    if end_date is None:
        # Leave FWD_DAYS trading days buffer so forward returns are computable
        end_date = (datetime.utcnow() - timedelta(days=FWD_DAYS * 1.5)).strftime("%Y-%m-%d")

    if progress_cb:
        progress_cb("Pulling base features from polygon_market_daily …")

    # Step 1: Pull all raw daily rows with trailing window features computed in SQL.
    # LEAD(close_price, 10) gives the stock price 10 trading days forward.
    # We filter to price > MIN_PRICE and volume > MIN_VOLUME for data quality.
    base_sql = f"""
    WITH ranked AS (
        SELECT
            scan_date,
            ticker,
            close_price,
            COALESCE(gap_pct, (open_price - prev_close) / NULLIF(prev_close, 0) * 100) AS gap_pct,
            COALESCE(rvol,
                volume::float / NULLIF(
                    AVG(volume::float) OVER (
                        PARTITION BY ticker ORDER BY scan_date
                        ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
                    ), 0)
            ) AS rvol,
            COALESCE(close_strength, 0.5)   AS close_strength,
            COALESCE(range_pct, 0.0)        AS range_pct,
            -- momentum features
            close_price / NULLIF(
                LAG(close_price, 5)  OVER (PARTITION BY ticker ORDER BY scan_date), 0
            ) - 1 AS momentum_5d,
            close_price / NULLIF(
                LAG(close_price, 20) OVER (PARTITION BY ticker ORDER BY scan_date), 0
            ) - 1 AS momentum_20d,
            -- volume trend
            volume::float / NULLIF(
                AVG(volume::float) OVER (
                    PARTITION BY ticker ORDER BY scan_date
                    ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
                ), 0
            ) AS vol_vs_20d_avg,
            -- proximity to 52-week high
            close_price / NULLIF(
                MAX(close_price) OVER (
                    PARTITION BY ticker ORDER BY scan_date
                    ROWS BETWEEN 252 PRECEDING AND CURRENT ROW
                ), 0
            ) AS price_vs_52wh,
            -- forward stock return (10 trading days)
            LEAD(close_price, {FWD_DAYS}) OVER (
                PARTITION BY ticker ORDER BY scan_date
            ) / NULLIF(close_price, 0) - 1 AS fwd_ret_10d,
            -- row number within ticker (need >= 21 days history for windows)
            ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY scan_date) AS rn
        FROM polygon_market_daily
        WHERE scan_date BETWEEN %(start)s AND %(end)s
          AND close_price >= %(min_price)s
          AND volume     >= %(min_vol)s
    )
    SELECT scan_date, ticker, close_price,
           gap_pct, rvol, close_strength, range_pct,
           momentum_5d, momentum_20d, vol_vs_20d_avg, price_vs_52wh,
           fwd_ret_10d
    FROM ranked
    WHERE rn >= 22              -- need 21 days of history for windows
      AND fwd_ret_10d IS NOT NULL
      AND momentum_5d  IS NOT NULL
      AND momentum_20d IS NOT NULL
    """

    cur.execute(base_sql, {
        "start":     start_date,
        "end":       end_date,
        "min_price": MIN_PRICE,
        "min_vol":   MIN_VOLUME,
    })
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    if progress_cb:
        progress_cb(f"  → {len(df):,} base rows loaded")

    if df.empty:
        return df, {}

    # Step 2: Pull SPY daily returns from sector_etf_daily for forward SPY return
    if progress_cb:
        progress_cb("Loading SPY & sector ETF forward returns …")

    spy_sql = """
        SELECT price_date, close_price,
               LEAD(close_price, %(fwd)s) OVER (ORDER BY price_date)
               / NULLIF(close_price, 0) - 1 AS spy_fwd_10d,
               close_price / NULLIF(
                   LAG(close_price, 5) OVER (ORDER BY price_date), 0
               ) - 1 AS spy_ret_5d
        FROM sector_etf_daily
        WHERE etf_ticker = 'SPY'
        ORDER BY price_date
    """
    cur.execute(spy_sql, {"fwd": FWD_DAYS})
    spy_rows = cur.fetchall()
    spy_df = pd.DataFrame(spy_rows, columns=["price_date", "spy_close", "spy_fwd_10d", "spy_ret_5d"])
    spy_df["price_date"]  = pd.to_datetime(spy_df["price_date"]).dt.date
    spy_df["spy_fwd_10d"] = spy_df["spy_fwd_10d"].astype(float)
    spy_df["spy_ret_5d"]  = spy_df["spy_ret_5d"].astype(float)

    # Step 3: Pull per-sector ETF forward return for sector_rs
    sector_sql = """
        SELECT etf_ticker, price_date,
               close_price / NULLIF(
                   LAG(close_price, 10) OVER (PARTITION BY etf_ticker ORDER BY price_date), 0
               ) - 1 AS etf_ret_10d
        FROM sector_etf_daily
        WHERE etf_ticker != 'SPY'
        ORDER BY etf_ticker, price_date
    """
    cur.execute(sector_sql)
    sector_rows = cur.fetchall()
    sector_df = pd.DataFrame(sector_rows, columns=["etf_ticker", "price_date", "etf_ret_10d"])
    sector_df["price_date"]  = pd.to_datetime(sector_df["price_date"]).dt.date
    sector_df["etf_ret_10d"] = sector_df["etf_ret_10d"].astype(float)

    # Step 4: Join everything
    df["scan_date"] = pd.to_datetime(df["scan_date"]).dt.date
    df = df.merge(
        spy_df[["price_date", "spy_fwd_10d", "spy_ret_5d"]],
        left_on="scan_date", right_on="price_date", how="inner"
    ).drop(columns=["price_date"])

    # Add sector ETF for each ticker
    df["etf_ticker"] = df["ticker"].map(lambda t: _sector_etf(t))
    df = df.merge(
        sector_df.rename(columns={"etf_ticker": "etf_ticker", "price_date": "price_date"}),
        left_on=["etf_ticker", "scan_date"],
        right_on=["etf_ticker", "price_date"],
        how="left"
    )
    if "price_date" in df.columns:
        df.drop(columns=["price_date"], inplace=True)

    # Cast ALL numeric columns to float immediately after merge.
    # psycopg2 returns NUMERIC as Decimal — arithmetic fails unless we cast first.
    for _col in ["fwd_ret_10d", "spy_fwd_10d", "spy_ret_5d", "etf_ret_10d",
                 "gap_pct", "rvol", "close_strength", "range_pct",
                 "momentum_5d", "momentum_20d", "vol_vs_20d_avg", "price_vs_52wh"]:
        if _col in df.columns:
            df[_col] = pd.to_numeric(df[_col], errors="coerce").astype(float)

    # sector_rs_10d = trailing stock return vs trailing sector ETF return.
    # MUST use only past data here — fwd_ret_10d is future and would leak the label.
    # Use momentum_5d (trailing 5d stock return) vs etf_ret_10d (trailing 10d ETF return).
    df["sector_rs_10d"] = df["momentum_5d"] - df["etf_ret_10d"].fillna(df["spy_ret_5d"])
    df["spy_rs_5d"]     = df["momentum_5d"] - df["spy_ret_5d"].fillna(0.0)

    # Step 5: Compute alpha label
    df["alpha_10d"] = df["fwd_ret_10d"] - df["spy_fwd_10d"]
    df = df.dropna(subset=["alpha_10d", "spy_fwd_10d"])
    df["label"] = np.where(
        df["alpha_10d"] >= ALPHA_THRESHOLD,   1,  # outperformer
        np.where(
            df["alpha_10d"] <= -ALPHA_THRESHOLD, 0,  # underperformer
            -1  # noise — will be dropped
        )
    )
    df = df[df["label"] >= 0].copy()

    stats = {
        "total_rows":    len(df),
        "outperformers": int((df["label"] == 1).sum()),
        "underperformers": int((df["label"] == 0).sum()),
        "pct_outperform": round(float((df["label"] == 1).mean()) * 100, 1),
        "date_range":    f"{df['scan_date'].min()} → {df['scan_date'].max()}",
        "unique_tickers": int(df["ticker"].nunique()),
    }

    if progress_cb:
        progress_cb(
            f"  → {stats['total_rows']:,} labeled rows | "
            f"{stats['outperformers']:,} outperformers ({stats['pct_outperform']}%) | "
            f"{stats['unique_tickers']:,} unique tickers"
        )

    return df, stats


# ─── WALK-FORWARD VALIDATION ─────────────────────────────────────────────────
def _walk_forward_validate(df, n_splits=4):
    """
    Time-aware walk-forward cross-validation.
    Splits chronologically — never trains on future data.
    Returns average AUC across folds.
    """
    import pandas as pd
    from sklearn.metrics import roc_auc_score

    df = df.sort_values("scan_date").reset_index(drop=True)
    n = len(df)
    split_size = n // (n_splits + 1)

    aucs, precisions, recalls = [], [], []
    for i in range(n_splits):
        train_end = split_size * (i + 1)
        test_start = train_end
        test_end   = train_end + split_size

        train_df = df.iloc[:train_end]
        test_df  = df.iloc[test_start:test_end]

        if len(train_df) < MIN_TRAIN_ROWS or len(test_df) < 100:
            continue

        X_tr = train_df[FEATURE_COLUMNS].values.astype(np.float32).copy()
        y_tr = train_df["label"].values.copy()
        X_te = test_df[FEATURE_COLUMNS].values.astype(np.float32).copy()
        y_te = test_df["label"].values.copy()

        # Fill NaN with column medians from training set
        medians = np.nanmedian(X_tr, axis=0)
        for col_idx in range(X_tr.shape[1]):
            X_tr[:, col_idx] = np.where(np.isnan(X_tr[:, col_idx]), medians[col_idx], X_tr[:, col_idx])
            X_te[:, col_idx] = np.where(np.isnan(X_te[:, col_idx]), medians[col_idx], X_te[:, col_idx])

        try:
            import xgboost as xgb
            pos_w = (y_tr == 0).sum() / max((y_tr == 1).sum(), 1)
            model = xgb.XGBClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                scale_pos_weight=pos_w, eval_metric="logloss",
                use_label_encoder=False, random_state=42,
                n_jobs=-1,
            )
            model.fit(X_tr, y_tr)
            probs = model.predict_proba(X_te)[:, 1]
            auc = roc_auc_score(y_te, probs)
            preds = (probs >= STRONG_THRESHOLD).astype(int)
            tp = ((preds == 1) & (y_te == 1)).sum()
            fp = ((preds == 1) & (y_te == 0)).sum()
            fn = ((preds == 0) & (y_te == 1)).sum()
            prec = tp / max(tp + fp, 1)
            rec  = tp / max(tp + fn, 1)
            aucs.append(auc)
            precisions.append(prec)
            recalls.append(rec)
        except Exception as e:
            logger.warning(f"WF fold {i} failed: {e}")

    return {
        "n_folds":         len(aucs),
        "avg_auc":         round(float(np.mean(aucs)),  4) if aucs else None,
        "avg_precision":   round(float(np.mean(precisions)), 4) if precisions else None,
        "avg_recall":      round(float(np.mean(recalls)), 4) if recalls else None,
        "fold_aucs":       [round(a, 4) for a in aucs],
    }


# ─── FULL TRAIN CYCLE ────────────────────────────────────────────────────────
def run_historical_alpha_train(start_date="2024-07-01", progress_cb=None):
    """
    Main entry point. Pulls historical data, labels it, walk-forward validates,
    then trains the final model on ALL data and saves it.

    Called by admin endpoint — NEVER called automatically.
    Returns a detailed report dict.
    """
    import psycopg2, pandas as pd
    import xgboost as xgb
    import json

    if progress_cb:
        progress_cb("=== AIEM Alpha Leaders Historical Train ===")

    conn = psycopg2.connect(_DB_URL)
    try:
        df, data_stats = _build_training_dataset(conn, start_date=start_date, progress_cb=progress_cb)
    finally:
        conn.close()

    if df.empty or len(df) < MIN_TRAIN_ROWS:
        return {
            "status": "insufficient_data",
            "rows":   len(df),
            "message": f"Need >={MIN_TRAIN_ROWS} labeled rows, got {len(df)}. Run sector ETF backfill first.",
        }

    if progress_cb:
        progress_cb("Running walk-forward validation …")
    wf_results = _walk_forward_validate(df)
    if progress_cb:
        progress_cb(
            f"  → AUC={wf_results['avg_auc']} | "
            f"Precision@{int(STRONG_THRESHOLD*100)}%={wf_results['avg_precision']} | "
            f"Recall={wf_results['avg_recall']} over {wf_results['n_folds']} folds"
        )

    # Final train on all data
    if progress_cb:
        progress_cb("Training final model on full dataset …")

    X = df[FEATURE_COLUMNS].values.astype(np.float32)
    y = df["label"].values

    # Median imputation for NaN
    medians = np.nanmedian(X, axis=0)
    for col_idx in range(X.shape[1]):
        X[:, col_idx] = np.where(np.isnan(X[:, col_idx]), medians[col_idx], X[:, col_idx])

    pos_w = float((y == 0).sum() / max((y == 1).sum(), 1))
    final_model = xgb.XGBClassifier(
        n_estimators=300, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pos_w, eval_metric="logloss",
        use_label_encoder=False, random_state=42,
        n_jobs=-1,
    )
    final_model.fit(X, y)

    # Feature importances
    fi = dict(zip(FEATURE_COLUMNS, final_model.feature_importances_))
    fi_sorted = sorted(fi.items(), key=lambda x: x[1], reverse=True)

    # Save model + metadata
    artifact = {
        "model":        final_model,
        "feature_cols": FEATURE_COLUMNS,
        "medians":      medians.tolist(),
        "trained_at":   datetime.utcnow().isoformat(),
        "n_samples":    int(len(df)),
        "pct_outperform": data_stats["pct_outperform"],
    }
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(artifact, f)

    report = {
        "status":             "trained",
        "model_path":         MODEL_PATH,
        "trained_at":         artifact["trained_at"],
        "data":               data_stats,
        "walk_forward":       wf_results,
        "feature_importance": {k: round(float(v), 4) for k, v in fi_sorted},
        "top_signals":        [k for k, _ in fi_sorted[:3]],
        "threshold_strong":   STRONG_THRESHOLD,
        "threshold_moderate": MODERATE_THRESHOLD,
        "note": (
            "Walk-forward AUC > 0.55 = model has edge. "
            "Precision = % of STRONG signals that actually outperformed SPY. "
            "Model retrained every Sunday 9 PM ET as new paper trades settle."
        ),
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)

    if progress_cb:
        progress_cb(f"=== Done. Model saved. Top signal: {fi_sorted[0][0]} ===")

    return report


# ─── SCORE A TICKER NOW ───────────────────────────────────────────────────────
def alpha_leaders_score(ticker: str, pick: dict = None) -> dict:
    """
    Score a single ticker against the alpha leaders model.
    Builds features from current polygon_market_daily data.
    Returns alpha_prob, signal, and all feature values.
    """
    import psycopg2, pandas as pd

    if not os.path.exists(MODEL_PATH):
        return {
            "ticker": ticker,
            "error": "Alpha leaders model not trained yet. "
                     "Run POST /stock-api/admin/run-historical-alpha-train first.",
            "signal": "NO_MODEL",
        }

    with open(MODEL_PATH, "rb") as f:
        artifact = pickle.load(f)

    model   = artifact["model"]
    medians = np.array(artifact["medians"], dtype=np.float32)

    conn = psycopg2.connect(_DB_URL)
    try:
        cur = conn.cursor()
        # Pull recent rows for this ticker to compute trailing features
        cur.execute("""
            SELECT scan_date, close_price, open_price, prev_close,
                   gap_pct, rvol, close_strength, range_pct, volume
            FROM polygon_market_daily
            WHERE ticker = %s
              AND close_price >= %s
            ORDER BY scan_date DESC
            LIMIT 30
        """, (ticker, MIN_PRICE))
        rows = cur.fetchall()
        if not rows:
            return {"ticker": ticker, "error": "No recent data in polygon_market_daily", "signal": "NO_DATA"}

        cols = ["scan_date", "close_price", "open_price", "prev_close",
                "gap_pct", "rvol", "close_strength", "range_pct", "volume"]
        tdf = pd.DataFrame(rows, columns=cols).sort_values("scan_date")

        latest = tdf.iloc[-1]
        closes = tdf["close_price"].values

        gap_pct        = float(latest["gap_pct"] or 0)
        rvol           = float(latest["rvol"] or 1.0)
        close_strength = float(latest["close_strength"] or 0.5)
        range_pct      = float(latest["range_pct"] or 0)
        momentum_5d    = float(closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0
        momentum_20d   = float(closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0
        vols           = tdf["volume"].values.astype(float)
        vol_vs_20d     = float(vols[-1] / np.mean(vols[:-1])) if len(vols) > 1 else 1.0
        price_vs_52wh  = float(closes[-1] / np.max(closes)) if len(closes) > 0 else 1.0

        # Sector relative strength
        sector_etf_sym = _sector_etf(ticker)
        cur.execute("""
            SELECT return_pct FROM sector_etf_daily
            WHERE etf_ticker = %s ORDER BY price_date DESC LIMIT 10
        """, (sector_etf_sym,))
        etf_rows = cur.fetchall()
        sector_rs_10d = 0.0
        if etf_rows:
            etf_cum = sum(r[0] or 0 for r in etf_rows) / 100.0
            sector_rs_10d = momentum_5d - etf_cum

        # SPY 5d return
        cur.execute("""
            SELECT return_pct FROM sector_etf_daily
            WHERE etf_ticker = 'SPY' ORDER BY price_date DESC LIMIT 5
        """)
        spy_rows = cur.fetchall()
        spy_rs_5d = 0.0
        if spy_rows:
            spy_cum = sum(r[0] or 0 for r in spy_rows) / 100.0
            spy_rs_5d = momentum_5d - spy_cum

    finally:
        conn.close()

    # Override features from pick dict if caller provided them
    if pick:
        if "rvol"    in pick: rvol     = float(pick["rvol"])
        if "gap_pct" in pick: gap_pct  = float(pick["gap_pct"])

    features = np.array([[
        gap_pct, rvol, close_strength, range_pct,
        momentum_5d, momentum_20d, vol_vs_20d, price_vs_52wh,
        sector_rs_10d, spy_rs_5d,
    ]], dtype=np.float32)

    # NaN → median imputation
    for i in range(features.shape[1]):
        if np.isnan(features[0, i]):
            features[0, i] = medians[i]

    prob = float(model.predict_proba(features)[0][1])

    if prob >= STRONG_THRESHOLD:
        signal = "STRONG"
    elif prob >= MODERATE_THRESHOLD:
        signal = "MODERATE"
    else:
        signal = "WEAK"

    return {
        "ticker":          ticker,
        "alpha_prob":      round(prob, 3),
        "signal":          signal,
        "model_version":   artifact.get("trained_at", "unknown"),
        "features": {
            "gap_pct":        round(gap_pct, 3),
            "rvol":           round(rvol, 2),
            "close_strength": round(close_strength, 3),
            "range_pct":      round(range_pct, 3),
            "momentum_5d":    round(momentum_5d * 100, 2),
            "momentum_20d":   round(momentum_20d * 100, 2),
            "vol_vs_20d_avg": round(vol_vs_20d, 2),
            "price_vs_52wh":  round(price_vs_52wh, 3),
            "sector_rs_10d":  round(sector_rs_10d * 100, 2),
            "spy_rs_5d":      round(spy_rs_5d * 100, 2),
            "sector_etf":     sector_etf_sym,
        },
        "interpretation": {
            "STRONG":   "High probability of beating SPY by >=2% over next 10 trading days",
            "MODERATE": "Moderate edge vs SPY — worth watching with other signals",
            "WEAK":     "Below average alpha probability — SPY likely to match or beat",
        }[signal],
    }


# ─── TRAINING STATUS ─────────────────────────────────────────────────────────
def get_alpha_leaders_status() -> dict:
    if not os.path.exists(MODEL_PATH):
        return {"status": "not_trained", "message": "Run the historical train endpoint to build the model."}
    with open(MODEL_PATH, "rb") as f:
        a = pickle.load(f)
    report = {}
    if os.path.exists(REPORT_PATH):
        with open(REPORT_PATH) as f:
            report = json.load(f)
    return {
        "status":          "trained",
        "trained_at":      a.get("trained_at"),
        "n_samples":       a.get("n_samples"),
        "pct_outperform":  a.get("pct_outperform"),
        "feature_columns": a.get("feature_cols"),
        "walk_forward":    report.get("walk_forward"),
        "feature_importance": report.get("feature_importance"),
        "top_signals":     report.get("top_signals"),
    }
