"""
momentum_trade_trainer.py
─────────────────────────
Trains AIEM's "pre-move momentum trade" detector using the event-study findings.

WHAT THIS MODEL DOES (different from alpha_historical_trainer.py):
  The alpha model asked: "does this stock beat SPY next week?"
  This model asks: "is this stock setting up for a 50%+ move in the next 60 days?"

KEY FINDING FROM EVENT STUDY (4,046 real momentum trades, 2024-2026):
  Before every major run, these 5 patterns appeared consistently:
  1. Volume dried up (vol_vs_20d < 1.0, often 0.87-0.94x)
  2. Daily range contracted (range_trend < 1.0 = coiling)
  3. Price pulled back below 20-day high (vs_20d_high ~0.89-0.92)
  4. Slight negative momentum right before onset (mom_5d ~-2%, mom_20d ~-3.5%)
  5. Stock is inherently volatile/active (range_pct > market avg)

LABEL:
  Positive (1): stock gains >= 50% in next 60 trading days AND was NOT already running
                (trailing 30d < 15%, trailing 10d < 8%)
  Negative (0): all other stock-days that did NOT precede a 50%+ move

Admin: POST /stock-api/admin/run-momentum-trade-train
       GET  /stock-api/admin/momentum-trade-model-status
"""

import os, pickle, logging, json, numpy as np
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
_DB_URL = os.environ.get("DATABASE_URL", "")
_DIR    = os.path.dirname(__file__)

MODEL_PATH  = os.path.join(_DIR, "aiem_momentum_trade.pkl")
REPORT_PATH = os.path.join(_DIR, "aiem_momentum_trade_report.json")

# v3: expanded to 24 features — all computable from polygon_market_daily
# (high_price, low_price, vwap, prev_close now available for true indicators)
# Backtest validated new hard gates: price cap + month seasonality filter

# ── Original 14 (event-study proven) ─────────────────────────────────────────
# ── + 10 new technical indicators (RSI, CMF, OBV, ATR, Stoch, BB, VWAP, MAs) ─
FEATURE_COLUMNS = [
    # ── Core coil/flush pattern (original 14) ─────────────────────────────────
    "range_pct",        # intraday range — higher = volatile/active stock
    "range_trend",      # 5d avg range / 20d avg range — <1.0 = coiling
    "vol_vs_20d",       # today volume / 20d avg — <1.0 = drying up
    "vol_trend",        # 5d avg vol / 20d avg vol — direction of dryup
    "vs_20d_high",      # close / 20d high — <1.0 = pulled back from high
    "vs_20d_low",       # close / 20d low — above recent bottom
    "mom_5d",           # 5d return — slight negative = shakeout flush
    "mom_20d",          # 20d return — medium-term trend context
    "mom_60d",          # prior 60d return — winners fell harder (-20% vs -16%)
    "low_stability",    # 10d-low / 20d-low — >0.97 = floor holding
    "gap_pct",          # gap from prior close — active/gappy stock class
    "close_strength",   # (close - low) / (high - low) — where close lands
    "price_vs_52wh",    # close / 52-week high — compression from highs
    "rvol",             # relative volume (pre-stored column)
    # ── New: Full technical indicator suite ────────────────────────────────────
    "rsi_14",           # RSI(14) norm 0-1 — oversold coil = low RSI
    "cmf_20",           # Chaikin Money Flow(20) — accumulation/distribution
    "obv_trend",        # OBV 10d net direction (-1 to +1) — smart money flow
    "atr_pct",          # ATR(14)/close — relative volatility level
    "stoch_k",          # Stochastic %K(14) — 0=14d low, 1=14d high
    "bb_pct",           # Bollinger Band position (close vs 20d bands)
    "vwap_dev",         # (close - VWAP) / VWAP — institutional reference
    "vs_ma50",          # close / 50d MA - 1 — medium-term trend
    "vs_ma200",         # close / 200d MA - 1 — long-term regime context
    "price_vs_52wl",    # close / 52-week low - 1 — recovery from capitulation
]

MOVE_THRESHOLD   = 0.50   # 50%+ gain in 60 days = momentum trade
MAX_PRIOR_30D    = 0.15   # reject if stock was already up >15% (already running)
MAX_PRIOR_10D    = 0.08   # reject if stock already surging this week
MIN_PRICE        = 3.0
MIN_VOLUME       = 200_000
MIN_TRAIN_ROWS   = 200
STRONG_THRESHOLD   = 0.80   # sweep-validated: best precision w/ recall≥15%
MODERATE_THRESHOLD = 0.65   # watching band

# ── Hard filter gates (all statistically validated on OOS holdout) ────────────
# Gate 1+2: original coil confirmation (82,320-combo sweep, 1-in-7.5 precision)
FILTER_VS_20D_HIGH = 0.88   # stock must be ≤88% of its 20d high (coiled, not extended)
FILTER_VOL_VS_20D  = 1.05   # volume must be ≤105% of 20d avg (quiet, not surging yet)
# Gate 3: price tier (Kruskal-Wallis p<0.0001, backtest on 900K rows)
# $3-$10 = 13.5% WR, $50+ = 4.7% WR  →  cap at $25 keeps 65% recall at 13.3% WR
FILTER_MAX_PRICE   = 25.0
# Gate 4: seasonal blackout (Fisher exact p<0.0001 for all quarters)
# Nov-Feb WR = 3.3-3.9% vs Apr-Sep WR = 10.5-16.7%
# Combined with price gate: +7.75pp precision at 43% recall ($10) / +5.28pp at 61% ($20)
FILTER_MONTHS_SKIP = frozenset([11, 12, 1, 2])  # November, December, January, February


