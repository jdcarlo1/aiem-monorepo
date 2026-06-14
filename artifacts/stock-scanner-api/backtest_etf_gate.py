"""
ETF Gate Comparison: Open-of-Day vs VWAP — Jun 1–5 + Jun 9–13, 2026
────────────────────────────────────────────────────────────────────
Tests whether switching the sector ETF filter gate from
  "ETF price > ETF today's open"  (current live gate)
to
  "ETF price > ETF VWAP"          (proposed upgrade)
improves the Steady Grinder win rate at 10:30 AM entry.

Also tests the morning burst scanner (9:35-9:45 AM) with both gates.
Note: at 9:35 AM the VWAP only has 5 min of data — nearly identical to open.
The real difference shows at 10:30 AM when 60 min of volume have accumulated.

Results show 4 columns:
  NO GATE      — raw grinder/morning signals, no ETF filter
  OPEN GATE    — current live: ETF now > ETF open price today
  VWAP GATE    — proposed: ETF now > ETF volume-weighted avg since 9:30
  BOTH AGREE   — only signals where open gate AND vwap gate agree (most conservative)
"""
import yfinance as yf
import pandas as pd
import warnings, statistics
from datetime import date
warnings.filterwarnings("ignore")

# ── Universe ────────────────────────────────────────────────────────────────
UNIVERSE = [
    "AAPL","MSFT","NVDA","AMD","AVGO","QCOM","TXN","MU","AMAT","KLAC",
    "LRCX","ON","MRVL","INTC","SMCI","AMKR","ONTO",
    "JPM","GS","MS","BAC","AXP","V","MA","BLK",
    "XOM","CVX","COP","OXY","FRO","SLB","HAL","GE","HON","CAT",
    "JNJ","LLY","UNH","ABBV","MRK","PFE","AMGN","GILD",
    "AMZN","META","GOOGL","NFLX","TSLA","HD","COST","NKE",
    "ANET","DECK","AXON","CELH","CRWD","FTNT","LULU","MELI","MPWR","NET","PANW",
]

# Sector ETF mapping (same as live scanner)
SECTOR_ETF = {
    "NVDA":"SMH","AMD":"SMH","AVGO":"SMH","QCOM":"SMH","MU":"SMH",
    "AMAT":"SMH","KLAC":"SMH","LRCX":"SMH","ON":"SMH","MRVL":"SMH",
    "INTC":"SMH","SMCI":"SMH","AMKR":"SMH","ONTO":"SMH",
    "AAPL":"XLK","MSFT":"XLK","TXN":"XLK","MPWR":"XLK","NET":"XLK",
    "CRWD":"XLK","FTNT":"XLK","PANW":"XLK","ANET":"XLK",
    "JPM":"XLF","GS":"XLF","MS":"XLF","BAC":"XLF","AXP":"XLF",
    "V":"XLF","MA":"XLF","BLK":"XLF",
    "XOM":"XLE","CVX":"XLE","COP":"XLE","OXY":"XLE","SLB":"XLE",
    "HAL":"XLE","FRO":"XLE",
    "GE":"XLI","HON":"XLI","CAT":"XLI",
    "JNJ":"XLV","LLY":"XLV","UNH":"XLV","ABBV":"XLV","MRK":"XLV",
    "PFE":"XLV","AMGN":"XLV","GILD":"XLV",
    "AMZN":"XLY","TSLA":"XLY","HD":"XLY","COST":"XLY","NKE":"XLY",
    "LULU":"XLY","DECK":"XLY","CELH":"XLY",
    "META":"XLC","GOOGL":"XLC","NFLX":"XLC",
    "AXON":"XLI","MELI":"XLC",
}

ALL_ETFS = list(set(SECTOR_ETF.values())) + ["SPY"]

WEEK_DATES = [date(2026, 6, d) for d in [1, 2, 3, 4, 5, 9, 10, 11, 12, 13]]
ET = "America/New_York"

