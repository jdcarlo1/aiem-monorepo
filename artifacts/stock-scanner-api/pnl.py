from datetime import datetime

TRADES: list = []


def log_trade(ticker: str, pnl: float, reason: str):
    TRADES.append({
        "ticker": ticker,
        "pnl": round(pnl, 2),
        "reason": reason,
        "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    })


def total_pnl() -> float:
    return round(sum(t["pnl"] for t in TRADES), 2)


def get_trades() -> list:
    return list(TRADES)


def clear_trades():
    global TRADES
    TRADES = []
