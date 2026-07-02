"""
predict.py - combines the 4 per-horizon models (train.py) with the T007
report overlays (context.py) into a final per-ticker ProbabilityReport
(schemas.py), matching the spec's output format.

Architect guidance this file follows (see .local/session_plan.md T008 /
2026-07-01 architect consult):

1. PROBABILITY SOURCE: raw model_horizon_{h}d.pkl probabilities are the
   DEFAULT for prob_up_Nd. calibration.py (T005) found calibration does NOT
   improve Brier at the current date-count, and its saved base_model is
   fit on only the 60% train split - not the full-data model train.py saves.
   Calibrated output is only used per-horizon if ALL of: a calibrated
   artifact exists, its test-fold cal_brier < raw_brier, its test fold has
   >= MIN_CALIBRATED_TEST_ROWS, and n_unique_dates >= MIN_UNIQUE_DATES_FOR_CV_TRUST.
   Every report says which source was used per horizon (never silently
   swapped).

2. NOT AN ENSEMBLE: there is one model per horizon, not multiple model
   families voting. The spec's "ensemble/disagreement" framing is
   reinterpreted honestly as CROSS-HORIZON term-structure instability
   (e.g. 1d says up, 2d says down, or a > DISAGREEMENT_SPREAD_THRESHOLD
   swing between adjacent horizons) - this LOWERS reported confidence, it
   never changes the probabilities themselves.

3. CONFIDENCE is a reliability/honesty score, not another win-probability.
   It starts at a neutral baseline and is only ever adjusted DOWN by
   documented penalties (date-count immaturity, Tier-2 sparsity, calibrated
   source, cross-horizon disagreement, missing/degraded overlays, spread
   floor-clamp ambiguity, no ticker history at all). Every active penalty
   is listed in `warnings` so a human can see exactly why a confidence
   number is capped where it is.

Run directly for a demo against real DB rows (does not write to the shadow
log; use reports.py for that):
    python predict.py
"""
import hashlib
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_training import get_feature_importance
from point_in_time_guard import LookaheadViolation

from config import HORIZONS, MODEL_DIR, MIN_UNIQUE_DATES_FOR_CV_TRUST, TIER2_FEATURE_COLUMNS
from schemas import HorizonProbability, ProbabilityReport
from context import (
    _load_spy_history,
    _load_ticker_ohlcv,
    regime_tag_as_of,
    liquidity_context_as_of,
    layer9_score_as_of,
    edge_after_cost,
)
import model_registry

# Cross-horizon prob_up range above which the term-structure is flagged as
# "disagreeing" (arbitrary but documented: half the distance from a coin
# flip to a fully confident call).
DISAGREEMENT_SPREAD_THRESHOLD = 0.25

# A calibrated artifact must clear this many test-fold rows before its
# reported cal_brier improvement is trusted enough to prefer it over raw.
MIN_CALIBRATED_TEST_ROWS = 30

# Hard confidence ceiling while unique trade dates are below the CV-trust
# floor (see config.MIN_UNIQUE_DATES_FOR_CV_TRUST) - this dataset is at
# 9-11 dates as of 2026-07-01, so this cap is ALWAYS active today. Kept as
# a named constant (not inlined) so it's easy to find and re-justify later.
DATE_IMMATURITY_CONFIDENCE_CAP = 0.55

BASELINE_CONFIDENCE = 0.5


def load_models() -> dict:
    """{horizon_days: TrainedModel}, loaded from MODEL_DIR. Skips any
    horizon whose file is missing rather than crashing the whole batch."""
    models = {}
    for h in HORIZONS:
        path = os.path.join(MODEL_DIR, f"model_horizon_{h}d.pkl")
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            models[h] = pickle.load(f)
    return models


