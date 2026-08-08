#!/usr/bin/env python3
"""
Asymmetric SPY paper strategies — Pattern Lab / OE Strategies.

Joel's top patterns (weekdays, 2y, no stop) — wired for live-Tradier NBBO paper practice:
  1. Narrow-wing call butterfly — TP +300% of |entry|
  2. Long put butterfly         — TP +275% of |entry|
  3. Long call butterfly        — TP +275% of |entry|
  4. Put ladder (defined)       — TP +300% of |entry|
  5. Long call condor           — TP +300% of |entry|
  6. Long put condor            — TP +300% of |entry|
Extra (catalog):
  7. Bullish risk reversal      — TP +75% of |entry| (credit, cash-secured)

Paper practice defaults:
  - Pricing: live Tradier NBBO (long@ask / short@bid) — NO live orders
  - Fees: $0.65 per contract per leg per side
  - Set ASYM_PAPER_PRICING=polygon_daily for historical BT-parity fills
  - Risk budget $500 debit max; Mon–Fri from 09:30 ET; flatten 15:30 expiry Friday
  - NO stop loss
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta
from typing import Any, Optional, Callable
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger("aim_asym")

ET = ZoneInfo("America/New_York")
RISK_USD = 500.0
# First RTH bar — matches spy_asymmetric_bt weekday entry (~ open)
ENTRY_AFTER = "09:30"
# Matches spy_asymmetric_bt.py docstring L12
FLATTEN_TIME = "15:30"
POLYGON_BASE = "https://api.polygon.io"
RATE_SLEEP = float(os.environ.get("ASYM_PAPER_RATE_SLEEP", "0.12"))
# Live Tradier NBBO paper fills (default). Set ASYM_PAPER_PRICING=polygon_daily for BT-parity.
ASYM_PAPER_PRICING = (os.environ.get("ASYM_PAPER_PRICING") or "tradier_nbbo").strip().lower()

# strategy key -> ledger (must match aiem_paper_trades.strategy filter)
STRATEGY_KEYS = (
    "put_butterfly",
    "call_butterfly",
    "put_ladder",
    "call_condor",
    "put_condor",
    "narrow_wing_butterfly",
    "bullish_risk_reversal",
)

# Cash-secured SPY short put needs ~strike×100; keep a dedicated paper book.
RR_PAPER_CAPITAL_USD = float(os.environ.get("ASYM_RR_PAPER_CAPITAL", "100000"))

# Condors use Joel's fixed BT TP (+300%). Dynamic plateau helper kept for tools/backtests.
MAX_PLATEAU_PAYOFF_USD = 500.00
SAFETY_MARGIN = 0.80
DYNAMIC_PLATEAU_TP_STRATEGIES = frozenset()  # empty — Joel list uses fixed TP% for all six

# Joel signed TP targets (weekdays / 2y / no-stop ranking)
JOEL_ASYM_TP_PCT = {
    "narrow_wing_butterfly": 300.0,
    "put_butterfly": 275.0,
    "call_butterfly": 275.0,
    "put_ladder": 300.0,
    "call_condor": 300.0,
    "put_condor": 300.0,
    "bullish_risk_reversal": 75.0,
}


def dynamic_tp_pct(entry_debit_usd: float) -> float:
    """TP% of |entry| = SAFETY_MARGIN × max reachable % given $500 plateau."""
    d = float(entry_debit_usd)
    if d <= 0:
        raise ValueError(f"dynamic_tp_pct requires debit > 0, got {d}")
    if d >= MAX_PLATEAU_PAYOFF_USD:
        raise ValueError(
            f"dynamic_tp_pct: debit ${d:.2f} ≥ plateau ${MAX_PLATEAU_PAYOFF_USD:.2f}"
        )
    max_reachable_pct = (MAX_PLATEAU_PAYOFF_USD - d) / d * 100.0
    return SAFETY_MARGIN * max_reachable_pct



def _api_key() -> str:
    return os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY") or ""


def _poly_get(path: str, params: Optional[dict] = None) -> dict:
    key = _api_key()
    if not key:
        return {"status": "ERROR", "error": "POLYGON_API_KEY not set", "results": []}
    params = dict(params or {})
    params["apiKey"] = key
    url = f"{POLYGON_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "aiem-asym-paper/1.0"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(min(30, 2 ** attempt + 1))
                continue
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            return {"status": "ERROR", "error": f"HTTP {e.code}: {body}", "results": []}
        except Exception as e:
            if attempt < 3:
                time.sleep(0.8 * (attempt + 1))
                continue
            return {"status": "ERROR", "error": str(e), "results": []}
    return {"status": "ERROR", "error": "rate_limited", "results": []}


def next_friday(d: date, weeks_ahead: int = 0) -> date:
    """Friday on or after d, plus weeks_ahead — identical to spy_asymmetric_bt.next_friday."""
    add = (4 - d.weekday()) % 7
    fri = d + timedelta(days=add)
    if fri <= d and weeks_ahead == 0:
        fri = fri + timedelta(days=7)
    fri = fri + timedelta(weeks=weeks_ahead)
    return fri


def _occ(underlying: str, strike: float, right: str, exp: date) -> str:
    """OCC option ticker O:SPYYYMMDD[C|P]######## — same as BT."""
    cp = "C" if right.upper().startswith("C") else "P"
    sk8 = f"{int(round(strike * 1000)):08d}"
    return f"O:{underlying.upper()}{exp.strftime('%y%m%d')}{cp}{sk8}"


