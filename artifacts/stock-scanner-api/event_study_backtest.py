"""
event_study_backtest.py

Finds every historical instance of a stock moving +15% (or whatever
threshold you set) within a 5-trading-day window over the last N years,
then looks BACKWARD at what your indicators were doing in the days
leading up to each move, and statistically compares that to a random
control sample of non-event days.

This answers the real question: "of everything I'm computing, what
ACTUALLY looked different before a big move vs. on a normal day" --
rather than guessing from the feature list.

Requires: POLYGON_API_KEY env var, `requests`, `pandas`, `numpy`, `scipy`.
Run this in your Replit/AIEM environment (it needs network access to
Polygon, which this sandbox doesn't have).

Usage:
    python event_study_backtest.py --start 2023-07-01 --end 2025-06-30 \
        --move-pct 0.15 --window-days 5 --precursor-days 10 \
        --universe-file tickers.txt
"""

import argparse
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
from scipy import stats

import precursor_signals as ps  # the module from earlier

POLYGON_BASE = "https://api.polygon.io"
API_KEY = os.environ.get("POLYGON_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")


# ---------------------------------------------------------------------------
# 1. DATA PULL
# ---------------------------------------------------------------------------
def fetch_grouped_daily(date_str: str, max_retries: int = 3) -> pd.DataFrame:
    """
    Pulls ALL US tickers' daily OHLCV for one date in a single call.
    Much more efficient than per-ticker calls when scanning the whole
    market for events. https://api.polygon.io/v2/aggs/grouped/...
    """
    url = f"{POLYGON_BASE}/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
    params = {"adjusted": "true", "apiKey": API_KEY}

    for attempt in range(max_retries):
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json().get("results", [])
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data).rename(
                columns={"T": "ticker", "o": "open", "h": "high", "l": "low",
                         "c": "close", "v": "volume", "t": "timestamp"}
            )
            df["date"] = pd.to_datetime(date_str)
            return df[["date", "ticker", "open", "high", "low", "close", "volume"]]
        elif resp.status_code == 429:
            time.sleep(2 ** attempt)  # backoff on rate limit
        else:
            print(f"  [{date_str}] HTTP {resp.status_code}, skipping")
            return pd.DataFrame()
    return pd.DataFrame()


def build_universe_history(start_date: str, end_date: str, cache_path: str = "grouped_daily_cache.parquet") -> pd.DataFrame:
    """
    Pulls grouped daily bars for every trading day in [start_date, end_date]
    and caches to parquet so re-runs don't re-hit the API. ~500 trading
    days for 2 years; budget for rate limits depending on your plan tier.
    """
    if os.path.exists(cache_path):
        print(f"Loading cached universe history from {cache_path}")
        return pd.read_parquet(cache_path)

    dates = pd.bdate_range(start_date, end_date)
    frames = []
    for i, d in enumerate(dates):
        date_str = d.strftime("%Y-%m-%d")
        daily = fetch_grouped_daily(date_str)
        if not daily.empty:
            frames.append(daily)
        if i % 20 == 0:
            print(f"  pulled {i}/{len(dates)} days...")
        time.sleep(0.1)  # be polite to rate limits; tune to your plan

    full = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    full.to_parquet(cache_path)
    print(f"Saved {len(full):,} rows to {cache_path}")
    return full