def load_calibrated_models() -> dict:
    """{horizon_days: calibration artifact dict}, best-effort - calibrated
    artifacts are optional and may not exist for every horizon."""
    calibrated = {}
    for h in HORIZONS:
        path = os.path.join(MODEL_DIR, f"calibrated_horizon_{h}d.pkl")
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            calibrated[h] = pickle.load(f)
    return calibrated


def load_models_as_of(as_of_date) -> tuple:
    """
    PIT-safe replacement for load_models() when the caller is about to
    score a row/date that has its OWN signal_date - i.e. every backlog or
    historical scoring path (reports.py). Returns (models, entries) where
    models={horizon: TrainedModel} and entries={horizon: registry entry},
    restricted to horizons whose model_registry.get_as_of(horizon,
    as_of_date) found an entry that could not possibly have absorbed
    outcome information from as_of_date or later (see model_registry.py).

    A horizon with no eligible entry is simply ABSENT from both dicts -
    callers must treat a missing horizon as "cannot be scored yet for this
    date" and skip it, never fall back to load_models() (which would
    silently reintroduce the exact leak this function exists to prevent).

    Do NOT use this for "what does the model say about the market right
    now" with no specific signal_date in mind - use load_models() (today's
    picks, scored today, have no signal_date in the past to leak against).
    """
    models, entries = {}, {}
    for h in HORIZONS:
        entry = model_registry.get_as_of(h, as_of_date)
        if entry is None:
            continue
        models[h] = model_registry.load_model_from_entry(entry)
        entries[h] = entry
    return models, entries


def compute_model_version(models: dict) -> str:
    """
    Deterministic short hash over the trained models' pickled bytes, so a
    logged prediction can always be traced to the exact model artifacts
    that produced it, and retraining automatically bumps the version
    (no manual version-string bookkeeping to forget).

    Also hashes any calibrated_horizon_{h}d.pkl artifacts that exist, even
    though calibration is not selectable today (gate always fails at
    n_unique_dates<20) - once it IS selectable, a calibration-only retrain
    must still bump model_version, or two differently-calibrated runs would
    collide under the same version and be indistinguishable in the shadow
    log.
    """
    h = hashlib.sha256()
    for horizon in sorted(models.keys()):
        for prefix in ("model_horizon", "calibrated_horizon"):
            path = os.path.join(MODEL_DIR, f"{prefix}_{horizon}d.pkl")
            if os.path.exists(path):
                with open(path, "rb") as f:
                    h.update(f.read())
    return h.hexdigest()[:12]


def _select_probability_source(h: int, trained, calibrated: dict) -> dict:
    """
    Returns {"source": "raw"|"calibrated", "trained": TrainedModel to score
    with}. Only "calibrated" if the gating conditions in the module
    docstring are ALL met; otherwise falls back to raw with a note.
    """
    art = calibrated.get(h)
    if art is None:
        return {"source": "raw", "reason": "no calibrated artifact for this horizon"}

    n_dates = getattr(trained, "n_unique_dates", 0)
    if art["n_test"] < MIN_CALIBRATED_TEST_ROWS:
        return {"source": "raw", "reason": f"calibrated test fold too small (n={art['n_test']} < {MIN_CALIBRATED_TEST_ROWS})"}
    if n_dates < MIN_UNIQUE_DATES_FOR_CV_TRUST:
        return {"source": "raw", "reason": f"only {n_dates} unique trade dates (need >= {MIN_UNIQUE_DATES_FOR_CV_TRUST}) to trust a calibration gain"}
    if not (art["cal_brier"] < art["raw_brier"]):
        return {"source": "raw", "reason": f"calibration did not improve Brier (raw={art['raw_brier']:.4f} cal={art['cal_brier']:.4f})"}

    return {"source": "calibrated", "artifact": art}


