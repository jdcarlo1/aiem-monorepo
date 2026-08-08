"""
f3_followthrough.py
On F3 monster days (real option return >100%), does the next trading
day also have an F3 signal, and does it follow through?

Uses real Polygon 1-min option bars for all trades.
"""


from aiem_broker.tradier_config import TRADIER_API_BASE

import os, time, requests
from datetime import datetime, timedelta, date
from collections import defaultdict

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
TRADIER_TOKEN   = (os.environ.get("TRADIER_API_TOKEN_2") or
                   os.environ.get("TRADIER_API_TOKEN", ""))
TRADE_SIZE      = 200
BACKTEST_DAYS   = 730
API_DELAY       = 0.25
MONSTER_THRESH  = 1.00   # 100% gain on the option


def _et_minute(ts_ms):
    utc_dt    = datetime.utcfromtimestamp(int(ts_ms) / 1000)
    is_winter = utc_dt.month in (11, 12, 1, 2, 3)
    et_dt     = utc_dt - timedelta(hours=5 if is_winter else 4)
    return et_dt.hour * 60 + et_dt.minute

def get_atm_ticker(spot, exp_date, is_call):
    s  = f"{int(round(spot) * 1000):08d}"
    cp = "C" if is_call else "P"
    return f"O:SPY{exp_date.strftime('%y%m%d')}{cp}{s}"

def fetch_daily(start_date, end_date):
    print("[1] Fetching SPY daily data...")
    h = {"Authorization": f"Bearer {TRADIER_TOKEN}", "Accept": "application/json"}
    p = {"symbol": "SPY", "interval": "daily",
         "start": start_date.strftime("%Y-%m-%d"),
         "end":   end_date.strftime("%Y-%m-%d")}
    r    = requests.get(f"{TRADIER_API_BASE}/v1/markets/history",
                        headers=h, params=p, timeout=20)
    days = r.json().get("history", {}).get("day", [])
    if not isinstance(days, list): days = [days]
    days = sorted(days, key=lambda x: x["date"])
    dm   = {}
    for i, d in enumerate(days):
        pc = float(days[i-1]["close"]) if i > 0 else None
        dm[d["date"]] = {"open": float(d["open"]), "close": float(d["close"]),
                         "prev_close": pc}
    print(f"    → {len(dm)} days")
    return dm

def fetch_intraday(start_date, end_date):
    print("[2] Fetching 5-min SPY bars...")
    url = (f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/"
           f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}")
    p    = {"adjusted": "true", "sort": "asc", "limit": 50000,
            "apiKey": POLYGON_API_KEY}
    r    = requests.get(url, params=p, timeout=60)
    data = r.json()
    bars = data.get("results") or []
    while data.get("next_url"):
        time.sleep(2)
        r    = requests.get(data["next_url"] + f"&apiKey={POLYGON_API_KEY}", timeout=60)
        data = r.json()
        bars.extend(data.get("results") or [])
    print(f"    → {len(bars)} bars")
    return bars

def organize(raw_bars):
    print("[3] Organizing...")
    reg = defaultdict(list); pm = defaultdict(list)
    for b in raw_bars:
        try:
            utc_dt    = datetime.utcfromtimestamp(int(b["t"]) / 1000)
            is_winter = utc_dt.month in (11, 12, 1, 2, 3)
            et_dt     = utc_dt - timedelta(hours=5 if is_winter else 4)
            ds = et_dt.strftime("%Y-%m-%d")
            mn = et_dt.hour * 60 + et_dt.minute
            bd = {"minute": mn, "time_str": et_dt.strftime("%H:%M"),
                  "open": float(b["o"]), "high": float(b["h"]),
                  "low":  float(b["l"]), "close": float(b["c"])}
            if 570 <= mn < 960:   reg[ds].append(bd)
            elif 240 <= mn < 570: pm[ds].append(bd)
        except: continue
    for d in reg: reg[d].sort(key=lambda x: x["minute"])
    for d in pm:  pm[d].sort(key=lambda x: x["minute"])
    print(f"    → {len(reg)} regular-hours days")
    return reg, pm

def fetch_option_bars(ticker, date_str):
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
           f"/range/1/minute/{date_str}/{date_str}")
    p   = {"adjusted": "true", "sort": "asc", "limit": 5000,
           "apiKey": POLYGON_API_KEY}
    try:
        r   = requests.get(url, params=p, timeout=30)
        raw = r.json()
    except Exception as e:
        raw = {"error": str(e)}
    return raw.get("results") or []

