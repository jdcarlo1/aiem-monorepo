"""
aiem_exit_engine.py
====================================================================
Indicator-based hold/exit judgment for open AIEM positions.
Every open ai_stock_picks row is re-evaluated every 30 minutes
during market hours. AIEM decides hold vs exit based on RSI, MACD,
SMA20, RVOL, and lower-lows structure — no countdown timer.
====================================================================
"""

import datetime as dt
from typing import Callable, Dict, Any

import psycopg2
import psycopg2.extras

try:
    import ta
except ImportError:
    ta = None

import decision_logger as dl


# ── Indicator computation ──────────────────────────────────────────────

def _compute_indicators(df) -> Dict[str, Any]:
    """Given a daily OHLCV DataFrame (Open/High/Low/Close/Volume,
    oldest -> newest), return the indicator readings AIEM judges a
    position by. Returns {} if there isn't enough history yet —
    never raises."""
    if df is None or df.empty or len(df) < 20 or ta is None:
        return {}

    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float)
    out: Dict[str, Any] = {}

    try:
        out["rsi_14"] = float(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1])
    except Exception:
        out["rsi_14"] = None

    try:
        macd = ta.trend.MACD(close)
        hist = float(macd.macd_diff().iloc[-1])
        out["macd_hist"] = hist
        out["macd_bullish"] = hist > 0
    except Exception:
        out["macd_hist"] = None
        out["macd_bullish"] = None

    try:
        sma20_now = close.rolling(20).mean().iloc[-1]
        sma20_prior = close.rolling(20).mean().iloc[-3]
        out["above_sma20"] = bool(close.iloc[-1] > sma20_now)
        out["sma20_rising"] = bool(sma20_now > sma20_prior)
    except Exception:
        out["above_sma20"] = None
        out["sma20_rising"] = None

    try:
        avg_vol_20 = vol.rolling(20).mean().iloc[-1]
        out["rvol"] = float(vol.iloc[-1] / avg_vol_20) if avg_vol_20 else None
    except Exception:
        out["rvol"] = None

    try:
        recent_lows = df["Low"].iloc[-6:]
        out["lower_lows_count"] = int(sum(
            1 for i in range(1, len(recent_lows))
            if recent_lows.iloc[i] < recent_lows.iloc[i - 1]
        ))
    except Exception:
        out["lower_lows_count"] = None

    out["last_close"] = float(close.iloc[-1])
    return out


def _score_exit(indicators: Dict[str, Any], unrealized_pnl_pct: float,
                 target_pct: float, stop_pct: float) -> Dict[str, str]:
    """The actual judgment call. Price-based stop/target stay as hard
    safety rails (not a timer); everything else is indicator-based
    reasoning, and every reason is returned in plain text so it can be
    logged and reviewed later."""
    if unrealized_pnl_pct <= -abs(stop_pct):
        return {"action": "exit", "reasoning":
                f"Stop hit: unrealized P&L {unrealized_pnl_pct:.2f}% <= -{stop_pct:.2f}% stop."}

    if unrealized_pnl_pct >= abs(target_pct) * 1.5:
        if indicators.get("macd_bullish") is False and indicators.get("above_sma20") is False:
            return {"action": "exit", "reasoning":
                    f"Well past target ({unrealized_pnl_pct:.2f}%) and trend is rolling over "
                    f"(MACD bearish, price below SMA20) — locking in the gain."}

    if not indicators:
        return {"action": "hold", "reasoning":
                "Not enough price history yet to judge indicators; holding by default."}

    reasons_exit, reasons_hold = [], []

    rsi = indicators.get("rsi_14")
    if rsi is not None and rsi >= 75:
        reasons_exit.append(f"RSI {rsi:.1f} extended/overbought")
    elif rsi is not None and rsi <= 35 and unrealized_pnl_pct < 0:
        reasons_exit.append(f"RSI {rsi:.1f} weak while position is underwater")
    elif rsi is not None:
        reasons_hold.append(f"RSI {rsi:.1f} not extreme")

    if indicators.get("macd_bullish") is False:
        reasons_exit.append("MACD histogram turned negative (momentum fading)")
    elif indicators.get("macd_bullish") is True:
        reasons_hold.append("MACD still bullish")

    if indicators.get("above_sma20") is False:
        reasons_exit.append("price below SMA20")
    elif indicators.get("above_sma20") is True:
        reasons_hold.append("price holding above SMA20")

    if (indicators.get("lower_lows_count") or 0) >= 4:
        reasons_exit.append("4+ consecutive lower lows — reversal structure forming")

    if len(reasons_exit) >= 2 and len(reasons_exit) > len(reasons_hold):
        return {"action": "exit", "reasoning": "; ".join(reasons_exit)}
    return {"action": "hold",
            "reasoning": "; ".join(reasons_hold) if reasons_hold else "No strong exit signal yet."}