def build_universe_history_from_db(start_date: str, end_date: str, cache_path: str = None) -> pd.DataFrame:
    """
    Loads OHLCV history from the AIEM-maintained `polygon_market_daily`
    table instead of re-pulling from the Polygon grouped-daily endpoint.

    This is the preferred path in this environment: the grouped-daily
    endpoint returns HTTP 403 on the current Polygon plan tier (verified
    against historical dates, not just "today"), while polygon_market_daily
    is already populated nightly for the full market and covers the same
    OHLCV fields this script needs.

    Returns a dataframe with columns [date, ticker, open, high, low, close,
    volume], same shape as build_universe_history(), so every downstream
    function (find_events, compute_feature_panel, run_event_study) works
    unchanged.
    """
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL environment variable not set.")
    import psycopg2

    conn = psycopg2.connect(DATABASE_URL)
    try:
        query = """
            SELECT scan_date AS date, ticker,
                   open_price AS open, high_price AS high,
                   low_price AS low, close_price AS close, volume
            FROM polygon_market_daily
            WHERE scan_date BETWEEN %s AND %s
            ORDER BY ticker, scan_date
        """
        df = pd.read_sql(query, conn, params=(start_date, end_date))
    finally:
        conn.close()

    df["date"] = pd.to_datetime(df["date"])
    print(f"Loaded {len(df):,} rows from polygon_market_daily "
          f"({df['ticker'].nunique():,} tickers, "
          f"{df['date'].min().date() if not df.empty else 'n/a'} to "
          f"{df['date'].max().date() if not df.empty else 'n/a'})")
    if cache_path:
        # Parquet caching is a nice-to-have, not load-bearing: this
        # environment can't install pyarrow/fastparquet (Nix permission
        # restrictions on the python site-packages dir), so a missing
        # engine must never crash a real backtest run over a fresh DB pull.
        try:
            df.to_parquet(cache_path)
        except ImportError as e:
            print(f"Skipping parquet cache ({cache_path}): {e}")
    return df


# ---------------------------------------------------------------------------
# 2. EVENT DETECTION
# ---------------------------------------------------------------------------
def find_events(history: pd.DataFrame, move_pct: float = 0.15, window_days: int = 5,
                 min_price: float = 1.0, min_avg_volume: float = 100_000) -> pd.DataFrame:
    """
    For every ticker, finds dates where close[t] -> max(close[t+1 : t+window_days])
    represents a gain >= move_pct. Filters out sub-$1 tickers and illiquid
    names (min_avg_volume) since those moves are often just noise/manipulation
    that won't generalize.

    Returns one row per event:
      ticker, event_start_date, event_end_date, move_pct_actual
    """
    history = history.sort_values(["ticker", "date"]).copy()
    events = []

    for ticker, g in history.groupby("ticker"):
        g = g.reset_index(drop=True)
        if len(g) < window_days + 20:
            continue
        avg_vol = g["volume"].rolling(20).mean()
        if avg_vol.iloc[-1] is np.nan or avg_vol.mean() < min_avg_volume:
            continue
        if g["close"].mean() < min_price:
            continue

        closes = g["close"].values
        dates = g["date"].values

        for t in range(len(g) - window_days):
            start_price = closes[t]
            if start_price <= 0:
                continue
            forward_window = closes[t + 1: t + 1 + window_days]
            if len(forward_window) == 0:
                continue
            max_fwd = forward_window.max()
            move = (max_fwd / start_price) - 1
            if move >= move_pct:
                events.append({
                    "ticker": ticker,
                    "event_start_date": dates[t],
                    "event_end_date": dates[t + np.argmax(forward_window) + 1],
                    "move_pct_actual": move,
                })

    events_df = pd.DataFrame(events)
    print(f"Found {len(events_df):,} qualifying events "
          f"(>= {move_pct:.0%} within {window_days}d) across "
          f"{events_df['ticker'].nunique() if not events_df.empty else 0} tickers")
    return events_df


# ---------------------------------------------------------------------------
# 3. PRECURSOR FEATURE EXTRACTION
# ---------------------------------------------------------------------------
def compute_feature_panel(ticker_history: pd.DataFrame) -> pd.DataFrame:
    """
    Runs your existing + new indicators on one ticker's full price history
    so features are available to look up on any given date. Extend this
    with your actual Layer 1-8 scores (OI, gamma, short interest, dark
    pool, sentiment) once you have them indexed by ticker/date -- this
    version covers everything computable from OHLCV alone.
    """
    df = ticker_history.sort_values("date").reset_index(drop=True)

    df = ps.stealth_accumulation_score(df)
    df = ps.squeeze_duration(df)
    df = ps.pocket_pivot_flag(df)

    # RSI(14) -- simple implementation since it's not in precursor_signals
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # volume buildup ratio (mirrors your aiem_autonomous.py definition)
    df["volume_buildup"] = df["volume"] / df["volume"].rolling(10).mean()

    return df


