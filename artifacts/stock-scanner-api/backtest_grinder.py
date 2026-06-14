"""
Steady Grinder backtest — Jun 9–13, 2026.
For every signal that passes at 11:30 AM, measures how much the stock moved
from entry (11:30 AM close) to end-of-day (3:45 PM close).

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


def last_week_dates():
    today = date(2026, 6, 14)
    days, d = [], today - timedelta(days=7)
    while d < today:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def run_backtest():
    dates = last_week_dates()

    print(f"\n{'='*72}")
    print(f"  STEADY GRINDER BACKTEST — Jun 9–13, 2026")
    print(f"  Entry: 11:30 AM price  |  Exit: 3:45 PM price  |  {len(UNIVERSE)} stocks")
    print(f"{'='*72}\n")

    print("Fetching daily data…")
    daily = yf.download(
        UNIVERSE, start="2026-06-06", end="2026-06-14",
        interval="1d", group_by="ticker", auto_adjust=True, progress=False
    )

    print("Fetching 1-min intraday data for the week…")
    intra = yf.download(
        UNIVERSE, start="2026-06-09", end="2026-06-14",
        interval="1m", group_by="ticker", auto_adjust=True, progress=False
    )

    print("Fetching SPY 1-min for day direction…")
    spy_intra = yf.download(
        "SPY", start="2026-06-09", end="2026-06-14",
        interval="1m", auto_adjust=True, progress=False
    )
    print("Data ready.\n")

    all_results = []
    total_hits = 0

    for dt in dates:
        snap_ts  = pd.Timestamp(f"{dt} 11:30:00").tz_localize("America/New_York")
        open_ts  = pd.Timestamp(f"{dt} 09:30:00").tz_localize("America/New_York")
        eod_ts   = pd.Timestamp(f"{dt} 15:45:00").tz_localize("America/New_York")
        day_frac = 120.0 / 390.0

        # SPY direction
        spy_day = spy_intra[(spy_intra.index >= open_ts) & (spy_intra.index <= snap_ts)]
        if not spy_day.empty:
            spy_open  = float(spy_day["Open"].iloc[0])
            spy_now   = float(spy_day["Close"].iloc[-1])
            spy_green = spy_now > spy_open
            spy_lbl   = "🟢 SPY +" + f"{(spy_now/spy_open-1)*100:.1f}%" if spy_green else "🔴 SPY " + f"{(spy_now/spy_open-1)*100:.1f}%"
        else:
            spy_lbl, spy_green = "❓ SPY ?", None

        print(f"── {dt.strftime('%a %b %d')}  {spy_lbl}")

        hits = []
        for tkr in UNIVERSE:
            try:
                # prev close
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

                # 1-min bars up to 11:30 AM
                try:
                    ic = intra[tkr] if isinstance(intra.columns, pd.MultiIndex) else intra
                except Exception:
                    continue
                snap_bars = ic[(ic.index >= open_ts) & (ic.index <= snap_ts)]
                if len(snap_bars) < 60:
                    continue

                cum_vol = float(snap_bars["Volume"].sum())
                price   = float(snap_bars["Close"].iloc[-1])   # entry price
                open_p  = float(snap_bars["Open"].iloc[0])
                if price <= 0 or open_p <= 0 or price < 10.0:
                    continue

                chg_pct = (price - prev) / prev * 100
                if not (2.0 <= chg_pct <= 8.0):
                    continue

                # avg vol from daily history
                dc_vols = (daily[tkr]["Volume"] if isinstance(daily.columns, pd.MultiIndex)
                           else daily["Volume"][tkr])
                dc_vols_dt = dc_vols.index.tz_localize(None) if dc_vols.index.tzinfo else dc_vols.index
                avg_vol = float(dc_vols[dc_vols_dt < pd.Timestamp(dt)].tail(10).mean()) if len(dc_vols) >= 3 else 0
                if avg_vol < 100_000:
                    continue
                proj_vol = cum_vol / day_frac
                rel_vol  = proj_vol / avg_vol
                if rel_vol < 1.0 or rel_vol >= 3.0:
                    continue

                # No single-bar spike >40%
                if cum_vol > 0 and float(snap_bars["Volume"].max()) / cum_vol > 0.40:
                    continue

                # VWAP
                snap_bars = snap_bars.copy()
                snap_bars["_tp"] = (snap_bars["High"] + snap_bars["Low"] + snap_bars["Close"]) / 3
                tp_vol_sum = float((snap_bars["_tp"] * snap_bars["Volume"]).sum())
                vwap = tp_vol_sum / cum_vol if cum_vol > 0 else price
                if price < vwap:
                    continue
                vwap_ext = (price - vwap) / vwap * 100
                if vwap_ext > 3.0:
                    continue

                # HOD within 2%
                hod = float(snap_bars["High"].max())
                if hod > 0 and (hod - price) / hod * 100 > 2.0:
                    continue

                # 45-min and 90-min trend
                if len(snap_bars) < 45:
                    continue
                p45 = float(snap_bars["Close"].iloc[-45])
                if price <= p45:
                    continue
                if len(snap_bars) >= 90:
                    p90 = float(snap_bars["Close"].iloc[-90])
                    if p45 <= p90:
                        continue
                t45 = (price - p45) / p45 * 100

                # EMA 9 > EMA 21 on 30-min
                b30 = snap_bars["Close"].resample("30min").last().dropna()
                if len(b30) >= 9:
                    ema9  = float(b30.ewm(span=9,  adjust=False).mean().iloc[-1])
                    ema21 = float(b30.ewm(span=21, adjust=False).mean().iloc[-1]) if len(b30) >= 21 else ema9 - 0.01
                    if ema9 <= ema21:
                        continue

                # ── EXIT: 3:45 PM price ──────────────────────────────────────
                eod_bars = ic[(ic.index >= snap_ts) & (ic.index <= eod_ts)]
                if eod_bars.empty:
                    exit_price = price
                else:
                    exit_price = float(eod_bars["Close"].iloc[-1])

                from_entry  = (exit_price - price) / price * 100
                eod_vs_prev = (exit_price - prev) / prev * 100

                hits.append({
                    "ticker":      tkr,
                    "entry":       round(price, 2),
                    "exit":        round(exit_price, 2),
                    "chg_at_entry": round(chg_pct, 1),
                    "move_after":  round(from_entry, 2),
                    "total_day":   round(eod_vs_prev, 1),
                    "rvol":        round(rel_vol, 2),
                    "vwap_ext":    round(vwap_ext, 1),
                    "t45":         round(t45, 2),
                    "spy_green":   spy_green,
                    "date":        str(dt),
                })
            except Exception:
                continue

        if hits:
            hits.sort(key=lambda x: -x["chg_at_entry"])
            for h in hits:
                arrow = "✅" if h["move_after"] > 0 else "❌"
                print(
                    f"  {arrow} {h['ticker']:6s}  entry ${h['entry']:.2f} (+{h['chg_at_entry']:.1f}% vs prev)"
                    f"  →  exit ${h['exit']:.2f}  "
                    f"({'+' if h['move_after']>=0 else ''}{h['move_after']:.2f}% from entry)"
                    f"  day total {'+' if h['total_day']>=0 else ''}{h['total_day']:.1f}%"
                )
            total_hits += len(hits)
            all_results.extend(hits)
        else:
            print("  (no signals)")
        print()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"{'='*72}")
    if all_results:
        df = pd.DataFrame(all_results)
        wins    = df[df["move_after"] > 0]
        losses  = df[df["move_after"] <= 0]
        win_rate = len(wins) / len(df) * 100
        avg_win  = wins["move_after"].mean()   if not wins.empty   else 0
        avg_loss = losses["move_after"].mean() if not losses.empty else 0
        avg_move = df["move_after"].mean()

        print(f"  Signals found:  {len(df)}")
        print(f"  Win rate:       {win_rate:.0f}%  ({len(wins)} wins / {len(losses)} losses)")
        print(f"  Avg move after entry:  {avg_move:+.2f}%")
        print(f"  Avg winner:  {avg_win:+.2f}%   |   Avg loser: {avg_loss:+.2f}%")
        print(f"  Best trade:  {df.loc[df['move_after'].idxmax(), 'ticker']}  {df['move_after'].max():+.2f}%")
        if not losses.empty:
            print(f"  Worst trade: {df.loc[df['move_after'].idxmin(), 'ticker']}  {df['move_after'].min():+.2f}%")
    else:
        print("  No signals found this week.")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    run_backtest()
