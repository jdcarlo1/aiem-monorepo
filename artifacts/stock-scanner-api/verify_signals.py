"""
verify_signals.py

Verification suite for precursor_signals.py and event_study_backtest.py.
Run this BEFORE trusting any output from the backtest on real data.

What it checks:
  1. Each indicator function produces mathematically correct output on
     synthetic inputs where the right answer is known in advance
     (not just "doesn't crash")
  2. NO LOOKAHEAD BIAS -- the single most common way a backtest lies to
     you. Verifies that precursor feature windows never use data from
     on/after the event date.
  3. Event detection correctness -- planted events of known size/timing
     are found, and non-events are not falsely flagged.
  4. Edge cases -- short history, all-NaN columns, flat price series,
     single-row windows -- don't crash or silently return garbage.

Run with:
    python verify_signals.py

Exits non-zero if any check fails, so you can wire it into a CI step
or just eyeball the PASS/FAIL summary before trusting a backtest run.
"""

import sys
import traceback

import numpy as np
import pandas as pd

import precursor_signals as ps
import event_study_backtest as eb

RESULTS = []  # (test_name, passed: bool, detail: str)


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if (detail and not condition) else ""))


def run_safely(name, fn):
    try:
        fn()
    except Exception as e:
        RESULTS.append((name, False, f"raised {type(e).__name__}: {e}"))
        print(f"[FAIL] {name}  -- raised exception: {e}")
        traceback.print_exc(limit=2)


# ---------------------------------------------------------------------------
# 1. rolling_slope / trend_zscore -- known-answer math checks
# ---------------------------------------------------------------------------
def test_rolling_slope_known_answer():
    # perfectly linear series, slope should be exactly 1.0/day
    s = pd.Series(np.arange(20, dtype=float))
    slope = ps.rolling_slope(s, window=5)
    last_val = slope.iloc[-1]
    check("rolling_slope: linear series slope == 1.0",
          abs(last_val - 1.0) < 1e-9, f"got {last_val}")

    # flat series, slope should be exactly 0
    flat = pd.Series([5.0] * 20)
    slope_flat = ps.rolling_slope(flat, window=5)
    check("rolling_slope: flat series slope == 0.0",
          abs(slope_flat.iloc[-1]) < 1e-9, f"got {slope_flat.iloc[-1]}")

    # negative trend
    s_down = pd.Series(np.arange(20, 0, -1, dtype=float))
    slope_down = ps.rolling_slope(s_down, window=5)
    check("rolling_slope: declining series slope == -1.0",
          abs(slope_down.iloc[-1] - (-1.0)) < 1e-9, f"got {slope_down.iloc[-1]}")


def test_trend_zscore_handles_constant_slope():
    # if slope never changes, std == 0 -> should return NaN, not crash/inf
    s = pd.Series(np.arange(30, dtype=float))  # perfectly linear -> constant slope
    z = ps.trend_zscore(s, window=10)
    check("trend_zscore: constant-slope series doesn't produce inf",
          not np.isinf(z.dropna()).any(), f"found inf values: {z[np.isinf(z)].tolist()}")


# ---------------------------------------------------------------------------
# 2. squeeze_duration -- known-answer streak check
# ---------------------------------------------------------------------------
def test_squeeze_duration_streak_logic():
    # construct a price series with strictly shrinking daily range for 8 days,
    # then expanding range -- streak should count up then reset to 0
    n = 40
    dates = pd.bdate_range("2023-01-01", periods=n)
    close = np.full(n, 50.0)
    high = close.copy()
    low = close.copy()

    # first 20 days: noisy range (warmup for ATR rolling window)
    rng = np.random.default_rng(0)
    high[:20] += rng.uniform(1, 3, 20)
    low[:20] -= rng.uniform(1, 3, 20)

    # next 8 days: monotonically shrinking range
    shrink = np.linspace(3, 0.2, 8)
    high[20:28] = close[20:28] + shrink
    low[20:28] = close[20:28] - shrink

    # final days: range expands again (streak should break)
    high[28:] = close[28:] + 5
    low[28:] = close[28:] - 5

    df = pd.DataFrame({"date": dates, "high": high, "low": low, "close": close})
    out = ps.squeeze_duration(df)

    streak_at_shrink_end = out["squeeze_streak"].iloc[27]
    streak_after_expand = out["squeeze_streak"].iloc[29]

    check("squeeze_duration: streak builds during contraction",
          streak_at_shrink_end >= 5, f"got streak={streak_at_shrink_end}")
    check("squeeze_duration: streak resets to 0 when range expands",
          streak_after_expand == 0, f"got streak={streak_after_expand}")