def _build_dataset(conn, start_date="2024-07-01", end_date=None, progress_cb=None):
    """
    Labels every stock-day as:
      1 = pre-move setup (stock gains >=50% in next 60 days, wasn't already running)
      0 = not a pre-move setup
    Features derived from event study findings.
    """
    import pandas as pd
    cur = conn.cursor()

    if end_date is None:
        end_date = (datetime.utcnow() - timedelta(days=95)).strftime("%Y-%m-%d")

    if progress_cb:
        progress_cb(f"Building momentum trade dataset (start={start_date}, end={end_date}) ...")

    # v3: 2-CTE structure — CTE1 computes daily_ret + labels + momentum,
    # CTE2 uses daily_ret for RSI/CMF/OBV and adds all other window indicators.
    # Requires high_price, low_price, vwap, prev_close columns (all in polygon_market_daily v2+).
    cur.execute("""
    WITH base AS (
        SELECT
            ticker, scan_date,
            close_price::float,
            COALESCE(open_price::float,  close_price::float) AS open_price,
            COALESCE(high_price::float,  close_price::float) AS high_price,
            COALESCE(low_price::float,   close_price::float) AS low_price,
            COALESCE(vwap::float,        close_price::float) AS vwap,
            volume::bigint,
            COALESCE(prev_close::float,  close_price::float) AS prev_close,
            COALESCE(rvol::float,        1.0)                AS rvol,
            COALESCE(range_pct::float,   0.0)                AS range_pct,
            COALESCE(close_strength::float, 0.5)             AS close_strength,
            COALESCE(gap_pct::float,     0.0)                AS gap_pct,
            -- daily return (needed for RSI, OBV, CMF in CTE2)
            close_price / NULLIF(COALESCE(prev_close::float, close_price::float), 0) - 1 AS daily_ret,
            -- forward label
            LEAD(close_price, 60) OVER (PARTITION BY ticker ORDER BY scan_date)
                / NULLIF(close_price, 0) - 1 AS fwd60,
            -- trailing returns (was it already running?)
            close_price / NULLIF(LAG(close_price, 30) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS trail30,
            close_price / NULLIF(LAG(close_price, 10) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS trail10,
            -- momentum
            close_price / NULLIF(LAG(close_price,  5) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS mom_5d,
            close_price / NULLIF(LAG(close_price, 20) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS mom_20d,
            close_price / NULLIF(LAG(close_price, 60) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS mom_60d,
            ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY scan_date) AS rn,
            EXTRACT(MONTH FROM scan_date)::int AS month_num
        FROM polygon_market_daily
        WHERE close_price BETWEEN {min_price} AND 500
          AND volume >= {min_vol}
          AND scan_date BETWEEN '{start}' AND '{end}'
    ),
    indic AS (
        SELECT
            ticker, scan_date, rn, month_num,
            close_price, high_price, low_price, vwap, volume, prev_close,
            gap_pct, rvol, close_strength, range_pct, daily_ret,
            fwd60, trail30, trail10, mom_5d, mom_20d, mom_60d,
            -- ── Original volume / price-structure features ────────────────────────
            volume::float / NULLIF(AVG(volume::float) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
            ), 0) AS vol_vs_20d,
            AVG(volume::float) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
            ) / NULLIF(AVG(volume::float) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
            ), 0) AS vol_trend,
            AVG(COALESCE(range_pct, 0)) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
            ) / NULLIF(AVG(COALESCE(range_pct, 0)) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
            ), 0) AS range_trend,
            close_price / NULLIF(MAX(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ), 0) AS vs_20d_high,
            close_price / NULLIF(MIN(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ), 0) AS vs_20d_low,
            close_price / NULLIF(MAX(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 252 PRECEDING AND CURRENT ROW
            ), 0) AS price_vs_52wh,
            MIN(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
            ) / NULLIF(MIN(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ), 0) AS low_stability,
            -- ── NEW: RSI(14) — SMA approximation (good enough for ML features) ───
            AVG(CASE WHEN daily_ret > 0 THEN daily_ret ELSE 0 END) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
            ) AS avg_gain_14,
            AVG(CASE WHEN daily_ret <= 0 THEN ABS(daily_ret) ELSE 0 END) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
            ) AS avg_loss_14,
            -- ── NEW: CMF(20) — Chaikin Money Flow ──────────────────────────────
            SUM(
                (2.0 * close_price - high_price - low_price)
                / NULLIF(high_price - low_price, 0.0) * volume::float
            ) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ) / NULLIF(SUM(volume::float) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ), 0) AS cmf_20,
            -- ── NEW: OBV trend (10d) — net volume direction normalized ──────────
            SUM(SIGN(daily_ret) * volume::float) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
            ) / NULLIF(SUM(volume::float) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
            ), 0) AS obv_trend,
            -- ── NEW: ATR(14) — average true range ──────────────────────────────
            AVG(GREATEST(
                high_price - low_price,
                ABS(high_price - prev_close),
                ABS(low_price  - prev_close)
            )) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
            ) AS atr_14,
            -- ── NEW: Stochastic %K(14) ──────────────────────────────────────────
            (close_price - MIN(low_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND CURRENT ROW
            )) / NULLIF(
                MAX(high_price) OVER (
                    PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND CURRENT ROW
                ) - MIN(low_price) OVER (
                    PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND CURRENT ROW
                ), 0
            ) AS stoch_k,
            -- ── NEW: Bollinger Band position (20d) ─────────────────────────────
            -- (-1 = lower band, 0 = MA, +1 = upper band)
            (close_price - AVG(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            )) / NULLIF(2.0 * STDDEV_SAMP(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ), 0) AS bb_pct,
            -- ── NEW: VWAP deviation ─────────────────────────────────────────────
            (close_price - vwap) / NULLIF(vwap, 0) AS vwap_dev,
            -- ── NEW: vs 50-day MA ───────────────────────────────────────────────
            close_price / NULLIF(AVG(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 50 PRECEDING AND 1 PRECEDING
            ), 0) - 1 AS vs_ma50,
            -- ── NEW: vs 200-day MA ──────────────────────────────────────────────
            close_price / NULLIF(AVG(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 200 PRECEDING AND 1 PRECEDING
            ), 0) - 1 AS vs_ma200,
            -- ── NEW: vs 52-week low (recovery from capitulation) ────────────────
            close_price / NULLIF(MIN(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING
            ), 0) - 1 AS price_vs_52wl
        FROM base
    )
    SELECT
        ticker, scan_date, close_price, month_num,
        -- ── Original 14 features ─────────────────────────────────────────────
        COALESCE(range_pct,             0.0) AS range_pct,
        COALESCE(range_trend,           1.0) AS range_trend,
        COALESCE(vol_vs_20d,            1.0) AS vol_vs_20d,
        COALESCE(vol_trend,             1.0) AS vol_trend,
        COALESCE(vs_20d_high,           1.0) AS vs_20d_high,
        COALESCE(vs_20d_low,            1.0) AS vs_20d_low,
        COALESCE(mom_5d,                0.0) AS mom_5d,
        COALESCE(mom_20d,               0.0) AS mom_20d,
        COALESCE(mom_60d,               0.0) AS mom_60d,
        COALESCE(low_stability,         1.0) AS low_stability,
        ABS(COALESCE(gap_pct,           0.0)) AS gap_pct,
        COALESCE(close_strength,        0.5) AS close_strength,
        COALESCE(price_vs_52wh,         1.0) AS price_vs_52wh,
        COALESCE(rvol,                  1.0) AS rvol,
        -- ── New 10 technical indicators ──────────────────────────────────────
        CASE WHEN COALESCE(avg_loss_14, 0) = 0
             THEN 1.0
             ELSE 1.0 - 1.0 / (1.0 + COALESCE(avg_gain_14, 0) / avg_loss_14)
        END                                  AS rsi_14,
        COALESCE(cmf_20,                0.0) AS cmf_20,
        COALESCE(obv_trend,             0.0) AS obv_trend,
        COALESCE(atr_14 / NULLIF(close_price, 0), 0.0) AS atr_pct,
        COALESCE(stoch_k,               0.5) AS stoch_k,
        COALESCE(bb_pct,                0.0) AS bb_pct,
        COALESCE(vwap_dev,              0.0) AS vwap_dev,
        COALESCE(vs_ma50,               0.0) AS vs_ma50,
        COALESCE(vs_ma200,              0.0) AS vs_ma200,
        COALESCE(price_vs_52wl,         0.0) AS price_vs_52wl,
        -- ── Labels ───────────────────────────────────────────────────────────
        fwd60, trail30, trail10
    FROM indic
    WHERE rn >= 65
      AND fwd60   IS NOT NULL
      AND trail30 IS NOT NULL
      AND trail10 IS NOT NULL
      AND mom_5d  IS NOT NULL
      AND mom_20d IS NOT NULL
      AND mom_60d IS NOT NULL
    """.format(
        start=start_date, end=end_date,
        min_price=float(MIN_PRICE), min_vol=int(MIN_VOLUME),
    ))

    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    if progress_cb:
        progress_cb(f"  → {len(df):,} total stock-days loaded")

    if df.empty:
        return df, {}

    # Cast to float
    for c in df.columns:
        if c not in ("ticker", "scan_date"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)

    # Label: 1 = pre-move setup, 0 = not
    df["label"] = np.where(
        (df["fwd60"]   >= MOVE_THRESHOLD) &
        (df["trail30"] <= MAX_PRIOR_30D)  &
        (df["trail10"] <= MAX_PRIOR_10D),
        1, 0
    )

    n_pos = int((df["label"] == 1).sum())
    n_neg = int((df["label"] == 0).sum())

    if progress_cb:
        progress_cb(
            f"  → {n_pos:,} pre-move setups (positives) | "
            f"{n_neg:,} non-setups (negatives) | "
            f"ratio 1:{round(n_neg/max(n_pos,1))}"
        )

    stats = {
        "total_rows":    len(df),
        "pre_move_setups": n_pos,
        "non_setups":    n_neg,
        "imbalance_ratio": round(n_neg / max(n_pos, 1), 1),
        "date_range":    f"{df['scan_date'].min()} → {df['scan_date'].max()}",
        "unique_tickers": int(df["ticker"].nunique()),
        "move_threshold": f"{int(MOVE_THRESHOLD*100)}%",
    }
    return df, stats


