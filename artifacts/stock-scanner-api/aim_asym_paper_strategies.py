#!/usr/bin/env python3
"""
Asymmetric SPY paper strategies — Pattern Lab / OE Strategies.

Top-3 from 2y real Polygon backtest (no stop, TP grid):
  1. Long put butterfly   — take-profit +200% of debit
  2. Long call butterfly  — take-profit +100% of debit
  3. Put ladder (defined) — take-profit +150% of debit

Parity with spy_asymmetric_bt.py (exact):
  - Underlying SPY
  - Risk budget $500 debit max per package
  - Entry: weekly Monday, first RTH bar (09:30 ET) — BT docstring L9
  - Expiry: next_friday(d0, weeks_ahead=3) — same helper as BT
  - NO stop loss
  - Flatten 15:30 ET on expiry Friday — BT docstring L12
  - Pricing: Polygon daily option aggregates (O:SPY…) — NOT Tradier
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
# First RTH bar — matches spy_asymmetric_bt.py docstring L9 ("Monday ~ open")
ENTRY_AFTER = "09:30"
# Matches spy_asymmetric_bt.py docstring L12
FLATTEN_TIME = "15:30"
POLYGON_BASE = "https://api.polygon.io"
RATE_SLEEP = float(os.environ.get("ASYM_PAPER_RATE_SLEEP", "0.12"))

# strategy key -> ledger (must match aiem_paper_trades.strategy filter)
STRATEGY_KEYS = ("put_butterfly", "call_butterfly", "put_ladder")


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


# Short TTL cache: (occ_symbol, day_iso) -> premium close or None
_OPT_PX_CACHE: dict[tuple[str, str], tuple[float, Optional[float]]] = {}
_OPT_PX_TTL = 300.0  # 5 min


def fetch_option_daily_close(occ_symbol: str, asof: date) -> Optional[float]:
    """
    Polygon daily option aggregate close on/asof `asof` (same source as BT).
    Uses /v2/aggs/ticker/{O:…}/range/1/day — lookback 10 calendar days for asof.
    """
    cache_key = (occ_symbol, asof.isoformat())
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
        # Prefer exact asof day; else last bar <= asof (asof semantics like BT _px_on)
        best_d = None
        for row in rows:
            try:
                d = datetime.fromtimestamp(row["t"] / 1000.0, tz=ET).date()
                c = float(row["c"])
            except (KeyError, TypeError, ValueError):
                continue
            if d > asof or c <= 0:
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
) -> Optional[float]:
    """
    Mark package in dollars (qty * premium * 100), Polygon daily closes.
    legs: (qty_signed, 'call'|'put', strike). Returns None if any leg missing.
    """
    total = 0.0
    priced_legs = []
    for qty, right, strike in legs:
        occ = _occ(underlying, float(strike), right, expiration)
        px = fetch_option_daily_close(occ, asof)
        if px is None or px <= 0:
            return None
        total += int(qty) * float(px) * 100.0
        priced_legs.append({
            "qty": int(qty),
            "right": right.lower(),
            "strike": float(strike),
            "premium": float(px),
            "symbol": occ,
        })
    return total  # dollars for 1 package; caller scales by packages


def price_legs_polygon(
    underlying: str,
    expiration: date,
    legs: list[tuple[int, str, float]],
    asof: date,
) -> Optional[dict]:
    """Entry pricing via Polygon daily — returns debit_per_share + legs."""
    total_usd = package_value_polygon(underlying, expiration, legs, asof)
    if total_usd is None:
        return None
    # Rebuild legs with premiums for storage
    priced = []
    for qty, right, strike in legs:
        occ = _occ(underlying, float(strike), right, expiration)
        px = fetch_option_daily_close(occ, asof)
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
    }


def build_long_put_butterfly(spot: float) -> list[tuple[int, str, float]]:
    k = float(round(spot))
    return [(1, "put", k + 5), (-2, "put", k), (1, "put", k - 5)]


def build_long_call_butterfly(spot: float) -> list[tuple[int, str, float]]:
    k = float(round(spot))
    return [(1, "call", k - 5), (-2, "call", k), (1, "call", k + 5)]


def build_put_ladder_defined(spot: float) -> list[tuple[int, str, float]]:
    k = float(round(spot))
    return [(1, "put", k), (-1, "put", k - 5), (-1, "put", k - 10), (1, "put", k - 15)]


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
                    %s, 'OPTIONS_PACKAGE', 'DEBIT_PACKAGE',
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
                                     THEN (%s / notional) * 100.0 ELSE NULL END,
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
    """Multi-leg debit package paper ledger — Monday RTH open, TP%, no stop, Polygon daily."""

    def __init__(
        self,
        pattern_name: str,
        builder: Callable[[float], list],
        take_profit_pct: float,
        strategy_key: str,
        underlying: str = "SPY",
        starting_capital_usd: float = 10000.0,
        risk_usd: float = RISK_USD,
    ):
        self.pattern_name = pattern_name
        self.strategy_key = strategy_key  # put_butterfly | call_butterfly | put_ladder
        self.builder = builder
        self.take_profit_pct = take_profit_pct
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
        self._entered_week: Optional[str] = None

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
                "entry": "Monday first RTH bar (09:30 ET) when flat",
                "structure": self.pattern_name,
                "risk_usd": self.risk_usd,
                "take_profit_pct": self.take_profit_pct,
                "stop_loss": None,
                "pricing": "Polygon daily option aggregates",
                "exit": f"+{self.take_profit_pct:.0f}% of debit or flatten 15:30 on expiry Friday",
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

    def _mark_package(self, expiration: date, legs: list, asof: date) -> Optional[float]:
        spec = [(int(L["qty"]), str(L["right"]), float(L["strike"])) for L in legs]
        return package_value_polygon(self.underlying, expiration, spec, asof)

    def _close(self, exit_value_usd: float, reason: str):
        pos = self.active_position
        if not pos:
            return
        entry_debit = float(pos["entry_debit_usd"])
        pnl = float(exit_value_usd) - entry_debit
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
            "pricing_source": "polygon_daily_option_aggs",
        })
        persist_asym_paper_close(
            paper_trade_id=pos.get("paper_trade_id"),
            strategy=self.strategy_key,
            exit_value_usd=float(exit_value_usd),
            pnl_usd=float(pnl),
            reason=reason,
        )
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
            mark = self._mark_package(exp, self.active_position["legs"], day)
            if mark is not None:
                packages = float(self.active_position["packages"])
                mark_usd = mark * packages
                self.active_position["mark_premium"] = round(
                    mark_usd / max(packages, 1) / 100.0, 4
                )
                self.active_position["unrealized_pnl"] = round(
                    mark_usd - float(self.active_position["entry_debit_usd"]), 2
                )
                self.net_liquidation_usd = self.account_balance_usd + mark_usd
                entry_debit = float(self.active_position["entry_debit_usd"])
                tp = entry_debit * (1.0 + self.take_profit_pct / 100.0)
                if mark_usd >= tp:
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
                "note": (
                    f"TP +{int(self.take_profit_pct)}% · no stop · "
                    f"Polygon daily · exp {self.active_position['expiration']}"
                ),
            }
            return

        # Entry: Monday at/after first RTH bar (09:30), one per week
        if day.weekday() != 0:
            self.signal_state = {"status": "WAIT_MONDAY", "note": "weekly Monday entry only"}
            return
        if bar_time < ENTRY_AFTER:
            self.signal_state = {
                "status": "WAIT_OPEN",
                "note": f"entry at first RTH bar ({ENTRY_AFTER} ET)",
            }
            return
        if self._entered_week == week_key:
            self.signal_state = {"status": "WEEK_DONE", "note": "already entered this week"}
            return

        exp = next_friday(day, weeks_ahead=3)
        legs_spec = self.builder(spot)
        priced = price_legs_polygon(self.underlying, exp, legs_spec, day)
        if not priced:
            self.signal_state = {
                "status": "WAITING_PREMIUM",
                "note": (
                    "Polygon daily option aggregates unavailable for asof "
                    f"{day.isoformat()} — not entering synthetic/Tradier"
                ),
            }
            return
        debit_ps = float(priced["debit_per_share"])
        if debit_ps <= 0:
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
        self.account_balance_usd -= entry_debit
        paper_id = persist_asym_paper_open(
            strategy=self.strategy_key,
            underlying=self.underlying,
            entry_debit_usd=entry_debit,
            packages=packages,
            expiration=priced["expiration"],
            legs=priced["legs"],
            entry_premium_ps=debit_ps,
            take_profit_pct=self.take_profit_pct,
        )
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
            "pricing_source": "polygon_daily_option_aggs",
            "paper_trade_id": paper_id,
            "strategy": self.strategy_key,
        }
        self._entered_week = week_key
        self.signal_state = {
            "status": "IN_POSITION",
            "note": (
                f"entered {packages} pkg debit ${entry_debit:.2f} "
                f"TP +{int(self.take_profit_pct)}% Polygon daily"
            ),
        }
        self.net_liquidation_usd = self.account_balance_usd + entry_debit
        log.info(
            "[%s] ENTRY: %d pkg @ $%.2f debit | TP +%.0f%% | exp %s | paper_id=%s",
            self.pattern_name, packages, entry_debit, self.take_profit_pct,
            priced["expiration"], paper_id,
        )


def build_default_asym_ledgers(underlying: str = "SPY", capital: float = 10000.0) -> dict:
    return {
        "put_butterfly": AsymOptionsLedger(
            "LONG_PUT_BUTTERFLY", build_long_put_butterfly, 200.0,
            "put_butterfly", underlying, capital,
        ),
        "call_butterfly": AsymOptionsLedger(
            "LONG_CALL_BUTTERFLY", build_long_call_butterfly, 100.0,
            "call_butterfly", underlying, capital,
        ),
        "put_ladder": AsymOptionsLedger(
            "PUT_LADDER_DEFINED", build_put_ladder_defined, 150.0,
            "put_ladder", underlying, capital,
        ),
    }
