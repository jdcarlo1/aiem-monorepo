"""
alpha_historical_trainer.py
────────────────────────────
Trains AIEM's "alpha leaders" XGBoost models from 2 years of real market data.
Two horizons — run both for complete coverage:

  10-day model  (aiem_alpha_leaders_10d.pkl)
    Label: stock beats SPY by >=2% over next 10 trading days (~2 weeks)
    Catches: short-term momentum bursts, post-catalyst moves, sector rotations
    Threshold: alpha >= 2%

  60-day model  (aiem_alpha_leaders_60d.pkl)
    Label: stock beats SPY by >=5% over next 60 trading days (~3 months)
    Catches: early-stage long-term uptrends — the MU/SNDK type moves
    Threshold: alpha >= 5%
    Extra features: momentum_60d, sector_rs_20d, spy_rs_20d (longer signals)

Ground truth: polygon_market_daily (July 2024→present) × sector_etf_daily (SPY/sector ETFs).
NO paper trades used — this is clean historical market data only.

Admin endpoints:
  POST /stock-api/admin/run-historical-alpha-train          body: {"fwd_days": 10}  (default)
  POST /stock-api/admin/run-historical-alpha-train          body: {"fwd_days": 60}
  GET  /stock-api/admin/alpha-model-status
"""

import os, pickle, logging, json
from datetime import datetime, timedelta
import numpy as np

logger = logging.getLogger(__name__)
_DB_URL = os.environ.get("DATABASE_URL", "")

_DIR = os.path.dirname(__file__)

def _model_path(fwd_days: int) -> str:
    return os.path.join(_DIR, f"aiem_alpha_leaders_{fwd_days}d.pkl")

def _report_path(fwd_days: int) -> str:
    return os.path.join(_DIR, f"aiem_alpha_leaders_{fwd_days}d_report.json")

# Backward-compat alias for existing code that imports MODEL_PATH
MODEL_PATH  = _model_path(10)
REPORT_PATH = _report_path(10)