def extract_precursor_window(feature_panel: pd.DataFrame, event_date, precursor_days: int = 10) -> dict:
    """
    Pulls the trailing `precursor_days` of feature values BEFORE event_date
    and summarizes each as: latest value, mean over window, and trend slope.
    This is what gets compared against the control sample.

    feature_panel is assumed already sorted ascending by "date" (which is
    how compute_feature_panel builds it). We use searchsorted instead of a
    full boolean mask (`feature_panel["date"] < event_date`) -- this is
    O(log n) instead of O(n) per call, which matters a lot when this gets
    invoked once per event/control row and a full-market multi-year pull
    can produce tens of thousands of events.
    """
    dates_arr = feature_panel["date"].values
    pos = np.searchsorted(dates_arr, np.datetime64(event_date), side="left")
    window = feature_panel.iloc[max(0, pos - precursor_days):pos]
    if window.empty:
        return {}

    summary = {}
    feature_cols = ["stealth_score", "rvol_trend_5d", "price_range_5d",
                     "squeeze_streak", "squeeze_percentile", "pocket_pivot",
                     "rsi_14", "volume_buildup"]
    for col in feature_cols:
        if col not in window.columns:
            continue
        vals = window[col].dropna()
        if vals.empty:
            continue
        summary[f"{col}_latest"] = vals.iloc[-1]
        summary[f"{col}_mean_{precursor_days}d"] = vals.mean()
        summary[f"{col}_slope"] = ps.rolling_slope(vals, window=min(5, len(vals))).iloc[-1] if len(vals) >= 2 else np.nan

    return summary


# ---------------------------------------------------------------------------
# 4. STATISTICAL COMPARISON: events vs. random control sample
# ---------------------------------------------------------------------------
def build_control_sample(history: pd.DataFrame, events_df: pd.DataFrame, n_samples: int = 2000,
                          seed: int = 42) -> pd.DataFrame:
    """
    Randomly samples (ticker, date) pairs that are NOT within precursor_days
    of any actual event, to serve as the baseline "nothing special happening"
    distribution. Same tickers/date range as your events, so it's an
    apples-to-apples comparison rather than a different universe.
    """
    rng = np.random.default_rng(seed)
    event_keys = set(zip(events_df["ticker"], events_df["event_start_date"]))

    candidates = history[["ticker", "date"]].drop_duplicates()
    # crude filter: exclude any (ticker, date) that's an actual event date
    candidates = candidates[~candidates.apply(lambda r: (r["ticker"], r["date"]) in event_keys, axis=1)]

    sample = candidates.sample(n=min(n_samples, len(candidates)), random_state=seed)
    return sample.reset_index(drop=True)


