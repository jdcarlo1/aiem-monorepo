"""
data_snapshot.py - point-in-time dataset builder for the AIEM Probability
Engine.

Builds one row per (ticker, trade_date) labeled pick from ai_short_calls_log,
attaches Tier 1 + Tier 2 features (see config.py for what's real vs sparse),
and computes EXACT forward 1/2/3/4-trading-day labels/returns directly from
polygon_market_daily.close_price (never from the log's own t1/t3/t5 columns,
so horizons match the spec exactly instead of approximating).

Leakage safety: every feature is computed from polygon_market_daily rows
with scan_date <= trade_date (never later). Labels are intentionally
computed from scan_date > trade_date (that's the target, not a feature).
assert_no_future_leakage() from point_in_time_guard.py is run on the
feature slice for every row before it's added to the output.

Run directly for a demo:
    python data_snapshot.py
"""
import os
import sys
import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feature_engineering import compute_volume_trend, compute_ma_relative, encode_conviction
from point_in_time_guard import assert_no_future_leakage, LookaheadViolation

from config import DB_URL, HORIZONS, TIER1_FEATURE_COLUMNS, TIER2_FEATURE_COLUMNS


def _load_labeled_picks() -> pd.DataFrame:
    sql = """
        SELECT
            id, pick_id, trade_date, ticker, vol_oi, otm_pct, days_out,
            conviction, gamma_score, dark_pool_score, squeeze_score,
            sector_heat_score
        FROM ai_short_calls_log
        WHERE trade_date IS NOT NULL AND ticker IS NOT NULL
        ORDER BY trade_date ASC
    """
    with psycopg2.connect(DB_URL) as conn:
        return pd.read_sql_query(sql, conn)


def _load_market_history(tickers: list) -> pd.DataFrame:
    """
    One batched query for every ticker's full polygon_market_daily history,
    instead of one query per row (445 rows -> 1 query, not 445).
    """
    sql = """
        SELECT ticker, scan_date, close_price, volume, rvol, gap_pct
        FROM polygon_market_daily
        WHERE ticker = ANY(%s)
        ORDER BY ticker ASC, scan_date ASC
    """
    with psycopg2.connect(DB_URL) as conn:
        df = pd.read_sql_query(sql, conn, params=(tickers,))
    df["scan_date"] = pd.to_datetime(df["scan_date"]).dt.date
    return df


def _forward_labels(hist: pd.DataFrame, trade_date, horizons=HORIZONS) -> dict:
    """
    hist: this ticker's full history, sorted by scan_date ascending.
    Returns {label_Nd, ret_Nd} for each horizon using the Nth trading row
    strictly after trade_date. Returns NaN for any horizon that runs past
    the end of available history (never fabricated).
    """
    out = {}
    idx_rows = hist.index[hist["scan_date"] == trade_date]
    if len(idx_rows) == 0:
        for h in horizons:
            out[f"label_{h}d"] = np.nan
            out[f"ret_{h}d"] = np.nan
        return out

    pos = hist.index.get_loc(idx_rows[0])
    base_price = hist.iloc[pos]["close_price"]

    for h in horizons:
        fut_pos = pos + h
        if fut_pos >= len(hist) or base_price in (None, 0) or pd.isna(base_price):
            out[f"label_{h}d"] = np.nan
            out[f"ret_{h}d"] = np.nan
            continue
        fut_price = hist.iloc[fut_pos]["close_price"]
        if fut_price is None or pd.isna(fut_price):
            out[f"label_{h}d"] = np.nan
            out[f"ret_{h}d"] = np.nan
            continue
        ret_pct = (fut_price - base_price) / base_price * 100.0
        out[f"label_{h}d"] = 1 if fut_price > base_price else 0
        out[f"ret_{h}d"] = ret_pct
    return out


def _pit_features(hist: pd.DataFrame, trade_date) -> dict:
    """
    Point-in-time Tier-1 technical features, computed ONLY from rows with
    scan_date <= trade_date. Verified with assert_no_future_leakage().
    """
    pit_hist = hist[hist["scan_date"] <= trade_date].copy()

    if pit_hist.empty:
        return {"rvol": np.nan, "gap_pct": np.nan, "volume_trend_3d": np.nan,
                "volume_trend_5d": np.nan, "ma20_relative": np.nan}

    assert_no_future_leakage(pit_hist, as_of_date=trade_date, date_col="scan_date")

    last_row = pit_hist.iloc[-1]
    vt3 = compute_volume_trend(
        pit_hist.rename(columns={"scan_date": "date"}), trade_date, 3
    )
    vt5 = compute_volume_trend(
        pit_hist.rename(columns={"scan_date": "date"}), trade_date, 5
    )
    ma20 = compute_ma_relative(
        pit_hist.rename(columns={"scan_date": "date", "close_price": "close"}),
        trade_date, 20
    )
    return {
        "rvol": last_row.get("rvol", np.nan),
        "gap_pct": last_row.get("gap_pct", np.nan),
        "volume_trend_3d": vt3,
        "volume_trend_5d": vt5,
        "ma20_relative": ma20,
    }


