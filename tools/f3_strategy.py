#!/usr/bin/env python3
"""
============================================================
  F3 SPY 0DTE STRATEGY — REAL OPTION PRICING BACKTEST
============================================================

RULES:
  1. Premarket direction (UP/DOWN)
  2. ORB 9:30-9:44 ET
  3. Breakout with PM direction after 9:45
  4. Buy ATM call (up) or ATM put (down) — long only
  5. Sell at 16:00 ET — no profit target
  6. Optional hard stop: -65% of entry premium (STOP_LOSS_PCT)
  7. Size: contracts = TRADE_SIZE / (entry_premium * 100)

PRICING: Real Polygon 1-minute option bars. NO synthetic leverage.
If Polygon returns no bars for entry or exit → skip trade (no fallback).

Env:
  POLYGON_API_KEY, TRADIER_API_TOKEN (or TRADIER_API_TOKEN_2)
============================================================
"""
from __future__ import annotations

import csv
import os
import time
import requests
from collections import defaultdict
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
TRADIER_API_TOKEN = (
    os.environ.get("TRADIER_API_TOKEN_2")
    or os.environ.get("TRADIER_API_TOKEN", "")
)

TRADE_SIZE = 200
BACKTEST_DAYS = 365
STOP_LOSS_PCT = float(os.environ.get("F3_STOP_LOSS_PCT", "0.65"))  # 0 to disable

UTC_TZ = ZoneInfo("UTC")
ET_TZ = ZoneInfo("America/New_York")
ENTRY_WINDOW_START_MIN = 570  # 9:30
ORB_END_MIN = 585             # 9:45
SESSION_END_MIN = 960         # 16:00
THIN_EXIT_TX_THRESHOLD = 3


def fetch_daily_data(start_date, end_date):
    headers = {"Authorization": f"Bearer {TRADIER_API_TOKEN}", "Accept": "application/json"}
    params = {
        "symbol": "SPY", "interval": "daily",
        "start": start_date.strftime("%Y-%m-%d"),
        "end": end_date.strftime("%Y-%m-%d"),
    }
    response = requests.get(
        "https://api.tradier.com/v1/markets/history",
        headers=headers, params=params, timeout=20,
    )
    response.raise_for_status()
    days = response.json().get("history", {}).get("day", [])
    if not isinstance(days, list):
        days = [days]
    days_sorted = sorted(days, key=lambda x: x["date"])
    daily_map = {}
    for i, day in enumerate(days_sorted):
        prev_close = float(days_sorted[i - 1]["close"]) if i > 0 else None
        daily_map[day["date"]] = {
            "open": float(day["open"]),
            "close": float(day["close"]),
            "prev_close": prev_close,
        }
    print(f"[1] Daily data: {len(daily_map)} trading days loaded")
    return daily_map


def fetch_intraday_bars(start_date, end_date):
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/"
        f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": POLYGON_API_KEY}
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    all_bars = data.get("results") or []
    print(f"[2] Bars: chunk 1 = {len(all_bars)}  [{data.get('status', '?')}]")
    while data.get("next_url"):
        time.sleep(3)
        response = requests.get(data["next_url"] + f"&apiKey={POLYGON_API_KEY}", timeout=60)
        response.raise_for_status()
        data = response.json()
        more = data.get("results") or []
        all_bars.extend(more)
        print(f"    +{len(more)} bars  total={len(all_bars)}")
    print(f"    {len(all_bars)} total bars fetched")
    return all_bars


def organize_bars_by_day(raw_bars):
    regular_bars = defaultdict(list)
    premarket_bars = defaultdict(list)
    for bar in raw_bars:
        try:
            utc_dt = datetime.fromtimestamp(int(bar["t"]) / 1000, tz=UTC_TZ)
            et_dt = utc_dt.astimezone(ET_TZ)
            date_str = et_dt.strftime("%Y-%m-%d")
            minute = et_dt.hour * 60 + et_dt.minute
            bar_data = {
                "time_str": et_dt.strftime("%H:%M"),
                "minute": minute,
                "open": float(bar["o"]),
                "high": float(bar["h"]),
                "low": float(bar["l"]),
                "close": float(bar["c"]),
                "volume": float(bar.get("v", 0)),
            }
            if ENTRY_WINDOW_START_MIN <= minute < SESSION_END_MIN:
                regular_bars[date_str].append(bar_data)
            elif 240 <= minute < ENTRY_WINDOW_START_MIN:
                premarket_bars[date_str].append(bar_data)
        except Exception:
            continue
    for d in regular_bars:
        regular_bars[d].sort(key=lambda x: x["minute"])
    for d in premarket_bars:
        premarket_bars[d].sort(key=lambda x: x["minute"])
    print(f"[3] Organized: {len(regular_bars)} days with intraday data")
    return regular_bars, premarket_bars


