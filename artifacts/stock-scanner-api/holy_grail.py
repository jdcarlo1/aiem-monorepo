"""
Holy Grail Signals — 9 advanced intraday indicators computed from 1-minute data.
Scored and added to the ICS (Institutional Conviction Score) in options_sweep.py.
Signal 5 (Bid/Ask Spread Tightening) is computed in options_sweep.py using live
options chain data. Combined new weight = 73 pts (80 with signal 5).

Signals:
  1. Delta Flow (10 pts)          — 3+ consecutive positive delta minutes
  2. Tape Reading (8 pts)         — 70%+ of last 5 candles closed near ask
  3. VWAP 2nd Std Dev (8 pts)     — price breaks above VWAP + 2σ = explosive move
  4. MFI > 70 (8 pts)             — Money Flow Index, price+volume combined
  6. Price Acceleration (7 pts)   — accelerating price for 3+ consecutive minutes
  7. Consecutive Green (6 pts)    — 3+ green 1-min candles while above VWAP
  8. Pre-Market Volume (8 pts)    — today's pre-market vol 5x+ recent average
  9. VWAP Reclaim (8 pts)         — dipped below VWAP then reclaimed in 3 candles
 10. Minute RVOL (10 pts)         — any single minute 3x+ its session average
"""
import yfinance as yf
import pandas as pd
import numpy as np
import pytz
from datetime import datetime

_ET = pytz.timezone("US/Eastern")


def _fetch_1m(ticker: str) -> pd.DataFrame:
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="1m")
        if df.empty:
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert(_ET)
        else:
            df.index = df.index.tz_convert(_ET)
        return df
    except Exception:
        return pd.DataFrame()


def _fetch_daily(ticker: str, period: str = "30d") -> pd.DataFrame:
    try:
        return yf.Ticker(ticker).history(period=period, interval="1d")
    except Exception:
        return pd.DataFrame()


# ── Signal 1: Delta Flow ───────────────────────────────────────────────────────

def _delta_flow(df: pd.DataFrame) -> tuple[bool, str]:
    """10 pts — 3+ consecutive minutes where buy volume > sell volume."""
    try:
        if len(df) < 5:
            return False, ""
        tail = df.tail(10).copy()
        hi = tail["High"].astype(float)
        lo = tail["Low"].astype(float)
        cl = tail["Close"].astype(float)
        op = tail["Open"].astype(float)
        vol = tail["Volume"].astype(float)
        rng = (hi - lo).replace(0, np.nan)
        delta = ((cl - op) / rng * vol).fillna(0)
        last3 = delta.iloc[-3:]
        if (last3 > 0).all():
            vals = [int(v) for v in last3.values]
            return True, f"⚡ Delta Flow: {vals[2]:+,} / {vals[1]:+,} / {vals[0]:+,} — 3 consecutive buy mins"
        return False, ""
    except Exception:
        return False, ""


# ── Signal 2: Time & Sales Tape Reading ───────────────────────────────────────

def _tape_reading(df: pd.DataFrame) -> tuple[bool, str]:
    """8 pts — 70%+ of last 5 candles closed in the top 30% of their range (ask-side)."""
    try:
        if len(df) < 5:
            return False, ""
        last5 = df.tail(5).copy()
        hi = last5["High"].astype(float)
        lo = last5["Low"].astype(float)
        cl = last5["Close"].astype(float)
        rng = (hi - lo).replace(0, np.nan)
        ask_ratio = ((cl - lo) / rng).fillna(0.5)
        pct = (ask_ratio >= 0.70).sum() / len(ask_ratio)
        if pct >= 0.70:
            return True, f"📋 Tape: {pct*100:.0f}% of last 5 mins closed at ask — buyers in control"
        return False, ""
    except Exception:
        return False, ""


# ── Signal 3: VWAP 2nd Standard Deviation Breakout ───────────────────────────

def _vwap_bands(df: pd.DataFrame, vwap: float, price: float) -> tuple[bool, str]:
    """8 pts — price breaks above VWAP + 2σ = explosive institutional move."""
    try:
        if len(df) < 10 or vwap <= 0:
            return False, ""
        tp = (df["High"].astype(float) + df["Low"].astype(float) + df["Close"].astype(float)) / 3
        deviations = tp - vwap
        std = float(deviations.std())
        if std <= 0:
            return False, ""
        band2 = vwap + 2 * std
        if price > band2:
            return True, f"🚀 VWAP +2σ breakout: ${price:.2f} > ${band2:.2f} — explosive move signal"
        return False, ""
    except Exception:
        return False, ""


