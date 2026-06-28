"""
Grinder Loser Autopsy — Jun 1–5 + Jun 9–13, 2026
───────────────────────────────────────────────────
Re-runs every grinder signal and for each one captures extra diagnostic stats:
  • SPY slope at 10:30 AM (is the market trending up or rolling over?)
  • Price momentum in last 30 min before entry (stock still climbing or stalling?)
  • Distance from intraday HOD at entry (already extended off top?)
  • RVOL trajectory last 15 min vs earlier (volume accelerating or fading?)
  • Sector ETF color at entry
  • Gain vs open (how much had already been made before entry?)
  • t45 value bucket

Then splits winners vs losers and shows distribution differences.
"""
import yfinance as yf
import pandas as pd
import warnings, statistics
from datetime import date
warnings.filterwarnings("ignore")

UNIVERSE = [
    "AAPL","MSFT","NVDA","AMD","AVGO","QCOM","TXN","MU","AMAT","KLAC",
    "LRCX","ON","MRVL","INTC","SMCI","AMKR","ONTO",
    "JPM","GS","MS","BAC","AXP","V","MA","BLK",
    "XOM","CVX","COP","OXY","FRO","SLB","HAL","GE","HON","CAT",
    "JNJ","LLY","UNH","ABBV","MRK","PFE","AMGN","GILD",
    "AMZN","META","GOOGL","NFLX","TSLA","HD","COST","NKE",
    "ANET","DECK","AXON","CELH","CRWD","FTNT","LULU","MELI","MPWR","NET","PANW",
]
SECTOR_ETFS = ["SMH","XLK","XLE","XLF","XLV","XLY","XLI","XLC"]

WEEK_DATES  = [date(2026, 6, d) for d in [1, 2, 3, 4, 5, 9, 10, 11, 12, 13]]
WEEK1       = {date(2026, 6, d) for d in [1, 2, 3, 4, 5]}
ET          = "America/New_York"

GRINDER_RVOL_MIN = 1.3
GRINDER_RVOL_MAX = 3.0
GRINDER_CHG_MIN  = 2.0
GRINDER_CHG_MAX  = 8.0
T45_MIN = 0.5
T45_MAX = 2.0
DAY_MINS = 390.0


def scalar(v):
    if hasattr(v, "iloc"): v = v.iloc[0]
    try: return float(v)
    except: return 0.0

def get_col(df, metric, ticker):
    try:
        s = df[ticker][metric] if isinstance(df.columns, pd.MultiIndex) else df[metric]
        return s.dropna()
    except:
        return pd.Series(dtype=float)


print("\n" + "="*74)
print("  GRINDER LOSER AUTOPSY — Jun 1–5 + Jun 9–13, 2026")
print("  Finding what the scanner missed on every losing signal")
print("="*74)

tickers_to_fetch = UNIVERSE + SECTOR_ETFS + ["SPY"]

print("\nFetching daily data…")
daily = yf.download(
    tickers_to_fetch, start="2026-05-16", end="2026-06-15",
    interval="1d", group_by="ticker", auto_adjust=False, progress=False
)
print("Fetching 5-min intraday data…")
intra = yf.download(
    tickers_to_fetch, start="2026-06-01", end="2026-06-15",
    interval="5m", group_by="ticker", auto_adjust=False, progress=False
)
print("Data ready.\n")


signals = []

