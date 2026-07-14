"""
live_query.py - live, single-ticker "what does AIEM say right now" query for
the AIEM Probability Engine, built to satisfy a skeptical external audit spec
that requires a cryptographically signed payload (see aiem_provenance.py)
plus explicit, honest disclosure of every place this differs from the
shadow-log's pit_safe/leaked backtest semantics.

ISOLATION CONTRACT: same as the rest of this package - this file is never
imported by main.py and never wired into live scan/alert logic. main.py
invokes it as a subprocess and reads its stdout JSON (same arm's-length
pattern as the existing force-run -> daily_picks.py subprocess call).

WHY THIS IS A THIRD, HONEST pit_status CATEGORY (not pit_safe, not leaked):
  - reports.py / daily_picks.py answer "what would this row's outcome have
    looked like, PIT-safely" for shadow-log rows with a KNOWN signal_date -
    that is what pit_status='pit_safe' vs 'leaked' means, gated by
    model_registry.get_as_of()'s label_settled_through < as_of_date check.
  - This file answers "what does AIEM say about a ticker RIGHT NOW" - there
    is no future signal_date to leak against (predict.py's own docstring:
    "today's picks ... have no signal_date in the past to leak against" -
    use model_registry.get_latest(), never get_as_of(), for this case).
    That is a different honesty question (is the model being asked
    something it can legitimately know, right now?) from the PIT-leakage
    question, so it gets its own label: pit_status='live_unsettled'.
    These rows are NEVER written into aiem_probability_engine_predictions
    (the pit_safe/leaked shadow log) - they get their own table below, so
    the track-record endpoint's pit_status_counts can never be diluted by
    live, not-yet-settled queries.

WHAT THIS ADDS BEYOND predict.py's _top_contributing_layers():
  - That function is a GLOBAL, dataset-averaged top-N list of NAMES only -
    not row-specific, no raw value, no sign. A skeptical auditor asking
    "which layer drove *this* ticker's score, and what was its raw value"
    cannot be answered with it.
  - This file computes a genuine PER-ROW signed contribution, with the
    exact method depending on which model_type model_training.py actually
    trained (checked live via HAS_XGBOOST at prediction time, not assumed):
      * LogisticRegression pipeline (impute -> scale -> clf): contribution_i
        = coef_i * scaled_feature_value_i, replaying the fitted pipeline's
        own impute+scale steps.
      * XGBoost pipeline (impute -> clf, no scale step): contribution_i via
        the booster's OWN native TreeSHAP implementation
        (Booster.predict(pred_contribs=True)) - this is exact, game-
        theoretic-additive, and does NOT require the separate `shap`
        package (which is not installed in this environment; xgboost ships
        TreeSHAP internally). Verified live in this environment
        (2026-07-02): xgboost IS installed (3.2.0) and IS what
        model_training.py's "auto" selects, so this path is the one
        actually exercised today, not the LogisticRegression fallback.
    Either way, the manually-replayed probability is asserted to match
    pipeline.predict_proba() before being trusted (see _row_contributions).

Run directly:
    python3 live_query.py --ticker AAPL --json
    python3 live_query.py --auto --json          (auto-picks an active ticker)
    python3 live_query.py --find-conflict --json  (Q2 scenario)
    python3 live_query.py --find-null --json      (Q3 scenario)
"""
import argparse
import io
import json
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_URL, HORIZONS, TIER1_FEATURE_COLUMNS, TIER2_FEATURE_COLUMNS
from data_snapshot import build_dataset
from features import add_standardized_features, LAYER_COLUMNS
import model_registry
from aiem_provenance import sign_payload, verify_payload

TABLE = "aiem_probability_engine_live_queries"

_ET = None


def _et_today() -> date:
    global _ET
    if _ET is None:
        try:
            from zoneinfo import ZoneInfo
            _ET = ZoneInfo("America/New_York")
        except Exception:
            import datetime as _dt
            _ET = _dt.timezone.utc
    import datetime as _dt
    return _dt.datetime.now(_ET).date()


