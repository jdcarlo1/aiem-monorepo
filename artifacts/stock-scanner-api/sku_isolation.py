"""AIEM vs OE SKU isolation helpers (same reserved VM, separate books).

Market data (Polygon / Tradier SPY bars) may be shared.
Paper ledgers, strategy tickers, and broker adapters are SKU-scoped.
"""
from __future__ import annotations

from typing import Optional, Tuple

VALID_SKUS = frozenset({"aiem", "oe"})


def normalize_sku(sku: Optional[str]) -> str:
    s = (sku or "aiem").strip().lower()
    return s if s in VALID_SKUS else "aiem"


def product_name(sku: Optional[str]) -> str:
    return "AIEM" if normalize_sku(sku) == "aiem" else "OE"


def is_sku_strategy_ticker(ticker: Optional[str]) -> bool:
    """True for Pattern Lab / Strategies persist tickers like AIEM:SPY:put_butterfly."""
    t = (ticker or "").upper()
    return t.startswith("AIEM:") or t.startswith("OE:")


def sku_strategy_ticker(sku: str, underlying: str, strategy: str) -> str:
    return f"{normalize_sku(sku).upper()}:{underlying}:{strategy}"


def equity_book_sql_exclusion() -> str:
    """Keep AIEM equity autonomous portfolio free of SKU strategy package rows."""
    return " AND ticker NOT LIKE 'AIEM:%' AND ticker NOT LIKE 'OE:%' "


def sku_book_sql_inclusion(sku: str) -> Tuple[str, tuple]:
    """Filter aiem_paper_trades to one SKU strategy book via ticker prefix."""
    prefix = f"{normalize_sku(sku).upper()}:%"
    return " AND ticker LIKE %s ", (prefix,)


def live_allow_env_key(sku: str) -> str:
    """Per-SKU third switch for live orders (in addition to simulation_lock)."""
    return "AIEM_ALLOW_LIVE_ORDERS" if normalize_sku(sku) == "aiem" else "OE_ALLOW_LIVE_ORDERS"