GRINDER_RVOL_MIN = 1.3
GRINDER_RVOL_MAX = 3.0
GRINDER_CHG_MIN  = 2.0
GRINDER_CHG_MAX  = 8.0
T45_MIN = 0.5
T45_MAX = 2.0
DAY_MINS = 390.0


def scalar(v):
    if hasattr(v, "iloc"): v = v.iloc[0]
    return float(v)


def get_col(df, metric, ticker):
    try:
        if isinstance(df.columns, pd.MultiIndex):
            s = df[ticker][metric]
        else:
            s = df[metric]
        return s.dropna()
    except Exception:
        return pd.Series(dtype=float)


def etf_state(etf_data: dict, etf: str, as_of_ts):
    """Returns (open_green, vwap_green) booleans for the given ETF at as_of_ts."""
    info = etf_data.get(etf)
    if info is None:
        return None, None   # no data — don't block
    c5, v5, h5, l5 = info
    mask = c5.index <= as_of_ts
    c = c5[mask]; v = v5[mask]; h = h5[mask]; l = l5[mask]
    if c.empty:
        return None, None
    now_p  = scalar(c.iloc[-1])
    open_p = scalar(c.iloc[0])
    tp     = (h + l + c) / 3
    cum_v  = float(v.sum())
    vwap   = float((tp * v).sum()) / cum_v if cum_v > 0 else open_p
    return (now_p > open_p), (now_p > vwap)


print("\n" + "="*74)
print("  ETF GATE COMPARISON: OPEN vs VWAP  —  Jun 1–5 + Jun 9–13, 2026")
print("  Steady Grinder (10:30 AM entry) + Morning Burst (9:35–9:45 AM)")
print("="*74)

print("\nFetching daily data (May 16 – Jun 14)…")
daily = yf.download(
    UNIVERSE, start="2026-05-16", end="2026-06-14",
    interval="1d", group_by="ticker", auto_adjust=True, progress=False
)

all_tickers = UNIVERSE + ALL_ETFS
print("Fetching 5-min intraday data (Jun 1 – Jun 14)…")
intra = yf.download(
    all_tickers, start="2026-06-01", end="2026-06-14",
    interval="5m", group_by="ticker", auto_adjust=True, progress=False
)
print("Data ready.\n")


# ── Results buckets ──────────────────────────────────────────────────────────
# Each signal dict has: ticker, date, same_day, open_etf_pass, vwap_etf_pass
grinder_all  = []
morning_all  = []


