"""
F3 SPY 0DTE — Pattern Lab / Options Engine paper ledger.

Rules (from Directive F3 real-options backtest):
  1. Premarket direction (4:00–9:30 ET): last close vs first open
  2. Opening range 9:30–9:44 ET
  3. After 9:45: breakout in SAME direction as premarket
  4. Buy ATM CALL (up) or ATM PUT (down) — long options only
  5. Exit at 16:00 ET — no stop, no target
  6. Size: contracts = 200 / (entry_premium * 100)

Live premiums: Tradier options chain mid/last when available; otherwise
entry is deferred (WAITING_PREMIUM). No synthetic leverage formula.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests

log = logging.getLogger("aim_f3")

ET = ZoneInfo("America/New_York")
TRADE_NOTIONAL_USD = 200.0
ORB_END = "09:44"
ENTRY_FROM = "09:45"
SESSION_END = "16:00"
THIN_EXIT_TX_THRESHOLD = 3


def _atm_option_symbol(underlying: str, expiration: date, strike: float, is_call: bool) -> str:
    """OCC-style symbol used by Tradier (no O: prefix)."""
    strike_i = int(round(strike))
    # Tradier symbol: SPY250806C00500000 style
    return (
        f"{underlying.upper()}{expiration.strftime('%y%m%d')}"
        f"{'C' if is_call else 'P'}{strike_i * 1000:08d}"
    )


def _polygon_option_ticker(underlying: str, expiration: date, strike: float, is_call: bool) -> str:
    strike_i = int(round(strike))
    return (
        f"O:{underlying.upper()}{expiration.strftime('%y%m%d')}"
        f"{'C' if is_call else 'P'}{strike_i * 1000:08d}"
    )


def fetch_premarket_direction(symbol: str = "SPY") -> Optional[int]:
    """
    +1 if premarket closed above its open, -1 if below.
    Uses Tradier timesales with session_filter=all, 4:00–9:29 ET.
    Returns None if unavailable (do not guess).
    """
    token = os.getenv("TRADIER_API_TOKEN_2") or os.getenv("TRADIER_API_TOKEN", "")
    if not token:
        return None
    try:
        now = datetime.now(ET)
        start = now.replace(hour=4, minute=0, second=0, microsecond=0)
        end = now.replace(hour=9, minute=29, second=0, microsecond=0)
        r = requests.get(
            "https://api.tradier.com/v1/markets/timesales",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={
                "symbol": symbol,
                "interval": "1min",
                "start": start.strftime("%Y-%m-%d %H:%M"),
                "end": end.strftime("%Y-%m-%d %H:%M"),
                "session_filter": "all",
            },
            timeout=15,
        )
        r.raise_for_status()
        series = (r.json().get("series") or {}).get("data") or []
        if not isinstance(series, list) or len(series) < 2:
            return None
        first_open = float(series[0].get("open") or series[0].get("price") or 0)
        last_close = float(series[-1].get("close") or series[-1].get("price") or 0)
        if first_open <= 0 or last_close <= 0:
            return None
        return 1 if last_close > first_open else -1
    except Exception as e:
        log.warning("[F3] premarket fetch failed: %s", e)
        return None


def fetch_atm_premium(symbol: str, spot: float, is_call: bool,
                      expiration: Optional[date] = None) -> Optional[dict]:
    """
    ATM option premium via Tradier chain (preferred for live).
    Returns {premium, strike, option_symbol, source, bid, ask} or None.
    """
    token = os.getenv("TRADIER_API_TOKEN_2") or os.getenv("TRADIER_API_TOKEN", "")
    if not token:
        return None
    expiration = expiration or datetime.now(ET).date()
    exp_s = expiration.strftime("%Y-%m-%d")
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
        want = "call" if is_call else "put"
        strike_tgt = float(round(spot))
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
            diff = abs(k - strike_tgt)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best = o
        if not best:
            return None
        bid = float(best.get("bid") or 0)
        ask = float(best.get("ask") or 0)
        last = float(best.get("last") or 0)
        if bid > 0 and ask > 0:
            prem = (bid + ask) / 2.0
        elif last > 0:
            prem = last
        elif ask > 0:
            prem = ask
        else:
            return None
        # Sanity: ATM 0DTE premium should be a small fraction of spot (reject garbage/wrong exp).
        if prem <= 0 or prem > max(5.0, spot * 0.20):
            log.warning("[F3] rejecting implausible premium %.3f at spot %.2f for %s",
                        prem, spot, best.get("symbol"))
            return None
        return {
            "premium": prem,
            "strike": float(best.get("strike")),
            "option_symbol": best.get("symbol") or _atm_option_symbol(
                symbol, expiration, float(best.get("strike")), is_call),
            "source": "tradier_chain",
            "bid": bid,
            "ask": ask,
            "n_tx": None,
        }
    except Exception as e:
        log.warning("[F3] Tradier premium fetch failed: %s", e)
        return None


class F3OptionsLedger:
    """Independent F3 paper ledger — $200 notional ATM 0DTE, exit 16:00."""

    def __init__(self, underlying: str = "SPY",
                 starting_capital_usd: float = 10000.0,
                 trade_notional_usd: float = TRADE_NOTIONAL_USD):
        self.pattern_name = "F3_SPY_0DTE"
        self.underlying = underlying
        self._starting_capital = starting_capital_usd
        self.account_balance_usd = starting_capital_usd
        self.net_liquidation_usd = starting_capital_usd
        self.trade_notional_usd = trade_notional_usd
        self.active_position: Optional[dict] = None
        self.signal_state: dict = {"status": "IDLE"}
        self.trade_log: list = []
        self.wins = 0
        self.losses = 0
        self._day_key: Optional[str] = None
        self._pm_direction: Optional[int] = None
        self._orb_high: Optional[float] = None
        self._orb_low: Optional[float] = None
        self._entered_today = False

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
                "premarket_filter": True,
                "orb": "09:30-09:44 ET",
                "entry": "breakout with PM direction → ATM long call/put",
                "exit": "16:00 ET, no stop/target",
                "notional_usd": self.trade_notional_usd,
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
            "pm_direction": (
                "UP" if self._pm_direction == 1
                else ("DOWN" if self._pm_direction == -1 else None)
            ),
            "orb_high": self._orb_high,
            "orb_low": self._orb_low,
            "recent_trades": self.trade_log[-10:],
        }

    def _reset_day(self, day_key: str):
        self._day_key = day_key
        self._pm_direction = None
        self._orb_high = None
        self._orb_low = None
        self._entered_today = False
        if not self.active_position:
            self.signal_state = {"status": "NEW_DAY", "day": day_key}

    def _close(self, exit_premium: float, reason: str, meta: Optional[dict] = None):
        pos = self.active_position
        if not pos:
            return
        # Entry already debited cash (premium paid). Credit exit proceeds now.
        exit_proceeds = float(exit_premium) * 100.0 * float(pos["contracts"])
        debit = float(pos["entry_premium"]) * 100.0 * float(pos["contracts"])
        pnl = exit_proceeds - debit
        self.account_balance_usd += exit_proceeds
        result = "WIN" if pnl > 0 else "LOSS"
        if result == "WIN":
            self.wins += 1
        else:
            self.losses += 1
        thin = bool((meta or {}).get("thin_exit"))
        row = {
            "symbol": pos.get("option_symbol"),
            "direction": pos.get("direction"),
            "side": pos.get("direction"),
            "entry": pos["entry_premium"],
            "exit": exit_premium,
            "shares": pos["contracts"],  # PatternCard compatibility
            "contracts": pos["contracts"],
            "strike": pos.get("strike"),
            "pnl_usd": round(pnl, 2),
            "result": result,
            "reason": reason,
            "thin_exit": thin,
            "premium_source": pos.get("premium_source"),
        }
        self.trade_log.append(row)
        log.info(
            "[F3] %s (%s): %s %s @ %.3f -> %.3f | P&L $%.2f | Bal $%.2f%s",
            result, reason, pos.get("direction"), pos.get("option_symbol"),
            pos["entry_premium"], exit_premium, pnl, self.account_balance_usd,
            " [THIN_EXIT]" if thin else "",
        )
        self.active_position = None
        self.signal_state = {"status": "FLAT", "last_result": result, "last_pnl": round(pnl, 2)}
        self.net_liquidation_usd = self.account_balance_usd

    def evaluate(self, today_dataframe: pd.DataFrame,
                 premarket_direction: Optional[int] = None):
        """Evaluate latest RTH bars. Optional explicit PM direction override."""
        if today_dataframe is None or getattr(today_dataframe, "empty", True):
            return
        df = today_dataframe.sort_index()
        if not isinstance(df.index, pd.DatetimeIndex):
            return
        if df.index.tz is None:
            df.index = df.index.tz_localize(ET)
        else:
            df.index = df.index.tz_convert(ET)

        latest = df.iloc[-1]
        bar_time = latest.name.strftime("%H:%M") if hasattr(latest.name, "strftime") else None
        day_key = latest.name.strftime("%Y-%m-%d") if hasattr(latest.name, "strftime") else None
        if not day_key or not bar_time:
            return

        if day_key != self._day_key:
            # Flatten any leftover overnight (should not happen for 0DTE)
            if self.active_position:
                self._close(self.active_position["entry_premium"], "DAY_ROLL_FLATTEN")
            self._reset_day(day_key)

        # Premarket direction once per day
        if self._pm_direction is None:
            if premarket_direction in (1, -1):
                self._pm_direction = premarket_direction
            else:
                # Prefer bars in df before 09:30 if present
                try:
                    pm = df.between_time("04:00", "09:29")
                    if len(pm) >= 2:
                        fo = float(pm.iloc[0]["open"])
                        lc = float(pm.iloc[-1]["close"])
                        if fo > 0 and lc > 0:
                            self._pm_direction = 1 if lc > fo else -1
                except Exception:
                    pass
            if self._pm_direction is None:
                self._pm_direction = fetch_premarket_direction(self.underlying)
            if self._pm_direction is None:
                self.signal_state = {"status": "NO_PREMARKET", "day": day_key}
                return
            self.signal_state = {
                "status": "PM_SET",
                "pm_direction": "UP" if self._pm_direction == 1 else "DOWN",
            }

        # Opening range once we have 09:30–09:44 bars
        if self._orb_high is None or self._orb_low is None:
            orb = df.between_time("09:30", ORB_END)
            if orb.empty:
                self.signal_state = {**self.signal_state, "status": "WAITING_ORB"}
                return
            self._orb_high = float(orb["high"].max())
            self._orb_low = float(orb["low"].min())
            self.signal_state = {
                "status": "ORB_SET",
                "pm_direction": "UP" if self._pm_direction == 1 else "DOWN",
                "orb_high": self._orb_high,
                "orb_low": self._orb_low,
            }

        # Manage open position — mark / EOD exit
        if self.active_position:
            spot = float(latest["close"])
            is_call = self.active_position["direction"] == "CALL"
            exp = date.fromisoformat(day_key)
            mark = fetch_atm_premium(self.underlying, spot, is_call, exp)
            if mark and mark["premium"] > 0:
                mark_val = mark["premium"] * 100.0 * float(self.active_position["contracts"])
                self.net_liquidation_usd = self.account_balance_usd + mark_val
                self.active_position["mark_premium"] = mark["premium"]
                floating = mark_val - (
                    float(self.active_position["entry_premium"])
                    * 100.0
                    * float(self.active_position["contracts"])
                )
                self.active_position["unrealized_pnl"] = round(floating, 2)
            if bar_time >= SESSION_END:
                exit_prem = (mark or {}).get("premium") or self.active_position.get("mark_premium") or self.active_position["entry_premium"]
                thin = False
                if mark and mark.get("n_tx") is not None:
                    thin = int(mark["n_tx"]) <= THIN_EXIT_TX_THRESHOLD
                self._close(float(exit_prem), "EOD_16:00", {"thin_exit": thin})
            return

        if self._entered_today:
            return

        if bar_time < ENTRY_FROM:
            self.signal_state = {
                "status": "WAITING_BREAKOUT",
                "pm_direction": "UP" if self._pm_direction == 1 else "DOWN",
                "orb_high": self._orb_high,
                "orb_low": self._orb_low,
            }
            return

        spot = float(latest["close"])
        fired = False
        is_call = False
        if self._pm_direction == 1 and spot > float(self._orb_high):
            fired, is_call = True, True
        elif self._pm_direction == -1 and spot < float(self._orb_low):
            fired, is_call = True, False

        if not fired:
            self.signal_state = {
                "status": "WAITING_BREAKOUT",
                "pm_direction": "UP" if self._pm_direction == 1 else "DOWN",
                "orb_high": self._orb_high,
                "orb_low": self._orb_low,
                "last_close": spot,
            }
            return

        exp = date.fromisoformat(day_key)
        quote = fetch_atm_premium(self.underlying, spot, is_call, exp)
        if not quote or quote["premium"] <= 0:
            self.signal_state = {
                "status": "WAITING_PREMIUM",
                "direction": "CALL" if is_call else "PUT",
                "spy_entry": spot,
                "note": "breakout fired; ATM premium unavailable — not entering on synthetic",
            }
            return

        contracts = self.trade_notional_usd / (quote["premium"] * 100.0)
        if contracts <= 0:
            return

        self.active_position = {
            "symbol": quote["option_symbol"],
            "option_symbol": quote["option_symbol"],
            "shares": round(contracts, 4),  # PatternCard field
            "contracts": round(contracts, 4),
            "side": "LONG",
            "direction": "CALL" if is_call else "PUT",
            "entry": quote["premium"],
            "entry_premium": quote["premium"],
            "stop": 0.0,   # none — UI shows 0
            "target": 0.0,  # none — exit 16:00
            "strike": quote["strike"],
            "spy_entry": spot,
            "premium_source": quote["source"],
            "orb_high": self._orb_high,
            "orb_low": self._orb_low,
            "entry_time": bar_time,
        }
        self._entered_today = True
        self.signal_state = {"status": "IN_POSITION", "direction": self.active_position["direction"]}
        # Debit notional from cash (premium paid)
        debit = quote["premium"] * 100.0 * contracts
        self.account_balance_usd -= debit
        self.net_liquidation_usd = self.account_balance_usd + debit  # long premium = asset
        log.info(
            "[F3] ENTRY: LONG %s %s @ %.3f x %.3f ctr | SPY %.2f | debit $%.2f",
            self.active_position["direction"], quote["option_symbol"],
            quote["premium"], contracts, spot, debit,
        )
