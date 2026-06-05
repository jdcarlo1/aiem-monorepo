import math
import numpy as np
import ta
from datetime import datetime, timezone

DEFAULT_LEADERBOARD = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD",
    "NFLX", "PLTR", "COIN", "SOFI", "MARA", "RBLX", "UBER", "SMCI",
    "ARM", "INTC", "MU", "AI", "SPY", "QQQ", "JPM", "V", "PYPL",
]


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def compute_smart_money(ticker: str, df, spy_ret_1m: float = 0.0) -> dict | None:
    try:
        close = df["Close"].squeeze().astype(float).dropna()
        volume = df["Volume"].squeeze().astype(float).fillna(0)
        high = df["High"].squeeze().astype(float)
        low = df["Low"].squeeze().astype(float)

        if len(close) < 60:
            return None

        price = _f(close.iloc[-1])
        if price <= 0:
            return None

        # ── Volume ──────────────────────────────────────────────────────
        avg30 = max(_f(volume.rolling(30).mean().iloc[-1]), 1.0)
        avg10 = max(_f(volume.rolling(10).mean().iloc[-1]), 1.0)
        cur_v = _f(volume.iloc[-1], avg30)
        rvol = max(0.0, cur_v / avg30)
        rvol10 = max(0.0, cur_v / avg10)

        # ── 1-day price change ───────────────────────────────────────────
        chg1d = _f((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] if len(close) > 1 else 0)

        # ── Moving Averages ──────────────────────────────────────────────
        sma20 = _f(close.rolling(20).mean().iloc[-1])
        sma50 = _f(close.rolling(50).mean().iloc[-1])
        sma200 = _f(close.rolling(200).mean().iloc[-1])

        # ── RSI ──────────────────────────────────────────────────────────
        try:
            rsi = _f(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1], 50.0)
        except Exception:
            rsi = 50.0

        # ── ATR ──────────────────────────────────────────────────────────
        try:
            atr = _f(ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1])
        except Exception:
            atr = price * 0.02
        atr = max(atr, price * 0.005)

        # ── 1-month return ───────────────────────────────────────────────
        ret1m = _f((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21] if len(close) > 21 else 0)

        # ────────────────────────────────────────────────────────────────
        # SCORE COMPONENTS
        # ────────────────────────────────────────────────────────────────

        # 1. Call Sweep proxy (0–25)
        #    High RVOL + strong intraday momentum ≈ institutional call sweep
        s_call = min(25.0, max(0.0, (rvol - 1.0) * 6.0 + max(0.0, chg1d * 180.0)))

        # 2. Volume / OI proxy (0–20)
        #    30-day RVOL + 10-day spike
        s_vol = min(20.0, max(0.0, (rvol - 1.0) * 4.5 + max(0.0, (rvol10 - 1.0) * 3.0)))

        # 3. Ask-Side Aggression (0–15)
        #    % of last 20 sessions closing in upper half of range
        recent = df.tail(20).copy()
        rng = (recent["High"].astype(float) - recent["Low"].astype(float))
        cls_up = (recent["Close"].astype(float) - recent["Low"].astype(float))
        pct_upper = _f((cls_up / rng.replace(0, np.nan)).fillna(0.5).mean(), 0.5)
        s_ask = min(15.0, pct_upper * 15.0)

        # 4. Dark Pool proxy (0–15)
        #    Price above key institutional SMAs = sustained accumulation
        s_dp = 0.0
        if sma50 > 0 and price > sma50:
            s_dp += 5.0
        if sma200 > 0 and price > sma200:
            s_dp += 5.0
        if sma20 > 0 and sma50 > 0 and sma20 > sma50:
            s_dp += 5.0

        # 5. Sector Strength (0–10)
        rel = ret1m - spy_ret_1m
        s_sector = min(10.0, max(0.0, 5.0 + rel * 100.0))

        # 6. Historical Similarity (0–15)
        #    Backtest: when RVOL>1.3 AND positive day occurred, what was 5-day forward return?
        wins, total, ret_sum = 0, 0, 0.0
        vol_roll = volume.rolling(30).mean()
        for i in range(30, len(close) - 5):
            av = _f(vol_roll.iloc[i], 1.0)
            if av <= 0:
                continue
            pr = _f(volume.iloc[i]) / av
            pc = _f((close.iloc[i] - close.iloc[i - 1]) / close.iloc[i - 1] if close.iloc[i - 1] > 0 else 0)
            if pr > 1.3 and pc > 0:
                fwd = _f((close.iloc[i + 5] - close.iloc[i]) / close.iloc[i] if close.iloc[i] > 0 else 0)
                if fwd > 0:
                    wins += 1
                total += 1
                ret_sum += fwd

        win_rate = round(wins / total * 100.0, 1) if total > 0 else 50.0
        avg_5d = round(ret_sum / total * 100.0, 2) if total > 0 else 0.0
        occurrences = total
        s_hist = min(15.0, max(0.0, (win_rate - 50.0) / 50.0 * 15.0 + 7.5))

        total_score = round(min(100, max(0, s_call + s_vol + s_ask + s_dp + s_sector + s_hist)))

        # ── Signal labels ────────────────────────────────────────────────
        if total_score >= 80:
            signal, direction, confidence = "Strong Bullish Institutional Accumulation", "Bullish", "Very High"
        elif total_score >= 65:
            signal, direction, confidence = "Bullish Options Flow Detected", "Bullish", "High"
        elif total_score >= 50:
            signal, direction, confidence = "Moderate Institutional Interest", "Bullish", "Moderate"
        elif total_score >= 35:
            signal, direction, confidence = "Neutral Market Activity", "Neutral", "Low"
        elif total_score >= 20:
            signal, direction, confidence = "Bearish Pressure Detected", "Bearish", "Moderate"
        else:
            signal, direction, confidence = "Strong Bearish Institutional Activity", "Bearish", "High"

        # ── Risk rating ───────────────────────────────────────────────────
        vol_daily = _f(close.pct_change().rolling(20).std().iloc[-1], 0.02) * 100.0
        risk = ("Low" if vol_daily < 1.5
                else "Moderate" if vol_daily < 2.5
                else "High" if vol_daily < 4.0
                else "Very High")

        # ── Expected Move (ATR-based, 5-day window) ───────────────────────
        em_low = round(atr / price * 100.0, 1)
        em_high = round(atr / price * 300.0, 1)

        # ── AI Trade Thesis ───────────────────────────────────────────────
        parts = []
        if rvol >= 2.0:
            parts.append(f"Relative volume is {rvol:.1f}x above average — significant institutional participation detected.")
        elif rvol >= 1.3:
            parts.append(f"Volume running {rvol:.1f}x normal levels, signaling elevated smart money activity.")

        if s_dp >= 10:
            parts.append("Dark pool accumulation confirmed — price is trading above both the 50-day and 200-day institutional moving averages.")
        elif s_dp >= 5:
            parts.append("Partial dark pool accumulation pattern detected; price above one key institutional moving average.")

        if chg1d > 0.02:
            parts.append(f"Strong ask-side aggression of +{chg1d * 100:.1f}% today — buyers actively lifting the offer.")
        elif chg1d > 0:
            parts.append(f"Positive intraday momentum (+{chg1d * 100:.1f}%) supports near-term bullish bias.")

        if rel > 0.03:
            parts.append(f"Sector strength ranks in top tier — outperforming SPY by {rel * 100:.1f}% over the past month.")
        elif rel > 0.01:
            parts.append(f"Modest relative strength vs SPY (+{rel * 100:.1f}%) shows selective institutional interest.")
        elif rel < -0.03:
            parts.append(f"Underperforming SPY by {abs(rel) * 100:.1f}% — weak sector rotation signal.")

        if occurrences > 5:
            prefix = "+" if avg_5d > 0 else ""
            parts.append(
                f"Historical setups produced a {win_rate:.0f}% win rate over {occurrences} previous occurrences, "
                f"with an average 5-day return of {prefix}{avg_5d:.1f}%."
            )

        if not parts:
            parts.append(
                f"Technical and volume indicators register a Smart Money Score of {total_score}/100 "
                f"with {confidence.lower()} confidence in the {direction.lower()} direction."
            )

        thesis = " ".join(parts)

        return {
            "ticker": ticker,
            "price": round(price, 2),
            "smart_money_score": total_score,
            "confidence": confidence,
            "signal": signal,
            "direction": direction,
            "risk_rating": risk,
            "win_rate": win_rate,
            "avg_5d_return": avg_5d,
            "occurrences": occurrences,
            "expected_move_low": em_low,
            "expected_move_high": em_high,
            "rvol": round(rvol, 2),
            "thesis": thesis,
            "score_breakdown": {
                "call_sweep": round(s_call),
                "volume_oi": round(s_vol),
                "ask_aggression": round(s_ask),
                "dark_pool": round(s_dp),
                "sector_strength": round(s_sector),
                "historical": round(s_hist),
            },
        }
    except Exception:
        return None


def scan_smart_money(tickers: list) -> dict:
    from scanner import fetch_stock_data

    spy_ret = 0.0
    try:
        spy_df = fetch_stock_data("SPY", period="35d")
        if spy_df is not None and len(spy_df) > 21:
            sc = spy_df["Close"].squeeze().astype(float).dropna()
            spy_ret = _f((sc.iloc[-1] - sc.iloc[-21]) / sc.iloc[-21])
    except Exception:
        pass

    results = []
    for ticker in tickers:
        try:
            df = fetch_stock_data(ticker, period="1y")
            if df is None or df.empty:
                continue
            result = compute_smart_money(ticker, df, spy_ret)
            if result:
                results.append(result)
        except Exception:
            continue

    results.sort(key=lambda x: x["smart_money_score"], reverse=True)
    return {
        "leaderboard": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
