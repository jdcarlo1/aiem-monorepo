"""
Steady Grinder full-week backtest: Jun 1–5, 2026.
Uses 5-min bars (yfinance keeps 60 days; 1-min only keeps 7 days).

Entry = 10:30 AM price (bar 12 from open, each bar = 5 min)
Exit  = 3:45 PM price (same day)
Follow-through = next-day close vs signal-day close

Run: python artifacts/stock-scanner-api/backtest_week.py
"""
import yfinance as yf
import pandas as pd
import warnings, statistics
from datetime import date, timedelta
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

ET         = "America/New_York"
WEEK_DATES = [date(2026, 6, d) for d in [1, 2, 3, 4, 5]]
# 5-min bars: 9:30 AM → 10:30 AM = 12 bars, each = 5 min
ENTRY_BARS = 12           # bars from open to 10:30 AM
MINS_45    = 9            # 9 × 5 min = 45 min for trend check
MINS_90    = 18           # 18 × 5 min = 90 min (won't have at 10:30, skip gracefully)
DAY_FRAC   = 60.0 / 390.0  # 60 min elapsed at 10:30 AM


def scalar(v):
    if hasattr(v, "iloc"): v = v.iloc[0]
    return float(v)


def get_col(df, metric, ticker):
    try:
        if isinstance(df.columns, pd.MultiIndex):
            # yfinance group_by="ticker" → columns are (ticker, metric)
            s = df[ticker][metric]
        else:
            s = df[metric]
        return s.dropna()
    except Exception:
        return pd.Series(dtype=float)


print("\n" + "="*70)
print("  STEADY GRINDER BACKTEST  —  Jun 1–5, 2026  (5-min bars)")
print("  Entry 10:30 AM  →  Exit 3:45 PM  +  Next-day follow-through")
print("="*70 + "\n")

print("Fetching daily data (May 16 – Jun 7)…")
daily = yf.download(
    UNIVERSE, start="2026-05-16", end="2026-06-09",
    interval="1d", group_by="ticker", auto_adjust=True, progress=False
)

print("Fetching 5-min intraday data (Jun 1 – Jun 6)…")
intra = yf.download(
    UNIVERSE + ["SPY"], start="2026-06-01", end="2026-06-07",
    interval="5m", group_by="ticker", auto_adjust=True, progress=False
)
print("Data ready. Running grinder logic…\n")

all_results = []