_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    mode TEXT NOT NULL,
    model_version TEXT,
    pit_status TEXT NOT NULL,
    request_json JSONB,
    envelope_json JSONB,
    verified BOOLEAN,
    verify_reason TEXT
)
"""


def ensure_table() -> None:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
        conn.commit()


def _nearest_horizon_to_friday(as_of: date) -> int:
    """
    Maps the user's "by Friday's close" framing onto the model's actual
    1-4 TRADING-day horizons. weekday(): Mon=0 ... Sun=6.
      Mon(0) -> 4 trading days to this Fri
      Tue(1) -> 3
      Wed(2) -> 2
      Thu(3) -> 1
      Fri(4)/Sat(5)/Sun(6) -> "this Friday" has already passed or IS today;
        falls back to 4 (next Friday) with an explicit scope warning, since
        the model has no horizon beyond 4 trading days.
    """
    wd = as_of.weekday()
    mapping = {0: 4, 1: 3, 2: 2, 3: 1}
    return mapping.get(wd, 4)


def _load_row_metadata(ticker: str, trade_date) -> dict:
    """Strike/expiry/pick_id + raw Tier-1/2 values straight from the source
    table, for the specific (ticker, trade_date) row - this is what an
    auditor can independently SELECT and compare against."""
    # rvol/gap_pct are NOT columns on ai_short_calls_log (they're derived
    # from polygon_market_daily in data_snapshot.py's _pit_features) -
    # raw_row already carries those; this query is only for the
    # strike/expiry/pick_id + Tier-1/2 source columns that DO live here.
    sql = """
        SELECT pick_id, strike, expiry, conviction, vol_oi, otm_pct, days_out,
               gamma_score, dark_pool_score, squeeze_score, sector_heat_score
        FROM ai_short_calls_log
        WHERE ticker = %s AND trade_date = %s
        ORDER BY id DESC LIMIT 1
    """
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (ticker, trade_date))
            row = cur.fetchone()
    return dict(row) if row else {}


def _load_latest_models() -> tuple:
    """{horizon: TrainedModel}, {horizon: registry entry} using
    model_registry.get_latest() - the function that file's own docstring
    reserves for "genuinely-live, no-as-of-date use" (this is exactly
    that use case, NOT a backtest of a fixed historical signal_date)."""
    models, entries = {}, {}
    for h in HORIZONS:
        entry = model_registry.get_latest(h)
        if entry is None:
            continue
        models[h] = model_registry.load_model_from_entry(entry)
        entries[h] = entry
    return models, entries


def _row_contributions(trained, std_row: pd.Series, raw_row: pd.Series) -> dict:
    """
    Returns {"probability": float, "layers": [ {name, raw_value, z_score,
    was_imputed, coef, signed_contribution}, ... ] } for one horizon's
    fitted pipeline against one row.

    Signed, row-specific contribution = coef_i * scaled_feature_value_i,
    computed by manually stepping the row through the pipeline's own
    impute -> scale stages (not refit, not approximated) so the numbers
    are exactly what the fitted model actually used.
    """
    pipeline = trained.model
    clf = pipeline.named_steps.get("clf")
    feature_columns = trained.feature_columns
    X = std_row[feature_columns].to_frame().T.apply(pd.to_numeric, errors="coerce")
    X_imputed = pipeline.named_steps["impute"].transform(X)

    if hasattr(clf, "coef_"):
        method = "logistic_coef_times_scaled_value"
        X_scaled = pipeline.named_steps["scale"].transform(X_imputed)
        proba_manual = float(clf.predict_proba(X_scaled)[:, 1][0])
        coefs = clf.coef_[0]
        raw_contribs = coefs * X_scaled[0]
        per_feature_coef = [float(c) for c in coefs]
    elif hasattr(clf, "get_booster"):
        # Native TreeSHAP, built into xgboost itself - NOT the separate
        # `shap` package (not installed). Returns additive per-feature
        # contributions in margin (log-odds) space; last column is the
        # bias/expected-value term. sum(contribs) + bias == margin, whose
        # sigmoid == predict_proba - asserted below, not assumed.
        import xgboost as xgb
        method = "xgboost_native_treeshap_pred_contribs"
        dmat = xgb.DMatrix(X_imputed, feature_names=feature_columns)
        contribs = clf.get_booster().predict(dmat, pred_contribs=True)[0]
        raw_contribs = contribs[:-1]
        bias = float(contribs[-1])
        margin = float(raw_contribs.sum()) + bias
        proba_manual = float(1.0 / (1.0 + np.exp(-margin)))
        per_feature_coef = [None] * len(feature_columns)
    else:
        raise NotImplementedError(
            f"signed per-row attribution is not implemented for clf type "
            f"{type(clf)} - only LogisticRegression (coef_) and XGBoost "
            f"(get_booster/pred_contribs) are handled."
        )

    proba_pipeline = float(pipeline.predict_proba(X)[:, 1][0])
    assert abs(proba_manual - proba_pipeline) < 1e-4, (
        f"manual pipeline replay ({proba_manual}) disagrees with pipeline.predict_proba "
        f"({proba_pipeline}) via method={method} - attribution math does not match the "
        f"real model, do not trust it"
    )

    layers = []
    for i, col in enumerate(feature_columns):
        name = col[:-2] if col.endswith("_z") else col
        raw_value = raw_row.get(name)
        raw_value = None if (raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value))) else float(raw_value)
        z_val = std_row.get(col)
        was_imputed = pd.isna(z_val) if col.endswith("_z") else False
        layers.append({
            "name": name,
            "raw_value": raw_value,
            "z_score_used_by_model": None if was_imputed else (float(z_val) if not pd.isna(z_val) else None),
            "was_imputed": bool(was_imputed),
            "coef": per_feature_coef[i],
            "signed_contribution": float(raw_contribs[i]),
            "contribution_method": method,
        })
    layers.sort(key=lambda l: abs(l["signed_contribution"]), reverse=True)
    return {"probability": round(proba_manual, 4), "layers": layers, "contribution_method": method}


def _select_ticker_row(std_df: pd.DataFrame, ticker: str) -> pd.Series:
    sub = std_df[std_df["ticker"].str.upper() == ticker.upper()]
    if sub.empty:
        return None
    return sub.sort_values("trade_date").iloc[-1]


def _select_auto_row(std_df: pd.DataFrame, raw_df: pd.DataFrame) -> pd.Series:
    """Most recent trade_date's row with the highest vol_oi (a proxy for
    "active options flow today") - deterministic from live data, not
    cherry-picked."""
    latest_date = std_df["trade_date"].max()
    todays = std_df[std_df["trade_date"] == latest_date].reset_index(drop=True)
    raw_todays = raw_df[raw_df["trade_date"] == pd.Timestamp(latest_date).date()]
    merged = todays.merge(raw_todays[["ticker", "vol_oi"]].rename(columns={"vol_oi": "_raw_vol_oi"}),
                           on="ticker", how="left")
    merged = merged.sort_values("_raw_vol_oi", ascending=False).reset_index(drop=True)
    return merged.iloc[0].drop(labels=["_raw_vol_oi"])


def _select_conflict_row(std_df: pd.DataFrame, models: dict, raw_df: pd.DataFrame,
                          horizon: int, threshold: float = 0.15) -> pd.Series:
    """
    Scans every row for a real conflict: top positive AND top negative
    signed (non-imputed) contribution both exceed `threshold` in magnitude
    - i.e. two real layers are genuinely pulling in opposite directions,
    not "one strong layer and a bunch of near-zero noise."
    """
    trained = models[horizon]
    best_row, best_spread = None, -1.0
    for idx, row in std_df.iterrows():
        raw_match = raw_df[(raw_df["ticker"] == row["ticker"]) &
                            (raw_df["trade_date"] == pd.Timestamp(row["trade_date"]).date())]
        if raw_match.empty:
            continue
        try:
            result = _row_contributions(trained, row, raw_match.iloc[0])
        except Exception:
            continue
        real_layers = [l for l in result["layers"] if not l["was_imputed"]]
        pos = [l for l in real_layers if l["signed_contribution"] > 0]
        neg = [l for l in real_layers if l["signed_contribution"] < 0]
        if not pos or not neg:
            continue
        top_pos = max(pos, key=lambda l: l["signed_contribution"])
        top_neg = min(neg, key=lambda l: l["signed_contribution"])
        if top_pos["signed_contribution"] < threshold or -top_neg["signed_contribution"] < threshold:
            continue
        spread = top_pos["signed_contribution"] - top_neg["signed_contribution"]
        if spread > best_spread:
            best_spread = spread
            best_row = row
    return best_row


_POLYGON_FALLBACK_NOTE = (
    "POLYGON_FALLBACK: ai_short_calls_log has no row for this ticker. "
    "Score computed from polygon_market_daily only. Options features "
    "(vol_oi, otm_pct, days_out, conviction_score, gamma_score, "
    "dark_pool_score, squeeze_score, sector_heat_score) not available; "
    "imputed with training-set median by the fitted pipeline's "
    "SimpleImputer(strategy='median'). Probability score is real but "
    "reflects only market/volume features, not options positioning."
)


def _polygon_fallback_score(ticker: str, models: dict, entries: dict) -> dict:
    """
    Minimal probability score for a ticker absent from ai_short_calls_log.
    Reads polygon_market_daily for market features; all options features
    are NaN (imputed by the fitted pipeline). No per-layer TreeSHAP
    attribution is computed (requires a full population for z-scores).
    Returns a signed, logged envelope with polygon_fallback=True and a
    prominent disclosure warning.
    """
    from data_snapshot import build_single_row_for_ticker
    raw_df = build_single_row_for_ticker(ticker)
    if raw_df.empty:
        return {"error": f"polygon_market_daily has no rows for ticker={ticker!r} — "
                          "cannot score this ticker via either ai_short_calls_log "
                          "or polygon_market_daily fallback"}

    today = _et_today()
    headline_h = _nearest_horizon_to_friday(today)
    if headline_h not in models:
        headline_h = max(models.keys())

    trained = models[headline_h]
    feature_cols = trained.feature_columns

    row = raw_df.iloc[0]
    X = pd.DataFrame([{col: (float(row[col])
                             if col in row.index and not pd.isna(row[col])
                             else float("nan"))
                        for col in feature_cols}])

    all_horizons = {}
    for h, m in models.items():
        try:
            p = float(m.model.predict_proba(X)[:, 1][0])
            all_horizons[str(h)] = {"probability": round(p, 4), "top_layer": None}
        except Exception as _he:
            all_horizons[str(h)] = {"error": str(_he)}

    headline_prob = all_horizons.get(str(headline_h), {}).get("probability")

    trade_date = row["trade_date"]
    warnings_list = [_POLYGON_FALLBACK_NOTE]
    if pd.Timestamp(trade_date).date() != today:
        warnings_list.append(
            f"STALENESS: polygon_market_daily last row is {trade_date}, not today "
            f"({today}) — most recent available market data used."
        )

    payload = {
        "ticker": ticker,
        "strike": None,
        "expiry": None,
        "as_of_date": str(trade_date),
        "query_generated_at": str(today),
        "probability_score": headline_prob,
        "probability_horizon_days": headline_h,
        "contributing_layer": None,
        "contributing_layer_raw_value": None,
        "contributing_layer_signed_contribution": None,
        "model_version": model_registry.version_string_for_entries(entries),
        "pit_status": "live_unsettled_polygon_only",
        "pit_status_note": (
            "Polygon-only fallback: ai_short_calls_log has 0 rows. Score uses "
            "polygon_market_daily market features only; options features imputed "
            "with training-set medians. Not written to aiem_probability_engine_predictions."
        ),
        "polygon_fallback": True,
        "all_horizons": all_horizons,
        "layers": [],
        "layer_conflict": None,
        "warnings": warnings_list,
        "mode": "ticker_polygon_fallback",
    }

    envelope = sign_payload(payload)
    verify_result = verify_payload(envelope, max_age_seconds=3600)
    _log_live_query(ticker, trade_date, "ticker_polygon_fallback",
                    payload["model_version"], payload, envelope, verify_result)
    return {"envelope": envelope, "self_verify": verify_result}


def _select_null_row(std_df: pd.DataFrame, raw_df: pd.DataFrame) -> pd.Series:
    """Most recent row where at least one Tier-2 layer is NULL in the
    SOURCE table (not just imputed downstream) - per config.py's verified
    coverage, gamma_score (3%) and sector_heat_score (<1%) make this true
    for nearly any row."""
    null_mask = raw_df[TIER2_FEATURE_COLUMNS].isna().any(axis=1)
    candidates = raw_df[null_mask].sort_values("trade_date")
    if candidates.empty:
        return None
    target = candidates.iloc[-1]
    sub = std_df[(std_df["ticker"] == target["ticker"]) &
                 (std_df["trade_date"] == pd.Timestamp(target["trade_date"]))]
    return sub.iloc[-1] if not sub.empty else None


def run_live_query(ticker: str = None, mode: str = "auto") -> dict:
    raw_df = build_dataset()
    if raw_df.empty:
        if ticker and mode == "ticker":
            _models_early, _entries_early = _load_latest_models()
            if not _models_early:
                return {"error": "no trained models found via model_registry.get_latest() - run train.py first"}
            return _polygon_fallback_score(ticker, _models_early, _entries_early)
        return {"error": "no dataset available from ai_short_calls_log/polygon_market_daily"}
    std_df = add_standardized_features(raw_df)

    models, entries = _load_latest_models()
    if not models:
        return {"error": "no trained models found via model_registry.get_latest() - run train.py first"}

    if mode == "ticker":
        target = _select_ticker_row(std_df, ticker)
        if target is None:
            return _polygon_fallback_score(ticker, models, entries)
    elif mode == "find-conflict":
        headline_h = 2 if 2 in models else next(iter(models))
        target = _select_conflict_row(std_df, models, raw_df, headline_h)
        if target is None:
            return {"error": "no row currently found where two non-imputed layers disagree past threshold"}
    elif mode == "find-null":
        target = _select_null_row(std_df, raw_df)
        if target is None:
            return {"error": "no row currently found with a null Tier-2 layer (unexpected given documented coverage)"}
    else:
        target = _select_auto_row(std_df, raw_df)

    ticker_resolved = target["ticker"]
    trade_date = pd.Timestamp(target["trade_date"]).date()
    raw_match = raw_df[(raw_df["ticker"] == ticker_resolved) &
                        (raw_df["trade_date"] == trade_date)]
    if raw_match.empty:
        return {"error": "internal: selected row has no matching raw_df record"}
    raw_row = raw_match.iloc[0]

    meta = _load_row_metadata(ticker_resolved, trade_date)

    today = _et_today()
    headline_h = _nearest_horizon_to_friday(today)
    if headline_h not in models:
        headline_h = max(models.keys())

    horizon_results = {}
    for h, trained in models.items():
        try:
            horizon_results[h] = _row_contributions(trained, target, raw_row)
        except NotImplementedError as e:
            horizon_results[h] = {"error": str(e)}

    headline = horizon_results.get(headline_h, {})
    headline_layers = headline.get("layers", [])
    top_layer = headline_layers[0] if headline_layers else None

    warnings = []
    if trade_date != today:
        warnings.append(
            f"STALENESS: selected row's trade_date ({trade_date}) is not today ({today}) - "
            f"this is the most recent row this pipeline has for this ticker/mode, not a "
            f"same-day live scan. Treat as 'AIEM's last scored view of this ticker', not "
            f"'as of this exact moment'."
        )
    if today.weekday() >= 4:
        warnings.append(
            f"'By Friday's close' could not be mapped to a horizon <=4 trading days for "
            f"as_of weekday={today.strftime('%A')} - falling back to the {headline_h}d "
            f"horizon (next Friday), not literally 'this Friday'."
        )
    imputed_layers = [l["name"] for l in headline_layers if l["was_imputed"]]
    if imputed_layers:
        warnings.append(
            f"IMPUTED (not real data): {imputed_layers} were NaN in the source row and "
            f"were median-imputed by the trained pipeline's SimpleImputer(strategy='median') "
            f"- the model does NOT silently pretend these are zero/neutral, it substitutes "
            f"the training-set median and this reduces reported confidence (see "
            f"predict.py's tier2_available penalty for the equivalent shadow-log path)."
        )

    # Row-specific weighting explanation: which layers actively DISAGREE
    # (opposite-signed, non-imputed contributions) for THIS row, and how
    # the net sum resolves the conflict. Always computed (not just in
    # find-conflict mode) since any row can have disagreeing layers and
    # the auditor should see the real math, not just the single winner.
    layer_conflict = None
    real_layers = [l for l in headline_layers if not l["was_imputed"]]
    pos_layers = sorted([l for l in real_layers if l["signed_contribution"] > 0],
                         key=lambda l: l["signed_contribution"], reverse=True)
    neg_layers = sorted([l for l in real_layers if l["signed_contribution"] < 0],
                         key=lambda l: l["signed_contribution"])
    if pos_layers and neg_layers:
        top_pos, top_neg = pos_layers[0], neg_layers[0]
        net = sum(l["signed_contribution"] for l in real_layers)
        layer_conflict = {
            "disagreement_detected": True,
            "top_positive_layer": top_pos["name"],
            "top_positive_signed_contribution": top_pos["signed_contribution"],
            "top_negative_layer": top_neg["name"],
            "top_negative_signed_contribution": top_neg["signed_contribution"],
            "net_of_all_real_layer_contributions": round(net, 4),
            "explanation": (
                f"'{top_pos['name']}' pushes the score UP (contribution "
                f"{top_pos['signed_contribution']:+.4f}) while '{top_neg['name']}' pushes it "
                f"DOWN (contribution {top_neg['signed_contribution']:+.4f}) for this specific "
                f"row - these are the two strongest disagreeing, non-imputed layers. The model "
                f"does not pick a 'winner' between them; it sums ALL {len(real_layers)} "
                f"real layers' contributions plus the model's bias term into one margin, which "
                f"is then passed through a sigmoid to get probability_score. "
                f"'contributing_layer' above is whichever single layer has the LARGEST-"
                f"magnitude contribution (here: "
                f"'{top_layer['name'] if top_layer else None}'), not necessarily the one that "
                f"'wins' the direction - a large single contribution can still be outweighed "
                f"by several smaller same-signed ones summing together."
            ),
        }
    elif real_layers:
        layer_conflict = {
            "disagreement_detected": False,
            "explanation": (
                "All real (non-imputed) layers for this row agree on direction (all positive "
                "or all negative) - there is no genuine layer-vs-layer conflict to disclose "
                "for this specific query."
            ),
        }

    payload = {
        "ticker": ticker_resolved,
        "strike": meta.get("strike"),
        "expiry": meta.get("expiry"),
        "as_of_date": str(trade_date),
        "query_generated_at": str(today),
        "probability_score": headline.get("probability"),
        "probability_horizon_days": headline_h,
        "contributing_layer": top_layer["name"] if top_layer else None,
        "contributing_layer_raw_value": top_layer["raw_value"] if top_layer else None,
        "contributing_layer_signed_contribution": top_layer["signed_contribution"] if top_layer else None,
        "model_version": model_registry.version_string_for_entries(entries),
        "pit_status": "live_unsettled",
        "pit_status_note": (
            "This is a live 'right now' query scored with model_registry.get_latest() "
            "(the latest trained artifact on disk), NOT model_registry.get_as_of() - there "
            "is no fixed past signal_date for this query to leak against (see predict.py's "
            "own docstring on this distinction), so 'pit_safe' vs 'leaked' does not apply. "
            "This row is NOT written to aiem_probability_engine_predictions (the pit_safe/"
            "leaked shadow log) and is NOT a 'corrected' historical re-score - it is its own "
            "honest third category, logged separately in "
            f"{TABLE} for durability."
        ),
        "probability_scope_disclosure": (
            f"probability_score is P(underlying close is HIGHER than {trade_date}'s close "
            f"after {headline_h} trading day(s)) - an ANY-MAGNITUDE up-move probability "
            f"(see data_snapshot.py's label: fut_price > base_price), NOT a >2%-move "
            f"probability and NOT an option-ITM probability at the given strike/expiry."
        ),
        "strike_expiry_disclosure": (
            "strike/expiry are echoed from ai_short_calls_log as informational_inputs only "
            "- they are not features the model consumes; the probability score is about the "
            "UNDERLYING's price direction, not this specific contract's moneyness."
        ),
        "layer_terminology_disclosure": (
            "this package's docs call these the '9 conviction layers'; the trained model "
            "actually consumes 14 standardized features (10 Tier-1 technical/structural + "
            "4 Tier-2 options-positioning); layer9_statistical_edge (statistical_score/"
            "regime/flags) is a separate report OVERLAY, not one of these 14 model features, "
            "and is intentionally excluded from this live-query endpoint."
        ),
        "all_horizons": {
            str(h): {
                "probability": r.get("probability"),
                "top_layer": r["layers"][0]["name"] if r.get("layers") else None,
            } for h, r in horizon_results.items()
        },
        "layers": headline_layers,
        "layer_conflict": layer_conflict,
        "warnings": warnings,
        "mode": mode,
    }

    envelope = sign_payload(payload)
    verify_result = verify_payload(envelope, max_age_seconds=3600)

    _log_live_query(ticker_resolved, trade_date, mode, payload["model_version"],
                     payload, envelope, verify_result)

    return {"envelope": envelope, "self_verify": verify_result}


def _log_live_query(ticker, as_of_date, mode, model_version, request_payload,
                     envelope, verify_result) -> None:
    ensure_table()
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO {TABLE}
                    (ticker, as_of_date, mode, model_version, pit_status,
                     request_json, envelope_json, verified, verify_reason)
                VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s)
            """, (
                ticker, as_of_date, mode, model_version, "live_unsettled",
                json.dumps(request_payload, default=str), json.dumps(envelope, default=str),
                verify_result.get("verified"), verify_result.get("reason"),
            ))
        conn.commit()


