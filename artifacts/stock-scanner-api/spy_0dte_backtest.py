"""
SPY 0DTE Options Backtest — Multi-Variable Filter Study
Stop loss: -6%  |  Profit target: +50%  |  Hold: intraday only

Tests every combination of these entry filters to find which ones
isolate profitable "July 28-type" trending days:

  F1. Gap size          — flat gap (<0.3%) vs gapped (>0.3%)
  F2. Premarket range   — narrow (<0.4%) vs wide (>0.4% vs >0.7%)
  F3. Premarket direction — PM closed above open (bullish bias)
  F4. ORB range size    — tight vs wide opening range
  F5. Signal direction  — only take calls on PM-up days, puts on PM-down days
  F6. Signal timing     — morning only (09:30–11:30) vs all day
  F7. Combined "trending day" score — 3+ filters must align

Proxy for option price: we simulate the ATM call/put using
intrinsic value + a simple IV-based premium from intraday bar ranges.
This is directional — we're testing WHICH DAYS work, not exact option prices.
"""

import os, time, hashlib, json
from datetime import datetime, timedelta, date
from collections import defaultdict
import requests

POLYGON_KEY = os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_API_TOKEN")
TRADIER_TOK = os.environ.get("TRADIER_API_TOKEN_2") or os.environ.get("TRADIER_API_TOKEN")
from aiem_broker.tradier_config import TRADIER_API_BASE as _TAB
TRADIER_BASE = f"{_TAB}/v1"

END_DATE   = date.today()
START_DATE = END_DATE - timedelta(days=365)

print("=" * 68)
print("  SPY 0DTE MULTI-VARIABLE FILTER BACKTEST")
print(f"  Window: {START_DATE} → {END_DATE}")
print(f"  Rule: Enter ATM call or put on signal | -6% stop | +50% target")
print("=" * 68)

# ── fetch data ────────────────────────────────────────────────────────────────
def tradier_daily(start, end):
    h = {"Authorization": f"Bearer {TRADIER_TOK}", "Accept": "application/json"}
    r = requests.get(f"{TRADIER_BASE}/markets/history", headers=h, timeout=20,
                     params={"symbol": "SPY", "interval": "daily",
                             "start": start.strftime("%Y-%m-%d"),
                             "end":   end.strftime("%Y-%m-%d")})
    d = r.json().get("history", {}).get("day", [])
    return d if isinstance(d, list) else [d]

def polygon_aggs(start, end):
    all_bars = []
    cursor = start
    while cursor < end:
        chunk = min(cursor + timedelta(days=30), end)
        url = (f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/"
               f"{cursor.strftime('%Y-%m-%d')}/{chunk.strftime('%Y-%m-%d')}")
        try:
            r = requests.get(url, params={"adjusted": "true", "sort": "asc",
                                          "limit": 50000, "apiKey": POLYGON_KEY}, timeout=20)
            all_bars.extend(r.json().get("results") or [])
        except Exception as e:
            print(f"  fetch error {cursor}: {e}")
        cursor = chunk + timedelta(days=1)
        time.sleep(0.15)
    return all_bars

print("\n[1/3] Daily data...")
daily_sorted = sorted(tradier_daily(START_DATE, END_DATE), key=lambda x: x["date"])
daily_map = {}
for i, d in enumerate(daily_sorted):
    pc = float(daily_sorted[i-1]["close"]) if i > 0 else None
    daily_map[d["date"]] = {
        "open": float(d["open"]), "high": float(d["high"]),
        "low":  float(d["low"]),  "close": float(d["close"]),
        "prev_close": pc
    }
print(f"  {len(daily_map)} trading days")

print("[2/3] 5-min bars from Polygon...")
raw_bars = polygon_aggs(START_DATE, END_DATE)
print(f"  {len(raw_bars)} bars returned")

bars_by_date = defaultdict(list)
pm_by_date   = defaultdict(list)