def detect_signal(date_str, daily_map, regular_bars, premarket_bars):
    """Returns trade dict or None if no signal."""
    daily  = daily_map.get(date_str, {})
    reg_bs = regular_bars.get(date_str, [])
    pm_bs  = premarket_bars.get(date_str, [])
    if not reg_bs or not daily.get("prev_close") or len(reg_bs) < 10:
        return None
    if not pm_bs:
        return None
    pm_dir   = 1 if pm_bs[-1]["close"] > pm_bs[0]["open"] else -1
    orb_bs   = [b for b in reg_bs if b["minute"] < 585]
    if not orb_bs: return None
    orb_high = max(b["high"] for b in orb_bs)
    orb_low  = min(b["low"]  for b in orb_bs)
    post_orb = [b for b in reg_bs if b["minute"] >= 585]
    if not post_orb: return None
    entry_bar = None
    for bar in post_orb:
        if pm_dir == 1  and bar["close"] > orb_high: entry_bar = bar; break
        if pm_dir == -1 and bar["close"] < orb_low:  entry_bar = bar; break
    if entry_bar is None: return None
    return {
        "date":         date_str,
        "direction":    "CALL" if pm_dir == 1 else "PUT",
        "is_call":      pm_dir == 1,
        "spy_price":    entry_bar["close"],
        "entry_minute": entry_bar["minute"],
        "final_spy":    post_orb[-1]["close"],
        "trade_date":   datetime.strptime(date_str, "%Y-%m-%d").date(),
    }

def price_trade(sig, fetch_bars=True):
    """Add real option entry/exit prices to a signal dict."""
    opt_ticker = get_atm_ticker(sig["spy_price"], sig["trade_date"], sig["is_call"])
    sig["opt_ticker"] = opt_ticker
    if not fetch_bars:
        return sig
    day_bars = fetch_option_bars(opt_ticker, sig["date"])
    time.sleep(API_DELAY)
    if not day_bars:
        sig["entry_px"] = None; return sig
    eb = min(day_bars, key=lambda b: abs(_et_minute(b["t"]) - sig["entry_minute"]))
    xb = min(day_bars, key=lambda b: abs(_et_minute(b["t"]) - 960))
    entry_px = float(eb["c"])
    exit_px  = float(xb["c"])
    if entry_px <= 0:
        sig["entry_px"] = None; return sig
    sig["entry_px"]  = round(entry_px, 4)
    sig["exit_px"]   = round(exit_px, 4)
    sig["opt_ret"]   = (exit_px - entry_px) / entry_px
    sig["dollar_pnl"]= round((exit_px - entry_px) / entry_px * TRADE_SIZE, 2)
    sig["win"]       = exit_px > entry_px
    return sig


def run(daily_map, regular_bars, premarket_bars):
    print("[4] Running F3 — finding monster days + next-day follow-through...")

    all_dates    = sorted(d for d in daily_map if regular_bars.get(d))
    date_index   = {d: i for i, d in enumerate(all_dates)}

    # First pass: price all signal days
    all_trades = {}
    for date_str in all_dates:
        sig = detect_signal(date_str, daily_map, regular_bars, premarket_bars)
        if sig:
            sig = price_trade(sig)
            if sig.get("entry_px"):
                all_trades[date_str] = sig

    print(f"    → {len(all_trades)} trades priced")

    # Second pass: find monster days and check next trading day
    monsters = {d: t for d, t in all_trades.items()
                if t.get("opt_ret", 0) >= MONSTER_THRESH}
    print(f"    → {len(monsters)} monster days (option +{int(MONSTER_THRESH*100)}%+)")

    results = []
    for date_str in sorted(monsters):
        monster   = monsters[date_str]
        idx       = date_index[date_str]
        # find next trading day with regular-hours data
        next_days = [all_dates[j] for j in range(idx+1, min(idx+4, len(all_dates)))]
        next_sig  = None
        next_trade= None
        for nd in next_days:
            sig = detect_signal(nd, daily_map, regular_bars, premarket_bars)
            if sig:
                next_sig   = sig
                next_trade = price_trade(sig)
                break

        results.append({
            "monster_date":     date_str,
            "monster_dir":      monster["direction"],
            "monster_entry":    monster["entry_px"],
            "monster_exit":     monster["exit_px"],
            "monster_ret_pct":  round(monster["opt_ret"] * 100, 1),
            "monster_pnl":      monster["dollar_pnl"],
            "next_date":        next_sig["date"] if next_sig else "—",
            "next_dir":         next_sig["direction"] if next_sig else "no signal",
            "next_entry":       next_trade.get("entry_px")  if next_trade else None,
            "next_exit":        next_trade.get("exit_px")   if next_trade else None,
            "next_ret_pct":     round(next_trade.get("opt_ret", 0) * 100, 1)
                                if next_trade and next_trade.get("entry_px") else None,
            "next_pnl":         next_trade.get("dollar_pnl") if next_trade else None,
            "next_win":         next_trade.get("win") if next_trade else None,
            "same_direction":   (next_sig["direction"] == monster["direction"])
                                if next_sig else None,
        })

    return results