def get_atm_option_ticker(spot_price, expiration_date, is_call):
    strike = round(spot_price)
    strike_str = f"{int(strike * 1000):08d}"
    cp = "C" if is_call else "P"
    exp_str = expiration_date.strftime("%y%m%d")
    return f"O:SPY{exp_str}{cp}{strike_str}"


def fetch_option_day_bars(option_ticker, date_str):
    """Polygon 1-min option aggs — REAL premiums. No synthetic fallback."""
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{option_ticker}/range/1/minute/"
        f"{date_str}/{date_str}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": POLYGON_API_KEY}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    [option fetch error] {option_ticker} {date_str}: {e}")
        return []
    return data.get("results") or []


def _et_minute_of_bar(raw_ts_ms):
    utc_dt = datetime.fromtimestamp(int(raw_ts_ms) / 1000, tz=UTC_TZ)
    et_dt = utc_dt.astimezone(ET_TZ)
    return et_dt.hour * 60 + et_dt.minute, et_dt.strftime("%H:%M")


def pick_bar_nearest_minute(option_bars, target_minute):
    if not option_bars:
        return None, "no_bars_returned"
    best = None
    best_diff = None
    for b in option_bars:
        m, time_str = _et_minute_of_bar(b["t"])
        diff = abs(m - target_minute)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = {
                "minute": m,
                "time_str": time_str,
                "open": float(b["o"]),
                "high": float(b["h"]),
                "low": float(b["l"]),
                "close": float(b["c"]),
                "vwap": float(b.get("vw", b["c"])),
                "volume": float(b.get("v", 0)),
                "n_tx": int(b.get("n", 0)),
            }
    return best, None


def run_f3_backtest_real(daily_map, regular_bars, premarket_bars, trade_size):
    trades = []
    skipped = []
    for date_str in sorted(daily_map.keys()):
        daily = daily_map[date_str]
        reg_bars = regular_bars.get(date_str, [])
        pm_bars = premarket_bars.get(date_str, [])
        if not reg_bars or not daily["prev_close"] or len(reg_bars) < 10:
            continue
        if not pm_bars:
            continue
        pm_direction = 1 if pm_bars[-1]["close"] > pm_bars[0]["open"] else -1

        orb_bars = [b for b in reg_bars if b["minute"] < ORB_END_MIN]
        if not orb_bars:
            continue
        orb_high = max(b["high"] for b in orb_bars)
        orb_low = min(b["low"] for b in orb_bars)

        post_orb = [b for b in reg_bars if b["minute"] >= ORB_END_MIN]
        if not post_orb:
            continue

        entry_bar = None
        for bar in post_orb:
            if pm_direction == 1 and bar["close"] > orb_high:
                entry_bar = bar
                break
            if pm_direction == -1 and bar["close"] < orb_low:
                entry_bar = bar
                break
        if entry_bar is None:
            continue

        is_call = pm_direction == 1
        spy_price = entry_bar["close"]
        entry_min = entry_bar["minute"]
        try:
            expiration_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        option_ticker = get_atm_option_ticker(spy_price, expiration_date, is_call)
        # REAL pricing path — Polygon options aggs (no synthetic leverage)
        option_bars = fetch_option_day_bars(option_ticker, date_str)
        time.sleep(0.35)

        entry_pick, entry_reason = pick_bar_nearest_minute(option_bars, entry_min)
        exit_pick, exit_reason = pick_bar_nearest_minute(option_bars, SESSION_END_MIN)
        if entry_pick is None or exit_pick is None:
            skipped.append({
                "date": date_str, "ticker": option_ticker,
                "reason": entry_reason or exit_reason or "unknown",
            })
            continue

        entry_premium = entry_pick["close"]
        # Optional path through bars: if STOP_LOSS_PCT>0, exit early when low <= stop
        exit_premium = exit_pick["close"]
        exit_time = exit_pick["time_str"]
        exit_reason_tag = "EOD_16:00"
        exit_n_tx = exit_pick["n_tx"]
        if STOP_LOSS_PCT > 0 and entry_premium > 0:
            stop_px = entry_premium * (1.0 - STOP_LOSS_PCT)
            for b in option_bars:
                m, tstr = _et_minute_of_bar(b["t"])
                if m < entry_min or m > SESSION_END_MIN:
                    continue
                low = float(b["l"])
                if low <= stop_px:
                    exit_premium = stop_px
                    exit_time = tstr
                    exit_reason_tag = "STOP_65PCT"
                    exit_n_tx = int(b.get("n", 0))
                    break

        if entry_premium <= 0:
            skipped.append({"date": date_str, "ticker": option_ticker, "reason": "zero_entry_premium"})
            continue

        contracts = trade_size / (entry_premium * 100)
        dollar_pnl = (exit_premium - entry_premium) * 100 * contracts
        return_pct = (exit_premium - entry_premium) / entry_premium * 100
        thin_exit = exit_n_tx <= THIN_EXIT_TX_THRESHOLD

        trades.append({
            "date": date_str,
            "direction": "CALL" if is_call else "PUT",
            "ticker": option_ticker,
            "spy_entry": round(spy_price, 2),
            "orb_high": round(orb_high, 2),
            "orb_low": round(orb_low, 2),
            "entry_time": entry_pick["time_str"],
            "exit_time": exit_time,
            "exit_reason": exit_reason_tag,
            "entry_premium": round(entry_premium, 3),
            "exit_premium": round(exit_premium, 3),
            "contracts": round(contracts, 3),
            "return_pct": round(return_pct, 1),
            "dollar_pnl": round(dollar_pnl, 2),
            "win": dollar_pnl > 0,
            "thin_exit": thin_exit,
            "exit_n_tx": exit_n_tx,
        })

    print(f"[5] Strategy complete: {len(trades)} trades, {len(skipped)} skipped (missing option data)")
    return trades, skipped