for b in raw_bars:
    try:
        dt_utc = datetime.utcfromtimestamp(int(b["t"]) / 1000)
        dt_et  = dt_utc - timedelta(hours=5 if dt_utc.month in (11,12,1,2,3) else 4)
        d_str  = dt_et.strftime("%Y-%m-%d")
        t_min  = dt_et.hour * 60 + dt_et.minute
        bar = {"dt": dt_et, "t": t_min, "ts": dt_et.strftime("%H:%M"),
               "open": float(b["o"]), "high": float(b["h"]),
               "low":  float(b["l"]), "close": float(b["c"]),
               "vol":  float(b.get("v", 0))}
        if 9*60+30 <= t_min < 16*60:
            bars_by_date[d_str].append(bar)
        elif 4*60 <= t_min < 9*60+30:
            pm_by_date[d_str].append(bar)
    except:
        continue

for d in bars_by_date: bars_by_date[d].sort(key=lambda x: x["t"])
for d in pm_by_date:   pm_by_date[d].sort(key=lambda x: x["t"])

# ── RVOL baseline ─────────────────────────────────────────────────────────────
slot_vols = defaultdict(list)
for d, bars in bars_by_date.items():
    for b in bars: slot_vols[b["t"]].append(b["vol"])
avg_vol = {t: sum(v)/len(v) for t, v in slot_vols.items() if v}
def rvol(bar):
    av = avg_vol.get(bar["t"], 0)
    return bar["vol"] / av if av > 0 else 1.0

def pct(a, b): return (b - a) / a * 100 if a else 0

# ── simulate 0DTE trade on a single signal bar ────────────────────────────────
# We use the 5-min close as "option premium proxy" — directional only.
# If SPY moves +X% after entry in our direction, option moves ~3-5x that (delta ~0.5 ATM).
# We use delta=0.5 * (SPY move) / option_price to approximate option P&L.
# stop=-6%, target=+50% on the option premium.

def simulate_0dte(entry_bar_idx, bars, direction, entry_spy_price):
    """
    direction: +1 = call (bullish), -1 = put (bearish)
    Returns: ('target'|'stop'|'expired', return_pct)
    """
    # Estimate ATM option premium: roughly 0.3-0.5% of underlying for 0DTE ATM
    # Use actual vol proxy: half the ORB range as the expected daily move
    spy_px = entry_spy_price
    # Approximate option delta = 0.50, gamma effect included via range
    # We track SPY move and convert to option P&L
    # ATM premium roughly = 0.35% * SPY * direction-factor
    atm_premium = spy_px * 0.0035  # ~$2.57 on $735 SPY

    for i in range(entry_bar_idx + 1, len(bars)):
        bar = bars[i]
        spy_move_pct = pct(spy_px, bar["close"]) * direction
        # Delta ~0.5 ATM, gamma adds ~0.05 per 0.5% move
        approx_option_return = spy_move_pct * 2.0  # simplified delta leverage

        if approx_option_return <= -6.0:
            return "stop", -6.0
        if approx_option_return >= 50.0:
            return "target", 50.0

    # EOD
    final_move = pct(spy_px, bars[-1]["close"]) * direction if bars else 0
    final_return = final_move * 2.0
    return "eod", min(max(final_return, -100.0), 200.0)

# ── build day-level feature set ───────────────────────────────────────────────
print("[3/3] Computing day features + simulating trades...")

day_features = {}
all_trades    = []  # list of trade dicts

DAYS_WITH_DATA = 0

