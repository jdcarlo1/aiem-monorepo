"""
slippage_model.py
====================================================================
Estimates expected execution cost (slippage) for an options order
before it's placed, using bid-ask spread and order size relative to
volume. No broker needed — this works off quote/volume data already
available from your options chain feeds.
====================================================================
"""

import datetime as dt
from typing import Dict, Any

import psycopg2


def estimate_slippage(bid: float, ask: float, contract_volume: int,
                       order_size: int) -> Dict[str, Any]:
    """
    Rough slippage estimate: half the bid-ask spread as baseline cost,
    plus an extra penalty if order_size is large relative to volume
    (since large orders relative to liquidity move the price more).
    """
    if bid is None or ask is None or ask <= bid:
        return {"error": "invalid bid/ask", "estimated_slippage_pct": None}

    mid = (bid + ask) / 2
    spread = ask - bid
    spread_pct = (spread / mid) * 100 if mid else 0

    size_ratio = order_size / contract_volume if contract_volume > 0 else 1.0
    size_penalty_pct = min(size_ratio * 5.0, 10.0)

    estimated_slippage_pct = round((spread_pct / 2) + size_penalty_pct, 3)
    estimated_cost_per_contract = round(mid * (estimated_slippage_pct / 100), 4)

    return {
        "mid_price": round(mid, 4),
        "spread_pct": round(spread_pct, 3),
        "size_ratio": round(size_ratio, 3),
        "estimated_slippage_pct": estimated_slippage_pct,
        "estimated_cost_per_contract": estimated_cost_per_contract,
        "checked_at": dt.datetime.utcnow().isoformat(),
    }


def log_slippage_estimate(db_url: str, ticker: str, result: Dict[str, Any]) -> None:
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO slippage_estimates
                    (ticker, estimated_slippage_pct, estimated_cost_per_contract, checked_at)
                VALUES (%s, %s, %s, %s)
            """, (ticker, result.get("estimated_slippage_pct"),
                  result.get("estimated_cost_per_contract"), result.get("checked_at")))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    print(estimate_slippage(bid=2.40, ask=2.55, contract_volume=500, order_size=10))