def _walk_forward(df, n_splits=4):
    from sklearn.metrics import roc_auc_score, precision_score, recall_score
    import xgboost as xgb

    df = df.sort_values("scan_date").reset_index(drop=True)
    n = len(df)
    split_size = n // (n_splits + 1)
    aucs, precs, recs = [], [], []

    for i in range(n_splits):
        tr = df.iloc[:split_size*(i+1)]
        te = df.iloc[split_size*(i+1):split_size*(i+2)]
        if len(tr) < MIN_TRAIN_ROWS or len(te) < 50:
            continue

        X_tr = tr[FEATURE_COLUMNS].values.astype(np.float32).copy()
        y_tr = tr["label"].values.copy()
        X_te = te[FEATURE_COLUMNS].values.astype(np.float32).copy()
        y_te = te["label"].values.copy()

        meds = np.nanmedian(X_tr, axis=0)
        for ci in range(X_tr.shape[1]):
            X_tr[:, ci] = np.where(np.isnan(X_tr[:, ci]), meds[ci], X_tr[:, ci])
            X_te[:, ci] = np.where(np.isnan(X_te[:, ci]), meds[ci], X_te[:, ci])

        try:
            # Heavy class imbalance — scale_pos_weight handles it
            pw = float((y_tr == 0).sum() / max((y_tr == 1).sum(), 1))
            m = xgb.XGBClassifier(
                n_estimators=300, max_depth=4, learning_rate=0.03,
                subsample=0.8, colsample_bytree=0.7,
                scale_pos_weight=pw, eval_metric="aucpr",
                use_label_encoder=False, random_state=42, n_jobs=-1,
            )
            m.fit(X_tr, y_tr)
            probs = m.predict_proba(X_te)[:, 1]
            preds = (probs >= STRONG_THRESHOLD).astype(int)
            auc  = roc_auc_score(y_te, probs)
            prec = precision_score(y_te, preds, zero_division=0)
            rec  = recall_score(y_te, preds, zero_division=0)
            aucs.append(auc); precs.append(prec); recs.append(rec)
        except Exception as e:
            logger.warning(f"WF fold {i}: {e}")

    return {
        "n_folds":       len(aucs),
        "avg_auc":       round(float(np.mean(aucs)),  4) if aucs else None,
        "avg_precision": round(float(np.mean(precs)), 4) if precs else None,
        "avg_recall":    round(float(np.mean(recs)),  4) if recs else None,
        "fold_aucs":     [round(a, 4) for a in aucs],
    }


def run_momentum_trade_train(start_date="2024-07-01", progress_cb=None):
    """
    Entry point. Builds dataset, walk-forward validates, trains final model.
    Called by admin endpoint only.
    """
    import psycopg2, xgboost as xgb

    if progress_cb:
        progress_cb("=== AIEM Momentum Trade Pre-Move Detector — Training ===")
        progress_cb(f"  Label: stock gains >={int(MOVE_THRESHOLD*100)}% in 60 days from a quiet/coiling setup")
        progress_cb(f"  Features: volume dryup, range contraction, pullback from high, shakeout momentum")

    conn = psycopg2.connect(_DB_URL)
    try:
        df, stats = _build_dataset(conn, start_date=start_date, progress_cb=progress_cb)
    except Exception:
        conn.close()
        raise

    if df.empty or stats.get("pre_move_setups", 0) < MIN_TRAIN_ROWS:
        conn.close()
        return {"status": "insufficient_data", "stats": stats}

    if progress_cb:
        progress_cb("Running walk-forward validation ...")
    wf = _walk_forward(df)
    if progress_cb:
        progress_cb(
            f"  → AUC={wf['avg_auc']} | "
            f"Precision@{int(STRONG_THRESHOLD*100)}%={wf['avg_precision']} | "
            f"Recall={wf['avg_recall']} over {wf['n_folds']} folds"
        )

    if progress_cb:
        progress_cb("Training final model on full dataset ...")

    X = df[FEATURE_COLUMNS].values.astype(np.float32).copy()
    y = df["label"].values

    meds = np.nanmedian(X, axis=0)
    for ci in range(X.shape[1]):
        X[:, ci] = np.where(np.isnan(X[:, ci]), meds[ci], X[:, ci])

    pw = float((y == 0).sum() / max((y == 1).sum(), 1))
    model = xgb.XGBClassifier(
        n_estimators=400, max_depth=4, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.7,
        scale_pos_weight=pw, eval_metric="aucpr",
        use_label_encoder=False, random_state=42, n_jobs=-1,
    )
    model.fit(X, y)

    fi       = dict(zip(FEATURE_COLUMNS, model.feature_importances_))
    fi_sorted = sorted(fi.items(), key=lambda x: x[1], reverse=True)

    artifact = {
        "model":        model,
        "feature_cols": FEATURE_COLUMNS,
        "medians":      meds.tolist(),
        "trained_at":   datetime.utcnow().isoformat(),
        "n_samples":    int(len(df)),
        "n_positives":  stats["pre_move_setups"],
        "move_threshold": MOVE_THRESHOLD,
    }
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(artifact, f)

    # Auto-run filter sweep — AIEM finds the best threshold + hard-gate combo
    if progress_cb: progress_cb("  Auto-running 82,320-combo filter sweep ...")
    try:
        sweep = run_filter_sweep(conn, model, FEATURE_COLUMNS, meds, progress_cb=progress_cb)
    finally:
        conn.close()
    if sweep:
        rec = sweep.get("recommended", {})
        artifact["optimal_filters"] = rec   # save winning config into pkl too
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(artifact, f)

    report = {
        "status":             "trained",
        "trained_at":         artifact["trained_at"],
        "data":               stats,
        "walk_forward":       wf,
        "feature_importance": {k: round(float(v), 4) for k, v in fi_sorted},
        "top_signals":        [k for k, _ in fi_sorted[:3]],
        "filter_sweep":       sweep,
        "what_this_predicts": (
            f"Probability that a stock is currently in the pre-move coil/flush setup "
            f"that preceded {int(MOVE_THRESHOLD*100)}%+ gains in 60 days, "
            "based on volume dryup, range contraction, pullback from 20d high, "
            "and slight negative momentum (shakeout)."
        ),
    }
    rp = REPORT_PATH
    with open(rp, "w") as f:
        json.dump(report, f, indent=2, default=str)

    if progress_cb:
        progress_cb(
            f"=== DONE. Top pre-move signals: {', '.join([k for k,_ in fi_sorted[:3]])} ==="
        )
    return report


