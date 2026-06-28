"""
portfolio_correlation_risk.py
====================================================================
Checks whether current open positions are secretly concentrated in
correlated names (e.g. 5 tech stocks that move together), even if
they look diversified by ticker count alone.
====================================================================
"""

import datetime as dt
from typing import Dict, Any, List

import psycopg2
import psycopg2.extras

CORRELATION_GROUPS = {
    "mega_tech": {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA"},
    "semis": {"NVDA", "AMD", "INTC", "AVGO", "MU", "SNDK", "LSCC", "MRVL", "LITE", "CRDO", "AMAT"},
    "ev_meme": {"TSLA", "RIVN", "LCID"},
    "biotech_meme": {"MRNA", "BYND", "HOOD"},
    "crypto_adjacent": {"COIN", "MARA", "RIOT", "HIVE"},
}


def get_open_position_tickers(db_url: str) -> List[str]:
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker FROM ai_stock_picks
                WHERE status IS NULL OR status = 'open'
            """)
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def check_concentration_risk(tickers: List[str]) -> Dict[str, Any]:
    """
    Flags if too many open positions fall into the same correlation
    group — meaning real diversification is lower than the position
    count suggests.
    """
    group_counts = {}
    for group_name, group_tickers in CORRELATION_GROUPS.items():
        matches = [t for t in tickers if t in group_tickers]
        if matches:
            group_counts[group_name] = matches

    total_positions = len(tickers)
    warnings = []
    for group_name, matches in group_counts.items():
        if len(matches) >= 3:
            pct = round(len(matches) / total_positions * 100, 1) if total_positions else 0
            warnings.append(
                f"{len(matches)} of {total_positions} open positions ({pct}%) "
                f"are in the '{group_name}' correlation group: {matches} — "
                f"these likely move together, real diversification is lower than position count suggests."
            )

    return {
        "total_open_positions": total_positions,
        "group_breakdown": group_counts,
        "warnings": warnings,
        "concentration_risk_flag": len(warnings) > 0,
        "checked_at": dt.datetime.utcnow().isoformat(),
    }


def check_current_portfolio_risk(db_url: str) -> Dict[str, Any]:
    tickers = get_open_position_tickers(db_url)
    return check_concentration_risk(tickers)


if __name__ == "__main__":
    test_tickers = ["AAPL", "NVDA", "AMD", "MU", "TSLA"]
    print(check_concentration_risk(test_tickers))