# ── Signal 4: Money Flow Index ────────────────────────────────────────────────

def _mfi(df_daily: pd.DataFrame) -> tuple[bool, str]:
    """8 pts — MFI > 70 on daily data (price + volume combined pressure)."""
    try:
        import ta
        if len(df_daily) < 14:
            return False, ""
        hi  = df_daily["High"].astype(float)
        lo  = df_daily["Low"].astype(float)
        cl  = df_daily["Close"].astype(float)
        vol = df_daily["Volume"].astype(float)
        mfi_series = ta.volume.MFIIndicator(hi, lo, cl, vol, window=14).money_flow_index()
        val = float(mfi_series.iloc[-1])
        if val > 70:
            return True, f"💧 MFI {val:.0f} — strong money inflow (>70 = institutional buying)"
        return False, ""
    except Exception:
        return False, ""


# ── Signal 6: Price Acceleration Rate ────────────────────────────────────────

def _price_acceleration(df: pd.DataFrame) -> tuple[bool, str]:
    """7 pts — price accelerating (rate of change increasing) for 3+ consecutive minutes."""
    try:
        if len(df) < 6:
            return False, ""
        closes = df["Close"].astype(float).tail(6).values
        roc = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        if all(roc[i] > roc[i - 1] for i in range(1, len(roc))) and roc[-1] > 0:
            return True, f"📈 Price accelerating {len(roc)} consecutive mins (+{roc[-1]:.2f} last min)"
        return False, ""
    except Exception:
        return False, ""


# ── Signal 7: Consecutive Green Candles ──────────────────────────────────────

def _consecutive_green(df: pd.DataFrame, vwap: float) -> tuple[bool, str]:
    """6 pts — 3+ consecutive green 1-min candles while price stays above VWAP."""
    try:
        if len(df) < 3:
            return False, ""
        last5 = df.tail(5).copy()
        is_green = (last5["Close"].astype(float) > last5["Open"].astype(float))
        above_vwap = last5["Close"].astype(float) > vwap
        qualified = is_green & above_vwap
        count = 0
        for q in reversed(qualified.values):
            if q:
                count += 1
            else:
                break
        if count >= 3:
            return True, f"🟢 {count} consecutive green candles above VWAP — trend confirmation"
        return False, ""
    except Exception:
        return False, ""


# ── Signal 8: Pre-Market Volume Ratio ────────────────────────────────────────

def _premarket_volume(ticker: str) -> tuple[bool, str]:
    """8 pts — today's pre-market volume 5x+ the recent average pre-market volume."""
    try:
        tk = yf.Ticker(ticker)
        df = tk.history(period="5d", interval="1m", prepost=True)
        if df.empty:
            return False, ""
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert(_ET)
        else:
            df.index = df.index.tz_convert(_ET)

        today = datetime.now(_ET).date()

        def is_premarket(idx):
            return idx.date() == today and (
                idx.hour < 9 or (idx.hour == 9 and idx.minute < 30)
            )

        today_vol = float(df[df.index.map(is_premarket)]["Volume"].sum())
        hist_dates = sorted({d for d in df.index.date if d < today})
        hist_vols = []
        for d in hist_dates:
            day_pm = df[
                (df.index.date == d) &
                ((df.index.hour < 9) | ((df.index.hour == 9) & (df.index.minute < 30)))
            ]
            hist_vols.append(float(day_pm["Volume"].sum()))

        avg_vol = float(np.mean(hist_vols)) if hist_vols else 0
        if avg_vol > 1000 and today_vol >= 5 * avg_vol:
            ratio = today_vol / avg_vol
            return True, f"🌅 Pre-market vol {ratio:.1f}x avg ({int(today_vol):,} shares) — informed early buyers"
        return False, ""
    except Exception:
        return False, ""


# ── Signal 9: VWAP Reclaim ────────────────────────────────────────────────────

def _vwap_reclaim(df: pd.DataFrame, vwap: float) -> tuple[bool, str]:
    """8 pts — stock dipped below VWAP then reclaimed it within 3 candles."""
    try:
        if len(df) < 4 or vwap <= 0:
            return False, ""
        closes = df["Close"].astype(float).tail(4).values
        above_now = closes[-1] > vwap
        was_below = any(c < vwap for c in closes[:-1])
        if above_now and was_below:
            return True, f"🔄 VWAP reclaim — dipped below ${vwap:.2f}, back above — high-probability long"
        return False, ""
    except Exception:
        return False, ""


