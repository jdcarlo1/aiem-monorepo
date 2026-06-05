import json
import os
from datetime import datetime

PORTFOLIO_FILE = "portfolio.json"


def load_portfolio() -> dict:
    if not os.path.exists(PORTFOLIO_FILE):
        return {"positions": [], "cash": 100000.0, "trades": []}
    with open(PORTFOLIO_FILE, "r") as f:
        return json.load(f)


def save_portfolio(data: dict):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_portfolio():
    return load_portfolio()


def add_position(ticker: str, shares: float, price: float):
    p = load_portfolio()
    cost = shares * price
    if cost > p["cash"]:
        return {"error": f"Insufficient cash. Available: ${p['cash']:,.2f}, needed: ${cost:,.2f}"}

    p["cash"] -= cost

    existing = next((pos for pos in p["positions"] if pos["ticker"] == ticker.upper()), None)
    if existing:
        total_shares = existing["shares"] + shares
        total_cost = existing["avg_cost"] * existing["shares"] + price * shares
        existing["shares"] = total_shares
        existing["avg_cost"] = total_cost / total_shares
        existing["updated"] = datetime.utcnow().isoformat()
    else:
        p["positions"].append({
            "ticker": ticker.upper(),
            "shares": shares,
            "avg_cost": price,
            "added": datetime.utcnow().isoformat(),
            "updated": datetime.utcnow().isoformat(),
        })

    p["trades"].append({
        "type": "BUY",
        "ticker": ticker.upper(),
        "shares": shares,
        "price": price,
        "total": cost,
        "date": datetime.utcnow().isoformat(),
    })

    save_portfolio(p)
    return {"success": True, "cash_remaining": p["cash"], "message": f"Bought {shares} shares of {ticker.upper()} @ ${price:.2f}"}


def remove_position(ticker: str, shares: float, price: float):
    p = load_portfolio()
    existing = next((pos for pos in p["positions"] if pos["ticker"] == ticker.upper()), None)
    if not existing:
        return {"error": f"No position in {ticker.upper()}"}
    if shares > existing["shares"]:
        return {"error": f"Only {existing['shares']} shares held, cannot sell {shares}"}

    proceeds = shares * price
    p["cash"] += proceeds
    existing["shares"] -= shares

    if existing["shares"] <= 0:
        p["positions"] = [pos for pos in p["positions"] if pos["ticker"] != ticker.upper()]

    p["trades"].append({
        "type": "SELL",
        "ticker": ticker.upper(),
        "shares": shares,
        "price": price,
        "total": proceeds,
        "pnl": (price - existing["avg_cost"]) * shares,
        "date": datetime.utcnow().isoformat(),
    })

    save_portfolio(p)
    return {"success": True, "cash_remaining": p["cash"], "message": f"Sold {shares} shares of {ticker.upper()} @ ${price:.2f}"}


def get_portfolio_value(current_prices: dict) -> dict:
    p = load_portfolio()
    positions_value = 0.0
    positions_out = []

    for pos in p["positions"]:
        ticker = pos["ticker"]
        price = current_prices.get(ticker, pos["avg_cost"])
        value = pos["shares"] * price
        cost_basis = pos["shares"] * pos["avg_cost"]
        pnl = value - cost_basis
        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

        positions_value += value
        positions_out.append({
            **pos,
            "current_price": price,
            "value": round(value, 2),
            "cost_basis": round(cost_basis, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })

    total_value = positions_value + p["cash"]
    return {
        "cash": round(p["cash"], 2),
        "positions_value": round(positions_value, 2),
        "total_value": round(total_value, 2),
        "total_pnl": round(total_value - 100000.0, 2),
        "total_pnl_pct": round((total_value - 100000.0) / 100000.0 * 100, 2),
        "positions": positions_out,
        "trades": p.get("trades", [])[-20:],
    }
