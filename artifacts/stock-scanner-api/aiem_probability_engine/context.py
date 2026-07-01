"""
context.py - as-of-safe REPORT OVERLAYS for the AIEM Probability Engine.

Per architect review after T006 (see .local/session_plan.md T007): with only
9-11 unique trade dates in the training set, effective sample size cannot
support added model dimensionality, and wiring "current" regime/macro
functions (which fetch LIVE Yahoo data) into historical training rows would
inject present-day knowledge into the past - a lookahead bug of the same
class already found and fixed twice this session (calibration.py's
row-count split, walk_forward.py's row-count windows).

So none of the functions in this file are model inputs, and none are wired
into data_snapshot.py / features.py / train.py. Instead, this module builds
REPORT OVERLAYS - metadata attached to a final prediction row for a human to
read - computed strictly from data with date <= as_of_date (verified via
point_in_time_guard.assert_no_future_leakage on every slice):

  1. regime_tag_as_of()       - point-in-time-safe SPY trend/drawdown read,
                                 reusing market_regime_overlay.combine_regime_votes()
                                 with a trimmed SPY price history. No historical
                                 VIX series exists in this DB, so vix_indicator
                                 degrades to a documented neutral vote rather
                                 than silently omitting a signal. This is a
                                 NARROWER proxy than the live regime_detector.
                                 get_current_regime() (10 indicators incl. VIX,
                                 breadth, put/call, GARCH, macro cross-asset) -
                                 labeled as such in the output.

  2. liquidity_context_as_of() - Corwin-Schultz spread / order-flow-imbalance /
                                 Kyle's lambda computed from a point-in-time
                                 trimmed OHLCV slice. Deliberately bypasses
                                 microstructure_proxy.compute_microstructure_proxy(),
                                 whose own _fetch_ohlcv() always pulls the MOST
                                 RECENT window with no date cutoff - unsafe to
                                 call for a historical row.

  3. layer9_score_as_of()      - optional layer9_statistical_edge.compute_layer9_score()
                                 on the same point-in-time-trimmed slice. Pure
                                 in-process computation (no DB writes, no HTTP
                                 calls, per its own docstring), so safe once
                                 history is correctly trimmed. Returns None
                                 (not a neutral 50) when it cannot be computed,
                                 so callers can tell "not computed" from
                                 "computed, neutral."

  4. edge_after_cost()          - raw model edge (prob_up - 0.5) minus an
                                 estimated round-trip cost proxy derived from
                                 the liquidity overlay's spread estimate (2x
                                 half-spread). This is an approximation: we
                                 have no historical OPTIONS bid/ask to run
                                 slippage_model.estimate_slippage() against,
                                 only the underlying equity's spread proxy.

Run directly for a demo against real DB rows:
    python context.py
"""
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_regime_overlay import combine_regime_votes
from microstructure_proxy import corwin_schultz_spread, order_flow_imbalance, kyle_lambda
from point_in_time_guard import assert_no_future_leakage, LookaheadViolation

try:
    from layer9_statistical_edge import compute_layer9_score
    _LAYER9_AVAILABLE = True
except ImportError:
    _LAYER9_AVAILABLE = False

from config import DB_URL

MIN_SPY_DAYS_FOR_REGIME = 55   # trend_structure_indicator needs a 50d SMA