for dt in WEEK_DATES:
    open_ts    = pd.Timestamp(f"{dt} 09:30:00").tz_localize(ET)
    grind_ts   = pd.Timestamp(f"{dt} 10:30:00").tz_localize(ET)
    exit_ts    = pd.Timestamp(f"{dt} 15:45:00").tz_localize(ET)
    burst_end  = pd.Timestamp(f"{dt} 09:50:00").tz_localize(ET)

    # Pre-load ETF bars for this day
    etf_data = {}
    for etf in set(SECTOR_ETF.values()):
        try:
            c5 = get_col(intra, "Close",  etf)
            v5 = get_col(intra, "Volume", etf)
            h5 = get_col(intra, "High",   etf)
            l5 = get_col(intra, "Low",    etf)
            day_mask = (c5.index >= open_ts) & (c5.index.date == dt)
            etf_data[etf] = (c5[day_mask], v5[day_mask], h5[day_mask], l5[day_mask])
        except Exception:
            pass

    try:
        spy_c = get_col(intra, "Close", "SPY")
        spy_d = spy_c[(spy_c.index >= open_ts) & (spy_c.index <= grind_ts) & (spy_c.index.date == dt)]
        spy_open = scalar(spy_d.iloc[0]) if not spy_d.empty else None
        spy_now  = scalar(spy_d.iloc[-1]) if not spy_d.empty else None
        spy_green = spy_open and spy_now and spy_now > spy_open
        spy_lbl = f"🟢 SPY {(spy_now/spy_open-1)*100:+.1f}%" if spy_green else f"🔴 SPY ?"
    except Exception:
        spy_green = None
        spy_lbl = "❓ SPY ?"

    print(f"── {dt.strftime('%a %b %d')}  {spy_lbl}")

    for tkr in UNIVERSE:
        try:
            # Daily prev close + avg vol
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

            day_mask = (c5.index >= open_ts) & (c5.index.date == dt)
            exit_mask = (c5.index.date == dt) & (c5.index <= exit_ts)
            exit_p = scalar(c5[exit_mask].iloc[-1]) if exit_mask.sum() > 0 else None

            etf = SECTOR_ETF.get(tkr)

            # ── MORNING BURST: 9:35–9:45 AM ─────────────────────────────────
            morning_mask = day_mask & (c5.index >= open_ts) & (c5.index <= burst_end)
            c_m = c5[morning_mask]; v_m = v5[morning_mask]
            h_m = h5[morning_mask]; l_m = l5[morning_mask]

            morning_signal = None
            for i in range(1, len(c_m)):
                bar_ts = c_m.index[i]
                elapsed_mins = (bar_ts - open_ts).total_seconds() / 60.0 + 5
                if elapsed_mins < 4: continue  # skip first bar (9:30)

                c_sf = c_m.iloc[:i+1]; v_sf = v_m.iloc[:i+1]
                h_sf = h_m.iloc[:i+1]; l_sf = l_m.iloc[:i+1]
                cum_v = float(v_sf.sum())
                price = scalar(c_sf.iloc[-1])
                if price < 10.0 or cum_v <= 0: continue

                chg_pct = (price - prev) / prev * 100
                if chg_pct < 3.0: continue

                proj_vol = cum_v * (DAY_MINS / elapsed_mins)
                rvol = proj_vol / avg_vol
                if rvol < 2.0: continue

                tp = (h_sf + l_sf + c_sf) / 3
                vwap = float((tp * v_sf).sum()) / cum_v
                if price < vwap: continue

                # ETF gate states at this bar's time
                og, vg = etf_state(etf_data, etf, bar_ts) if etf else (None, None)
                morning_signal = {
                    "ticker": tkr, "date": dt,
                    "signal_time": bar_ts.strftime("%H:%M"),
                    "chg_pct": round(chg_pct, 2), "rvol": round(rvol, 2),
                    "entry_p": round(price, 2),
                    "exit_p": round(exit_p, 2) if exit_p else None,
                    "same_day": round((exit_p - price) / price * 100, 2) if exit_p else None,
                    "open_etf_pass": og,  # None = no ETF mapped (don't block)
                    "vwap_etf_pass": vg,
                    "spy_green": spy_green,
                }
                break

            if morning_signal and morning_signal["same_day"] is not None:
                morning_all.append(morning_signal)
                og = morning_signal["open_etf_pass"]
                vg = morning_signal["vwap_etf_pass"]
                ogv = "✅" if og in (True, None) else "🚫open"
                vgv = "✅" if vg in (True, None) else "🚫vwap"
                win = "✅" if morning_signal["same_day"] > 0 else "❌"
                print(f"  [BURST {morning_signal['signal_time']}] {tkr:5s} "
                      f"+{morning_signal['chg_pct']:.1f}% RVOL {morning_signal['rvol']:.1f}x "
                      f"open:{ogv} vwap:{vgv} → {morning_signal['same_day']:+.2f}% {win}")

            # ── STEADY GRINDER: 10:30 AM ─────────────────────────────────────
            mask_g = day_mask & (c5.index <= grind_ts)
            if mask_g.sum() < 9: continue

            c = c5[mask_g]; v = v5[mask_g]; h = h5[mask_g]; l = l5[mask_g]

            price_g   = scalar(c.iloc[-1])
            open_p    = scalar(o5[mask_g].iloc[0])
            cum_vol_g = float(v.sum())
            if price_g < 10.0 or cum_vol_g <= 0 or open_p <= 0: continue

            chg_g = (price_g - prev) / prev * 100
            if not (GRINDER_CHG_MIN <= chg_g <= GRINDER_CHG_MAX): continue

            day_frac = 60.0 / DAY_MINS
            rvol_g = (cum_vol_g / day_frac) / avg_vol
            if not (GRINDER_RVOL_MIN <= rvol_g < GRINDER_RVOL_MAX): continue

            if float(v.max()) / cum_vol_g > 0.40: continue

            tp_g = (h + l + c) / 3
            vwap_g = float((tp_g * v).sum()) / cum_vol_g
            if price_g < vwap_g: continue
            if (price_g - vwap_g) / vwap_g * 100 > 3.0: continue

            hod = float(h.max())
            if hod > 0 and (hod - price_g) / hod * 100 > 2.0: continue

            if len(c) < 9: continue
            p45 = scalar(c.iloc[-9])
            t45 = (price_g - p45) / p45 * 100
            if not (T45_MIN <= t45 <= T45_MAX): continue
            if price_g <= p45: continue

            b30 = c.resample("30min").last().dropna()
            if len(b30) >= 2:
                ema9  = float(b30.ewm(span=9,  adjust=False).mean().iloc[-1])
                ema21 = float(b30.ewm(span=21, adjust=False).mean().iloc[-1]) if len(b30) >= 3 else ema9 - 0.01
                if ema9 <= ema21: continue

            exit_g = scalar(c5[exit_mask].iloc[-1]) if exit_mask.sum() > 0 else price_g
            same_g = (exit_g - price_g) / price_g * 100

            # ETF gate states at 10:30 AM
            og, vg = etf_state(etf_data, etf, grind_ts) if etf else (None, None)

            win = "✅" if same_g > 0 else "❌"
            ogv = "✅" if og in (True, None) else "🚫open"
            vgv = "✅" if vg in (True, None) else "🚫vwap"
            print(f"  [GRIND 10:30] {tkr:5s} +{chg_g:.1f}% RVOL {rvol_g:.1f}x t45 {t45:.1f}% "
                  f"open:{ogv} vwap:{vgv} → {same_g:+.2f}% {win}")

            grinder_all.append({
                "ticker": tkr, "date": dt,
                "chg_pct": round(chg_g, 2), "rvol": round(rvol_g, 2),
                "t45": round(t45, 2), "entry_p": round(price_g, 2),
                "exit_p": round(exit_g, 2), "same_day": round(same_g, 2),
                "open_etf_pass": og,   # True/False/None
                "vwap_etf_pass": vg,
                "spy_green": spy_green,
            })

        except Exception:
            continue

    print()