# ── Scheduled entry point ──────────────────────────────────────────────

def review_open_positions(db_url: str, price_history_fn: Callable,
                           safety_cap_days: int = 15) -> Dict[str, Any]:
    """Call on a schedule (every 30 min, market hours). Loops every open
    ai_stock_picks row, computes indicators, lets AIEM decide hold vs
    exit, and writes the outcome. Logs every decision via
    decision_logger. Never raises — logs and continues per-ticker on
    error so one bad ticker can't stall the whole review."""
    reviewed, closed = 0, 0
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, ticker, pick_date, target_pct, stop_pct
                    FROM ai_stock_picks
                    WHERE status = 'open' OR status IS NULL
                """)
                positions = cur.fetchall()
    except Exception as e:
        print(f"[aiem_exit_engine] could not load open positions: {e}")
        return {"reviewed": 0, "closed": 0, "error": str(e)}

    for pos in positions:
        reviewed += 1
        ticker = pos["ticker"]
        try:
            df = price_history_fn(ticker, days=40)
            if df is None or df.empty:
                continue

            entry_rows = df[df.index.date >= pos["pick_date"]]
            entry_price = float(entry_rows["Close"].iloc[0]) if not entry_rows.empty else float(df["Close"].iloc[0])
            last_price = float(df["Close"].iloc[-1])
            unrealized_pnl_pct = (last_price - entry_price) / entry_price * 100

            indicators = _compute_indicators(df)
            decision = _score_exit(
                indicators, unrealized_pnl_pct,
                target_pct=float(pos["target_pct"] or 5.0),
                stop_pct=float(pos["stop_pct"] or 2.5),
            )

            days_open = (dt.date.today() - pos["pick_date"]).days
            if days_open >= safety_cap_days and decision["action"] != "exit":
                decision = {"action": "exit", "reasoning":
                            f"Safety cap reached ({days_open}d open) — closing to bound risk "
                            f"regardless of current indicator reading."}

            dl.log_decision(
                signal_name="aiem_position_review",
                decision_type=decision["action"],
                reasoning=decision["reasoning"],
                ticker=ticker,
                input_state_snapshot={
                    "unrealized_pnl_pct": round(unrealized_pnl_pct, 3),
                    "days_open": days_open,
                    **{k: v for k, v in indicators.items() if k != "last_close"},
                },
            )

            if decision["action"] == "exit":
                with psycopg2.connect(db_url, connect_timeout=5) as conn2, conn2.cursor() as cur2:
                    cur2.execute("""
                        UPDATE ai_stock_picks
                        SET status = 'closed', exit_price = %s,
                            exit_reason = %s, closed_at = NOW()
                        WHERE id = %s
                    """, (last_price, decision["reasoning"][:500], pos["id"]))
                    conn2.commit()
                closed += 1

        except Exception as e:
            print(f"[aiem_exit_engine] {ticker} review error: {e}")
            continue

    print(f"[aiem_exit_engine] reviewed {reviewed} open position(s), closed {closed}")
    return {"reviewed": reviewed, "closed": closed}


if __name__ == "__main__":
    print("aiem_exit_engine: indicator-based hold/exit judgment for open AIEM positions.")
    print("Wire review_open_positions() into the scheduler per the instructions above.")