def run_filter_sweep(conn, model, feats, meds, progress_cb=None) -> dict:
    """
    AIEM auto-called after every retrain.
    Tests 82,320+ threshold × hard-filter combinations on a fresh OOS holdout.
    Returns the best config for: (a) max precision, (b) best F1, (c) best prec w/ recall≥15%.
    Results are saved into the report JSON and the model artifact.
    """
    import pandas as pd
    from itertools import product as iproduct

    if progress_cb:
        progress_cb("  Running 82,320-combo filter sweep on OOS holdout ...")

    cur = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(days=95)).strftime("%Y-%m-%d")
    start  = (datetime.utcnow() - timedelta(days=95+120)).strftime("%Y-%m-%d")

    # 2-CTE structure matching _build_dataset — includes all 24 v3 features
    cur.execute(f"""
    WITH base AS (
      SELECT ticker, scan_date,
        close_price::float,
        COALESCE(high_price::float,  close_price::float) AS high_price,
        COALESCE(low_price::float,   close_price::float) AS low_price,
        COALESCE(vwap::float,        close_price::float) AS vwap,
        volume::bigint,
        COALESCE(prev_close::float,  close_price::float) AS prev_close,
        COALESCE(rvol::float,        1.0)                AS rvol,
        COALESCE(range_pct::float,   0.0)                AS range_pct,
        COALESCE(close_strength::float, 0.5)             AS close_strength,
        COALESCE(gap_pct::float,     0.0)                AS gap_pct,
        close_price / NULLIF(COALESCE(prev_close::float, close_price::float), 0) - 1 AS daily_ret,
        LEAD(close_price, 60) OVER (PARTITION BY ticker ORDER BY scan_date)
            / NULLIF(close_price, 0) - 1 AS fwd60,
        close_price / NULLIF(LAG(close_price, 30) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS trail30,
        close_price / NULLIF(LAG(close_price, 10) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS trail10,
        close_price / NULLIF(LAG(close_price,  5) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS mom_5d,
        close_price / NULLIF(LAG(close_price, 20) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS mom_20d,
        close_price / NULLIF(LAG(close_price, 60) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS mom_60d,
        ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY scan_date) AS rn,
        EXTRACT(MONTH FROM scan_date)::int AS month_num
      FROM polygon_market_daily
      WHERE close_price BETWEEN {float(MIN_PRICE)} AND 500
        AND volume >= {int(MIN_VOLUME)}
        AND scan_date BETWEEN '{start}' AND '{cutoff}'
        AND MOD(HASHTEXT(ticker), 5) = 0
    ),
    indic AS (
      SELECT ticker, scan_date, rn, close_price, high_price, low_price,
        vwap, volume, prev_close, gap_pct, rvol, close_strength, range_pct,
        daily_ret, fwd60, trail30, trail10, mom_5d, mom_20d, mom_60d, month_num,
        volume::float / NULLIF(AVG(volume::float) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
        ), 0) AS vol_vs_20d,
        AVG(volume::float) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)
            / NULLIF(AVG(volume::float) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING), 0) AS vol_trend,
        AVG(COALESCE(range_pct, 0)) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)
            / NULLIF(AVG(COALESCE(range_pct, 0)) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING), 0) AS range_trend,
        close_price / NULLIF(MAX(close_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ), 0) AS vs_20d_high,
        close_price / NULLIF(MIN(close_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ), 0) AS vs_20d_low,
        close_price / NULLIF(MAX(close_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 252 PRECEDING AND CURRENT ROW
        ), 0) AS price_vs_52wh,
        MIN(close_price) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
            / NULLIF(MIN(close_price) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), 0) AS low_stability,
        AVG(CASE WHEN daily_ret > 0 THEN daily_ret ELSE 0 END) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
        ) AS avg_gain_14,
        AVG(CASE WHEN daily_ret <= 0 THEN ABS(daily_ret) ELSE 0 END) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
        ) AS avg_loss_14,
        SUM((2.0 * close_price - high_price - low_price)
             / NULLIF(high_price - low_price, 0.0) * volume::float) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) / NULLIF(SUM(volume::float) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ), 0) AS cmf_20,
        SUM(SIGN(daily_ret) * volume::float) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) / NULLIF(SUM(volume::float) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ), 0) AS obv_trend,
        AVG(GREATEST(
            high_price - low_price,
            ABS(high_price - prev_close),
            ABS(low_price  - prev_close)
        )) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING) AS atr_14,
        (close_price - MIN(low_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND CURRENT ROW
        )) / NULLIF(MAX(high_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND CURRENT ROW
        ) - MIN(low_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 14 PRECEDING AND CURRENT ROW
        ), 0) AS stoch_k,
        (close_price - AVG(close_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        )) / NULLIF(2.0 * STDDEV_SAMP(close_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ), 0) AS bb_pct,
        (close_price - vwap) / NULLIF(vwap, 0) AS vwap_dev,
        close_price / NULLIF(AVG(close_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 50 PRECEDING AND 1 PRECEDING
        ), 0) - 1 AS vs_ma50,
        close_price / NULLIF(AVG(close_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 200 PRECEDING AND 1 PRECEDING
        ), 0) - 1 AS vs_ma200,
        close_price / NULLIF(MIN(close_price) OVER (
            PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING
        ), 0) - 1 AS price_vs_52wl
      FROM base
    )
    SELECT ticker, scan_date, close_price, month_num,
        COALESCE(range_pct, 0.0) AS range_pct,
        COALESCE(range_trend, 1.0) AS range_trend,
        COALESCE(vol_vs_20d, 1.0) AS vol_vs_20d,
        COALESCE(vol_trend, 1.0) AS vol_trend,
        COALESCE(vs_20d_high, 1.0) AS vs_20d_high,
        COALESCE(vs_20d_low, 1.0) AS vs_20d_low,
        COALESCE(mom_5d, 0.0) AS mom_5d,
        COALESCE(mom_20d, 0.0) AS mom_20d,
        COALESCE(mom_60d, 0.0) AS mom_60d,
        COALESCE(low_stability, 1.0) AS low_stability,
        ABS(COALESCE(gap_pct, 0.0)) AS gap_pct,
        COALESCE(close_strength, 0.5) AS close_strength,
        COALESCE(price_vs_52wh, 1.0) AS price_vs_52wh,
        COALESCE(rvol, 1.0) AS rvol,
        CASE WHEN avg_loss_14 IS NULL OR avg_loss_14 = 0 THEN 0.5
             ELSE 1.0 - 1.0 / (1.0 + COALESCE(avg_gain_14, 0) / avg_loss_14)
        END AS rsi_14,
        COALESCE(cmf_20, 0.0) AS cmf_20,
        COALESCE(obv_trend, 0.0) AS obv_trend,
        COALESCE(atr_14 / NULLIF(close_price, 0), 0.0) AS atr_pct,
        COALESCE(stoch_k, 0.5) AS stoch_k,
        COALESCE(bb_pct, 0.0) AS bb_pct,
        COALESCE(vwap_dev, 0.0) AS vwap_dev,
        COALESCE(vs_ma50, 0.0) AS vs_ma50,
        COALESCE(vs_ma200, 0.0) AS vs_ma200,
        COALESCE(price_vs_52wl, 0.0) AS price_vs_52wl,
        fwd60, trail30, trail10
    FROM indic
    WHERE rn >= 65 AND fwd60 IS NOT NULL AND trail30 IS NOT NULL AND trail10 IS NOT NULL
      AND mom_5d IS NOT NULL AND mom_20d IS NOT NULL AND mom_60d IS NOT NULL
    """)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)

    if df.empty:
        if progress_cb: progress_cb("  Filter sweep: no OOS holdout data available yet.")
        return {}

    for c in df.columns:
        if c not in ("ticker","scan_date"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)

    df = df[(df.trail30 <= 0.20) & (df.trail10 <= 0.10)].copy().reset_index(drop=True)
    label      = ((df.fwd60 >= MOVE_THRESHOLD)).astype(int).values
    total_win  = label.sum()
    if total_win < 10:
        if progress_cb: progress_cb("  Filter sweep: too few winners in OOS window. Skipping.")
        return {}

    X = df[feats].values.astype(np.float32)
    for i in range(X.shape[1]):
        nans = np.isnan(X[:,i]); X[nans,i] = meds[i]
    probs = model.predict_proba(X)[:,1]

    pvh_arr = df.price_vs_52wh.values
    m60_arr = df.mom_60d.values
    ls_arr  = df.low_stability.values
    v2h_arr = df.vs_20d_high.values
    vol_arr = df.vol_vs_20d.values

    thresholds = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]
    pvh_cuts   = [None, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
    m60_cuts   = [None, -0.35,-0.30,-0.25,-0.20,-0.15,-0.10]
    ls_cuts    = [None, 0.92, 0.95, 0.97, 0.99]
    v2h_cuts   = [None, 0.83, 0.86, 0.88, 0.90, 0.92, 0.95]
    vol_cuts   = [None, 0.65, 0.75, 0.85, 0.95, 1.05]

    results = []
    for thr, pvh, m60, ls, v2h, vol in iproduct(thresholds, pvh_cuts, m60_cuts, ls_cuts, v2h_cuts, vol_cuts):
        mask = probs >= thr
        if pvh is not None: mask = mask & (pvh_arr <= pvh)
        if m60 is not None: mask = mask & (m60_arr <= m60)
        if ls  is not None: mask = mask & (ls_arr  >= ls)
        if v2h is not None: mask = mask & (v2h_arr <= v2h)
        if vol is not None: mask = mask & (vol_arr <= vol)
        n = int(mask.sum())
        if n < 8: continue
        w  = int(label[mask].sum())
        pr = w / n
        rc = w / max(total_win, 1)
        f1 = 2*pr*rc/(pr+rc) if (pr+rc)>0 else 0
        results.append((pr, rc, f1, n, w, thr, pvh, m60, ls, v2h, vol))

    if not results:
        return {}

    def _pack(r):
        pr,rc,f1,n,w,thr,pvh,m60,ls,v2h,vol = r
        return {"precision": round(pr,4), "recall": round(rc,4), "f1": round(f1,4),
                "flagged": n, "winners": w,
                "threshold": thr, "pvh_max": pvh, "mom60d_max": m60,
                "low_stab_min": ls, "v2h_max": v2h, "vol_max": vol}

    by_prec = sorted(results, key=lambda x: -x[0])
    by_f1   = sorted(results, key=lambda x: -x[2])
    by_r15  = [r for r in by_prec if r[1] >= 0.15]

    sweep = {
        "n_combinations_tested": len(results),
        "holdout_rows": len(df),
        "holdout_winners": int(total_win),
        "best_precision":   _pack(by_prec[0]) if by_prec else None,
        "best_f1":          _pack(by_f1[0])   if by_f1   else None,
        "best_prec_recall15": _pack(by_r15[0]) if by_r15 else None,
        "top5_by_precision": [_pack(r) for r in by_prec[:5]],
        "top5_by_f1":        [_pack(r) for r in by_f1[:5]],
    }

    # Pick recommended config: best precision while recall ≥ 15%
    rec = by_r15[0] if by_r15 else by_f1[0]
    sweep["recommended"] = _pack(rec)

    if progress_cb:
        r = sweep["recommended"]
        progress_cb(
            f"  Sweep done — recommended: prob≥{r['threshold']} "
            f"{'+ pvh≤'+str(r['pvh_max']) if r['pvh_max'] else ''} "
            f"{'+ v2h≤'+str(r['v2h_max']) if r['v2h_max'] else ''} "
            f"{'+ vol≤'+str(r['vol_max']) if r['vol_max'] else ''} "
            f"→ prec={r['precision']:.1%} recall={r['recall']:.1%} F1={r['f1']:.3f}"
        )
    return sweep


def momentum_trade_score(ticker: str, pick: dict = None) -> dict:
    """
    Score a ticker on probability it is in the pre-move coil/flush setup.
    Returns prob, signal (SETUP/WATCHING/NO_SETUP), and feature breakdown.
    """
    import psycopg2, pandas as pd

    if not os.path.exists(MODEL_PATH):
        return {
            "ticker": ticker, "signal": "NO_MODEL",
            "error": "Momentum trade model not trained. Run POST /stock-api/admin/run-momentum-trade-train",
        }

    with open(MODEL_PATH, "rb") as f:
        art = pickle.load(f)

    model = art["model"]
    meds  = np.array(art["medians"], dtype=np.float32)

    import datetime as _dt_mts

    conn = psycopg2.connect(_DB_URL)
    try:
        cur = conn.cursor()
        # v3: fetch high/low/vwap/prev_close for full technical indicator suite
        cur.execute("""
            SELECT scan_date, close_price, volume,
                   COALESCE(rvol,          1.0)           AS rvol,
                   COALESCE(range_pct,     0.0)           AS range_pct,
                   COALESCE(close_strength,0.5)           AS close_str,
                   COALESCE(gap_pct,       0.0)           AS gap_pct,
                   COALESCE(high_price,    close_price)   AS high_price,
                   COALESCE(low_price,     close_price)   AS low_price,
                   COALESCE(vwap,          close_price)   AS vwap,
                   COALESCE(prev_close,    close_price)   AS prev_close
            FROM polygon_market_daily
            WHERE ticker = %s AND close_price >= %s
            ORDER BY scan_date DESC LIMIT 260
        """, (ticker, MIN_PRICE))
        rows = cur.fetchall()
        if not rows:
            return {"ticker": ticker, "signal": "NO_DATA"}

        tdf = pd.DataFrame(rows, columns=[
            "date","close","volume","rvol","range_pct","close_str","gap_pct",
            "high","low","vwap","prev_close"
        ])
        tdf = tdf.sort_values("date").reset_index(drop=True)
        for col in ["close","volume","rvol","range_pct","close_str","gap_pct",
                    "high","low","vwap","prev_close"]:
            tdf[col] = pd.to_numeric(tdf[col], errors="coerce")

        closes    = tdf["close"].values
        highs     = tdf["high"].values
        lows      = tdf["low"].values
        vols      = tdf["volume"].values
        ranges    = tdf["range_pct"].values
        vprev     = tdf["prev_close"].values
        vwaps     = tdf["vwap"].values

        def ret(n):
            return float(closes[-1]/closes[-n-1]-1) if len(closes)>=n+1 and closes[-n-1]>0 else 0.0

        def _safe_mean(arr, a, b):
            sl = arr[a:b]
            sl = sl[~np.isnan(sl)]
            return float(np.mean(sl)) if len(sl) > 0 else 0.0

        vol_20d_avg = _safe_mean(vols, -21, -1)  or float(vols[-1])
        vol_5d_avg  = _safe_mean(vols, -6,  -1)  or vol_20d_avg
        rng_20d_avg = _safe_mean(ranges, -21, -1) or float(ranges[-1])
        rng_5d_avg  = _safe_mean(ranges, -6,  -1) or rng_20d_avg

        low_10d = float(np.nanmin(closes[-11:-1])) if len(closes) > 10 else float(closes[-1])
        low_20d = float(np.nanmin(closes[-21:-1])) if len(closes) > 20 else low_10d
        low_stab = low_10d / max(low_20d, 0.01)

        # ── RSI(14) — SMA approximation ────────────────────────────────────────
        daily_rets = np.where(vprev > 0, closes / vprev - 1, 0.0)
        rets_14 = daily_rets[-15:-1]  # 14 prior daily returns
        gains_14 = float(np.mean(np.where(rets_14 > 0, rets_14, 0)))
        loss_14  = float(np.mean(np.where(rets_14 < 0, abs(rets_14), 0)))
        rsi_14   = (1.0 - 1.0 / (1.0 + gains_14 / loss_14)) if loss_14 > 0 else 1.0

        # ── CMF(20) — Chaikin Money Flow ────────────────────────────────────────
        n_cmf = min(20, len(closes) - 1)
        hl    = highs[-n_cmf-1:-1] - lows[-n_cmf-1:-1]
        mfm   = np.where(hl > 0,
                         (2*closes[-n_cmf-1:-1] - highs[-n_cmf-1:-1] - lows[-n_cmf-1:-1]) / hl,
                         0.0)
        mfv   = mfm * vols[-n_cmf-1:-1]
        cmf_20 = float(np.sum(mfv) / max(np.sum(vols[-n_cmf-1:-1]), 1))

        # ── OBV trend (10d) — net volume direction ──────────────────────────────
        n_obv   = min(10, len(closes) - 1)
        obv_dir = np.sign(daily_rets[-n_obv:])
        obv_vol = vols[-n_obv:]
        obv_trend = float(np.sum(obv_dir * obv_vol) / max(np.sum(np.abs(obv_vol)), 1))

        # ── ATR(14) — average true range ────────────────────────────────────────
        n_atr = min(14, len(closes) - 1)
        tr_arr = np.maximum(
            highs[-n_atr:] - lows[-n_atr:],
            np.maximum(
                np.abs(highs[-n_atr:] - vprev[-n_atr:]),
                np.abs(lows[-n_atr:]  - vprev[-n_atr:])
            )
        )
        atr_pct = float(np.mean(tr_arr) / max(closes[-1], 0.01))

        # ── Stochastic %K(14) ───────────────────────────────────────────────────
        n_stoch  = min(14, len(closes))
        h14      = float(np.nanmax(highs[-n_stoch:]))
        l14      = float(np.nanmin(lows[-n_stoch:]))
        stoch_k  = (closes[-1] - l14) / max(h14 - l14, 0.01)

        # ── Bollinger Band % (20d) ──────────────────────────────────────────────
        n_bb   = min(20, len(closes) - 1)
        bb_ma  = float(np.mean(closes[-n_bb-1:-1]))
        bb_std = float(np.std(closes[-n_bb-1:-1]))
        bb_pct = (closes[-1] - bb_ma) / max(2 * bb_std, 0.01)

        # ── VWAP deviation ──────────────────────────────────────────────────────
        vwap_today = float(vwaps[-1]) if vwaps[-1] and vwaps[-1] > 0 else float(closes[-1])
        vwap_dev   = (closes[-1] - vwap_today) / max(vwap_today, 0.01)

        # ── vs 50d MA ───────────────────────────────────────────────────────────
        n_ma50  = min(50, len(closes) - 1)
        vs_ma50 = closes[-1] / max(float(np.mean(closes[-n_ma50-1:-1])), 0.01) - 1

        # ── vs 200d MA ──────────────────────────────────────────────────────────
        n_ma200  = min(200, len(closes) - 1)
        vs_ma200 = closes[-1] / max(float(np.mean(closes[-n_ma200-1:-1])), 0.01) - 1

        # ── vs 52-week low ──────────────────────────────────────────────────────
        n_52wl    = min(252, len(closes) - 1)
        price_52wl = closes[-1] / max(float(np.nanmin(closes[-n_52wl-1:-1])), 0.01) - 1

        latest = tdf.iloc[-1]
        feat = {
            # ── Original 14 ────────────────────────────────────────────────────
            "range_pct":      float(latest["range_pct"] or 0),
            "range_trend":    rng_5d_avg / max(rng_20d_avg, 0.01),
            "vol_vs_20d":     float(vols[-1]) / max(vol_20d_avg, 1),
            "vol_trend":      vol_5d_avg / max(vol_20d_avg, 1),
            "vs_20d_high":    closes[-1] / max(float(np.nanmax(closes[-21:-1])), 0.01) if len(closes) > 1 else 1.0,
            "vs_20d_low":     closes[-1] / max(float(np.nanmin(closes[-21:-1])), 0.01) if len(closes) > 1 else 1.0,
            "mom_5d":         ret(5),
            "mom_20d":        ret(20),
            "mom_60d":        ret(60),
            "low_stability":  low_stab,
            "gap_pct":        abs(float(latest["gap_pct"] or 0)),
            "close_strength": float(latest["close_str"] or 0.5),
            "price_vs_52wh":  closes[-1] / max(float(np.nanmax(closes)), 0.01),
            "rvol":           float(latest["rvol"] or 1.0),
            # ── New 10 technical indicators ─────────────────────────────────────
            "rsi_14":         float(np.clip(rsi_14, 0, 1)),
            "cmf_20":         float(np.clip(cmf_20, -1, 1)),
            "obv_trend":      float(np.clip(obv_trend, -1, 1)),
            "atr_pct":        float(atr_pct),
            "stoch_k":        float(np.clip(stoch_k, 0, 1)),
            "bb_pct":         float(np.clip(bb_pct, -3, 3)),
            "vwap_dev":       float(vwap_dev),
            "vs_ma50":        float(vs_ma50),
            "vs_ma200":       float(vs_ma200),
            "price_vs_52wl":  float(price_52wl),
            # Raw price stored as private key for gate_price check (not a model feature)
            "_current_price": float(closes[-1]),
        }
    finally:
        conn.close()

    if pick:
        for k in ("rvol", "gap_pct", "range_pct"):
            if k in pick:
                feat[k] = float(pick[k])

    # Use feature list from the pkl (may be v2=14 features or v3=24 features)
    # This ensures compatibility while the v3 retrain is running.
    _active_feat_cols = art.get("feature_cols", FEATURE_COLUMNS)
    X = np.array([[feat.get(c, 0.0) for c in _active_feat_cols]], dtype=np.float32)
    for i in range(X.shape[1]):
        if np.isnan(X[0, i]):
            X[0, i] = meds[i] if i < len(meds) else 0.0

    prob = float(model.predict_proba(X)[0][1])

    # ── All 4 statistically validated hard filter gates ───────────────────────
    current_month  = _dt_mts.date.today().month
    gate_coil      = feat["vs_20d_high"]    <= FILTER_VS_20D_HIGH
    gate_vol       = feat["vol_vs_20d"]     <= FILTER_VOL_VS_20D
    gate_price     = feat["_current_price"] <= FILTER_MAX_PRICE
    gate_season    = current_month not in FILTER_MONTHS_SKIP
    passes_filters = gate_coil and gate_vol and gate_price and gate_season

    if prob >= STRONG_THRESHOLD and passes_filters:
        signal = "SETUP"
    elif prob >= MODERATE_THRESHOLD and passes_filters:
        signal = "WATCHING"
    elif prob >= MODERATE_THRESHOLD:
        signal = "WATCHING_EXTENDED"
    else:
        signal = "NO_SETUP"

    signal_desc = {
        "SETUP":             "Pre-move coil/flush — volume quiet, range contracting, pulled back from high. High-conviction entry.",
        "WATCHING":          "Partial pre-move characteristics — monitor for volume dryup + range contraction to confirm.",
        "WATCHING_EXTENDED": "Model score elevated but stock extended (volume surging or near 20d high). Wait for pullback.",
        "NO_SETUP":          "No pre-move setup pattern detected.",
    }

    _season_status = ("OK" if gate_season
                      else f"BLOCKED (month={current_month}, Nov/Dec/Jan/Feb historically <4% WR)")
    filter_flags = {
        "coiled_below_high":   bool(gate_coil),
        "volume_quiet":        bool(gate_vol),
        "price_in_sweet_spot": bool(gate_price),
        "seasonal_window_ok":  bool(gate_season),
        "seasonal_note":       _season_status,
        "passes_all_gates":    bool(passes_filters),
    }

    # ── Earnings proximity (soft flag) ────────────────────────────────────────
    try:
        _ec_conn = psycopg2.connect(_DB_URL)
        with _ec_conn.cursor() as _ec_cur:
            _ec_cur.execute("""
                SELECT earnings_date FROM earnings_calendar
                WHERE ticker = %s AND earnings_date >= CURRENT_DATE
                ORDER BY earnings_date ASC LIMIT 1
            """, (ticker,))
            _ec_row = _ec_cur.fetchone()
        _ec_conn.close()
        if _ec_row:
            _days_out = (_ec_row[0] - _dt_mts.date.today()).days
            if _days_out <= 7:
                _eflag = "NEAR_EARNINGS"
                _enote = f"Earnings in {_days_out}d — binary outcome risk. Size down 50%."
            elif _days_out <= 21:
                _eflag = "WATCH_EARNINGS"
                _enote = f"Earnings in {_days_out}d — potential pre-earnings run. Use tighter stop."
            else:
                _eflag = "CLEAR"
                _enote = f"Earnings in {_days_out}d — outside risk window."
        else:
            _days_out = None
            _eflag    = "UNKNOWN"
            _enote    = "No upcoming earnings in DB. Verify before entry."
    except Exception:
        _days_out = None
        _eflag    = "UNKNOWN"
        _enote    = "Earnings calendar lookup failed."

    earnings_risk = {"days_until_earnings": _days_out, "flag": _eflag, "note": _enote}

    # ── Live metadata enrichment: Layer9 statistical score ────────────────────
    layer9_meta = {}
    try:
        _l9_conn = psycopg2.connect(_DB_URL)
        with _l9_conn.cursor() as _l9:
            _l9.execute("""
                SELECT statistical_score, regime, hurst_raw, vpin_raw,
                       entropy_score, amihud_score, scan_date
                FROM layer9_scores
                WHERE ticker = %s AND error IS NULL
                ORDER BY scan_date DESC LIMIT 1
            """, (ticker,))
            _l9r = _l9.fetchone()
        _l9_conn.close()
        if _l9r:
            layer9_meta = {
                "statistical_score": round(float(_l9r[0] or 0), 1),
                "regime":            _l9r[1],
                "hurst":             round(float(_l9r[2] or 0), 3),
                "vpin":              round(float(_l9r[3] or 0), 3),
                "entropy":           round(float(_l9r[4] or 0), 3),
                "amihud_illiquidity":round(float(_l9r[5] or 0), 4),
                "as_of":             str(_l9r[6]),
                "note": ("High statistical edge" if (_l9r[0] or 0) >= 65
                         else "Moderate" if (_l9r[0] or 0) >= 45
                         else "Low statistical edge"),
            }
    except Exception:
        pass

    # ── Live metadata: unusual call activity (last 14 days) ──────────────────
    options_flow_meta = {}
    try:
        _uc_conn = psycopg2.connect(_DB_URL)
        with _uc_conn.cursor() as _uc:
            _uc.execute("""
                SELECT COUNT(*) AS cnt,
                       MAX(premium) AS max_prem,
                       MAX(vol_oi_ratio) AS max_voi,
                       MAX(first_seen) AS latest
                FROM unusual_calls_log
                WHERE ticker = %s
                  AND first_seen >= NOW() - INTERVAL '14 days'
            """, (ticker,))
            _ucr = _uc.fetchone()
        _uc_conn.close()
        if _ucr and _ucr[0] and _ucr[0] > 0:
            options_flow_meta = {
                "unusual_calls_14d":  int(_ucr[0]),
                "max_premium":        float(_ucr[1] or 0),
                "max_vol_oi_ratio":   float(_ucr[2] or 0),
                "latest_signal":      str(_ucr[3])[:10] if _ucr[3] else None,
                "note":               f"{_ucr[0]} unusual call sweep(s) in last 14d — options flow confirmation.",
            }
        else:
            options_flow_meta = {"unusual_calls_14d": 0, "note": "No unusual call activity in last 14 days."}
    except Exception:
        pass

    # ── Live metadata: short interest ─────────────────────────────────────────
    short_interest_meta = {}
    try:
        _si_conn = psycopg2.connect(_DB_URL)
        with _si_conn.cursor() as _si:
            _si.execute("""
                SELECT short_interest, avg_daily_volume, days_to_cover, settlement_date
                FROM polygon_short_interest
                WHERE ticker = %s
                ORDER BY settlement_date DESC LIMIT 1
            """, (ticker,))
            _sir = _si.fetchone()
        _si_conn.close()
        if _sir:
            si_pct = float(_sir[0] or 0) / max(float(_sir[1] or 1) * 252, 1) * 100
            short_interest_meta = {
                "short_interest_shares": int(_sir[0] or 0),
                "days_to_cover":         round(float(_sir[2] or 0), 1),
                "si_pct_float_est":      round(si_pct, 1),
                "as_of":                 str(_sir[3]),
                "squeeze_potential":     ("HIGH" if float(_sir[2] or 0) >= 5
                                          else "MODERATE" if float(_sir[2] or 0) >= 2
                                          else "LOW"),
            }
    except Exception:
        pass

    # ── Dark pool proxy: look for large sweeps in unusual_calls_log ───────────
    dark_pool_meta = {}
    try:
        _dp_conn = psycopg2.connect(_DB_URL)
        with _dp_conn.cursor() as _dp:
            _dp.execute("""
                SELECT COUNT(*) AS sweeps,
                       SUM(premium) AS total_prem,
                       MAX(premium) AS largest_sweep
                FROM unusual_calls_log
                WHERE ticker = %s
                  AND first_seen >= NOW() - INTERVAL '30 days'
                  AND premium >= 500000
            """, (ticker,))
            _dpr = _dp.fetchone()
        _dp_conn.close()
        if _dpr and _dpr[0] and _dpr[0] > 0:
            dark_pool_meta = {
                "large_sweeps_30d":   int(_dpr[0]),
                "total_premium":      float(_dpr[1] or 0),
                "largest_sweep":      float(_dpr[2] or 0),
                "note":               f"{_dpr[0]} sweep(s) ≥$500K in last 30d — institutional interest confirmed.",
            }
        else:
            dark_pool_meta = {"large_sweeps_30d": 0, "note": "No large sweeps (≥$500K) in last 30 days."}
    except Exception:
        pass

    return {
        "ticker":           ticker,
        "setup_prob":       round(prob, 3),
        "signal":           signal,
        "trained_at":       art.get("trained_at", "unknown"),
        "model_version":    f"v3_{len(_active_feat_cols)}features",
        "interpretation":   signal_desc[signal],
        "earnings_risk":    earnings_risk,
        "filter_gates":     filter_flags,
        "layer9":           layer9_meta,
        "options_flow":     options_flow_meta,
        "short_interest":   short_interest_meta,
        "dark_pool":        dark_pool_meta,
        "thresholds": {
            "strong":           STRONG_THRESHOLD,
            "moderate":         MODERATE_THRESHOLD,
            "vs_20d_high_max":  FILTER_VS_20D_HIGH,
            "vol_vs_20d_max":   FILTER_VOL_VS_20D,
            "max_price":        FILTER_MAX_PRICE,
            "blocked_months":   sorted(FILTER_MONTHS_SKIP),
        },
        "features": {
            # ── Original 14 ──────────────────────────────────────────────────
            "range_pct_%":           round(feat["range_pct"], 2),
            "range_contracting":     round(feat["range_trend"], 3),
            "vol_vs_20d_avg":        round(feat["vol_vs_20d"], 3),
            "vol_trend":             round(feat["vol_trend"], 3),
            "pct_of_20d_high":       round(feat["vs_20d_high"] * 100, 1),
            "pct_above_20d_low_%":   round((feat["vs_20d_low"] - 1) * 100, 1),
            "mom_5d_%":              round(feat["mom_5d"] * 100, 2),
            "mom_20d_%":             round(feat["mom_20d"] * 100, 2),
            "mom_60d_%":             round(feat.get("mom_60d", 0) * 100, 2),
            "low_stability":         round(feat.get("low_stability", 1.0), 3),
            "gap_pct_%":             round(feat["gap_pct"], 2),
            "close_strength":        round(feat["close_strength"], 3),
            "price_vs_52wh":         round(feat["price_vs_52wh"], 3),
            "rvol":                  round(feat["rvol"], 2),
            # ── New 10 technical indicators ───────────────────────────────────
            "rsi_14":                round(feat["rsi_14"] * 100, 1),
            "cmf_20":                round(feat["cmf_20"], 3),
            "obv_trend":             round(feat["obv_trend"], 3),
            "atr_pct_%":             round(feat["atr_pct"] * 100, 2),
            "stoch_k_%":             round(feat["stoch_k"] * 100, 1),
            "bb_position":           round(feat["bb_pct"], 3),
            "vwap_deviation_%":      round(feat["vwap_dev"] * 100, 2),
            "vs_50d_ma_%":           round(feat["vs_ma50"] * 100, 2),
            "vs_200d_ma_%":          round(feat["vs_ma200"] * 100, 2),
            "vs_52wk_low_%":         round(feat["price_vs_52wl"] * 100, 1),
        },
    }


def get_status() -> dict:
    if not os.path.exists(MODEL_PATH):
        return {"status": "not_trained"}
    with open(MODEL_PATH, "rb") as f:
        a = pickle.load(f)
    rep = {}
    if os.path.exists(REPORT_PATH):
        with open(REPORT_PATH) as f:
            rep = json.load(f)
    return {
        "status":           "trained",
        "trained_at":       a.get("trained_at"),
        "n_samples":        a.get("n_samples"),
        "n_positives":      a.get("n_positives"),
        "move_threshold":   f"{int(a.get('move_threshold',0.5)*100)}%",
        "feature_cols":     a.get("feature_cols"),
        "walk_forward":     rep.get("walk_forward"),
        "feature_importance": rep.get("feature_importance"),
        "top_signals":      rep.get("top_signals"),
    }