# ── Analysis ─────────────────────────────────────────────────────────────────
def stats(signals):
    valid = [s for s in signals if s.get("same_day") is not None]
    if not valid:
        return 0, 0, 0, 0, 0
    wins   = [s for s in valid if s["same_day"] > 0]
    losses = [s for s in valid if s["same_day"] <= 0]
    wr     = len(wins) / len(valid) * 100
    avg_w  = statistics.mean(s["same_day"] for s in wins)  if wins   else 0
    avg_l  = statistics.mean(s["same_day"] for s in losses) if losses else 0
    ev     = wr/100 * avg_w + (1 - wr/100) * avg_l
    return len(valid), wr, avg_w, avg_l, ev


def gate_filter(signals, use_open, use_vwap):
    out = []
    for s in signals:
        og = s.get("open_etf_pass")
        vg = s.get("vwap_etf_pass")
        # None means no ETF mapped for this ticker — don't block
        if use_open and og is False:
            continue
        if use_vwap and vg is False:
            continue
        out.append(s)
    return out


def print_row(label, signals):
    n, wr, avg_w, avg_l, ev = stats(signals)
    print(f"  {label:<22} n={n:2d}  WR={wr:4.0f}%  "
          f"win={avg_w:+.2f}%  loss={avg_l:+.2f}%  EV={ev:+.3f}%/trade")


