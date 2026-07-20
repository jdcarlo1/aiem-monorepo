"""
aiem_risk_guards.py — Round 2 gap-fill modules for the AIEM paper trading pipeline.

Four classes, each operating at a different level of the risk stack:

  1. PortfolioCircuitBreaker  — account-level: trips when rolling avg P&L drops below
                                threshold, halting ALL new picks until cooling expires.
  2. CorrelationGuard         — position-level: blocks duplicate tickers, enforces
                                per-source concentration limits, caps total open count.
  3. LiquidityFilter          — contract-level: rejects picks where VOI/premium/volume
                                is too thin to fill cleanly at notional size.
  4. EventRiskFilter          — calendar-level: blocks picks where earnings or FOMC
                                falls inside the expected hold window.

All four are fail-open by default (error → pass-through with a log line) so a DB
outage or bad data row never silently kills the whole pick pipeline.
"""

from __future__ import annotations

import os
import time
import threading
import datetime
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL", "")

def _conn():
    return psycopg2.connect(_DB_URL, connect_timeout=5)


# ─────────────────────────────────────────────────────────────────────────────
# DB schema
# ─────────────────────────────────────────────────────────────────────────────

def init_schema():
    """Create the one table owned by this module. Idempotent."""
    ddl = """
    CREATE TABLE IF NOT EXISTS portfolio_circuit_breaker_state (
        id          SERIAL PRIMARY KEY,
        status      TEXT    NOT NULL DEFAULT 'open',   -- 'open' | 'tripped' | 'cooling'
        tripped_at  TIMESTAMPTZ,
        reason      TEXT,
        avg_pnl_pct NUMERIC(10,4),
        n_trades    INTEGER,
        reset_at    TIMESTAMPTZ,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    -- Seed a single open-state row if the table is empty.
    INSERT INTO portfolio_circuit_breaker_state (status)
    SELECT 'open'
    WHERE NOT EXISTS (SELECT 1 FROM portfolio_circuit_breaker_state);
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(ddl)
    print("[risk_guards] schema init OK")


# ─────────────────────────────────────────────────────────────────────────────
# 1. PortfolioCircuitBreaker
# ─────────────────────────────────────────────────────────────────────────────

_PCB_TRIP_AVG_PCT    = -5.0   # avg pnl_pct per trade over the window
_PCB_TRIP_MIN_TRADES = 5      # need at least this many closed trades to trip
_PCB_LOOKBACK_DAYS   = 5      # calendar days to look back for avg pnl_pct
_PCB_COOLING_HOURS   = 24     # hours before auto-reset after a trip

class PortfolioCircuitBreaker:
    """
    Account-level halt gate.

    Checks avg realized pnl_pct across closed aiem_paper_trades in the last
    PCB_LOOKBACK_DAYS calendar days.  If avg < PCB_TRIP_AVG_PCT and there are
    >= PCB_TRIP_MIN_TRADES, the breaker trips and all new picks are blocked.

    State is persisted to portfolio_circuit_breaker_state so a restart doesn't
    silently reset a live trip.  The breaker auto-resets after PCB_COOLING_HOURS.

    check()  → dict with 'tripped' bool + context
    status() → full state dict for the AIEM tool
    reset()  → manual admin reset (writes new open row)
    """

    def _load_state(self) -> Dict[str, Any]:
        """Read the latest row from the state table."""
        try:
            with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM portfolio_circuit_breaker_state ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row is None:
                    return {"status": "open", "tripped_at": None, "reset_at": None,
                            "reason": None, "avg_pnl_pct": None, "n_trades": None}
                return dict(row)
        except Exception as e:
            print(f"[PCB] load_state error: {e}")
            return {"status": "open"}

    def _trip(self, reason: str, avg_pnl_pct: float, n_trades: int):
        now = datetime.datetime.now(datetime.timezone.utc)
        reset_at = now + datetime.timedelta(hours=_PCB_COOLING_HOURS)
        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute("""
                    INSERT INTO portfolio_circuit_breaker_state
                        (status, tripped_at, reason, avg_pnl_pct, n_trades, reset_at, updated_at)
                    VALUES ('tripped', %s, %s, %s, %s, %s, NOW())
                """, (now, reason, avg_pnl_pct, n_trades, reset_at))
        except Exception as e:
            print(f"[PCB] _trip write error: {e}")

    def _reset_auto(self):
        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute("""
                    INSERT INTO portfolio_circuit_breaker_state
                        (status, reason, updated_at)
                    VALUES ('open', 'auto_reset_after_cooling', NOW())
                """)
        except Exception as e:
            print(f"[PCB] auto-reset write error: {e}")

    def reset(self, by: str = "admin") -> Dict[str, Any]:
        """Manual admin reset — inserts a new 'open' row."""
        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute("""
                    INSERT INTO portfolio_circuit_breaker_state
                        (status, reason, updated_at)
                    VALUES ('open', %s, NOW())
                """, (f"manual_reset_by_{by}",))
            return {"ok": True, "status": "open", "reset_by": by}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _compute_rolling_stats(self) -> Dict[str, Any]:
        """Compute avg pnl_pct and trade count over the lookback window."""
        try:
            cutoff = (
                datetime.date.today() - datetime.timedelta(days=_PCB_LOOKBACK_DAYS)
            ).isoformat()
            with _conn() as c, c.cursor() as cur:
                cur.execute("""
                    SELECT
                        AVG(pnl_pct)  AS avg_pnl_pct,
                        COUNT(*)      AS n_trades,
                        MIN(pnl_pct)  AS worst_trade
                    FROM aiem_paper_trades
                    WHERE exit_date >= %s
                      AND pnl_pct IS NOT NULL
                """, (cutoff,))
                row = cur.fetchone()
                if row is None:
                    return {"avg_pnl_pct": 0.0, "n_trades": 0, "worst_trade": 0.0}
                avg = float(row[0] or 0.0)
                n   = int(row[1] or 0)
                worst = float(row[2] or 0.0)
                return {"avg_pnl_pct": round(avg, 4), "n_trades": n,
                        "worst_trade": round(worst, 4)}
        except Exception as e:
            print(f"[PCB] rolling stats error: {e}")
            return {"avg_pnl_pct": 0.0, "n_trades": 0, "worst_trade": 0.0}

    def check(self) -> Dict[str, Any]:
        """
        Primary gate method.  Returns:
          {
            "tripped":    bool,
            "status":     "open" | "tripped" | "cooling",
            "reason":     str,
            "avg_pnl_pct_5d": float,
            "n_trades_5d":    int,
            "reset_at":       str | None,
          }
        """
        state = self._load_state()
        now   = datetime.datetime.now(datetime.timezone.utc)

        # ── If currently tripped, check whether cooling period has expired ──
        if state.get("status") == "tripped":
            reset_at = state.get("reset_at")
            if reset_at is not None:
                # psycopg2 returns datetime with tz; handle both
                if isinstance(reset_at, str):
                    import dateutil.parser
                    reset_at = dateutil.parser.parse(reset_at)
                if reset_at.tzinfo is None:
                    reset_at = reset_at.replace(tzinfo=datetime.timezone.utc)
                if now >= reset_at:
                    self._reset_auto()
                    print("[PCB] cooling period expired — auto-reset to OPEN")
                else:
                    remaining_h = (reset_at - now).total_seconds() / 3600
                    return {
                        "tripped": True,
                        "status":  "tripped",
                        "reason":  state.get("reason", "unknown"),
                        "avg_pnl_pct_5d": float(state.get("avg_pnl_pct") or 0),
                        "n_trades_5d":    int(state.get("n_trades") or 0),
                        "reset_at":       reset_at.isoformat(),
                        "reset_in_hours": round(remaining_h, 1),
                    }

        # ── Breaker is open (or just auto-reset) — compute current metrics ─
        stats = self._compute_rolling_stats()
        avg   = stats["avg_pnl_pct"]
        n     = stats["n_trades"]

        if n >= _PCB_TRIP_MIN_TRADES and avg < _PCB_TRIP_AVG_PCT:
            reason = (
                f"avg_pnl_pct={avg:.2f}% over last {_PCB_LOOKBACK_DAYS}d "
                f"({n} trades, threshold={_PCB_TRIP_AVG_PCT}%)"
            )
            self._trip(reason, avg, n)
            print(f"[PCB] TRIPPED — {reason}")
            reset_at_str = (
                now + datetime.timedelta(hours=_PCB_COOLING_HOURS)
            ).isoformat()
            return {
                "tripped": True,
                "status":  "tripped",
                "reason":  reason,
                "avg_pnl_pct_5d": avg,
                "n_trades_5d":    n,
                "reset_at":       reset_at_str,
                "reset_in_hours": float(_PCB_COOLING_HOURS),
            }

        # ── Proximity warning: within 2pp of the trip threshold ─────────────
        _PCB_PROXIMITY_PP = 2.0
        proximity = (
            n >= _PCB_TRIP_MIN_TRADES
            and avg < _PCB_TRIP_AVG_PCT + _PCB_PROXIMITY_PP
        )
        return {
            "tripped":           False,
            "status":            "open",
            "reason":            "",
            "avg_pnl_pct_5d":    avg,
            "n_trades_5d":       n,
            "reset_at":          None,
            "proximity_warning": proximity,
            "proximity_gap_pp":  round(avg - _PCB_TRIP_AVG_PCT, 2) if n >= _PCB_TRIP_MIN_TRADES else None,
        }

    def status(self) -> Dict[str, Any]:
        """Full status for the AIEM tool — includes both DB state and live metrics."""
        state = self._load_state()
        stats = self._compute_rolling_stats()
        avg   = stats["avg_pnl_pct"]
        n     = stats["n_trades"]
        _PCB_PROXIMITY_PP = 2.0
        proximity = (
            n >= _PCB_TRIP_MIN_TRADES
            and avg < _PCB_TRIP_AVG_PCT + _PCB_PROXIMITY_PP
        )
        return {
            "db_status":           state.get("status", "unknown"),
            "tripped_at":          str(state.get("tripped_at") or ""),
            "reset_at":            str(state.get("reset_at") or ""),
            "db_reason":           state.get("reason", ""),
            "avg_pnl_pct_5d":      avg,
            "n_trades_5d":         n,
            "worst_trade_5d":      stats["worst_trade"],
            "trip_threshold_pct":  _PCB_TRIP_AVG_PCT,
            "min_trades_to_trip":  _PCB_TRIP_MIN_TRADES,
            "lookback_days":       _PCB_LOOKBACK_DAYS,
            "cooling_hours":       _PCB_COOLING_HOURS,
            "proximity_warning":   proximity,
            "proximity_gap_pp":    round(avg - _PCB_TRIP_AVG_PCT, 2) if n >= _PCB_TRIP_MIN_TRADES else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. CorrelationGuard
# ─────────────────────────────────────────────────────────────────────────────

_CG_MAX_OPEN_POSITIONS    = 20    # hard cap on total simultaneous open positions
_CG_MAX_SOURCE_PCT        = 0.50  # block if one signal_source > 50% of open positions

class CorrelationGuard:
    """
    Position-level concentration gate.

    Loads the current open position set from aiem_paper_trades (status='OPEN')
    and enforces three rules:

      Rule 1 — Duplicate block: if ticker already has an OPEN position, block it.
      Rule 2 — Portfolio cap:   if total open positions >= MAX_OPEN_POSITIONS, block.
      Rule 3 — Source concentration: if signal_source already accounts for
                > MAX_SOURCE_PCT of open positions, block new picks from that source.

    Results are cached for 60 seconds to avoid per-candidate DB hits during a
    single pick cycle.
    """

    def __init__(self):
        self._cache: Optional[List[Dict]] = None
        self._cache_ts: float = 0.0
        self._lock = threading.Lock()

    def _load_open(self) -> List[Dict]:
        now = time.time()
        with self._lock:
            if self._cache is not None and (now - self._cache_ts) < 60:
                return self._cache
            try:
                with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute("""
                        SELECT ticker, signal_source, trade_date, trade_type
                        FROM aiem_paper_trades
                        WHERE status = 'OPEN'
                          AND (is_test_data IS NULL OR is_test_data = FALSE)
                          AND signal_source NOT IN ('test_source','pos_cap_test',
                              'pos_cap_conc_test','live_verification_test')
                          AND trade_date <= '2099-01-01'::date - INTERVAL '1 day'
                        ORDER BY trade_date DESC
                    """)
                    rows = [dict(r) for r in cur.fetchall()]
                    self._cache    = rows
                    self._cache_ts = now
                    return rows
            except Exception as e:
                print(f"[CorrelationGuard] load_open error: {e}")
                return self._cache or []

    def invalidate(self):
        """Call after a new trade is committed to flush the cache."""
        with self._lock:
            self._cache    = None
            self._cache_ts = 0.0

    def check(self, ticker: str, signal_source: str) -> Dict[str, Any]:
        """
        Returns:
          {
            "approved":     bool,
            "reason":       str,    # empty if approved
            "open_count":   int,
            "source_count": int,    # open positions from this source
            "source_pct":   float,
          }
        """
        open_pos  = self._load_open()
        n_open    = len(open_pos)
        tickers_open = [p["ticker"] for p in open_pos]

        # Rule 1: Duplicate
        if ticker in tickers_open:
            return {
                "approved": False,
                "reason":   f"duplicate — {ticker} already has an open position",
                "open_count":   n_open,
                "source_count": 0,
                "source_pct":   0.0,
            }

        # Rule 2: Portfolio cap
        if n_open >= _CG_MAX_OPEN_POSITIONS:
            return {
                "approved": False,
                "reason":   f"portfolio_cap — {n_open} open positions (max {_CG_MAX_OPEN_POSITIONS})",
                "open_count":   n_open,
                "source_count": 0,
                "source_pct":   0.0,
            }

        # Rule 3: Source concentration
        if n_open > 0:
            source_count = sum(
                1 for p in open_pos if p.get("signal_source") == signal_source
            )
            source_pct = source_count / n_open
            if source_pct >= _CG_MAX_SOURCE_PCT:
                return {
                    "approved": False,
                    "reason":   (
                        f"source_concentration — {signal_source} already at "
                        f"{source_count}/{n_open} ({source_pct:.0%}) open positions "
                        f"(limit {_CG_MAX_SOURCE_PCT:.0%})"
                    ),
                    "open_count":   n_open,
                    "source_count": source_count,
                    "source_pct":   round(source_pct, 4),
                }
        else:
            source_count = 0
            source_pct   = 0.0

        return {
            "approved":     True,
            "reason":       "",
            "open_count":   n_open,
            "source_count": source_count,
            "source_pct":   round(source_pct, 4),
        }

    def status(self) -> Dict[str, Any]:
        open_pos = self._load_open()
        source_counts: Dict[str, int] = {}
        for p in open_pos:
            src = p.get("signal_source", "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1
        return {
            "open_positions": len(open_pos),
            "max_open":       _CG_MAX_OPEN_POSITIONS,
            "max_source_pct": _CG_MAX_SOURCE_PCT,
            "tickers_open":   sorted(set(p["ticker"] for p in open_pos)),
            "source_breakdown": source_counts,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. LiquidityFilter
# ─────────────────────────────────────────────────────────────────────────────

_LF_MIN_VOI         = 300     # minimum volume-to-OI ratio (contract vol / open interest × 1000, unitless stored value)
_LF_MIN_PREMIUM_K   = 50      # minimum premium in $thousands ($50K)
_LF_MIN_RVOL        = 1.2     # minimum relative volume for stock picks
_LF_CACHE_TTL       = 120     # seconds to cache the liquidity lookup table

class LiquidityFilter:
    """
    Contract/share liquidity gate.

    For CALL_OPTION picks — queries unusual_calls_log for today's VOI and premium.
    For STOCK/ETF picks   — queries polygon_rvol_scan for today's RVOL.

    Block conditions:
      CALL_OPTION: VOI < MIN_VOI  AND  premium < MIN_PREMIUM_K * 1000
                   (both must fail to block — either threshold passing is enough)
      STOCK/ETF:   RVOL < MIN_RVOL  (RVOL is unitless ratio from polygon_rvol_scan)

    Results are cached per ticker per cycle (TTL=120s) to avoid hitting the DB
    once per candidate during the per-pick scoring loop.
    """

    def __init__(self):
        self._cache: Dict[str, Dict] = {}
        self._cache_ts: float = 0.0
        self._lock = threading.Lock()

    def _load_today_calls(self) -> Dict[str, Dict]:
        """Load today's unusual_calls_log rows keyed by ticker.
        Columns: vol_oi (volume/OI ratio), prem (total premium $), last_seen (timestamp).
        """
        try:
            with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("""
                    SELECT ticker,
                           MAX(vol_oi)   AS voi,
                           MAX(prem)     AS premium
                    FROM unusual_calls_log
                    WHERE DATE(last_seen) = CURRENT_DATE
                    GROUP BY ticker
                """)
                return {r["ticker"]: dict(r) for r in cur.fetchall()}
        except Exception as e:
            print(f"[LiquidityFilter] load_today_calls error: {e}")
            return {}

    def _load_today_stocks(self) -> Dict[str, Dict]:
        """Load today's polygon_rvol_scan rows keyed by ticker."""
        try:
            with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("""
                    SELECT ticker,
                           MAX(rvol) AS rvol
                    FROM polygon_rvol_scan
                    WHERE scan_date = CURRENT_DATE
                    GROUP BY ticker
                """)
                return {r["ticker"]: dict(r) for r in cur.fetchall()}
        except Exception as e:
            print(f"[LiquidityFilter] load_today_stocks error: {e}")
            return {}

    def _refresh_cache(self):
        now = time.time()
        with self._lock:
            if (now - self._cache_ts) < _LF_CACHE_TTL:
                return
            calls  = self._load_today_calls()
            stocks = self._load_today_stocks()
            merged: Dict[str, Dict] = {}
            all_tickers = set(calls) | set(stocks)
            for t in all_tickers:
                merged[t] = {
                    "voi":     float(calls.get(t, {}).get("voi") or 0.0),
                    "premium": float(calls.get(t, {}).get("premium") or 0.0),
                    "rvol":    float(stocks.get(t, {}).get("rvol") or 0.0),
                }
            self._cache    = merged
            self._cache_ts = now

    def check(self, ticker: str, trade_type: str) -> Dict[str, Any]:
        """
        Returns:
          {
            "approved": bool,
            "reason":   str,
            "voi":      float,    # 0.0 if not found
            "premium":  float,
            "rvol":     float,
          }
        """
        self._refresh_cache()
        data = self._cache.get(ticker, {})
        voi     = data.get("voi", 0.0)
        premium = data.get("premium", 0.0)
        rvol    = data.get("rvol", 0.0)

        if trade_type == "CALL_OPTION":
            # Pass if either threshold met; block only if both fail
            voi_ok     = voi     >= _LF_MIN_VOI
            premium_ok = premium >= (_LF_MIN_PREMIUM_K * 1000)
            if not voi_ok and not premium_ok:
                # No scan data at all → let it through (fail-open)
                if voi == 0.0 and premium == 0.0:
                    return {
                        "approved": True,
                        "reason":   "no_scan_data_pass_through",
                        "voi": voi, "premium": premium, "rvol": rvol,
                    }
                return {
                    "approved": False,
                    "reason":   (
                        f"illiquid_call — VOI={voi:.0f} (min {_LF_MIN_VOI}), "
                        f"premium=${premium/1000:.0f}K (min ${_LF_MIN_PREMIUM_K}K)"
                    ),
                    "voi": voi, "premium": premium, "rvol": rvol,
                }
        else:
            # STOCK / ETF
            if rvol > 0.0 and rvol < _LF_MIN_RVOL:
                return {
                    "approved": False,
                    "reason":   f"low_rvol — RVOL={rvol:.2f} (min {_LF_MIN_RVOL})",
                    "voi": voi, "premium": premium, "rvol": rvol,
                }

        return {
            "approved": True,
            "reason":   "",
            "voi": voi, "premium": premium, "rvol": rvol,
        }

    def status(self) -> Dict[str, Any]:
        self._refresh_cache()
        return {
            "tickers_cached":   len(self._cache),
            "cache_age_s":      round(time.time() - self._cache_ts, 1),
            "min_voi":          _LF_MIN_VOI,
            "min_premium_k":    _LF_MIN_PREMIUM_K,
            "min_rvol":         _LF_MIN_RVOL,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. EventRiskFilter
# ─────────────────────────────────────────────────────────────────────────────

# FOMC decision dates for 2026 (second day of each meeting — market-reaction day)
# Source: Federal Reserve calendar
_FOMC_DATES_2026 = [
    datetime.date(2026, 1, 29),
    datetime.date(2026, 3, 18),
    datetime.date(2026, 4, 29),
    datetime.date(2026, 6, 18),
    datetime.date(2026, 7, 29),
    datetime.date(2026, 9, 16),
    datetime.date(2026, 10, 28),
    datetime.date(2026, 12, 10),
]

# CPI release dates for 2026 (approximate BLS schedule — second/third Wed of month)
_CPI_DATES_2026 = [
    datetime.date(2026, 1, 14),
    datetime.date(2026, 2, 11),
    datetime.date(2026, 3, 11),
    datetime.date(2026, 4, 10),
    datetime.date(2026, 5, 13),
    datetime.date(2026, 6, 10),
    datetime.date(2026, 7, 14),
    datetime.date(2026, 8, 12),
    datetime.date(2026, 9, 11),
    datetime.date(2026, 10, 14),
    datetime.date(2026, 11, 12),
    datetime.date(2026, 12, 10),
]

_ERF_EARNINGS_BLOCK    = True    # block on earnings within window
_ERF_FOMC_BLOCK        = False   # warn only (reduce score) — FOMC affects all tickers
_ERF_CPI_BLOCK         = False   # warn only
_ERF_FOMC_SCORE_MULT   = 0.80    # score reduction when FOMC is in window
_ERF_CPI_SCORE_MULT    = 0.90    # score reduction when CPI is in window

class EventRiskFilter:
    """
    Calendar-based event risk gate.

    For each candidate ticker, checks:
      1. Earnings — queries earnings_calendar WHERE ticker=X AND earnings_date
                    falls in [today, today + hold_days_max].  Hard block by default.
      2. FOMC     — static 2026 date list.  Soft block (score reduction) by default.
      3. CPI      — static 2026 date list.  Soft block (score reduction) by default.

    The earnings query uses the existing earnings_calendar table populated by
    _populate_earnings_calendar() in main.py.
    """

    def _earnings_in_window(
        self, ticker: str, today: datetime.date, end_date: datetime.date
    ) -> Optional[datetime.date]:
        """Return the first earnings date in window, or None."""
        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute("""
                    SELECT earnings_date
                    FROM earnings_calendar
                    WHERE ticker = %s
                      AND earnings_date >= %s
                      AND earnings_date <= %s
                    ORDER BY earnings_date ASC
                    LIMIT 1
                """, (ticker, today.isoformat(), end_date.isoformat()))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            print(f"[EventRiskFilter] earnings lookup error for {ticker}: {e}")
            return None

    def _macro_event_in_window(
        self, today: datetime.date, end_date: datetime.date,
        dates: List[datetime.date]
    ) -> Optional[datetime.date]:
        for d in dates:
            if today <= d <= end_date:
                return d
        return None

    def check(
        self,
        ticker: str,
        hold_days_max: int = 11,
        score: float = 1.0
    ) -> Dict[str, Any]:
        """
        Returns:
          {
            "approved":       bool,
            "reason":         str,
            "event_type":     str,   # "earnings" | "fomc" | "cpi" | ""
            "event_date":     str,   # ISO date or ""
            "score_mult":     float, # 1.0 unless a soft event is in window
          }
        """
        today    = datetime.date.today()
        end_date = today + datetime.timedelta(days=hold_days_max)

        # ── 1. Earnings ───────────────────────────────────────────────────
        earnings_date = self._earnings_in_window(ticker, today, end_date)
        if earnings_date:
            if _ERF_EARNINGS_BLOCK:
                return {
                    "approved":   False,
                    "reason":     f"earnings on {earnings_date} — within {hold_days_max}d hold window",
                    "event_type": "earnings",
                    "event_date": earnings_date.isoformat(),
                    "score_mult": 1.0,
                }
            else:
                return {
                    "approved":   True,
                    "reason":     f"earnings_warn — {earnings_date} in window",
                    "event_type": "earnings",
                    "event_date": earnings_date.isoformat(),
                    "score_mult": 0.70,
                }

        # ── 2. FOMC ───────────────────────────────────────────────────────
        fomc_date = self._macro_event_in_window(today, end_date, _FOMC_DATES_2026)
        if fomc_date:
            if _ERF_FOMC_BLOCK:
                return {
                    "approved":   False,
                    "reason":     f"fomc on {fomc_date} — within {hold_days_max}d hold window",
                    "event_type": "fomc",
                    "event_date": fomc_date.isoformat(),
                    "score_mult": 1.0,
                }
            return {
                "approved":   True,
                "reason":     f"fomc_warn — {fomc_date} in window (score reduced)",
                "event_type": "fomc",
                "event_date": fomc_date.isoformat(),
                "score_mult": _ERF_FOMC_SCORE_MULT,
            }

        # ── 3. CPI ────────────────────────────────────────────────────────
        cpi_date = self._macro_event_in_window(today, end_date, _CPI_DATES_2026)
        if cpi_date:
            if _ERF_CPI_BLOCK:
                return {
                    "approved":   False,
                    "reason":     f"cpi on {cpi_date} — within {hold_days_max}d hold window",
                    "event_type": "cpi",
                    "event_date": cpi_date.isoformat(),
                    "score_mult": 1.0,
                }
            return {
                "approved":   True,
                "reason":     f"cpi_warn — {cpi_date} in window (score reduced)",
                "event_type": "cpi",
                "event_date": cpi_date.isoformat(),
                "score_mult": _ERF_CPI_SCORE_MULT,
            }

        return {
            "approved":   True,
            "reason":     "",
            "event_type": "",
            "event_date": "",
            "score_mult": 1.0,
        }

    def status(self) -> Dict[str, Any]:
        today = datetime.date.today()
        next_fomc = next((d for d in _FOMC_DATES_2026 if d >= today), None)
        next_cpi  = next((d for d in _CPI_DATES_2026 if d >= today), None)
        return {
            "earnings_block": _ERF_EARNINGS_BLOCK,
            "fomc_block":     _ERF_FOMC_BLOCK,
            "cpi_block":      _ERF_CPI_BLOCK,
            "fomc_score_mult": _ERF_FOMC_SCORE_MULT,
            "cpi_score_mult":  _ERF_CPI_SCORE_MULT,
            "next_fomc":      next_fomc.isoformat() if next_fomc else None,
            "next_cpi":       next_cpi.isoformat()  if next_cpi  else None,
            "fomc_dates_remaining_2026": [d.isoformat() for d in _FOMC_DATES_2026 if d >= today],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Singleton factories (lazy, thread-safe)
# ─────────────────────────────────────────────────────────────────────────────

_pcb_instance: Optional[PortfolioCircuitBreaker] = None
_cg_instance:  Optional[CorrelationGuard]         = None
_lf_instance:  Optional[LiquidityFilter]          = None
_erf_instance: Optional[EventRiskFilter]          = None
_factory_lock  = threading.Lock()


def get_portfolio_circuit_breaker() -> PortfolioCircuitBreaker:
    global _pcb_instance
    with _factory_lock:
        if _pcb_instance is None:
            _pcb_instance = PortfolioCircuitBreaker()
    return _pcb_instance


def get_correlation_guard() -> CorrelationGuard:
    global _cg_instance
    with _factory_lock:
        if _cg_instance is None:
            _cg_instance = CorrelationGuard()
    return _cg_instance


def get_liquidity_filter() -> LiquidityFilter:
    global _lf_instance
    with _factory_lock:
        if _lf_instance is None:
            _lf_instance = LiquidityFilter()
    return _lf_instance


def get_event_risk_filter() -> EventRiskFilter:
    global _erf_instance
    with _factory_lock:
        if _erf_instance is None:
            _erf_instance = EventRiskFilter()
    return _erf_instance
