"""
Steady Grinder backtest — every trading day of last week (Jun 9–13, 2026).
Uses yf.download() to batch-fetch all tickers in 2 calls per day instead of
one HTTP request per ticker. Much faster.

Run: python artifacts/stock-scanner-api/backtest_grinder.py
"""

import yfinance as yf
import pandas as pd
from datetime import date, timedelta
import warnings
warnings.filterwarnings("ignore")

UNIVERSE = [
    "AAPL","MSFT","NVDA","AMD","AVGO","QCOM","TXN","MU","AMAT","KLAC",
    "LRCX","ON","MRVL","INTC","SMCI","AMKR","ONTO","POWI","SLAB",
    "JPM","GS","MS","BAC","AXP","V","MA","BLK",
    "XOM","CVX","COP","OXY","FRO","SLB","HAL","GE","HON","CAT",
    "JNJ","LLY","UNH","ABBV","MRK","PFE","AMGN","GILD",
    "AMZN","META","GOOGL","NFLX","TSLA","HD","COST","WMT","NKE",
    "ANET","DECK","AXON","CELH","CRWD","DXCM","FTNT","KEYS",
    "LULU","MELI","MPWR","NET","PANW","PAYC","PKG",
]

SCAN_TIME = "11:30"   # simulate 11:30 AM ET each day


def last_week_dates():
    today = date(2026, 6, 14)
    days, d = [], today - timedelta(days=7)
    while d < today:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def spy_direction(dt):
    try:
        d = yf.download("SPY", start=str(dt), end=str(dt + timedelta(days=1)),
                        interval="1d", progress=False, auto_adjust=True)
        if d.empty:
            return None
        return float(d["Close"].iloc[0]) > float(d["Open"].iloc[0])
    except Exception:
        return None


