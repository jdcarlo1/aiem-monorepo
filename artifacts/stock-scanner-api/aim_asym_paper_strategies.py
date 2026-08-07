#!/usr/bin/env python3
"""
Asymmetric SPY paper strategies — Pattern Lab / OE Strategies.

Top-3 from 2y real Polygon backtest (no stop, TP grid):
  1. Long put butterfly   — take-profit +200% of debit
  2. Long call butterfly  — take-profit +100% of debit
  3. Put ladder (defined) — take-profit +150% of debit

Shared rules:
  - Underlying SPY
  - Risk budget $500 debit max per package (1 lot if fits)
  - Entry: Monday after 10:00 ET when flat (weekly)
  - Expiry: ~3 weeks out (next Friday + 3 weeks)
  - NO stop loss
  - Exit at TP% of entry debit, else flatten 15:55 ET on expiry Friday
  - Real Tradier mids only — WAITING_PREMIUM if chain unavailable
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date, timedelta
from typing import Any, Optional, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

log = logging.getLogger("aim_asym")

ET = ZoneInfo("America/New_York")
RISK_USD = 500.0
ENTRY_AFTER = "10:00"
FLATTEN_TIME = "15:55"


def _next_friday(d: date, weeks_ahead: int = 3) -> date:
    add = (4 - d.weekday()) % 7
    fri = d + timedelta(days=add)
    if fri <= d:
        fri += timedelta(days=7)
    return fri + timedelta(weeks=weeks_ahead)


def _tradier_token() -> str:
    return os.getenv("TRADIER_API_TOKEN_2") or os.getenv("TRADIER_API_TOKEN", "")


# Short TTL so three ledgers + marks don't hammer Tradier every bar.
_CHAIN_CACHE: dict[tuple[str, str], tuple[float, list]] = {}
_CHAIN_TTL_SEC = 60.0


def fetch_chain(symbol: str, expiration: date) -> list:
    token = _tradier_token()
    if not token:
        return []
    exp_s = expiration.strftime("%Y-%m-%d")
    cache_key = (symbol.upper(), exp_s)
    now = datetime.now(ET).timestamp()
    hit = _CHAIN_CACHE.get(cache_key)
    if hit and (now - hit[0]) < _CHAIN_TTL_SEC:
        return hit[1]
    try:
        r = requests.get(
            "https://api.tradier.com/v1/markets/options/chains",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"symbol": symbol, "expiration": exp_s, "greeks": "false"},
            timeout=20,
        )
        r.raise_for_status()
        opts = (r.json().get("options") or {}).get("option") or []
        if not isinstance(opts, list):
            opts = [opts] if opts else []
        _CHAIN_CACHE[cache_key] = (now, opts)
        return opts
    except Exception as e:
        log.warning("[asym] chain fetch failed: %s", e)
        return []


def _mid(o: dict) -> Optional[float]:
    bid = float(o.get("bid") or 0)
    ask = float(o.get("ask") or 0)
    last = float(o.get("last") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if last > 0:
        return last
    if ask > 0:
        return ask
    return None


def price_legs(
    underlying: str,
    expiration: date,
    legs: list[tuple[int, str, float]],
    spot: float,
) -> Optional[dict]:
    """
    legs: list of (qty_signed, 'call'|'put', strike)
    Returns {debit, legs:[{qty, right, strike, premium, symbol}], expiration} or None.
    """
    opts = fetch_chain(underlying, expiration)
    if not opts:
        return None
    exp_s = expiration.strftime("%Y-%m-%d")
    priced = []
    net = 0.0
    for qty, right, strike in legs:
        want = right.lower()
        best = None
        best_diff = None
        for o in opts:
            if str(o.get("option_type", "")).lower() != want:
                continue
            if str(o.get("expiration_date", ""))[:10] != exp_s:
                continue
            try:
                k = float(o.get("strike") or 0)
            except (TypeError, ValueError):
                continue
            diff = abs(k - strike)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best = o
        if not best:
            return None
        prem = _mid(best)
        if prem is None or prem <= 0:
            return None
        # reject absurd
        if prem > max(50.0, spot * 0.5):
            return None
        net += qty * prem
        priced.append({
            "qty": qty,
            "right": want,
            "strike": float(best.get("strike")),
            "premium": prem,
            "symbol": best.get("symbol"),
            "bid": float(best.get("bid") or 0),
            "ask": float(best.get("ask") or 0),
        })
    return {"debit_per_share": net, "legs": priced, "expiration": expiration.isoformat()}


def build_long_put_butterfly(spot: float) -> list[tuple[int, str, float]]:
    k = float(round(spot))
    return [(1, "put", k + 5), (-2, "put", k), (1, "put", k - 5)]


def build_long_call_butterfly(spot: float) -> list[tuple[int, str, float]]:
    k = float(round(spot))
    return [(1, "call", k - 5), (-2, "call", k), (1, "call", k + 5)]


def build_put_ladder_defined(spot: float) -> list[tuple[int, str, float]]:
    k = float(round(spot))
    return [(1, "put", k), (-1, "put", k - 5), (-1, "put", k - 10), (1, "put", k - 15)]


class AsymOptionsLedger:
    """Multi-leg debit package paper ledger — weekly Monday entry, TP%, no stop."""

    def __init__(
        self,
        pattern_name: str,
        builder: Callable[[float], list],
        take_profit_pct: float,
        underlying: str = "SPY",
        starting_capital_usd: float = 10000.0,
        risk_usd: float = RISK_USD,
    ):
        self.pattern_name = pattern_name
        self.builder = builder
        self.take_profit_pct = take_profit_pct  # e.g. 200 = +200% of debit
        self.underlying = underlying
        self._starting_capital = starting_capital_usd
        self.account_balance_usd = starting_capital_usd
        self.net_liquidation_usd = starting_capital_usd
        self.risk_usd = risk_usd
        self.active_position: Optional[dict] = None
        self.signal_state: dict = {"status": "IDLE"}
        self.trade_log: list = []
        self.wins = 0
        self.losses = 0
        self._day_key: Optional[str] = None
        self._entered_week: Optional[str] = None  # ISO week key

    @property
    def total_trades(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate_pct(self) -> float:
        return (self.wins / self.total_trades * 100.0) if self.total_trades else 0.0

    @property
    def profit_rate_pct(self) -> float:
        if not self._starting_capital:
            return 0.0
        return ((self.account_balance_usd - self._starting_capital)
                / self._starting_capital * 100.0)

    def snapshot(self) -> dict:
        return {
            "pattern": self.pattern_name,
            "rules": {
                "entry": "Monday after 10:00 ET when flat",
                "structure": self.pattern_name,
                "risk_usd": self.risk_usd,
                "take_profit_pct": self.take_profit_pct,
                "stop_loss": None,
                "exit": f"+{self.take_profit_pct:.0f}% of debit or flatten 15:55 on expiry Friday",
            },
            "account_balance_usd": round(self.account_balance_usd, 2),
            "net_liquidation_usd": round(self.net_liquidation_usd, 2),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate_pct": round(self.win_rate_pct, 2),
            "profit_rate_pct": round(self.profit_rate_pct, 2),
            "active_position": self.active_position,
            "signal_state": self.signal_state,
            "recent_trades": self.trade_log[-10:],
        }

    def _week_key(self, d: date) -> str:
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    def _mark_package(self, expiration: date, legs: list, spot: float) -> Optional[float]:
        """Return current package value in dollars (debit convention: long premium positive)."""
        spec = [(int(L["qty"]), str(L["right"]), float(L["strike"])) for L in legs]
        priced = price_legs(self.underlying, expiration, spec, spot)
        if not priced:
            return None
        # net debit_per_share * 100 * packages
        return float(priced["debit_per_share"]) * 100.0

    def _close(self, exit_value_usd: float, reason: str):
        pos = self.active_position
        if not pos:
            return
        entry_debit = float(pos["entry_debit_usd"])
        # Entry debited cash; exit credits current package mark
        # For debit packages: pnl = exit_mark - entry_debit
        # When short legs dominate mark can be small/negative
        pnl = float(exit_value_usd) - entry_debit
        # Cash: we paid entry_debit at entry (already deducted). At exit we "receive" exit_value.
        self.account_balance_usd += float(exit_value_usd)
        result = "WIN" if pnl > 0 else "LOSS"
        if result == "WIN":
            self.wins += 1
        else:
            self.losses += 1
        self.trade_log.append({
            "symbol": self.underlying,
            "direction": pos.get("direction"),
            "side": "DEBIT_PACKAGE",
            "entry": pos.get("entry_debit_usd"),
            "exit": round(exit_value_usd, 2),
            "shares": pos.get("packages"),
            "contracts": pos.get("packages"),
            "pnl_usd": round(pnl, 2),
            "result": result,
            "reason": reason,
            "legs": pos.get("legs"),
            "expiration": pos.get("expiration"),
        })
        log.info(
            "[%s] %s (%s): debit $%.2f -> mark $%.2f | P&L $%.2f | Bal $%.2f",
            self.pattern_name, result, reason, entry_debit, exit_value_usd, pnl,
            self.account_balance_usd,
        )
        self.active_position = None
        self.signal_state = {"status": "FLAT", "last_exit": reason}
        self.net_liquidation_usd = self.account_balance_usd

    def evaluate(self, today_dataframe: pd.DataFrame):
        df = today_dataframe.sort_index()
        if df.empty:
            return
        latest = df.iloc[-1]
        spot = float(latest["close"])
        ts = latest.name
        if hasattr(ts, "tz_convert"):
            ts = ts.tz_convert(ET) if ts.tzinfo else ts.tz_localize(ET)
        day = ts.date() if hasattr(ts, "date") else datetime.now(ET).date()
        bar_time = ts.strftime("%H:%M") if hasattr(ts, "strftime") else "12:00"
        day_key = day.isoformat()
        week_key = self._week_key(day)

        if self._day_key != day_key:
            self._day_key = day_key
            if not self.active_position:
                self.signal_state = {"status": "NEW_DAY", "day": day_key}

        # Manage open position
        if self.active_position:
            exp = date.fromisoformat(self.active_position["expiration"])
            mark = self._mark_package(exp, self.active_position["legs"], spot)
            if mark is not None:
                packages = float(self.active_position["packages"])
                # mark is for 1 package in dollars of net debit; scale
                # price_legs returns debit_per_share for 1x qty in legs already including qty signs
                # Our stored legs have qty for 1 package; mark dollars = debit_per_share*100
                mark_usd = mark * packages
                self.active_position["mark_premium"] = round(mark_usd / max(packages, 1) / 100.0, 4)
                self.active_position["unrealized_pnl"] = round(
                    mark_usd - float(self.active_position["entry_debit_usd"]), 2
                )
                self.net_liquidation_usd = self.account_balance_usd + mark_usd
                entry_debit = float(self.active_position["entry_debit_usd"])
                tp = entry_debit * (1.0 + self.take_profit_pct / 100.0)
                # Profit when package mark rises to entry*(1+tp%) for debit flies
                # Actually: pnl = mark_usd - entry_debit; TP when pnl >= entry_debit * tp_pct/100
                # <=> mark_usd >= entry_debit * (1 + tp_pct/100)
                if mark_usd >= tp:
                    self.account_balance_usd  # entry already deducted
                    self._close(mark_usd, f"TP_{int(self.take_profit_pct)}PCT")
                    return
                if day >= exp and bar_time >= FLATTEN_TIME:
                    self._close(mark_usd, "EXPIRY_FLATTEN")
                    return
                if day > exp:
                    self._close(mark_usd, "EXPIRY_FLATTEN")
                    return
            self.signal_state = {
                "status": "IN_POSITION",
                "note": f"TP +{int(self.take_profit_pct)}% · no stop · exp {self.active_position['expiration']}",
            }
            return

        # Entry: Monday after 10:00, one per week
        if day.weekday() != 0:
            self.signal_state = {"status": "WAIT_MONDAY", "note": "weekly Monday entry only"}
            return
        if bar_time < ENTRY_AFTER:
            self.signal_state = {"status": "WAIT_OPEN", "note": f"entry after {ENTRY_AFTER} ET"}
            return
        if self._entered_week == week_key:
            self.signal_state = {"status": "WEEK_DONE", "note": "already entered this week"}
            return

        exp = _next_friday(day, weeks_ahead=3)
        legs_spec = self.builder(spot)
        priced = price_legs(self.underlying, exp, legs_spec, spot)
        if not priced:
            self.signal_state = {
                "status": "WAITING_PREMIUM",
                "note": "Tradier chain unavailable or incomplete — not entering synthetic",
            }
            return
        debit_ps = float(priced["debit_per_share"])
        if debit_ps <= 0:
            # credit or zero — skip for these debit-oriented structures
            self.signal_state = {
                "status": "SKIP_CREDIT",
                "note": f"package debit/share {debit_ps:.3f} ≤ 0 — skip",
            }
            return
        debit_1 = debit_ps * 100.0
        if debit_1 > self.risk_usd:
            self.signal_state = {
                "status": "SKIP_BUDGET",
                "note": f"1-lot debit ${debit_1:.0f} > risk ${self.risk_usd:.0f}",
            }
            return
        packages = max(int(self.risk_usd / debit_1), 1)
        while packages > 1 and debit_1 * packages > self.risk_usd:
            packages -= 1
        entry_debit = debit_1 * packages
        # Pay debit
        self.account_balance_usd -= entry_debit
        self.active_position = {
            "symbol": self.underlying,
            "option_symbol": ",".join(
                f"{L['qty']}x{L['symbol']}" for L in priced["legs"]
            ),
            "shares": packages,
            "contracts": packages,
            "packages": packages,
            "side": "LONG",
            "direction": self.pattern_name,
            "entry": round(debit_ps, 4),
            "entry_premium": round(debit_ps, 4),
            "entry_debit_usd": round(entry_debit, 2),
            "stop": 0.0,
            "target": round(debit_ps * (1.0 + self.take_profit_pct / 100.0), 4),
            "strike": float(round(spot)),
            "legs": priced["legs"],
            "expiration": priced["expiration"],
            "entry_time": bar_time,
            "spy_entry": spot,
        }
        self._entered_week = week_key
        self.signal_state = {
            "status": "IN_POSITION",
            "note": f"entered {packages} pkg debit ${entry_debit:.2f} TP +{int(self.take_profit_pct)}%",
        }
        self.net_liquidation_usd = self.account_balance_usd + entry_debit
        log.info(
            "[%s] ENTRY: %d pkg @ $%.2f debit | TP +%.0f%% | exp %s",
            self.pattern_name, packages, entry_debit, self.take_profit_pct, priced["expiration"],
        )


def build_default_asym_ledgers(underlying: str = "SPY", capital: float = 10000.0) -> dict:
    return {
        "put_butterfly": AsymOptionsLedger(
            "LONG_PUT_BUTTERFLY", build_long_put_butterfly, 200.0, underlying, capital
        ),
        "call_butterfly": AsymOptionsLedger(
            "LONG_CALL_BUTTERFLY", build_long_call_butterfly, 100.0, underlying, capital
        ),
        "put_ladder": AsymOptionsLedger(
            "PUT_LADDER_DEFINED", build_put_ladder_defined, 150.0, underlying, capital
        ),
    }