def _predict_horizon(h: int, trained, calibrated: dict, feature_row: pd.Series) -> tuple:
    """Returns (HorizonProbability, probability_source_str)."""
    selection = _select_probability_source(h, trained, calibrated)
    X = feature_row[trained.feature_columns].to_frame().T

    if selection["source"] == "calibrated":
        art = selection["artifact"]
        raw_p = art["base_model"].model.predict_proba(X[art["feature_columns"]])[:, 1][0]
        if art["method"] == "isotonic":
            p = float(art["calibrator"].predict(np.array([raw_p]))[0])
        else:
            p = float(art["calibrator"].predict_proba(np.array([[raw_p]]))[:, 1][0])
        source = f"calibrated_{art['method']}"
    else:
        p = float(trained.model.predict_proba(X)[:, 1][0])
        source = "raw"

    hp = HorizonProbability(
        horizon_days=h,
        prob_up=round(p, 4),
        n_training_samples=trained.n_samples,
        is_trustworthy=trained.is_trustworthy,
        model_type=trained.model_type,
        calibration_bucket_n=selection.get("artifact", {}).get("n_test") if selection["source"] == "calibrated" else None,
    )
    return hp, source


def _top_contributing_layers(models: dict, top_n: int = 5) -> list:
    """
    Averages feature_importance across all loaded horizon models (a feature
    important to only one horizon is a weaker "top layer" claim than one
    important across the term structure), strips the _z suffix back to the
    underlying layer name, and returns the top_n names.
    """
    if not models:
        return []
    combined = None
    for trained in models.values():
        try:
            imp = get_feature_importance(trained).set_index("feature")["importance"]
        except Exception:
            continue
        combined = imp if combined is None else combined.add(imp, fill_value=0)
    if combined is None:
        return []
    combined = combined.sort_values(ascending=False)
    layer_names = [c[:-2] if c.endswith("_z") else c for c in combined.index]
    seen, out = set(), []
    for name in layer_names:
        if name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= top_n:
            break
    return out


def _compute_confidence(horizon_probs: dict, models: dict, sources: dict,
                         overlays: dict, tier2_available: bool) -> tuple:
    """
    Returns (confidence: float, warnings: list[str]). Confidence is an
    honesty/reliability score in [0, 1] that only ever moves DOWN from
    BASELINE_CONFIDENCE via documented, additive penalties, then is clamped
    by any active hard cap. Every active adjustment is listed in warnings.
    """
    warnings = []
    penalties = 0.0
    hard_cap = 1.0

    min_dates = min((getattr(m, "n_unique_dates", 0) for m in models.values()), default=0)
    if min_dates < MIN_UNIQUE_DATES_FOR_CV_TRUST:
        hard_cap = min(hard_cap, DATE_IMMATURITY_CONFIDENCE_CAP)
        warnings.append(
            f"confidence capped at {DATE_IMMATURITY_CONFIDENCE_CAP} - only {min_dates} unique "
            f"trade dates in training (need >= {MIN_UNIQUE_DATES_FOR_CV_TRUST}); model_training's "
            f"internal CV is row-count sufficient but date-count immature"
        )

    if not tier2_available:
        penalties += 0.05
        warnings.append(
            "-0.05: Tier-2 options-positioning layers (gamma/dark_pool/squeeze/sector_heat) "
            "were NaN for this row - prediction relies on Tier-1 technicals only"
        )

    if any(src.startswith("calibrated") for src in sources.values()):
        penalties += 0.05
        warnings.append("-0.05: at least one horizon used a calibrated (not raw) probability")

    probs = [v for v in horizon_probs.values() if v is not None]
    if len(probs) >= 2:
        spread = max(probs) - min(probs)
        directions = {p >= 0.5 for p in probs}
        if spread > DISAGREEMENT_SPREAD_THRESHOLD or len(directions) > 1:
            penalties += 0.10
            warnings.append(
                f"-0.10: cross-horizon disagreement (range={spread:.2f}, "
                f"{'mixed direction' if len(directions) > 1 else 'wide spread'} across "
                f"1d-4d) - term structure is unstable, not an ensemble vote"
            )

    regime = overlays.get("regime") or {}
    if regime.get("regime_tag") in (None, "insufficient_history", "leakage_guard_tripped"):
        penalties += 0.05
        warnings.append("-0.05: regime overlay unavailable (insufficient SPY history or guard trip)")

    liq = overlays.get("liquidity") or {}
    if liq.get("error"):
        penalties += 0.05
        warnings.append("-0.05: liquidity/cost overlay unavailable (insufficient ticker history)")
    elif liq.get("spread_possibly_floor_clamped"):
        penalties += 0.03
        warnings.append("-0.03: liquidity spread estimate may be floor-clamped (0.0 is ambiguous, see context.py)")

    if overlays.get("layer9") is None:
        warnings.append("note: layer9 statistical-edge overlay not computed (optional, no penalty)")

    conf = max(0.0, min(BASELINE_CONFIDENCE - penalties, hard_cap))
    return round(conf, 3), warnings


