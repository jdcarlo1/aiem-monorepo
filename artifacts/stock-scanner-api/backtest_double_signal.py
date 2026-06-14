"""
Backtest double-signal for a specific date.
Pulls Barchart universe, checks:
  1. Morning burst criteria (RVOL ≥ 2x in first hour, price change ≥ 3%)
  2. Swing scanner score ≥ 60 + closed in top 60% of range

Usage: python backtest_double_signal.py
"""

import yfinance as yf
import requests
import warnings
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore")

TARGET_DATE = date(2026, 6, 13)  # Friday

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json",
    "Referer":    "https://www.barchart.com/stocks/advances",
}


def get_universe(min_pct=2.0):
    tiers = (
        "stocks.advances.microcap.us",
        "stocks.advances.smallcap.us",
        "stocks.advances.midcap.us",
        "stocks.advances.largecap.us",
    )
    syms = []
    for tier in tiers:
        try:
            url = (
                "https://www.barchart.com/proxies/core-api/v1/quotes/get"
                f"?fields=symbol%2CpercentChange%2Cvolume%2CaverageVolume&"
                f"list={tier}&orderBy=percentChange&orderDir=desc&raw=1&limit=100"
            )
            r = requests.get(url, headers=HEADERS, timeout=8)
            if r.ok:
                for row in r.json().get("data", []):
                    sym = (row.get("symbol") or "").strip().upper()
                    pct = float(row.get("percentChange") or 0)
                    if sym and len(sym) <= 5 and "." not in sym and pct >= min_pct:
                        syms.append(sym)
        except Exception as e:
            print(f"  [barchart] tier {tier} error: {e}")
    return list(dict.fromkeys(syms))


def check_double_signal(ticker):
    try:
        tk = yf.Ticker(ticker)

        # ── Daily data (last 30 days) ──────────────────────────────────────────
        daily = tk.history(period="30d", interval="1d")
        if len(daily) < 4:
            return None

        # Find the row for TARGET_DATE
        daily.index = daily.index.tz_convert("US/Eastern").normalize()
        target = daily[daily.index.date == TARGET_DATE]
        if target.empty:
            return None

        t = target.iloc[0]
        prior_rows = daily[daily.index.date < TARGET_DATE].tail(3)
        if len(prior_rows) < 3:
            return None

        prev_close = prior_rows.iloc[-1]["Close"]
        today_chg = round((t["Close"] - prev_close) / prev_close * 100, 2)
        if today_chg < 2.0:
            return None

        # Close position in day's range
        day_range = t["High"] - t["Low"]
        if day_range == 0:
            return None
        close_pos = (t["Close"] - t["Low"]) / day_range

        # RVOL (today vs 20d avg)
        avg_vol = daily["Volume"].iloc[-22:-1].mean()
        rvol = round(t["Volume"] / avg_vol, 2) if avg_vol > 0 else 0

        # 3-day momentum
        momentum_3d = round(
            (t["Close"] - prior_rows.iloc[0]["Close"]) / prior_rows.iloc[0]["Close"] * 100, 2
        )

        # 20d MA
        ma20 = daily["Close"].iloc[-22:-1].mean()
        above_ma = t["Close"] > ma20

        # ── Swing score ────────────────────────────────────────────────────────
        swing_score = 0

        # Close position (25 pts)
        if close_pos >= 0.90:   swing_score += 25
        elif close_pos >= 0.75: swing_score += 18
        elif close_pos >= 0.60: swing_score += 10

        # RVOL (25 pts)
        if rvol >= 5:    swing_score += 25
        elif rvol >= 3:  swing_score += 20
        elif rvol >= 2:  swing_score += 14
        elif rvol >= 1.5: swing_score += 7

        # 3d momentum (20 pts)
        if momentum_3d >= 15:   swing_score += 20
        elif momentum_3d >= 10: swing_score += 15
        elif momentum_3d >= 5:  swing_score += 10
        elif momentum_3d >= 3:  swing_score += 5

        # Pullback quality (15 pts) — how many of last 3 days were green
        green_days = sum(1 for i in range(len(prior_rows))
                         if i == 0 or prior_rows.iloc[i]["Close"] >= prior_rows.iloc[i-1]["Close"])
        swing_score += green_days * 5

        # Above 20d MA (5 pts)
        if above_ma:
            swing_score += 5

        # ── Morning burst simulation ───────────────────────────────────────────
        # Use 5-min intraday for TARGET_DATE
        intraday = tk.history(
            start=TARGET_DATE.strftime("%Y-%m-%d"),
            end="2026-06-14",
            interval="5m"
        )

        morning_fired = False
        if not intraday.empty:
            intraday.index = intraday.index.tz_convert("US/Eastern")
            # First-hour bars: 9:30 AM – 10:30 AM
            first_hour = intraday.between_time("09:30", "10:30")
            if not first_hour.empty:
                open_price = intraday.iloc[0]["Open"]
                first_hour_high = first_hour["High"].max()
                first_hour_vol = first_hour["Volume"].sum()
                avg_5min_vol = avg_vol / 78  # ~78 5-min bars in full day
                first_hour_rvol = first_hour_vol / (avg_5min_vol * len(first_hour)) if avg_5min_vol > 0 else 0
                first_hour_chg = (first_hour_high - open_price) / open_price * 100 if open_price > 0 else 0
                morning_fired = first_hour_rvol >= 2.0 and first_hour_chg >= 3.0

        # ── Must-have gates ────────────────────────────────────────────────────
        if close_pos < 0.60:
            return None
        if momentum_3d < 3.0:
            return None

        return {
            "ticker":        ticker,
            "price":         round(t["Close"], 2),
            "today_chg":     today_chg,
            "close_pos":     round(close_pos * 100, 1),
            "rvol":          rvol,
            "momentum_3d":   momentum_3d,
            "swing_score":   swing_score,
            "above_ma":      above_ma,
            "morning_fired": morning_fired,
            "double_signal": morning_fired and swing_score >= 60,
        }

    except Exception as e:
        return None