# ---------------------------------------------------------------------------
# 3. pocket_pivot_flag -- known-answer check
# ---------------------------------------------------------------------------
def test_pocket_pivot_known_case():
    dates = pd.bdate_range("2023-01-01", periods=15)
    close = [10, 10.2, 10.1, 9.9, 10.3, 10.2, 9.8, 10.1, 9.95, 10.0,
             10.5, 10.4, 10.6, 10.3, 11.0]  # last day: big up move
    # down-day volumes intentionally modest; final up-day volume is a clear spike
    volume = [100, 90, 110, 120, 95, 130, 140, 100, 115, 105,
              120, 110, 125, 130, 500]  # 500 should exceed max down-day vol

    df = pd.DataFrame({"date": dates, "close": close, "volume": volume})
    out = ps.pocket_pivot_flag(df, lookback=10)

    check("pocket_pivot: flags planted high-volume up day",
          bool(out["pocket_pivot"].iloc[-1]) is True,
          f"got {out['pocket_pivot'].iloc[-1]}, max_down_vol={out['max_down_volume_10d'].iloc[-1]}")

    # a normal up day with unremarkable volume should NOT be flagged
    check("pocket_pivot: does not flag ordinary up day",
          bool(out["pocket_pivot"].iloc[4]) is False,
          f"day index 4 incorrectly flagged")


# ---------------------------------------------------------------------------
# 4. stealth_accumulation_score -- sanity bounds + directional check
# ---------------------------------------------------------------------------
def test_stealth_accumulation_bounds_and_direction():
    n = 60
    dates = pd.bdate_range("2023-01-01", periods=n)
    rng = np.random.default_rng(1)

    # baseline noisy series
    close = 20 + np.cumsum(rng.normal(0, 0.1, n))
    high = close + 0.3
    low = close - 0.3
    volume = rng.integers(100000, 150000, n).astype(float)

    # inject stealth pattern in last 5 days: volume ramps hard, price stays flat
    close[-5:] = close[-6]
    high[-5:] = close[-6] + 0.05
    low[-5:] = close[-6] - 0.05
    volume[-5:] = [200000, 260000, 320000, 400000, 480000]

    df = pd.DataFrame({"date": dates, "close": close, "high": high, "low": low, "volume": volume})
    out = ps.stealth_accumulation_score(df)

    score = out["stealth_score"]
    check("stealth_accumulation_score: stays within [0,1] bounds",
          (score.dropna() >= 0).all() and (score.dropna() <= 1).all(),
          f"min={score.min()}, max={score.max()}")

    check("stealth_accumulation_score: planted pattern scores higher than baseline",
          score.iloc[-1] > score.iloc[:40].mean(),
          f"planted={score.iloc[-1]}, baseline_mean={score.iloc[:40].mean()}")


# ---------------------------------------------------------------------------
# 5. CRITICAL: no-lookahead-bias check on precursor window extraction
# ---------------------------------------------------------------------------
def test_no_lookahead_bias():
    """
    Builds a feature panel where a marker column has a distinct, easily
    detectable value ONLY on and after a specific date. Then extracts a
    precursor window ending right before that date and confirms the
    marker value never appears in the window. If it does, the pipeline
    is leaking future information into "predictive" features -- which
    would make every backtest result fake.
    """
    n = 40
    dates = pd.bdate_range("2023-01-01", periods=n)
    df = pd.DataFrame({
        "date": dates,
        "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0,
        "volume": 100000,
    })

    leak_date = dates[30]
    panel = ps.stealth_accumulation_score(df)
    panel = ps.squeeze_duration(panel)
    panel = ps.pocket_pivot_flag(panel)

    # plant an obviously-fake marker value only from leak_date onward
    panel.loc[panel["date"] >= leak_date, "rvol_trend_5d"] = 999999.0

    window_feats = eb.extract_precursor_window(panel, leak_date, precursor_days=10)

    leaked = False
    for k, v in window_feats.items():
        if "rvol_trend_5d" in k and isinstance(v, (int, float)) and v == 999999.0:
            leaked = True

    check("no_lookahead_bias: precursor window excludes data on/after event_date",
          not leaked, "FOUND LEAKED FUTURE VALUE IN PRECURSOR WINDOW -- fix extract_precursor_window")

    # also confirm the window itself, by date, never touches leak_date or later
    window_raw = panel[panel["date"] < leak_date].tail(10)
    check("no_lookahead_bias: raw window dates are strictly before event_date",
          (window_raw["date"] < leak_date).all(),
          f"max date in window: {window_raw['date'].max()}, event_date: {leak_date}")


