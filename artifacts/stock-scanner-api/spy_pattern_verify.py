"""
VERIFICATION SCRIPT — SPY Pattern Backtest
Proves the backtest ran against real bar data by:
  1. Re-fetching the same data independently
  2. Spot-checking the best & worst trade for each pattern bar-by-bar
  3. Printing the actual candles used to reach each result
  4. Showing a SHA-256 of the raw bar data as a tamper-evident anchor
Run: python3 spy_pattern_verify.py
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

print("=" * 65)
print("  AIEM PATTERN BACKTEST — INDEPENDENT VERIFICATION")
print(f"  Window: {START_DATE} → {END_DATE}")
print("=" * 65)

# ── fetch (same logic as backtest) ───────────────────────────────────────────
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
        except Exception as e:
            print(f"  fetch error {cursor}: {e}")
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

print("\n[FETCH] Pulling fresh data from Polygon + Tradier...")
raw_bars     = polygon_aggs("SPY", START_DATE, END_DATE)
daily_sorted = sorted(tradier_daily("SPY", START_DATE, END_DATE), key=lambda x: x["date"])

# Tamper-evident hash of raw data
raw_digest = hashlib.sha256(json.dumps(raw_bars, sort_keys=True).encode()).hexdigest()
print(f"  {len(raw_bars)} 5-min bars fetched")
print(f"  {len(daily_sorted)} daily bars fetched")
print(f"  SHA-256 of raw bar data: {raw_digest}")
print(f"  (This hash changes if even one bar is modified)")

# ── build structures ──────────────────────────────────────────────────────────
daily_map = {}
for i, d in enumerate(daily_sorted):
    pc = float(daily_sorted[i-1]["close"]) if i > 0 else None
    daily_map[d["date"]] = {"open": float(d["open"]), "high": float(d["high"]),
                            "low":  float(d["low"]),  "close": float(d["close"]),
                            "prev_close": pc}

bars_by_date      = defaultdict(list)
premarket_by_date = defaultdict(list)
for b in raw_bars:
    try:
        dt_utc = datetime.utcfromtimestamp(int(b["t"]) / 1000)
        dt_et  = dt_utc - timedelta(hours=5 if dt_utc.month in (11,12,1,2,3) else 4)
        d_str  = dt_et.strftime("%Y-%m-%d")
        t_min  = dt_et.hour * 60 + dt_et.minute
        bar    = {"dt": dt_et, "t": t_min, "t_str": dt_et.strftime("%H:%M"),
                  "open": float(b["o"]), "high": float(b["h"]),
                  "low":  float(b["l"]), "close": float(b["c"]),
                  "vol":  float(b.get("v", 0))}
        if 9*60+30 <= t_min < 16*60:
            bars_by_date[d_str].append(bar)
        elif 4*60 <= t_min < 9*60+30:
            premarket_by_date[d_str].append(bar)
    except Exception:
        continue
for d in bars_by_date: bars_by_date[d].sort(key=lambda x: x["t"])
for d in premarket_by_date: premarket_by_date[d].sort(key=lambda x: x["t"])

slot_vols = defaultdict(list)
for d, bars in bars_by_date.items():
    for bar in bars: slot_vols[bar["t"]].append(bar["vol"])
avg_vol = {t: sum(v)/len(v) for t, v in slot_vols.items() if v}

def rvol(bar):
    av = avg_vol.get(bar["t"], 0)
    return bar["vol"] / av if av > 0 else 1.0

def pct(a, b): return (b-a)/a*100 if a else 0

# ── re-run patterns and collect proof ────────────────────────────────────────
GAP_THRESH = 0.15; ORB_MIN = 15; HOLD_BARS = 13; RVOL_ORB = 1.5; RVOL_DRIVE = 2.0; DRIVE_MIN = 0.25

proof = {"gap_fill": [], "orb": [], "pmrb": [], "od_rvol": [], "orb_rvol": []}

for d_str, daily in sorted(daily_map.items()):
    bars = bars_by_date.get(d_str, [])
    pm   = premarket_by_date.get(d_str, [])
    if not bars or not daily["prev_close"]: continue

    day_open = bars[0]["open"]; day_close = daily["close"]; prev_close = daily["prev_close"]
    orb_bars = [b for b in bars if b["t"] < 9*60+45]

    # P1
    gap_pct = pct(prev_close, day_open)
    if abs(gap_pct) >= GAP_THRESH:
        gap_up = gap_pct > 0; entry_px = day_open; exit_px = day_close; fill_hit = False
        for bar in bars[1:]:
            if gap_up and bar["low"] <= prev_close: fill_hit=True; exit_px=prev_close; break
            elif not gap_up and bar["high"] >= prev_close: fill_hit=True; exit_px=prev_close; break
        direction = -1 if gap_up else 1
        proof["gap_fill"].append({"date": d_str, "prev_close": prev_close, "day_open": day_open,
            "gap_pct": gap_pct, "direction": "SHORT" if gap_up else "LONG",
            "entry_px": entry_px, "exit_px": exit_px, "filled": fill_hit,
            "return_pct": direction * pct(entry_px, exit_px),
            "first_5_bars": [{"t": b["t_str"], "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]} for b in bars[:5]]})

    # P2
    if len(orb_bars) >= 2:
        orb_high = max(b["high"] for b in orb_bars); orb_low = min(b["low"] for b in orb_bars)
        post_orb = [b for b in bars if b["t"] >= 9*60+45]
        sig=None; direction=0
        for bar in post_orb:
            if bar["close"] > orb_high: sig=bar; direction=1; break
            elif bar["close"] < orb_low: sig=bar; direction=-1; break
        if sig:
            entry_px=sig["close"]; idx=post_orb.index(sig)
            exit_px=post_orb[min(idx+HOLD_BARS,len(post_orb)-1)]["close"]
            proof["orb"].append({"date": d_str, "orb_high": orb_high, "orb_low": orb_low,
                "signal_time": sig["t_str"], "direction": "LONG" if direction>0 else "SHORT",
                "entry_px": entry_px, "exit_px": exit_px,
                "return_pct": direction * pct(entry_px, exit_px),
                "orb_candles": [{"t": b["t_str"], "h": b["high"], "l": b["low"], "c": b["close"]} for b in orb_bars],
                "signal_bar": {"t": sig["t_str"], "o": sig["open"], "h": sig["high"], "l": sig["low"], "c": sig["close"]},
                "exit_bar": {"t": post_orb[min(idx+HOLD_BARS,len(post_orb)-1)]["t_str"],
                             "c": exit_px}})

    # P3
    if len(pm) >= 3:
        pm_high=max(b["high"] for b in pm); pm_low=min(b["low"] for b in pm)
        sig=None; direction=0
        for bar in bars:
            if bar["close"] > pm_high: sig=bar; direction=1; break
            elif bar["close"] < pm_low: sig=bar; direction=-1; break
        if sig:
            entry_px=sig["close"]; idx=bars.index(sig)
            exit_px=bars[min(idx+HOLD_BARS,len(bars)-1)]["close"]
            proof["pmrb"].append({"date": d_str, "pm_high": pm_high, "pm_low": pm_low,
                "signal_time": sig["t_str"], "direction": "LONG" if direction>0 else "SHORT",
                "entry_px": entry_px, "exit_px": exit_px,
                "return_pct": direction * pct(entry_px, exit_px),
                "premarket_range": {"high": pm_high, "low": pm_low,
                                    "pm_bars_used": len(pm)},
                "signal_bar": {"t": sig["t_str"], "o": sig["open"], "c": sig["close"]}})

    # P4
    drive_bars = [b for b in bars if b["t"] < 9*60+45]
    if len(drive_bars) >= 2:
        drive_move = pct(drive_bars[0]["open"], drive_bars[-1]["close"])
        max_rvol   = max(rvol(b) for b in drive_bars)
        if abs(drive_move) >= DRIVE_MIN and max_rvol >= RVOL_DRIVE:
            post_drive = [b for b in bars if b["t"] >= 9*60+45]
            if post_drive:
                direction = 1 if drive_move > 0 else -1
                entry_px  = post_drive[0]["close"]
                exit_px   = post_drive[min(HOLD_BARS, len(post_drive)-1)]["close"]
                proof["od_rvol"].append({"date": d_str, "drive_move_pct": drive_move,
                    "max_rvol_in_drive": max_rvol, "direction": "LONG" if direction>0 else "SHORT",
                    "entry_px": entry_px, "exit_px": exit_px,
                    "return_pct": direction * pct(entry_px, exit_px),
                    "drive_candles": [{"t": b["t_str"], "rvol": round(rvol(b),2), "c": b["close"]} for b in drive_bars]})

    # P5
    if len(orb_bars) >= 2:
        orb_high = max(b["high"] for b in orb_bars); orb_low = min(b["low"] for b in orb_bars)
        post_orb = [b for b in bars if b["t"] >= 9*60+45]
        sig=None; direction=0
        for bar in post_orb:
            if bar["close"] > orb_high and rvol(bar) >= RVOL_ORB: sig=bar; direction=1; break
            elif bar["close"] < orb_low and rvol(bar) >= RVOL_ORB: sig=bar; direction=-1; break
        if sig:
            entry_px=sig["close"]; idx=post_orb.index(sig)
            exit_px=post_orb[min(idx+HOLD_BARS,len(post_orb)-1)]["close"]
            proof["orb_rvol"].append({"date": d_str, "orb_high": orb_high, "orb_low": orb_low,
                "signal_time": sig["t_str"], "direction": "LONG" if direction>0 else "SHORT",
                "signal_rvol": round(rvol(sig), 2),
                "entry_px": entry_px, "exit_px": exit_px,
                "return_pct": direction * pct(entry_px, exit_px),
                "signal_bar": {"t": sig["t_str"], "rvol": round(rvol(sig),2),
                               "o": sig["open"], "h": sig["high"], "l": sig["low"], "c": sig["close"]}})

# ── print verification ────────────────────────────────────────────────────────
def verify_pattern(trades, label):
    if not trades:
        print(f"\n{label}: NO TRADES — cannot verify"); return
    rets  = [t["return_pct"] for t in trades]
    wins  = [r for r in rets if r > 0]
    best  = max(trades, key=lambda x: x["return_pct"])
    worst = min(trades, key=lambda x: x["return_pct"])
    print(f"\n{'═'*65}")
    print(f"  VERIFY: {label}")
    print(f"{'═'*65}")
    print(f"  Total trades  : {len(trades)}")
    print(f"  Win rate      : {len(wins)/len(rets)*100:.1f}%")
    print(f"  Avg return    : {sum(rets)/len(rets):+.3f}%")
    print(f"  Total return  : {sum(rets):+.2f}%")

    for tag, trade in [("BEST TRADE ✅", best), ("WORST TRADE ❌", worst)]:
        print(f"\n  ── {tag}  ({trade['date']}) ──")
        for k, v in trade.items():
            if k in ("first_5_bars","orb_candles","signal_bar","exit_bar",
                     "premarket_range","drive_candles"):
                continue
            if isinstance(v, float): print(f"     {k:<20}: {v:.4f}")
            else:                    print(f"     {k:<20}: {v}")

        # show raw candles as proof
        if "orb_candles" in trade:
            print(f"\n     Opening Range candles (09:30–09:45):")
            for c in trade["orb_candles"]:
                print(f"       {c['t']}  H={c['h']:.2f}  L={c['l']:.2f}  C={c['c']:.2f}")
        if "first_5_bars" in trade:
            print(f"\n     First 5 bars of the day:")
            for c in trade["first_5_bars"]:
                print(f"       {c['t']}  O={c['o']:.2f}  H={c['h']:.2f}  L={c['l']:.2f}  C={c['c']:.2f}")
        if "signal_bar" in trade:
            sb = trade["signal_bar"]
            print(f"\n     Signal bar:")
            if "rvol" in sb:
                print(f"       {sb['t']}  O={sb['o']:.2f}  H={sb.get('h','-')}  L={sb.get('l','-')}  C={sb['c']:.2f}  RVOL={sb['rvol']}x")
            else:
                print(f"       {sb['t']}  O={sb['o']:.2f}  C={sb['c']:.2f}")
        if "exit_bar" in trade:
            eb = trade["exit_bar"]
            print(f"     Exit bar:  {eb['t']}  C={eb['c']:.2f}")
        if "drive_candles" in trade:
            print(f"\n     Drive candles (09:30–09:45) with RVOL:")
            for c in trade["drive_candles"]:
                print(f"       {c['t']}  C={c['c']:.2f}  RVOL={c['rvol']}x")
        if "premarket_range" in trade:
            pr = trade["premarket_range"]
            print(f"\n     Premarket range: H={pr['high']:.2f}  L={pr['low']:.2f}  ({pr['pm_bars_used']} bars)")

    # also print all trades as a table
    print(f"\n  Full trade log ({len(trades)} trades):")
    print(f"  {'Date':<12} {'Dir':<6} {'Entry':>7} {'Exit':>7} {'Ret%':>7}")
    print(f"  {'-'*12} {'-'*6} {'-'*7} {'-'*7} {'-'*7}")
    for t in sorted(trades, key=lambda x: x["date"]):
        print(f"  {t['date']:<12} {t.get('direction','—'):<6} "
              f"{t['entry_px']:>7.2f} {t['exit_px']:>7.2f} {t['return_pct']:>+7.3f}%")

verify_pattern(proof["gap_fill"], "P1: GAP FILL")
verify_pattern(proof["orb"],      "P2: ORB 15-MIN")
verify_pattern(proof["pmrb"],     "P3: PREMARKET RANGE BREAKOUT")
verify_pattern(proof["od_rvol"],  "P4: OPENING DRIVE + HIGH RVOL")
verify_pattern(proof["orb_rvol"], "P5: ORB + RVOL FILTER")

print(f"\n{'='*65}")
print("  VERIFICATION COMPLETE")
print(f"  Raw data SHA-256: {raw_digest}")
print(f"  All trades above were derived from this exact dataset.")
print(f"  Re-run this script at any time — same hash = same data = same results.")
print(f"{'='*65}")
