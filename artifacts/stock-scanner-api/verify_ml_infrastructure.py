"""
verify_ml_infrastructure.py
=============================
Standalone verification harness for ml_infrastructure.py (Group 3: data_prep,
model_training, ml_engine, evaluation_metrics, slippage_model,
signal_discovery_gp).

Design intent: no mocking, no self-reported "looks good" — every check runs
against synthetic-but-realistic data and prints RAW numeric output plus an
explicit PASS/FAIL line. Run this directly:

    python verify_ml_infrastructure.py

Exit code is 0 only if every check passes. A non-zero exit code means at
least one module is broken — read the FAIL lines above it.
"""
from __future__ import annotations

import sys
import traceback
import numpy as np
import pandas as pd

from ml_infrastructure import (
    time_series_train_test_split,
    walk_forward_splits,
    train_model,
    predict,
    MLEngine,
    classification_metrics,
    regression_metrics,
    SlippageModel,
    gp_signal_search,
)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = ""):
    RESULTS.append((name, condition, detail))
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def make_synthetic_ohlcv(n: int = 500, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="5min")
    price = 100 + np.cumsum(rng.normal(0, 0.3, n))
    volume = rng.integers(1000, 50000, n)
    feat1 = rng.normal(0, 1, n)
    feat2 = rng.normal(0, 1, n)
    # target correlated with feat1 so signal search / training has something
    # real to find, plus noise.
    fwd_return = 0.5 * feat1 + rng.normal(0, 1, n)
    label_up = (fwd_return > 0).astype(int)
    return pd.DataFrame({
        "timestamp": dates,
        "price": price,
        "volume": volume,
        "feat1": feat1,
        "feat2": feat2,
        "fwd_return": fwd_return,
        "label_up": label_up,
    })


# ============================================================================
# 1. data_prep
# ============================================================================
def verify_data_prep():
    print("\n--- 1. data_prep ---")
    df = make_synthetic_ohlcv(500)
    train_df, test_df = time_series_train_test_split(
        df, "timestamp", train_frac=0.7, embargo_bars=5
    )
    check(
        "data_prep: split sizes correct",
        len(train_df) == 350 and len(test_df) == 500 - 350 - 5,
        f"train={len(train_df)} test={len(test_df)}",
    )
    check(
        "data_prep: no lookahead (test min ts > train max ts)",
        test_df["timestamp"].min() > train_df["timestamp"].max(),
        f"train_max={train_df['timestamp'].max()} test_min={test_df['timestamp'].min()}",
    )
    # Bad input should raise, not silently succeed
    raised = False
    try:
        time_series_train_test_split(df, "timestamp", train_frac=1.5)
    except ValueError:
        raised = True
    check("data_prep: invalid train_frac raises ValueError", raised)

    folds = list(walk_forward_splits(df, "timestamp", n_splits=4, embargo_bars=3))
    check(
        "data_prep: walk_forward yields expanding windows",
        all(folds[i][1].shape[0] < folds[i + 1][1].shape[0] for i in range(len(folds) - 1)),
        f"train sizes = {[f[1].shape[0] for f in folds]}",
    )


# ============================================================================
# 2. model_training
# ============================================================================
def verify_model_training():
    print("\n--- 2. model_training ---")
    df = make_synthetic_ohlcv(500)
    train_df, test_df = time_series_train_test_split(df, "timestamp", 0.7, 5)
    from sklearn.linear_model import LinearRegression

    X_train, y_train = train_df[["feat1", "feat2"]], train_df["fwd_return"]
    result = train_model(X_train, y_train, lambda: LinearRegression())
    check(
        "model_training: TrainResult populated",
        result.n_train_rows == len(X_train) and result.feature_names == ["feat1", "feat2"],
        f"n_train_rows={result.n_train_rows} features={result.feature_names}",
    )
    check(
        "model_training: train_score is finite R^2",
        np.isfinite(result.train_score),
        f"train_score={result.train_score}",
    )

    X_test = test_df[["feat1", "feat2"]]
    preds = predict(result, X_test)
    check(
        "model_training: predict returns correct length",
        len(preds) == len(X_test),
        f"len(preds)={len(preds)}",
    )

    # Feature mismatch should raise
    raised = False
    try:
        predict(result, X_test.rename(columns={"feat2": "featX"}))
    except ValueError:
        raised = True
    check("model_training: feature mismatch raises ValueError", raised)

    # NaN input should raise
    raised = False
    try:
        bad_X = X_train.copy()
        bad_X.iloc[0, 0] = np.nan
        train_model(bad_X, y_train, lambda: LinearRegression())
    except ValueError:
        raised = True
    check("model_training: NaN in X_train raises ValueError", raised)


# ============================================================================
# 3. ml_engine
# ============================================================================
def verify_ml_engine():
    print("\n--- 3. ml_engine ---")
    df = make_synthetic_ohlcv(500)
    from sklearn.linear_model import LinearRegression

    engine = MLEngine(
        model_factory=lambda: LinearRegression(),
        timestamp_col="timestamp",
        embargo_bars=5,
    )
    metrics = engine.fit(df, feature_cols=["feat1", "feat2"], target_col="fwd_return")
    check(
        "ml_engine: fit() returns regression metrics",
        set(["mae", "rmse", "r2", "directional_hit_rate"]).issubset(metrics.keys()),
        f"metrics={metrics}",
    )
    check(
        "ml_engine: directional_hit_rate beats coin flip (signal is real in synth data)",
        metrics["directional_hit_rate"] > 0.55,
        f"directional_hit_rate={metrics['directional_hit_rate']}",
    )

    preds = engine.predict(df[["feat1", "feat2"]])
    check(
        "ml_engine: predict() works post-fit",
        len(preds) == len(df),
        f"len(preds)={len(preds)}",
    )

    raised = False
    try:
        MLEngine(lambda: LinearRegression(), "timestamp").predict(df[["feat1", "feat2"]])
    except RuntimeError:
        raised = True
    check("ml_engine: predict() before fit() raises RuntimeError", raised)