def build_dataset() -> pd.DataFrame:
    picks = _load_labeled_picks()
    if picks.empty:
        print("[data_snapshot] no picks found in ai_short_calls_log")
        return pd.DataFrame()

    tickers = picks["ticker"].dropna().unique().tolist()
    market = _load_market_history(tickers)
    market_by_ticker = {t: g.reset_index(drop=True) for t, g in market.groupby("ticker")}

    rows = []
    leakage_violations = 0
    for _, pick in picks.iterrows():
        ticker = pick["ticker"]
        trade_date = pd.Timestamp(pick["trade_date"]).date()
        hist = market_by_ticker.get(ticker)

        if hist is None or hist.empty:
            continue

        try:
            pit_feat = _pit_features(hist, trade_date)
        except LookaheadViolation as e:
            leakage_violations += 1
            print(f"[data_snapshot] LEAKAGE GUARD TRIPPED for {ticker} {trade_date}: {e}")
            continue

        labels = _forward_labels(hist, trade_date)

        row = {
            "pick_id": pick.get("pick_id"),
            "ticker": ticker,
            "trade_date": trade_date,
            "vol_oi": pick.get("vol_oi", np.nan),
            "otm_pct": pick.get("otm_pct", np.nan),
            "days_out": pick.get("days_out", np.nan),
            "conviction_score": encode_conviction(pick.get("conviction")),
            "day_of_week": float(pd.Timestamp(trade_date).dayofweek),
            "gamma_score": pick.get("gamma_score", np.nan),
            "dark_pool_score": pick.get("dark_pool_score", np.nan),
            "squeeze_score": pick.get("squeeze_score", np.nan),
            "sector_heat_score": pick.get("sector_heat_score", np.nan),
            **pit_feat,
            **labels,
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    # Post-loop contamination-rate gate.
    # Scope: this gate is the implementation of the Task 1 post-loop fix approved
    # for data_snapshot.py. It is NOT "Decision 3" — Decision 3 was the scope
    # ruling that limited Task 1 to this file and excluded context.py/predict.py.
    # The 5% threshold is an implementation choice made within that approved scope;
    # it was not separately approved as a numbered decision. If the threshold needs
    # explicit sign-off, it should be assigned its own decision number before merge.
    #
    # Behaviour: if more than 5% of picks triggered the leakage guard, the training
    # data is likely corrupted (e.g. a mis-wired as_of_date, a bulk data import with
    # wrong dates, or a systematic lookback error). Returning a partially-contaminated
    # DataFrame silently would allow ML training on biased labels. We abort instead.
    # The 5% threshold is intentionally loose — a handful of edge-case tickers on a
    # bad weekend bar should not abort a full Sunday retrain — but a systematic error
    # affecting more than 1 in 20 picks should.
    # ERROR_CODE=DATA_SNAPSHOT_CONTAMINATION_RATE_EXCEEDED is greppable in logs.
    if leakage_violations > 0 and len(picks) > 0:
        _contamination_rate = leakage_violations / len(picks)
        print(
            f"[data_snapshot] contamination_rate={_contamination_rate:.1%} "
            f"({leakage_violations}/{len(picks)} picks triggered leakage guard)"
        )
        if _contamination_rate > 0.05:
            raise RuntimeError(
                f"DATA_SNAPSHOT_CONTAMINATION_RATE_EXCEEDED: "
                f"{leakage_violations}/{len(picks)} picks ({_contamination_rate:.1%}) "
                f"triggered LookaheadViolation. Threshold: 5%. "
                f"Dataset build aborted — training on contaminated data is not allowed."
            )

    print(f"[data_snapshot] built {len(df)} rows from {len(picks)} picks "
          f"({leakage_violations} dropped for leakage-guard trips)")
    return df


def _tier_coverage_report(df: pd.DataFrame) -> None:
    print("\n--- Tier / label coverage (non-null counts) ---")
    for col in TIER1_FEATURE_COLUMNS:
        if col in df.columns:
            print(f"  tier1  {col:22s} {df[col].notna().sum():4d} / {len(df)}")
    for col in TIER2_FEATURE_COLUMNS:
        if col in df.columns:
            print(f"  tier2  {col:22s} {df[col].notna().sum():4d} / {len(df)}")
    for h in HORIZONS:
        col = f"label_{h}d"
        if col in df.columns:
            print(f"  label  {col:22s} {df[col].notna().sum():4d} / {len(df)}")


if __name__ == "__main__":
    dataset = build_dataset()
    if dataset.empty:
        sys.exit(0)

    _tier_coverage_report(dataset)

    print("\n--- Sample rows (point-in-time feature values + timestamps) ---")
    sample_tickers = dataset["ticker"].drop_duplicates().head(3).tolist()
    for t in sample_tickers:
        sub = dataset[dataset["ticker"] == t].head(1)
        print(f"\nticker={t}")
        print(sub.to_string(index=False))