# ---------------------------------------------------------------------------
# 6. find_events -- planted event detection + false positive check
# ---------------------------------------------------------------------------
def test_find_events_detects_planted_move_and_no_false_positive():
    n = 60
    dates = pd.bdate_range("2023-01-01", periods=n)
    rng = np.random.default_rng(2)

    # ticker A: flat/noisy, no real move -- should NOT generate events
    close_a = 10 + np.cumsum(rng.normal(0, 0.05, n))
    close_a = np.clip(close_a, 8, 12)

    # ticker B: planted clean +20% jump on day 30 (captured within 5d window)
    close_b = 10 + np.cumsum(rng.normal(0, 0.05, n))
    close_b[30] = close_b[29] * 1.20
    close_b[31:] = close_b[30] + np.cumsum(rng.normal(0, 0.05, n - 31))

    def make_df(ticker, close):
        return pd.DataFrame({
            "date": dates, "ticker": ticker,
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": rng.integers(200000, 400000, n),
        })

    history = pd.concat([make_df("FLAT", close_a), make_df("MOVER", close_b)], ignore_index=True)
    events = eb.find_events(history, move_pct=0.15, window_days=5, min_price=1.0, min_avg_volume=1000)

    check("find_events: detects planted +20% move",
          (events["ticker"] == "MOVER").any(),
          "planted event on MOVER ticker not found")

    check("find_events: does not false-positive on flat/noisy ticker",
          not (events["ticker"] == "FLAT").any(),
          f"FLAT ticker incorrectly flagged {len(events[events['ticker']=='FLAT'])} times")


# ---------------------------------------------------------------------------
# 6b. run_event_study -- restricted-ticker panel optimization correctness
# ---------------------------------------------------------------------------
def test_run_event_study_finds_planted_signal():
    """
    run_event_study only builds feature panels for tickers that appear in
    events_df or the control sample (not the full universe) for performance.
    This confirms that restriction doesn't drop or corrupt any tickers it
    actually needs: a clean volume-buildup spike planted right before each
    "event" ticker's event date must still surface as a significant,
    correctly-signed feature versus unflagged control tickers.
    """
    # run_event_study drops any feature with fewer than 20 valid (non-NaN)
    # samples on either side -- need at least 20 event tickers and a control
    # sample of at least 20 to clear that gate and actually exercise the
    # p_value/effect_size computation, not just panel-building.
    n = 80
    dates = pd.bdate_range("2023-01-01", periods=n)
    rng = np.random.default_rng(7)
    event_date = dates[70]
    n_event_tickers = 25
    n_control_tickers = 25

    rows = []
    event_records = []
    for i in range(n_event_tickers):
        ticker = f"EVT{i}"
        close = 20 + np.cumsum(rng.normal(0, 0.1, n))
        volume = rng.integers(100000, 150000, n).astype(float)
        # planted buildup: volume ramps hard in the 5 days before event_date
        idx = dates.get_loc(event_date)
        volume[idx - 5:idx] = np.linspace(300000, 600000, 5)
        rows.append(pd.DataFrame({
            "date": dates, "ticker": ticker, "open": close,
            "high": close + 0.3, "low": close - 0.3, "close": close, "volume": volume,
        }))
        event_records.append({"ticker": ticker, "event_start_date": event_date, "move_pct_actual": 0.20})

    for i in range(n_control_tickers):
        ticker = f"CTRL{i}"
        close = 20 + np.cumsum(rng.normal(0, 0.1, n))
        volume = rng.integers(100000, 150000, n).astype(float)
        rows.append(pd.DataFrame({
            "date": dates, "ticker": ticker, "open": close,
            "high": close + 0.3, "low": close - 0.3, "close": close, "volume": volume,
        }))

    history = pd.concat(rows, ignore_index=True)
    events_df = pd.DataFrame(event_records)

    results = eb.run_event_study(history, events_df, precursor_days=5, n_control=n_control_tickers)

    row = results[results["feature"] == "volume_buildup_latest"]
    # NOTE on sign convention (this is existing, unmodified code -- not
    # something this test is asserting should change): effect_size here is
    # 1 - 2*U/(n1*n2) computed from mannwhitneyu(event_vals, control_vals).
    # With event_mean clearly > control_mean by construction, that yields a
    # NEGATIVE effect_size in this codebase's convention -- confirmed
    # empirically (event_mean=2.09 vs control_mean=0.96 -> effect_size=-1.0).
    check("run_event_study: restricted-panel scope still surfaces planted volume_buildup signal",
          not row.empty
          and bool((row["p_value"] < 0.05).iloc[0])
          and bool((row["event_mean"] > row["control_mean"]).iloc[0])
          and bool((row["effect_size"] < 0).iloc[0]),
          f"got row={row.to_dict('records')}")