def run():
    print(f"\n=== Double Signal Backtest — {TARGET_DATE} ===\n")
    print("Fetching Barchart universe…")
    universe = get_universe(min_pct=2.0)
    print(f"Universe: {len(universe)} tickers\n")

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(check_double_signal, t): t for t in universe}
        for fut in as_completed(futs):
            res = fut.result()
            if res:
                results.append(res)

    results.sort(key=lambda x: (x["double_signal"], x["swing_score"]), reverse=True)

    doubles = [r for r in results if r["double_signal"]]
    swing_only = [r for r in results if not r["double_signal"] and r["swing_score"] >= 60]

    print(f"{'='*60}")
    print(f"DOUBLE SIGNALS ❤️ ({len(doubles)} found):")
    print(f"{'='*60}")
    for r in doubles:
        print(f"  {r['ticker']:6s} ${r['price']:>7.2f}  +{r['today_chg']:>5.1f}%  "
              f"Score:{r['swing_score']:>3}  RVOL:{r['rvol']:>4.1f}x  "
              f"ClosePos:{r['close_pos']:>4.1f}%  3d:{r['momentum_3d']:>+5.1f}%")

    print(f"\n{'='*60}")
    print(f"SWING ONLY (score ≥ 60, no morning burst) ({len(swing_only)} found):")
    print(f"{'='*60}")
    for r in swing_only[:15]:
        print(f"  {r['ticker']:6s} ${r['price']:>7.2f}  +{r['today_chg']:>5.1f}%  "
              f"Score:{r['swing_score']:>3}  RVOL:{r['rvol']:>4.1f}x  "
              f"ClosePos:{r['close_pos']:>4.1f}%  3d:{r['momentum_3d']:>+5.1f}%")

    print(f"\nTotal qualifying swing setups: {len(results)}")
    print(f"Double signals: {len(doubles)}")


if __name__ == "__main__":
    run()