for dt in WEEK_DATES:
    open_ts  = pd.Timestamp(f"{dt} 09:30:00").tz_localize(ET)
    entry_ts = pd.Timestamp(f"{dt} 10:30:00").tz_localize(ET)
    exit_ts  = pd.Timestamp(f"{dt} 15:45:00").tz_localize(ET)
    p30_ts   = pd.Timestamp(f"{dt} 10:00:00").tz_localize(ET)  # 30 min before entry

    # ── SPY stats at entry ────────────────────────────────────────────────
    try:
        spy_c = get_col(intra, "Close", "SPY")
        spy_day = spy_c[(spy_c.index >= open_ts) & (spy_c.index <= entry_ts)]
        spy_open = scalar(spy_day.iloc[0])
        spy_now  = scalar(spy_day.iloc[-1])
        spy_chg  = (spy_now - spy_open) / spy_open * 100

        # SPY slope in last 30 min (10:00→10:30)
        spy_30 = spy_c[(spy_c.index >= p30_ts) & (spy_c.index <= entry_ts)]
        spy_slope = (scalar(spy_30.iloc[-1]) - scalar(spy_30.iloc[0])) / scalar(spy_30.iloc[0]) * 100 \
                    if len(spy_30) >= 2 else 0.0
    except:
        spy_chg = 0.0; spy_slope = 0.0

    # ── Sector ETF colors at entry ────────────────────────────────────────
    etf_green = {}
    for etf in SECTOR_ETFS:
        try:
            ec = get_col(intra, "Close", etf)
            ed = ec[(ec.index >= open_ts) & (ec.index <= entry_ts)]
            etf_green[etf] = scalar(ed.iloc[-1]) >= scalar(ed.iloc[0])
        except:
            etf_green[etf] = True

    spy_lbl = f"🟢 SPY {spy_chg:+.1f}% (slope {spy_slope:+.2f}%)" if spy_chg > 0 \
              else f"🔴 SPY {spy_chg:+.1f}% (slope {spy_slope:+.2f}%)"
    print(f"── {dt.strftime('%a %b %d')}  {spy_lbl}")

    for tkr in UNIVERSE:
        try:
            # daily prev close + avg vol
            dc = get_col(daily, "Close", tkr)
            dc.index = dc.index.tz_localize(None) if dc.index.tzinfo else dc.index
            dc_prev = dc[dc.index < pd.Timestamp(dt)]
            if dc_prev.empty: continue
            prev = scalar(dc_prev.iloc[-1])
            if prev <= 0: continue

            dv = get_col(daily, "Volume", tkr)
            dv.index = dv.index.tz_localize(None) if dv.index.tzinfo else dv.index
            avg_vol = float(dv[dv.index < pd.Timestamp(dt)].tail(10).mean())
            if avg_vol < 500_000: continue

            c5 = get_col(intra, "Close",  tkr)
            v5 = get_col(intra, "Volume", tkr)
            h5 = get_col(intra, "High",   tkr)
            l5 = get_col(intra, "Low",    tkr)
            o5 = get_col(intra, "Open",   tkr)

            mask_g   = (c5.index >= open_ts) & (c5.index <= entry_ts)
            exit_mask = (c5.index.date == dt) & (c5.index <= exit_ts)
            if mask_g.sum() < 9: continue

            c = c5[mask_g]; v = v5[mask_g]; h = h5[mask_g]; l = l5[mask_g]
            o = o5[mask_g]

            price_g   = scalar(c.iloc[-1])
            open_p    = scalar(o.iloc[0])
            cum_vol_g = float(v.sum())
            if price_g < 10.0 or cum_vol_g <= 0 or open_p <= 0: continue

            chg_g = (price_g - prev) / prev * 100
            if not (GRINDER_CHG_MIN <= chg_g <= GRINDER_CHG_MAX): continue

            day_frac = 60.0 / DAY_MINS
            proj_g   = cum_vol_g / day_frac
            rvol_g   = proj_g / avg_vol
            if not (GRINDER_RVOL_MIN <= rvol_g < GRINDER_RVOL_MAX): continue

            if float(v.max()) / cum_vol_g > 0.40: continue

            tp_g   = (h + l + c) / 3
            vwap_g = float((tp_g * v).sum()) / cum_vol_g
            if price_g < vwap_g: continue
            if (price_g - vwap_g) / vwap_g * 100 > 3.0: continue

            hod = float(h.max())
            pct_from_hod = (hod - price_g) / hod * 100 if hod > 0 else 0.0
            if pct_from_hod > 2.0: continue

            p45 = scalar(c.iloc[-9])
            t45 = (price_g - p45) / p45 * 100
            if not (T45_MIN <= t45 <= T45_MAX): continue
            if price_g <= p45: continue

            b30 = c.resample("30min").last().dropna()
            if len(b30) >= 2:
                ema9  = float(b30.ewm(span=9,  adjust=False).mean().iloc[-1])
                ema21 = float(b30.ewm(span=21, adjust=False).mean().iloc[-1]) if len(b30) >= 3 else ema9 - 0.01
                if ema9 <= ema21: continue

            # ── EXTRA DIAGNOSTIC STATS ─────────────────────────────────────

            # 1. Price slope in last 30 min (10:00→10:30 AM) — stalling or climbing?
            mask_30 = (c5.index >= p30_ts) & (c5.index <= entry_ts)
            c_30 = c5[mask_30]; v_30 = v5[mask_30]
            slope_30 = (scalar(c_30.iloc[-1]) - scalar(c_30.iloc[0])) / scalar(c_30.iloc[0]) * 100 \
                       if len(c_30) >= 2 else 0.0

            # 2. RVOL trajectory — last 15 min vol vs prior 45 min vol (per minute)
            mask_late = (v5.index >= p30_ts) & (v5.index <= entry_ts)
            mask_early = (v5.index >= open_ts) & (v5.index < p30_ts)
            vol_late  = float(v5[mask_late].sum())   / max(len(v5[mask_late]),  1)
            vol_early = float(v5[mask_early].sum())  / max(len(v5[mask_early]), 1)
            vol_accel = vol_late / vol_early if vol_early > 0 else 1.0  # >1 = vol accelerating

            # 3. Gain from open (how much had already run before entry?)
            gain_from_open = (price_g - open_p) / open_p * 100

            # 4. % from HOD at entry
            # (already computed above)

            # 5. SPY slope in last 30 min (shared per day)

            # 6. Sector ETF — try to determine ETF for this ticker
            # Use SMH for semis, XLK for tech, XLE for energy, etc.
            # Simple heuristic based on ticker membership
            SEMI   = {"NVDA","AMD","AVGO","QCOM","TXN","MU","AMAT","KLAC","LRCX",
                      "ON","MRVL","INTC","SMCI","AMKR","ONTO","MPWR"}
            TECH   = {"AAPL","MSFT","ANET","CRWD","FTNT","NET","PANW","GOOGL","META"}
            ENERGY = {"XOM","CVX","COP","OXY","FRO","SLB","HAL"}
            FINANCE= {"JPM","GS","MS","BAC","AXP","V","MA","BLK"}
            HEALTH = {"JNJ","LLY","UNH","ABBV","MRK","PFE","AMGN","GILD"}
            CONSUM = {"AMZN","HD","COST","NKE","LULU","MELI","DECK"}

            if tkr in SEMI:   etf = "SMH"
            elif tkr in TECH: etf = "XLK"
            elif tkr in ENERGY: etf = "XLE"
            elif tkr in FINANCE: etf = "XLF"
            elif tkr in HEALTH: etf = "XLV"
            elif tkr in CONSUM: etf = "XLY"
            else: etf = "XLK"

            sector_green = etf_green.get(etf, True)

            # 7. How many green 5-min bars in last 6 bars (consistency)?
            green_bars_6 = sum(1 for i in range(-6, 0)
                               if i-1 >= -len(c) and scalar(c.iloc[i]) > scalar(c.iloc[i-1]))

            # ── Exit ──────────────────────────────────────────────────────
            exit_g = scalar(c5[exit_mask].iloc[-1]) if exit_mask.sum() > 0 else price_g
            same_g = (exit_g - price_g) / price_g * 100

            win = "✅" if same_g > 0 else "❌"
            etf_flag = f"({etf} {'🟢' if sector_green else '🔴'})"
            print(f"  {'WIN ' if same_g>0 else 'LOSS'} {tkr:6s} "
                  f"+{chg_g:.1f}% | RVOL {rvol_g:.1f}x | t45 {t45:.2f}% | "
                  f"slope30 {slope_30:+.2f}% | volAccel {vol_accel:.1f}x | "
                  f"gainOpen {gain_from_open:+.1f}% | HOD-{pct_from_hod:.1f}% | "
                  f"spy_slope {spy_slope:+.2f}% | {etf_flag} | "
                  f"greenBars {green_bars_6}/6 → {same_g:+.2f}% {win}")

            signals.append({
                "ticker":        tkr,
                "date":          dt,
                "week":          1 if dt in WEEK1 else 2,
                "chg_pct":       round(chg_g, 2),
                "rvol":          round(rvol_g, 2),
                "t45":           round(t45, 2),
                "slope_30":      round(slope_30, 3),
                "vol_accel":     round(vol_accel, 2),
                "gain_from_open":round(gain_from_open, 2),
                "pct_from_hod":  round(pct_from_hod, 2),
                "spy_chg":       round(spy_chg, 2),
                "spy_slope":     round(spy_slope, 3),
                "sector_green":  sector_green,
                "green_bars_6":  green_bars_6,
                "entry_p":       round(price_g, 2),
                "exit_p":        round(exit_g, 2),
                "same_day":      round(same_g, 2),
                "etf":           etf,
            })

        except Exception:
            continue

    print()