def print_report(results):
    if not results:
        print("No monster days found."); return

    n = len(results)
    has_signal   = [r for r in results if r["next_date"] != "—"]
    no_signal    = [r for r in results if r["next_date"] == "—"]
    has_priced   = [r for r in has_signal if r["next_entry"] is not None]
    next_wins    = [r for r in has_priced if r["next_win"]]
    next_losses  = [r for r in has_priced if not r["next_win"]]
    same_dir     = [r for r in has_signal if r["same_direction"]]
    opp_dir      = [r for r in has_signal if r["same_direction"] is False]

    print()
    print("=" * 80)
    print(f"  MONSTER DAY FOLLOW-THROUGH STUDY")
    print(f"  Monster = F3 option return ≥ +{int(MONSTER_THRESH*100)}%  |  2-year window")
    print("=" * 80)

    print()
    print(f"  Total monster days found          : {n}")
    print(f"  Next trading day had F3 signal    : {len(has_signal)}  ({len(has_signal)/n*100:.0f}%)")
    print(f"  Next day had NO F3 signal         : {len(no_signal)}  ({len(no_signal)/n*100:.0f}%)")

    if has_priced:
        wr = len(next_wins) / len(has_priced) * 100
        avg_ret = sum(r["next_ret_pct"] for r in has_priced) / len(has_priced)
        avg_pnl = sum(r["next_pnl"] for r in has_priced) / len(has_priced)
        tot_pnl = sum(r["next_pnl"] for r in has_priced)
        print(f"\n  Of next-day signals with real data ({len(has_priced)}):")
        print(f"    Win rate                        : {wr:.1f}%")
        print(f"    Avg option return               : {avg_ret:+.1f}%")
        print(f"    Avg dollar P&L ($200/trade)     : ${avg_pnl:+.2f}")
        print(f"    Total P&L next-day trades       : ${tot_pnl:+.2f}")
        print(f"    Same direction as monster day   : {len(same_dir)} / {len(has_signal)}")
        print(f"    Opposite direction              : {len(opp_dir)} / {len(has_signal)}")

        if has_priced:
            same_priced = [r for r in has_priced if r["same_direction"]]
            opp_priced  = [r for r in has_priced if not r["same_direction"]]
            if same_priced:
                sp_wr  = sum(1 for r in same_priced if r["next_win"]) / len(same_priced) * 100
                sp_avg = sum(r["next_pnl"] for r in same_priced) / len(same_priced)
                print(f"\n    Same-direction next-day trades  : {len(same_priced)}")
                print(f"      Win rate                      : {sp_wr:.1f}%")
                print(f"      Avg P&L                       : ${sp_avg:+.2f}")
            if opp_priced:
                op_wr  = sum(1 for r in opp_priced if r["next_win"]) / len(opp_priced) * 100
                op_avg = sum(r["next_pnl"] for r in opp_priced) / len(opp_priced)
                print(f"\n    Opposite-direction next-day     : {len(opp_priced)}")
                print(f"      Win rate                      : {op_wr:.1f}%")
                print(f"      Avg P&L                       : ${op_avg:+.2f}")

    # ── full table ────────────────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("  FULL TABLE — each monster day + next-day result")
    print("=" * 80)
    print(f"  {'Monster Date':<13} {'Dir':<5} {'Ret%':>7} {'PnL$':>8}  │  "
          f"{'Next Date':<13} {'Dir':<5} {'Ret%':>7} {'PnL$':>8}  {'W/L':<5} {'SameDir'}")
    print("  " + "─" * 90)
    for r in sorted(results, key=lambda x: x["monster_date"]):
        m_str = f"{r['monster_ret_pct']:>+6.0f}%  ${r['monster_pnl']:>+7.2f}"
        if r["next_entry"] is not None:
            wl       = "WIN" if r["next_win"] else "loss"
            same_str = "same" if r["same_direction"] else "FLIP"
            n_str    = (f"{r['next_date']:<13} {r['next_dir']:<5} "
                        f"{r['next_ret_pct']:>+6.0f}%  ${r['next_pnl']:>+7.2f}  "
                        f"{wl:<5} {same_str}")
        elif r["next_date"] != "—":
            n_str = f"{r['next_date']:<13} {r['next_dir']:<5} {'no data':>14}"
        else:
            n_str = "  no signal next day"
        print(f"  {r['monster_date']:<13} {r['monster_dir']:<5} {m_str}  │  {n_str}")

    # ── comparison vs baseline ────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("  VS BASELINE F3 (all trades, no filter)")
    print("=" * 80)
    print(f"  Baseline F3 win rate (2-yr)     : ~39-43%")
    if has_priced:
        wr = len(next_wins) / len(has_priced) * 100
        print(f"  Post-monster next-day win rate  : {wr:.1f}%")
        if wr > 45:
            print(f"  → YES — next day after a monster has higher win rate than baseline")
        elif wr < 35:
            print(f"  → NO  — next day after a monster actually underperforms baseline")
        else:
            print(f"  → NEUTRAL — next day is in line with baseline, no edge")


if __name__ == "__main__":
    end_date   = date.today()
    start_date = end_date - timedelta(days=BACKTEST_DAYS)

    print()
    print("=" * 72)
    print("  F3 MONSTER DAY FOLLOW-THROUGH")
    print(f"  {start_date} → {end_date}")
    print(f"  Monster threshold: option return ≥ +{int(MONSTER_THRESH*100)}%")
    print("=" * 72)

    dm      = fetch_daily(start_date, end_date)
    raw     = fetch_intraday(start_date, end_date)
    reg, pm = organize(raw)
    results = run(dm, reg, pm)
    print_report(results)
