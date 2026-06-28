"""
Morning Burst Loser Autopsy — Jun 1–5 + Jun 9–13, 2026
────────────────────────────────────────────────────────
Same logic as the grinder autopsy but applied to the early morning burst
scanner (9:35–9:50 AM signals, exit 3:45 PM same day).

For every losing signal we capture:
  • Signal time (how many minutes after open?)
  • SPY direction at signal bar (rising or falling?)
  • SPY chg% from open to signal
  • Sector ETF color at signal bar
  • RVOL at signal vs threshold
  • Chg% vs prev close
  • Gap-up vs continuation (open vs prev close)
  • How far above VWAP at entry (extended or just reclaimed?)
  • Price action quality: was the first 5 bars clean or choppy?
  • % from intraday HOD at entry (already pulled back?)

Then splits winners vs losers, shows distributions, and tests filter combos.
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

WEEK_DATES = [date(2026, 6, d) for d in [1, 2, 3, 4, 5, 9, 10, 11, 12, 13]]
WEEK1      = {date(2026, 6, d) for d in [1, 2, 3, 4, 5]}
ET         = "America/New_York"

RVOL_MIN   = 2.0
CHG_MIN    = 3.0
DAY_MINS   = 390.0

# Sector mapping (same as grinder)
SEMI   = {"NVDA","AMD","AVGO","QCOM","TXN","MU","AMAT","KLAC","LRCX",
          "ON","MRVL","INTC","SMCI","AMKR","ONTO","MPWR"}
TECH   = {"AAPL","MSFT","ANET","CRWD","FTNT","NET","PANW","GOOGL","META","NFLX"}
ENERGY = {"XOM","CVX","COP","OXY","FRO","SLB","HAL"}
FINANCE= {"JPM","GS","MS","BAC","AXP","V","MA","BLK"}
HEALTH = {"JNJ","LLY","UNH","ABBV","MRK","PFE","AMGN","GILD"}
CONSUM = {"AMZN","HD","COST","NKE","LULU","MELI","DECK","TSLA","AXON","CELH"}

def ticker_etf(tkr):
    if tkr in SEMI:   return "SMH"
    if tkr in TECH:   return "XLK"
    if tkr in ENERGY: return "XLE"
    if tkr in FINANCE:return "XLF"
    if tkr in HEALTH: return "XLV"
    if tkr in CONSUM: return "XLY"
    return "XLK"

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
print("  MORNING BURST LOSER AUTOPSY — Jun 1–5 + Jun 9–13, 2026")
print("  Entry = first qualifying 5-min bar 9:35–9:50 AM  |  Exit = 3:45 PM")
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
    open_ts   = pd.Timestamp(f"{dt} 09:30:00").tz_localize(ET)
    cutoff_ts = pd.Timestamp(f"{dt} 09:50:00").tz_localize(ET)  # morning burst window
    exit_ts   = pd.Timestamp(f"{dt} 15:45:00").tz_localize(ET)

    # ── SPY at each bar ───────────────────────────────────────────────────
    spy_c = get_col(intra, "Close", "SPY")
    spy_open_val = None
    try:
        spy_day = spy_c[(spy_c.index >= open_ts) & (spy_c.index.date == dt)]
        spy_open_val = scalar(spy_day.iloc[0]) if len(spy_day) > 0 else None
    except: pass

    # ── Sector ETF at each bar ────────────────────────────────────────────
    etf_series = {}
    for etf in SECTOR_ETFS:
        try:
            ec = get_col(intra, "Close", etf)
            ed = ec[(ec.index >= open_ts) & (ec.index.date == dt)]
            etf_series[etf] = ed
        except: etf_series[etf] = pd.Series(dtype=float)

    print(f"── {dt.strftime('%a %b %d')}")

    for tkr in UNIVERSE:
        try:
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

            day_mask  = (c5.index >= open_ts) & (c5.index.date == dt)
            exit_mask = (c5.index.date == dt) & (c5.index <= exit_ts)
            c_day = c5[day_mask]; v_day = v5[day_mask]
            h_day = h5[day_mask]; l_day = l5[day_mask]
            if len(c_day) < 3: continue

            exit_p = scalar(c5[exit_mask].iloc[-1]) if exit_mask.sum() > 0 else None

            # Scan each 5-min bar for the first qualifying signal
            signal = None
            for i in range(1, len(c_day)):
                bar_ts = c_day.index[i]
                if bar_ts > cutoff_ts:
                    break

                elapsed_mins = (bar_ts - open_ts).total_seconds() / 60.0 + 5
                if elapsed_mins < 1: continue

                c_so_far = c_day.iloc[:i+1]
                v_so_far = v_day.iloc[:i+1]
                h_so_far = h_day.iloc[:i+1]
                l_so_far = l_day.iloc[:i+1]

                cum_vol   = float(v_so_far.sum())
                price     = scalar(c_so_far.iloc[-1])
                if price < 10.0 or cum_vol <= 0: continue

                chg_pct   = (price - prev) / prev * 100
                if chg_pct < CHG_MIN: continue

                proj_vol  = cum_vol * (DAY_MINS / elapsed_mins)
                rvol      = proj_vol / avg_vol
                if rvol < RVOL_MIN: continue

                # VWAP from open
                tp   = (h_so_far + l_so_far + c_so_far) / 3
                vwap = float((tp * v_so_far).sum()) / cum_vol
                if price < vwap: continue

                # ── EXTRA DIAGNOSTIC STATS ─────────────────────────────────

                # 1. SPY chg at signal bar
                spy_at_bar = None
                spy_slope_5 = 0.0   # SPY direction in last 5 min
                if spy_open_val and spy_open_val > 0:
                    try:
                        spy_bar = spy_c[spy_c.index <= bar_ts]
                        spy_bar = spy_bar[spy_bar.index >= open_ts]
                        spy_now = scalar(spy_bar.iloc[-1])
                        spy_at_bar = (spy_now - spy_open_val) / spy_open_val * 100
                        if len(spy_bar) >= 2:
                            spy_prev_bar = scalar(spy_bar.iloc[-2])
                            spy_slope_5 = (spy_now - spy_prev_bar) / spy_prev_bar * 100
                    except: pass

                # 2. Sector ETF color at signal bar
                etf = ticker_etf(tkr)
                sector_green = True
                try:
                    es = etf_series.get(etf, pd.Series(dtype=float))
                    es_at_bar = es[es.index <= bar_ts]
                    if len(es_at_bar) >= 2:
                        sector_green = scalar(es_at_bar.iloc[-1]) >= scalar(es_at_bar.iloc[0])
                except: pass

                # 3. Gap-up size (open vs prev close)
                open_bar = scalar(o5[(o5.index >= open_ts) & (o5.index.date == dt)].iloc[0]) \
                           if len(o5[(o5.index >= open_ts) & (o5.index.date == dt)]) > 0 else prev
                gap_pct = (open_bar - prev) / prev * 100

                # 4. VWAP extension (how far above VWAP at entry)
                vwap_ext = (price - vwap) / vwap * 100 if vwap > 0 else 0.0

                # 5. Price action quality: were the first N bars choppy?
                # Choppiness = max single bar chg / total chg (1.0 = all in one bar, 0.2 = smooth)
                bar_chgs = [abs(scalar(c_so_far.iloc[j]) - scalar(c_so_far.iloc[j-1]))
                            for j in range(1, len(c_so_far))]
                total_move = abs(price - scalar(c_so_far.iloc[0]))
                choppiness = max(bar_chgs) / total_move if total_move > 0 else 1.0

                # 6. Distance from HOD at entry
                hod = float(h_so_far.max())
                pct_from_hod = (hod - price) / hod * 100 if hod > 0 else 0.0

                # 7. Minutes since open
                mins_since_open = elapsed_mins

                signal = {
                    "ticker":          tkr,
                    "date":            dt,
                    "week":            1 if dt in WEEK1 else 2,
                    "signal_time":     bar_ts.strftime("%H:%M"),
                    "mins_since_open": round(mins_since_open, 1),
                    "chg_pct":         round(chg_pct, 2),
                    "rvol":            round(rvol, 2),
                    "gap_pct":         round(gap_pct, 2),
                    "vwap_ext":        round(vwap_ext, 2),
                    "choppiness":      round(choppiness, 2),
                    "pct_from_hod":    round(pct_from_hod, 2),
                    "spy_chg":         round(spy_at_bar, 2) if spy_at_bar is not None else 0.0,
                    "spy_slope_5":     round(spy_slope_5, 3),
                    "sector_green":    sector_green,
                    "etf":             etf,
                    "entry_p":         round(price, 2),
                    "exit_p":          round(exit_p, 2) if exit_p else None,
                    "same_day":        round((exit_p - price) / price * 100, 2) if exit_p else None,
                }
                break

            if signal and signal.get("same_day") is not None:
                win = "✅" if signal["same_day"] > 0 else "❌"
                etf_flag = f"({signal['etf']} {'🟢' if signal['sector_green'] else '🔴'})"
                print(f"  {'WIN ' if signal['same_day']>0 else 'LOSS'} "
                      f"{signal['signal_time']} {tkr:6s} "
                      f"+{signal['chg_pct']:.1f}% | RVOL {signal['rvol']:.1f}x | "
                      f"gap {signal['gap_pct']:+.1f}% | vwapExt {signal['vwap_ext']:+.1f}% | "
                      f"chop {signal['choppiness']:.2f} | spy {signal['spy_chg']:+.2f}% "
                      f"(Δ{signal['spy_slope_5']:+.3f}%) | {etf_flag} "
                      f"→ {signal['same_day']:+.2f}% {win}")
                signals.append(signal)

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
    vals = [s[key] for s in lst if s.get(key) is not None]
    if not vals: return {}
    return {lbl: f"{sum(1 for v in vals if lo<=v<hi)}/{len(vals)} "
                 f"({sum(1 for v in vals if lo<=v<hi)/len(vals)*100:.0f}%)"
            for lo, hi, lbl in buckets}

print("\n" + "="*74)
print(f"  AUTOPSY: {len(wins)} WINNERS vs {len(losses)} LOSERS  (morning burst, both weeks)")
print("="*74)

print(f"""
  {'Metric':<32} {'Winners':>14} {'Losers':>14}  Δ
  {'─'*72}
  {'Avg gain/loss':<32} {avg(wins,'same_day'):>+13.2f}% {avg(losses,'same_day'):>+13.2f}%
  {'Minutes since open (signal)':<32} {avg(wins,'mins_since_open'):>13.1f}  {avg(losses,'mins_since_open'):>13.1f}  Δ{avg(wins,'mins_since_open')-avg(losses,'mins_since_open'):+.1f}
  {'RVOL at signal':<32} {avg(wins,'rvol'):>13.1f}x {avg(losses,'rvol'):>13.1f}x  Δ{avg(wins,'rvol')-avg(losses,'rvol'):+.1f}x
  {'Chg% vs prev close':<32} {avg(wins,'chg_pct'):>+13.2f}% {avg(losses,'chg_pct'):>+13.2f}%  Δ{avg(wins,'chg_pct')-avg(losses,'chg_pct'):+.2f}%
  {'Gap-up size (open vs prev)':<32} {avg(wins,'gap_pct'):>+13.2f}% {avg(losses,'gap_pct'):>+13.2f}%  Δ{avg(wins,'gap_pct')-avg(losses,'gap_pct'):+.2f}%
  {'VWAP extension at entry':<32} {avg(wins,'vwap_ext'):>+13.2f}% {avg(losses,'vwap_ext'):>+13.2f}%  Δ{avg(wins,'vwap_ext')-avg(losses,'vwap_ext'):+.2f}%
  {'Choppiness (0=smooth, 1=spike)':<32} {avg(wins,'choppiness'):>13.2f}  {avg(losses,'choppiness'):>13.2f}  Δ{avg(wins,'choppiness')-avg(losses,'choppiness'):+.2f}
  {'% from HOD at entry':<32} {avg(wins,'pct_from_hod'):>13.2f}% {avg(losses,'pct_from_hod'):>13.2f}%  Δ{avg(wins,'pct_from_hod')-avg(losses,'pct_from_hod'):+.2f}%
  {'SPY chg from open at signal':<32} {avg(wins,'spy_chg'):>+13.2f}% {avg(losses,'spy_chg'):>+13.2f}%  Δ{avg(wins,'spy_chg')-avg(losses,'spy_chg'):+.2f}%
  {'SPY last-bar slope':<32} {avg(wins,'spy_slope_5'):>+13.3f}% {avg(losses,'spy_slope_5'):>+13.3f}%  Δ{avg(wins,'spy_slope_5')-avg(losses,'spy_slope_5'):+.3f}%
  {'Sector ETF green %':<32} {pct_true(wins,'sector_green'):>13.1f}% {pct_true(losses,'sector_green'):>13.1f}%  Δ{pct_true(wins,'sector_green')-pct_true(losses,'sector_green'):+.1f}%