for d_str, daily in sorted(daily_map.items()):
    bars = bars_by_date.get(d_str, [])
    pm   = pm_by_date.get(d_str, [])
    if not bars or not daily["prev_close"] or len(bars) < 10:
        continue

    DAYS_WITH_DATA += 1
    prev_close = daily["prev_close"]
    day_open   = daily["open"]
    day_close  = daily["close"]

    # ── features ──────────────────────────────────────────────────────────────
    gap_pct = pct(prev_close, day_open)

    pm_high   = max(b["high"]  for b in pm) if pm else day_open
    pm_low    = min(b["low"]   for b in pm) if pm else day_open
    pm_range  = pct(pm_low, pm_high)
    pm_close  = pm[-1]["close"] if pm else day_open
    pm_open   = pm[0]["open"]   if pm else day_open
    pm_dir    = 1 if pm_close > pm_open else -1   # +1 = PM trending up

    orb_bars  = [b for b in bars if b["t"] < 9*60+45]
    orb_high  = max(b["high"] for b in orb_bars) if orb_bars else day_open
    orb_low   = min(b["low"]  for b in orb_bars) if orb_bars else day_open
    orb_range = pct(orb_low, orb_high)

    # How directional was the morning (09:30–11:30)?
    morning_bars = [b for b in bars if 9*60+30 <= b["t"] <= 11*60+30]
    if len(morning_bars) >= 4:
        morning_move = pct(morning_bars[0]["open"], morning_bars[-1]["close"])
    else:
        morning_move = 0

    # RVOL peak in first 30 min
    early_bars   = [b for b in bars if b["t"] < 10*60]
    peak_rvol    = max(rvol(b) for b in early_bars) if early_bars else 1.0

    day_features[d_str] = {
        "gap_pct":      gap_pct,
        "pm_range":     pm_range,
        "pm_dir":       pm_dir,
        "orb_range":    orb_range,
        "morning_move": morning_move,
        "peak_rvol":    peak_rvol,
        "bars":         bars,
        "orb_high":     orb_high,
        "orb_low":      orb_low,
        "day_open":     day_open,
    }

    # ── generate signals: ORB breakout (same as proven P2 strategy) ───────────
    post_orb = [b for b in bars if b["t"] >= 9*60+45]
    signal_bars = []
    for idx, bar in enumerate(post_orb):
        if bar["close"] > orb_high:
            signal_bars.append({"bar": bar, "direction": 1,  "idx": idx, "time": bar["t"]})
            break  # first signal only per day
    for idx, bar in enumerate(post_orb):
        if bar["close"] < orb_low:
            signal_bars.append({"bar": bar, "direction": -1, "idx": idx, "time": bar["t"]})
            break

    for sig in signal_bars:
        direction  = sig["direction"]
        entry_bar  = sig["bar"]
        entry_px   = entry_bar["close"]
        signal_idx = post_orb.index(entry_bar)
        reason, ret = simulate_0dte(signal_idx, post_orb, direction, entry_px)

        all_trades.append({
            "date":          d_str,
            "direction":     "call" if direction == 1 else "put",
            "entry_time":    entry_bar["ts"],
            "entry_px":      entry_px,
            "exit_reason":   reason,
            "return_pct":    ret,
            "win":           ret > 0,
            # features for filtering
            "gap_pct":       gap_pct,
            "pm_range":      pm_range,
            "pm_dir":        pm_dir,
            "orb_range":     orb_range,
            "morning_move":  morning_move,
            "peak_rvol":     peak_rvol,
            "direction_val": direction,
        })

print(f"  {DAYS_WITH_DATA} days with data | {len(all_trades)} raw signals")

# ── filter engine ─────────────────────────────────────────────────────────────
def apply_filters(trades, filters: dict):
    """filters is a dict of {name: lambda trade: bool}"""
    result = trades
    for name, fn in filters.items():
        result = [t for t in result if fn(t)]
    return result

def stats(trades, label):
    if not trades:
        return {"label": label, "n": 0, "wr": 0, "avg": 0, "total": 0, "pf": 0}
    rets  = [t["return_pct"] for t in trades]
    wins  = [r for r in rets if r > 0]
    loss  = [r for r in rets if r <= 0]
    pf    = abs(sum(wins)/sum(loss)) if loss and sum(loss) != 0 else 99.0
    return {
        "label":  label,
        "n":      len(trades),
        "wr":     len(wins)/len(rets)*100,
        "avg":    sum(rets)/len(rets),
        "total":  sum(rets),
        "pf":     pf,
    }

# ── run all filter combos ─────────────────────────────────────────────────────
print("\n" + "=" * 68)
print("  FILTER COMBINATION RESULTS")
print("  Baseline = no filter (raw ORB signal)")
print("=" * 68)

