"""
SPY 0DTE Stop Loss Comparison
Compares three scenarios across 155 raw signals + 4 filtered sets:
  A) No stop loss
  B) -6% stop loss
  C) -20% stop loss
All use +50% profit target. $200 per trade.
"""

from aiem_broker.tradier_config import TRADIER_API_BASE

import os, time, requests
from datetime import datetime, timedelta, date
from collections import defaultdict

POLYGON_KEY = os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_API_TOKEN")
TRADIER_TOK = os.environ.get("TRADIER_API_TOKEN_2") or os.environ.get("TRADIER_API_TOKEN")
END_DATE   = date.today()
START_DATE = END_DATE - timedelta(days=365)
TRADE_SIZE = 200

def tradier_daily(start, end):
    h = {"Authorization": f"Bearer {TRADIER_TOK}", "Accept": "application/json"}
    r = requests.get(f"{TRADIER_API_BASE}/v1/markets/history", headers=h, timeout=20,
                     params={"symbol":"SPY","interval":"daily",
                             "start":start.strftime("%Y-%m-%d"),"end":end.strftime("%Y-%m-%d")})
    d = r.json().get("history",{}).get("day",[])
    return d if isinstance(d,list) else [d]

def polygon_aggs(start, end):
    all_bars = []
    cursor = start
    while cursor < end:
        chunk = min(cursor + timedelta(days=30), end)
        url = (f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/"
               f"{cursor.strftime('%Y-%m-%d')}/{chunk.strftime('%Y-%m-%d')}")
        try:
            r = requests.get(url, params={"adjusted":"true","sort":"asc",
                                          "limit":50000,"apiKey":POLYGON_KEY}, timeout=20)
            all_bars.extend(r.json().get("results") or [])
        except:
            pass
        cursor = chunk + timedelta(days=1)
        time.sleep(0.15)
    return all_bars

daily_sorted = sorted(tradier_daily(START_DATE, END_DATE), key=lambda x: x["date"])
daily_map = {}
for i, d in enumerate(daily_sorted):
    pc = float(daily_sorted[i-1]["close"]) if i > 0 else None
    daily_map[d["date"]] = {"open":float(d["open"]),"high":float(d["high"]),
                            "low":float(d["low"]),"close":float(d["close"]),"prev_close":pc}

raw_bars = polygon_aggs(START_DATE, END_DATE)
bars_by_date = defaultdict(list)
pm_by_date   = defaultdict(list)
for b in raw_bars:
    try:
        dt_utc = datetime.utcfromtimestamp(int(b["t"])/1000)
        dt_et  = dt_utc - timedelta(hours=5 if dt_utc.month in (11,12,1,2,3) else 4)
        d_str  = dt_et.strftime("%Y-%m-%d")
        t_min  = dt_et.hour*60+dt_et.minute
        bar = {"t":t_min,"ts":dt_et.strftime("%H:%M"),
               "open":float(b["o"]),"high":float(b["h"]),"low":float(b["l"]),
               "close":float(b["c"]),"vol":float(b.get("v",0))}
        if 9*60+30 <= t_min < 16*60:
            bars_by_date[d_str].append(bar)
        elif 4*60 <= t_min < 9*60+30:
            pm_by_date[d_str].append(bar)
    except:
        continue

for d in bars_by_date: bars_by_date[d].sort(key=lambda x: x["t"])
for d in pm_by_date:   pm_by_date[d].sort(key=lambda x: x["t"])

slot_vols = defaultdict(list)
for d, bars in bars_by_date.items():
    for b in bars: slot_vols[b["t"]].append(b["vol"])
avg_vol = {t: sum(v)/len(v) for t, v in slot_vols.items() if v}
def rvol(bar):
    av = avg_vol.get(bar["t"], 0)
    return bar["vol"]/av if av > 0 else 1.0
def pct(a, b): return (b-a)/a*100 if a else 0

