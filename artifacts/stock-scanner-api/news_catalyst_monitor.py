"""
news_catalyst_monitor.py
====================================================================
Lightweight headline-risk flag: checks for recent high-severity news
keywords (FDA, lawsuit, investigation, etc.) before allowing entry on
a ticker. Pairs with news_catalyst.py — this is specifically a
pre-entry GATE, not a full news analysis system.

Table used: news_catalyst_log (ticker, alert_date, catalyst, alerted_at)
====================================================================
"""

import datetime as dt
from typing import Dict, Any, List

import psycopg2

HIGH_RISK_KEYWORDS = {
    "fda rejection", "fda denial", "lawsuit", "sec investigation",
    "fraud", "bankruptcy", "recall", "ceo resigns", "ceo fired",
    "halted", "delisting", "going concern",
}


def check_recent_headlines(db_url: str, ticker: str, lookback_hours: int = 24) -> Dict[str, Any]:
    """
    Checks news_catalyst_log for high-risk keywords in catalyst text
    logged in the last N hours.
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT catalyst, alerted_at FROM news_catalyst_log
                WHERE ticker = %s AND alerted_at > NOW() - INTERVAL '%s hours'
            """ % ('%s', lookback_hours), (ticker,))
            rows = cur.fetchall()
    except Exception as e:
        return {"ticker": ticker, "error": f"could not query news table: {e}", "high_risk_flag": False}
    finally:
        conn.close()

    flagged = []
    for catalyst, alerted_at in rows:
        if not catalyst:
            continue
        catalyst_lower = catalyst.lower()
        for keyword in HIGH_RISK_KEYWORDS:
            if keyword in catalyst_lower:
                flagged.append({
                    "catalyst": catalyst,
                    "alerted_at": str(alerted_at),
                    "matched_keyword": keyword,
                })
                break

    return {
        "ticker": ticker,
        "headlines_checked": len(rows),
        "flagged_headlines": flagged,
        "high_risk_flag": len(flagged) > 0,
        "checked_at": dt.datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        print(check_recent_headlines(db_url, "AAPL"))
    else:
        print("Set DATABASE_URL to test against real data.")