# ── Analysis ────────────────────────────────────────────────────────────────
wins   = [s for s in signals if s["same_day"] > 0]
losses = [s for s in signals if s["same_day"] <= 0]

def avg(lst, key):
    v = [s[key] for s in lst if s.get(key) is not None]
    return statistics.mean(v) if v else 0.0

def pct_true(lst, key):
    v = [s[key] for s in lst if s.get(key) is not None]
    return sum(v) / len(v) * 100 if v else 0.0

def dist(lst, key, buckets):
    """% of signals in each bucket"""
    vals = [s[key] for s in lst if s.get(key) is not None]
    if not vals: return {}
    out = {}
    for lo, hi, label in buckets:
        n = sum(1 for v in vals if lo <= v < hi)
        out[label] = f"{n}/{len(vals)} ({n/len(vals)*100:.0f}%)"
    return out

print("\n" + "="*74)
print(f"  AUTOPSY: {len(wins)} WINNERS vs {len(losses)} LOSERS")
print("="*74)

print(f"""
  {'Metric':<30} {'Winners':>15} {'Losers':>15}  {'Diff':>10}
  {'─'*72}
  {'Avg gain/loss':<30} {avg(wins,'same_day'):>+14.2f}% {avg(losses,'same_day'):>+14.2f}%
  {'SPY chg at entry (open→now)':<30} {avg(wins,'spy_chg'):>+14.2f}% {avg(losses,'spy_chg'):>+14.2f}%  Δ{avg(wins,'spy_chg')-avg(losses,'spy_chg'):+.2f}%
  {'SPY slope last 30 min':<30} {avg(wins,'spy_slope'):>+14.3f}% {avg(losses,'spy_slope'):>+14.3f}%  Δ{avg(wins,'spy_slope')-avg(losses,'spy_slope'):+.3f}%
  {'Stock slope last 30 min':<30} {avg(wins,'slope_30'):>+14.3f}% {avg(losses,'slope_30'):>+14.3f}%  Δ{avg(wins,'slope_30')-avg(losses,'slope_30'):+.3f}%
  {'Vol accel (late vs early)':<30} {avg(wins,'vol_accel'):>14.2f}x {avg(losses,'vol_accel'):>14.2f}x  Δ{avg(wins,'vol_accel')-avg(losses,'vol_accel'):+.2f}x
  {'Gain from open at entry':<30} {avg(wins,'gain_from_open'):>+14.2f}% {avg(losses,'gain_from_open'):>+14.2f}%  Δ{avg(wins,'gain_from_open')-avg(losses,'gain_from_open'):+.2f}%
  {'Distance from HOD':<30} {avg(wins,'pct_from_hod'):>14.2f}% {avg(losses,'pct_from_hod'):>14.2f}%  Δ{avg(wins,'pct_from_hod')-avg(losses,'pct_from_hod'):+.2f}%
  {'t45 (45-min momentum)':<30} {avg(wins,'t45'):>14.3f}% {avg(losses,'t45'):>14.3f}%  Δ{avg(wins,'t45')-avg(losses,'t45'):+.3f}%
  {'RVOL at entry':<30} {avg(wins,'rvol'):>14.2f}x {avg(losses,'rvol'):>14.2f}x  Δ{avg(wins,'rvol')-avg(losses,'rvol'):+.2f}x
  {'Chg% vs prev close':<30} {avg(wins,'chg_pct'):>+14.2f}% {avg(losses,'chg_pct'):>+14.2f}%  Δ{avg(wins,'chg_pct')-avg(losses,'chg_pct'):+.2f}%
  {'Green bars (last 6)':<30} {avg(wins,'green_bars_6'):>14.2f} {avg(losses,'green_bars_6'):>14.2f}  Δ{avg(wins,'green_bars_6')-avg(losses,'green_bars_6'):+.2f}
  {'Sector ETF green %':<30} {pct_true(wins,'sector_green'):>14.1f}% {pct_true(losses,'sector_green'):>14.1f}%  Δ{pct_true(wins,'sector_green')-pct_true(losses,'sector_green'):+.1f}%
""")

