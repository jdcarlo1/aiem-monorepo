"""
SPY Pattern Backtest — 5 patterns vs last 365 days
  1. Gap Fill
  2. ORB 15-min
  3. Premarket Range Breakout
  4. Opening Drive + High RVOL Breakout   (NEW)
  5. ORB 15-min + RVOL filter             (NEW)
Uses Polygon 5-min bars.
"""
import os, time
from datetime import datetime, timedelta, date
from collections import defaultdict
import requests

POLYGON_KEY = os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_API_TOKEN")
TRADIER_TOK = os.environ.get("TRADIER_API_TOKEN_2") or os.environ.get("TRADIER_API_TOKEN")
from aiem_broker.tradier_config import TRADIER_API_BASE as _TAB
TRADIER_BASE = f"{_TAB}/v1"

END_DATE   = date.today()
START_DATE = END_DATE - timedelta(days=365)

print(f"SPY Pattern Backtest  |  {START_DATE} → {END_DATE}")
print("=" * 60)

# ── data fetchers ─────────────────────────────────────────────────────────────
def polygon_aggs(symbol, start, end):
    url_base = "https://api.polygon.io/v2/aggs/ticker"
    all_bars = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=30), end)
        url = (f"{url_base}/{symbol}/range/5/minute/"
               f"{cursor.strftime('%Y-%m-%d')}/{chunk_end.strftime('%Y-%m-%d')}")
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": POLYGON_KEY}
        try:
            r = requests.get(url, params=params, timeout=20)
            results = r.json().get("results") or []
            all_bars.extend(results)
            print(f"  polygon {cursor} → {chunk_end}: {len(results)} bars")
        except Exception as e:
            print(f"  polygon error {cursor}: {e}")
        cursor = chunk_end + timedelta(days=1)
        time.sleep(0.15)
    return all_bars

def tradier_daily(symbol, start, end):
    h = {"Authorization": f"Bearer {TRADIER_TOK}", "Accept": "application/json"}
    r = requests.get(f"{TRADIER_BASE}/markets/history", headers=h, timeout=20,
                     params={"symbol": symbol, "interval": "daily",
                             "start": start.strftime("%Y-%m-%d"),
                             "end":   end.strftime("%Y-%m-%d")})
    days = r.json().get("history", {}).get("day", [])
    return days if isinstance(days, list) else [days]

# ── fetch ─────────────────────────────────────────────────────────────────────
print("\n[1/3] Daily data from Tradier...")
daily_sorted = sorted(tradier_daily("SPY", START_DATE, END_DATE), key=lambda x: x["date"])
daily_map = {}
for i, d in enumerate(daily_sorted):
    pc = float(daily_sorted[i-1]["close"]) if i > 0 else None
    daily_map[d["date"]] = {"open": float(d["open"]), "high": float(d["high"]),
                            "low":  float(d["low"]),  "close": float(d["close"]),
                            "prev_close": pc}
print(f"  {len(daily_map)} trading days")

print("\n[2/3] 5-min bars from Polygon...")
raw_bars = polygon_aggs("SPY", START_DATE, END_DATE)
print(f"  {len(raw_bars)} total bars")

bars_by_date     = defaultdict(list)
premarket_by_date = defaultdict(list)

for b in raw_bars:
    try:
        dt_utc = datetime.utcfromtimestamp(int(b["t"]) / 1000)
        # approximate ET offset
        dt_et  = dt_utc - timedelta(hours=5 if dt_utc.month in (11,12,1,2,3) else 4)
        d_str  = dt_et.strftime("%Y-%m-%d")
        t_min  = dt_et.hour * 60 + dt_et.minute
        bar = {"dt": dt_et, "t": t_min,
               "open": float(b["o"]), "high": float(b["h"]),
               "low":  float(b["l"]), "close": float(b["c"]),
               "vol":  float(b.get("v", 0))}
        if 9*60+30 <= t_min < 16*60:
            bars_by_date[d_str].append(bar)
        elif 4*60 <= t_min < 9*60+30:
            premarket_by_date[d_str].append(bar)
    except Exception:
        continue

for d in bars_by_date:
    bars_by_date[d].sort(key=lambda x: x["t"])
for d in premarket_by_date:
    premarket_by_date[d].sort(key=lambda x: x["t"])

print(f"  {len(bars_by_date)} days with regular bars  |  "
      f"{len(premarket_by_date)} days with premarket bars")

# ── build RVOL lookup ─────────────────────────────────────────────────────────
# avg_vol[t_min] = mean volume across all days for that 5-min slot
slot_vols = defaultdict(list)
for d, bars in bars_by_date.items():
    for bar in bars:
        slot_vols[bar["t"]].append(bar["vol"])
avg_vol = {t: sum(v)/len(v) for t, v in slot_vols.items() if v}

def rvol(bar):
    av = avg_vol.get(bar["t"], 0)
    return bar["vol"] / av if av > 0 else 1.0