# ── Signal 10: Minute-by-Minute RVOL ─────────────────────────────────────────

def _minute_rvol(df: pd.DataFrame) -> tuple[bool, str]:
    """10 pts — any recent single minute shows 3x+ its session average volume."""
    try:
        if len(df) < 5:
            return False, ""
        session_avg = float(df["Volume"].astype(float).mean())
        if session_avg <= 0:
            return False, ""
        last5 = df.tail(5)
        ratios = last5["Volume"].astype(float) / session_avg
        max_ratio = float(ratios.max())
        max_vol   = int(last5["Volume"].max())
        if max_ratio >= 3.0:
            return True, f"⏱ Minute RVOL spike {max_ratio:.1f}x session avg ({max_vol:,} shares in 1 min)"
        return False, ""
    except Exception:
        return False, ""


# ── Main entry point ──────────────────────────────────────────────────────────

def compute_holy_grail_signals(ticker: str, price: float, vwap: float) -> dict:
    """
    Compute all 9 Holy Grail signals (signal 5 computed in options_sweep.py).
    Returns: { "pts": int, "labels": list[str], "vwap_reclaim": bool }
    Max score from this module = 73 pts.
    """
    df_1m    = _fetch_1m(ticker)
    df_daily = _fetch_daily(ticker)

    pts    = 0
    labels = []

    checks = [
        (_delta_flow,        (df_1m,),                10),
        (_tape_reading,      (df_1m,),                 8),
        (_vwap_bands,        (df_1m, vwap, price),     8),
        (_mfi,               (df_daily,),              8),
        (_price_acceleration,(df_1m,),                 7),
        (_consecutive_green, (df_1m, vwap),            6),
        (_premarket_volume,  (ticker,),                8),
        (_vwap_reclaim,      (df_1m, vwap),            8),
        (_minute_rvol,       (df_1m,),                10),
    ]

    vwap_reclaim_fired = False
    for fn, args, weight in checks:
        try:
            fired, label = fn(*args)
            if fired:
                pts += weight
                labels.append(label)
                if fn == _vwap_reclaim:
                    vwap_reclaim_fired = True
        except Exception:
            pass

    return {
        "pts":          pts,
        "labels":       labels,
        "vwap_reclaim": vwap_reclaim_fired,
    }


# ── VWAP Reclaim standalone scanner (for immediate SMS alerts) ────────────────

def run_vwap_reclaim_scan():
    """
    Runs every 5 minutes during market hours.
    Scans today's SMS-alerted tickers for VWAP reclaim patterns.
    Sends immediate SMS when detected — does not require 80+ ICS score.
    """
    import os
    import psycopg2
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=15, minute=45, second=0, microsecond=0)
    if now_et < market_open or now_et > market_close:
        return

    try:
        con = psycopg2.connect(os.environ["DATABASE_URL"])
        with con.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ticker FROM sms_alerts_log
                WHERE alert_date = (now() AT TIME ZONE 'America/New_York')::date
            """)
            tickers = [r[0] for r in cur.fetchall()]
        con.close()
    except Exception as e:
        print(f"[vwap_reclaim] DB error: {e}")
        return

    if not tickers:
        return

    try:
        from sms_alerts import send_sms
    except Exception as e:
        print(f"[vwap_reclaim] cannot import send_sms: {e}")
        return

    print(f"[vwap_reclaim] scanning {len(tickers)} tickers for VWAP reclaim...")

    for ticker in tickers:
        try:
            df_1m = _fetch_1m(ticker)
            if df_1m.empty:
                continue

            # Compute current VWAP
            df_1m["_tp"] = (df_1m["High"] + df_1m["Low"] + df_1m["Close"]) / 3
            vol_sum = float(df_1m["Volume"].sum())
            if vol_sum <= 0:
                continue
            vwap = float((df_1m["_tp"] * df_1m["Volume"]).sum()) / vol_sum
            price = float(df_1m["Close"].iloc[-1])

            fired, label = _vwap_reclaim(df_1m, vwap)
            if not fired:
                continue

            msg = (
                f"🔄 VWAP RECLAIM ALERT: {ticker}\n"
                f"{label}\n"
                f"Price: ${price:.2f} | VWAP: ${vwap:.2f}\n"
                f"High-probability long setup — previously flagged stock\n"
                f"{now_et.strftime('%I:%M %p ET')}"
            )
            send_sms(msg)
            print(f"[vwap_reclaim] alert sent for {ticker}")
        except Exception as e:
            print(f"[vwap_reclaim] error on {ticker}: {e}")