for dt in WEEK_DATES:
    open_ts   = pd.Timestamp(f"{dt} 09:30:00").tz_localize(ET)
    entry_ts  = pd.Timestamp(f"{dt} 10:30:00").tz_localize(ET)
    exit_ts   = pd.Timestamp(f"{dt} 15:45:00").tz_localize(ET)

    next_dt = dt + timedelta(days=3 if dt.weekday() == 4 else 1)

    # SPY direction at 10:30 AM
    try:
        spy_c = get_col(intra, "Close", "SPY")
        spy_d = spy_c[(spy_c.index >= open_ts) & (spy_c.index <= entry_ts)]
        spy_chg = (scalar(spy_d.iloc[-1]) / scalar(spy_d.iloc[0]) - 1) * 100
        spy_lbl = f"🟢 SPY {spy_chg:+.1f}%" if spy_chg > 0 else f"🔴 SPY {spy_chg:+.1f}%"
    except Exception:
        spy_lbl = "❓ SPY ?"

    print(f"── {dt.strftime('%a %b %d')}  {spy_lbl}")

    hits = []
    for tkr in UNIVERSE:
        try:
            # prev close
            dc = get_col(daily, "Close", tkr)
            dc.index = dc.index.tz_localize(None) if dc.index.tzinfo else dc.index
            dc_prev = dc[dc.index < pd.Timestamp(dt)]
            if dc_prev.empty: continue
            prev = scalar(dc_prev.iloc[-1])
            if prev <= 0: continue

            # avg vol (10 days)
            dv = get_col(daily, "Volume", tkr)
            dv.index = dv.index.tz_localize(None) if dv.index.tzinfo else dv.index
            avg_vol = float(dv[dv.index < pd.Timestamp(dt)].tail(10).mean())
            if avg_vol < 500_000: continue

            # next-day close
            dc_next = dc[dc.index >= pd.Timestamp(next_dt)]
            next_close = scalar(dc_next.iloc[0]) if not dc_next.empty else None

            # 5-min bars up to 10:30 AM
            c5 = get_col(intra, "Close",  tkr)
            v5 = get_col(intra, "Volume", tkr)
            h5 = get_col(intra, "High",   tkr)
            l5 = get_col(intra, "Low",    tkr)
            o5 = get_col(intra, "Open",   tkr)

            mask = (c5.index >= open_ts) & (c5.index <= entry_ts)
            if mask.sum() < MINS_45: continue

            c = c5[mask]; v = v5[mask]; h = h5[mask]
            l = l5[mask]; o = o5[mask]

            price   = scalar(c.iloc[-1])
            open_p  = scalar(o.iloc[0])
            cum_vol = float(v.sum())
            if price <= 0 or open_p <= 0 or price < 10.0: continue

            chg_pct = (price - prev) / prev * 100
            if not (2.0 <= chg_pct <= 8.0): continue

            proj_vol = cum_vol / DAY_FRAC
            rel_vol  = proj_vol / avg_vol
            if rel_vol < 1.0 or rel_vol >= 3.0: continue

            # No dominant single bar (>50% threshold — more lenient for 5-min bars)
            if cum_vol > 0 and float(v.max()) / cum_vol > 0.50: continue

            # VWAP
            tp    = (h + l + c) / 3
            vwap  = float((tp * v).sum()) / cum_vol if cum_vol > 0 else price
            if price < vwap: continue
            vwap_ext = (price - vwap) / vwap * 100
            if vwap_ext > 3.0: continue

            # HOD within 2%
            hod = float(h.max())
            if hod > 0 and (hod - price) / hod * 100 > 2.0: continue

            # 45-min trend (9 bars back at 5-min = 45 min)
            if len(c) < MINS_45: continue
            p45 = scalar(c.iloc[-MINS_45])
            if price <= p45: continue
            t45 = (price - p45) / p45 * 100

            # 90-min graceful skip (won't have 18 bars at 10:30 AM)
            if len(c) >= MINS_90:
                p90 = scalar(c.iloc[-MINS_90])
                if p45 <= p90: continue

            # EMA 9 > EMA 21 on 30-min (resample 5-min → 30-min)
            b30 = c.resample("30min").last().dropna()
            if len(b30) >= 9:
                ema9  = float(b30.ewm(span=9,  adjust=False).mean().iloc[-1])
                ema21 = float(b30.ewm(span=21, adjust=False).mean().iloc[-1]) if len(b30) >= 21 else ema9 - 0.01
                if ema9 <= ema21: continue

            # exit 3:45 PM
            exit_mask = (c5.index > entry_ts) & (c5.index <= exit_ts)
            exit_p    = scalar(c5[exit_mask].iloc[-1]) if exit_mask.sum() > 0 else price
            same_day  = (exit_p - price) / price * 100

            # next-day follow-through vs signal-day close
            sig_close_mask = (c5.index.date == dt)
            sig_close  = scalar(c5[sig_close_mask].iloc[-1]) if sig_close_mask.sum() > 0 else exit_p
            next_day   = (next_close - sig_close) / sig_close * 100 if next_close else None

            hits.append({
                "ticker": tkr, "entry": round(price, 2), "exit": round(exit_p, 2),
                "chg_at_entry": round(chg_pct, 1), "same_day": round(same_day, 2),
                "next_day": round(next_day, 2) if next_day is not None else None,
                "rvol": round(rel_vol, 2), "vwap_ext": round(vwap_ext, 1),
                "t45": round(t45, 2), "date": str(dt),
            })
        except Exception:
            continue

    if hits:
        hits.sort(key=lambda x: -x["chg_at_entry"])
        for h in hits:
            sd_icon = "✅" if h["same_day"]  > 0 else "❌"
            if h["next_day"] is None:
                nd_str, nd_icon = "n/a", "—"
            else:
                nd_str  = f"{h['next_day']:+.2f}%"
                nd_icon = "✅" if h["next_day"] > 0 else "❌"
            print(
                f"  {h['ticker']:6s}  +{h['chg_at_entry']:.1f}% at 10:30  RVOL {h['rvol']:.1f}x  "
                f"${h['entry']:.2f}  VWAP+{h['vwap_ext']:.1f}%  ↑{h['t45']:.1f}% last 45m\n"
                f"         {sd_icon} same-day: {h['same_day']:+.2f}%  "
                f"  {nd_icon} next-day: {nd_str}"
            )
        all_results.extend(hits)
    else:
        print("  (no signals)")
    print()

# ── Summary ──────────────────────────────────────────────────────────────────
print("="*70)
if all_results:
    sd = [r["same_day"] for r in all_results]
    nd = [r["next_day"] for r in all_results if r["next_day"] is not None]

    sd_wins = [x for x in sd if x > 0]
    nd_wins = [x for x in nd if x > 0]

    print(f"  Total signals:        {len(all_results)}")
    print(f"  Same-day win rate:    {len(sd_wins)}/{len(sd)} = {len(sd_wins)/len(sd)*100:.0f}%"
          f"  |  avg {statistics.mean(sd):+.2f}%")
    if sd_wins:
        print(f"  Avg winning trade:   {statistics.mean(sd_wins):+.2f}%")
    sd_losses = [x for x in sd if x <= 0]
    if sd_losses:
        print(f"  Avg losing trade:    {statistics.mean(sd_losses):+.2f}%")
    if nd:
        print(f"  Next-day win rate:   {len(nd_wins)}/{len(nd)} = {len(nd_wins)/len(nd)*100:.0f}%"
              f"  |  avg {statistics.mean(nd):+.2f}%")
    best = max(all_results, key=lambda x: x["same_day"])
    print(f"  Best trade:          {best['ticker']} {best['date']}  {best['same_day']:+.2f}%")
else:
    print("  No signals found.")
print("="*70 + "\n")