# ── Distribution analysis ────────────────────────────────────────────────────
print("  ── SPY slope buckets (last 30 min into entry) ──")
buckets_spy = [(-9, -0.1, "SPY falling  (<-0.1%)"),
               (-0.1, 0.1, "SPY flat (-0.1%–+0.1%)"),
               (0.1,  9.0, "SPY rising   (>+0.1%)")]
for label, lst, tag in [("Winners", wins, ""), ("Losers ", losses, "")]:
    d = dist(lst, "spy_slope", buckets_spy)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── Stock slope last 30 min ──")
buckets_slope = [(-9, -0.05, "Fading  (<-0.05%)"),
                 (-0.05, 0.1, "Flat (-0.05–+0.1%)"),
                 (0.1, 9.0, "Climbing (>+0.1%)")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "slope_30", buckets_slope)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── SPY chg from open buckets ──")
buckets_spychg = [(-9, -0.2, "SPY <-0.2%"), (-0.2, 0.2, "SPY flat"), (0.2, 9, "SPY >+0.2%")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "spy_chg", buckets_spychg)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── Gain from open at entry ──")
buckets_open = [(0, 1.5, "0–1.5% gain"), (1.5, 3, "1.5–3% gain"), (3, 9, ">3% gain")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "gain_from_open", buckets_open)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── Vol acceleration (late 30 min vs early 60 min) ──")
buckets_vol = [(0, 0.8, "Fading (<0.8x)"), (0.8, 1.2, "Steady (0.8–1.2x)"), (1.2, 9, "Accel (>1.2x)")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "vol_accel", buckets_vol)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── t45 buckets ──")
buckets_t45 = [(0.5, 0.8, "0.5–0.8%"), (0.8, 1.2, "0.8–1.2%"), (1.2, 2.0, "1.2–2.0%")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "t45", buckets_t45)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