# Short TTL cache: (occ_symbol, day_iso, require_exact) -> premium close or None
_OPT_PX_CACHE: dict[tuple[str, str, bool], tuple[float, Optional[float]]] = {}
_OPT_PX_TTL = 300.0  # 5 min


def fetch_option_daily_close(
    occ_symbol: str,
    asof: date,
    *,
    require_exact: bool = False,
) -> Optional[float]:
    """
    Polygon daily option aggregate close on/asof `asof` (same source as BT).
    Uses /v2/aggs/ticker/{O:…}/range/1/day — lookback 10 calendar days for asof.

    require_exact=True: only accept a bar dated exactly `asof` (no prior-day
    lookback). Used on entry so live fills match BT asof that session day
    instead of prior-session premiums at 09:30 before today's daily settles.
    """
    cache_key = (occ_symbol, asof.isoformat(), bool(require_exact))
    now = time.time()
    hit = _OPT_PX_CACHE.get(cache_key)
    if hit and (now - hit[0]) < _OPT_PX_TTL:
        return hit[1]

    start = asof - timedelta(days=10)
    sym = urllib.parse.quote(occ_symbol)
    data = _poly_get(
        f"/v2/aggs/ticker/{sym}/range/1/day/{start.isoformat()}/{asof.isoformat()}",
        {"adjusted": "false", "sort": "asc", "limit": 50},
    )
    time.sleep(RATE_SLEEP)
    rows = data.get("results") or []
    px: Optional[float] = None
    if rows:
        best_d = None
        for row in rows:
            try:
                d = datetime.fromtimestamp(row["t"] / 1000.0, tz=ET).date()
                c = float(row["c"])
            except (KeyError, TypeError, ValueError):
                continue
            if d > asof or c <= 0:
                continue
            if require_exact and d != asof:
                continue
            if best_d is None or d >= best_d:
                best_d = d
                px = c
    _OPT_PX_CACHE[cache_key] = (now, px)
    return px


def package_value_polygon(
    underlying: str,
    expiration: date,
    legs: list[tuple[int, str, float]],
    asof: date,
    *,
    require_exact: bool = False,
) -> Optional[float]:
    """
    Mark package in dollars (qty * premium * 100), Polygon daily closes.
    legs: (qty_signed, 'call'|'put', strike). Returns None if any leg missing.
    """
    total = 0.0
    for qty, right, strike in legs:
        occ = _occ(underlying, float(strike), right, expiration)
        px = fetch_option_daily_close(occ, asof, require_exact=require_exact)
        if px is None or px <= 0:
            return None
        total += int(qty) * float(px) * 100.0
    return total  # dollars for 1 package; caller scales by packages


def price_legs_polygon(
    underlying: str,
    expiration: date,
    legs: list[tuple[int, str, float]],
    asof: date,
    *,
    require_exact: bool = False,
) -> Optional[dict]:
    """Entry pricing via Polygon daily — returns debit_per_share + legs."""
    total_usd = package_value_polygon(
        underlying, expiration, legs, asof, require_exact=require_exact
    )
    if total_usd is None:
        return None
    # Rebuild legs with premiums for storage
    priced = []
    for qty, right, strike in legs:
        occ = _occ(underlying, float(strike), right, expiration)
        px = fetch_option_daily_close(occ, asof, require_exact=require_exact)
        if px is None:
            return None
        priced.append({
            "qty": int(qty),
            "right": right.lower(),
            "strike": float(strike),
            "premium": float(px),
            "symbol": occ,
            "bid": float(px),
            "ask": float(px),
        })
    return {
        "debit_per_share": total_usd / 100.0,
        "legs": priced,
        "expiration": expiration.isoformat(),
        "pricing_source": "polygon_daily_option_aggs",
        "asof": asof.isoformat(),
        "require_exact": bool(require_exact),
    }