""")

# ── Distribution breakdowns ──────────────────────────────────────────────────
print("  ── Signal time (minutes after open) ──")
buckets_time = [(0,6,"0–5 min (9:35)"), (6,11,"5–10 min (9:40)"), (11,21,"10–20 min (9:45-50)")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "mins_since_open", buckets_time)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── Gap-up size ──")
buckets_gap = [(-1, 1, "Flat (<1% gap)"), (1, 3, "Moderate (1–3%)"), (3, 20, "Big (>3%)")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "gap_pct", buckets_gap)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── VWAP extension at entry ──")
buckets_vwap = [(0, 1, "Tight (0–1%)"), (1, 2, "Moderate (1–2%)"), (2, 10, "Extended (>2%)")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "vwap_ext", buckets_vwap)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── Choppiness ──")
buckets_chop = [(0, 0.4, "Smooth (<0.4)"), (0.4, 0.7, "Moderate (0.4–0.7)"), (0.7, 2, "Choppy (>0.7)")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "choppiness", buckets_chop)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── RVOL at signal ──")
buckets_rvol = [(0, 2.5, "<2.5x"), (2.5, 4, "2.5–4x"), (4, 99, ">4x")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "rvol", buckets_rvol)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── SPY chg from open at signal ──")
buckets_spy = [(-5, -0.1, "SPY negative"), (-0.1, 0.2, "SPY flat"), (0.2, 5, "SPY positive")]
for label, lst in [("Winners", wins), ("Losers ", losses)]:
    d = dist(lst, "spy_chg", buckets_spy)
    print(f"    {label}: " + "  |  ".join(f"{k}: {v}" for k, v in d.items()))

print("\n  ── Win rate by sector ETF ──")
for etf in sorted(set(s["etf"] for s in signals)):
    grp = [s for s in signals if s["etf"] == etf]
    if not grp: continue
    w = sum(1 for s in grp if s["same_day"] > 0)
    wr = w / len(grp) * 100
    bar = "▓" * w + "░" * (len(grp) - w)
    print(f"    {etf:5s}  {bar}  {w}/{len(grp)} ({wr:.0f}% WR)")

# ── Filter tests ──────────────────────────────────────────────────────────────
print("\n" + "="*74)
print("  FILTER TESTS — Two-week full sample")
print("="*74)

n    = len(signals)
nw   = len(wins)
base_wr  = nw / n * 100
base_aw  = avg(wins,   "same_day")
base_al  = avg(losses, "same_day")
base_ev  = base_wr/100 * base_aw + (1-base_wr/100) * base_al

print(f"\n  BASELINE: {n} signals  WR={base_wr:.1f}%  "
      f"avg_win={base_aw:+.2f}%  avg_loss={base_al:+.2f}%  EV={base_ev:+.3f}%/trade")

def show_filter(label, flt):
    fw = [s for s in flt if s["same_day"] > 0]
    fl = [s for s in flt if s["same_day"] <= 0]
    if not flt:
        print(f"\n  {label}: 0 signals"); return
    wr  = len(fw)/len(flt)*100
    aw  = avg(fw, "same_day")
    al  = avg(fl, "same_day")
    ev  = wr/100*aw + (1-wr/100)*al
    w_drop = nw - len(fw)
    l_drop = len(losses) - len(fl)
    print(f"\n  {label}")
    print(f"    {len(flt)} signals (removed {n-len(flt)}: -{w_drop} wins, -{l_drop} losses)")
    print(f"    WR={wr:.1f}%  avg_win={aw:+.2f}%  avg_loss={al:+.2f}%  EV={ev:+.3f}%/trade")

# Individual filters
show_filter("A. Block SPY negative at signal (spy_chg < 0)",
            [s for s in signals if s["spy_chg"] >= 0])
show_filter("B. Require RVOL ≥ 2.5x (raise bar from 2.0x)",
            [s for s in signals if s["rvol"] >= 2.5])
show_filter("C. Block extended VWAP (vwap_ext > 2%)",
            [s for s in signals if s["vwap_ext"] <= 2.0])
show_filter("D. Block big gaps (gap > 4%)",
            [s for s in signals if s["gap_pct"] <= 4.0])
show_filter("E. Block choppy entry (choppiness > 0.7)",
            [s for s in signals if s["choppiness"] <= 0.7])
show_filter("F. Block XLV + XLY (same as grinder fix)",
            [s for s in signals if s["etf"] not in {"XLV","XLY"}])

# Combo tests
show_filter("A+B. SPY positive + RVOL ≥ 2.5x",
            [s for s in signals if s["spy_chg"] >= 0 and s["rvol"] >= 2.5])
show_filter("A+F. SPY positive + no XLV/XLY",
            [s for s in signals if s["spy_chg"] >= 0 and s["etf"] not in {"XLV","XLY"}])
show_filter("B+F. RVOL ≥ 2.5x + no XLV/XLY",
            [s for s in signals if s["rvol"] >= 2.5 and s["etf"] not in {"XLV","XLY"}])
show_filter("A+B+F. SPY positive + RVOL ≥ 2.5x + no XLV/XLY",
            [s for s in signals if s["spy_chg"] >= 0 and s["rvol"] >= 2.5
             and s["etf"] not in {"XLV","XLY"}])

# ── Print every loser annotated ───────────────────────────────────────────────
print("\n" + "="*74)
print("  EVERY LOSER — annotated")
print("="*74)
for s in sorted(losses, key=lambda x: x["same_day"]):
    flags = []
    if s["spy_chg"]    <  0:     flags.append("SPY-NEGATIVE")
    if s["spy_slope_5"]< -0.05:  flags.append("SPY-FALLING")
    if s["vwap_ext"]   >  2.0:   flags.append("VWAP-EXTENDED")
    if s["gap_pct"]    >  4.0:   flags.append("BIG-GAP")
    if s["choppiness"] >  0.7:   flags.append("CHOPPY")
    if s["rvol"]       <  2.5:   flags.append("LOW-RVOL")
    if s["etf"] in {"XLV","XLY"}:flags.append(f"{s['etf']}")
    print(f"  ❌ {s['signal_time']} {s['ticker']:6s} {s['date']}  {s['same_day']:+.2f}%  "
          f"RVOL {s['rvol']:.1f}x  +{s['chg_pct']:.1f}%  "
          f"gap {s['gap_pct']:+.1f}%  vwap+{s['vwap_ext']:.1f}%  "
          f"chop {s['choppiness']:.2f}  SPY {s['spy_chg']:+.2f}%  "
          f"{'  '.join(flags) if flags else '(no obvious flag)'}")
print()
