#!/usr/bin/env python3
"""
Gamma Blast Strategy — AIEM / Polygon backtest handoff
Directive_GammaBlast_Backtest_2026-08-07

HOW THIS IS "TOLD" TO AIEM (no chat inbox):
  Place/run this script under artifacts/stock-scanner-api/ on the stock-api
  host (Replit) where POLYGON_API_KEY is set:
      python gamma_blast_backtest.py --days 20 --mode synthetic
      python gamma_blast_backtest.py --days 20 --mode real

Strategy (from user paste, fixes retained):
  - SPY 0DTE directional option after range compression + straddle expansion
  - Risk $100/trade, TP 3x premium, SL 50% of premium, time stop 45m
  - Entry window 09:30–14:30 ET

Pricing modes:
  - synthetic : Black-Scholes from underlying bars only — LOGIC sanity check.
                Do NOT report as real P&L.
  - real      : Polygon 1-min option aggregates for ATM 0DTE call/put
                (O:SPY{YYMMDD}{C|P}{strike*1000:08d}). Requires Options plan
                with historical minute aggregates (Starter+).

Does NOT place live broker orders. Does NOT touch D1/D2/D3 or Pattern Lab ledgers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    from scipy.stats import norm
except ImportError:
    print("ERROR: scipy required (Black-Scholes synthetic mode).", file=sys.stderr)
    sys.exit(2)

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except ImportError:
    _ET = None

POLYGON_BASE = "https://api.polygon.io"
RATE_SLEEP = 0.22

# Baseline knobs — keep every run's full config + trade log so we can
# compare variable sweeps later (do not discard results).
DEFAULT_CONFIG = {
    "ticker": "SPY",
    "risk_per_trade": 100.0,
    "take_profit_multiplier": 3.0,
    "stop_loss_pct": 0.50,
    "range_threshold": 0.01,
    "breakout_threshold": 0.05,
    "time_stop_minutes": 45,
    "entry_start": "09:30",
    "entry_end": "14:30",
    "risk_free_rate": 0.05,
    "iv_estimate": 0.20,
}

# Mutable copy used by the engine (overridden per CLI / sweep variant).
CONFIG = dict(DEFAULT_CONFIG)

ARCHIVE_DIR_NAME = "gamma-blast"


def _api_key() -> str:
    k = os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY") or ""
    if not k:
        raise SystemExit(
            "POLYGON_API_KEY not set — run on the stock-api / AIEM host that has the key."
        )
    return k


def _poly_get(path: str, params: Optional[dict] = None) -> dict:
    params = dict(params or {})
    params["apiKey"] = _api_key()
    url = f"{POLYGON_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "aiem-gamma-blast/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "results": []}


def fetch_spy_1m(day: date) -> pd.DataFrame:
    """Polygon SPY 1-min bars for one session (ET calendar date)."""
    d = day.isoformat()
    data = _poly_get(
        f"/v2/aggs/ticker/SPY/range/1/minute/{d}/{d}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    time.sleep(RATE_SLEEP)
    rows = data.get("results") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(
        "America/New_York"
    )
    df = df.set_index("timestamp").rename(
        columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}
    )
    # RTH only
    df = df.between_time("09:30", "16:00")
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _occ_symbol(day: date, strike: float, option_type: str) -> str:
    yy, mm, dd = day.strftime("%y"), day.strftime("%m"), day.strftime("%d")
    cp = "C" if option_type == "CALL" else "P"
    sk8 = f"{int(round(strike * 1000)):08d}"
    return f"O:SPY{yy}{mm}{dd}{cp}{sk8}"


def fetch_option_1m(day: date, strike: float, option_type: str) -> pd.Series:
    """Return Series of mid≈close prices indexed by ET timestamp for one contract."""
    d = day.isoformat()
    sym = urllib.parse.quote(_occ_symbol(day, strike, option_type))
    data = _poly_get(
        f"/v2/aggs/ticker/{sym}/range/1/minute/{d}/{d}",
        {"adjusted": "false", "sort": "asc", "limit": 50000},
    )
    time.sleep(RATE_SLEEP)
    rows = data.get("results") or []
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows)
    ts = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert("America/New_York")
    # Use close of minute bar as mark (trade-print mid proxy).
    return pd.Series(df["c"].astype(float).values, index=ts)


def black_scholes_price(S, K, T_years, r, sigma, option_type: str) -> float:
    if T_years <= 0:
        return max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T_years) / (sigma * np.sqrt(T_years))
    d2 = d1 - sigma * np.sqrt(T_years)
    if option_type == "CALL":
        return float(S * norm.cdf(d1) - K * np.exp(-r * T_years) * norm.cdf(d2))
    return float(K * np.exp(-r * T_years) * norm.cdf(-d2) - S * norm.cdf(-d1))


def get_atm_straddle(
    now_ts,
    underlying_price: float,
    expiry_ts,
    mode: str,
    day: date,
    opt_cache: dict,
    iv_estimate: float,
):
    strike = float(round(underlying_price))
    if mode == "real":
        key_c = (day, strike, "CALL")
        key_p = (day, strike, "PUT")
        if key_c not in opt_cache:
            opt_cache[key_c] = fetch_option_1m(day, strike, "CALL")
        if key_p not in opt_cache:
            opt_cache[key_p] = fetch_option_1m(day, strike, "PUT")
        call_s, put_s = opt_cache[key_c], opt_cache[key_p]
        if call_s.empty or put_s.empty:
            return None
        # nearest bar at or before now
        call_px = call_s.asof(now_ts)
        put_px = put_s.asof(now_ts)
        if pd.isna(call_px) or pd.isna(put_px) or call_px <= 0 or put_px <= 0:
            return None
        call_price, put_price = float(call_px), float(put_px)
    else:
        T_years = max((expiry_ts - now_ts).total_seconds(), 0) / (3600 * 6.5 * 252)
        call_price = black_scholes_price(
            underlying_price, strike, T_years, CONFIG["risk_free_rate"], iv_estimate, "CALL"
        )
        put_price = black_scholes_price(
            underlying_price, strike, T_years, CONFIG["risk_free_rate"], iv_estimate, "PUT"
        )

    return {
        "strike": strike,
        "call_price": call_price,
        "put_price": put_price,
        "straddle_total": call_price + put_price,
    }


def run_backtest_day(underlying_bars: pd.DataFrame, mode: str, day: date) -> list:
    trades = []
    open_trade = None
    straddle_history = []
    opt_cache: dict = {}

    if underlying_bars.empty:
        return trades

    expiry_ts = pd.Timestamp(datetime.combine(day, datetime.strptime("16:00", "%H:%M").time()))
    if _ET is not None:
        expiry_ts = expiry_ts.tz_localize(_ET)
    else:
        expiry_ts = expiry_ts.tz_localize("America/New_York")

    entry_start = pd.Timestamp(
        datetime.combine(day, datetime.strptime(CONFIG["entry_start"], "%H:%M").time()),
        tz=_ET or "America/New_York",
    )
    entry_end = pd.Timestamp(
        datetime.combine(day, datetime.strptime(CONFIG["entry_end"], "%H:%M").time()),
        tz=_ET or "America/New_York",
    )

    for ts, bar in underlying_bars.iterrows():
        price = float(bar["Close"])

        if open_trade is not None:
            elapsed_min = (ts - open_trade["entry_time"]).total_seconds() / 60
            straddle = get_atm_straddle(
                ts, price, expiry_ts, mode, day, opt_cache, CONFIG["iv_estimate"]
            )
            if straddle is None:
                continue
            leg = straddle["call_price"] if open_trade["type"] == "CALL" else straddle["put_price"]
            current_value = leg * open_trade["contracts"] * 100

            exit_reason = None
            if current_value >= open_trade["take_profit_price"]:
                exit_reason = "TAKE_PROFIT"
            elif current_value <= open_trade["stop_loss_price"]:
                exit_reason = "STOP_LOSS"
            elif elapsed_min > CONFIG["time_stop_minutes"]:
                exit_reason = "TIME_STOP"
            elif ts >= expiry_ts:
                exit_reason = "EXPIRY"

            if exit_reason:
                open_trade.update(
                    {
                        "exit_time": ts,
                        "exit_value": current_value,
                        "exit_reason": exit_reason,
                        "pnl": current_value - open_trade["premium"],
                        "pricing_mode": mode,
                    }
                )
                trades.append(open_trade)
                open_trade = None
            continue

        if not (entry_start <= ts <= entry_end):
            continue

        window = underlying_bars.loc[:ts].tail(30)
        if len(window) < 5:
            continue
        range_pct = (window["High"].max() - window["Low"].min()) / price
        if range_pct >= CONFIG["range_threshold"]:
            continue

        straddle = get_atm_straddle(
            ts, price, expiry_ts, mode, day, opt_cache, CONFIG["iv_estimate"]
        )
        if straddle is None:
            continue
        straddle_history.append((ts, straddle["straddle_total"]))
        if len(straddle_history) < 2:
            continue

        prev_total = straddle_history[-2][1]
        curr_total = straddle_history[-1][1]
        if prev_total <= 0:
            continue
        expansion = (curr_total - prev_total) / prev_total
        if expansion <= CONFIG["breakout_threshold"]:
            continue

        trend_window = underlying_bars.loc[:ts].tail(5)
        price_change = (
            trend_window["Close"].iloc[-1] - trend_window["Close"].iloc[0]
        ) / trend_window["Close"].iloc[0]
        if price_change > 0.002:
            direction = "CALL"
            entry_price = straddle["call_price"]
        elif price_change < -0.002:
            direction = "PUT"
            entry_price = straddle["put_price"]
        else:
            continue

        if entry_price <= 0:
            continue
        contracts = max(int(CONFIG["risk_per_trade"] / (entry_price * 100)), 1)
        premium = entry_price * contracts * 100

        open_trade = {
            "trade_date": day.isoformat(),
            "type": direction,
            "strike": straddle["strike"],
            "contracts": contracts,
            "entry_time": ts,
            "entry_underlying": price,
            "entry_option_price": entry_price,
            "premium": premium,
            "take_profit_price": premium * CONFIG["take_profit_multiplier"],
            "stop_loss_price": premium * (1 - CONFIG["stop_loss_pct"]),
        }

    if open_trade is not None:
        last_ts = underlying_bars.index[-1]
        last_price = float(underlying_bars["Close"].iloc[-1])
        straddle = get_atm_straddle(
            last_ts, last_price, expiry_ts, mode, day, opt_cache, CONFIG["iv_estimate"]
        )
        if straddle is not None:
            leg = (
                straddle["call_price"]
                if open_trade["type"] == "CALL"
                else straddle["put_price"]
            )
            current_value = leg * open_trade["contracts"] * 100
            open_trade.update(
                {
                    "exit_time": last_ts,
                    "exit_value": current_value,
                    "exit_reason": "FORCED_EOD",
                    "pnl": current_value - open_trade["premium"],
                    "pricing_mode": mode,
                }
            )
            trades.append(open_trade)

    return trades


def trading_days_back(n: int, end: Optional[date] = None) -> list:
    """Weekday calendar list (no holiday calendar) — good enough for SPY probe."""
    end = end or date.today()
    out = []
    d = end
    # if weekend, step back
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _jsonable_trades(trades: list) -> list:
    """Serialize timestamps so full ledgers survive for later variable sweeps."""
    out = []
    for t in trades:
        row = dict(t)
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif isinstance(v, (np.floating, np.integer)):
                row[k] = float(v) if isinstance(v, np.floating) else int(v)
        out.append(row)
    return out


def summarize(trades: list, mode: str) -> dict:
    banner = (
        "=== SYNTHETIC MODE — logic check only; NOT real P&L ==="
        if mode == "synthetic"
        else "=== REAL MODE — Polygon 1-min option aggregates ==="
    )
    print(banner)
    if not trades:
        print("No trades generated.")
        return {"trades": 0, "mode": mode, "total_pnl": 0.0, "win_rate": None}

    df = pd.DataFrame(trades)
    total_pnl = float(df["pnl"].sum())
    win_rate = float((df["pnl"] > 0).mean())
    print(f"Trades: {len(df)}")
    print(f"Win rate: {win_rate:.1%}")
    print(f"Total P&L: ${total_pnl:.2f}")
    print(f"Avg P&L/trade: ${df['pnl'].mean():.2f}")
    cols = [
        c
        for c in [
            "trade_date",
            "entry_time",
            "type",
            "premium",
            "exit_reason",
            "pnl",
            "pricing_mode",
        ]
        if c in df.columns
    ]
    print(df[cols].to_string(index=False))
    return {
        "trades": int(len(df)),
        "mode": mode,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 4),
        "avg_pnl": round(float(df["pnl"].mean()), 2),
    }


def archive_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    ver = root / "docs" / "verification" / ARCHIVE_DIR_NAME
    ver.mkdir(parents=True, exist_ok=True)
    return ver


def save_run_archive(
    *,
    mode: str,
    days: list,
    days_with_bars: int,
    trades: list,
    summary: dict,
    label: str,
    out_override: str = "",
) -> Path:
    """
    Persist FULL run (config + every trade + summary). Never summary-only —
    needed so Joel can re-sweep variables later without re-pulling history.
    """
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    end = days[-1].isoformat() if days else "na"
    base = f"gamma-blast-{label}-{mode}-{end}-{stamp}"
    if out_override:
        path = Path(out_override)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = archive_root() / f"{base}.json"

    payload = {
        "strategy": "GAMMA_BLAST",
        "label": label,
        "saved_utc": stamp,
        "pricing_mode": mode,
        "disclaimer": (
            "synthetic = logic check only, not real P&L"
            if mode == "synthetic"
            else "real = Polygon 1-min option aggregates"
        ),
        "days_requested": len(days),
        "days_with_bars": days_with_bars,
        "start": days[0].isoformat() if days else None,
        "end": end,
        "config": dict(CONFIG),
        "default_config": dict(DEFAULT_CONFIG),
        "summary": summary,
        "trades": _jsonable_trades(trades),
        "trade_count": len(trades),
    }
    path.write_text(json.dumps(payload, indent=2, default=str))

    # Rolling pointer to latest run for easy follow-up sweeps
    latest = archive_root() / f"LATEST-{mode}.json"
    latest.write_text(json.dumps({"path": str(path), **payload}, indent=2, default=str))

    # Append index row for comparing variants later
    index_path = archive_root() / "RUN_INDEX.jsonl"
    with index_path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "saved_utc": stamp,
                    "path": str(path),
                    "label": label,
                    "mode": mode,
                    "trades": summary.get("trades"),
                    "total_pnl": summary.get("total_pnl"),
                    "win_rate": summary.get("win_rate"),
                    "config": dict(CONFIG),
                },
                default=str,
            )
            + "\n"
        )
    print(f"[gamma_blast] ARCHIVED full ledger → {path}")
    print(f"[gamma_blast] LATEST pointer → {latest}")
    print(f"[gamma_blast] index append → {index_path}")
    return path


def apply_config_overrides(args) -> str:
    """Apply CLI knobs onto CONFIG; return a short label for the archive name."""
    global CONFIG
    CONFIG = dict(DEFAULT_CONFIG)
    overrides = {}
    if args.risk_per_trade is not None:
        CONFIG["risk_per_trade"] = float(args.risk_per_trade)
        overrides["risk"] = CONFIG["risk_per_trade"]
    if args.take_profit is not None:
        CONFIG["take_profit_multiplier"] = float(args.take_profit)
        overrides["tp"] = CONFIG["take_profit_multiplier"]
    if args.stop_loss is not None:
        CONFIG["stop_loss_pct"] = float(args.stop_loss)
        overrides["sl"] = CONFIG["stop_loss_pct"]
    if args.range_threshold is not None:
        CONFIG["range_threshold"] = float(args.range_threshold)
        overrides["range"] = CONFIG["range_threshold"]
    if args.breakout_threshold is not None:
        CONFIG["breakout_threshold"] = float(args.breakout_threshold)
        overrides["brk"] = CONFIG["breakout_threshold"]
    if args.time_stop is not None:
        CONFIG["time_stop_minutes"] = int(args.time_stop)
        overrides["tstop"] = CONFIG["time_stop_minutes"]
    if not overrides:
        return args.label or "baseline"
    tag = args.label or "custom"
    bits = "-".join(f"{k}{v}" for k, v in overrides.items())
    return f"{tag}-{bits}"


def run_window(mode: str, day_list: list) -> tuple[list, int]:
    all_trades = []
    days_with_bars = 0
    for d in day_list:
        bars = fetch_spy_1m(d)
        if bars.empty:
            print(f"  {d}: no SPY bars (skip)")
            continue
        days_with_bars += 1
        day_trades = run_backtest_day(bars, mode, d)
        print(f"  {d}: bars={len(bars)} trades={len(day_trades)}")
        all_trades.extend(day_trades)
    return all_trades, days_with_bars


def main():
    ap = argparse.ArgumentParser(description="Gamma Blast Polygon backtest (AIEM handoff)")
    ap.add_argument("--days", type=int, default=20, help="Trading days lookback")
    ap.add_argument(
        "--mode",
        choices=("synthetic", "real"),
        default="synthetic",
        help="synthetic=BS logic check; real=Polygon option 1m aggs",
    )
    ap.add_argument(
        "--out",
        default="",
        help="Optional explicit archive path (still writes LATEST + RUN_INDEX)",
    )
    ap.add_argument("--label", default="", help="Archive label (e.g. baseline, sweep-tp2)")
    ap.add_argument("--risk-per-trade", type=float, default=None)
    ap.add_argument("--take-profit", type=float, default=None, help="Premium multiple, e.g. 3.0")
    ap.add_argument("--stop-loss", type=float, default=None, help="Fraction of premium, e.g. 0.50")
    ap.add_argument("--range-threshold", type=float, default=None)
    ap.add_argument("--breakout-threshold", type=float, default=None)
    ap.add_argument("--time-stop", type=int, default=None, help="Minutes")
    ap.add_argument(
        "--sweep-quick",
        action="store_true",
        help="Run a small TP/SL grid and archive each variant (keeps all results)",
    )
    args = ap.parse_args()

    days = trading_days_back(args.days)

    if args.sweep_quick:
        # Small grid for later "best settings" comparison — every variant archived.
        grid = [
            {"take_profit_multiplier": tp, "stop_loss_pct": sl}
            for tp in (2.0, 3.0, 4.0)
            for sl in (0.35, 0.50, 0.65)
        ]
        print(
            f"[gamma_blast] SWEEP mode={args.mode} days={args.days} "
            f"variants={len(grid)} range={days[0]}→{days[-1]}"
        )
        if args.mode == "synthetic":
            print("[gamma_blast] WARNING: synthetic — compare ranks, not dollar P&L.")
        for i, knobs in enumerate(grid, 1):
            global CONFIG
            CONFIG = dict(DEFAULT_CONFIG)
            CONFIG.update(knobs)
            label = f"sweep-tp{knobs['take_profit_multiplier']}-sl{knobs['stop_loss_pct']}"
            print(f"\n--- variant {i}/{len(grid)} {label} ---")
            trades, n_bars = run_window(args.mode, days)
            summary = summarize(trades, args.mode)
            save_run_archive(
                mode=args.mode,
                days=days,
                days_with_bars=n_bars,
                trades=trades,
                summary=summary,
                label=label,
            )
        print(f"\n[gamma_blast] sweep complete — see {archive_root()}/RUN_INDEX.jsonl")
        return 0

    label = apply_config_overrides(args)
    print(
        f"[gamma_blast] mode={args.mode} days={args.days} label={label} "
        f"range={days[0]}→{days[-1]} ticker={CONFIG['ticker']} "
        f"risk=${CONFIG['risk_per_trade']}"
    )
    if args.mode == "synthetic":
        print(
            "[gamma_blast] WARNING: synthetic Black-Scholes — report as logic check only."
        )

    all_trades, days_with_bars = run_window(args.mode, days)
    summary = summarize(all_trades, args.mode)
    save_run_archive(
        mode=args.mode,
        days=days,
        days_with_bars=days_with_bars,
        trades=all_trades,
        summary=summary,
        label=label,
        out_override=args.out,
    )
    return 0 if days_with_bars else 1


if __name__ == "__main__":
    sys.exit(main())