def predict_row(feature_row: pd.Series, models: dict, calibrated: dict,
                 spy_hist: pd.DataFrame, ticker_hist: pd.DataFrame) -> ProbabilityReport:
    """
    feature_row: one row from features.add_standardized_features()'s output
    (must have ticker, trade_date, and the *_z feature columns).
    spy_hist / ticker_hist: pre-loaded, batched history (see context.py) -
    passed in so a batch caller loads them ONCE, not once per row.
    """
    ticker = feature_row["ticker"]
    as_of = pd.Timestamp(feature_row["trade_date"]).date()

    horizons, sources, horizon_probs = {}, {}, {}
    for h, trained in models.items():
        hp, source = _predict_horizon(h, trained, calibrated, feature_row)
        horizons[h] = hp
        sources[h] = source
        horizon_probs[h] = hp.prob_up

    try:
        regime = regime_tag_as_of(spy_hist, as_of)
    except LookaheadViolation as e:
        regime = {"regime_tag": "leakage_guard_tripped", "error": str(e)}

    try:
        liquidity = liquidity_context_as_of(ticker_hist, as_of)
    except LookaheadViolation as e:
        liquidity = {"error": f"leakage_guard_tripped: {e}"}

    try:
        l9 = layer9_score_as_of(ticker_hist, as_of)
    except LookaheadViolation:
        l9 = None

    overlays = {"regime": regime, "liquidity": liquidity, "layer9": l9}

    tier2_available = any(pd.notna(feature_row.get(f"{c}_z")) for c in TIER2_FEATURE_COLUMNS)
    confidence, warnings = _compute_confidence(horizon_probs, models, sources, overlays, tier2_available)

    # edge_after_cost is reported off the nearest horizon (1d) as the most
    # directly actionable number; per-horizon edge is in _horizon_detail
    # via the caller if needed (schemas.py currently reports one edge value).
    lead_prob = horizon_probs.get(min(horizons.keys())) if horizons else None
    eac = edge_after_cost(lead_prob, liquidity) if lead_prob is not None else None
    if eac and eac.get("edge_after_cost_pct") is not None:
        # edge_after_cost_prob_pts is a PROBABILITY-DISTANCE measure (how
        # far prob_up sits from a 50/50 coin flip, minus an estimated cost
        # drag) reported directly in percentage points - it is NOT a
        # backtested or historical return figure, and must never be read
        # as one (deliberately not called "bps" - see schemas.py). Stated
        # explicitly here (not just in context.py) because this is the
        # number that actually reaches the final report.
        warnings.append(
            f"edge_after_cost_prob_pts ({eac['edge_after_cost_pct']}) is a "
            f"probability-distance-from-coinflip measure minus estimated cost, NOT a "
            f"historical/backtested return - see context.edge_after_cost() docstring"
        )

    # Overlays are computed with data <= as_of_date, i.e. INCLUDING as_of_date's
    # own close (point-in-time-safe for a POST-CLOSE view of that day). If this
    # ticker_hist actually contains a row dated exactly as_of, that day's full
    # close was available when these overlays were computed. In live daily use
    # this is naturally safe: polygon_market_daily is an EOD batch table, so a
    # same-day run before that table updates simply won't see today's row yet
    # and overlays fall back to the prior day. It only matters when BACKFILLING
    # a historical pick that was originally made intraday, before that day's
    # own close was known - flag it so nobody mistakes this for what would
    # have been knowable at the moment the pick actually fired.
    if not ticker_hist.empty and (ticker_hist["date"] == as_of).any():
        warnings.append(
            "overlays include trade_date's own closing price (post-close view) - "
            "if this pick originally fired intraday, regime/liquidity/layer9 reflect "
            "more information than was available at that moment"
        )

    top_layers = _top_contributing_layers(models)

    report = ProbabilityReport(
        ticker=ticker,
        signal_date=str(as_of),
        horizons=horizons,
        confidence=confidence,
        top_contributing_layers=top_layers,
        regime_tag=regime.get("regime_tag"),
        edge_after_cost_prob_pts=eac.get("edge_after_cost_pct") if eac else None,
        data_tier_used="tier1_dominant" if not tier2_available else "tier1_and_tier2",
        warnings=warnings,
    )
    # Attach raw overlay/source detail for shadow logging without changing
    # the spec-facing to_dict() contract in schemas.py.
    report._probability_sources = sources
    report._overlays = overlays
    return report