# ── Sector breakdown ─────────────────────────────────────────────────────────
print("\n  ── Win rate by sector ETF ──")
etfs = sorted(set(s["etf"] for s in signals))
for etf in etfs:
    grp = [s for s in signals if s["etf"] == etf]
    if not grp: continue
    w = sum(1 for s in grp if s["same_day"] > 0)
    wr = w / len(grp) * 100
    bar = "▓" * w + "░" * (len(grp) - w)
    print(f"    {etf:5s}  {bar}  {w}/{len(grp)} ({wr:.0f}% WR)")

# ── Proposed new filters ─────────────────────────────────────────────────────
print("\n" + "="*74)
print("  FILTER TESTS — Two-week full sample (Jun 1–5 + Jun 9–13, 2026)")
print("="*74)

DEAD_SECTORS = {"XLV", "XLY"}

n  = len(signals)
nw = len(wins)
base_wr  = nw / n * 100
base_avg_w = avg(wins, "same_day")
base_avg_l = avg(losses, "same_day")
base_ev    = base_wr/100 * base_avg_w + (1 - base_wr/100) * base_avg_l

print(f"\n  BASELINE (current scanner):")
print(f"    {n} signals  WR={base_wr:.1f}%  avg_win={base_avg_w:+.2f}%  avg_loss={base_avg_l:+.2f}%  EV={base_ev:+.3f}%/trade")

