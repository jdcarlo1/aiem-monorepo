"""
Pattern Discovery Framework — standalone (Directive_PatternDiscovery_Framework_2026-08-05).

Searches parameter variants of the existing 6 patterns + up to 5 new literature-
grounded families. Real Polygon 1-min SPY bars only. Fixed $100 risk/trade.
Chronological IS (8mo) / OOS (4mo) split is mandatory and printed before results.

Does NOT touch AIEM D1/D2/D3 or the live Pattern Lab dashboard.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Reuse Polygon fetch + ledger from Pattern Lab backtest engine (sibling module).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest_pattern_lab import (  # noqa: E402
    BacktestLedger,
    fetch_polygon_minute_bars,
    run_gap_fill,
    run_liquidity_sweep,
    run_orb,
)

# ---------------------------------------------------------------------------
# Fixed calendar (confirmed in report BEFORE any results)
# ---------------------------------------------------------------------------
FULL_START = "2025-08-05"
FULL_END = "2026-08-05"
IS_START = "2025-08-05"
IS_END = "2026-04-05"  # exclusive for OOS start; IS includes sessions < IS_END
OOS_START = "2026-04-05"
OOS_END = "2026-08-05"
MIN_TRADES_IS = 100
MAX_VARIANTS = 200
TOP_N_OOS = 20
FIXED_RISK = 100.0


# ---------------------------------------------------------------------------
# VWAP with geometry guard (rejects inverted stop — required after evidence check)
# ---------------------------------------------------------------------------

def run_vwap_reversion_safe(
    day_df: pd.DataFrame,
    ledger: BacktestLedger,
    symbol: str,
    adx_max: float = 20.0,
    entry_sd: float = 2.0,
    stop_sd: float = 3.0,
):
    """Literature mean-reversion to VWAP with ADX trend filter.
    Entry only when entry_sd <= |dev| < stop_sd so stop stays on the loss side.
    """
    if stop_sd <= entry_sd:
        return
    session = day_df.between_time("09:30", "15:55")
    if len(session) < 20:
        return

    typical = (session["high"] + session["low"] + session["close"]) / 3.0
    cum_vol = session["volume"].cumsum()
    vwap = (typical * session["volume"]).cumsum() / cum_vol.replace(0, np.nan)
    vwap_std = (session["close"] - vwap).expanding().std()

    high, low, close = session["high"], session["low"], session["close"]
    prev_close = close.shift(1)
    plus_dm = (high - high.shift(1)).clip(lower=0)
    minus_dm = (low.shift(1) - low).clip(lower=0)
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr.replace(0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(14).mean()

    for ts, bar in session.iterrows():
        if ledger.active_position:
            ledger.check_exits(bar)
            continue
        v, s, a = vwap.get(ts), vwap_std.get(ts), adx.get(ts)
        if pd.isna(v) or pd.isna(s) or pd.isna(a) or s < (v * 0.0001):
            continue
        if a >= adx_max:
            continue
        dev_sd = (bar["close"] - v) / s
        # Geometry guard: must be past entry band but NOT past stop band
        if entry_sd <= dev_sd < stop_sd:
            ledger.enter(symbol, bar["close"], v + s * stop_sd, v, "SHORT")
        elif -stop_sd < dev_sd <= -entry_sd:
            ledger.enter(symbol, bar["close"], v - s * stop_sd, v, "LONG")


# ---------------------------------------------------------------------------
# New pattern families (max 5) — literature-grounded
# ---------------------------------------------------------------------------

def run_orb_retest(
    day_df: pd.DataFrame,
    ledger: BacktestLedger,
    symbol: str,
    range_minutes: int = 15,
    target_r: float = 2.0,
):
    """Opening Range Retest (Toby Crabel / ORB literature):
    Wait for break of OR, then enter on retest of the broken level in break direction.
    Stop beyond opposite OR extreme; target = target_r * risk.
    """
    end_time = (pd.Timestamp("09:30") + pd.Timedelta(minutes=range_minutes)).strftime("%H:%M")
    window = day_df.between_time("09:30", end_time)
    if len(window) < range_minutes:
        return
    rh, rl = window["high"].max(), window["low"].min()
    broke_up = broke_dn = False
    entered = False
    post = day_df.between_time(end_time, "15:55")
    for _, bar in post.iterrows():
        if ledger.active_position:
            ledger.check_exits(bar)
            continue
        if entered:
            continue
        if bar["high"] > rh:
            broke_up = True
        if bar["low"] < rl:
            broke_dn = True
        # Retest long: after upside break, close back near OR high then bounce
        if broke_up and bar["low"] <= rh * 1.0005 and bar["close"] > rh:
            stop = rl * 0.9995
            risk = bar["close"] - stop
            if risk > 0:
                ledger.enter(symbol, bar["close"], stop, bar["close"] + risk * target_r, "LONG")
                entered = True
        elif broke_dn and bar["high"] >= rl * 0.9995 and bar["close"] < rl:
            stop = rh * 1.0005
            risk = stop - bar["close"]
            if risk > 0:
                ledger.enter(symbol, bar["close"], stop, bar["close"] - risk * target_r, "SHORT")
                entered = True


def run_prior_day_level(
    day_df: pd.DataFrame,
    ledger: BacktestLedger,
    symbol: str,
    prior_high: float,
    prior_low: float,
    mode: str = "fade",
    target_r: float = 1.5,
    buffer_pct: float = 0.0005,
):
    """Prior-day high/low (classic S/R day-trading):
    - fade: reject of PDH/PDL after pierce (mean reversion to mid)
    - break: close through PDH/PDL with stop back inside prior range
    Session window 10:00–15:00 to avoid open noise.
    """
    if prior_high is None or prior_low is None or prior_high <= prior_low:
        return
    session = day_df.between_time("10:00", "15:00")
    mid = (prior_high + prior_low) / 2.0
    for _, bar in session.iterrows():
        if ledger.active_position:
            ledger.check_exits(bar)
            continue
        if mode == "fade":
            if bar["high"] > prior_high * (1 + buffer_pct) and bar["close"] < prior_high:
                stop = bar["high"]
                risk = stop - bar["close"]
                if risk > 0:
                    ledger.enter(symbol, bar["close"], stop, bar["close"] - risk * target_r, "SHORT")
            elif bar["low"] < prior_low * (1 - buffer_pct) and bar["close"] > prior_low:
                stop = bar["low"]
                risk = bar["close"] - stop
                if risk > 0:
                    ledger.enter(symbol, bar["close"], stop, bar["close"] + risk * target_r, "LONG")
        else:  # break
            if bar["close"] > prior_high * (1 + buffer_pct):
                stop = mid
                risk = bar["close"] - stop
                if risk > 0:
                    ledger.enter(symbol, bar["close"], stop, bar["close"] + risk * target_r, "LONG")
            elif bar["close"] < prior_low * (1 - buffer_pct):
                stop = mid
                risk = stop - bar["close"]
                if risk > 0:
                    ledger.enter(symbol, bar["close"], stop, bar["close"] - risk * target_r, "SHORT")


def run_first_hour_trend(
    day_df: pd.DataFrame,
    ledger: BacktestLedger,
    symbol: str,
    target_r: float = 2.0,
    min_move_pct: float = 0.002,
):
    """First-hour trend continuation (intraday trend-day literature):
    Direction of 09:30–10:30 close vs open; enter on break of first-hour extreme
    after 10:30 with stop at first-hour midpoint; target = target_r * risk.
    Requires |first-hour move| >= min_move_pct.
    """
    fh = day_df.between_time("09:30", "10:30")
    if len(fh) < 30:
        return
    o, c = fh.iloc[0]["open"], fh.iloc[-1]["close"]
    move = (c - o) / o if o else 0
    if abs(move) < min_move_pct:
        return
    fh_high, fh_low = fh["high"].max(), fh["low"].min()
    mid = (fh_high + fh_low) / 2.0
    post = day_df.between_time("10:30", "15:55")
    for _, bar in post.iterrows():
        if ledger.active_position:
            ledger.check_exits(bar)
            continue
        if move > 0 and bar["close"] > fh_high:
            stop = mid
            risk = bar["close"] - stop
            if risk > 0:
                ledger.enter(symbol, bar["close"], stop, bar["close"] + risk * target_r, "LONG")
        elif move < 0 and bar["close"] < fh_low:
            stop = mid
            risk = stop - bar["close"]
            if risk > 0:
                ledger.enter(symbol, bar["close"], stop, bar["close"] - risk * target_r, "SHORT")


def run_nr_breakout(
    day_df: pd.DataFrame,
    ledger: BacktestLedger,
    symbol: str,
    range_minutes: int = 15,
    nr_max_pct: float = 0.0012,
    target_r: float = 2.0,
):
    """Narrow Range Opening Breakout (Crabel NR / OR compression):
    If opening-range % width <= nr_max_pct, trade first break of OR like ORB.
    """
    end_time = (pd.Timestamp("09:30") + pd.Timedelta(minutes=range_minutes)).strftime("%H:%M")
    window = day_df.between_time("09:30", end_time)
    if len(window) < range_minutes:
        return
    rh, rl = window["high"].max(), window["low"].min()
    o0 = window.iloc[0]["open"]
    if not o0 or (rh - rl) / o0 > nr_max_pct:
        return
    post = day_df.between_time(end_time, "15:55")
    for _, bar in post.iterrows():
        if ledger.active_position:
            ledger.check_exits(bar)
            continue
        if bar["close"] > rh:
            stop = rl * 0.9995
            risk = bar["close"] - stop
            if risk > 0:
                ledger.enter(symbol, bar["close"], stop, bar["close"] + risk * target_r, "LONG")
        elif bar["close"] < rl:
            stop = rh * 1.0005
            risk = stop - bar["close"]
            if risk > 0:
                ledger.enter(symbol, bar["close"], stop, bar["close"] - risk * target_r, "SHORT")


def run_closing_drive(
    day_df: pd.DataFrame,
    ledger: BacktestLedger,
    symbol: str,
    target_r: float = 1.5,
    drive_start: str = "14:00",
    min_rvol: float = 1.2,
):
    """Closing drive / late-day momentum (intraday auction literature):
    After 14:00, if price makes new session high/low on elevated volume vs
    morning average, enter with stop at VWAP; flatten by 15:55 via ledger EOD.
    """
    morning = day_df.between_time("09:30", "13:59")
    late = day_df.between_time(drive_start, "15:55")
    if len(morning) < 30 or len(late) < 5:
        return
    sess_high = morning["high"].max()
    sess_low = morning["low"].min()
    avg_vol = morning["volume"].mean()
    typical = (morning["high"] + morning["low"] + morning["close"]) / 3.0
    cum_vol = morning["volume"].cumsum()
    vwap_m = float(
        ((typical * morning["volume"]).cumsum() / cum_vol.replace(0, np.nan)).iloc[-1]
    )
    if not np.isfinite(vwap_m) or avg_vol <= 0:
        return
    for _, bar in late.iterrows():
        if ledger.active_position:
            ledger.check_exits(bar)
            continue
        if bar["volume"] < avg_vol * min_rvol:
            continue
        if bar["close"] > sess_high and bar["close"] > vwap_m:
            stop = vwap_m
            risk = bar["close"] - stop
            if risk > 0:
                ledger.enter(symbol, bar["close"], stop, bar["close"] + risk * target_r, "LONG")
        elif bar["close"] < sess_low and bar["close"] < vwap_m:
            stop = vwap_m
            risk = stop - bar["close"]
            if risk > 0:
                ledger.enter(symbol, bar["close"], stop, bar["close"] - risk * target_r, "SHORT")


# ---------------------------------------------------------------------------
# Variant registry
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    id: str
    family: str
    params: Dict[str, Any]
    rationale: str
    kind: str  # "sweep" | "new"


def build_variants() -> List[Variant]:
    variants: List[Variant] = []

    # --- (a) Parameter sweeps of existing 6 ---
    # ORB / HIGH_BETA_ORB / WEEKLY_MACRO_ORB
    for rm, tr in itertools.product([10, 15, 20, 30], [1.0, 1.5, 2.0, 2.5]):
        variants.append(
            Variant(
                id=f"ORB_rm{rm}_r{tr}",
                family="ORB",
                params={"range_minutes": rm, "target_r": tr},
                rationale="Classic Opening Range Breakout (Crabel/IB): break of N-min OR, target = R*risk.",
                kind="sweep",
            )
        )
        variants.append(
            Variant(
                id=f"HBORB_rm{rm}_r{tr}",
                family="HIGH_BETA_ORB",
                params={"range_minutes": rm, "target_r": tr, "rvol_min": 1.5},
                rationale="ORB with relative-volume filter (rvol>=1.5) — high-participation breakouts.",
                kind="sweep",
            )
        )
    for rm, tr, mrp in itertools.product([20, 30], [1.0, 1.5, 2.0, 2.5], [0.001, 0.0015, 0.002]):
        variants.append(
            Variant(
                id=f"WMORB_rm{rm}_r{tr}_mrp{mrp}",
                family="WEEKLY_MACRO_ORB",
                params={"range_minutes": rm, "target_r": tr, "min_range_pct": mrp},
                rationale="Wider OR with minimum range filter — macro/volatile open days only.",
                kind="sweep",
            )
        )

    # VWAP (safe geometry)
    for entry_sd, stop_sd, adx in itertools.product(
        [1.5, 2.0, 2.5], [2.5, 3.0, 3.5], [15, 20, 25]
    ):
        if stop_sd <= entry_sd:
            continue
        variants.append(
            Variant(
                id=f"VWAP_e{entry_sd}_s{stop_sd}_adx{adx}",
                family="VWAP_REVERSION",
                params={"entry_sd": entry_sd, "stop_sd": stop_sd, "adx_max": adx},
                rationale="VWAP mean reversion with ADX trend filter; geometry guard |dev|<stop_sd.",
                kind="sweep",
            )
        )

    # Liquidity sweep
    for tr, lb in itertools.product([1.0, 1.5, 2.0, 2.5], [15, 20, 30]):
        variants.append(
            Variant(
                id=f"LS_r{tr}_lb{lb}",
                family="LIQUIDITY_SWEEP",
                params={"target_r": tr, "swing_lookback": lb},
                rationale="Lunch liquidity sweep of morning swing + reclaim (ICT/SMC-adjacent).",
                kind="sweep",
            )
        )

    # Gap fill — limited knobs (gap_pct already internal); use as single baseline + mild variants via OR window proxy
    variants.append(
        Variant(
            id="GAP_FILL_default",
            family="GAP_FILL",
            params={},
            rationale="Gap fill toward prior close after 15-min open (classic gap fade).",
            kind="sweep",
        )
    )

    # --- (b) New families (max 5) ---
    for rm, tr in itertools.product([10, 15, 20], [1.5, 2.0, 2.5]):
        variants.append(
            Variant(
                id=f"ORB_RETEST_rm{rm}_r{tr}",
                family="ORB_RETEST",
                params={"range_minutes": rm, "target_r": tr},
                rationale="Toby Crabel OR retest: break then retest of OR level in break direction.",
                kind="new",
            )
        )
    for mode, tr in itertools.product(["fade", "break"], [1.0, 1.5, 2.0]):
        variants.append(
            Variant(
                id=f"PDL_{mode}_r{tr}",
                family="PRIOR_DAY_LEVEL",
                params={"mode": mode, "target_r": tr, "buffer_pct": 0.0005},
                rationale="Prior-day high/low S/R: fade rejection or breakout through PDH/PDL.",
                kind="new",
            )
        )
    for tr, mm in itertools.product([1.5, 2.0, 2.5], [0.0015, 0.002, 0.003]):
        variants.append(
            Variant(
                id=f"FH_TREND_r{tr}_mm{mm}",
                family="FIRST_HOUR_TREND",
                params={"target_r": tr, "min_move_pct": mm},
                rationale="First-hour trend continuation after directional 09:30–10:30 open.",
                kind="new",
            )
        )
    for rm, nr, tr in itertools.product([10, 15], [0.0008, 0.0012, 0.0015], [1.5, 2.0, 2.5]):
        variants.append(
            Variant(
                id=f"NRBO_rm{rm}_nr{nr}_r{tr}",
                family="NR_BREAKOUT",
                params={"range_minutes": rm, "nr_max_pct": nr, "target_r": tr},
                rationale="Crabel narrow-range opening breakout — compressed OR then expansion.",
                kind="new",
            )
        )
    for tr, rv in itertools.product([1.0, 1.5, 2.0], [1.1, 1.2, 1.5]):
        variants.append(
            Variant(
                id=f"CLOSE_DRIVE_r{tr}_rv{rv}",
                family="CLOSING_DRIVE",
                params={"target_r": tr, "min_rvol": rv, "drive_start": "14:00"},
                rationale="Late-day closing drive: new session extreme on elevated volume vs VWAP.",
                kind="new",
            )
        )

    # Cap at 200 — prefer keeping all families; truncate newest extras if over
    if len(variants) > MAX_VARIANTS:
        variants = variants[:MAX_VARIANTS]
    return variants


def run_variant_on_bars(
    variant: Variant,
    bars: pd.DataFrame,
    symbol: str = "SPY",
) -> Tuple[BacktestLedger, Dict[str, Any]]:
    ledger = BacktestLedger(variant.id, fixed_risk_usd=FIXED_RISK)
    fam = variant.family
    p = variant.params
    trading_days = sorted(bars.index.normalize().unique())
    prior_close = None
    prior_high = prior_low = None

    for day in trading_days:
        day_df = bars[bars.index.normalize() == day]
        if day_df.empty:
            continue
        if fam == "GAP_FILL":
            if prior_close is not None:
                run_gap_fill(day_df, prior_close, ledger, symbol)
        elif fam == "ORB":
            run_orb(
                day_df,
                ledger,
                symbol,
                range_minutes=int(p["range_minutes"]),
                target_r=float(p["target_r"]),
            )
        elif fam == "HIGH_BETA_ORB":
            run_orb(
                day_df,
                ledger,
                symbol,
                range_minutes=int(p["range_minutes"]),
                target_r=float(p["target_r"]),
                rvol_min=float(p.get("rvol_min", 1.5)),
            )
        elif fam == "WEEKLY_MACRO_ORB":
            run_orb(
                day_df,
                ledger,
                symbol,
                range_minutes=int(p["range_minutes"]),
                target_r=float(p["target_r"]),
                min_range_pct=float(p.get("min_range_pct", 0.0015)),
            )
        elif fam == "VWAP_REVERSION":
            run_vwap_reversion_safe(
                day_df,
                ledger,
                symbol,
                adx_max=float(p["adx_max"]),
                entry_sd=float(p["entry_sd"]),
                stop_sd=float(p["stop_sd"]),
            )
        elif fam == "LIQUIDITY_SWEEP":
            run_liquidity_sweep(
                day_df,
                ledger,
                symbol,
                swing_lookback=int(p.get("swing_lookback", 20)),
                target_r=float(p.get("target_r", 1.5)),
            )
        elif fam == "ORB_RETEST":
            run_orb_retest(
                day_df,
                ledger,
                symbol,
                range_minutes=int(p["range_minutes"]),
                target_r=float(p["target_r"]),
            )
        elif fam == "PRIOR_DAY_LEVEL":
            run_prior_day_level(
                day_df,
                ledger,
                symbol,
                prior_high=prior_high,
                prior_low=prior_low,
                mode=str(p.get("mode", "fade")),
                target_r=float(p.get("target_r", 1.5)),
                buffer_pct=float(p.get("buffer_pct", 0.0005)),
            )
        elif fam == "FIRST_HOUR_TREND":
            run_first_hour_trend(
                day_df,
                ledger,
                symbol,
                target_r=float(p["target_r"]),
                min_move_pct=float(p.get("min_move_pct", 0.002)),
            )
        elif fam == "NR_BREAKOUT":
            run_nr_breakout(
                day_df,
                ledger,
                symbol,
                range_minutes=int(p["range_minutes"]),
                nr_max_pct=float(p["nr_max_pct"]),
                target_r=float(p["target_r"]),
            )
        elif fam == "CLOSING_DRIVE":
            run_closing_drive(
                day_df,
                ledger,
                symbol,
                target_r=float(p["target_r"]),
                drive_start=str(p.get("drive_start", "14:00")),
                min_rvol=float(p.get("min_rvol", 1.2)),
            )
        else:
            raise ValueError(f"Unknown family {fam}")

        prior_close = float(day_df.iloc[-1]["close"])
        prior_high = float(day_df["high"].max())
        prior_low = float(day_df["low"].min())

    stats = {
        "id": variant.id,
        "family": variant.family,
        "kind": variant.kind,
        "params": variant.params,
        "rationale": variant.rationale,
        "trades": ledger.total_trades,
        "wins": ledger.wins,
        "win_pct": round(ledger.win_rate_pct, 2),
        "net_pnl": round(ledger.balance_usd, 2),
        "avg_trade": round(
            (ledger.balance_usd / ledger.total_trades) if ledger.total_trades else 0.0, 2
        ),
    }
    return ledger, stats


def slice_bars(bars: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Inclusive start date, exclusive end date (session dates in America/New_York)."""
    idx_dates = bars.index.normalize()
    start_ts = pd.Timestamp(start, tz="America/New_York")
    end_ts = pd.Timestamp(end, tz="America/New_York")
    mask = (idx_dates >= start_ts) & (idx_dates < end_ts)
    return bars.loc[mask]