def price_legs_tradier_nbbo(
    underlying: str,
    expiration: date,
    legs: list[tuple[int, str, float]],
    *,
    packages: int = 1,
    for_exit: bool = False,
) -> Optional[dict]:
    """Live Tradier ask/bid package pricing for paper practice (no live orders)."""
    try:
        from aiem_broker.paper_fills import price_package_nbbo
    except Exception as e:
        log.warning("[%s] tradier paper_fills unavailable: %s", underlying, e)
        return None
    packed = price_package_nbbo(
        underlying,
        expiration,
        legs,
        packages=packages,
        for_exit=for_exit,
        include_fees=True,
    )
    if not packed:
        return None
    # Normalize to price_legs_polygon-compatible shape + fee fields
    return {
        "debit_per_share": float(packed["debit_per_share"]),
        "legs": packed["legs"],
        "expiration": packed["expiration"],
        "pricing_source": "tradier_nbbo_paper",
        "asof": date.today().isoformat(),
        "require_exact": False,
        "net_usd": packed["net_usd"],
        "fees_usd": packed["fees_usd"],
        "net_usd_after_fees": packed["net_usd_after_fees"],
        "contract_count": packed["contract_count"],
        "packages": packed["packages"],
        "for_exit": for_exit,
        "live_order_sent": False,
        "simulated": True,
    }


def price_legs_for_paper(
    underlying: str,
    expiration: date,
    legs: list[tuple[int, str, float]],
    asof: date,
    *,
    require_exact: bool = False,
    packages: int = 1,
    for_exit: bool = False,
) -> Optional[dict]:
    """Unified paper pricing — Tradier NBBO by default, Polygon daily optional."""
    if ASYM_PAPER_PRICING in ("tradier_nbbo", "tradier", "nbbo", "live"):
        priced = price_legs_tradier_nbbo(
            underlying, expiration, legs, packages=packages, for_exit=for_exit
        )
        if priced:
            return priced
        log.warning(
            "[%s] Tradier NBBO unavailable — falling back to Polygon daily for paper",
            underlying,
        )
    return price_legs_polygon(
        underlying, expiration, legs, asof, require_exact=require_exact
    )


def package_value_for_paper(
    underlying: str,
    expiration: date,
    legs: list[tuple[int, str, float]],
    asof: date,
    *,
    require_exact: bool = False,
    packages: int = 1,
    for_exit: bool = True,
) -> Optional[float]:
    """Mark package in dollars (1×packages). Prefer Tradier NBBO."""
    if ASYM_PAPER_PRICING in ("tradier_nbbo", "tradier", "nbbo", "live"):
        priced = price_legs_tradier_nbbo(
            underlying, expiration, legs, packages=packages, for_exit=for_exit
        )
        if priced is not None:
            return float(priced["net_usd"])
    return package_value_polygon(
        underlying, expiration, legs, asof, require_exact=require_exact
    )


def build_long_put_butterfly(spot: float) -> list[tuple[int, str, float]]:
    k = float(round(spot))
    return [(1, "put", k + 5), (-2, "put", k), (1, "put", k - 5)]


def build_long_call_butterfly(spot: float) -> list[tuple[int, str, float]]:
    k = float(round(spot))
    return [(1, "call", k - 5), (-2, "call", k), (1, "call", k + 5)]


def build_put_ladder_defined(spot: float) -> list[tuple[int, str, float]]:
    k = float(round(spot))
    return [(1, "put", k), (-1, "put", k - 5), (-1, "put", k - 10), (1, "put", k - 15)]


def build_long_call_condor(spot: float) -> list[tuple[int, str, float]]:
    """ATM ±5 / ±10 long call condor — plateau $500; TP set dynamically at entry."""
    k = float(round(spot))
    return [
        (1, "call", k - 10),
        (-1, "call", k - 5),
        (-1, "call", k + 5),
        (1, "call", k + 10),
    ]


def build_long_put_condor(spot: float) -> list[tuple[int, str, float]]:
    """ATM ±5 / ±10 long put condor — plateau $500; TP set dynamically at entry."""
    k = float(round(spot))
    return [
        (1, "put", k + 10),
        (-1, "put", k + 5),
        (-1, "put", k - 5),
        (1, "put", k - 10),
    ]


def build_narrow_wing_call_butterfly(spot: float) -> list[tuple[int, str, float]]:
    """ATM ±2 call butterfly — catalog Narrow-Wing Butterfly (+200% TP winner)."""
    k = float(round(spot))
    return [(1, "call", k - 2), (-2, "call", k), (1, "call", k + 2)]