def _load_spy_history() -> pd.DataFrame:
    sql = """
        SELECT scan_date AS date, close_price AS close
        FROM polygon_market_daily
        WHERE ticker = 'SPY'
        ORDER BY scan_date ASC
    """
    with psycopg2.connect(DB_URL) as conn:
        df = pd.read_sql_query(sql, conn)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def _load_ticker_ohlcv(tickers: list) -> pd.DataFrame:
    """
    One batched query for full OHLCV history (open/high/low/close/volume) -
    unlike data_snapshot._load_market_history(), this keeps high/low, which
    the microstructure and layer9 overlays both require.
    """
    sql = """
        SELECT ticker, scan_date AS date, open_price AS open, high_price AS high,
               low_price AS low, close_price AS close, volume
        FROM polygon_market_daily
        WHERE ticker = ANY(%s)
        ORDER BY ticker ASC, scan_date ASC
    """
    with psycopg2.connect(DB_URL) as conn:
        df = pd.read_sql_query(sql, conn, params=(tickers,))
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def regime_tag_as_of(spy_hist: pd.DataFrame, as_of_date) -> dict:
    pit = spy_hist[spy_hist["date"] <= as_of_date].copy()
    if len(pit) < MIN_SPY_DAYS_FOR_REGIME:
        return {
            "regime_tag": "insufficient_history",
            "confidence": "low",
            "n_indicators_used": 0,
            "note": f"only {len(pit)} SPY trading days as of {as_of_date}, need >= {MIN_SPY_DAYS_FOR_REGIME}",
        }

    assert_no_future_leakage(pit, as_of_date=as_of_date, date_col="date")

    result = combine_regime_votes(
        vix_history=pd.Series(dtype=float),   # no historical VIX in this DB
        price_history=pit,
    )
    # combine_regime_votes() needs >=3 aligned votes to call "sit_out" or
    # "full_exposure" (see market_regime_overlay.py thresholds). With only
    # 2 indicators available historically (trend_structure + drawdown - no
    # VIX/breadth/put-call/GARCH/macro), those two states are STRUCTURALLY
    # UNREACHABLE here: the tag can only ever land on "reduce_exposure" or
    # "normal_exposure". Surfacing this explicitly so a reader never mistakes
    # "never saw full_exposure/sit_out in the backtest" for a market finding
    # rather than a data-availability ceiling.
    return {
        "regime_tag": result["recommendation"],
        "confidence": result["confidence"],
        "n_indicators_used": result["n_indicators_used"],
        "plain_language_summary": result["plain_language_summary"],
        "proxy_note": "SPY trend+drawdown only (no historical VIX/breadth/put-call/"
                       "GARCH/macro data available) - narrower than the live "
                       "regime_detector.get_current_regime()",
        "reachable_states_caveat": (
            "only 2/10 live indicators available historically -> 'full_exposure' "
            "and 'sit_out' cannot occur (both require >=3 aligned votes); only "
            "'reduce_exposure' or 'normal_exposure' are reachable"
        ),
    }


def liquidity_context_as_of(ticker_hist: pd.DataFrame, as_of_date, window: int = 5) -> dict:
    pit = ticker_hist[ticker_hist["date"] <= as_of_date].copy().sort_values("date")
    if len(pit) < window + 2:
        return {"error": "insufficient_history", "n_rows": len(pit)}

    assert_no_future_leakage(pit, as_of_date=as_of_date, date_col="date")

    spread = corwin_schultz_spread(pit, window=window)
    ofi = order_flow_imbalance(pit, window=window)
    lam = kyle_lambda(pit, window=min(20, len(pit) - 1)) if len(pit) > 6 else None

    if ofi is None:
        ofi_signal = "unknown"
    elif ofi > 0.20:
        ofi_signal = "buying_pressure"
    elif ofi < -0.20:
        ofi_signal = "selling_pressure"
    else:
        ofi_signal = "neutral"

    spread_bps = round(spread * 10000, 1) if spread is not None else None
    # 2x half-spread as a rough round-trip cost proxy, expressed in %.
    est_roundtrip_cost_pct = round(spread * 200, 3) if spread is not None else None

    # corwin_schultz_spread() internally clamps any negative raw estimate to
    # 0.0 ("noise artifact" per its own comment). A negative raw alpha is
    # common over short windows during whipsaw/volatile stretches (verified
    # empirically: every large-cap sample on 2026-06-08, a volatile day,
    # clamped to exactly 0.0). So spread_est == 0.0 is AMBIGUOUS - it can
    # mean "genuinely tight spread" OR "estimator floor hit" - never treat
    # 0.0 here as a confident "very liquid" signal.
    spread_possibly_floor_clamped = spread == 0.0

    return {
        "spread_est": spread,
        "spread_bps": spread_bps,
        "spread_possibly_floor_clamped": spread_possibly_floor_clamped,
        "ofi": ofi,
        "ofi_signal": ofi_signal,
        "kyle_lambda": lam,
        "est_roundtrip_cost_pct": est_roundtrip_cost_pct,
        "cost_note": "approximated from underlying-equity spread proxy (Corwin-Schultz); "
                     "no historical options bid/ask available for slippage_model.estimate_slippage(). "
                     "See spread_possibly_floor_clamped before trusting a 0.0 reading.",
    }


def layer9_score_as_of(ticker_hist: pd.DataFrame, as_of_date):
    """Returns a dict, or None if it cannot be computed (never a fake neutral score)."""
    if not _LAYER9_AVAILABLE:
        return None

    pit = ticker_hist[ticker_hist["date"] <= as_of_date].copy().sort_values("date")
    if len(pit) < 30:
        return None

    assert_no_future_leakage(pit, as_of_date=as_of_date, date_col="date")

    hist_df = pit.rename(columns={"close": "Close", "volume": "Volume",
                                   "high": "High", "low": "Low"})
    result = compute_layer9_score(ticker="__pit__", history_df=hist_df,
                                   lookback=min(60, len(pit) - 1))
    if result.get("error"):
        return None
    return {
        "statistical_score": result["statistical_score"],
        "regime": result["regime"],
        "flags": result["flags"],
    }