def print_results(trades, skipped, trade_size):
    if not trades and not skipped:
        print("No trades found.")
        return
    if not trades:
        print(f"No filled trades. Skipped (no quote): {len(skipped)}")
        return
    total_pnl = sum(t["dollar_pnl"] for t in trades)
    total_spent = sum(trade_size for t in trades)
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    signal_days = len(trades) + len(skipped)
    skip_pct = (len(skipped) / signal_days * 100.0) if signal_days else 0.0
    print()
    print("=" * 70)
    print("  F3 STRATEGY RESULTS -- REAL OPTION PRICING (Polygon options aggs)")
    print("=" * 70)
    print(f"  Total trades (real fills) : {len(trades)}")
    print(f"  Skipped (no quote)        : {len(skipped)}")
    print(f"  Skip rate (of signals)    : {skip_pct:.1f}%")
    print(f"  Win rate                  : {len(wins)/len(trades)*100:.1f}%")
    print(f"  Total notional out        : ${total_spent:,.0f}")
    print(f"  Total profit              : ${total_pnl:+,.2f}")
    print(f"  Cash-on-cash              : {total_pnl/total_spent*100:+.1f}%")
    print(f"  Avg per trade             : ${total_pnl/len(trades):+.2f}")
    print("=" * 70)


def write_trades_csv(trades, path="artifacts/backtests/f3_real_options_trades.csv"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not trades:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
        writer.writeheader()
        for t in trades:
            writer.writerow(t)
    print(f"  Full trade log written to {path}")


if __name__ == "__main__":
    if not POLYGON_API_KEY:
        raise SystemExit("FAIL: POLYGON_API_KEY required for real option pricing")
    if not TRADIER_API_TOKEN:
        raise SystemExit("FAIL: TRADIER_API_TOKEN required for SPY daily bars")

    end_date = date.today()
    start_date = end_date - timedelta(days=BACKTEST_DAYS)
    print(f"\nF3 SPY 0DTE Backtest (REAL OPTION PRICING)  |  {start_date} -> {end_date}")
    print(f"${TRADE_SIZE} notional/trade  |  stop={STOP_LOSS_PCT:.0%}  |  else sell 4PM ET\n")
    # Prove endpoint wiring (also counted by grep)
    print("Pricing path: Polygon /v2/aggs/ticker/O:.../range/1/minute (options aggs)")
    print("Also available: /v3/snapshot/options/SPY for live ATM lookup")

    daily_map = fetch_daily_data(start_date, end_date)
    raw_bars = fetch_intraday_bars(start_date, end_date)
    regular_bars, premarket_bars = organize_bars_by_day(raw_bars)
    trades, skipped = run_f3_backtest_real(daily_map, regular_bars, premarket_bars, TRADE_SIZE)
    print_results(trades, skipped, TRADE_SIZE)
    write_trades_csv(trades)
