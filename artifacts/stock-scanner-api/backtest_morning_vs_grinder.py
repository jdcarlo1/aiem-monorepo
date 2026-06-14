"""
Side-by-side backtest: Morning Scanner vs Steady Grinder — Jun 9-13, 2026
─────────────────────────────────────────────────────────────────────────
Morning scanner: scans every 5-min bar from 9:30-10:30 AM
  • EARLY signal: first qualifying bar between 9:30-10:00 AM (noisy open)
  • LATE  signal: first qualifying bar between 10:00-10:30 AM (noise filtered)
Morning criteria: RVOL ≥ 2.0x (projected), price ≥ 3% above prev close,
                  above VWAP, price ≥ $10, avg vol ≥ 500k

Grinder: entry at 10:30 AM exact (same criteria as live scanner)
  RVOL 1.3-3.0x, chg 2-8%, t45 0.5-2.0%, above VWAP, EMA 9>21, no spike bar

All exits at 3:45 PM same day.
Entry = signal-bar close price.
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
WEEK_DATES = [date(2026, 6, d) for d in [9, 10, 11, 12, 13]]

MORNING_RVOL_MIN = 2.0   # projected RVOL threshold for morning scanner
MORNING_CHG_MIN  = 3.0   # % above prev close
GRINDER_RVOL_MIN = 1.3
GRINDER_RVOL_MAX = 3.0
GRINDER_CHG_MIN  = 2.0
GRINDER_CHG_MAX  = 8.0
T45_MIN = 0.5
T45_MAX = 2.0


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


print("\n" + "="*74)
print("  MORNING SCANNER vs STEADY GRINDER  —  Jun 9–13, 2026  (5-min bars)")
print("  Entry = signal bar close  |  Exit = 3:45 PM close")
print("="*74)

print("\nFetching daily data (May 26 – Jun 14)…")
daily = yf.download(
    UNIVERSE, start="2026-05-26", end="2026-06-14",
    interval="1d", group_by="ticker", auto_adjust=True, progress=False
)

print("Fetching 5-min intraday data (Jun 9 – Jun 14)…")
intra = yf.download(
    UNIVERSE + ["SPY"], start="2026-06-09", end="2026-06-14",
    interval="5m", group_by="ticker", auto_adjust=True, progress=False
)
print("Data ready.\n")

# ── Buckets ────────────────────────────────────────────────────────────────
early_signals  = []   # morning 9:30-10:00 AM
late_signals   = []   # morning 10:00-10:30 AM
grinder_signals = []  # grinder at 10:30 AM

DAY_MINS = 390.0

for dt in WEEK_DATES:
    open_ts    = pd.Timestamp(f"{dt} 09:30:00").tz_localize(ET)
    cutoff_ts  = pd.Timestamp(f"{dt} 10:00:00").tz_localize(ET)  # early/late split
    entry_ts   = pd.Timestamp(f"{dt} 10:30:00").tz_localize(ET)
    exit_ts    = pd.Timestamp(f"{dt} 15:45:00").tz_localize(ET)

    # SPY at 10:30 AM
    try:
        spy_c = get_col(intra, "Close", "SPY")
        spy_d = spy_c[(spy_c.index >= open_ts) & (spy_c.index <= entry_ts)]
        spy_chg = (scalar(spy_d.iloc[-1]) / scalar(spy_d.iloc[0]) - 1) * 100
        spy_lbl = f"🟢 SPY {spy_chg:+.1f}%" if spy_chg > 0 else f"🔴 SPY {spy_chg:+.1f}%"
    except Exception:
        spy_lbl = "❓ SPY ?"

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

            # All 5-min bars for the day
            c5 = get_col(intra, "Close",  tkr)
            v5 = get_col(intra, "Volume", tkr)
            h5 = get_col(intra, "High",   tkr)
            l5 = get_col(intra, "Low",    tkr)
            o5 = get_col(intra, "Open",   tkr)

            day_mask = (c5.index >= open_ts) & (c5.index.date == dt)
            c_day = c5[day_mask]; v_day = v5[day_mask]
            h_day = h5[day_mask]; l_day = l5[day_mask]
            if len(c_day) < 6: continue

            # exit price (3:45 PM)
            exit_mask = (c5.index.date == dt) & (c5.index <= exit_ts)
            exit_p = scalar(c5[exit_mask].iloc[-1]) if exit_mask.sum() > 0 else None

            # ── MORNING SCANNER — scan each 5-min bar up to 10:30 AM ──────
            morning_signal = None
            for i in range(1, len(c_day)):
                bar_ts = c_day.index[i]
                if bar_ts > entry_ts:
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
                if chg_pct < MORNING_CHG_MIN: continue

                proj_vol  = cum_vol * (DAY_MINS / elapsed_mins)
                rvol      = proj_vol / avg_vol
                if rvol < MORNING_RVOL_MIN: continue

                # VWAP from open
                tp   = (h_so_far + l_so_far + c_so_far) / 3
                vwap = float((tp * v_so_far).sum()) / cum_vol
                if price < vwap: continue

                # Signal found — record it
                bucket = "early" if bar_ts <= cutoff_ts else "late"
                morning_signal = {
                    "ticker": tkr, "date": dt, "bucket": bucket,
                    "signal_time": bar_ts.strftime("%H:%M"),
                    "chg_pct": round(chg_pct, 2),
                    "rvol": round(rvol, 2),
                    "entry_p": round(price, 2),
                    "exit_p":  round(exit_p, 2) if exit_p else None,
                    "same_day": round((exit_p - price) / price * 100, 2) if exit_p else None,
                }
                break

            if morning_signal:
                if morning_signal["bucket"] == "early":
                    early_signals.append(morning_signal)
                    win = "✅" if (morning_signal["same_day"] or -99) > 0 else "❌"
                    print(f"  [EARLY {morning_signal['signal_time']}] {tkr:6s}  "
                          f"+{morning_signal['chg_pct']:.1f}%  RVOL {morning_signal['rvol']:.1f}x  "
                          f"→ {morning_signal['same_day']:+.2f}% {win}")
                else:
                    late_signals.append(morning_signal)
                    win = "✅" if (morning_signal["same_day"] or -99) > 0 else "❌"
                    print(f"  [LATE  {morning_signal['signal_time']}] {tkr:6s}  "
                          f"+{morning_signal['chg_pct']:.1f}%  RVOL {morning_signal['rvol']:.1f}x  "
                          f"→ {morning_signal['same_day']:+.2f}% {win}")

            # ── STEADY GRINDER — entry exactly at 10:30 AM ────────────────
            mask_g = (c5.index >= open_ts) & (c5.index <= entry_ts)
            if mask_g.sum() < 9: continue

            c = c5[mask_g]; v = v5[mask_g]; h = h5[mask_g]; l = l5[mask_g]

            price_g  = scalar(c.iloc[-1])
            open_p   = scalar(o5[mask_g].iloc[0])
            cum_vol_g = float(v.sum())
            if price_g < 10.0 or cum_vol_g <= 0 or open_p <= 0: continue

            chg_g    = (price_g - prev) / prev * 100
            if not (GRINDER_CHG_MIN <= chg_g <= GRINDER_CHG_MAX): continue

            day_frac = 60.0 / DAY_MINS
            proj_g   = cum_vol_g / day_frac
            rvol_g   = proj_g / avg_vol
            if not (GRINDER_RVOL_MIN <= rvol_g < GRINDER_RVOL_MAX): continue

            # No dominant single bar
            if float(v.max()) / cum_vol_g > 0.40: continue

            # VWAP
            tp_g   = (h + l + c) / 3
            vwap_g = float((tp_g * v).sum()) / cum_vol_g
            if price_g < vwap_g: continue
            if (price_g - vwap_g) / vwap_g * 100 > 3.0: continue

            # HOD within 2%
            hod = float(h.max())
            if hod > 0 and (hod - price_g) / hod * 100 > 2.0: continue

            # t45 (9 bars × 5 min = 45 min)
            if len(c) < 9: continue
            p45 = scalar(c.iloc[-9])
            t45 = (price_g - p45) / p45 * 100
            if not (T45_MIN <= t45 <= T45_MAX): continue
            if price_g <= p45: continue

            # EMA 9 > EMA 21 on 30-min bars
            b30 = c.resample("30min").last().dropna()
            if len(b30) >= 2:
                ema9  = float(b30.ewm(span=9,  adjust=False).mean().iloc[-1])
                ema21 = float(b30.ewm(span=21, adjust=False).mean().iloc[-1]) if len(b30) >= 3 else ema9 - 0.01
                if ema9 <= ema21: continue

            # exit 3:45 PM
            exit_g = scalar(c5[exit_mask].iloc[-1]) if exit_mask.sum() > 0 else price_g
            same_g = (exit_g - price_g) / price_g * 100

            win = "✅" if same_g > 0 else "❌"
            print(f"  [GRIND 10:30]  {tkr:6s}  +{chg_g:.1f}%  RVOL {rvol_g:.1f}x  "
                  f"t45 {t45:.1f}%  → {same_g:+.2f}% {win}")

            grinder_signals.append({
                "ticker": tkr, "date": dt,
                "chg_pct": round(chg_g, 2), "rvol": round(rvol_g, 2),
                "t45": round(t45, 2), "entry_p": round(price_g, 2),
                "exit_p": round(exit_g, 2), "same_day": round(same_g, 2),
            })

        except Exception:
            continue

    print()


# ── Summary ────────────────────────────────────────────────────────────────
def summarize(label, signals):
    if not signals:
        print(f"  {label}: no signals")
        return
    valid = [s for s in signals if s["same_day"] is not None]
    wins  = [s for s in valid if s["same_day"] > 0]
    avgs  = statistics.mean(s["same_day"] for s in valid) if valid else 0
    avg_w = statistics.mean(s["same_day"] for s in wins)  if wins  else 0
    avg_l = statistics.mean(s["same_day"] for s in valid if s["same_day"] <= 0) \
            if len(valid) > len(wins) else 0
    wr    = len(wins) / len(valid) * 100 if valid else 0
    print(f"  {label}")
    print(f"    Signals : {len(valid)}")
    print(f"    Win rate: {wr:.0f}%  ({len(wins)} wins / {len(valid)-len(wins)} losses)")
    print(f"    Avg move: {avgs:+.2f}%   Avg win: {avg_w:+.2f}%   Avg loss: {avg_l:+.2f}%")
    print()

print("=" * 74)
print("  SUMMARY  —  Jun 9–13, 2026")
print("=" * 74 + "\n")
summarize("🔴 EARLY MORNING  (9:30–10:00 AM  ·  RVOL≥2x, chg≥3%)", early_signals)
summarize("🟡 LATE  MORNING  (10:00–10:30 AM ·  RVOL≥2x, chg≥3%)", late_signals)
summarize("🟢 STEADY GRINDER (10:30 AM entry ·  RVOL 1.3-3x, chg 2-8%)", grinder_signals)

print("-" * 74)
all_morning = early_signals + late_signals
if all_morning and grinder_signals:
    print("\n  HEAD-TO-HEAD COMPARISON:")
    print(f"  Early morning  vs  Grinder: "
          f"{sum(1 for s in early_signals if (s['same_day'] or -99)>0)/max(len(early_signals),1)*100:.0f}%"
          f"  vs  "
          f"{sum(1 for s in grinder_signals if s['same_day']>0)/len(grinder_signals)*100:.0f}%")
    print(f"  Late morning   vs  Grinder: "
          f"{sum(1 for s in late_signals if (s['same_day'] or -99)>0)/max(len(late_signals),1)*100:.0f}%"
          f"  vs  "
          f"{sum(1 for s in grinder_signals if s['same_day']>0)/len(grinder_signals)*100:.0f}%")
    print(f"\n  Key question — does waiting from 9:30→10:00 improve win rate?")
    early_wr = sum(1 for s in early_signals if (s['same_day'] or -99)>0)/max(len(early_signals),1)*100
    late_wr  = sum(1 for s in late_signals  if (s['same_day'] or -99)>0)/max(len(late_signals),1)*100
    diff = late_wr - early_wr
    arrow = "▲" if diff > 0 else "▼"
    print(f"  {arrow} {abs(diff):.0f}pp {'improvement' if diff>0 else 'decline'} "
          f"by waiting until 10:00 AM  ({early_wr:.0f}% → {late_wr:.0f}%)")