def edge_after_cost(prob_up: float, liquidity_ctx: dict) -> dict:
    """
    raw_edge_pct: how far the model's probability is from a coin flip (50%),
    in percentage points. NOT a return estimate.
    edge_after_cost_pct: raw_edge_pct minus the round-trip cost proxy - a
    sanity check, not a P&L forecast.
    """
    raw_edge_pct = round((prob_up - 0.5) * 100, 2)
    cost_pct = liquidity_ctx.get("est_roundtrip_cost_pct") if liquidity_ctx else None
    if cost_pct is None:
        return {
            "raw_edge_pct": raw_edge_pct,
            "edge_after_cost_pct": None,
            "note": "cost unavailable - liquidity context missing/insufficient history",
        }
    return {
        "raw_edge_pct": raw_edge_pct,
        "est_roundtrip_cost_pct": cost_pct,
        "edge_after_cost_pct": round(raw_edge_pct - cost_pct, 2),
    }


def build_context_overlays(rows: pd.DataFrame, prob_col: str = None) -> pd.DataFrame:
    """
    rows: DataFrame with at least ['ticker', 'trade_date'] columns (e.g. the
    output of data_snapshot.build_dataset() / features.add_standardized_features()).
    prob_col: optional column name holding a predicted prob_up, to also emit
    edge_after_cost. If omitted, only regime/liquidity/layer9 overlays are added.

    Returns a COPY of rows with overlay columns appended. These columns are
    for reporting only - never re-fed into train.py/features.py as model inputs.
    """
    if rows.empty:
        return rows.copy()

    tickers = rows["ticker"].dropna().unique().tolist()
    spy_hist = _load_spy_history()
    ticker_hist = _load_ticker_ohlcv(tickers)
    hist_by_ticker = {t: g.reset_index(drop=True) for t, g in ticker_hist.groupby("ticker")}

    out_rows = []
    for _, r in rows.iterrows():
        ticker = r["ticker"]
        as_of = pd.Timestamp(r["trade_date"]).date()
        thist = hist_by_ticker.get(ticker, pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]))

        try:
            regime = regime_tag_as_of(spy_hist, as_of)
        except LookaheadViolation as e:
            regime = {"regime_tag": "leakage_guard_tripped", "error": str(e)}

        try:
            liq = liquidity_context_as_of(thist, as_of)
        except LookaheadViolation as e:
            liq = {"error": f"leakage_guard_tripped: {e}"}

        try:
            l9 = layer9_score_as_of(thist, as_of)
        except LookaheadViolation as e:
            l9 = None

        row_out = dict(r)
        row_out["regime_tag"] = regime.get("regime_tag")
        row_out["regime_confidence"] = regime.get("confidence")
        row_out["liquidity_spread_bps"] = liq.get("spread_bps")
        row_out["liquidity_spread_floor_clamped"] = liq.get("spread_possibly_floor_clamped")
        row_out["liquidity_ofi_signal"] = liq.get("ofi_signal")
        row_out["est_roundtrip_cost_pct"] = liq.get("est_roundtrip_cost_pct")
        row_out["layer9_statistical_score"] = l9.get("statistical_score") if l9 else None
        row_out["layer9_regime"] = l9.get("regime") if l9 else None

        if prob_col and prob_col in rows.columns and pd.notna(r.get(prob_col)):
            eac = edge_after_cost(float(r[prob_col]), liq)
            row_out["edge_after_cost_pct"] = eac.get("edge_after_cost_pct")

        out_rows.append(row_out)

    return pd.DataFrame(out_rows)


if __name__ == "__main__":
    from data_snapshot import _load_labeled_picks

    picks = _load_labeled_picks()
    if picks.empty:
        print("[context] no picks found in ai_short_calls_log")
        sys.exit(0)

    sample = picks[["pick_id", "ticker", "trade_date"]].drop_duplicates("ticker").head(5).copy()
    print(f"--- Building context overlays for {len(sample)} sample (ticker, trade_date) rows ---")

    enriched = build_context_overlays(sample, prob_col=None)
    cols = ["ticker", "trade_date", "regime_tag", "regime_confidence",
            "liquidity_spread_bps", "liquidity_spread_floor_clamped", "liquidity_ofi_signal",
            "est_roundtrip_cost_pct", "layer9_statistical_score", "layer9_regime"]
    print(enriched[cols].to_string(index=False))

    print("\n--- edge_after_cost() demo (using a fabricated prob_up=0.62 for illustration only) ---")
    for _, r in sample.iterrows():
        as_of = pd.Timestamp(r["trade_date"]).date()
        spy_hist = _load_spy_history()
        thist = _load_ticker_ohlcv([r["ticker"]])
        liq = liquidity_context_as_of(thist, as_of)
        eac = edge_after_cost(0.62, liq)
        print(f"  {r['ticker']} {as_of}: {eac}")
