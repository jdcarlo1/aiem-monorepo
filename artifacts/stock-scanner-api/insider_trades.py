"""
C-Suite Insider Trades via yfinance insider_transactions.
"""
import math
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _classify_trade(text: str) -> str | None:
    """Return 'Buy' or 'Sell' based on the transaction text, or None to skip."""
    if not isinstance(text, str):
        return None
    t = text.lower()
    if any(k in t for k in ("purchase", "acquisition", "bought", "buy")):
        return "Buy"
    if any(k in t for k in ("sale", "sold", "sell")):
        return "Sell"
    # Stock gifts, option exercises, conversions — skip
    return None


def _fetch_ticker_trades(ticker: str, cutoff: str) -> list:
    try:
        import yfinance as yf
        import pandas as pd

        tkr = yf.Ticker(ticker)
        df  = tkr.insider_transactions

        if df is None or df.empty:
            return []

        # Normalise column names (yfinance can vary slightly)
        df.columns = [c.strip() for c in df.columns]

        trades = []
        for _, row in df.iterrows():
            # Date filter
            raw_date = row.get("Start Date") or row.get("startDate") or ""
            if hasattr(raw_date, "date"):
                trade_date = str(raw_date.date())
            else:
                trade_date = str(raw_date)[:10]

            if trade_date < cutoff:
                continue

            text = str(row.get("Text", "") or row.get("text", "") or "")
            trade_type = _classify_trade(text)
            if trade_type is None:
                continue

            shares = int(_f(row.get("Shares", 0)))
            value  = _f(row.get("Value", 0))
            price  = round(value / shares, 2) if shares > 0 else 0.0

            insider_name = str(row.get("Insider", "") or "").strip()
            title        = str(row.get("Position", "") or "").strip()

            if shares <= 0:
                continue

            trades.append({
                "ticker":       ticker,
                "insider_name": insider_name,
                "title":        title,
                "trade_type":   trade_type,
                "shares":       shares,
                "price":        price,
                "value":        round(value),
                "date":         trade_date,
            })

        return trades

    except Exception:
        return []


def fetch_insider_trades(tickers: list, days: int = 30) -> list:
    cutoff     = (date.today() - timedelta(days=days)).isoformat()
    all_trades: list = []

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_ticker_trades, t, cutoff): t for t in tickers}
        for fut in as_completed(futures):
            all_trades.extend(fut.result())

    # Sort: most recent first, then by value descending
    all_trades.sort(key=lambda x: (x["date"], x["value"]), reverse=True)
    return all_trades