def build_bullish_risk_reversal(spot: float) -> list[tuple[int, str, float]]:
    """Long OTM call + short OTM put — catalog Bullish Risk Reversal (+75% TP)."""
    k = float(round(spot))
    return [(1, "call", k + 5), (-1, "put", k - 5)]


def _short_put_collateral_usd(legs: list[dict], packages: int) -> float:
    """Cash-secured put collateral = sum(short put strike * 100 * |qty|) * packages."""
    total = 0.0
    for L in legs:
        if str(L.get("right", "")).lower() != "put":
            continue
        qty = int(L.get("qty") or 0)
        if qty >= 0:
            continue
        total += float(L["strike"]) * 100.0 * abs(qty) * packages
    return total


def _db_url() -> str:
    return (
        os.environ.get("DATABASE_URL")
        or os.environ.get("NEON_DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or ""
    )


def _ensure_strategy_column(cur) -> None:
    cur.execute(
        "ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS strategy TEXT"
    )


def persist_asym_paper_open(
    *,
    strategy: str,
    underlying: str,
    entry_debit_usd: float,
    packages: int,
    expiration: str,
    legs: list,
    entry_premium_ps: float,
    take_profit_pct: float,
) -> Optional[int]:
    """INSERT OPEN row into aiem_paper_trades. Returns id or None."""
    dsn = _db_url()
    if not dsn:
        log.warning("[asym] DATABASE_URL unset — skip aiem_paper_trades INSERT")
        return None
    # Distinct ticker per strategy so ticker+trade_date unique allows 3 same-day rows
    ticker = f"SPY:{strategy}"
    detail = (
        f"asym paper {strategy} TP+{take_profit_pct:.0f}% no-stop "
        f"exp={expiration} legs={legs!r}"
    )[:500]
    try:
        import psycopg2
        with psycopg2.connect(dsn, connect_timeout=8) as conn, conn.cursor() as cur:
            _ensure_strategy_column(cur)
            cur.execute(
                """
                INSERT INTO aiem_paper_trades
                    (trade_date, ticker, trade_type, direction,
                     entry_price, quantity, notional,
                     signal_source, signal_detail, hold_days_max,
                     last_price, status, strike, expiry,
                     option_entry_mid, strategy, fill_price)
                VALUES (
                    (NOW() AT TIME ZONE 'America/New_York')::date,
                    %s, 'OPTIONS_PACKAGE', %s,
                    %s, %s, %s,
                    %s, %s, 21,
                    %s, 'OPEN', %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT ON CONSTRAINT aiem_paper_trades_ticker_date_unique DO NOTHING
                RETURNING id
                """,
                (
                    ticker,
                    "CREDIT_PACKAGE" if float(entry_debit_usd) < 0 else "DEBIT_PACKAGE",
                    float(entry_premium_ps),
                    float(packages),
                    float(entry_debit_usd),
                    strategy,
                    detail,
                    float(entry_premium_ps),
                    float(round(float(legs[0]["strike"]))) if legs else None,
                    expiration,
                    float(entry_premium_ps),
                    strategy,
                    float(entry_premium_ps),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            if row:
                log.info("[asym] aiem_paper_trades OPEN id=%s strategy=%s", row[0], strategy)
                return int(row[0])
            # Conflict: fetch existing open id
            cur.execute(
                """
                SELECT id FROM aiem_paper_trades
                WHERE ticker=%s AND trade_date=(NOW() AT TIME ZONE 'America/New_York')::date
                ORDER BY id DESC LIMIT 1
                """,
                (ticker,),
            )
            ex = cur.fetchone()
            return int(ex[0]) if ex else None
    except Exception as e:
        log.warning("[asym] aiem_paper_trades OPEN failed: %s", e)
        return None


def persist_asym_paper_close(
    *,
    paper_trade_id: Optional[int],
    strategy: str,
    exit_value_usd: float,
    pnl_usd: float,
    reason: str,
) -> None:
    """Close OPEN aiem_paper_trades row for this asym package."""
    dsn = _db_url()
    if not dsn:
        return
    try:
        import psycopg2
        with psycopg2.connect(dsn, connect_timeout=8) as conn, conn.cursor() as cur:
            _ensure_strategy_column(cur)
            if paper_trade_id:
                cur.execute(
                    """
                    UPDATE aiem_paper_trades
                    SET status='CLOSED',
                        exit_price=%s,
                        exit_date=(NOW() AT TIME ZONE 'America/New_York')::date,
                        pnl=%s,
                        pnl_pct=CASE WHEN notional IS NOT NULL AND notional<>0
                                     THEN (%s / ABS(notional)) * 100.0 ELSE NULL END,
                        last_price=%s,
                        exit_reason=%s,
                        updated_at=NOW()
                    WHERE id=%s AND status='OPEN'
                    """,
                    (
                        float(exit_value_usd),
                        float(pnl_usd),
                        float(pnl_usd),
                        float(exit_value_usd),
                        reason[:200],
                        int(paper_trade_id),
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE aiem_paper_trades
                    SET status='CLOSED',
                        exit_price=%s,
                        exit_date=(NOW() AT TIME ZONE 'America/New_York')::date,
                        pnl=%s,
                        last_price=%s,
                        exit_reason=%s,
                        updated_at=NOW()
                    WHERE id = (
                        SELECT id FROM aiem_paper_trades
                        WHERE strategy=%s AND status='OPEN'
                        ORDER BY id DESC LIMIT 1
                    )
                    """,
                    (
                        float(exit_value_usd),
                        float(pnl_usd),
                        float(exit_value_usd),
                        reason[:200],
                        strategy,
                    ),
                )
            conn.commit()
            log.info(
                "[asym] aiem_paper_trades CLOSED strategy=%s id=%s pnl=%.2f reason=%s",
                strategy, paper_trade_id, pnl_usd, reason,
            )
    except Exception as e:
        log.warning("[asym] aiem_paper_trades CLOSE failed: %s", e)


class AsymOptionsLedger:
    """Multi-leg package paper ledger — Mon–Fri RTH open, TP%, no stop.

    Default pricing: live Tradier NBBO paper fills (ask entry / bid exit) + $0.65/leg fees.
    Set ASYM_PAPER_PRICING=polygon_daily for historical BT-parity fills.
    """

    def __init__(
        self,
        pattern_name: str,
        builder: Callable[[float], list],
        take_profit_pct: float,
        strategy_key: str,
        underlying: str = "SPY",
        starting_capital_usd: float = 10000.0,
        risk_usd: float = RISK_USD,
        allow_credit: bool = False,
        cash_secured: bool = False,
    ):
        self.pattern_name = pattern_name
        self.strategy_key = strategy_key
        self.builder = builder
        self.take_profit_pct = take_profit_pct
        self.underlying = underlying
        self._starting_capital = starting_capital_usd
        self.account_balance_usd = starting_capital_usd
        self.net_liquidation_usd = starting_capital_usd
        self.risk_usd = risk_usd
        self.allow_credit = allow_credit
        self.cash_secured = cash_secured
        self.active_position: Optional[dict] = None
        self.signal_state: dict = {"status": "IDLE"}
        self.trade_log: list = []
        self.wins = 0
        self.losses = 0
        self._day_key: Optional[str] = None
        self._entered_day: Optional[str] = None
        self._reserved_collateral_usd = 0.0

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
            "strategy": self.strategy_key,
            "rules": {
                "entry": (
                    "Mon–Fri from 09:30 ET when flat; fill = live Tradier NBBO "
                    "(long@ask / short@bid) + $0.65/contract/leg paper fees "
                    f"[mode={ASYM_PAPER_PRICING}]"
                ),
                "structure": self.pattern_name,
                "risk_usd": self.risk_usd,
                "take_profit_pct": self.take_profit_pct,
                "take_profit_mode": (
                    "dynamic_plateau_80pct"
                    if self.strategy_key in DYNAMIC_PLATEAU_TP_STRATEGIES
                    else "fixed_pct"
                ),
                "max_plateau_payoff_usd": (
                    MAX_PLATEAU_PAYOFF_USD
                    if self.strategy_key in DYNAMIC_PLATEAU_TP_STRATEGIES
                    else None
                ),
                "stop_loss": None,
                "pricing": (
                    "Tradier NBBO paper (no live orders)"
                    if ASYM_PAPER_PRICING in ("tradier_nbbo", "tradier", "nbbo", "live")
                    else "Polygon daily option aggregates"
                ),
                "allow_credit": self.allow_credit,
                "cash_secured": self.cash_secured,
                "live_orders": False,
                "exit": (
                    (
                        f"dynamic TP = {SAFETY_MARGIN:.0%} of max reachable "
                        f"vs ${MAX_PLATEAU_PAYOFF_USD:.0f} plateau (set at entry) or "
                    )
                    if self.strategy_key in DYNAMIC_PLATEAU_TP_STRATEGIES
                    else f"+{self.take_profit_pct:.0f}% of |entry| premium or "
                )
                + "flatten 15:30 on expiry Friday (Tradier NBBO bid/ask mark)",
            },
            "reserved_collateral_usd": round(self._reserved_collateral_usd, 2),
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

    def _mark_package(self, expiration: date, legs: list, asof: date, packages: int = 1) -> Optional[float]:
        spec = [(int(L["qty"]), str(L["right"]), float(L["strike"])) for L in legs]
        pkgs = int(packages or 1)
        return package_value_for_paper(
            self.underlying,
            expiration,
            spec,
            asof,
            packages=pkgs,
            for_exit=True,
        )

    def _free_cash_usd(self) -> float:
        return float(self.account_balance_usd) - float(self._reserved_collateral_usd)

    def _close(self, exit_value_usd: float, reason: str, *, exit_fees_usd: float = 0.0):
        pos = self.active_position
        if not pos:
            return
        entry_usd = float(pos["entry_debit_usd"])
        # Proceeds after exit-side commission (longs sold at bid, shorts covered at ask)
        exit_net = float(exit_value_usd) - float(exit_fees_usd or 0.0)
        pnl = exit_net - entry_usd
        self.account_balance_usd += exit_net
        coll = float(pos.get("collateral_usd") or 0.0)
        if coll > 0:
            self._reserved_collateral_usd = max(
                0.0, float(self._reserved_collateral_usd) - coll
            )
        result = "WIN" if pnl > 0 else "LOSS"
        if result == "WIN":
            self.wins += 1
        else:
            self.losses += 1
        side = "CREDIT_PACKAGE" if entry_usd < 0 else "DEBIT_PACKAGE"
        self.trade_log.append({
            "symbol": self.underlying,
            "direction": pos.get("direction"),
            "side": side,
            "entry": pos.get("entry_debit_usd"),
            "exit": round(exit_net, 2),
            "exit_mark": round(float(exit_value_usd), 2),
            "entry_fees_usd": pos.get("entry_fees_usd"),
            "exit_fees_usd": round(float(exit_fees_usd or 0.0), 4),
            "shares": pos.get("packages"),
            "contracts": pos.get("packages"),
            "pnl_usd": round(pnl, 2),
            "result": result,
            "reason": reason,
            "legs": pos.get("legs"),
            "expiration": pos.get("expiration"),
            "pricing_source": pos.get("pricing_source") or ASYM_PAPER_PRICING,
            "live_order_sent": False,
        })
        persist_asym_paper_close(
            paper_trade_id=pos.get("paper_trade_id"),
            strategy=self.strategy_key,
            exit_value_usd=float(exit_net),
            pnl_usd=float(pnl),
            reason=reason,
        )
        log.info(
            "[%s] %s (%s): entry $%.2f -> mark $%.2f fees_exit $%.2f | P&L $%.2f | Bal $%.2f",
            self.pattern_name, result, reason, entry_usd, exit_value_usd,
            float(exit_fees_usd or 0.0), pnl, self.account_balance_usd,
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

        if self._day_key != day_key:
            self._day_key = day_key
            if not self.active_position:
                self.signal_state = {"status": "NEW_DAY", "day": day_key}

        # Manage open position
        if self.active_position:
            exp = date.fromisoformat(self.active_position["expiration"])
            packages = float(self.active_position["packages"])
            mark_usd = self._mark_package(
                exp, self.active_position["legs"], day, packages=int(packages)
            )
            if mark_usd is not None:
                entry_usd = float(self.active_position["entry_debit_usd"])
                try:
                    from aiem_broker.paper_fills import fee_one_way, package_contract_count
                    spec = [
                        (int(L["qty"]), str(L["right"]), float(L["strike"]))
                        for L in self.active_position["legs"]
                    ]
                    exit_fees = fee_one_way(package_contract_count(spec, int(packages)))
                except Exception:
                    exit_fees = 0.0
                exit_net = float(mark_usd) - exit_fees
                pnl = exit_net - entry_usd
                self.active_position["mark_premium"] = round(
                    float(mark_usd) / max(packages, 1) / 100.0, 4
                )
                self.active_position["unrealized_pnl"] = round(pnl, 2)
                self.active_position["exit_fees_est"] = round(exit_fees, 4)
                self.net_liquidation_usd = self.account_balance_usd + float(mark_usd)
                tp_pct = float(
                    self.active_position.get("take_profit_pct", self.take_profit_pct)
                )
                tp_dollars = abs(entry_usd) * (tp_pct / 100.0)
                if pnl >= tp_dollars:
                    self._close(float(mark_usd), f"TP_{int(round(tp_pct))}PCT",
                                exit_fees_usd=exit_fees)
                    return
                if day >= exp and bar_time >= FLATTEN_TIME:
                    self._close(float(mark_usd), "EXPIRY_FLATTEN", exit_fees_usd=exit_fees)
                    return
                if day > exp:
                    self._close(float(mark_usd), "EXPIRY_FLATTEN", exit_fees_usd=exit_fees)
                    return
            tp_pct_note = float(
                self.active_position.get("take_profit_pct", self.take_profit_pct)
            )
            self.signal_state = {
                "status": "IN_POSITION",
                "note": (
                    f"TP +{tp_pct_note:.1f}% of |entry| · no stop · "
                    f"{self.active_position.get('pricing_source', ASYM_PAPER_PRICING)} · "
                    f"exp {self.active_position['expiration']}"
                ),
            }
            return

        # Entry: any Mon–Fri at/after first RTH bar (09:30), when flat
        if day.weekday() >= 5:
            self.signal_state = {
                "status": "WAIT_WEEKDAY",
                "note": "Mon–Fri entry only (weekends skipped)",
            }
            return
        if bar_time < ENTRY_AFTER:
            self.signal_state = {
                "status": "WAIT_OPEN",
                "note": f"entry at first RTH bar ({ENTRY_AFTER} ET)",
            }
            return
        if self._entered_day == day_key:
            self.signal_state = {
                "status": "DAY_DONE",
                "note": "already entered this session",
            }
            return

        exp = next_friday(day, weeks_ahead=3)
        legs_spec = self.builder(spot)
        priced = price_legs_for_paper(
            self.underlying, exp, legs_spec, day, require_exact=True, packages=1
        )
        if not priced:
            self.signal_state = {
                "status": "WAITING_ENTRY_QUOTE",
                "note": (
                    f"No {ASYM_PAPER_PRICING} package quote for {day.isoformat()} — "
                    "waiting (paper only, no live orders)"
                ),
            }
            return
        debit_ps = float(priced["debit_per_share"])
        unit_cost = debit_ps * 100.0  # signed: >0 debit, <0 credit (premium only)
        collateral = 0.0
        packages = 1
        premium_usd = 0.0
        entry_fees = 0.0

        if unit_cost > 0:
            if unit_cost > self.risk_usd:
                self.signal_state = {
                    "status": "SKIP_BUDGET",
                    "note": f"1-lot debit ${unit_cost:.0f} > risk ${self.risk_usd:.0f}",
                }
                return
            packages = max(int(self.risk_usd / unit_cost), 1)
            while packages > 1 and unit_cost * packages > self.risk_usd:
                packages -= 1
            premium_usd = unit_cost * packages
            try:
                from aiem_broker.paper_fills import fee_one_way, package_contract_count
                entry_fees = fee_one_way(package_contract_count(legs_spec, packages))
            except Exception:
                entry_fees = float(priced.get("fees_usd") or 0.0) * packages
            entry_usd = premium_usd + entry_fees
            if entry_usd > self._free_cash_usd() + 1e-9:
                self.signal_state = {
                    "status": "SKIP_CASH",
                    "note": f"need ${entry_usd:.0f} free cash (prem+fees)",
                }
                return
        elif unit_cost < 0 and self.allow_credit:
            packages = 1
            premium_usd = unit_cost * packages
            try:
                from aiem_broker.paper_fills import fee_one_way, package_contract_count
                entry_fees = fee_one_way(package_contract_count(legs_spec, packages))
            except Exception:
                entry_fees = float(priced.get("fees_usd") or 0.0)
            entry_usd = premium_usd + entry_fees
            if self.cash_secured:
                collateral = _short_put_collateral_usd(priced["legs"], packages)
                if collateral > self._free_cash_usd() + 1e-9:
                    self.signal_state = {
                        "status": "SKIP_COLLATERAL",
                        "note": (
                            f"need ${collateral:.0f} cash-secured collateral "
                            f"(free ${self._free_cash_usd():.0f})"
                        ),
                    }
                    return
                self._reserved_collateral_usd += collateral
        else:
            self.signal_state = {
                "status": "SKIP_CREDIT",
                "note": f"package net/share {debit_ps:.3f} ≤ 0 — skip (debit-only)",
            }
            return

        if self.strategy_key in DYNAMIC_PLATEAU_TP_STRATEGIES:
            if premium_usd <= 0:
                self.signal_state = {
                    "status": "SKIP_DYNAMIC_TP",
                    "note": "condor dynamic TP requires debit package",
                }
                return
            per_pkg_debit = float(premium_usd) / float(packages)
            try:
                self.take_profit_pct = float(dynamic_tp_pct(per_pkg_debit))
            except ValueError as _dtp_err:
                self.signal_state = {
                    "status": "SKIP_DYNAMIC_TP",
                    "note": str(_dtp_err),
                }
                return

        self.account_balance_usd -= entry_usd
        if priced.get("pricing_source") == "tradier_nbbo_paper" and packages != 1:
            priced_n = price_legs_for_paper(
                self.underlying, exp, legs_spec, day, packages=packages
            )
            if priced_n:
                priced = priced_n
        paper_id = persist_asym_paper_open(
            strategy=self.strategy_key,
            underlying=self.underlying,
            entry_debit_usd=entry_usd,
            packages=packages,
            expiration=priced["expiration"],
            legs=priced["legs"],
            entry_premium_ps=debit_ps,
            take_profit_pct=self.take_profit_pct,
        )
        side = "LONG" if entry_usd >= 0 else "CREDIT"
        self.active_position = {
            "symbol": self.underlying,
            "option_symbol": ",".join(
                str(L.get("symbol") or L.get("option_symbol") or "")
                for L in priced["legs"]
            ),
            "shares": packages,
            "packages": packages,
            "side": side,
            "direction": self.pattern_name,
            "entry": round(entry_usd / max(packages, 1) / 100.0, 4),
            "entry_debit_usd": entry_usd,
            "entry_fees_usd": round(float(entry_fees), 4),
            "premium_usd": round(float(premium_usd), 4),
            "collateral_usd": round(collateral, 2),
            "take_profit_pct": self.take_profit_pct,
            "stop": 0.0,
            "target": 0.0,
            "legs": priced["legs"],
            "expiration": priced["expiration"],
            "entry_time": bar_time,
            "spy_entry": spot,
            "pricing_source": priced.get("pricing_source") or ASYM_PAPER_PRICING,
            "live_order_sent": False,
            "paper_trade_id": paper_id,
            "strategy": self.strategy_key,
        }
        self._entered_day = day_key
        kind = "credit" if entry_usd < 0 else "debit"
        self.signal_state = {
            "status": "IN_POSITION",
            "note": (
                f"entered {packages} pkg {kind} ${entry_usd:.2f} "
                f"(fees ${float(entry_fees):.2f}) TP +{self.take_profit_pct:.1f}% "
                f"{priced.get('pricing_source', ASYM_PAPER_PRICING)}"
                + (f" · CSP ${collateral:.0f}" if collateral else "")
            ),
        }
        self.net_liquidation_usd = self.account_balance_usd + entry_usd
        log.info(
            "[%s] ENTRY: %d pkg @ $%.2f %s fees=$%.2f | TP +%.1f%% | exp %s | src=%s | paper_id=%s",
            self.pattern_name, packages, entry_usd, kind, float(entry_fees),
            self.take_profit_pct, priced["expiration"],
            priced.get("pricing_source"), paper_id,
        )



def build_default_asym_ledgers(underlying: str = "SPY", capital: float = 10000.0) -> dict:
    """Joel top-6 first (narrow-wing #1), then optional bullish risk reversal."""
    return {
        "narrow_wing_butterfly": AsymOptionsLedger(
            "NARROW_WING_CALL_BUTTERFLY", build_narrow_wing_call_butterfly,
            JOEL_ASYM_TP_PCT["narrow_wing_butterfly"],
            "narrow_wing_butterfly", underlying, capital,
        ),
        "put_butterfly": AsymOptionsLedger(
            "LONG_PUT_BUTTERFLY", build_long_put_butterfly,
            JOEL_ASYM_TP_PCT["put_butterfly"],
            "put_butterfly", underlying, capital,
        ),
        "call_butterfly": AsymOptionsLedger(
            "LONG_CALL_BUTTERFLY", build_long_call_butterfly,
            JOEL_ASYM_TP_PCT["call_butterfly"],
            "call_butterfly", underlying, capital,
        ),
        "put_ladder": AsymOptionsLedger(
            "PUT_LADDER_DEFINED", build_put_ladder_defined,
            JOEL_ASYM_TP_PCT["put_ladder"],
            "put_ladder", underlying, capital,
        ),
        "call_condor": AsymOptionsLedger(
            "LONG_CALL_CONDOR", build_long_call_condor,
            JOEL_ASYM_TP_PCT["call_condor"],
            "call_condor", underlying, capital,
        ),
        "put_condor": AsymOptionsLedger(
            "LONG_PUT_CONDOR", build_long_put_condor,
            JOEL_ASYM_TP_PCT["put_condor"],
            "put_condor", underlying, capital,
        ),
        "bullish_risk_reversal": AsymOptionsLedger(
            "BULLISH_RISK_REVERSAL", build_bullish_risk_reversal,
            JOEL_ASYM_TP_PCT["bullish_risk_reversal"],
            "bullish_risk_reversal", underlying, RR_PAPER_CAPITAL_USD,
            allow_credit=True,
            cash_secured=True,
        ),
    }