# Features shared by both horizons
_FEATURES_BASE = [
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

# Extra features only in the 60-day model — longer signals that reveal accumulation
_FEATURES_60D_EXTRA = [
    "momentum_60d",    # 60-day trailing price return — is the stock in an uptrend?
    "sector_rs_20d",   # stock 20d return vs sector ETF 20d return
    "spy_rs_20d",      # stock 20d return vs SPY 20d return
]

def _feature_cols(fwd_days: int):
    if fwd_days >= 60:
        return _FEATURES_BASE + _FEATURES_60D_EXTRA
    return _FEATURES_BASE

# Config per horizon
_CFG = {
    10: {"alpha_threshold": 0.02, "min_history_rows": 22,  "label_col": "alpha_10d"},
    60: {"alpha_threshold": 0.05, "min_history_rows": 65,  "label_col": "alpha_60d"},
}

MIN_PRICE         = 2.0
MIN_VOLUME        = 100_000
MIN_TRAIN_ROWS    = 500
STRONG_THRESHOLD  = 0.65
MODERATE_THRESHOLD= 0.50


# ─── SECTOR MAP ──────────────────────────────────────────────────────────────
try:
    from sector_etf_data import TICKER_SECTOR_MAP as _TSM
except Exception:
    _TSM = {}

def _sector_etf(ticker: str) -> str:
    return _TSM.get(ticker, "SPY")


# ─── DATA EXTRACTION ─────────────────────────────────────────────────────────
def _build_training_dataset(conn, fwd_days=10, start_date="2024-07-01",
                             end_date=None, progress_cb=None):
    """
    Pulls polygon_market_daily + sector_etf_daily and builds a labeled DataFrame.
    All features use ONLY past data — zero leakage of the forward return.
    """
    import pandas as pd
    cur = conn.cursor()
    cfg = _CFG.get(fwd_days, _CFG[10])
    alpha_threshold = cfg["alpha_threshold"]
    min_hist        = cfg["min_history_rows"]
    label_col       = cfg["label_col"]

    if end_date is None:
        end_date = (datetime.utcnow() - timedelta(days=int(fwd_days * 1.6))).strftime("%Y-%m-%d")

    if progress_cb:
        progress_cb(f"Pulling base features (horizon={fwd_days}d, start={start_date}) …")

    # Build momentum_60d column only when needed (costs extra DB work)
    mom60_col = ""
    if fwd_days >= 60:
        mom60_col = """
            close_price / NULLIF(
                LAG(close_price, 60) OVER (PARTITION BY ticker ORDER BY scan_date), 0
            ) - 1 AS momentum_60d,"""

    base_sql = f"""
    WITH ranked AS (
        SELECT
            scan_date,
            ticker,
            close_price,
            COALESCE(gap_pct,
                (open_price - prev_close) / NULLIF(prev_close, 0) * 100
            ) AS gap_pct,
            COALESCE(rvol,
                volume::float / NULLIF(
                    AVG(volume::float) OVER (
                        PARTITION BY ticker ORDER BY scan_date
                        ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
                    ), 0)
            ) AS rvol,
            COALESCE(close_strength, 0.5) AS close_strength,
            COALESCE(range_pct,     0.0)  AS range_pct,
            close_price / NULLIF(
                LAG(close_price, 5)  OVER (PARTITION BY ticker ORDER BY scan_date), 0
            ) - 1 AS momentum_5d,
            close_price / NULLIF(
                LAG(close_price, 20) OVER (PARTITION BY ticker ORDER BY scan_date), 0
            ) - 1 AS momentum_20d,
            {mom60_col}
            volume::float / NULLIF(
                AVG(volume::float) OVER (
                    PARTITION BY ticker ORDER BY scan_date
                    ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
                ), 0
            ) AS vol_vs_20d_avg,
            close_price / NULLIF(
                MAX(close_price) OVER (
                    PARTITION BY ticker ORDER BY scan_date
                    ROWS BETWEEN 252 PRECEDING AND CURRENT ROW
                ), 0
            ) AS price_vs_52wh,
            LEAD(close_price, {fwd_days}) OVER (
                PARTITION BY ticker ORDER BY scan_date
            ) / NULLIF(close_price, 0) - 1 AS fwd_ret,
            ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY scan_date) AS rn
        FROM polygon_market_daily
        WHERE scan_date BETWEEN %(start)s AND %(end)s
          AND close_price >= %(min_price)s
          AND volume      >= %(min_vol)s
    )
    SELECT *
    FROM ranked
    WHERE rn        >= {min_hist}
      AND fwd_ret    IS NOT NULL
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
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    if progress_cb:
        progress_cb(f"  → {len(df):,} base rows loaded")
    if df.empty:
        return df, {}

    # ── SPY forward + trailing returns ───────────────────────────────────────
    if progress_cb:
        progress_cb("Loading SPY & sector ETF reference returns …")

    spy_sql = """
        SELECT price_date,
               LEAD(close_price, %(fwd)s) OVER (ORDER BY price_date)
               / NULLIF(close_price, 0) - 1 AS spy_fwd,
               close_price / NULLIF(LAG(close_price,  5) OVER (ORDER BY price_date), 0) - 1 AS spy_ret_5d,
               close_price / NULLIF(LAG(close_price, 20) OVER (ORDER BY price_date), 0) - 1 AS spy_ret_20d
        FROM sector_etf_daily
        WHERE etf_ticker = 'SPY'
        ORDER BY price_date
    """
    cur.execute(spy_sql, {"fwd": fwd_days})
    spy_df = pd.DataFrame(cur.fetchall(),
                          columns=["price_date", "spy_fwd", "spy_ret_5d", "spy_ret_20d"])
    spy_df["price_date"] = pd.to_datetime(spy_df["price_date"]).dt.date
    for c in ["spy_fwd", "spy_ret_5d", "spy_ret_20d"]:
        spy_df[c] = pd.to_numeric(spy_df[c], errors="coerce").astype(float)

    # ── Sector ETF trailing returns (10d and 20d) ─────────────────────────────
    sector_sql = """
        SELECT etf_ticker, price_date,
               close_price / NULLIF(LAG(close_price, 10) OVER (PARTITION BY etf_ticker ORDER BY price_date), 0) - 1 AS etf_ret_10d,
               close_price / NULLIF(LAG(close_price, 20) OVER (PARTITION BY etf_ticker ORDER BY price_date), 0) - 1 AS etf_ret_20d
        FROM sector_etf_daily
        WHERE etf_ticker != 'SPY'
        ORDER BY etf_ticker, price_date
    """
    cur.execute(sector_sql)
    sec_df = pd.DataFrame(cur.fetchall(),
                          columns=["etf_ticker", "price_date", "etf_ret_10d", "etf_ret_20d"])
    sec_df["price_date"]  = pd.to_datetime(sec_df["price_date"]).dt.date
    sec_df["etf_ret_10d"] = pd.to_numeric(sec_df["etf_ret_10d"], errors="coerce").astype(float)
    sec_df["etf_ret_20d"] = pd.to_numeric(sec_df["etf_ret_20d"], errors="coerce").astype(float)

    # ── Joins ────────────────────────────────────────────────────────────────
    df["scan_date"] = pd.to_datetime(df["scan_date"]).dt.date
    df = df.merge(spy_df, left_on="scan_date", right_on="price_date", how="inner").drop(columns=["price_date"])

    df["etf_ticker"] = df["ticker"].map(_sector_etf)
    df = df.merge(sec_df, left_on=["etf_ticker", "scan_date"],
                  right_on=["etf_ticker", "price_date"], how="left")
    if "price_date" in df.columns:
        df.drop(columns=["price_date"], inplace=True)

    # ── Cast all numerics to float (Postgres NUMERIC → Decimal otherwise) ────
    num_cols = ["fwd_ret", "spy_fwd", "spy_ret_5d", "spy_ret_20d",
                "etf_ret_10d", "etf_ret_20d",
                "gap_pct", "rvol", "close_strength", "range_pct",
                "momentum_5d", "momentum_20d", "vol_vs_20d_avg", "price_vs_52wh"]
    if "momentum_60d" in df.columns:
        num_cols.append("momentum_60d")
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)

    # ── Derived features (all trailing — NO future data) ─────────────────────
    # sector_rs_10d: stock 5d return vs sector ETF trailing 10d return
    df["sector_rs_10d"] = df["momentum_5d"] - df["etf_ret_10d"].fillna(df["spy_ret_5d"])
    df["spy_rs_5d"]     = df["momentum_5d"] - df["spy_ret_5d"].fillna(0.0)

    if fwd_days >= 60:
        # sector_rs_20d: stock 20d return vs sector ETF trailing 20d return
        df["sector_rs_20d"] = df["momentum_20d"] - df["etf_ret_20d"].fillna(df["spy_ret_20d"])
        df["spy_rs_20d"]    = df["momentum_20d"] - df["spy_ret_20d"].fillna(0.0)

    # ── Alpha label ───────────────────────────────────────────────────────────
    df[label_col] = df["fwd_ret"] - df["spy_fwd"]
    df = df.dropna(subset=[label_col, "spy_fwd"])
    df["label"] = np.where(
        df[label_col] >=  alpha_threshold, 1,
        np.where(df[label_col] <= -alpha_threshold, 0, -1)
    )
    df = df[df["label"] >= 0].copy()

    stats = {
        "total_rows":      len(df),
        "outperformers":   int((df["label"] == 1).sum()),
        "underperformers": int((df["label"] == 0).sum()),
        "pct_outperform":  round(float((df["label"] == 1).mean()) * 100, 1),
        "date_range":      f"{df['scan_date'].min()} → {df['scan_date'].max()}",
        "unique_tickers":  int(df["ticker"].nunique()),
        "fwd_days":        fwd_days,
        "alpha_threshold": alpha_threshold,
    }
    if progress_cb:
        progress_cb(
            f"  → {stats['total_rows']:,} labeled rows | "
            f"{stats['outperformers']:,} outperformers ({stats['pct_outperform']}%) | "
            f"{stats['unique_tickers']:,} tickers"
        )
    return df, stats


# ─── WALK-FORWARD VALIDATION ─────────────────────────────────────────────────
def _walk_forward_validate(df, fwd_days=10, n_splits=4):
    from sklearn.metrics import roc_auc_score
    import xgboost as xgb

    feat_cols = _feature_cols(fwd_days)
    df = df.sort_values("scan_date").reset_index(drop=True)
    n = len(df)
    split_size = n // (n_splits + 1)
    aucs, precisions, recalls = [], [], []

    for i in range(n_splits):
        train_end  = split_size * (i + 1)
        test_start = train_end
        test_end   = train_end + split_size
        tr = df.iloc[:train_end]
        te = df.iloc[test_start:test_end]
        if len(tr) < MIN_TRAIN_ROWS or len(te) < 100:
            continue

        X_tr = tr[feat_cols].values.astype(np.float32).copy()
        y_tr = tr["label"].values.copy()
        X_te = te[feat_cols].values.astype(np.float32).copy()
        y_te = te["label"].values.copy()

        meds = np.nanmedian(X_tr, axis=0)
        for ci in range(X_tr.shape[1]):
            X_tr[:, ci] = np.where(np.isnan(X_tr[:, ci]), meds[ci], X_tr[:, ci])
            X_te[:, ci] = np.where(np.isnan(X_te[:, ci]), meds[ci], X_te[:, ci])

        try:
            pw = float((y_tr == 0).sum() / max((y_tr == 1).sum(), 1))
            m  = xgb.XGBClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                scale_pos_weight=pw, eval_metric="logloss",
                use_label_encoder=False, random_state=42, n_jobs=-1,
            )
            m.fit(X_tr, y_tr)
            probs = m.predict_proba(X_te)[:, 1]
            auc   = roc_auc_score(y_te, probs)
            preds = (probs >= STRONG_THRESHOLD).astype(int)
            tp  = ((preds == 1) & (y_te == 1)).sum()
            fp  = ((preds == 1) & (y_te == 0)).sum()
            fn  = ((preds == 0) & (y_te == 1)).sum()
            aucs.append(auc)
            precisions.append(tp / max(tp + fp, 1))
            recalls.append(tp / max(tp + fn, 1))
        except Exception as e:
            logger.warning(f"WF fold {i} failed: {e}")

    return {
        "n_folds":       len(aucs),
        "avg_auc":       round(float(np.mean(aucs)),       4) if aucs else None,
        "avg_precision": round(float(np.mean(precisions)), 4) if precisions else None,
        "avg_recall":    round(float(np.mean(recalls)),    4) if recalls else None,
        "fold_aucs":     [round(a, 4) for a in aucs],
    }


# ─── FULL TRAIN CYCLE ────────────────────────────────────────────────────────
def run_historical_alpha_train(start_date="2024-07-01", fwd_days=10, progress_cb=None):
    """
    Main entry. Pulls 2 years of real market data, labels it, walk-forward
    validates, trains final XGBoost, saves model.

    fwd_days=10  → short-term momentum model  (2-week alpha)
    fwd_days=60  → long-term uptrend model    (3-month alpha — catches MU/SNDK type moves)
    """
    import psycopg2, xgboost as xgb

    label = "60-DAY LONG-TERM RUNNER" if fwd_days >= 60 else "10-DAY MOMENTUM"
    if progress_cb:
        progress_cb(f"=== AIEM Alpha Leaders [{label}] Train ===")

    conn = psycopg2.connect(_DB_URL)
    try:
        df, data_stats = _build_training_dataset(
            conn, fwd_days=fwd_days, start_date=start_date, progress_cb=progress_cb
        )
    finally:
        conn.close()

    if df.empty or len(df) < MIN_TRAIN_ROWS:
        return {
            "status":  "insufficient_data",
            "rows":    len(df),
            "fwd_days": fwd_days,
            "message": f"Need >={MIN_TRAIN_ROWS} labeled rows, got {len(df)}.",
        }

    feat_cols = _feature_cols(fwd_days)
    if progress_cb:
        progress_cb(f"Walk-forward validation ({len(feat_cols)} features) …")
    wf = _walk_forward_validate(df, fwd_days=fwd_days)
    if progress_cb:
        progress_cb(
            f"  → AUC={wf['avg_auc']} | "
            f"Precision@{int(STRONG_THRESHOLD*100)}%={wf['avg_precision']} | "
            f"Recall={wf['avg_recall']} over {wf['n_folds']} folds"
        )

    if progress_cb:
        progress_cb("Training final model on full dataset …")

    X = df[feat_cols].values.astype(np.float32).copy()
    y = df["label"].values

    meds = np.nanmedian(X, axis=0)
    for ci in range(X.shape[1]):
        X[:, ci] = np.where(np.isnan(X[:, ci]), meds[ci], X[:, ci])

    pw = float((y == 0).sum() / max((y == 1).sum(), 1))
    model = xgb.XGBClassifier(
        n_estimators=300, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pw, eval_metric="logloss",
        use_label_encoder=False, random_state=42, n_jobs=-1,
    )
    model.fit(X, y)

    fi       = dict(zip(feat_cols, model.feature_importances_))
    fi_sorted = sorted(fi.items(), key=lambda x: x[1], reverse=True)

    artifact = {
        "model":        model,
        "feature_cols": feat_cols,
        "medians":      meds.tolist(),
        "trained_at":   datetime.utcnow().isoformat(),
        "n_samples":    int(len(df)),
        "fwd_days":     fwd_days,
        "pct_outperform": data_stats["pct_outperform"],
    }
    mp = _model_path(fwd_days)
    with open(mp, "wb") as f:
        pickle.dump(artifact, f)

    report = {
        "status":             "trained",
        "horizon":            f"{fwd_days}d",
        "model_path":         mp,
        "trained_at":         artifact["trained_at"],
        "data":               data_stats,
        "walk_forward":       wf,
        "feature_importance": {k: round(float(v), 4) for k, v in fi_sorted},
        "top_signals":        [k for k, _ in fi_sorted[:3]],
        "threshold_strong":   STRONG_THRESHOLD,
        "threshold_moderate": MODERATE_THRESHOLD,
        "interpretation": (
            "AUC > 0.55 = genuine edge. "
            f"{'60d model catches early-stage multi-month runners (MU/SNDK type). ' if fwd_days >= 60 else '10d model catches short-term momentum bursts. '}"
            "Precision = % of STRONG signals that actually outperformed SPY."
        ),
    }
    rp = _report_path(fwd_days)
    with open(rp, "w") as f:
        json.dump(report, f, indent=2, default=str)

    if progress_cb:
        progress_cb(f"=== Done [{label}]. Model saved → {os.path.basename(mp)}. "
                    f"Top signal: {fi_sorted[0][0]} ===")
    return report


# ─── SCORE A TICKER ──────────────────────────────────────────────────────────
def alpha_leaders_score(ticker: str, pick: dict = None, fwd_days: int = 10) -> dict:
    """
    Score a single ticker. fwd_days=10 → short-term model, fwd_days=60 → long-term.
    Builds features from current polygon_market_daily — no future data.
    """
    import psycopg2, pandas as pd

    mp = _model_path(fwd_days)
    if not os.path.exists(mp):
        # Fall back to other horizon if available
        other = 60 if fwd_days == 10 else 10
        fallback = _model_path(other)
        if os.path.exists(fallback):
            mp = fallback
            fwd_days = other
        else:
            return {
                "ticker": ticker, "signal": "NO_MODEL",
                "error": f"No model trained yet for {fwd_days}d. "
                         "Run POST /stock-api/admin/run-historical-alpha-train",
            }

    with open(mp, "rb") as f:
        art = pickle.load(f)
    model     = art["model"]
    feat_cols = art["feature_cols"]
    meds      = np.array(art["medians"], dtype=np.float32)
    horizon   = art.get("fwd_days", fwd_days)

    conn = psycopg2.connect(_DB_URL)
    try:
        cur = conn.cursor()
        limit = 70 if horizon >= 60 else 30
        cur.execute("""
            SELECT scan_date, close_price, gap_pct, rvol,
                   close_strength, range_pct, volume
            FROM polygon_market_daily
            WHERE ticker = %s AND close_price >= %s
            ORDER BY scan_date DESC LIMIT %s
        """, (ticker, MIN_PRICE, limit))
        rows = cur.fetchall()
        if not rows:
            return {"ticker": ticker, "signal": "NO_DATA",
                    "error": "No recent data in polygon_market_daily"}

        tdf = pd.DataFrame(rows, columns=[
            "scan_date", "close_price", "gap_pct", "rvol",
            "close_strength", "range_pct", "volume"
        ]).sort_values("scan_date")

        latest = tdf.iloc[-1]
        closes = pd.to_numeric(tdf["close_price"], errors="coerce").values
        vols   = pd.to_numeric(tdf["volume"],      errors="coerce").values

        def _safe_ret(n):
            if len(closes) >= n + 1 and closes[-n-1] > 0:
                return float(closes[-1] / closes[-n-1] - 1)
            return 0.0

        gap_pct        = float(latest["gap_pct"]        or 0)
        rvol           = float(latest["rvol"]            or 1.0)
        close_strength = float(latest["close_strength"]  or 0.5)
        range_pct      = float(latest["range_pct"]       or 0)
        momentum_5d    = _safe_ret(5)
        momentum_20d   = _safe_ret(20)
        momentum_60d   = _safe_ret(60)
        vol_vs_20d     = float(vols[-1] / np.mean(vols[:-1])) if len(vols) > 1 and np.mean(vols[:-1]) > 0 else 1.0
        price_vs_52wh  = float(closes[-1] / np.nanmax(closes)) if len(closes) > 0 else 1.0

        sector_sym = _sector_etf(ticker)
        cur.execute("""
            SELECT return_pct FROM sector_etf_daily
            WHERE etf_ticker = %s ORDER BY price_date DESC LIMIT 20
        """, (sector_sym,))
        etf_rets = [float(r[0] or 0) / 100 for r in cur.fetchall()]
        etf_ret_10d = sum(etf_rets[:10])  if len(etf_rets) >= 10 else sum(etf_rets)
        etf_ret_20d = sum(etf_rets[:20])  if len(etf_rets) >= 20 else sum(etf_rets)

        cur.execute("""
            SELECT return_pct FROM sector_etf_daily
            WHERE etf_ticker = 'SPY' ORDER BY price_date DESC LIMIT 20
        """)
        spy_rets  = [float(r[0] or 0) / 100 for r in cur.fetchall()]
        spy_ret_5d  = sum(spy_rets[:5])  if len(spy_rets) >= 5  else sum(spy_rets)
        spy_ret_20d = sum(spy_rets[:20]) if len(spy_rets) >= 20 else sum(spy_rets)

    finally:
        conn.close()

    if pick:
        if "rvol"    in pick: rvol    = float(pick["rvol"])
        if "gap_pct" in pick: gap_pct = float(pick["gap_pct"])

    sector_rs_10d = momentum_5d  - etf_ret_10d
    spy_rs_5d     = momentum_5d  - spy_ret_5d
    sector_rs_20d = momentum_20d - etf_ret_20d
    spy_rs_20d    = momentum_20d - spy_ret_20d

    feat_map = {
        "gap_pct":        gap_pct,
        "rvol":           rvol,
        "close_strength": close_strength,
        "range_pct":      range_pct,
        "momentum_5d":    momentum_5d,
        "momentum_20d":   momentum_20d,
        "vol_vs_20d_avg": vol_vs_20d,
        "price_vs_52wh":  price_vs_52wh,
        "sector_rs_10d":  sector_rs_10d,
        "spy_rs_5d":      spy_rs_5d,
        "momentum_60d":   momentum_60d,
        "sector_rs_20d":  sector_rs_20d,
        "spy_rs_20d":     spy_rs_20d,
    }

    X = np.array([[feat_map.get(c, 0.0) for c in feat_cols]], dtype=np.float32)
    for i in range(X.shape[1]):
        if np.isnan(X[0, i]):
            X[0, i] = meds[i]

    prob = float(model.predict_proba(X)[0][1])
    if prob >= STRONG_THRESHOLD:
        signal = "STRONG"
    elif prob >= MODERATE_THRESHOLD:
        signal = "MODERATE"
    else:
        signal = "WEAK"

    horizon_desc = {
        10: "next 2 weeks vs SPY",
        60: "next 3 months vs SPY (long-term runner signal)",
    }.get(horizon, f"next {horizon} trading days vs SPY")

    return {
        "ticker":      ticker,
        "alpha_prob":  round(prob, 3),
        "signal":      signal,
        "horizon":     f"{horizon}d",
        "trained_at":  art.get("trained_at", "unknown"),
        "features": {
            "gap_pct_%":         round(gap_pct, 2),
            "rvol":              round(rvol, 2),
            "close_strength":    round(close_strength, 3),
            "range_pct_%":       round(range_pct, 2),
            "momentum_5d_%":     round(momentum_5d  * 100, 2),
            "momentum_20d_%":    round(momentum_20d * 100, 2),
            "momentum_60d_%":    round(momentum_60d * 100, 2),
            "vol_vs_20d_avg":    round(vol_vs_20d, 2),
            "price_vs_52wh":     round(price_vs_52wh, 3),
            "sector_rs_10d_%":   round(sector_rs_10d * 100, 2),
            "sector_rs_20d_%":   round(sector_rs_20d * 100, 2),
            "spy_rs_5d_%":       round(spy_rs_5d  * 100, 2),
            "spy_rs_20d_%":      round(spy_rs_20d * 100, 2),
            "sector_etf":        sector_sym,
        },
        "interpretation": {
            "STRONG":   f"High probability of beating SPY over {horizon_desc}",
            "MODERATE": f"Moderate edge — worth watching alongside other signals",
            "WEAK":     f"Low alpha probability — SPY likely to match or beat over {horizon_desc}",
        }[signal],
    }


# ─── STATUS ──────────────────────────────────────────────────────────────────
def get_alpha_leaders_status() -> dict:
    out = {"models": {}}
    for days in [10, 60]:
        mp = _model_path(days)
        rp = _report_path(days)
        if not os.path.exists(mp):
            out["models"][f"{days}d"] = {"status": "not_trained"}
            continue
        with open(mp, "rb") as f:
            a = pickle.load(f)
        rep = {}
        if os.path.exists(rp):
            with open(rp) as f:
                rep = json.load(f)
        out["models"][f"{days}d"] = {
            "status":          "trained",
            "trained_at":      a.get("trained_at"),
            "n_samples":       a.get("n_samples"),
            "pct_outperform":  a.get("pct_outperform"),
            "feature_columns": a.get("feature_cols"),
            "walk_forward":    rep.get("walk_forward"),
            "feature_importance": rep.get("feature_importance"),
            "top_signals":     rep.get("top_signals"),
        }
    return out