def run_backtest():
    dates = last_week_dates()

    print(f"\n{'='*68}")
    print(f"  STEADY GRINDER BACKTEST  —  Jun 9–13, 2026  (11:30 AM snapshot)")
    print(f"  Universe: {len(UNIVERSE)} stocks")
    print(f"{'='*68}\n")

    # ── Pre-fetch daily closes for the whole period so we have prev-close ──
    print("Fetching daily data for the week…")
    daily = yf.download(
        UNIVERSE, start="2026-06-06", end="2026-06-14",
        interval="1d", group_by="ticker", auto_adjust=True, progress=False
    )

    # ── Pre-fetch 1-min data for the whole week in one call ────────────────
    print("Fetching 1-min intraday data for Mon–Fri…")
    intra = yf.download(
        UNIVERSE, start="2026-06-09", end="2026-06-14",
        interval="1m", group_by="ticker", auto_adjust=True, progress=False
    )
    print("Data loaded. Running grinder logic…\n")

    total_hits = 0

    for dt in dates:
        spy_green = spy_direction(dt)
        spy_lbl   = "🟢 SPY green" if spy_green else ("🔴 SPY red " if spy_green is False else "❓")
        skipped   = "" if spy_green else "  ← would have been SKIPPED by old filter"
        print(f"── {dt.strftime('%A %b %d')}  {spy_lbl}{skipped}")

        snap_ts  = pd.Timestamp(f"{dt} 11:30:00").tz_localize("America/New_York")
        open_ts  = pd.Timestamp(f"{dt} 09:30:00").tz_localize("America/New_York")
        day_frac = 120.0 / 390.0   # 2 hours / 6.5 hours

        hits = []
        for tkr in UNIVERSE:
            try:
                # ── prev close ──────────────────────────────────────────────
                try:
                    dc = daily[tkr]["Close"] if isinstance(daily.columns, pd.MultiIndex) else daily["Close"][tkr]
                except Exception:
                    continue
                dc_dt = dc.index.tz_localize(None) if dc.index.tzinfo else dc.index
                dc_before = dc[dc_dt < pd.Timestamp(dt)]
                if dc_before.empty:
                    continue
                prev = float(dc_before.iloc[-1])
                if prev <= 0:
                    continue

                # ── 1-min bars up to 11:30 AM on this date ──────────────────
                try:
                    ic = intra[tkr] if isinstance(intra.columns, pd.MultiIndex) else intra
                except Exception:
                    continue
                day_bars = ic[(ic.index >= open_ts) & (ic.index <= snap_ts)]
                if len(day_bars) < 60:
                    continue

                cum_vol = float(day_bars["Volume"].sum())
                price   = float(day_bars["Close"].iloc[-1])
                open_p  = float(day_bars["Open"].iloc[0])
                if price <= 0 or open_p <= 0 or price < 10.0:
                    continue

                chg_pct = (price - prev) / prev * 100
                if not (2.0 <= chg_pct <= 8.0):
                    continue

                proj_vol = cum_vol / day_frac
                # Use a loose avg-vol proxy: any stock in our large/mid universe is ≥1M
                # but cap check at 500k to not over-filter — universe is pre-screened
                avg_vol = proj_vol / 1.5   # assume they pass if proj is reasonable
                rel_vol = proj_vol / max(avg_vol, 1)
                # Better: compute rel_vol from daily volume if available
                dc_vols = (daily[tkr]["Volume"] if isinstance(daily.columns, pd.MultiIndex)
                           else daily["Volume"][tkr])
                dc_vols_dt = dc_vols.index.tz_localize(None) if dc_vols.index.tzinfo else dc_vols.index
                avg_from_daily = float(dc_vols[dc_vols_dt < pd.Timestamp(dt)].tail(10).mean()) if len(dc_vols) >= 3 else 0
                if avg_from_daily >= 100_000:
                    proj_vol_real = cum_vol / day_frac
                    rel_vol = proj_vol_real / avg_from_daily
                else:
                    continue  # skip if we can't compute avg vol

                if rel_vol < 1.0 or rel_vol >= 3.0:
                    continue

                # No single-bar spike >40%
                if cum_vol > 0 and float(day_bars["Volume"].max()) / cum_vol > 0.40:
                    continue

                # VWAP
                day_bars = day_bars.copy()
                day_bars["_tp"] = (day_bars["High"] + day_bars["Low"] + day_bars["Close"]) / 3
                tp_vol_sum = float((day_bars["_tp"] * day_bars["Volume"]).sum())
                vwap = tp_vol_sum / cum_vol if cum_vol > 0 else price
                if price < vwap:
                    continue
                vwap_ext = (price - vwap) / vwap * 100
                if vwap_ext > 3.0:
                    continue

                # HOD within 2%
                hod = float(day_bars["High"].max())
                if hod > 0 and (hod - price) / hod * 100 > 2.0:
                    continue

                # 45-min trend
                if len(day_bars) < 45:
                    continue
                p45 = float(day_bars["Close"].iloc[-45])
                if price <= p45:
                    continue
                if len(day_bars) >= 90:
                    p90 = float(day_bars["Close"].iloc[-90])
                    if p45 <= p90:
                        continue
                t45 = (price - p45) / p45 * 100

                # EMA 9 > EMA 21 on 30-min
                b30 = day_bars["Close"].resample("30min").last().dropna()
                if len(b30) >= 9:
                    ema9  = float(b30.ewm(span=9,  adjust=False).mean().iloc[-1])
                    ema21 = float(b30.ewm(span=21, adjust=False).mean().iloc[-1]) if len(b30) >= 21 else ema9 - 0.01
                    if ema9 <= ema21:
                        continue

                gap_pct = (open_p - prev) / prev * 100
                hits.append({
                    "ticker": tkr, "price": round(price, 2), "chg": round(chg_pct, 1),
                    "rvol": round(rel_vol, 2), "vwap_ext": round(vwap_ext, 1),
                    "hod": round(hod, 2), "t45": round(t45, 2), "gap": round(gap_pct, 1),
                })
            except Exception:
                continue

        if hits:
            hits.sort(key=lambda x: -x["chg"])
            for h in hits:
                print(
                    f"  📶 {h['ticker']:6s}  +{h['chg']:.1f}%  "
                    f"RVOL {h['rvol']:.1f}x  ${h['price']:.2f}  "
                    f"VWAP +{h['vwap_ext']:.1f}%  "
                    f"↑{h['t45']:.1f}% last 45m  "
                    f"gap {h['gap']:+.1f}%"
                )
            total_hits += len(hits)
        else:
            print("  (no grinder signals)")
        print()

    print(f"{'='*68}")
    print(f"  Total signals this week: {total_hits}")
    print(f"{'='*68}\n")


if __name__ == "__main__":
    run_backtest()