# ── helpers ───────────────────────────────────────────────────────────────────
def pct(a, b):
    return (b - a) / a * 100 if a else 0

GAP_THRESH  = 0.15   # min gap % for gap-fill
ORB_MIN     = 15     # opening range duration (09:30-09:45)
HOLD_BARS   = 13     # ~65-min hold after signal
RVOL_ORB    = 1.5    # RVOL threshold for pattern 5
RVOL_DRIVE  = 2.0    # RVOL threshold for pattern 4
DRIVE_MIN   = 0.25   # min % move in opening drive window

results = {k: [] for k in ["gap_fill","orb","pmrb","od_rvol","orb_rvol"]}

print("\n[3/3] Running 5 patterns...")

for d_str, daily in sorted(daily_map.items()):
    bars = bars_by_date.get(d_str, [])
    pm   = premarket_by_date.get(d_str, [])
    if not bars or not daily["prev_close"]:
        continue

    day_open   = bars[0]["open"]
    day_close  = daily["close"]
    prev_close = daily["prev_close"]

    # ── P1: GAP FILL ─────────────────────────────────────────────────────────
    gap_pct = pct(prev_close, day_open)
    if abs(gap_pct) >= GAP_THRESH:
        gap_up   = gap_pct > 0
        entry_px = day_open
        exit_px  = day_close
        fill_hit = False
        for bar in bars[1:]:
            if gap_up and bar["low"] <= prev_close:
                fill_hit = True; exit_px = prev_close; break
            elif not gap_up and bar["high"] >= prev_close:
                fill_hit = True; exit_px = prev_close; break
        direction = -1 if gap_up else 1
        results["gap_fill"].append({
            "date": d_str, "direction": "SHORT" if gap_up else "LONG",
            "entry": entry_px, "exit": exit_px, "filled": fill_hit,
            "return_pct": direction * pct(entry_px, exit_px)})

    # ── P2: ORB 15-MIN ────────────────────────────────────────────────────────
    orb_bars = [b for b in bars if b["t"] < 9*60+45]
    if len(orb_bars) >= 2:
        orb_high = max(b["high"] for b in orb_bars)
        orb_low  = min(b["low"]  for b in orb_bars)
        post_orb = [b for b in bars if b["t"] >= 9*60+45]
        sig = None; direction = 0
        for bar in post_orb:
            if bar["close"] > orb_high: sig = bar; direction = 1; break
            elif bar["close"] < orb_low: sig = bar; direction = -1; break
        if sig:
            entry_px = sig["close"]
            idx = post_orb.index(sig)
            exit_px = post_orb[min(idx+HOLD_BARS, len(post_orb)-1)]["close"]
            results["orb"].append({
                "date": d_str, "direction": "LONG" if direction>0 else "SHORT",
                "entry": entry_px, "exit": exit_px,
                "return_pct": direction * pct(entry_px, exit_px)})

    # ── P3: PREMARKET RANGE BREAKOUT ─────────────────────────────────────────
    if len(pm) >= 3:
        pm_high = max(b["high"] for b in pm)
        pm_low  = min(b["low"]  for b in pm)
        sig = None; direction = 0
        for bar in bars:
            if bar["close"] > pm_high: sig = bar; direction = 1; break
            elif bar["close"] < pm_low: sig = bar; direction = -1; break
        if sig:
            entry_px = sig["close"]
            idx = bars.index(sig)
            exit_px = bars[min(idx+HOLD_BARS, len(bars)-1)]["close"]
            results["pmrb"].append({
                "date": d_str, "direction": "LONG" if direction>0 else "SHORT",
                "entry": entry_px, "exit": exit_px,
                "return_pct": direction * pct(entry_px, exit_px)})

    # ── P4: OPENING DRIVE + HIGH RVOL BREAKOUT ───────────────────────────────
    # First 15-min window shows strong directional drive (>DRIVE_MIN%)
    # with at least one bar having RVOL >= RVOL_DRIVE.
    # Entry: close of first bar after the drive window.
    # Exit: HOLD_BARS later or EOD.
    drive_bars = [b for b in bars if b["t"] < 9*60+45]  # first 15 min
    if len(drive_bars) >= 2:
        drive_open  = drive_bars[0]["open"]
        drive_close = drive_bars[-1]["close"]
        drive_move  = pct(drive_open, drive_close)
        max_rvol    = max(rvol(b) for b in drive_bars)
        if abs(drive_move) >= DRIVE_MIN and max_rvol >= RVOL_DRIVE:
            direction = 1 if drive_move > 0 else -1
            post_drive = [b for b in bars if b["t"] >= 9*60+45]
            if post_drive:
                entry_bar = post_drive[0]
                entry_px  = entry_bar["close"]
                exit_px   = post_drive[min(HOLD_BARS, len(post_drive)-1)]["close"]
                results["od_rvol"].append({
                    "date": d_str, "direction": "LONG" if direction>0 else "SHORT",
                    "drive_move": drive_move, "max_rvol": max_rvol,
                    "entry": entry_px, "exit": exit_px,
                    "return_pct": direction * pct(entry_px, exit_px)})

    # ── P5: ORB 15-MIN + RVOL FILTER ─────────────────────────────────────────
    # Same as P2 but only take the breakout bar if its RVOL >= RVOL_ORB.
    if len(orb_bars) >= 2:
        orb_high = max(b["high"] for b in orb_bars)
        orb_low  = min(b["low"]  for b in orb_bars)
        post_orb = [b for b in bars if b["t"] >= 9*60+45]
        sig = None; direction = 0
        for bar in post_orb:
            if bar["close"] > orb_high and rvol(bar) >= RVOL_ORB:
                sig = bar; direction = 1; break
            elif bar["close"] < orb_low and rvol(bar) >= RVOL_ORB:
                sig = bar; direction = -1; break
        if sig:
            entry_px = sig["close"]
            idx = post_orb.index(sig)
            exit_px = post_orb[min(idx+HOLD_BARS, len(post_orb)-1)]["close"]
            results["orb_rvol"].append({
                "date": d_str, "direction": "LONG" if direction>0 else "SHORT",
                "bar_rvol": rvol(sig),
                "entry": entry_px, "exit": exit_px,
                "return_pct": direction * pct(entry_px, exit_px)})

