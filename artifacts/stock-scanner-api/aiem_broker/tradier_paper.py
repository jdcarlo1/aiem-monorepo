"""Tradier-quoted paper broker — feels like a real brokerage, never sends live orders.

Uses live Tradier NBBO / option chains for fills, maintains a local paper ledger,
and returns brokerage-shaped OrderResults. HTTP POSTs to /v1/accounts/.../orders
are intentionally impossible from this adapter.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

from .base import BrokerAdapter
from .tradier_market import (
    connection_probe,
    fetch_option_quote,
    fetch_quote,
    tradier_account_id,
    tradier_token,
)
from .types import (
    AssetClass,
    BrokerAccount,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
)

# Default simulated buying power (real Tradier account may be unfunded).
_DEFAULT_CASH = float(os.environ.get("TRADIER_PAPER_STARTING_CASH", "100000") or 100000)
_COMMISSION_EQUITY = float(os.environ.get("TRADIER_PAPER_COMMISSION_EQUITY", "0") or 0)
_COMMISSION_OPT_PER_CONTRACT = float(
    os.environ.get("TRADIER_PAPER_COMMISSION_OPT", "0.35") or 0.35
)
_STATE_PATH = Path(
    os.environ.get(
        "TRADIER_PAPER_STATE_PATH",
        "/tmp/aiem_tradier_paper_state.json",
    )
)


class TradierPaperBrokerAdapter(BrokerAdapter):
    """Paper trading with live Tradier market data.

    provider_id = tradier_paper
    supports_live = False  — cannot place live orders by construction
    """

    provider_id = "tradier_paper"
    supports_live = False
    supports_options = True
    uses_live_quotes = True

    def __init__(self, starting_cash: float | None = None):
        self._lock = threading.RLock()
        self._starting_cash = float(
            starting_cash if starting_cash is not None else _DEFAULT_CASH
        )
        self._cash = self._starting_cash
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: list[dict] = []
        self._fills: list[dict] = []
        self._load_state()

    # ── persistence (process-local; survives restarts in same VM) ──────────
    def _load_state(self) -> None:
        try:
            if not _STATE_PATH.exists():
                return
            data = json.loads(_STATE_PATH.read_text())
            self._cash = float(data.get("cash", self._starting_cash))
            self._orders = list(data.get("orders") or [])
            self._fills = list(data.get("fills") or [])
            pos = {}
            for p in data.get("positions") or []:
                t = (p.get("ticker") or "").upper()
                if not t:
                    continue
                pos[t] = BrokerPosition(
                    ticker=t,
                    quantity=float(p.get("quantity") or 0),
                    avg_price=p.get("avg_price"),
                    market_value=p.get("market_value"),
                    asset_class=p.get("asset_class") or "equity",
                    raw=p.get("raw") or {},
                )
            self._positions = pos
        except Exception as e:
            print(f"[tradier_paper] state load skipped: {e}")

    def _save_state(self) -> None:
        try:
            payload = {
                "cash": self._cash,
                "starting_cash": self._starting_cash,
                "orders": self._orders[-500:],
                "fills": self._fills[-500:],
                "positions": [p.to_dict() for p in self._positions.values()],
                "updated_at": time.time(),
            }
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _STATE_PATH.write_text(json.dumps(payload, default=str))
        except Exception as e:
            print(f"[tradier_paper] state save skipped: {e}")

    # ── BrokerAdapter API ─────────────────────────────────────────────────
    def status(self) -> dict:
        probe = connection_probe()
        acct = tradier_account_id() or probe.get("account_number")
        return {
            "provider": self.provider_id,
            "connected": bool(probe.get("quotes_ok")),
            "ready_for_live_hookup": False,
            "supports_live": False,
            "supports_options": True,
            "mode": "paper",
            "uses_live_quotes": True,
            "token_present": bool(tradier_token()),
            "linked_account_id": acct,
            "option_level": probe.get("option_level"),
            "account_type": probe.get("account_type"),
            "paper_cash": round(self._cash, 2),
            "open_positions": len(self._positions),
            "fill_count": len(self._fills),
            "tradier_probe": {
                k: probe.get(k)
                for k in (
                    "quotes_ok",
                    "profile_ok",
                    "api_base",
                    "option_level",
                    "account_number",
                    "spy_quote",
                )
            },
            "note": (
                "SIMULATED fills at live Tradier bid/ask — "
                "no HTTP orders sent to Tradier. "
                "Feels like brokerage; money stays paper."
            ),
        }

    def get_account(self) -> BrokerAccount:
        probe = connection_probe()
        acct = tradier_account_id() or probe.get("account_number") or "TRADIER-PAPER"
        equity = self._cash + sum(float(p.market_value or 0) for p in self._positions.values())
        return BrokerAccount(
            provider=self.provider_id,
            account_id=f"PAPER-{acct}",
            cash=round(self._cash, 2),
            buying_power=round(self._cash, 2),
            mode="paper",
            connected=bool(tradier_token()),
            details={
                "simulated": True,
                "linked_tradier_account": acct,
                "option_level": probe.get("option_level"),
                "account_type": probe.get("account_type"),
                "equity": round(equity, 2),
                "starting_cash": self._starting_cash,
                "live_orders": False,
                "commission_equity": _COMMISSION_EQUITY,
                "commission_opt_per_contract": _COMMISSION_OPT_PER_CONTRACT,
            },
        )

    def get_positions(self) -> List[BrokerPosition]:
        with self._lock:
            # Refresh market values from live quotes when possible
            refreshed = []
            for p in self._positions.values():
                q = None
                raw = p.raw or {}
                if (p.asset_class or "").startswith("option") or raw.get("option_symbol"):
                    if raw.get("option_symbol"):
                        q = fetch_quote(raw["option_symbol"])
                    elif raw.get("strike") and raw.get("expiry"):
                        q = fetch_option_quote(
                            p.ticker,
                            float(raw["strike"]),
                            str(raw["expiry"]),
                            right=str(raw.get("option_right") or "call"),
                        )
                else:
                    q = fetch_quote(p.ticker)
                mv = p.market_value
                if q:
                    last = q.get("last") or q.get("mid") or q.get("ask") or q.get("bid")
                    if last:
                        mult = 100.0 if (p.asset_class or "").startswith("option") else 1.0
                        mv = float(last) * float(p.quantity) * mult
                refreshed.append(
                    BrokerPosition(
                        ticker=p.ticker,
                        quantity=p.quantity,
                        avg_price=p.avg_price,
                        market_value=mv,
                        asset_class=p.asset_class,
                        raw=p.raw,
                    )
                )
            return refreshed

    def get_quote(self, ticker: str) -> Optional[dict]:
        q = fetch_quote(ticker)
        if not q:
            return {
                "ticker": (ticker or "").upper(),
                "last": None,
                "bid": None,
                "ask": None,
                "source": "tradier_paper_unavailable",
            }
        return q

    def get_option_quote(
        self,
        underlying: str,
        strike: float,
        expiry: str,
        right: str = "call",
    ) -> Optional[dict]:
        return fetch_option_quote(underlying, strike, expiry, right=right)

    def resolve_fill_price(self, order: OrderRequest) -> dict:
        """Compute realistic fill from live Tradier NBBO (no order sent)."""
        side = order.side.value if hasattr(order.side, "value") else str(order.side)
        is_buy = side in ("buy", "buy_to_open")
        is_option = (
            order.asset_class == AssetClass.OPTION
            or (order.strike is not None and order.expiry)
            or str((order.metadata or {}).get("trade_type") or "").upper()
            in ("CALL_OPTION", "PUT_OPTION")
        )

        quote = None
        if is_option:
            right = (order.option_right or "call").lower()
            tt = str((order.metadata or {}).get("trade_type") or "").upper()
            if tt == "PUT_OPTION":
                right = "put"
            quote = fetch_option_quote(
                order.ticker,
                float(order.strike),
                str(order.expiry),
                right=right,
            )
            if quote:
                bid, ask = quote.get("bid"), quote.get("ask")
                mid = quote.get("mid")
                if is_buy:
                    fill = ask or mid or quote.get("last") or bid
                else:
                    fill = bid or mid or quote.get("last") or ask
                return {
                    "ok": fill is not None and float(fill) > 0,
                    "fill_price": float(fill) if fill else None,
                    "mid_price": float(mid) if mid else None,
                    "bid": bid,
                    "ask": ask,
                    "quote": quote,
                    "asset_class": "option",
                    "multiplier": 100.0,
                    "option_symbol": quote.get("option_symbol"),
                    "source": "tradier_option_nbbo",
                }

        quote = fetch_quote(order.ticker)
        if quote:
            bid, ask = quote.get("bid"), quote.get("ask")
            last = quote.get("last")
            mid = None
            if bid and ask:
                mid = (float(bid) + float(ask)) / 2.0
            elif last:
                mid = float(last)
            if is_buy:
                fill = ask or last or bid
            else:
                fill = bid or last or ask
            # Limit orders: do not fill through limit
            if order.limit_price is not None and fill is not None:
                lp = float(order.limit_price)
                if is_buy and float(fill) > lp:
                    fill = lp  # assume limit resting → fill at limit when marketable
                if (not is_buy) and float(fill) < lp:
                    fill = lp
            return {
                "ok": fill is not None and float(fill) > 0,
                "fill_price": float(fill) if fill else None,
                "mid_price": float(mid) if mid else None,
                "bid": bid,
                "ask": ask,
                "quote": quote,
                "asset_class": "equity",
                "multiplier": 1.0,
                "option_symbol": None,
                "source": "tradier_equity_nbbo",
            }

        # Fallback: metadata ref_price / limit (still paper, no broker order)
        ref = order.limit_price or (order.metadata or {}).get("ref_price")
        if ref is not None and float(ref) > 0:
            return {
                "ok": True,
                "fill_price": float(ref),
                "mid_price": float(ref),
                "bid": None,
                "ask": None,
                "quote": None,
                "asset_class": "option" if is_option else "equity",
                "multiplier": 100.0 if is_option else 1.0,
                "option_symbol": None,
                "source": "ref_price_fallback",
            }
        return {
            "ok": False,
            "fill_price": None,
            "mid_price": None,
            "bid": None,
            "ask": None,
            "quote": None,
            "asset_class": "option" if is_option else "equity",
            "multiplier": 100.0 if is_option else 1.0,
            "option_symbol": None,
            "source": "no_quote",
            "error": "Tradier quote unavailable and no ref_price",
        }

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Simulate a brokerage fill at live Tradier prices. Never hits order API."""
        ticker = (order.ticker or "").upper().strip()
        qty = float(order.quantity or 0)
        side = order.side.value if hasattr(order.side, "value") else str(order.side)

        if not ticker or qty <= 0:
            return OrderResult(
                ok=False,
                status=OrderStatus.REJECTED,
                provider=self.provider_id,
                mode="paper",
                ticker=ticker,
                side=side,
                quantity=qty,
                message="invalid ticker/quantity",
            )

        # Hard safety: refuse if someone tries to arm live mode through this adapter
        if os.environ.get("AIEM_ALLOW_LIVE_ORDERS") == "1" and os.environ.get(
            "TRADIER_PAPER_FORCE_LIVE", ""
        ) == "1":
            return OrderResult(
                ok=False,
                status=OrderStatus.BLOCKED,
                provider=self.provider_id,
                mode="live_blocked",
                ticker=ticker,
                side=side,
                quantity=qty,
                message=(
                    "tradier_paper adapter cannot place live orders by design. "
                    "Use a dedicated live adapter after review."
                ),
            )

        resolved = self.resolve_fill_price(order)
        if not resolved.get("ok") or not resolved.get("fill_price"):
            return OrderResult(
                ok=False,
                status=OrderStatus.REJECTED,
                provider=self.provider_id,
                mode="paper",
                ticker=ticker,
                side=side,
                quantity=qty,
                message=resolved.get("error") or "no Tradier quote for fill",
                raw=resolved,
            )

        fill = float(resolved["fill_price"])
        mid = resolved.get("mid_price")
        mult = float(resolved.get("multiplier") or 1.0)
        is_option = resolved.get("asset_class") == "option"
        commission = (
            _COMMISSION_OPT_PER_CONTRACT * qty
            if is_option
            else _COMMISSION_EQUITY
        )
        notional = fill * qty * mult
        is_buy = side in ("buy", "buy_to_open")

        with self._lock:
            if is_buy and (notional + commission) > self._cash + 1e-6:
                return OrderResult(
                    ok=False,
                    status=OrderStatus.REJECTED,
                    provider=self.provider_id,
                    mode="paper",
                    ticker=ticker,
                    side=side,
                    quantity=qty,
                    fill_price=fill,
                    message=(
                        f"insufficient paper buying power "
                        f"(need ${notional + commission:.2f}, have ${self._cash:.2f})"
                    ),
                    raw=resolved,
                )

            pos_key = ticker
            opt_sym = resolved.get("option_symbol")
            if is_option and opt_sym:
                pos_key = str(opt_sym)

            if is_buy:
                self._cash -= notional + commission
                prev = self._positions.get(pos_key)
                if prev:
                    new_qty = prev.quantity + qty
                    avg = (
                        (prev.avg_price or fill) * prev.quantity + fill * qty
                    ) / max(new_qty, 1e-9)
                    self._positions[pos_key] = BrokerPosition(
                        ticker=ticker if not is_option else pos_key,
                        quantity=new_qty,
                        avg_price=avg,
                        market_value=new_qty * fill * mult,
                        asset_class="option" if is_option else "equity",
                        raw={
                            "underlying": ticker,
                            "option_symbol": opt_sym,
                            "strike": order.strike,
                            "expiry": order.expiry,
                            "option_right": order.option_right
                            or (
                                "put"
                                if str((order.metadata or {}).get("trade_type") or "")
                                .upper()
                                == "PUT_OPTION"
                                else "call"
                            ),
                            "multiplier": mult,
                        },
                    )
                else:
                    self._positions[pos_key] = BrokerPosition(
                        ticker=ticker if not is_option else pos_key,
                        quantity=qty,
                        avg_price=fill,
                        market_value=qty * fill * mult,
                        asset_class="option" if is_option else "equity",
                        raw={
                            "underlying": ticker,
                            "option_symbol": opt_sym,
                            "strike": order.strike,
                            "expiry": order.expiry,
                            "option_right": order.option_right
                            or (
                                "put"
                                if str((order.metadata or {}).get("trade_type") or "")
                                .upper()
                                == "PUT_OPTION"
                                else "call"
                            ),
                            "multiplier": mult,
                        },
                    )
            else:
                self._cash += notional - commission
                prev = self._positions.get(pos_key) or self._positions.get(ticker)
                key = pos_key if pos_key in self._positions else ticker
                if prev:
                    left = prev.quantity - qty
                    if left <= 1e-9:
                        self._positions.pop(key, None)
                    else:
                        self._positions[key] = BrokerPosition(
                            ticker=prev.ticker,
                            quantity=left,
                            avg_price=prev.avg_price,
                            market_value=left * fill * mult,
                            asset_class=prev.asset_class,
                            raw=prev.raw,
                        )

            oid = order.client_order_id or f"TRADIER-PAPER-{uuid.uuid4().hex[:12].upper()}"
            spread_pct = None
            if mid and mid > 0 and resolved.get("bid") and resolved.get("ask"):
                spread_pct = (float(resolved["ask"]) - float(resolved["bid"])) / float(mid)

            result = OrderResult(
                ok=True,
                status=OrderStatus.FILLED,
                provider=self.provider_id,
                mode="paper",
                ticker=ticker,
                side=side,
                quantity=qty,
                fill_price=round(fill, 4),
                broker_order_id=oid,
                message=(
                    "FILLED (SIMULATED) at live Tradier "
                    f"{'ask' if is_buy else 'bid'} — no broker order sent"
                ),
                raw={
                    "ts": time.time(),
                    "mid_price": mid,
                    "bid": resolved.get("bid"),
                    "ask": resolved.get("ask"),
                    "spread_pct": spread_pct,
                    "commission": commission,
                    "notional": round(notional, 4),
                    "multiplier": mult,
                    "asset_class": resolved.get("asset_class"),
                    "option_symbol": opt_sym,
                    "fill_source": resolved.get("source"),
                    "quote": {
                        k: (resolved.get("quote") or {}).get(k)
                        for k in ("last", "bid", "ask", "mid", "option_symbol")
                    },
                    "simulated": True,
                    "live_order_sent": False,
                },
            )
            self._orders.append(result.to_dict())
            self._fills.append(result.to_dict())
            self._save_state()
            return result