def nudge_params(params: Dict[str, Any], factor: float) -> Dict[str, Any]:
    out = {}
    for k, v in params.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if isinstance(v, int) and k in ("range_minutes", "swing_lookback", "adx_max"):
                out[k] = max(1, int(round(v * factor)))
            else:
                out[k] = float(v) * factor
        else:
            out[k] = v
    # Keep VWAP geometry valid
    if "entry_sd" in out and "stop_sd" in out and out["stop_sd"] <= out["entry_sd"]:
        out["stop_sd"] = float(out["entry_sd"]) + 0.5
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--cache", default="/tmp/spy_1y_discovery.pkl")
    parser.add_argument("--out-dir", default="/workspace/docs/verification")
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key and not (args.skip_fetch and os.path.exists(args.cache)):
        raise SystemExit("POLYGON_API_KEY required")

    print("=" * 72)
    print("Directive_PatternDiscovery_Framework_2026-08-05 — START")
    print("=" * 72)
    print("STEP 1 — Data split (confirmed BEFORE any results):")
    print(f"  Full window : {FULL_START} → {FULL_END}")
    print(f"  In-sample   : {IS_START} → {IS_END}  (first 8 months; search/tuning only)")
    print(f"  Out-of-sample: {OOS_START} → {OOS_END}  (last 4 months; NEVER used in search)")
    print(f"  Risk/trade  : ${FIXED_RISK:.0f} fixed, no compounding")
    print(f"  Max variants: {MAX_VARIANTS}")
    print("=" * 72)

    # Fetch / load
    if os.path.exists(args.cache) and args.skip_fetch:
        print(f"Loading bars from cache {args.cache} (DISCLOSED)")
        bars = pd.read_pickle(args.cache)
    else:
        print(f"Fetching REAL Polygon 1-min bars {args.symbol} {FULL_START}→{FULL_END}…")
        bars = fetch_polygon_minute_bars(args.symbol, FULL_START, FULL_END, api_key)
        bars.to_pickle(args.cache)
        print(f"Cached → {args.cache}")

    print(
        f"Bars loaded: {len(bars)} | sessions={bars.index.normalize().nunique()} | "
        f"first={bars.index.min()} | last={bars.index.max()}"
    )
    is_bars = slice_bars(bars, IS_START, IS_END)
    oos_bars = slice_bars(bars, OOS_START, OOS_END)
    print(
        f"IS bars={len(is_bars)} sessions={is_bars.index.normalize().nunique()} | "
        f"OOS bars={len(oos_bars)} sessions={oos_bars.index.normalize().nunique()}"
    )
    if is_bars.empty or oos_bars.empty:
        raise SystemExit("Empty IS or OOS slice — abort")

    variants = build_variants()
    print(f"\nSTEP 2 — Generated {len(variants)} variants (cap {MAX_VARIANTS})")
    new_count = sum(1 for v in variants if v.kind == "new")
    print(f"  sweeps={len(variants) - new_count} | new_families_variants={new_count}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = out_dir / f"pattern-discovery-variant-log-{stamp}.jsonl"
    progress_path = Path("/tmp/pattern_discovery_progress.json")

    # STEP 3 — IS screen
    print("\nSTEP 3 — In-sample screening…")
    is_results: List[Dict[str, Any]] = []
    t0 = time.time()
    with open(log_path, "w") as logf:
        for i, var in enumerate(variants, 1):
            _, stats = run_variant_on_bars(var, is_bars, args.symbol)
            stats["window"] = "IS"
            is_results.append(stats)
            logf.write(json.dumps(stats) + "\n")
            logf.flush()
            progress_path.write_text(
                json.dumps(
                    {
                        "phase": "IS",
                        "i": i,
                        "n": len(variants),
                        "id": var.id,
                        "net_pnl": stats["net_pnl"],
                        "trades": stats["trades"],
                        "elapsed_s": round(time.time() - t0, 1),
                    }
                )
            )
            if i % 10 == 0 or i == len(variants):
                print(
                    f"  [{i}/{len(variants)}] {var.id} trades={stats['trades']} "
                    f"net={stats['net_pnl']:.2f} ({time.time()-t0:.0f}s)"
                )

    survivors = [r for r in is_results if r["trades"] >= MIN_TRADES_IS]
    survivors.sort(key=lambda r: r["net_pnl"], reverse=True)
    discarded = len(is_results) - len(survivors)
    print(
        f"IS done: tested={len(is_results)} discarded(<{MIN_TRADES_IS} trades)={discarded} "
        f"survivors={len(survivors)}"
    )

    top20 = survivors[:TOP_N_OOS]
    if len(top20) < TOP_N_OOS:
        print(
            f"WARNING: only {len(top20)} variants met min trades — OOS will use all of them"
        )

    # STEP 4 — OOS
    print(f"\nSTEP 4 — OOS validation of top {len(top20)} (params UNCHANGED)…")
    id_to_var = {v.id: v for v in variants}
    oos_rows: List[Dict[str, Any]] = []
    for j, is_row in enumerate(top20, 1):
        var = id_to_var[is_row["id"]]
        _, oos_stats = run_variant_on_bars(var, oos_bars, args.symbol)
        row = {
            "id": var.id,
            "family": var.family,
            "kind": var.kind,
            "params": var.params,
            "rationale": var.rationale,
            "is_trades": is_row["trades"],
            "is_win_pct": is_row["win_pct"],
            "is_net_pnl": is_row["net_pnl"],
            "oos_trades": oos_stats["trades"],
            "oos_win_pct": oos_stats["win_pct"],
            "oos_net_pnl": oos_stats["net_pnl"],
            "passed_oos": oos_stats["net_pnl"] > 0,
        }
        oos_rows.append(row)
        print(
            f"  [{j}/{len(top20)}] {var.id} IS={is_row['net_pnl']:.2f} "
            f"OOS={oos_stats['net_pnl']:.2f} pass={row['passed_oos']}"
        )

    step4_pass = [r for r in oos_rows if r["passed_oos"]]
    print(f"Step 4 passers (OOS net>0): {len(step4_pass)} / {len(oos_rows)}")

    # STEP 5 — ±20% robustness
    print("\nSTEP 5 — Robustness ±20% on Step-4 survivors…")
    final_survivors: List[Dict[str, Any]] = []
    for row in step4_pass:
        var = id_to_var[row["id"]]
        if not var.params:
            # no tunable numeric params — pass with note
            final_survivors.append({**row, "robust": True, "nudge_results": [], "fragile": False})
            continue
        nudge_results = []
        fragile = False
        for label, factor in [("-20%", 0.8), ("+20%", 1.2)]:
            nudged = Variant(
                id=f"{var.id}_{label}",
                family=var.family,
                params=nudge_params(var.params, factor),
                rationale=var.rationale,
                kind=var.kind,
            )
            _, ns = run_variant_on_bars(nudged, oos_bars, args.symbol)
            nudge_results.append(
                {
                    "nudge": label,
                    "params": nudged.params,
                    "oos_net_pnl": ns["net_pnl"],
                    "oos_trades": ns["trades"],
                }
            )
            # Collapse = OOS net flips negative or drops >70% vs baseline OOS
            base = row["oos_net_pnl"]
            if ns["net_pnl"] <= 0 or (base > 0 and ns["net_pnl"] < 0.3 * base):
                fragile = True
        entry = {
            **row,
            "robust": not fragile,
            "fragile": fragile,
            "nudge_results": nudge_results,
        }
        if not fragile:
            final_survivors.append(entry)
        print(
            f"  {var.id} fragile={fragile} nudges={nudge_results}"
        )

    # Trade-log samples for final survivors
    trade_samples = {}
    for entry in final_survivors:
        var = id_to_var[entry["id"]]
        # Enrich exits with stop/target for sample
        ledger, _ = run_variant_on_bars(var, oos_bars, args.symbol)
        # ledger trade_log lacks stop/target; sample what we have
        trade_samples[entry["id"]] = ledger.trade_log[:10]

    report = {
        "directive": "Directive_PatternDiscovery_Framework_2026-08-05",
        "split": {
            "full": [FULL_START, FULL_END],
            "in_sample": [IS_START, IS_END],
            "out_of_sample": [OOS_START, OOS_END],
            "is_sessions": int(is_bars.index.normalize().nunique()),
            "oos_sessions": int(oos_bars.index.normalize().nunique()),
            "bars_total": len(bars),
        },
        "risk_usd": FIXED_RISK,
        "variants_tested": len(variants),
        "is_discarded_low_trades": discarded,
        "is_survivors_ge_100": len(survivors),
        "top20_oos": oos_rows,
        "step4_pass_count": len(step4_pass),
        "step5_final_survivors": final_survivors,
        "final_survivor_count": len(final_survivors),
        "survival_rate_vs_tested": round(len(final_survivors) / max(len(variants), 1), 4),
        "trade_samples_oos": trade_samples,
        "variant_log_path": str(log_path),
        "is_full_results": is_results,
    }

    json_path = out_dir / "pattern-discovery-FINAL.json"
    json_path.write_text(json.dumps(report, indent=2, default=str))

    # Markdown report
    md_lines = [
        "# Pattern Discovery Framework — Final Report",
        "",
        f"**Directive:** Directive_PatternDiscovery_Framework_2026-08-05",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "## Step 1 — Data split (confirmed before results)",
        "",
        f"- Full window: `{FULL_START}` → `{FULL_END}`",
        f"- In-sample (search): `{IS_START}` → `{IS_END}` ({report['split']['is_sessions']} sessions)",
        f"- Out-of-sample (validation only): `{OOS_START}` → `{OOS_END}` ({report['split']['oos_sessions']} sessions)",
        f"- Bars: {report['split']['bars_total']} SPY 1-min from Polygon",
        f"- Risk: ${FIXED_RISK:.0f}/trade fixed",
        "",
        "## Counts",
        "",
        f"- Variants tested: **{len(variants)}** (cap {MAX_VARIANTS})",
        f"- Discarded IS (<{MIN_TRADES_IS} trades): **{discarded}**",
        f"- IS survivors ranked: **{len(survivors)}**",
        f"- Top-N sent to OOS: **{len(top20)}**",
        f"- Passed Step 4 (OOS net > 0): **{len(step4_pass)}**",
        f"- Passed Step 5 (robust ±20%): **{len(final_survivors)}**",
        f"- Survival rate vs tested: **{report['survival_rate_vs_tested']*100:.2f}%**",
        "",
        "> Most variants should NOT survive. Near-100% survival would be a red flag.",
        "",
        "## Top 20 — IS vs OOS (side by side)",
        "",
        "| ID | Family | IS trades | IS win% | IS net | OOS trades | OOS win% | OOS net | Pass OOS |",
        "|----|--------|----------:|--------:|-------:|-----------:|---------:|--------:|:--------:|",
    ]
    for r in oos_rows:
        md_lines.append(
            f"| `{r['id']}` | {r['family']} | {r['is_trades']} | {r['is_win_pct']} | "
            f"{r['is_net_pnl']:.2f} | {r['oos_trades']} | {r['oos_win_pct']} | "
            f"{r['oos_net_pnl']:.2f} | {'YES' if r['passed_oos'] else 'no'} |"
        )

    md_lines += ["", "## Final survivors (Step 4 + Step 5)", ""]
    if not final_survivors:
        md_lines.append(
            "**None.** Nothing survived both OOS profitability and ±20% robustness. "
            "This is a valid result — bar was not lowered."
        )
    else:
        for e in final_survivors:
            md_lines += [
                f"### `{e['id']}`",
                f"- Family: {e['family']} ({e['kind']})",
                f"- Params: `{json.dumps(e['params'])}`",
                f"- Rationale: {e['rationale']}",
                f"- IS: trades={e['is_trades']} win%={e['is_win_pct']} net={e['is_net_pnl']}",
                f"- OOS: trades={e['oos_trades']} win%={e['oos_win_pct']} net={e['oos_net_pnl']}",
                f"- Nudges: `{json.dumps(e.get('nudge_results'))}`",
                "",
                "Sample OOS trades (first 10):",
                "```",
                json.dumps(trade_samples.get(e["id"], []), indent=2),
                "```",
                "",
            ]

    md_lines += [
        "",
        "## Full variant log",
        f"JSONL: `{log_path}`",
        f"Machine report: `{json_path}`",
        "",
    ]
    md_path = out_dir / "pattern-discovery-FINAL.md"
    md_path.write_text("\n".join(md_lines))
    print(f"\nWrote {md_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {log_path}")
    print("=" * 72)
    print(
        f"DONE — tested={len(variants)} final_survivors={len(final_survivors)} "
        f"survival_rate={report['survival_rate_vs_tested']*100:.2f}%"
    )


if __name__ == "__main__":
    main()
