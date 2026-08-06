"""
SPY 0DTE Stop Loss Sweep — 5% through 10%
Tests all 4 filtered patterns across 6 stop loss levels.
+50% profit target | $200/trade | 2-year window
"""
import os, time, requests
from datetime import datetime, timedelta, date
from collections import defaultdict

POLYGON_KEY = os.environ["POLYGON_API_KEY"]
TRADIER_TOK = os.environ.get("TRADIER_API_TOKEN_2") or os.environ.get("TRADIER_API_TOKEN")
END_DATE   = date.today()
START_DATE = END_DATE - timedelta(days=730)
TRADE_SIZE = 200
STOP_LEVELS = [-5, -6, -7, -8, -9, -10]   # % stop losses to test

print(f"Window: {START_DATE} → {END_DATE}")
print(f"Stop levels: {STOP_LEVELS}")

# ── fetch ─────────────────────────────────────────────────────────────────────
def fetch_daily():
    h = {"Authorization": f"Bearer {TRADIER_TOK}", "Accept": "application/json"}
    r = requests.get("https://api.tradier.com/v1/markets/history", headers=h, timeout=20,
                     params={"symbol":"SPY","interval":"daily",
                             "start":START_DATE.strftime("%Y-%m-%d"),
                             "end":END_DATE.strftime("%Y-%m-%d")})
    d = r.json().get("history",{}).get("day",[])
    return d if isinstance(d,list) else [d]

def fetch_bars():
    """Fetch in 2-month chunks with 3s delay to stay under Polygon rate limit."""
    all_bars = []
    cursor = START_DATE
    while cursor < END_DATE:
        chunk = min(cursor + timedelta(days=60), END_DATE)   # 2-month chunks
        url = (f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/"
               f"{cursor.strftime('%Y-%m-%d')}/{chunk.strftime('%Y-%m-%d')}")
        try:
            r = requests.get(url, params={"adjusted":"true","sort":"asc",
                                          "limit":50000,"apiKey":POLYGON_KEY}, timeout=60)
            j = r.json()
            res = j.get("results") or []
            all_bars.extend(res)
            print(f"  {cursor} → {chunk}: {len(res):>5} bars  total={len(all_bars)}  [{j.get('status','?')}]")
        except Exception as e:
            print(f"  {cursor}: error {e}")
        cursor = chunk + timedelta(days=1)
        time.sleep(3.0)   # 3s between calls — well under any rate limit
    return all_bars

print("\n[1/3] Daily data...")
daily_sorted = sorted(fetch_daily(), key=lambda x: x["date"])
daily_map = {}
for i, d in enumerate(daily_sorted):
    pc = float(daily_sorted[i-1]["close"]) if i > 0 else None
    daily_map[d["date"]] = {"open": float(d["open"]), "close": float(d["close"]),
                            "prev_close": pc}
print(f"  {len(daily_map)} trading days")

print("\n[2/3] 5-min bars...")
raw = fetch_bars()
print(f"  {len(raw)} total bars")

bars_by = defaultdict(list)
pm_by   = defaultdict(list)
for b in raw:
    try:
        dt = datetime.utcfromtimestamp(int(b["t"])/1000) - \
             timedelta(hours=5 if datetime.utcfromtimestamp(int(b["t"])/1000).month in (11,12,1,2,3) else 4)
        ds = dt.strftime("%Y-%m-%d")
        tm = dt.hour*60 + dt.minute
        bar = {"t":tm,"ts":dt.strftime("%H:%M"),
               "o":float(b["o"]),"h":float(b["h"]),"l":float(b["l"]),"c":float(b["c"]),
               "v":float(b.get("v",0))}
        if 9*60+30 <= tm < 16*60:   bars_by[ds].append(bar)
        elif 4*60 <= tm < 9*60+30:  pm_by[ds].append(bar)
    except: continue
for d in bars_by: bars_by[d].sort(key=lambda x: x["t"])
for d in pm_by:   pm_by[d].sort(key=lambda x: x["t"])

slot_vols = defaultdict(list)
for d, bars in bars_by.items():
    for b in bars: slot_vols[b["t"]].append(b["v"])
avg_vol = {t: sum(v)/len(v) for t, v in slot_vols.items() if v}
def rvol(bar): av = avg_vol.get(bar["t"],0); return bar["v"]/av if av>0 else 1.0
def pct(a, b): return (b-a)/a*100 if a else 0

def simulate(entry_idx, post_orb, direction, spy_px, atm_est, stop_pct):
    leverage = min(max((0.50 * spy_px) / atm_est, 50.0), 250.0)
    for bar in post_orb[entry_idx+1:]:
        opt_ret = pct(spy_px, bar["c"]) * direction * leverage
        if stop_pct is not None and opt_ret <= stop_pct:
            return "stop", float(stop_pct)
        if opt_ret >= 50.0:
            return "target", 50.0
    final = pct(spy_px, post_orb[-1]["c"]) * direction if post_orb else 0
    return "eod", min(max(final * leverage, -100.0), 200.0)

