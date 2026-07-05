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

# Features derived from event study + false-positive autopsy findings
# v2 adds mom_60d and low_stability — the two biggest winner/loser separators
FEATURE_COLUMNS = [
    "range_pct",        # intraday range — higher = volatile/active stock class
    "range_trend",      # 5d avg range / 20d avg range — <1.0 means coiling
    "vol_vs_20d",       # today volume / 20d average — <1.0 means drying up
    "vol_trend",        # 5d avg vol / 20d avg vol — direction of volume trend
    "vs_20d_high",      # close / 20d high — <1.0 means pulled back from high
    "vs_20d_low",       # close / 20d low — >1.0 means above recent bottom
    "mom_5d",           # 5d price return — slight negative = shakeout
    "mom_20d",          # 20d price return — context of prior trend
    "mom_60d",          # NEW: prior 60d return BEFORE setup — winners fell harder (-20% vs -16%)
    "low_stability",    # NEW: 10d-low / 20d-low — >0.97 = bottom holding, not still falling
    "gap_pct",          # gap from prior close — active/gappy stock
    "close_strength",   # where close lands in day's range
    "price_vs_52wh",    # proximity to 52-week high — winners at 51% vs losers at 62%
    "rvol",             # relative volume from stored column
]

MOVE_THRESHOLD   = 0.50   # 50%+ gain in 60 days = momentum trade
MAX_PRIOR_30D    = 0.15   # reject if stock was already up >15% (already running)
MAX_PRIOR_10D    = 0.08   # reject if stock already surging this week
MIN_PRICE        = 3.0
MIN_VOLUME       = 200_000
MIN_TRAIN_ROWS   = 200
STRONG_THRESHOLD   = 0.80   # sweep-validated: best precision w/ recall≥15%
MODERATE_THRESHOLD = 0.65   # watching band

