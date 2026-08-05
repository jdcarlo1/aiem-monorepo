"""
SPY recheck: VWAP Reversion (geometry-fixed) + MACD Histogram Divergence
+ Bollinger Band Exhaustion.

Same economics as Pattern Lab: $100 fixed risk/trade, $0.04 round-trip
slippage model, no compounding. Real Polygon data only.

Prior Pattern Lab bug (PR #22 evidence check): VWAP entered when |dev| > stop_sd,
which inverted stop vs entry and manufactured fake STOP wins. This script
requires entry_sd <= |dev| < stop_sd so stop is always on the loss side.

Usage:
    export POLYGON_API_KEY=$(cat /tmp/.polygon_key)
    python3 backtest_spy_three_patterns.py --symbol SPY --start 2026-02-05 --end 2026-08-05
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

POLYGON_BASE = "https://api.polygon.io"


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def _polygon_get(url, params=None, tries=8):
    last = None
    for i in range(tries):
        resp = requests.get(url, params=params, timeout=60)
        if resp.status_code == 429:
            wait = min(60, 2 ** i)
            print(f"  rate-limited, sleep {wait}s…")
            time.sleep(wait)
            last = resp
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(
        f"Polygon rate limit persisted after {tries} tries "
        f"(last={getattr(last, 'status_code', None)})"
    )


def fetch_polygon_bars(symbol: str, start: str, end: str, api_key: str,
                       multiplier: int, timespan: str) -> pd.DataFrame:
    """Pull real aggregate bars. Refuses synthetic fallback."""
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY not set — cannot fetch real data.")

    if timespan == "minute":
        # Month chunks keep free-tier requests smaller.
        months = pd.date_range(start=start, end=end, freq="MS").strftime("%Y-%m-%d").tolist()
        if not months or months[0] != start:
            months = [start] + months
        edges = sorted(set(months))
        if edges[-1] < end:
            edges.append(end)
        if edges[-1] != end:
            edges.append(end)
    else:
        edges = [start, end]

    all_rows = []
    for i in range(len(edges) - 1):
        chunk_start, chunk_end = edges[i], edges[i + 1]
        if chunk_start >= chunk_end:
            continue
        print(f"  fetching {symbol} {multiplier}/{timespan} {chunk_start} → {chunk_end}…")
        url = (
            f"{POLYGON_BASE}/v2/aggs/ticker/{symbol}/range/"
            f"{multiplier}/{timespan}/{chunk_start}/{chunk_end}"
        )
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
        while url:
            resp = _polygon_get(url, params if params else None)
            data = resp.json()
            if data.get("status") not in ("OK", "DELAYED"):
                raise RuntimeError(
                    f"Polygon non-OK: {data.get('status')} — {data.get('error')}"
                )
            all_rows.extend(data.get("results", []))
            next_url = data.get("next_url")
            url = next_url
            params = None
            if url and "apiKey=" not in url:
                url = f"{url}&apiKey={api_key}"
            time.sleep(0.35)
        time.sleep(0.5)

    if not all_rows:
        raise RuntimeError(f"Polygon returned zero bars for {symbol} {start}-{end}.")

    df = pd.DataFrame(all_rows)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(
        "America/New_York"
    )
    df = df.set_index("timestamp").rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    )[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class BacktestLedger:
    def __init__(self, pattern_name: str, fixed_risk_usd: float = 100.0,
                 slippage_usd: float = 0.04):
        self.pattern_name = pattern_name
        self.fixed_risk_usd = fixed_risk_usd
        self.slippage_usd = slippage_usd
        self.balance_usd = 0.0
        self.trade_log = []
        self.active_position = None

    @property
    def total_trades(self):
        return len(self.trade_log)

    @property
    def wins(self):
        return sum(1 for t in self.trade_log if t["pnl_usd"] > 0)

    @property
    def win_rate_pct(self):
        return (self.wins / self.total_trades * 100.0) if self.total_trades else 0.0

    def enter(self, symbol, entry, stop, target, side, meta=None):
        if self.active_position:
            return False
        if side == "LONG":
            p_entry = entry + self.slippage_usd / 2
            p_stop = stop - self.slippage_usd / 2
            p_target = target - self.slippage_usd / 2
            if not (p_stop < p_entry < p_target):
                return False  # reject inverted / zero-risk geometry
        else:
            p_entry = entry - self.slippage_usd / 2
            p_stop = stop + self.slippage_usd / 2
            p_target = target + self.slippage_usd / 2
            if not (p_target < p_entry < p_stop):
                return False

        risk_per_share = abs(p_entry - p_stop)
        if risk_per_share <= 0:
            return False
        shares = int(self.fixed_risk_usd / risk_per_share)
        if shares <= 0:
            return False
        self.active_position = {
            "symbol": symbol,
            "shares": shares,
            "side": side,
            "entry": p_entry,
            "stop": p_stop,
            "target": p_target,
            "meta": meta or {},
        }
        return True

    def check_exits(self, bar: pd.Series, eod_flatten: bool = True,
                    force_flatten: bool = False):
        pos = self.active_position
        if not pos:
            return
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        bar_time = bar.name.strftime("%H:%M") if hasattr(bar.name, "strftime") else "00:00"

        exit_price, reason = None, None
        if pos["side"] == "LONG":
            if low <= pos["stop"]:
                exit_price, reason = pos["stop"], "STOP"
            elif high >= pos["target"]:
                exit_price, reason = pos["target"], "TARGET"
        else:
            if high >= pos["stop"]:
                exit_price, reason = pos["stop"], "STOP"
            elif low <= pos["target"]:
                exit_price, reason = pos["target"], "TARGET"

        if exit_price is None and force_flatten:
            exit_price, reason = close, "MAX_HOLD"
        elif exit_price is None and eod_flatten and bar_time >= "15:55":
            exit_price, reason = close, "EOD_FLATTEN"

        if exit_price is not None:
            if pos["side"] == "LONG":
                pnl = (exit_price - pos["entry"]) * pos["shares"]
            else:
                pnl = (pos["entry"] - exit_price) * pos["shares"]
            self.balance_usd += pnl
            row = {
                "side": pos["side"],
                "entry": round(pos["entry"], 4),
                "exit": round(exit_price, 4),
                "stop": round(pos["stop"], 4),
                "target": round(pos["target"], 4),
                "shares": pos["shares"],
                "pnl_usd": round(pnl, 2),
                "reason": reason,
                "ts": str(bar.name),
            }
            row.update(pos.get("meta") or {})
            self.trade_log.append(row)
            self.active_position = None

    def summary(self) -> dict:
        reasons = {}
        for t in self.trade_log:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        avg = (self.balance_usd / self.total_trades) if self.total_trades else 0.0
        return {
            "pattern": self.pattern_name,
            "trades": self.total_trades,
            "wins": self.wins,
            "win_rate_pct": round(self.win_rate_pct, 2),
            "net_pnl_usd": round(self.balance_usd, 2),
            "avg_pnl_usd": round(avg, 2),
            "exit_reasons": reasons,
        }


# ---------------------------------------------------------------------------
# Pattern 1 — VWAP Reversion (geometry-fixed)
# ---------------------------------------------------------------------------

def run_vwap_reversion_fixed(day_df: pd.DataFrame, ledger: BacktestLedger, symbol: str,
                             adx_max: float = 20.0, entry_sd: float = 2.0,
                             stop_sd: float = 3.0):
    """Mean-revert to session VWAP when ADX < adx_max.

    FIX vs buggy Pattern Lab: only enter when entry_sd <= |dev_sd| < stop_sd
    so the stop band is beyond entry (loss side), never inside it.
    """
    session = day_df.between_time("09:30", "15:55")
    if len(session) < 30:
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

    rejected_inverted = 0
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
        abs_dev = abs(dev_sd)
        # Geometry gate: must be past entry band but NOT already past stop band.
        if abs_dev < entry_sd or abs_dev >= stop_sd:
            if abs_dev >= stop_sd and abs_dev >= entry_sd:
                rejected_inverted += 1
            continue
        if dev_sd >= entry_sd:
            ok = ledger.enter(
                symbol, bar["close"], v + s * stop_sd, v, "SHORT",
                meta={"dev_sd": round(float(dev_sd), 3)},
            )
            if not ok:
                rejected_inverted += 1
        elif dev_sd <= -entry_sd:
            ok = ledger.enter(
                symbol, bar["close"], v - s * stop_sd, v, "LONG",
                meta={"dev_sd": round(float(dev_sd), 3)},
            )
            if not ok:
                rejected_inverted += 1
    ledger._rejected_inverted = rejected_inverted  # noqa: SLF001 — diagnostic only


def run_vwap_reversion_buggy_ref(day_df: pd.DataFrame, ledger: BacktestLedger,
                                 symbol: str, adx_max: float = 20.0,
                                 entry_sd: float = 2.0, stop_sd: float = 3.0):
    """Prior Pattern Lab VWAP rule (intentionally buggy) for side-by-side proof.

    Enters whenever |dev_sd| >= entry_sd with no |dev| < stop_sd gate, so stops
    can land on the profit side of entry when |dev| > stop_sd.
    """
    session = day_df.between_time("09:30", "15:55")
    if len(session) < 30:
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
        # Bypass geometry guard in enter() by writing position directly —
        # mirrors the prior ledger which allowed inverted stops.
        dev_sd = (bar["close"] - v) / s
        if abs(dev_sd) < entry_sd:
            continue
        side = "SHORT" if dev_sd >= entry_sd else "LONG"
        entry = float(bar["close"])
        stop = float(v + s * stop_sd) if side == "SHORT" else float(v - s * stop_sd)
        target = float(v)
        slip = ledger.slippage_usd
        if side == "LONG":
            p_entry, p_stop, p_target = entry + slip / 2, stop - slip / 2, target - slip / 2
        else:
            p_entry, p_stop, p_target = entry - slip / 2, stop + slip / 2, target + slip / 2
        risk = abs(p_entry - p_stop)
        if risk <= 0:
            continue
        shares = int(ledger.fixed_risk_usd / risk)
        if shares <= 0:
            continue
        ledger.active_position = {
            "symbol": symbol,
            "shares": shares,
            "side": side,
            "entry": p_entry,
            "stop": p_stop,
            "target": p_target,
            "meta": {"dev_sd": round(float(dev_sd), 3), "buggy": True},
        }


# ---------------------------------------------------------------------------
# Pattern 2 — MACD Histogram Divergence (daily)
# ---------------------------------------------------------------------------

def _macd_hist(close: pd.Series, fast=12, slow=26, signal=9) -> pd.Series:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd - sig


def _find_swing_lows(series: pd.Series, order: int = 3) -> list[int]:
    idxs = []
    vals = series.values
    for i in range(order, len(vals) - order):
        window = vals[i - order: i + order + 1]
        if vals[i] == np.min(window) and np.sum(window == vals[i]) == 1:
            idxs.append(i)
    return idxs


def _find_swing_highs(series: pd.Series, order: int = 3) -> list[int]:
    idxs = []
    vals = series.values
    for i in range(order, len(vals) - order):
        window = vals[i - order: i + order + 1]
        if vals[i] == np.max(window) and np.sum(window == vals[i]) == 1:
            idxs.append(i)
    return idxs


def run_macd_histogram_divergence(daily: pd.DataFrame, ledger: BacktestLedger,
                                  symbol: str, swing_order: int = 3,
                                  max_pivot_gap: int = 40,
                                  max_hold_days: int = 10, target_r: float = 2.0):
    """Bullish: price LL + MACD-hist HL at those pivots. Bearish: price HH + hist LH.

    Histogram is compared at the price-pivot bars (classic chartist definition),
    not via independent hist pivots — that was too strict and produced 0 trades.
    Entry on the confirmation bar (pivot + swing_order). Stop beyond the pivot
    extreme; target = target_r × risk. Flatten after max_hold_days.
    """
    if len(daily) < 60:
        return
    close = daily["close"]
    hist = _macd_hist(close)

    hold_bars = 0
    last_signal_pivot = -1

    for i in range(50, len(daily)):
        bar = daily.iloc[i]
        if ledger.active_position:
            hold_bars += 1
            force = hold_bars >= max_hold_days
            ledger.check_exits(bar, eod_flatten=False, force_flatten=force)
            if not ledger.active_position:
                hold_bars = 0
            continue

        # A pivot at j is confirmed once we have swing_order bars after it.
        j = i - swing_order
        if j < 40:
            continue
        if j == last_signal_pivot:
            continue

        # Is j a price swing low?
        left = close.iloc[j - swing_order: j].values
        right = close.iloc[j + 1: j + swing_order + 1].values
        if len(left) < swing_order or len(right) < swing_order:
            continue
        is_low = close.iloc[j] < left.min() and close.iloc[j] < right.min()
        is_high = close.iloc[j] > left.max() and close.iloc[j] > right.max()

        if not is_low and not is_high:
            continue

        # Prior pivot of same type within max_pivot_gap
        prior = None
        for k in range(j - 1, max(40, j - max_pivot_gap) - 1, -1):
            kl = close.iloc[k - swing_order: k].values
            kr = close.iloc[k + 1: k + swing_order + 1].values
            if len(kl) < swing_order or len(kr) < swing_order:
                continue
            if is_low and close.iloc[k] < kl.min() and close.iloc[k] < kr.min():
                prior = k
                break
            if is_high and close.iloc[k] > kl.max() and close.iloc[k] > kr.max():
                prior = k
                break
        if prior is None:
            continue

        entry = float(bar["close"])
        if is_low and close.iloc[j] < close.iloc[prior] and hist.iloc[j] > hist.iloc[prior]:
            stop = float(daily["low"].iloc[j]) * 0.999
            risk = entry - stop
            if risk > 0:
                target = entry + risk * target_r
                if ledger.enter(
                    symbol, entry, stop, target, "LONG",
                    meta={
                        "signal_date": str(daily.index[j].date()),
                        "prior_pivot": str(daily.index[prior].date()),
                    },
                ):
                    last_signal_pivot = j
                    hold_bars = 0
                    continue

        if is_high and close.iloc[j] > close.iloc[prior] and hist.iloc[j] < hist.iloc[prior]:
            stop = float(daily["high"].iloc[j]) * 1.001
            risk = stop - entry
            if risk > 0:
                target = entry - risk * target_r
                if ledger.enter(
                    symbol, entry, stop, target, "SHORT",
                    meta={
                        "signal_date": str(daily.index[j].date()),
                        "prior_pivot": str(daily.index[prior].date()),
                    },
                ):
                    last_signal_pivot = j
                    hold_bars = 0


# ---------------------------------------------------------------------------
# Pattern 3 — Bollinger Band Exhaustion (daily)
# ---------------------------------------------------------------------------

def run_bollinger_band_exhaustion(daily: pd.DataFrame, ledger: BacktestLedger,
                                  symbol: str, window: int = 20, num_std: float = 2.0,
                                  max_hold_days: int = 8, target_r: float = 1.5):
    """Fade an outside-band close once price closes back inside the bands.

    Long: prior close < lower band, current close > lower band (reclaim).
    Short: prior close > upper band, current close < upper band.
    Stop beyond the exhaustion extreme; target = mid-band or R-multiple,
    whichever is closer in the trade direction but still valid geometry.
    """
    if len(daily) < window + 5:
        return
    mid = daily["close"].rolling(window).mean()
    std = daily["close"].rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std

    hold_bars = 0
    for i in range(window + 1, len(daily)):
        bar = daily.iloc[i]
        if ledger.active_position:
            hold_bars += 1
            force = hold_bars >= max_hold_days
            ledger.check_exits(bar, eod_flatten=False, force_flatten=force)
            if not ledger.active_position:
                hold_bars = 0
            continue

        prev = daily.iloc[i - 1]
        u, l, m = upper.iloc[i], lower.iloc[i], mid.iloc[i]
        pu, pl = upper.iloc[i - 1], lower.iloc[i - 1]
        if any(pd.isna(x) for x in (u, l, m, pu, pl)):
            continue

        # Bullish exhaustion reclaim
        if prev["close"] < pl and bar["close"] > l:
            entry = float(bar["close"])
            stop = float(min(prev["low"], bar["low"])) * 0.999
            risk = entry - stop
            if risk <= 0:
                continue
            r_target = entry + risk * target_r
            mid_target = float(m)
            # Prefer mid if it is a valid profit target closer than r_target
            if mid_target > entry:
                target = min(r_target, mid_target) if mid_target < r_target else mid_target
                # If mid is beyond r_target keep r_target; if mid below entry skip to r
                if target <= entry:
                    target = r_target
            else:
                target = r_target
            if ledger.enter(
                symbol, entry, stop, target, "LONG",
                meta={"signal_date": str(bar.name.date())},
            ):
                hold_bars = 0
                continue

        # Bearish exhaustion reclaim
        if prev["close"] > pu and bar["close"] < u:
            entry = float(bar["close"])
            stop = float(max(prev["high"], bar["high"])) * 1.001
            risk = stop - entry
            if risk <= 0:
                continue
            r_target = entry - risk * target_r
            mid_target = float(m)
            if mid_target < entry:
                target = max(r_target, mid_target) if mid_target > r_target else mid_target
                if target >= entry:
                    target = r_target
            else:
                target = r_target
            if ledger.enter(
                symbol, entry, stop, target, "SHORT",
                meta={"signal_date": str(bar.name.date())},
            ):
                hold_bars = 0


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _print_table(summaries: list[dict]):
    print("\n" + "=" * 74)
    print(f"{'PATTERN':<28}{'TRADES':>8}{'WIN%':>8}{'NET P&L':>14}{'AVG/TRADE':>14}")
    print("=" * 74)
    for s in sorted(summaries, key=lambda r: r["net_pnl_usd"], reverse=True):
        print(
            f"{s['pattern']:<28}{s['trades']:>8}"
            f"{s['win_rate_pct']:>7.1f}%{s['net_pnl_usd']:>13.2f}$ "
            f"{s['avg_pnl_usd']:>12.2f}$"
        )
    print("=" * 74)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--start", default="2026-02-05")
    parser.add_argument("--end", default="2026-08-05")
    parser.add_argument(
        "--daily-lookback-start",
        default="2025-08-01",
        help="Extra daily history so MACD/BB indicators are warm at --start",
    )
    parser.add_argument("--out-json", default="")
    parser.add_argument("--minute-cache", default="/tmp/spy_6mo_bars_recheck.pkl")
    args = parser.parse_args()

    api_key = os.environ.get("POLYGON_API_KEY") or ""
    if not api_key and Path("/tmp/.polygon_key").exists():
        api_key = Path("/tmp/.polygon_key").read_text().strip()
        os.environ["POLYGON_API_KEY"] = api_key

    # --- Daily bars for MACD + Bollinger ---
    print(f"Fetching REAL daily bars for {args.symbol} "
          f"{args.daily_lookback_start} -> {args.end}…")
    daily_all = fetch_polygon_bars(
        args.symbol, args.daily_lookback_start, args.end, api_key, 1, "day"
    )
    print(f"Fetched {len(daily_all)} daily bars.")

    # Restrict tradeable window but keep indicator warm-up in the frame.
    start_ts = pd.Timestamp(args.start, tz="America/New_York")
    end_ts = pd.Timestamp(args.end, tz="America/New_York") + pd.Timedelta(days=1)

    macd_ledger = BacktestLedger("MACD_HIST_DIVERGENCE")
    bb_ledger = BacktestLedger("BB_EXHAUSTION")
    # Run on full warm series; entries before --start are skipped by masking:
    # we wrap enter to no-op before start via filtering after, cleaner to slice
    # active window inside runners by only calling check/enter from start.
    # Implement by running on full df but clearing early trades afterward.
    run_macd_histogram_divergence(daily_all, macd_ledger, args.symbol)
    run_bollinger_band_exhaustion(daily_all, bb_ledger, args.symbol)

    def _filter_window(ledger: BacktestLedger):
        kept = []
        bal = 0.0
        for t in ledger.trade_log:
            # Prefer signal_date (entry regime) when present; else exit timestamp.
            if t.get("signal_date"):
                ts = pd.Timestamp(t["signal_date"], tz="America/New_York")
            else:
                ts = pd.Timestamp(t["ts"])
                if ts.tzinfo is None:
                    ts = ts.tz_localize("America/New_York")
            if start_ts <= ts < end_ts:
                kept.append(t)
                bal += t["pnl_usd"]
        ledger.trade_log = kept
        ledger.balance_usd = bal
        ledger.active_position = None

    _filter_window(macd_ledger)
    _filter_window(bb_ledger)

    # --- 1-min bars for VWAP ---
    cache = Path(args.minute_cache)
    if cache.exists():
        print(f"Loading minute bars from cache {cache}…")
        minute = pd.read_pickle(cache)
        print(f"Cache has {len(minute)} bars / {minute.index.normalize().nunique()} sessions.")
    else:
        print(f"Fetching REAL 1-min bars for {args.symbol} {args.start} -> {args.end}…")
        minute = fetch_polygon_bars(args.symbol, args.start, args.end, api_key, 1, "minute")
        minute.to_pickle(cache)
        print(f"Fetched {len(minute)} bars / {minute.index.normalize().nunique()} sessions. "
              f"Cached → {cache}")

    vwap_ledger = BacktestLedger("VWAP_REVERSION_FIXED")
    vwap_buggy = BacktestLedger("VWAP_REVERSION_BUGGY_REF")

    trading_days = sorted(minute.index.normalize().unique())
    for day in trading_days:
        if day < start_ts or day >= end_ts:
            continue
        day_df = minute[minute.index.normalize() == day]
        if day_df.empty:
            continue
        run_vwap_reversion_fixed(day_df, vwap_ledger, args.symbol)
        run_vwap_reversion_buggy_ref(day_df, vwap_buggy, args.symbol)

    summaries = [
        vwap_ledger.summary(),
        macd_ledger.summary(),
        bb_ledger.summary(),
        vwap_buggy.summary(),
    ]
    # Attach VWAP reject diagnostic
    for s in summaries:
        if s["pattern"] == "VWAP_REVERSION_FIXED":
            s["rejected_beyond_stop_band"] = getattr(vwap_ledger, "_rejected_inverted", 0)

    _print_table(summaries)
    print("\nExit reason breakdown:")
    for s in summaries:
        print(f"  {s['pattern']}: {s['exit_reasons']}")

    # Geometry audit on buggy vs fixed
    def _inverted_count(ledger: BacktestLedger) -> int:
        n = 0
        for t in ledger.trade_log:
            if t["side"] == "SHORT" and t["stop"] < t["entry"]:
                n += 1
            elif t["side"] == "LONG" and t["stop"] > t["entry"]:
                n += 1
        return n

    print("\nVWAP geometry audit (stop on wrong side of entry):")
    print(f"  FIXED inverted stops: {_inverted_count(vwap_ledger)}")
    print(f"  BUGGY inverted stops: {_inverted_count(vwap_buggy)}")

    payload = {
        "symbol": args.symbol,
        "start": args.start,
        "end": args.end,
        "daily_lookback_start": args.daily_lookback_start,
        "fixed_risk_usd": 100.0,
        "minute_bars": int(len(minute)),
        "minute_sessions": int(minute.index.normalize().nunique()),
        "daily_bars": int(len(daily_all)),
        "summaries": summaries,
        "vwap_fixed_inverted_stops": _inverted_count(vwap_ledger),
        "vwap_buggy_inverted_stops": _inverted_count(vwap_buggy),
        "notes": [
            "VWAP_REVERSION_FIXED requires entry_sd <= |dev| < stop_sd (geometry gate).",
            "VWAP_REVERSION_BUGGY_REF is the prior Pattern Lab rule for comparison.",
            "MACD_HIST_DIVERGENCE and BB_EXHAUSTION run on daily SPY bars.",
        ],
    }

    out = args.out_json or str(
        Path(__file__).resolve().parents[2]
        / "docs/verification/spy-three-pattern-recheck.json"
    )
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