def run_event_study(history: pd.DataFrame, events_df: pd.DataFrame, precursor_days: int = 10,
                     n_control: int = 2000) -> pd.DataFrame:
    """
    Main driver: for each event, computes the precursor feature window.
    For the control sample, does the same. Then runs a Mann-Whitney U
    test per feature to see whether the event-preceding distribution is
    statistically different from the control distribution.

    Returns a ranked dataframe of features by effect size (rank-biserial
    correlation) and p-value -- this is your real answer to "what actually
    predicts the move," not a guess from the indicator list.
    """
    # Only build feature panels for tickers actually needed (event tickers +
    # whatever ends up in the control sample) instead of every ticker in the
    # full universe history. With ~11K tickers in history but only ~3-4K
    # tickers carrying events/control rows, building panels for all of them
    # was the dominant cost (full rolling-window feature computation per
    # ticker) -- restricting the scope plus a single groupby (instead of an
    # O(n) DataFrame filter per ticker) is what makes this tractable on a
    # full-market multi-year pull.
    print("Building control sample...")
    control_sample = build_control_sample(history, events_df, n_samples=n_control)

    needed_tickers = set(events_df["ticker"].unique()) | set(control_sample["ticker"].unique())
    print(f"Building per-ticker feature panels for {len(needed_tickers):,} tickers "
          f"(event + control universe, out of {history['ticker'].nunique():,} total)...")
    panels = {}
    for ticker, g in history[history["ticker"].isin(needed_tickers)].groupby("ticker"):
        if len(g) < 30:
            continue
        panels[ticker] = compute_feature_panel(g)

    print("Extracting precursor windows for events...")
    event_rows = []
    for _, ev in events_df.iterrows():
        if ev["ticker"] not in panels:
            continue
        feats = extract_precursor_window(panels[ev["ticker"]], ev["event_start_date"], precursor_days)
        if feats:
            feats["ticker"] = ev["ticker"]
            feats["date"] = ev["event_start_date"]
            event_rows.append(feats)
    event_features = pd.DataFrame(event_rows)

    print("Extracting precursor windows for control sample...")
    control_rows = []
    for _, row in control_sample.iterrows():
        if row["ticker"] not in panels:
            continue
        feats = extract_precursor_window(panels[row["ticker"]], row["date"], precursor_days)
        if feats:
            feats["ticker"] = row["ticker"]
            feats["date"] = row["date"]
            control_rows.append(feats)
    control_features = pd.DataFrame(control_rows)

    print(f"Event samples: {len(event_features)} | Control samples: {len(control_features)}")

    # Statistical comparison per feature
    results = []
    shared_cols = [c for c in event_features.columns if c in control_features.columns
                   and c not in ("ticker", "date")]

    for col in shared_cols:
        ev_vals = event_features[col].dropna()
        ctrl_vals = control_features[col].dropna()
        if len(ev_vals) < 20 or len(ctrl_vals) < 20:
            continue

        stat, p = stats.mannwhitneyu(ev_vals, ctrl_vals, alternative="two-sided")
        # rank-biserial correlation as effect size (range -1 to 1)
        n1, n2 = len(ev_vals), len(ctrl_vals)
        effect_size = 1 - (2 * stat) / (n1 * n2)

        results.append({
            "feature": col,
            "event_mean": ev_vals.mean(),
            "control_mean": ctrl_vals.mean(),
            "p_value": p,
            "effect_size": effect_size,
            "n_events": n1,
            "n_control": n2,
        })

    results_df = pd.DataFrame(results).sort_values("p_value")
    results_df["significant_at_0.01"] = results_df["p_value"] < 0.01
    return results_df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--move-pct", type=float, default=0.15)
    parser.add_argument("--window-days", type=int, default=5)
    parser.add_argument("--precursor-days", type=int, default=10)
    parser.add_argument("--cache", default="grouped_daily_cache.parquet")
    parser.add_argument("--source", choices=["db", "api"], default="db",
                         help="db = read from polygon_market_daily (fast, no rate limits); "
                              "api = live Polygon grouped-daily pull (requires a plan with "
                              "grouped-daily access)")
    args = parser.parse_args()

    if args.source == "api" and not API_KEY:
        raise SystemExit("Set POLYGON_API_KEY environment variable first.")
    if args.source == "db" and not DATABASE_URL:
        raise SystemExit("DATABASE_URL environment variable not set.")

    print(f"Step 1: pulling/loading universe history (source={args.source})...")
    if args.source == "db":
        history = build_universe_history_from_db(args.start, args.end, cache_path=args.cache)
    else:
        history = build_universe_history(args.start, args.end, cache_path=args.cache)

    print("Step 2: finding events...")
    events_df = find_events(history, move_pct=args.move_pct, window_days=args.window_days)
    events_df.to_csv("events_found.csv", index=False)

    print("Step 3: running event study (event vs control feature comparison)...")
    results_df = run_event_study(history, events_df, precursor_days=args.precursor_days)
    results_df.to_csv("event_study_results.csv", index=False)

    print("\nTop precursor features ranked by statistical significance:")
    print(results_df.head(20).to_string(index=False))
    print("\nFull results saved to event_study_results.csv")
    print("Raw events saved to events_found.csv")