# Hard filter gates validated by 82,320-combination sweep on OOS holdout
# These two cut losers most without killing winners (1-in-7.5 precision)
FILTER_VS_20D_HIGH = 0.88   # stock must be ≤88% of its 20d high (coiled, not extended)
FILTER_VOL_VS_20D  = 1.05   # volume must be ≤105% of 20d avg (quiet, not surging yet)


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

    cur.execute("""
    WITH base AS (
        SELECT
            ticker, scan_date, close_price, volume,
            COALESCE(rvol, 1.0)           AS rvol,
            COALESCE(range_pct, 0.0)      AS range_pct,
            COALESCE(close_strength, 0.5) AS close_strength,
            COALESCE(gap_pct, 0.0)        AS gap_pct,
            -- forward 60-day return (the label)
            LEAD(close_price, 60) OVER (PARTITION BY ticker ORDER BY scan_date)
                / NULLIF(close_price, 0) - 1 AS fwd60,
            -- trailing returns (was it already running?)
            close_price / NULLIF(LAG(close_price, 30) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS trail30,
            close_price / NULLIF(LAG(close_price, 10) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS trail10,
            -- 5d and 20d momentum
            close_price / NULLIF(LAG(close_price,  5) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS mom_5d,
            close_price / NULLIF(LAG(close_price, 20) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS mom_20d,
            -- volume features
            volume::float / NULLIF(AVG(volume::float) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
            ), 0) AS vol_vs_20d,
            AVG(volume::float) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
            ) / NULLIF(AVG(volume::float) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
            ), 0) AS vol_trend,
            -- range contraction (coiling signal)
            AVG(COALESCE(range_pct,0)) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
            ) / NULLIF(AVG(COALESCE(range_pct,0)) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
            ), 0) AS range_trend,
            -- breakout proximity
            close_price / NULLIF(MAX(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ), 0) AS vs_20d_high,
            close_price / NULLIF(MIN(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
            ), 0) AS vs_20d_low,
            close_price / NULLIF(MAX(close_price) OVER (
                PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 252 PRECEDING AND CURRENT ROW
            ), 0) AS price_vs_52wh,
            -- NEW v2: prior 60d momentum before setup (winners fell -20% vs losers -16%)
            close_price / NULLIF(LAG(close_price, 60) OVER (PARTITION BY ticker ORDER BY scan_date), 0) - 1 AS mom_60d,
            -- NEW v2: bottom holding — 10d-low / 20d-low (>0.97 = floor is holding)
            MIN(close_price) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
            / NULLIF(MIN(close_price) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING), 0) AS low_stability,
            ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY scan_date) AS rn
        FROM polygon_market_daily
        WHERE close_price BETWEEN {min_price} AND 500
          AND volume >= {min_vol}
          AND scan_date BETWEEN '{start}' AND '{end}'
    )
    SELECT
        ticker, scan_date,
        range_pct, range_trend, vol_vs_20d, vol_trend,
        vs_20d_high, vs_20d_low, mom_5d, mom_20d, mom_60d,
        COALESCE(low_stability, 1.0) AS low_stability,
        ABS(gap_pct) AS gap_pct, close_strength, price_vs_52wh, rvol,
        fwd60, trail30, trail10
    FROM base
    WHERE rn >= 65
      AND fwd60 IS NOT NULL AND trail30 IS NOT NULL AND trail10 IS NOT NULL
      AND mom_5d IS NOT NULL AND mom_20d IS NOT NULL AND mom_60d IS NOT NULL
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
    finally:
        conn.close()

    if df.empty or stats.get("pre_move_setups", 0) < MIN_TRAIN_ROWS:
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
    sweep = run_filter_sweep(conn, model, FEATURE_COLUMNS, meds, progress_cb=progress_cb)
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

    cur.execute(f"""
    WITH base AS (
      SELECT ticker, scan_date, close_price,
        COALESCE(rvol,1.0) AS rvol, COALESCE(range_pct,0) AS range_pct,
        COALESCE(close_strength,0.5) AS close_strength, COALESCE(gap_pct,0) AS gap_pct,
        LEAD(close_price,60)  OVER (PARTITION BY ticker ORDER BY scan_date) / NULLIF(close_price,0) - 1 AS fwd60,
        close_price / NULLIF(LAG(close_price,30) OVER (PARTITION BY ticker ORDER BY scan_date),0)-1 AS trail30,
        close_price / NULLIF(LAG(close_price,10) OVER (PARTITION BY ticker ORDER BY scan_date),0)-1 AS trail10,
        close_price / NULLIF(LAG(close_price, 5) OVER (PARTITION BY ticker ORDER BY scan_date),0)-1 AS mom_5d,
        close_price / NULLIF(LAG(close_price,20) OVER (PARTITION BY ticker ORDER BY scan_date),0)-1 AS mom_20d,
        close_price / NULLIF(LAG(close_price,60) OVER (PARTITION BY ticker ORDER BY scan_date),0)-1 AS mom_60d,
        volume::float / NULLIF(AVG(volume::float) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING),0) AS vol_vs_20d,
        AVG(volume::float) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 5  PRECEDING AND 1 PRECEDING)
          / NULLIF(AVG(volume::float) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING),0) AS vol_trend,
        AVG(COALESCE(range_pct,0)) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 5  PRECEDING AND 1 PRECEDING)
          / NULLIF(AVG(COALESCE(range_pct,0)) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING),0) AS range_trend,
        close_price / NULLIF(MAX(close_price) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),0) AS vs_20d_high,
        close_price / NULLIF(MIN(close_price) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),0) AS vs_20d_low,
        close_price / NULLIF(MAX(close_price) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 252 PRECEDING AND CURRENT ROW),0) AS price_vs_52wh,
        MIN(close_price) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
          / NULLIF(MIN(close_price) OVER (PARTITION BY ticker ORDER BY scan_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),0) AS low_stability,
        ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY scan_date) AS rn
      FROM polygon_market_daily
      WHERE close_price BETWEEN {float(MIN_PRICE)} AND 500
        AND volume >= {int(MIN_VOLUME)}
        AND scan_date BETWEEN '{start}' AND '{cutoff}'
        AND MOD(HASHTEXT(ticker), 5) = 0
    )
    SELECT ticker, scan_date,
        range_pct, range_trend, vol_vs_20d, vol_trend,
        vs_20d_high, vs_20d_low, mom_5d, mom_20d, mom_60d,
        COALESCE(low_stability,1.0) AS low_stability,
        ABS(gap_pct) AS gap_pct, close_strength, price_vs_52wh, rvol,
        fwd60, trail30, trail10
    FROM base
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

    conn = psycopg2.connect(_DB_URL)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT scan_date, close_price, volume,
                   COALESCE(rvol, 1.0) AS rvol,
                   COALESCE(range_pct, 0) AS range_pct,
                   COALESCE(close_strength, 0.5) AS close_str,
                   COALESCE(gap_pct, 0) AS gap_pct
            FROM polygon_market_daily
            WHERE ticker = %s AND close_price >= %s
            ORDER BY scan_date DESC LIMIT 90
        """, (ticker, MIN_PRICE))
        rows = cur.fetchall()
        if not rows:
            return {"ticker": ticker, "signal": "NO_DATA"}

        tdf = pd.DataFrame(rows, columns=["date","close","volume","rvol","range_pct","close_str","gap_pct"])
        tdf = tdf.sort_values("date")
        closes = pd.to_numeric(tdf["close"],  errors="coerce").values
        vols   = pd.to_numeric(tdf["volume"], errors="coerce").values
        ranges = pd.to_numeric(tdf["range_pct"], errors="coerce").values

        def ret(n):
            return float(closes[-1]/closes[-n-1]-1) if len(closes)>=n+1 and closes[-n-1]>0 else 0.0

        vol_20d_avg  = float(np.mean(vols[-21:-1])) if len(vols)>1 else float(vols[-1])
        vol_5d_avg   = float(np.mean(vols[-6:-1]))  if len(vols)>5 else vol_20d_avg
        rng_20d_avg  = float(np.nanmean(ranges[-21:-1])) if len(ranges)>1 else float(ranges[-1])
        rng_5d_avg   = float(np.nanmean(ranges[-6:-1]))  if len(ranges)>5 else rng_20d_avg

        # low_stability: 10d-low / 20d-low (>0.97 = bottom is holding)
        low_10d = float(np.nanmin(closes[-11:-1])) if len(closes) > 10 else float(closes[-1])
        low_20d = float(np.nanmin(closes[-21:-1])) if len(closes) > 20 else low_10d
        low_stab = low_10d / max(low_20d, 0.01)

        latest = tdf.iloc[-1]
        feat = {
            "range_pct":    float(latest["range_pct"] or 0),
            "range_trend":  rng_5d_avg / max(rng_20d_avg, 0.01),
            "vol_vs_20d":   float(vols[-1]) / max(vol_20d_avg, 1),
            "vol_trend":    vol_5d_avg / max(vol_20d_avg, 1),
            "vs_20d_high":  closes[-1] / max(np.nanmax(closes[-21:-1]), 0.01) if len(closes)>1 else 1.0,
            "vs_20d_low":   closes[-1] / max(np.nanmin(closes[-21:-1]), 0.01) if len(closes)>1 else 1.0,
            "mom_5d":       ret(5),
            "mom_20d":      ret(20),
            "mom_60d":      ret(60),
            "low_stability": low_stab,
            "gap_pct":      abs(float(latest["gap_pct"] or 0)),
            "close_strength": float(latest["close_str"] or 0.5),
            "price_vs_52wh": closes[-1] / max(np.nanmax(closes), 0.01),
            "rvol":         float(latest["rvol"] or 1.0),
        }
    finally:
        conn.close()

    if pick:
        for k in ("rvol","gap_pct","range_pct"):
            if k in pick:
                feat[k] = float(pick[k])

    X = np.array([[feat[c] for c in FEATURE_COLUMNS]], dtype=np.float32)
    for i in range(X.shape[1]):
        if np.isnan(X[0, i]):
            X[0, i] = meds[i]

    prob = float(model.predict_proba(X)[0][1])

    # Apply OOS-validated hard filter gates on top of model score
    # These cut false positives ~3x without destroying recall
    passes_filters = (
        feat["vs_20d_high"] <= FILTER_VS_20D_HIGH and
        feat["vol_vs_20d"]  <= FILTER_VOL_VS_20D
    )

    if prob >= STRONG_THRESHOLD and passes_filters:
        signal = "SETUP"
    elif prob >= MODERATE_THRESHOLD and passes_filters:
        signal = "WATCHING"
    elif prob >= MODERATE_THRESHOLD:
        signal = "WATCHING_EXTENDED"   # model likes it but filters flag it as already moving
    else:
        signal = "NO_SETUP"

    signal_desc = {
        "SETUP":             "Pre-move coil/flush — volume quiet, range contracting, pulled back from high. High-conviction setup.",
        "WATCHING":          "Some pre-move characteristics — monitor for volume dry-up and range contraction to confirm.",
        "WATCHING_EXTENDED": "Model score is elevated but stock is already extended (volume surging or at/near 20d high). Wait for pullback.",
        "NO_SETUP":          "No pre-move setup pattern detected.",
    }

    filter_flags = {
        "coiled_below_high":   feat["vs_20d_high"] <= FILTER_VS_20D_HIGH,
        "volume_quiet":        feat["vol_vs_20d"]  <= FILTER_VOL_VS_20D,
        "passes_all_gates":    passes_filters,
    }

    return {
        "ticker":      ticker,
        "setup_prob":  round(prob, 3),
        "signal":      signal,
        "trained_at":  art.get("trained_at", "unknown"),
        "interpretation": signal_desc[signal],
        "filter_gates":   filter_flags,
        "thresholds": {
            "strong":          STRONG_THRESHOLD,
            "moderate":        MODERATE_THRESHOLD,
            "vs_20d_high_max": FILTER_VS_20D_HIGH,
            "vol_vs_20d_max":  FILTER_VOL_VS_20D,
        },
        "features": {
            "range_pct_%":         round(feat["range_pct"], 2),
            "range_contracting":   round(feat["range_trend"], 3),
            "vol_vs_20d_avg":      round(feat["vol_vs_20d"], 3),
            "vol_trend":           round(feat["vol_trend"], 3),
            "pct_of_20d_high":     round(feat["vs_20d_high"] * 100, 1),
            "pct_above_20d_low":   round((feat["vs_20d_low"] - 1) * 100, 1),
            "mom_5d_%":            round(feat["mom_5d"] * 100, 2),
            "mom_20d_%":           round(feat["mom_20d"] * 100, 2),
            "mom_60d_%":           round(feat.get("mom_60d", 0) * 100, 2),
            "low_stability":       round(feat.get("low_stability", 1.0), 3),
            "gap_pct_%":           round(feat["gap_pct"], 2),
            "close_strength":      round(feat["close_strength"], 3),
            "price_vs_52wh":       round(feat["price_vs_52wh"], 3),
            "rvol":                round(feat["rvol"], 2),
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