# ============================================================================
# 4. evaluation_metrics
# ============================================================================
def verify_evaluation_metrics():
    print("\n--- 4. evaluation_metrics ---")
    y_true_cls = np.array([1, 0, 1, 1, 0, 0, 1, 0])
    y_pred_cls = np.array([1, 0, 0, 1, 0, 1, 1, 0])
    cm = classification_metrics(y_true_cls, y_pred_cls)
    # hand-computed: tp=3 fp=1 tn=3 fn=1 -> acc=6/8=0.75, prec=3/4=0.75, recall=3/4=0.75
    check(
        "evaluation_metrics: classification_metrics matches hand calc",
        cm["accuracy"] == 0.75 and cm["precision"] == 0.75 and cm["recall"] == 0.75,
        f"cm={cm}",
    )

    y_true_reg = np.array([1.0, 2.0, 3.0, -1.0])
    y_pred_reg = np.array([1.1, 1.9, 3.2, -0.8])
    rm = regression_metrics(y_true_reg, y_pred_reg)
    check(
        "evaluation_metrics: regression_metrics MAE/RMSE sane and positive",
        rm["mae"] > 0 and rm["rmse"] >= rm["mae"],
        f"rm={rm}",
    )
    check(
        "evaluation_metrics: directional_hit_rate is 1.0 for same-sign preds",
        rm["directional_hit_rate"] == 1.0,
        f"directional_hit_rate={rm['directional_hit_rate']}",
    )

    raised = False
    try:
        regression_metrics(np.array([1, 2]), np.array([1, 2, 3]))
    except ValueError:
        raised = True
    check("evaluation_metrics: shape mismatch raises ValueError", raised)


# ============================================================================
# 5. slippage_model
# ============================================================================
def verify_slippage_model():
    print("\n--- 5. slippage_model ---")
    # NOTE: source PDF truncated this constructor call — impact_coefficient
    # value was cut off. Verify this against your actual ml_infrastructure.py
    # signature/defaults before running.
    model = SlippageModel(
        commission_per_contract=0.65,
        base_spread_bps=2.0,
        impact_coefficient=0.1,  # <-- ASSUMED VALUE, confirm against source
    )
    buy = model.estimate_fill(
        mid_price=100.0, quantity=10, avg_daily_volume=1_000_000, side="buy"
    )
    sell = model.estimate_fill(
        mid_price=100.0, quantity=10, avg_daily_volume=1_000_000, side="sell"
    )
    check(
        "slippage_model: buy fill price > mid, sell fill price < mid",
        buy.fill_price > 100.0 and sell.fill_price < 100.0,
        f"buy={buy.fill_price} sell={sell.fill_price}",
    )
    check(
        "slippage_model: total_cost = slippage + commission",
        abs(buy.total_cost - (buy.slippage_dollars + buy.commission)) < 1e-6,
        f"total_cost={buy.total_cost} slippage={buy.slippage_dollars} commission={buy.commission}",
    )

    small = model.estimate_fill(100.0, 10, 1_000_000, "buy")
    large = model.estimate_fill(100.0, 100_000, 1_000_000, "buy")
    check(
        "slippage_model: larger order size => larger slippage_bps (market impact)",
        large.slippage_bps > small.slippage_bps,
        f"small_bps={small.slippage_bps} large_bps={large.slippage_bps}",
    )

    raised = False
    try:
        model.estimate_fill(100.0, 10, 1_000_000, side="hold")
    except ValueError:
        raised = True
    check("slippage_model: invalid side raises ValueError", raised)

    raised = False
    try:
        model.estimate_fill(-5.0, 10, 1_000_000, "buy")
    except ValueError:
        raised = True
    check("slippage_model: negative mid_price raises ValueError", raised)


# ============================================================================
# 6. signal_discovery_gp
# ============================================================================
def verify_signal_discovery_gp():
    print("\n--- 6. signal_discovery_gp ---")
    df = make_synthetic_ohlcv(200)  # smaller n — GP fitting is O(n^3)
    X = df[["feat1", "feat2"]]
    y = df["fwd_return"]
    result = gp_signal_search(X, y, n_candidates=5)
    check(
        "signal_discovery_gp: returns ranked_features covering all columns",
        len(result["ranked_features"]) == 2,
        f"ranked_features={result['ranked_features']}",
    )
    top_feature = result["ranked_features"][0][0]
    check(
        "signal_discovery_gp: feat1 (the real signal) ranks above feat2 (noise)",
        top_feature == "feat1",
        f"top_feature={top_feature} full_ranking={result['ranked_features']}",
    )

    raised = False
    try:
        gp_signal_search(pd.DataFrame(), pd.Series(dtype=float))
    except ValueError:
        raised = True
    check("signal_discovery_gp: empty input raises ValueError", raised)


# ============================================================================
# Runner
# ============================================================================
def main():
    verifiers = [
        verify_data_prep,
        verify_model_training,
        verify_ml_engine,
        verify_evaluation_metrics,
        verify_slippage_model,
        verify_signal_discovery_gp,
    ]
    for v in verifiers:
        try:
            v()
        except Exception as exc:
            check(v.__name__, False, f"RAISED UNEXPECTED EXCEPTION: {exc}")
            traceback.print_exc()

    print("\n" + "=" * 60)
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"RESULT: {passed}/{total} checks passed")
    if failed:
        print("FAILED CHECKS:")
        for name in failed:
            print(f"  - {name}")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