def show_filter(label, flt):
    fw  = [s for s in flt if s["same_day"] > 0]
    fl  = [s for s in flt if s["same_day"] <= 0]
    if not flt:
        print(f"\n  {label}: 0 signals")
        return
    wr   = len(fw)/len(flt)*100
    aw   = avg(fw, "same_day")
    al   = avg(fl, "same_day")
    ev   = wr/100*aw + (1-wr/100)*al
    drop = n - len(flt)
    w_drop = nw - len(fw)
    l_drop = len(losses) - len(fl)
    print(f"\n  {label}")
    print(f"    {len(flt)} signals (removed {drop}: -{w_drop} winners, -{l_drop} losers)")
    print(f"    WR={wr:.1f}%  avg_win={aw:+.2f}%  avg_loss={al:+.2f}%  EV={ev:+.3f}%/trade  "
          f"(was {base_wr:.0f}% / EV {base_ev:+.3f}%)")


# Filter A: Block dead sectors (XLV + XLY — 0% WR combined)
fA = [s for s in signals if s["etf"] not in DEAD_SECTORS]
show_filter("A. Block XLV + XLY (0% WR sectors)", fA)

# Filter B: Cap gain-from-open at 3% (exhausted movers)
fB = [s for s in signals if s["gain_from_open"] < 3.0]
show_filter("B. Cap gain-from-open < 3%  (ONTO +6%, INTC +4.7% killed us)", fB)

# Filter C: Block when SPY slope < -0.15% (market rolling over)
fC = [s for s in signals if s["spy_slope"] >= -0.15]
show_filter("C. Block when SPY slope < -0.15% (Jun 9 kill zone)", fC)

# Filter AB: A + B
fAB = [s for s in fA if s["gain_from_open"] < 3.0]
show_filter("A+B. No dead sectors + gain-from-open < 3%", fAB)

# Filter AC: A + C
fAC = [s for s in fA if s["spy_slope"] >= -0.15]
show_filter("A+C. No dead sectors + SPY slope filter", fAC)

# Filter BC: B + C
fBC = [s for s in fB if s["spy_slope"] >= -0.15]
show_filter("B+C. Gain-from-open < 3% + SPY slope filter", fBC)

# Filter ABC: All three
fABC = [s for s in fAB if s["spy_slope"] >= -0.15]
show_filter("A+B+C. All three combined", fABC)

# Show which signals are removed by each filter
print("\n" + "─"*74)
print("  Signals removed by each filter:")
print(f"  {'Ticker':6s} {'Date':12s} {'Sector':5s} {'GainOpen':9s} {'SPYslope':9s} {'Outcome':8s}  Removed by")
for s in sorted(signals, key=lambda x: x["date"]):
    removed_by = []
    if s["etf"] in DEAD_SECTORS:          removed_by.append("A(sector)")
    if s["gain_from_open"] >= 3.0:        removed_by.append("B(gainOpen)")
    if s["spy_slope"] < -0.15:            removed_by.append("C(SPYslope)")
    if not removed_by: continue
    tag = "✅" if s["same_day"] > 0 else "❌"
    print(f"  {s['ticker']:6s} {str(s['date']):12s} {s['etf']:5s} "
          f"{s['gain_from_open']:+8.1f}% {s['spy_slope']:+8.3f}%  "
          f"{s['same_day']:+6.2f}% {tag}  {', '.join(removed_by)}")

print()

# ── Print all losers with annotations ────────────────────────────────────────
print("="*74)
print("  EVERY LOSER — annotated")
print("="*74)
for s in sorted(losses, key=lambda x: x["same_day"]):
    flags = []
    if s["spy_slope"] < -0.05:  flags.append("SPY-FALLING")
    if s["slope_30"] < -0.02:   flags.append("STOCK-FADING")
    if s["vol_accel"] < 0.8:    flags.append("VOL-COLLAPSING")
    if not s["sector_green"]:   flags.append(f"{s['etf']}-RED")
    if s["spy_chg"] < -0.2:     flags.append("SPY-RED")
    print(f"  ❌ {s['ticker']:6s} {s['date']}  {s['same_day']:+.2f}%  "
          f"SPY {s['spy_chg']:+.1f}%/slope {s['spy_slope']:+.3f}%  "
          f"stock_slope {s['slope_30']:+.3f}%  "
          f"volAccel {s['vol_accel']:.1f}x  "
          f"{'  '.join(flags) if flags else '(no obvious flag)'}")
print()