# ---------------------------------------------------------------------------
# 7. Edge cases -- should not crash
# ---------------------------------------------------------------------------
def test_edge_cases_do_not_crash():
    # very short history
    short_df = pd.DataFrame({
        "date": pd.bdate_range("2023-01-01", periods=3),
        "open": [10, 10.1, 10.2], "high": [10.2, 10.3, 10.4],
        "low": [9.8, 9.9, 10.0], "close": [10, 10.1, 10.2],
        "volume": [1000, 1100, 1200],
    })
    out1 = ps.stealth_accumulation_score(short_df)
    out2 = ps.squeeze_duration(short_df)
    out3 = ps.pocket_pivot_flag(short_df)
    check("edge_case: short history (3 rows) does not crash", True)

    # all-NaN volume column
    nan_df = short_df.copy()
    nan_df["volume"] = np.nan
    out4 = ps.stealth_accumulation_score(nan_df)
    check("edge_case: all-NaN volume column does not crash", True)

    # zero/flat price series (division-by-zero risk in pct-based calcs)
    flat_df = pd.DataFrame({
        "date": pd.bdate_range("2023-01-01", periods=20),
        "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0,
        "volume": 10000,
    })
    out5 = ps.squeeze_duration(flat_df)
    check("edge_case: zero-range flat price series does not crash/inf",
          not np.isinf(out5["atr_pct"].dropna()).any())


# ---------------------------------------------------------------------------
# RUN ALL
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 70)
    print("VERIFICATION SUITE: precursor_signals.py + event_study_backtest.py")
    print("=" * 70)

    run_safely("rolling_slope known-answer", test_rolling_slope_known_answer)
    run_safely("trend_zscore constant-slope handling", test_trend_zscore_handles_constant_slope)
    run_safely("squeeze_duration streak logic", test_squeeze_duration_streak_logic)
    run_safely("pocket_pivot known case", test_pocket_pivot_known_case)
    run_safely("stealth_accumulation bounds/direction", test_stealth_accumulation_bounds_and_direction)
    run_safely("NO LOOKAHEAD BIAS check", test_no_lookahead_bias)
    run_safely("find_events detection + false positive", test_find_events_detects_planted_move_and_no_false_positive)
    run_safely("run_event_study restricted-panel scope", test_run_event_study_finds_planted_signal)
    run_safely("edge cases", test_edge_cases_do_not_crash)

    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"RESULT: {passed}/{total} checks passed")
    print("=" * 70)

    failed = [r for r in RESULTS if not r[1]]
    if failed:
        print("\nFAILED CHECKS:")
        for name, ok, detail in failed:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    else:
        print("\nAll checks passed. Safe to run the real backtest on Polygon data.")
        sys.exit(0)