print("\n[3/3] Building trades...")
all_trades = []
days_used = 0
for d_str, daily in sorted(daily_map.items()):
    bars = bars_by.get(d_str, [])
    pm   = pm_by.get(d_str, [])
    if not bars or not daily["prev_close"] or len(bars) < 10: continue
    days_used += 1
    gap_pct  = pct(daily["prev_close"], daily["open"])
    pm_high  = max(b["h"] for b in pm) if pm else daily["open"]
    pm_low   = min(b["l"] for b in pm) if pm else daily["open"]
    pm_range = pct(pm_low, pm_high)
    pm_dir   = 1 if (pm[-1]["c"] > pm[0]["o"] if pm else True) else -1
    orb_bars = [b for b in bars if b["t"] < 9*60+45]
    if not orb_bars: continue
    orb_high = max(b["h"] for b in orb_bars)
    orb_low  = min(b["l"] for b in orb_bars)
    post_orb = [b for b in bars if b["t"] >= 9*60+45]
    sigs = []
    for idx, bar in enumerate(post_orb):
        if bar["c"] > orb_high: sigs.append((bar, 1,  idx)); break
    for idx, bar in enumerate(post_orb):
        if bar["c"] < orb_low:  sigs.append((bar, -1, idx)); break

    for bar, direction, idx in sigs:
        spy_px  = bar["c"]
        atm_est = max((orb_high - orb_low) / 2.0, spy_px * 0.0015)
        row = {"date": d_str, "dir": "call" if direction==1 else "put",
               "ts": bar["ts"], "spy": spy_px, "atm_est": atm_est,
               "gap_pct": gap_pct, "pm_range": pm_range, "pm_dir": pm_dir,
               "direction_val": direction}
        # simulate every stop level + no-stop
        row["ret_none"], row["pnl_none"] = (lambda r, ret: (ret, TRADE_SIZE*(ret/100)))(
            *simulate(idx, post_orb, direction, spy_px, atm_est, None)[1:2],
            simulate(idx, post_orb, direction, spy_px, atm_est, None)[1])
        # fix: store all results properly
        res_none = simulate(idx, post_orb, direction, spy_px, atm_est, None)
        row["reason_none"] = res_none[0]; row["ret_none"] = res_none[1]
        row["pnl_none"] = TRADE_SIZE * (res_none[1]/100)
        for sl in STOP_LEVELS:
            res = simulate(idx, post_orb, direction, spy_px, atm_est, sl)
            row[f"reason_{abs(sl)}"] = res[0]
            row[f"ret_{abs(sl)}"]    = res[1]
            row[f"pnl_{abs(sl)}"]    = TRADE_SIZE * (res[1]/100)
        all_trades.append(row)

print(f"  {days_used} days with data | {len(all_trades)} signals")

# ── groups ────────────────────────────────────────────────────────────────────
groups = [
    ("F11: PM-aligned+flat gap+mod PM(0.40-0.80%)+morning",
     lambda t: t["direction_val"]==t["pm_dir"] and abs(t["gap_pct"])<0.30
               and 0.40<=t["pm_range"]<0.80 and int(t["ts"].replace(":",""))<1130),
    ("F3:  Direction aligned with PM trend",
     lambda t: t["direction_val"]==t["pm_dir"]),
    ("F7:  PM-aligned + flat gap",
     lambda t: t["direction_val"]==t["pm_dir"] and abs(t["gap_pct"])<0.30),
    ("F8/F9: PM-aligned+flat gap+PM≥0.40%",
     lambda t: t["direction_val"]==t["pm_dir"] and abs(t["gap_pct"])<0.30
               and t["pm_range"]>=0.40),
]

def grp_stats(trades, ret_k, pnl_k):
    if not trades: return {"n":0,"wr":0,"total":0,"avg":0,"pf":0}
    rets = [t[ret_k] for t in trades]
    pnls = [t[pnl_k] for t in trades]
    wins = [r for r in rets if r>0]
    loss = [r for r in rets if r<=0]
    pf   = abs(sum(wins)/sum(loss)) if loss and sum(loss)!=0 else 99.0
    return {"n":len(trades),"wr":len(wins)/len(rets)*100,
            "total":sum(pnls),"avg":sum(pnls)/len(pnls),"pf":pf}

# ── print results ─────────────────────────────────────────────────────────────
print("\n" + "="*72)
print("  SPY 0DTE — STOP LOSS SWEEP  |  +50% target  |  $200/trade  |  2 years")
print("="*72)