results = []

# Baseline
results.append(stats(all_trades, "BASELINE — No filter"))

# F1: Gap size
results.append(stats(apply_filters(all_trades, {
    "flat_gap": lambda t: abs(t["gap_pct"]) < 0.30
}), "F1a: Flat gap (<0.30%)"))

results.append(stats(apply_filters(all_trades, {
    "large_gap": lambda t: abs(t["gap_pct"]) >= 0.50
}), "F1b: Large gap (≥0.50%)"))

# F2: Premarket range
results.append(stats(apply_filters(all_trades, {
    "narrow_pm": lambda t: t["pm_range"] < 0.40
}), "F2a: Narrow PM range (<0.40%)"))

results.append(stats(apply_filters(all_trades, {
    "moderate_pm": lambda t: 0.40 <= t["pm_range"] < 0.80
}), "F2b: Moderate PM range (0.40–0.80%)"))

results.append(stats(apply_filters(all_trades, {
    "wide_pm": lambda t: t["pm_range"] >= 0.80
}), "F2c: Wide PM range (≥0.80%)"))

# F3: Direction aligned with PM trend
results.append(stats(apply_filters(all_trades, {
    "pm_aligned": lambda t: t["direction_val"] == t["pm_dir"]
}), "F3: Direction aligned with PM trend"))

results.append(stats(apply_filters(all_trades, {
    "pm_against": lambda t: t["direction_val"] != t["pm_dir"]
}), "F3b: Direction AGAINST PM trend"))

# F4: ORB range (tight = more decisive breakout)
results.append(stats(apply_filters(all_trades, {
    "tight_orb": lambda t: t["orb_range"] < 0.30
}), "F4a: Tight ORB (<0.30%)"))

results.append(stats(apply_filters(all_trades, {
    "wide_orb": lambda t: t["orb_range"] >= 0.40
}), "F4b: Wide ORB (≥0.40%)"))

# F5: Morning signal only (before 11:30)
results.append(stats(apply_filters(all_trades, {
    "morning": lambda t: int(t["entry_time"].replace(":", "")) < 1130
}), "F5: Morning signal only (before 11:30)"))

results.append(stats(apply_filters(all_trades, {
    "afternoon": lambda t: int(t["entry_time"].replace(":", "")) >= 1130
}), "F5b: Afternoon signal only (≥11:30)"))

# F6: High early RVOL
results.append(stats(apply_filters(all_trades, {
    "high_rvol": lambda t: t["peak_rvol"] >= 1.5
}), "F6: High early RVOL (≥1.5x)"))

# F7: Calls only on up days / puts only on down days (PM-aligned, flat gap)
results.append(stats(apply_filters(all_trades, {
    "aligned": lambda t: t["direction_val"] == t["pm_dir"],
    "flat":    lambda t: abs(t["gap_pct"]) < 0.30,
}), "F7: PM-aligned + flat gap"))

results.append(stats(apply_filters(all_trades, {
    "aligned":  lambda t: t["direction_val"] == t["pm_dir"],
    "flat":     lambda t: abs(t["gap_pct"]) < 0.30,
    "mod_pm":   lambda t: t["pm_range"] >= 0.40,
}), "F8: PM-aligned + flat gap + PM range ≥0.40%"))

results.append(stats(apply_filters(all_trades, {
    "aligned":  lambda t: t["direction_val"] == t["pm_dir"],
    "flat":     lambda t: abs(t["gap_pct"]) < 0.30,
    "mod_pm":   lambda t: t["pm_range"] >= 0.40,
    "morning":  lambda t: int(t["entry_time"].replace(":", "")) < 1130,
}), "F9: PM-aligned + flat gap + PM≥0.40% + morning signal"))

results.append(stats(apply_filters(all_trades, {
    "aligned":  lambda t: t["direction_val"] == t["pm_dir"],
    "flat":     lambda t: abs(t["gap_pct"]) < 0.30,
    "mod_pm":   lambda t: t["pm_range"] >= 0.40,
    "morning":  lambda t: int(t["entry_time"].replace(":", "")) < 1130,
    "rvol":     lambda t: t["peak_rvol"] >= 1.5,
}), "F10: F9 + high RVOL (≥1.5x)"))