# ── report ────────────────────────────────────────────────────────────────────
def report(trades, label):
    if not trades:
        print(f"\n{label}: NO TRADES FOUND"); return {}
    rets = [t["return_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    loss = [r for r in rets if r <= 0]
    pf   = abs(sum(wins)/sum(loss)) if loss and sum(loss) != 0 else float("inf")
    best  = max(trades, key=lambda x: x["return_pct"])
    worst = min(trades, key=lambda x: x["return_pct"])
    longs  = [t for t in trades if t.get("direction") == "LONG"]
    shorts = [t for t in trades if t.get("direction") == "SHORT"]
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    print(f"  Trades        : {len(trades)}")
    print(f"  Win Rate      : {len(wins)/len(rets)*100:.1f}%")
    print(f"  Avg/trade     : {sum(rets)/len(rets):+.3f}%")
    print(f"  Avg Win       : {sum(wins)/len(wins):+.3f}%" if wins else "  Avg Win       : —")
    print(f"  Avg Loss      : {sum(loss)/len(loss):+.3f}%" if loss else "  Avg Loss      : —")
    print(f"  Profit Factor : {pf:.2f}")
    print(f"  Total (sum %) : {sum(rets):+.2f}%")
    print(f"  Best Trade    : {best['date']}  {best['return_pct']:+.3f}%")
    print(f"  Worst Trade   : {worst['date']}  {worst['return_pct']:+.3f}%")
    if longs and shorts:
        lw = len([t for t in longs  if t["return_pct"]>0])/len(longs)*100
        sw = len([t for t in shorts if t["return_pct"]>0])/len(shorts)*100
        print(f"  Long  ({len(longs):>3}): WR {lw:.1f}%  avg {sum(t['return_pct'] for t in longs)/len(longs):+.3f}%")
        print(f"  Short ({len(shorts):>3}): WR {sw:.1f}%  avg {sum(t['return_pct'] for t in shorts)/len(shorts):+.3f}%")
    return {"total": sum(rets), "win_rate": len(wins)/len(rets)*100,
            "avg": sum(rets)/len(rets), "pf": pf, "n": len(trades)}

print("\n" + "=" * 60)
print("  RESULTS — SPY 5-PATTERN BACKTEST (last 365 days)")
print("=" * 60)

labels = [
    ("gap_fill", "P1: GAP FILL  (fade gap to prev close)"),
    ("orb",      "P2: ORB 15-MIN  (Opening Range Breakout)"),
    ("pmrb",     "P3: PREMARKET RANGE BREAKOUT"),
    ("od_rvol",  f"P4: OPENING DRIVE + HIGH RVOL (≥{RVOL_DRIVE}x, drive ≥{DRIVE_MIN}%)"),
    ("orb_rvol", f"P5: ORB 15-MIN + RVOL FILTER  (≥{RVOL_ORB}x at breakout)"),
]
scores = {}
for key, label in labels:
    s = report(results[key], label)
    if s:
        scores[label.split(":")[0].strip()] = s

print("\n" + "=" * 60)
print("  FINAL RANKING  (by total cumulative return)")
print("=" * 60)
medals = ["🥇","🥈","🥉","  4️⃣","  5️⃣"]
for rank, (name, s) in enumerate(
        sorted(scores.items(), key=lambda x: x[1]["total"], reverse=True), 1):
    print(f"\n  {medals[rank-1]}  {name}")
    print(f"      Win Rate      : {s['win_rate']:.1f}%")
    print(f"      Avg per trade : {s['avg']:+.3f}%")
    print(f"      Profit Factor : {s['pf']:.2f}")
    print(f"      Total return  : {s['total']:+.2f}%  (n={s['n']})")

print("\nDone.")