def generate_predictions(std_df: pd.DataFrame, models: dict = None) -> list:
    """
    Batch entry point: std_df is features.add_standardized_features()'s
    output (or any subset of it - e.g. only unlogged rows, see reports.py).
    Loads history ONCE for the whole batch, not per row.

    models: pass the exact {horizon: TrainedModel} dict to score with (e.g.
    from load_models_as_of(as_of_date) for PIT-safe historical scoring).
    Defaults to load_models() (today's latest) for backward compatibility
    with callers that are genuinely scoring "right now" with no specific
    signal_date to protect against - see load_models_as_of()'s docstring
    for when that default is and isn't appropriate.
    """
    if std_df.empty:
        return []

    if models is None:
        models = load_models()
    if not models:
        raise RuntimeError("no trained models found in MODEL_DIR - run train.py first")
    calibrated = load_calibrated_models()

    spy_hist = _load_spy_history()
    tickers = std_df["ticker"].dropna().unique().tolist()
    ticker_hist_all = _load_ticker_ohlcv(tickers)
    hist_by_ticker = {t: g.reset_index(drop=True) for t, g in ticker_hist_all.groupby("ticker")}
    empty_hist = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    reports = []
    for _, row in std_df.iterrows():
        thist = hist_by_ticker.get(row["ticker"], empty_hist)
        reports.append(predict_row(row, models, calibrated, spy_hist, thist))
    return reports


if __name__ == "__main__":
    from data_snapshot import build_dataset
    from features import add_standardized_features

    raw = build_dataset()
    if raw.empty:
        raise SystemExit("no dataset available - run data_snapshot.py first")

    std_df = add_standardized_features(raw)
    sample = std_df.sort_values("trade_date").tail(5)

    models = load_models()
    print(f"model_version = {compute_model_version(models)}")

    reports = generate_predictions(sample)
    for r in reports:
        print(f"\n=== {r.ticker} signal_date={r.signal_date} ===")
        d = r.to_dict()
        for k in ("prob_up_1d", "prob_up_2d", "prob_up_3d", "prob_up_4d",
                   "confidence", "regime_tag", "edge_after_cost_prob_pts", "data_tier_used"):
            print(f"  {k:20s} {d[k]}")
        print(f"  top_contributing_layers: {d['top_contributing_layers']}")
        print(f"  probability_sources: {r._probability_sources}")
        if d["warnings"]:
            print("  warnings:")
            for w in d["warnings"]:
                print(f"    - {w}")