def simulate(entry_idx, bars, direction, entry_spy, stop_pct):
    """stop_pct: None = no stop, else negative number e.g. -6 or -20"""
    spy_px = entry_spy
    for bar in bars[entry_idx+1:]:
        spy_move = pct(spy_px, bar["close"]) * direction
        opt_ret  = spy_move * 2.0
        if stop_pct is not None and opt_ret <= stop_pct:
            return "stop", stop_pct
        if opt_ret >= 50.0:
            return "target", 50.0
    final_move = pct(spy_px, bars[-1]["close"]) * direction if bars else 0
    return "eod", min(max(final_move * 2.0, -100.0), 200.0)

# Build all trades with all three scenarios in one pass
all_trades = []
for d_str, daily in sorted(daily_map.items()):
    bars = bars_by_date.get(d_str, [])
    pm   = pm_by_date.get(d_str, [])
    if not bars or not daily["prev_close"] or len(bars) < 10:
        continue

    prev_close = daily["prev_close"]
    day_open   = daily["open"]
    gap_pct    = pct(prev_close, day_open)

    pm_high  = max(b["high"] for b in pm) if pm else day_open
    pm_low   = min(b["low"]  for b in pm) if pm else day_open
    pm_range = pct(pm_low, pm_high)
    pm_close = pm[-1]["close"] if pm else day_open
    pm_open  = pm[0]["open"]   if pm else day_open
    pm_dir   = 1 if pm_close > pm_open else -1

    orb_bars = [b for b in bars if b["t"] < 9*60+45]
    orb_high = max(b["high"] for b in orb_bars) if orb_bars else day_open
    orb_low  = min(b["low"]  for b in orb_bars) if orb_bars else day_open
    orb_range = pct(orb_low, orb_high)
    early_bars = [b for b in bars if b["t"] < 10*60]
    peak_rvol  = max(rvol(b) for b in early_bars) if early_bars else 1.0

    post_orb = [b for b in bars if b["t"] >= 9*60+45]
    sigs = []
    for idx, bar in enumerate(post_orb):
        if bar["close"] > orb_high: sigs.append((bar, 1, idx)); break
    for idx, bar in enumerate(post_orb):
        if bar["close"] < orb_low:  sigs.append((bar, -1, idx)); break

    for bar, direction, idx in sigs:
        entry_px = bar["close"]
        r_none, ret_none   = simulate(idx, post_orb, direction, entry_px, None)
        r_6,    ret_6      = simulate(idx, post_orb, direction, entry_px, -6.0)
        r_20,   ret_20     = simulate(idx, post_orb, direction, entry_px, -20.0)

        all_trades.append({
            "date": d_str, "direction": "call" if direction==1 else "put",
            "entry_time": bar["ts"], "entry_px": entry_px,
            "gap_pct": gap_pct, "pm_range": pm_range, "pm_dir": pm_dir,
            "orb_range": orb_range, "peak_rvol": peak_rvol,
            "direction_val": direction,
            # three scenarios
            "r_none": r_none, "ret_none": ret_none, "pnl_none": TRADE_SIZE*(ret_none/100),
            "r_6":    r_6,    "ret_6":    ret_6,    "pnl_6":    TRADE_SIZE*(ret_6/100),
            "r_20":   r_20,   "ret_20":   ret_20,   "pnl_20":   TRADE_SIZE*(ret_20/100),
        })