results.append(stats(apply_filters(all_trades, {
    "aligned":  lambda t: t["direction_val"] == t["pm_dir"],
    "flat":     lambda t: abs(t["gap_pct"]) < 0.30,
    "mod_pm":   lambda t: 0.40 <= t["pm_range"] < 0.80,
    "morning":  lambda t: int(t["entry_time"].replace(":", "")) < 1130,
}), "F11: PM-aligned + flat gap + moderate PM (0.40-0.80%) + morning"))

# Calls only (simulate call-only strategy with best filters)
results.append(stats(apply_filters(all_trades, {
    "calls_only": lambda t: t["direction"] == "call",
    "aligned":    lambda t: t["direction_val"] == t["pm_dir"],
    "flat":       lambda t: abs(t["gap_pct"]) < 0.30,
    "mod_pm":     lambda t: t["pm_range"] >= 0.40,
    "morning":    lambda t: int(t["entry_time"].replace(":", "")) < 1130,
}), "F12: CALLS ONLY — PM-aligned + flat gap + PM≥0.40% + morning"))

# Print ranked by win rate (min 5 trades)
print(f"\n{'Label':<55} {'N':>5} {'WR%':>6} {'Avg%':>7} {'Total%':>8} {'PF':>5}")
print("-" * 90)
qualified = [r for r in results if r["n"] >= 5]
for r in sorted(qualified, key=lambda x: x["wr"], reverse=True):
    flag = " ◀ BEST" if r == sorted(qualified, key=lambda x: x["wr"], reverse=True)[0] else ""
    print(f"  {r['label']:<53} {r['n']:>5} {r['wr']:>5.1f}% {r['avg']:>+6.2f}% {r['total']:>+7.1f}% {r['pf']:>5.2f}{flag}")

print("\n" + "=" * 68)
print("  TOP 5 BY TOTAL RETURN (min 5 trades)")
print("=" * 68)
for i, r in enumerate(sorted(qualified, key=lambda x: x["total"], reverse=True)[:5], 1):
    print(f"\n  #{i}  {r['label']}")
    print(f"      Trades : {r['n']}")
    print(f"      Win Rate: {r['wr']:.1f}%")
    print(f"      Avg/trade: {r['avg']:+.2f}%")
    print(f"      Total Return: {r['total']:+.1f}%")
    print(f"      Profit Factor: {r['pf']:.2f}")

# ── verify July 28 specifically ───────────────────────────────────────────────
print("\n" + "=" * 68)
print("  JULY 28 FORENSICS — Did it pass the best filters?")
print("=" * 68)
jul28 = [t for t in all_trades if t["date"] == "2026-07-28"]
if jul28:
    t = jul28[0]
    print(f"  Gap          : {t['gap_pct']:+.3f}%  → {'FLAT ✅' if abs(t['gap_pct'])<0.30 else 'LARGE ❌'}")
    print(f"  PM range     : {t['pm_range']:.3f}%  → {'MODERATE ✅' if t['pm_range']>=0.40 else 'NARROW ❌'}")
    print(f"  PM dir       : {'UP ✅' if t['pm_dir']==1 else 'DOWN'}")
    print(f"  Direction    : {t['direction'].upper()}")
    print(f"  Aligned w/PM : {'YES ✅' if t['direction_val']==t['pm_dir'] else 'NO ❌'}")
    print(f"  Morning sig  : {'YES ✅' if int(t['entry_time'].replace(':',''))<1130 else 'NO'} ({t['entry_time']} ET)")
    print(f"  Peak RVOL    : {t['peak_rvol']:.2f}x  → {'HIGH ✅' if t['peak_rvol']>=1.5 else 'NORMAL'}")
    print(f"\n  Result: {t['exit_reason'].upper()} at {t['return_pct']:+.1f}%")
else:
    print("  No July 28 trades found in this dataset (data gap)")

print("\nDone.")
