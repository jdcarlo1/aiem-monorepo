"""
AIM Pattern Lab — Real Backtest Engine
Pulls REAL historical 1-min bars from Polygon (same API your AIEM system already
uses) and runs all 6 pattern rules against them. Fixed $100 risk per trade, no
compounding — this isolates which pattern has real edge from position sizing,
so the comparison across patterns is apples-to-apples.

THIS PRODUCES REAL NUMBERS ONLY IF RUN AGAINST REAL DATA. It has not been run
here — no network access in this environment. Run it in Cursor with your
Polygon API key to get genuine win rate / net P&L results.

Usage:
    export POLYGON_API_KEY=your_key_here
    python3 backtest_pattern_lab.py --symbol SPY --start 2026-02-05 --end 2026-08-05

Default window for AIM Pattern Lab ranking: last ~6 months of SPY 1-min bars.
Results (baseline + R sweeps): docs/verification/pattern-lab-backtest-6mo.md
"""

import os
import sys
import time
import argparse
import requests
import numpy as np
import pandas as pd

POLYGON_BASE = "https://api.polygon.io"


# ---------------------------------------------------------------------------
# Data fetch — real bars only, no synthetic fallback
# ---------------------------------------------------------------------------

def fetch_polygon_minute_bars(symbol: str, start: str, end: str, api_key: str) -> pd.DataFrame:
    """Pulls real 1-min aggregate bars from Polygon for [start, end] (YYYY-MM-DD).
    Paginates through Polygon's cursor-based results. Raises if the API key is
    missing or the request fails — this must fail loudly, never silently return
    empty/fake data. Fetches month-by-month with retries to stay under rate limits.
    """
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY not set — cannot fetch real data. "
                            "This script refuses to fall back to synthetic bars.")

    def _get(url, params=None, tries=8):
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
        raise RuntimeError(f"Polygon rate limit persisted after {tries} tries "
                           f"(last={getattr(last,'status_code',None)})")

    # Month chunks reduce single-request volume and help free-tier pacing.
    months = pd.date_range(start=start, end=end, freq="MS").strftime("%Y-%m-%d").tolist()
    if not months or months[0] != start:
        months = [start] + months
    if months[-1] != end:
        months.append(end)
    # unique ordered window edges
    edges = sorted(set(months))
    if edges[-1] < end:
        edges.append(end)

    all_rows = []
    for i in range(len(edges) - 1):
        chunk_start, chunk_end = edges[i], edges[i + 1]
        if chunk_start >= chunk_end:
            continue
        print(f"  fetching {symbol} {chunk_start} → {chunk_end}…")
        url = f"{POLYGON_BASE}/v2/aggs/ticker/{symbol}/range/1/minute/{chunk_start}/{chunk_end}"
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
        while url:
            resp = _get(url, params if params else None)
            data = resp.json()
            if data.get("status") not in ("OK", "DELAYED"):
                raise RuntimeError(
                    f"Polygon returned non-OK status: {data.get('status')} — {data.get('error')}"
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
        raise RuntimeError(f"Polygon returned zero bars for {symbol} {start}-{end}. "
                            f"Check symbol/date range/market hours before trusting any downstream result.")

    df = pd.DataFrame(all_rows)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert("America/New_York")
    df = df.set_index("timestamp").rename(columns={
        "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"
    })[["open", "high", "low", "close", "volume"]]
    # Deduplicate overlapping chunk edges
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


# ---------------------------------------------------------------------------
# Fixed-risk backtest ledger (NOT compounding — intentional, for fair comparison)
# ---------------------------------------------------------------------------

class BacktestLedger:
    def __init__(self, pattern_name: str, fixed_risk_usd: float = 100.0, slippage_usd: float = 0.04):
        self.pattern_name = pattern_name
        self.fixed_risk_usd = fixed_risk_usd
        self.slippage_usd = slippage_usd
        self.balance_usd = 0.0  # cumulative P&L only, not a real account balance
        self.trade_log = []
        self.active_position = None

    @property
    def total_trades(self):
        return len(self.trade_log)

    @property
    def wins(self):
        return sum(1 for t in self.trade_log if t["pnl_usd"] > 0)

    @property
    def losses(self):
        return sum(1 for t in self.trade_log if t["pnl_usd"] <= 0)

    @property
    def win_rate_pct(self):
        return (self.wins / self.total_trades * 100.0) if self.total_trades else 0.0

    def enter(self, symbol, entry, stop, target, side):
        if self.active_position:
            return
        if side == "LONG":
            p_entry, p_stop, p_target = entry + self.slippage_usd / 2, stop - self.slippage_usd / 2, target - self.slippage_usd / 2
        else:
            p_entry, p_stop, p_target = entry - self.slippage_usd / 2, stop + self.slippage_usd / 2, target + self.slippage_usd / 2

        risk_per_share = abs(p_entry - p_stop)
        if risk_per_share <= 0:
            return
        shares = int(self.fixed_risk_usd / risk_per_share)  # FIXED $100 risk, no compounding
        if shares <= 0:
            return
        self.active_position = {"symbol": symbol, "shares": shares, "side": side,
                                 "entry": p_entry, "stop": p_stop, "target": p_target}

    def check_exits(self, bar: pd.Series):
        pos = self.active_position
        if not pos:
            return
        high, low, close = bar["high"], bar["low"], bar["close"]
        bar_time = bar.name.strftime("%H:%M")

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

        if exit_price is None and bar_time >= "15:55":
            exit_price, reason = close, "EOD_FLATTEN"

        if exit_price is not None:
            pnl = (exit_price - pos["entry"]) * pos["shares"] if pos["side"] == "LONG" \
                else (pos["entry"] - exit_price) * pos["shares"]
            self.balance_usd += pnl
            self.trade_log.append({"side": pos["side"], "entry": pos["entry"], "exit": exit_price,
                                    "shares": pos["shares"], "pnl_usd": round(pnl, 2), "reason": reason})
            self.active_position = None


# ---------------------------------------------------------------------------
# Pattern signal generators (same rules as the live engines — literature-grounded)
# ---------------------------------------------------------------------------

def run_gap_fill(day_df: pd.DataFrame, prior_close: float, ledger: BacktestLedger, symbol: str):
    opening_15 = day_df.between_time("09:30", "09:45")
    if len(opening_15) < 15:
        return
    range_high, range_low = opening_15["high"].max(), opening_15["low"].min()
    open_price = day_df.between_time("09:30", "09:30").iloc[-1]["open"]
    gap_pct = abs(open_price - prior_close) / prior_close if prior_close else 0

    for _, bar in day_df.iterrows():
        if ledger.active_position:
            ledger.check_exits(bar)
            continue
        if bar.name.strftime("%H:%M") != "09:45":
            continue
        if gap_pct <= 0.0050 and abs(open_price - prior_close) >= 0.05:
            bias = "SHORT" if open_price > prior_close else "LONG"
            entry = opening_15.iloc[-1]["close"]
            stop = range_high * 1.0005 if bias == "SHORT" else range_low * 0.9995
            target = prior_close
            if abs(entry - target) / max(abs(entry - stop), 1e-9) >= 1.2:
                ledger.enter(symbol, entry, stop, target, bias)


def run_orb(day_df: pd.DataFrame, ledger: BacktestLedger, symbol: str,
            range_minutes: int, target_r: float, min_range_pct: float = 0.0, rvol_min: float = 0.0):
    end_time = (pd.Timestamp("09:30") + pd.Timedelta(minutes=range_minutes)).strftime("%H:%M")
    window = day_df.between_time("09:30", end_time)
    if len(window) < range_minutes:
        return
    range_high, range_low = window["high"].max(), window["low"].min()
    range_pct = (range_high - range_low) / window.iloc[0]["open"] if window.iloc[0]["open"] else 0
    if range_pct < min_range_pct:
        return

    post = day_df.between_time(end_time, "16:00")
    for _, bar in post.iterrows():
        if ledger.active_position:
            ledger.check_exits(bar)
            continue
        if rvol_min > 0:
            avg_vol = day_df["volume"].rolling(20).mean().get(bar.name, np.nan)
            if pd.isna(avg_vol) or avg_vol == 0 or (bar["volume"] / avg_vol) < rvol_min:
                continue
        if bar["close"] > range_high:
            stop = range_low * 0.9995
            target = bar["close"] + abs(bar["close"] - stop) * target_r
            ledger.enter(symbol, bar["close"], stop, target, "LONG")
        elif bar["close"] < range_low:
            stop = range_high * 1.0005
            target = bar["close"] - abs(stop - bar["close"]) * target_r
            ledger.enter(symbol, bar["close"], stop, target, "SHORT")


def run_vwap_reversion(day_df: pd.DataFrame, ledger: BacktestLedger, symbol: str,
                        adx_max: float = 20.0, entry_sd: float = 2.0, stop_sd: float = 3.0):
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
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
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
        if a >= adx_max:  # TREND FILTER — mandatory
            continue
        dev_sd = (bar["close"] - v) / s
        if dev_sd >= entry_sd:
            ledger.enter(symbol, bar["close"], v + s * stop_sd, v, "SHORT")
        elif dev_sd <= -entry_sd:
            ledger.enter(symbol, bar["close"], v - s * stop_sd, v, "LONG")


def run_liquidity_sweep(day_df: pd.DataFrame, ledger: BacktestLedger, symbol: str,
                         swing_lookback: int = 20, target_r: float = 1.5):
    window = day_df.between_time("12:00", "13:30")
    pre_lunch_full = day_df.between_time("09:30", "11:59")
    if len(window) < 3 or len(pre_lunch_full) < 5:
        return

    for i in range(2, len(window)):
        bar = window.iloc[i]
        if ledger.active_position:
            ledger.check_exits(bar)
            continue
        pre_lunch = pre_lunch_full.tail(swing_lookback)
        swing_high, swing_low = pre_lunch["high"].max(), pre_lunch["low"].min()
        sweep_bar, confirm_bar = window.iloc[i - 1], bar

        if sweep_bar["low"] < swing_low and confirm_bar["close"] > swing_low:
            stop = sweep_bar["low"]
            risk = confirm_bar["close"] - stop
            if risk > 0:
                ledger.enter(symbol, confirm_bar["close"], stop, confirm_bar["close"] + risk * target_r, "LONG")
        elif sweep_bar["high"] > swing_high and confirm_bar["close"] < swing_high:
            stop = sweep_bar["high"]
            risk = stop - confirm_bar["close"]
            if risk > 0:
                ledger.enter(symbol, confirm_bar["close"], stop, confirm_bar["close"] - risk * target_r, "SHORT")


# ---------------------------------------------------------------------------
# Main backtest driver
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    api_key = os.environ.get("POLYGON_API_KEY")
    print(f"Fetching REAL 1-min bars for {args.symbol} {args.start} -> {args.end} from Polygon...")
    bars = fetch_polygon_minute_bars(args.symbol, args.start, args.end, api_key)
    print(f"Fetched {len(bars)} real bars across {bars.index.normalize().nunique()} sessions.")

    ledgers = {
        "GAP_FILL": BacktestLedger("GAP_FILL"),
        "ORB": BacktestLedger("ORB"),
        "WEEKLY_MACRO_ORB": BacktestLedger("WEEKLY_MACRO_ORB"),
        "HIGH_BETA_ORB": BacktestLedger("HIGH_BETA_ORB"),
        "VWAP_REVERSION": BacktestLedger("VWAP_REVERSION"),
        "LIQUIDITY_SWEEP": BacktestLedger("LIQUIDITY_SWEEP"),
    }

    trading_days = sorted(bars.index.normalize().unique())
    prior_close = None

    for day in trading_days:
        day_df = bars[bars.index.normalize() == day]
        if day_df.empty:
            continue

        if prior_close is not None:
            run_gap_fill(day_df, prior_close, ledgers["GAP_FILL"], args.symbol)
        run_orb(day_df, ledgers["ORB"], args.symbol, range_minutes=15, target_r=2.0)
        run_orb(day_df, ledgers["WEEKLY_MACRO_ORB"], args.symbol, range_minutes=30, target_r=1.5, min_range_pct=0.0015)
        run_orb(day_df, ledgers["HIGH_BETA_ORB"], args.symbol, range_minutes=15, target_r=2.0, rvol_min=1.5)
        run_vwap_reversion(day_df, ledgers["VWAP_REVERSION"], args.symbol)
        run_liquidity_sweep(day_df, ledgers["LIQUIDITY_SWEEP"], args.symbol)

        prior_close = day_df.iloc[-1]["close"]

    print("\n" + "=" * 70)
    print(f"{'PATTERN':<18}{'TRADES':>8}{'WIN%':>8}{'NET P&L':>14}{'AVG/TRADE':>14}")
    print("=" * 70)
    results = []
    for name, ledger in ledgers.items():
        avg = (ledger.balance_usd / ledger.total_trades) if ledger.total_trades else 0
        results.append((name, ledger.total_trades, ledger.win_rate_pct, ledger.balance_usd, avg))
    for name, trades, win_rate, net, avg in sorted(results, key=lambda r: r[3], reverse=True):
        print(f"{name:<18}{trades:>8}{win_rate:>7.1f}%{net:>13.2f}$ {avg:>12.2f}$")
    print("=" * 70)
    print("Ranked by real net P&L over the actual test period, $100 fixed risk/trade, no compounding.")


if __name__ == "__main__":
    main()