# ── report helper ─────────────────────────────────────────────────────────────
def report_scenario(trades, pnl_key, ret_key, r_key, scenario_label):
    if not trades:
        return
    rets  = [t[ret_key] for t in trades]
    pnls  = [t[pnl_key] for t in trades]
    wins  = [r for r in rets if r > 0]
    loss  = [r for r in rets if r <= 0]
    pf    = abs(sum(wins)/sum(loss)) if loss and sum(loss) != 0 else 99.0
    by_reason = defaultdict(lambda: {"n":0,"pnl":0.0})
    for t in trades:
        by_reason[t[r_key]]["n"] += 1
        by_reason[t[r_key]]["pnl"] += t[pnl_key]
    total_cap = len(trades) * TRADE_SIZE
    total_pnl = sum(pnls)
    print(f"\n  {'─'*54}")
    print(f"  {scenario_label}")
    print(f"  {'─'*54}")
    print(f"    Trades        : {len(trades)}")
    print(f"    Win Rate      : {len(wins)/len(rets)*100:.1f}%")
    print(f"    Profit Factor : {pf:.2f}")
    print(f"    Avg P&L/trade : ${total_pnl/len(trades):>+8.2f}")
    print(f"    TOTAL P&L     : ${total_pnl:>+10,.2f}  ◀")
    print(f"    Capital used  : ${total_cap:>10,}  (${TRADE_SIZE}/trade)")
    print(f"    Return on cap : {total_pnl/total_cap*100:>+8.1f}%")
    print(f"    Exit breakdown:")
    for reason, data in sorted(by_reason.items(), key=lambda x: -x[1]["n"]):
        pct_of_trades = data["n"]/len(trades)*100
        print(f"      {reason:<16}: {data['n']:>3} trades ({pct_of_trades:>4.1f}%)  ${data['pnl']:>+8,.2f}")

def run_group(trades, group_label):
    n = len(trades)
    if n == 0:
        print(f"\n  {group_label}: 0 trades"); return
    print(f"\n{'═'*60}")
    print(f"  {group_label}  ({n} trades × ${TRADE_SIZE})")
    print(f"{'═'*60}")
    report_scenario(trades, "pnl_none", "ret_none", "r_none", "A) NO STOP LOSS")
    report_scenario(trades, "pnl_6",    "ret_6",    "r_6",    "B) -6% STOP LOSS")
    report_scenario(trades, "pnl_20",   "ret_20",   "r_20",   "C) -20% STOP LOSS")

# ── define the 5 groups ───────────────────────────────────────────────────────
groups = [
    ("ALL 155 SIGNALS — No Filter (Baseline)",
     lambda t: True),

    ("F11: PM-aligned + flat gap + moderate PM (0.40–0.80%) + morning",
     lambda t: (t["direction_val"]==t["pm_dir"]
                and abs(t["gap_pct"])<0.30
                and 0.40<=t["pm_range"]<0.80
                and int(t["entry_time"].replace(":",""))<1130)),

    ("F3: Direction aligned with PM trend",
     lambda t: t["direction_val"]==t["pm_dir"]),

    ("F7: PM-aligned + flat gap",
     lambda t: t["direction_val"]==t["pm_dir"] and abs(t["gap_pct"])<0.30),

    ("F8/F9: PM-aligned + flat gap + PM range ≥0.40%",
     lambda t: (t["direction_val"]==t["pm_dir"]
                and abs(t["gap_pct"])<0.30
                and t["pm_range"]>=0.40)),
]

print("=" * 60)
print("  SPY 0DTE: NO STOP vs -6% STOP vs -20% STOP")
print(f"  $200/trade | +50% profit target | {len(all_trades)} total signals")
print("=" * 60)

for label, fn in groups:
    subset = [t for t in all_trades if fn(t)]
    run_group(subset, label)

# ── summary table ─────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("  SUMMARY TABLE — Total P&L by group and stop scenario")
print(f"{'='*60}")
print(f"\n  {'Group':<42} {'No Stop':>9} {'6% Stop':>9} {'20% Stop':>9}")
print(f"  {'-'*42} {'-'*9} {'-'*9} {'-'*9}")
for label, fn in groups:
    subset = [t for t in all_trades if fn(t)]
    if not subset: continue
    pnl_none = sum(t["pnl_none"] for t in subset)
    pnl_6    = sum(t["pnl_6"]    for t in subset)
    pnl_20   = sum(t["pnl_20"]   for t in subset)
    short = label[:40]
    print(f"  {short:<42} ${pnl_none:>+7,.0f}  ${pnl_6:>+7,.0f}  ${pnl_20:>+7,.0f}")

print("\nDone.")