for group_label, fn in groups:
    sub = [t for t in all_trades if fn(t)]
    if not sub: continue
    cap = len(sub) * TRADE_SIZE
    print(f"\n{'─'*72}")
    print(f"  {group_label}  ({len(sub)} trades | ${cap:,} capital)")
    print(f"{'─'*72}")
    print(f"  {'Stop Loss':<12} {'Trades':>6}  {'Win%':>5}  {'Total P&L':>11}  {'Avg/trade':>10}  {'PF':>5}  {'Target':>7}  {'Stop':>6}  {'EOD':>6}")
    print(f"  {'-'*12} {'-'*6}  {'-'*5}  {'-'*11}  {'-'*10}  {'-'*5}  {'-'*7}  {'-'*6}  {'-'*6}")

    # pre-compute best total across all scenarios for ★ flag
    all_scenario_totals = [grp_stats(sub,"ret_none","pnl_none")["total"]] + \
                          [grp_stats(sub,f"ret_{abs(sl)}",f"pnl_{abs(sl)}")["total"] for sl in STOP_LEVELS]
    best_of_all = max(all_scenario_totals)

    # No stop baseline row
    s = grp_stats(sub, "ret_none", "pnl_none")
    tgt = sum(1 for t in sub if t["reason_none"]=="target")
    eod = sum(1 for t in sub if t["reason_none"]=="eod")
    flag = " ★" if abs(s["total"] - best_of_all) < 0.01 else ""
    print(f"  {'No stop':<12} {s['n']:>6}  {s['wr']:>4.1f}%  ${s['total']:>+9,.0f}  ${s['avg']:>+8.2f}  {s['pf']:>5.2f}  {tgt:>7}  {'—':>6}  {eod:>6}{flag}")

    # Each stop level
    for sl in STOP_LEVELS:
        key_r = f"ret_{abs(sl)}"; key_p = f"pnl_{abs(sl)}"; key_rs = f"reason_{abs(sl)}"
        s = grp_stats(sub, key_r, key_p)
        tgt  = sum(1 for t in sub if t[key_rs]=="target")
        stop = sum(1 for t in sub if t[key_rs]=="stop")
        eod  = sum(1 for t in sub if t[key_rs]=="eod")
        flag = " ★" if abs(s["total"] - best_of_all) < 0.01 else ""
        print(f"  {f'{abs(sl)}% stop':<12} {s['n']:>6}  {s['wr']:>4.1f}%  ${s['total']:>+9,.0f}  ${s['avg']:>+8.2f}  {s['pf']:>5.2f}  {tgt:>7}  {stop:>6}  {eod:>6}{flag}")

# ── final summary heat-map table ──────────────────────────────────────────────
print("\n" + "="*72)
print("  SUMMARY TABLE — Total P&L by group × stop level  ($200/trade)")
print("="*72)
col_labels = ["No stop", " -5%", " -6%", " -7%", " -8%", " -9%", "-10%"]
print(f"\n  {'Group':<42} " + "  ".join(f"{c:>8}" for c in col_labels))
print(f"  {'-'*42} " + "  ".join(["-"*8]*7))
for group_label, fn in groups:
    sub = [t for t in all_trades if fn(t)]
    if not sub: continue
    vals = [sum(t["pnl_none"] for t in sub)]
    for sl in STOP_LEVELS:
        vals.append(sum(t[f"pnl_{abs(sl)}"] for t in sub))
    best = max(vals)
    row = f"  {group_label[:42]:<42} "
    row += "  ".join(f"${v:>+6,.0f}{'★' if abs(v-best)<0.01 else ' '}" for v in vals)
    print(row)

# ── year-by-year for best combo ───────────────────────────────────────────────
print("\n" + "="*72)
print("  YEAR-BY-YEAR — F3 (PM-aligned, no stop vs best stop)")
print("="*72)
f3 = [t for t in all_trades if t["direction_val"]==t["pm_dir"]]
by_year = defaultdict(list)
for t in f3: by_year[t["date"][:4]].append(t)
print(f"\n  {'Year':<6} {'Trades':>6}  {'WR%':>5}  {'No Stop':>9}  {'-6% Stop':>9}  {'-10% Stop':>10}")
print(f"  {'-'*6} {'-'*6}  {'-'*5}  {'-'*9}  {'-'*9}  {'-'*10}")
for yr in sorted(by_year):
    yt = by_year[yr]
    pn  = sum(t["pnl_none"] for t in yt)
    p6  = sum(t["pnl_6"]    for t in yt)
    p10 = sum(t["pnl_10"]   for t in yt)
    wr  = sum(1 for t in yt if t["ret_none"]>0)/len(yt)*100
    print(f"  {yr:<6} {len(yt):>6}  {wr:>4.1f}%  ${pn:>+7,.0f}  ${p6:>+7,.0f}  ${p10:>+8,.0f}")

print("\nDone.")
