"""
backtest_harness.py
====================================================================
Runs your ACTUAL signal logic against stored historical data
(polygon_market_daily) to see how it would have performed — separate
from live signals firing in real time. Pure historical replay.
====================================================================
"""

import datetime as dt
from typing import Dict, Any, List, Callable

import psycopg2
import psycopg2.extras


def get_historical_bars(db_url: str, ticker: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT scan_date, open_price, high_price, low_price, close_price, volume
                FROM polygon_market_daily
                WHERE ticker = %s AND scan_date BETWEEN %s AND %s
                ORDER BY scan_date ASC
            """, (ticker, start_date, end_date))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def run_backtest(db_url: str, ticker: str, start_date: str, end_date: str,
                  signal_fn: Callable[[List[Dict[str, Any]], int], bool],
                  exit_after_days: int = 5) -> Dict[str, Any]:
    """
    signal_fn: a function that takes (bars_so_far, current_index) and
    returns True if it would have entered a position on that day.
    """
    bars = get_historical_bars(db_url, ticker, start_date, end_date)
    if len(bars) < exit_after_days + 1:
        return {"error": "not enough historical data for this window"}

    trades = []
    for i in range(len(bars) - exit_after_days):
        bars_so_far = bars[:i + 1]
        if signal_fn(bars_so_far, i):
            entry_price = bars[i]["close_price"]
            exit_price = bars[i + exit_after_days]["close_price"]
            pct_return = round((exit_price - entry_price) / entry_price * 100, 3)
            trades.append({
                "entry_date": str(bars[i]["scan_date"]),
                "exit_date": str(bars[i + exit_after_days]["scan_date"]),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pct_return": pct_return,
            })

    if not trades:
        return {"ticker": ticker, "total_trades": 0, "trades": [], "note": "signal never fired in this window"}

    win_rate = round(sum(1 for t in trades if t["pct_return"] > 0) / len(trades) * 100, 1)
    avg_return = round(sum(t["pct_return"] for t in trades) / len(trades), 3)

    return {
        "ticker": ticker,
        "total_trades": len(trades),
        "win_rate_pct": win_rate,
        "avg_return_pct": avg_return,
        "trades": trades,
        "checked_at": dt.datetime.utcnow().isoformat(),
    }


def example_simple_signal(bars_so_far: List[Dict[str, Any]], idx: int) -> bool:
    """Example placeholder signal: enter if today closed up >3%."""
    if idx == 0:
        return False
    today = bars_so_far[-1]
    yesterday = bars_so_far[-2]
    if yesterday["close_price"] == 0:
        return False
    pct_change = (today["close_price"] - yesterday["close_price"]) / yesterday["close_price"]
    return pct_change > 0.03


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        result = run_backtest(db_url, "AAPL", "2025-01-01", "2025-06-01", example_simple_signal)
        print(result)
    else:
        print("Set DATABASE_URL to test against real data.")