def verify_stored_live_query(row_id: int, max_age_seconds: int = 10 ** 9) -> dict:
    """Standalone re-verification, meant to be run by an auditor from a
    SEPARATE process/session than the one that generated the row - reads
    the envelope back from the DB and recomputes the HMAC independently."""
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {TABLE} WHERE id = %s", (row_id,))
            row = cur.fetchone()
    if not row:
        return {"error": f"no row id={row_id} in {TABLE}"}
    envelope = row["envelope_json"]
    result = verify_payload(envelope, max_age_seconds=max_age_seconds)
    result["row_id"] = row_id
    result["ticker"] = row["ticker"]
    result["as_of_date"] = str(row["as_of_date"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--find-conflict", action="store_true")
    parser.add_argument("--find-null", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    # data_snapshot.py and other shared modules print progress lines
    # (e.g. "[data_snapshot] built N rows...") straight to stdout. In
    # --json mode a caller (main.py's subprocess route) must be able to
    # json.loads(proc.stdout) with nothing else mixed in, so swallow those
    # incidental prints and emit ONLY the final JSON on real stdout.
    if args.json:
        _real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            if args.ticker:
                result = run_live_query(ticker=args.ticker, mode="ticker")
            elif args.find_conflict:
                result = run_live_query(mode="find-conflict")
            elif args.find_null:
                result = run_live_query(mode="find-null")
            else:
                result = run_live_query(mode="auto")
        finally:
            sys.stdout = _real_stdout
        print(json.dumps(result, default=str))
    else:
        if args.ticker:
            result = run_live_query(ticker=args.ticker, mode="ticker")
        elif args.find_conflict:
            result = run_live_query(mode="find-conflict")
        elif args.find_null:
            result = run_live_query(mode="find-null")
        else:
            result = run_live_query(mode="auto")
        print(json.dumps(result, indent=2, default=str))