print("\n" + "="*74)
print("  RESULTS — STEADY GRINDER (10:30 AM)")
print("="*74)
print_row("No gate",         grinder_all)
print_row("Open gate (live)", gate_filter(grinder_all, use_open=True,  use_vwap=False))
print_row("VWAP gate (new)",  gate_filter(grinder_all, use_open=False, use_vwap=True))
print_row("Both agree",       gate_filter(grinder_all, use_open=True,  use_vwap=True))

# SPY split
spy_green_g = [s for s in grinder_all if s.get("spy_green")]
spy_red_g   = [s for s in grinder_all if s.get("spy_green") is False]
if spy_green_g:
    print(f"\n  ── SPY green days only ──")
    print_row("No gate",          spy_green_g)
    print_row("Open gate",        gate_filter(spy_green_g, True, False))
    print_row("VWAP gate",        gate_filter(spy_green_g, False, True))
if spy_red_g:
    print(f"\n  ── SPY red days only ──")
    print_row("No gate",          spy_red_g)
    print_row("Open gate",        gate_filter(spy_red_g, True, False))
    print_row("VWAP gate",        gate_filter(spy_red_g, False, True))

print("\n" + "="*74)
print("  RESULTS — MORNING BURST (9:35–9:45 AM)")
print("="*74)
print_row("No gate",          morning_all)
print_row("Open gate (live)", gate_filter(morning_all, use_open=True,  use_vwap=False))
print_row("VWAP gate (new)",  gate_filter(morning_all, use_open=False, use_vwap=True))
print_row("Both agree",       gate_filter(morning_all, use_open=True,  use_vwap=True))

spy_green_m = [s for s in morning_all if s.get("spy_green")]
spy_red_m   = [s for s in morning_all if s.get("spy_green") is False]
if spy_green_m:
    print(f"\n  ── SPY green days only ──")
    print_row("No gate",          spy_green_m)
    print_row("Open gate",        gate_filter(spy_green_m, True, False))
    print_row("VWAP gate",        gate_filter(spy_green_m, False, True))

print("\n" + "="*74)
print("  VERDICT")
print("="*74)
# Which gate is better for grinder?
_, wr_none,   _, _, ev_none   = stats(grinder_all)
_, wr_open,   _, _, ev_open   = stats(gate_filter(grinder_all, True,  False))
_, wr_vwap,   _, _, ev_vwap   = stats(gate_filter(grinder_all, False, True))
_, wr_both,   _, _, ev_both   = stats(gate_filter(grinder_all, True,  True))

best_wr  = max(wr_none,  wr_open,  wr_vwap,  wr_both)
best_ev  = max(ev_none, ev_open, ev_vwap, ev_both)
wr_names = {wr_none:"no gate", wr_open:"open gate", wr_vwap:"vwap gate", wr_both:"both agree"}
ev_names = {ev_none:"no gate", ev_open:"open gate", ev_vwap:"vwap gate", ev_both:"both agree"}
print(f"\n  Grinder — best win rate : {wr_names[best_wr]} ({best_wr:.0f}%)")
print(f"  Grinder — best EV/trade : {ev_names[best_ev]} ({best_ev:+.3f}% per trade)")
delta_wr = wr_vwap - wr_open
delta_ev = ev_vwap - ev_open
arrow = "▲" if delta_wr >= 0 else "▼"
print(f"\n  VWAP gate vs open gate:")
print(f"    Win rate delta : {arrow} {abs(delta_wr):.0f}pp  ({wr_open:.0f}% → {wr_vwap:.0f}%)")
print(f"    EV delta       : {'+' if delta_ev >= 0 else ''}{delta_ev:+.3f}% per trade")
if delta_ev > 0.05 or delta_wr > 3:
    print(f"\n  ✅ SWITCH TO VWAP GATE — meaningful improvement")
elif delta_ev < -0.05 or delta_wr < -3:
    print(f"\n  ❌ KEEP OPEN GATE — VWAP gate is worse")
else:
    print(f"\n  ⚠️  NO SIGNIFICANT DIFFERENCE — keep simpler open gate")
print()
