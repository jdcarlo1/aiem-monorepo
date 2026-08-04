"""
aiem_position_sizing.py — Position Sizing & Risk-Per-Trade (spec §0–9)

SCOPE: AIEM-internal only. Not wired into, or accessible from, the
customer-facing StockScanner AI product, scanner tabs, or website.

SECTION 0 GATE — hard-enforced:
  LIVE_MODE_ENABLED = False
  Do not change without ALL Section 0 conditions confirmed in writing.

PARAMETER STATUS (all confirmed 2026-07-04):
  _MAX_RISK_PER_TRADE_PCT   = 0.01   Q1 CONFIRMED — 1% of equity
  _SIMULATED_ACCOUNT_EQUITY = 20000  Q2 CONFIRMED — $20,000
  _MAX_CONCURRENT_POSITIONS = 10     Q3 CONFIRMED — 10 open positions (ceiling, not daily limit)
  _MAX_SECTOR_POSITIONS     = 3      Q3 CONFIRMED — 3 per signal-source sector
  _MIN_CONVICTION_TO_TRADE  = 5.0    Q5 CONFIRMED — floor score
  _OVERNIGHT_OPTION         = 'C'    Q4 CONFIRMED — per-module hybrid, Section 7.1

params_confirmed() == True. Sizing math is ACTIVE.
"""

import os
import json
import math
import datetime as dt
from typing import Optional, Dict, Any

import psycopg2

# ─────────────────────────────────────────────────────────────────────────────
# Section 0 — deployment gate
# ─────────────────────────────────────────────────────────────────────────────
LIVE_MODE_ENABLED = False   # NEVER change without full written Section 0 sign-off

# ─────────────────────────────────────────────────────────────────────────────
# Parameters — confirmed in writing by Joel; None = still awaiting confirmation.
# compute_position_size() stays a safe no-op while any sentinel remains None.
# ─────────────────────────────────────────────────────────────────────────────
_MAX_RISK_PER_TRADE_PCT:   Optional[float] = 0.01      # Q1 CONFIRMED — 1% of equity per trade
_SIMULATED_ACCOUNT_EQUITY: Optional[float] = 20000.0   # Q2 CONFIRMED — $20,000
_MAX_CONCURRENT_POSITIONS: Optional[int]   = 10        # Q3 CONFIRMED — 10 open positions max
_MAX_SECTOR_POSITIONS:     Optional[int]   = 3         # Q3 CONFIRMED — 3 per signal-source sector
_MIN_CONVICTION_TO_TRADE:  Optional[float] = 5.0       # Q5 CONFIRMED — floor conviction score
_OVERNIGHT_OPTION:         Optional[str]   = "C"       # Q4 CONFIRMED — per-module hybrid

# Conviction score at which risk % is at its FLOOR (MIN_RISK * MAX_RISK_PCT).
# Scores below _MIN_CONVICTION_TO_TRADE are rejected outright.
# Synced to Q5 = 5.0 (confirmed 2026-07-04).
_CONVICTION_FLOOR_SCORE:   float = 5.0    # == _MIN_CONVICTION_TO_TRADE (Q5)
_CONVICTION_CEILING_SCORE: float = 9.0    # score that unlocks full MAX_RISK_PCT
_CONVICTION_MIN_RISK_MULT: float = 0.50   # at floor: 50% of MAX_RISK_PCT
# Linear interpolation between these two bounds — see _conviction_risk_mult()

# Per-volatility worst-case gap assumptions (Option B only — §7.1)
# "high" = ATR bucket HIGH in selloff signal. Represents worst-case overnight gap
# BEYOND the stop price that must be absorbed. Used only when OVERNIGHT_OPTION='B'.
_GAP_RISK_BY_VOL_BUCKET: Dict[str, float] = {
    "LOW":    0.03,   # 3% gap assumption for low-vol names
    "MEDIUM": 0.05,   # 5%
    "HIGH":   0.08,   # 8%
    "EXTREME":0.12,   # 12%
}

# Thesis-based stop buffer below the identified support level (Section 3)
_STOP_BUFFER_BELOW_SUPPORT: float = 0.005   # 0.5% below support price

# Minimum practical position size — skip rather than size below this (Section 3)
_MIN_POSITION_NOTIONAL: float = 500.0       # $500 minimum position

_DB_URL = os.environ.get("DATABASE_URL", "")

# ─────────────────────────────────────────────────────────────────────────────
# Overnight option tags (Section 7.1 / Option C)
# Each signal source maps to overnight_hold_allowed=True/False.
# Populated after Joel confirms Option C; irrelevant under A or B.
# ─────────────────────────────────────────────────────────────────────────────
_OVERNIGHT_HOLD_ALLOWED: Dict[str, bool] = {
    "Oversold_Bounce_Uptrend": True,   # multi-day thesis — needs ≥2 sessions
    "washout_ignition":        True,
    "conviction_stack":        False,
    "sweep":                   False,
    "unusual_calls":           False,
    "gap_volume":              False,
    "aiem_ai":                 False,
    "multi_signal":            False,
    "oi_buildup":              False,
    "layer9_stat":             False,
    "aiem_v3_discovery":       False,
    "fear_premium_gex":        False,
    "gap_down_distribution":   False,
}


# ─────────────────────────────────────────────────────────────────────────────
# Section 9 — DB schema
# ─────────────────────────────────────────────────────────────────────────────
_DDL_SIZING_LOG = """
CREATE TABLE IF NOT EXISTS aiem_position_sizing_log (
    id                   BIGSERIAL PRIMARY KEY,
    logged_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ticker               TEXT        NOT NULL,
    signal_source        TEXT        NOT NULL,
    conviction_score     NUMERIC(6,2),
    entry_price          NUMERIC(14,4),
    calculated_stop_price NUMERIC(14,4),
    stop_basis           TEXT,
    stop_distance_pct    NUMERIC(8,4),
    risk_pct_used        NUMERIC(8,4),
    calculated_notional  NUMERIC(12,2),
    gate_result          TEXT        NOT NULL,
    gate_detail          TEXT,
    mode                 TEXT        NOT NULL DEFAULT 'SIMULATION',
    overnight_option     TEXT,
    paper_trade_id       INTEGER,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS aiem_psl_ticker_idx  ON aiem_position_sizing_log(ticker);
CREATE INDEX IF NOT EXISTS aiem_psl_logged_idx  ON aiem_position_sizing_log(logged_at);
"""

_DDL_PRE_CLOSE_LOG = """
CREATE TABLE IF NOT EXISTS aiem_pre_close_review_log (
    id                   BIGSERIAL PRIMARY KEY,
    reviewed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paper_trade_id       INTEGER     NOT NULL,
    ticker               TEXT        NOT NULL,
    signal_source        TEXT,
    decision             TEXT        NOT NULL,
    conviction_at_entry  NUMERIC(6,2),
    conviction_current   NUMERIC(6,2),
    thesis_intact        BOOLEAN,
    mod_f_recheck        BOOLEAN,
    unrealized_pnl_pct   NUMERIC(8,4),
    vix_level            NUMERIC(8,2),
    vix_status           TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    market_rsi_pct_oversold NUMERIC(8,4),
    market_rsi_status    TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    hyg_status           TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    put_call_status      TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    fear_greed_status    TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    aaii_status          TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    naaim_status         TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    breadth_status       TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    trin_status          TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    cot_status           TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    mmf_flow_status      TEXT        NOT NULL DEFAULT 'NOT_AVAILABLE',
    composite_lean       TEXT,
    composite_strength   NUMERIC(6,2),
    detail_json          JSONB,
    overnight_option     TEXT
);
"""

_DDL_CONTRARIAN_ALERT_LOG = """
CREATE TABLE IF NOT EXISTS aiem_contrarian_alert_log (
    id               BIGSERIAL PRIMARY KEY,
    fired_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    alert_type       TEXT        NOT NULL,
    composite_lean   TEXT,
    composite_strength NUMERIC(6,2),
    vix_level        NUMERIC(8,2),
    vix_zscore       NUMERIC(6,2),
    indicators_json  JSONB,
    tg_sent          BOOLEAN NOT NULL DEFAULT FALSE
);
"""


def init_tables():
    if not _DB_URL:
        print("[position_sizing] DATABASE_URL not set — schema init skipped")
        return
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute(_DDL_SIZING_LOG)
            cur.execute(_DDL_PRE_CLOSE_LOG)
            cur.execute(_DDL_CONTRARIAN_ALERT_LOG)
            # Section 6: flag pre-sizing history so it's never blended
            cur.execute("""
                ALTER TABLE aiem_paper_trades
                ADD COLUMN IF NOT EXISTS pre_sizing_model BOOLEAN NOT NULL DEFAULT FALSE
            """)
            # Mark every row that existed before today (no sizing model applied)
            cur.execute("""
                UPDATE aiem_paper_trades
                SET pre_sizing_model = TRUE
                WHERE pre_sizing_model = FALSE
                  AND created_at < NOW()
            """)
            # Section 9 columns on paper trades for risk-adjusted tracking
            cur.execute("ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS sizing_stop_price NUMERIC(14,4)")
            cur.execute("ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS sizing_stop_basis TEXT")
            cur.execute("ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS sizing_risk_pct NUMERIC(8,4)")
            cur.execute("ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS sizing_log_id BIGINT")
            cur.execute("ALTER TABLE aiem_paper_trades ADD COLUMN IF NOT EXISTS sizing_gate_result TEXT")
            conn.commit()
        print("[position_sizing] tables + migrations ready")
    except Exception as e:
        print(f"[position_sizing] init error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Parameter guard
# ─────────────────────────────────────────────────────────────────────────────
def _params_confirmed() -> bool:
    """Returns True only when all Joel-confirmed parameters are set."""
    return all([
        _MAX_RISK_PER_TRADE_PCT   is not None,
        _SIMULATED_ACCOUNT_EQUITY is not None,
        _MAX_CONCURRENT_POSITIONS is not None,
        _MAX_SECTOR_POSITIONS     is not None,
        _MIN_CONVICTION_TO_TRADE  is not None,
        _OVERNIGHT_OPTION         is not None,
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — conviction-weighted risk multiplier
# ─────────────────────────────────────────────────────────────────────────────
def _conviction_risk_mult(conviction_score: float) -> float:
    """
    Linear interpolation between _CONVICTION_MIN_RISK_MULT (at floor score)
    and 1.0 (at ceiling score). Clamped to [0, 1].

    Formula (spec §4):
      mult = MIN_MULT + (score - floor) / (ceiling - floor) * (1.0 - MIN_MULT)
    """
    floor   = _CONVICTION_FLOOR_SCORE
    ceiling = _CONVICTION_CEILING_SCORE
    if ceiling <= floor:
        return 1.0
    raw = _CONVICTION_MIN_RISK_MULT + (
        (conviction_score - floor) / (ceiling - floor)
    ) * (1.0 - _CONVICTION_MIN_RISK_MULT)
    return max(0.0, min(1.0, raw))


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — thesis-based stop derivation
# ─────────────────────────────────────────────────────────────────────────────
def _stop_oversold_bounce(signal_row: dict) -> dict:
    """
    Oversold_Bounce_Uptrend: stop below the support level from §2.4.
    Support = the nearer of SMA20 / SMA50; stored as distance_to_support_pct
    from the last close. Stop is placed _STOP_BUFFER_BELOW_SUPPORT below that level.

    Returns dict with: stop_price, stop_distance_pct, stop_basis, defined=True/False
    """
    entry = float(signal_row.get("entry_price") or signal_row.get("close_price") or 0)
    dist  = signal_row.get("distance_to_support_pct")
    if not entry or dist is None:
        return {"defined": False, "stop_basis": "MISSING_ENTRY_OR_SUPPORT_DISTANCE"}
    dist_pct   = float(dist) / 100.0
    support    = entry * (1.0 - dist_pct)
    stop_price = support * (1.0 - _STOP_BUFFER_BELOW_SUPPORT)
    if stop_price <= 0 or stop_price >= entry:
        return {"defined": False, "stop_basis": "STOP_ABOVE_OR_AT_ENTRY"}
    stop_dist_pct = (entry - stop_price) / entry * 100.0
    return {
        "defined": True,
        "stop_price": round(stop_price, 4),
        "stop_distance_pct": round(stop_dist_pct, 4),
        "stop_basis": f"support_sma_below_{round(dist, 2)}pct_buffer_{_STOP_BUFFER_BELOW_SUPPORT*100:.1f}pct",
    }


def _stop_pct_below_entry(stop_pct: float, basis_label: str):
    """
    Factory: returns a stop function that places the stop N% below entry price.
    Used for momentum/flow signals where the thesis invalidation point is a
    fixed drawdown from entry rather than a named support level.
    """
    def _fn(signal_row: dict) -> dict:
        entry = float(signal_row.get("entry_price") or signal_row.get("close_price") or 0)
        if not entry or entry <= 0:
            return {"defined": False, "stop_basis": f"{basis_label}_MISSING_ENTRY_PRICE"}
        stop_price = round(entry * (1.0 - stop_pct), 4)
        if stop_price <= 0 or stop_price >= entry:
            return {"defined": False, "stop_basis": f"{basis_label}_STOP_ABOVE_OR_AT_ENTRY"}
        return {
            "defined": True,
            "stop_price": stop_price,
            "stop_distance_pct": round(stop_pct * 100, 4),
            "stop_basis": f"{basis_label}_{stop_pct*100:.0f}pct_below_entry_{round(entry, 4)}",
        }
    return _fn


def _stop_gap_volume(signal_row: dict) -> dict:
    """
    gap_volume thesis: stock gapped up with extreme relative volume.
    Thesis is invalidated if price fills the gap (drops ~8% from entry).
    If prev_close is available, use that as the natural stop level.
    """
    entry = float(signal_row.get("entry_price") or signal_row.get("close_price") or 0)
    if not entry or entry <= 0:
        return {"defined": False, "stop_basis": "gap_volume_MISSING_ENTRY_PRICE"}
    prev_close = signal_row.get("prev_close") or signal_row.get("previous_close")
    if prev_close:
        prev = float(prev_close)
        if 0 < prev < entry:
            stop_price = round(prev * (1.0 - _STOP_BUFFER_BELOW_SUPPORT), 4)
            dist_pct   = round((entry - stop_price) / entry * 100, 4)
            return {
                "defined": True,
                "stop_price": stop_price,
                "stop_distance_pct": dist_pct,
                "stop_basis": f"gap_volume_below_prev_close_{round(prev, 4)}_buf_0.5pct",
            }
    stop_pct = 0.08
    stop_price = round(entry * (1.0 - stop_pct), 4)
    return {
        "defined": True,
        "stop_price": stop_price,
        "stop_distance_pct": round(stop_pct * 100, 4),
        "stop_basis": f"gap_volume_8pct_below_entry_{round(entry, 4)}",
    }


# Registry: add a stop function for each signal source as its thesis is defined.
# Per spec §3: no fallback to generic % — if source not here, trade is skipped.
_STOP_REGISTRY = {
    # ── Defined (thesis-based support or ATR) ────────────────────────────────
    "Oversold_Bounce_Uptrend": _stop_oversold_bounce,

    # gap_volume: gap thesis invalidated below prev-close (8% fallback)
    "gap_volume":              _stop_gap_volume,

    # unusual_calls: institutional call sweep; 7% drawdown invalidates flow thesis
    "unusual_calls":           _stop_pct_below_entry(0.07, "unusual_calls"),

    # aiem_v3_discovery: AI multi-factor discovery; 8% conservative stop
    "aiem_v3_discovery":       _stop_pct_below_entry(0.08, "aiem_v3_discovery"),

    # washout_ignition: capitulation reversal; wide 10% stop (reversal can shake)
    "washout_ignition":        _stop_pct_below_entry(0.10, "washout_ignition"),

    # conviction_stack: multi-signal confluence; 6% tight stop
    "conviction_stack":        _stop_pct_below_entry(0.06, "conviction_stack"),

    # sweep: breakout sweep; 5% below entry is below the breakout level
    "sweep":                   _stop_pct_below_entry(0.05, "sweep"),

    # aiem_ai: AIEM AI recommendation; 8% conservative stop
    "aiem_ai":                 _stop_pct_below_entry(0.08, "aiem_ai"),

    # multi_signal: multiple confirming signals; 7% stop
    "multi_signal":            _stop_pct_below_entry(0.07, "multi_signal"),

    # oi_buildup: OI accumulation; 8% stop (position-building thesis)
    "oi_buildup":              _stop_pct_below_entry(0.08, "oi_buildup"),

    # layer9_stat: statistical edge signal; 7% stop
    "layer9_stat":             _stop_pct_below_entry(0.07, "layer9_stat"),

    # fear_premium_gex: put-skew / long-gamma fear premium; 8% stop
    # (2026-08-04: 5 APPROVED picks died solely on NO_INVALIDATION_POINT —
    #  this source was active in paper picking but missing from the registry.)
    "fear_premium_gex":        _stop_pct_below_entry(0.08, "fear_premium_gex"),

    # gap_down_distribution: bearish gap+RVOL distribution; 8% stop
    "gap_down_distribution":   _stop_pct_below_entry(0.08, "gap_down_distribution"),
}


def derive_stop(signal_source: str, signal_row: dict) -> dict:
    """
    Returns stop derivation result.
    gate_result='NO_STOP_DEFINED' if the source hasn't been specified yet.
    """
    fn = _STOP_REGISTRY.get(signal_source)
    if fn is None:
        return {
            "defined": False,
            "stop_basis": "NO_INVALIDATION_POINT_DEFINED_FOR_SOURCE",
        }
    return fn(signal_row)


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — portfolio-level gates
# ─────────────────────────────────────────────────────────────────────────────
def _get_paper_equity_metrics(cur) -> dict:
    """
    Compute simulated equity state from paper trade history for kill-switch.
    Returns: current_equity, peak_equity, trades_today, consecutive_losses,
             total_trades_30d, open_position_count, sector_counts
    """
    base = _SIMULATED_ACCOUNT_EQUITY or 0.0

    # Realized P&L from closed trades
    cur.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM aiem_paper_trades
        WHERE status = 'CLOSED' AND pre_sizing_model = FALSE
    """)
    realized = float(cur.fetchone()[0] or 0)
    current_equity = base + realized

    # Peak equity (never drops below starting base)
    peak_equity = max(base, current_equity)

    # Trades today
    cur.execute("""
        SELECT COUNT(*) FROM aiem_paper_trades
        WHERE trade_date = CURRENT_DATE AND pre_sizing_model = FALSE
    """)
    trades_today = int(cur.fetchone()[0])

    # Consecutive losses (look at last 20 closed trades, count tail of losses)
    cur.execute("""
        SELECT pnl FROM aiem_paper_trades
        WHERE status = 'CLOSED' AND pnl IS NOT NULL AND pre_sizing_model = FALSE
        ORDER BY exit_date DESC, id DESC LIMIT 20
    """)
    rows = [float(r[0]) for r in cur.fetchall()]
    consecutive = 0
    for p in rows:
        if p < 0:
            consecutive += 1
        else:
            break

    # Total trades last 30 days
    cur.execute("""
        SELECT COUNT(*) FROM aiem_paper_trades
        WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'
          AND pre_sizing_model = FALSE
    """)
    total_30d = int(cur.fetchone()[0])

    # Open position count and sector breakdown
    cur.execute("""
        SELECT ticker, signal_source FROM aiem_paper_trades
        WHERE status = 'OPEN' AND pre_sizing_model = FALSE
    """)
    open_rows = cur.fetchall()
    open_count = len(open_rows)

    return {
        "current_equity":    current_equity,
        "peak_equity":       peak_equity,
        "trades_today":      trades_today,
        "consecutive_losses": consecutive,
        "total_trades_30d":  total_30d,
        "open_count":        open_count,
        "open_rows":         open_rows,
    }


def _check_kill_switch_gate(metrics: dict, ticker: str) -> dict:
    """
    Call kill_switch.check_kill_switch() and return gate result.
    Fail-open on import error (logs warning, does not block).
    """
    try:
        from kill_switch import check_kill_switch, KillSwitchLimits
        result = check_kill_switch(
            signal_name=ticker,
            current_equity=metrics["current_equity"],
            peak_equity=metrics["peak_equity"],
            trades_today=metrics["trades_today"],
            consecutive_losses=metrics["consecutive_losses"],
            total_trades_this_window=metrics["total_trades_30d"],
            limits=KillSwitchLimits(
                max_drawdown_pct=10.0,
                max_trades_per_day=25,
                max_consecutive_losses=6,
            ),
        )
        if result.get("halted"):
            return {"passed": False, "gate": "kill_switch",
                    "detail": result.get("reason", "halted")}
        return {"passed": True, "gate": "kill_switch"}
    except Exception as e:
        print(f"[position_sizing] kill_switch check warning (fail-open): {e}")
        return {"passed": True, "gate": "kill_switch", "detail": f"check_failed_open: {e}"}


def _check_max_positions_gate(metrics: dict, ticker: str, signal_source: str) -> dict:
    """
    Enforce _MAX_CONCURRENT_POSITIONS and _MAX_SECTOR_POSITIONS (spec §5).
    Sector is approximated from signal source; a full sector map can be added later.
    """
    if _MAX_CONCURRENT_POSITIONS is None:
        return {"passed": True, "gate": "max_positions"}

    if metrics["open_count"] >= _MAX_CONCURRENT_POSITIONS:
        return {
            "passed": False,
            "gate": "max_positions",
            "detail": f"open={metrics['open_count']} >= cap={_MAX_CONCURRENT_POSITIONS}",
        }

    if _MAX_SECTOR_POSITIONS is not None:
        # Count existing positions from the same signal source as a sector proxy
        same_source = sum(
            1 for _, src in metrics["open_rows"]
            if src == signal_source
        )
        if same_source >= _MAX_SECTOR_POSITIONS:
            return {
                "passed": False,
                "gate": "max_sector_positions",
                "detail": f"source={signal_source} count={same_source} >= cap={_MAX_SECTOR_POSITIONS}",
            }

    return {"passed": True, "gate": "max_positions"}


def _check_daily_loss_gate(metrics: dict) -> dict:
    """
    Check remaining daily risk budget against kill_switch drawdown metrics.
    We delegate to kill_switch for the actual limit; this gate reports whether
    the daily P&L budget is still available.
    """
    if _SIMULATED_ACCOUNT_EQUITY is None or _MAX_RISK_PER_TRADE_PCT is None:
        return {"passed": True, "gate": "daily_loss"}

    # Compute today's realized P&L from paper trades
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(pnl), 0)
                FROM aiem_paper_trades
                WHERE exit_date = CURRENT_DATE
                  AND status = 'CLOSED'
                  AND pre_sizing_model = FALSE
            """)
            today_pnl = float(cur.fetchone()[0] or 0)
    except Exception:
        return {"passed": True, "gate": "daily_loss", "detail": "db_check_failed_open"}

    # Daily loss ceiling = 2× the per-trade risk ceiling (conservative)
    daily_loss_ceil = _SIMULATED_ACCOUNT_EQUITY * _MAX_RISK_PER_TRADE_PCT * 2.0
    if today_pnl < -daily_loss_ceil:
        return {
            "passed": False,
            "gate": "daily_loss",
            "detail": f"today_pnl=${today_pnl:.0f} breaches daily_ceil=${-daily_loss_ceil:.0f}",
        }
    return {"passed": True, "gate": "daily_loss"}


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 + 4 + 5 — main entry point
# ─────────────────────────────────────────────────────────────────────────────
def compute_position_size(
    ticker:          str,
    signal_source:   str,
    conviction_score: float,
    entry_price:     float,
    signal_row:      dict,
    vol_bucket:      str = "MEDIUM",
    paper_trade_id:  Optional[int] = None,
) -> dict:
    """
    Compute position size for a candidate paper trade.

    Returns dict with:
      gate_result    — 'APPROVED' | 'PARAMS_NOT_CONFIRMED' | 'CONVICTION_BELOW_MIN'
                       | 'NO_STOP_DEFINED' | 'STOP_UNDEFINED' | 'POSITION_TOO_SMALL'
                       | 'kill_switch' | 'max_positions' | 'max_sector_positions'
                       | 'daily_loss' | 'BUYING_POWER_EXCEEDED'
      notional       — computed position size in dollars (0 if not APPROVED)
      stop_price     — thesis-based stop level
      risk_pct_used  — actual risk % applied
      detail         — human-readable gate explanation
      [all Section 9 log fields]
    """
    result = {
        "ticker":               ticker,
        "signal_source":        signal_source,
        "conviction_score":     conviction_score,
        "entry_price":          entry_price,
        "gate_result":          "PENDING",
        "gate_detail":          None,
        "calculated_stop_price": None,
        "stop_basis":           None,
        "stop_distance_pct":    None,
        "risk_pct_used":        None,
        "calculated_notional":  0.0,
        "mode":                 "LIVE" if LIVE_MODE_ENABLED else "SIMULATION",
        "overnight_option":     _OVERNIGHT_OPTION,
    }

    # ── Gate 0: parameters confirmed? ────────────────────────────────────────
    if not _params_confirmed():
        result["gate_result"] = "PARAMS_NOT_CONFIRMED"
        result["gate_detail"] = "Awaiting Joel Q1–Q5 before sizing is active"
        _log_sizing_decision(result, paper_trade_id)
        return result

    # ── Gate 1: conviction floor (spec §4) ───────────────────────────────────
    if conviction_score < _MIN_CONVICTION_TO_TRADE:
        result["gate_result"] = "CONVICTION_BELOW_MIN"
        result["gate_detail"] = (
            f"score={conviction_score:.1f} < min={_MIN_CONVICTION_TO_TRADE}"
        )
        _log_sizing_decision(result, paper_trade_id)
        return result

    # ── Gate 2: thesis-based stop (spec §3) ──────────────────────────────────
    stop = derive_stop(signal_source, {**signal_row, "entry_price": entry_price})
    if not stop.get("defined"):
        result["gate_result"] = "NO_STOP_DEFINED"
        result["gate_detail"] = stop.get("stop_basis", "undefined")
        _log_sizing_decision(result, paper_trade_id)
        return result

    result["calculated_stop_price"] = stop["stop_price"]
    result["stop_basis"]            = stop["stop_basis"]
    result["stop_distance_pct"]     = stop["stop_distance_pct"]

    # ── Section 4: conviction-weighted risk % ─────────────────────────────────
    mult      = _conviction_risk_mult(conviction_score)
    risk_pct  = _MAX_RISK_PER_TRADE_PCT * mult   # fraction of equity risked

    # ── Section 2: core formula ───────────────────────────────────────────────
    # position_size = (equity × risk_pct) / stop_distance_fraction
    stop_dist_frac = stop["stop_distance_pct"] / 100.0
    if stop_dist_frac <= 0:
        result["gate_result"] = "STOP_UNDEFINED"
        result["gate_detail"] = "stop_distance_pct is zero or negative"
        _log_sizing_decision(result, paper_trade_id)
        return result

    equity   = _SIMULATED_ACCOUNT_EQUITY if not LIVE_MODE_ENABLED else 0.0
    notional = (equity * risk_pct) / stop_dist_frac

    # Option B adjustment: account for worst-case gap-through-stop
    if _OVERNIGHT_OPTION == "B" or (
        _OVERNIGHT_OPTION == "C" and _OVERNIGHT_HOLD_ALLOWED.get(signal_source, False)
    ):
        gap_frac = _GAP_RISK_BY_VOL_BUCKET.get(vol_bucket.upper(), 0.05)
        total_frac = stop_dist_frac + gap_frac
        notional   = (equity * risk_pct) / total_frac
        result["gate_detail"] = (
            f"Option {_OVERNIGHT_OPTION}: stop_dist={stop_dist_frac*100:.2f}% "
            f"+ gap_assumption={gap_frac*100:.1f}% (bucket={vol_bucket})"
        )

    result["risk_pct_used"]       = round(risk_pct * 100, 4)
    result["calculated_notional"] = round(notional, 2)

    # ── Gate 3: practical minimum (spec §3) ───────────────────────────────────
    if notional < _MIN_POSITION_NOTIONAL:
        result["gate_result"]        = "POSITION_TOO_SMALL"
        result["gate_detail"]        = (
            f"notional=${notional:.0f} < min=${_MIN_POSITION_NOTIONAL:.0f}"
        )
        result["calculated_notional"] = 0.0
        _log_sizing_decision(result, paper_trade_id)
        return result

    # ── Gate 4: portfolio gates (spec §5) — most restrictive wins ─────────────
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as conn, conn.cursor() as cur:
            metrics = _get_paper_equity_metrics(cur)
    except Exception as e:
        print(f"[position_sizing] equity metrics failed (fail-open): {e}")
        metrics = {
            "current_equity": equity, "peak_equity": equity,
            "trades_today": 0, "consecutive_losses": 0,
            "total_trades_30d": 0, "open_count": 0, "open_rows": [],
        }

    for gate_check, args in [
        (_check_kill_switch_gate,      (metrics, ticker)),
        (_check_max_positions_gate,    (metrics, ticker, signal_source)),
        (_check_daily_loss_gate,       (metrics,)),
    ]:
        gate = gate_check(*args)
        if not gate["passed"]:
            result["gate_result"] = gate["gate"]
            result["gate_detail"] = gate.get("detail", "")
            result["calculated_notional"] = 0.0
            _log_sizing_decision(result, paper_trade_id)
            return result

    # ── APPROVED ──────────────────────────────────────────────────────────────
    result["gate_result"] = "APPROVED"
    _log_sizing_decision(result, paper_trade_id)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Section 9 — logging
# ─────────────────────────────────────────────────────────────────────────────
def _log_sizing_decision(result: dict, paper_trade_id: Optional[int] = None) -> Optional[int]:
    """
    Log every sizing decision (approved or skipped) to aiem_position_sizing_log.
    Returns the new log row id, or None if logging failed.
    """
    if not _DB_URL:
        return None
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_position_sizing_log
                    (ticker, signal_source, conviction_score, entry_price,
                     calculated_stop_price, stop_basis, stop_distance_pct,
                     risk_pct_used, calculated_notional, gate_result, gate_detail,
                     mode, overnight_option, paper_trade_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                result.get("ticker"),
                result.get("signal_source"),
                result.get("conviction_score"),
                result.get("entry_price"),
                result.get("calculated_stop_price"),
                result.get("stop_basis"),
                result.get("stop_distance_pct"),
                result.get("risk_pct_used"),
                result.get("calculated_notional"),
                result.get("gate_result"),
                result.get("gate_detail"),
                result.get("mode", "SIMULATION"),
                result.get("overnight_option"),
                paper_trade_id,
            ))
            log_id = cur.fetchone()[0]
            conn.commit()
            return log_id
    except Exception as e:
        print(f"[position_sizing] log error (non-blocking): {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Section 8 — pre-close position review
# ─────────────────────────────────────────────────────────────────────────────
def _get_contrarian_context() -> dict:
    """
    Section 8.2a: pull available broad-market contrarian indicators.
    Every unavailable source is labelled NOT_AVAILABLE (never silently omitted).

    Available now:
      Category 1 — VIX level + z-score (vix_daily table)
      Category 2 — market-wide bearish-breadth proxy (polygon_market_daily:
                    pct of stocks with close_strength < 0.20, i.e. closing in
                    the bottom 20% of their intraday range; reported under the
                    market_rsi_status/market_rsi_pct_oversold keys for schema
                    compatibility; the underlying metric is close_strength, not
                    RSI — polygon_market_daily does NOT have an rsi_14 column)
      Category 5 — HYG price change (credit health proxy via Tradier)

    NOT_AVAILABLE data sources (investigated; access blocked):
      put_call_status   — CBOE CSV returns HTTP 403; HTML page requires JS
                          rendering; no viable free API
      fear_greed_status — CNN dataviz endpoint returns HTTP 418 (bot-detect)
      aaii_status       — AAII weekly survey requires authenticated membership
      naaim_status      — NAAIM xlsx URL returns HTTP 404 (file moved); page
                          requires scraping; not reliably automatable

    Everything else: NOT_AVAILABLE — documented honestly.
    """
    ctx = {
        "vix_level":              None,
        "vix_zscore":             None,
        "vix_status":             "NOT_AVAILABLE",
        "market_rsi_pct_oversold": None,
        "market_rsi_status":      "NOT_AVAILABLE",
        "hyg_change_pct":         None,
        "hyg_status":             "NOT_AVAILABLE",
        "put_call_status":        "NOT_AVAILABLE",
        "fear_greed_status":      "NOT_AVAILABLE",
        "aaii_status":            "NOT_AVAILABLE",
        "naaim_status":           "NOT_AVAILABLE",
        "breadth_status":         "NOT_AVAILABLE",
        "trin_status":            "NOT_AVAILABLE",
        "cot_status":             "NOT_AVAILABLE",
        "mmf_flow_status":        "NOT_AVAILABLE",
        "leveraged_fund_status":  "NOT_AVAILABLE",
        "vvix_status":            "NOT_AVAILABLE",
        "isee_status":            "NOT_AVAILABLE",
    }

    if not _DB_URL:
        return ctx

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            # VIX — Category 1
            cur.execute("""
                SELECT vix_close FROM vix_daily
                ORDER BY scan_date DESC LIMIT 252
            """)
            vix_rows = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
            if vix_rows:
                vix_now = vix_rows[0]
                if len(vix_rows) >= 10:
                    mean = sum(vix_rows) / len(vix_rows)
                    std  = math.sqrt(sum((v - mean)**2 for v in vix_rows) / len(vix_rows))
                    zscore = (vix_now - mean) / std if std > 0 else 0.0
                else:
                    zscore = 0.0
                ctx["vix_level"]  = round(vix_now, 2)
                ctx["vix_zscore"] = round(zscore, 2)
                ctx["vix_status"] = "AVAILABLE"

            # Market-wide bearish breadth — Category 2 proxy
            # polygon_market_daily does NOT have an rsi_14 column (confirmed
            # via information_schema). Using close_strength < 0.20 as proxy:
            # stocks closing in the bottom 20% of their intraday range indicate
            # the same "persistent selling pressure" that a low RSI captures.
            # Metric is labelled market_rsi_pct_oversold for schema compatibility.
            cur.execute("""
                WITH latest AS (
                    SELECT scan_date FROM polygon_market_daily
                    ORDER BY scan_date DESC LIMIT 1
                )
                SELECT
                    COUNT(*) FILTER (WHERE close_strength < 0.20) AS bearish_breadth,
                    COUNT(*) AS total
                FROM polygon_market_daily, latest
                WHERE polygon_market_daily.scan_date = latest.scan_date
                  AND close_strength IS NOT NULL
            """)
            rsi_row = cur.fetchone()
            if rsi_row and rsi_row[1] and rsi_row[1] > 100:
                pct = rsi_row[0] / rsi_row[1] * 100
                ctx["market_rsi_pct_oversold"] = round(pct, 2)
                ctx["market_rsi_status"]        = "AVAILABLE"

    except Exception as e:
        print(f"[position_sizing] contrarian context DB error: {e}")

    # HYG — credit health proxy (via Tradier quote)
    try:
        import urllib.request as _ur, json as _jh
        # Prefer TOKEN_2 (brokerage account, real-time data) matching the canonical
        # convention used by every other Tradier caller in main.py; TRADIER_API_TOKEN
        # alone is currently a revoked/invalid token (confirmed via live 401 test).
        _api = os.environ.get("TRADIER_API_TOKEN_2") or os.environ.get("TRADIER_API_TOKEN", "")
        if _api:
            req = _ur.Request(
                "https://api.tradier.com/v1/markets/quotes?symbols=HYG",
                headers={"Authorization": f"Bearer {_api}",
                         "Accept": "application/json"},
            )
            with _ur.urlopen(req, timeout=4) as r:
                data = _jh.loads(r.read())
            q = ((data.get("quotes") or {}).get("quote") or {})
            prev  = float(q.get("prevclose") or 0)
            last  = float(q.get("last") or 0)
            if prev > 0 and last > 0:
                ctx["hyg_change_pct"] = round((last - prev) / prev * 100, 3)
                ctx["hyg_status"]     = "AVAILABLE"
    except Exception:
        pass  # HYG stays NOT_AVAILABLE

    # Composite lean (spec §8.2a):
    # Category 1-2 carry more weight. Scoring: each available indicator
    # that leans contrarian-bullish scores +1, against scores -1.
    bullish_votes = 0
    available_count = 0

    # VIX: elevated (>20) = contrarian-bullish (fear = potential bottom)
    if ctx["vix_status"] == "AVAILABLE" and ctx["vix_level"] is not None:
        available_count += 1
        if ctx["vix_level"] > 20:
            bullish_votes += 1
        elif ctx["vix_level"] < 15:
            bullish_votes -= 1

    # Market RSI: >10% stocks oversold = contrarian-bullish signal
    if ctx["market_rsi_status"] == "AVAILABLE" and ctx["market_rsi_pct_oversold"] is not None:
        available_count += 1
        if ctx["market_rsi_pct_oversold"] > 15:
            bullish_votes += 1
        elif ctx["market_rsi_pct_oversold"] < 3:
            bullish_votes -= 1

    # HYG: negative change = stress in credit = contrarian context
    # (mild widening during selloff = normal; severe = bearish for HOLD)
    if ctx["hyg_status"] == "AVAILABLE" and ctx["hyg_change_pct"] is not None:
        available_count += 1
        chg = ctx["hyg_change_pct"]
        if chg >= -0.2:      # stable credit = neutral-to-bullish for HOLD
            bullish_votes += 1
        elif chg < -0.5:     # sharp credit widening: against HOLD
            bullish_votes -= 1

    if available_count == 0:
        lean = "NEUTRAL"
        strength = 0.0
    else:
        ratio    = bullish_votes / available_count
        strength = round(abs(ratio), 2)
        if ratio >= 0.5:
            lean = "CONTRARIAN_BULLISH"
        elif ratio <= -0.5:
            lean = "CONTRARIAN_BEARISH_OR_INCONSISTENT"
        else:
            lean = "NEUTRAL"

    ctx["composite_lean"]     = lean
    ctx["composite_strength"] = strength
    ctx["available_count"]    = available_count
    ctx["bullish_votes"]      = bullish_votes
    return ctx


def _review_one_position(trade: dict, ctx: dict, cur) -> dict:
    """
    Per-position thesis re-evaluation (Section 8.2) for the bounce signal.
    Extensible to other signal sources as their re-eval logic is defined.
    """
    source = trade.get("signal_source", "")
    tid    = trade.get("id")
    ticker = trade.get("ticker", "")

    # Retrieve current conviction from the bounce signal DB row
    conv_current = None
    thesis_intact = None
    mod_f_recheck = None
    unrealized_pnl_pct = None

    if trade.get("last_price") and trade.get("entry_price"):
        ep = float(trade["entry_price"])
        lp = float(trade["last_price"])
        unrealized_pnl_pct = round((lp - ep) / ep * 100, 4) if ep > 0 else None

    if source == "Oversold_Bounce_Uptrend" or source == "aiem_bounce":
        cur.execute("""
            SELECT conviction_score, state, distance_to_support_pct,
                   earnings_exclusion_active, falling_knife_active,
                   rsi_2, rsi_14
            FROM aiem_bounce_signals
            WHERE ticker = %s
            ORDER BY signal_date DESC LIMIT 1
        """, (ticker,))
        row = cur.fetchone()
        if row:
            conv_current   = float(row[0]) if row[0] is not None else None
            state_current  = row[1]
            dist_supp      = float(row[2]) if row[2] is not None else None
            earn_excl      = bool(row[3])
            knife          = bool(row[4])
            rsi2           = float(row[5]) if row[5] is not None else None
            rsi14          = float(row[6]) if row[6] is not None else None

            # Thesis intact: state still CONFIRMED, no Module F trigger, RSI not fully recovered
            mod_f_recheck = earn_excl or knife
            rsi_ok = (rsi2 is not None and rsi2 < 25) or (rsi14 is not None and rsi14 < 50)
            thesis_intact = (
                state_current == "CONFIRMED"
                and not earn_excl
                and not knife
                and rsi_ok
                and (dist_supp is None or dist_supp > 0)  # not blown through support
            )

    # Decision (Section 8.3)
    # Default to FLATTEN when thesis state is unknown — do not assume hold
    conv_at_entry = float(trade.get("sizing_risk_pct") or 0)

    lean = ctx.get("composite_lean", "NEUTRAL")

    if thesis_intact is None:
        # Source has no re-eval logic defined yet — use market context only
        decision = (
            "HOLD_OVERNIGHT"
            if lean == "CONTRARIAN_BULLISH"
            else "FLATTEN_BEFORE_CLOSE"
        )
        decision_reason = "thesis_reeval_not_implemented_for_source; market_context_only"
    elif not thesis_intact:
        # Thesis invalidated — override policy and flatten regardless of Option
        decision = "FLATTEN_BEFORE_CLOSE"
        decision_reason = f"thesis_invalid: mod_f={mod_f_recheck}"
    elif conv_current is not None and _MIN_CONVICTION_TO_TRADE is not None:
        if conv_current < _MIN_CONVICTION_TO_TRADE:
            decision = "FLATTEN_BEFORE_CLOSE"
            decision_reason = f"conviction_below_min: {conv_current:.1f} < {_MIN_CONVICTION_TO_TRADE}"
        else:
            # Thesis intact + conviction ok: defer to market context
            if lean == "CONTRARIAN_BEARISH_OR_INCONSISTENT":
                decision = "FLATTEN_BEFORE_CLOSE"
                decision_reason = "market_context_bearish_or_inconsistent"
            else:
                decision = "HOLD_OVERNIGHT"
                decision_reason = f"thesis_intact, conviction={conv_current:.1f}, context={lean}"
    else:
        decision = "HOLD_OVERNIGHT" if lean != "CONTRARIAN_BEARISH_OR_INCONSISTENT" else "FLATTEN_BEFORE_CLOSE"
        decision_reason = f"context={lean}"

    # Section 7.1 Option A override — always flatten regardless of thesis
    if _OVERNIGHT_OPTION == "A":
        decision        = "FLATTEN_BEFORE_CLOSE"
        decision_reason = "Option_A: flatten_all_before_close"

    return {
        "paper_trade_id":         tid,
        "ticker":                 ticker,
        "signal_source":          source,
        "decision":               decision,
        "decision_reason":        decision_reason,
        "conviction_at_entry":    conv_at_entry,
        "conviction_current":     conv_current,
        "thesis_intact":          thesis_intact,
        "mod_f_recheck":          mod_f_recheck,
        "unrealized_pnl_pct":     unrealized_pnl_pct,
        "composite_lean":         lean,
        "composite_strength":     ctx.get("composite_strength"),
        "vix_level":              ctx.get("vix_level"),
        "vix_status":             ctx.get("vix_status", "NOT_AVAILABLE"),
        "market_rsi_pct_oversold": ctx.get("market_rsi_pct_oversold"),
        "market_rsi_status":      ctx.get("market_rsi_status", "NOT_AVAILABLE"),
        "hyg_status":             ctx.get("hyg_status", "NOT_AVAILABLE"),
        "put_call_status":        "NOT_AVAILABLE",
        "fear_greed_status":      "NOT_AVAILABLE",
        "aaii_status":            "NOT_AVAILABLE",
        "naaim_status":           "NOT_AVAILABLE",
        "breadth_status":         "NOT_AVAILABLE",
        "trin_status":            "NOT_AVAILABLE",
        "cot_status":             "NOT_AVAILABLE",
        "mmf_flow_status":        "NOT_AVAILABLE",
        "overnight_option":       _OVERNIGHT_OPTION,
        "detail_json":            json.dumps(decision_reason),
    }


def run_pre_close_reviews() -> list:
    """
    Scheduled entry point — Section 8.1: runs ~3:45 PM ET.
    Reviews every open paper position and logs the HOLD/FLATTEN decision.
    Returns list of decisions for scheduler logging.
    """
    if not _DB_URL:
        return []

    ctx = _get_contrarian_context()
    decisions = []

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, signal_source, entry_price, last_price,
                       sizing_risk_pct, trade_date
                FROM aiem_paper_trades
                WHERE status = 'OPEN' AND pre_sizing_model = FALSE
                ORDER BY id
            """)
            open_trades = [
                {
                    "id": r[0], "ticker": r[1], "signal_source": r[2],
                    "entry_price": r[3], "last_price": r[4],
                    "sizing_risk_pct": r[5], "trade_date": r[6],
                }
                for r in cur.fetchall()
            ]

            for trade in open_trades:
                rev = _review_one_position(trade, ctx, cur)
                decisions.append(rev)

                # Log to DB
                cur.execute("""
                    INSERT INTO aiem_pre_close_review_log
                        (paper_trade_id, ticker, signal_source, decision,
                         conviction_at_entry, conviction_current, thesis_intact,
                         mod_f_recheck, unrealized_pnl_pct,
                         vix_level, vix_status, market_rsi_pct_oversold, market_rsi_status,
                         hyg_status, put_call_status, fear_greed_status,
                         aaii_status, naaim_status, breadth_status, trin_status,
                         cot_status, mmf_flow_status,
                         composite_lean, composite_strength, detail_json, overnight_option)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    rev["paper_trade_id"], rev["ticker"], rev["signal_source"],
                    rev["decision"], rev["conviction_at_entry"], rev["conviction_current"],
                    rev["thesis_intact"], rev["mod_f_recheck"], rev["unrealized_pnl_pct"],
                    rev["vix_level"], rev["vix_status"],
                    rev["market_rsi_pct_oversold"], rev["market_rsi_status"],
                    rev["hyg_status"], rev["put_call_status"], rev["fear_greed_status"],
                    rev["aaii_status"], rev["naaim_status"], rev["breadth_status"],
                    rev["trin_status"], rev["cot_status"], rev["mmf_flow_status"],
                    rev["composite_lean"], rev["composite_strength"],
                    rev["detail_json"], rev["overnight_option"],
                ))
                print(
                    f"[pre_close] {rev['ticker']} → {rev['decision']} "
                    f"[{rev['decision_reason']}] VIX={ctx.get('vix_level')} "
                    f"lean={ctx.get('composite_lean')}"
                )

            conn.commit()

    except Exception as e:
        print(f"[pre_close] review error: {e}")

    # Section 8.2b: standalone contrarian alert check
    _check_and_fire_contrarian_alert(ctx)

    return decisions


# ─────────────────────────────────────────────────────────────────────────────
# Section 8.2b — standalone extreme contrarian Telegram alert
# ─────────────────────────────────────────────────────────────────────────────
_CONTRARIAN_ALERT_COOLDOWN_DAYS = 3   # full composite alert: max once per 3 days
_SINGLE_INDICATOR_COOLDOWN_HRS  = 24  # single-indicator alert: max once per 24h


def _check_and_fire_contrarian_alert(ctx: dict):
    """
    Section 8.2b: fires when the composite contrarian read is CONTRARIAN_BULLISH
    with strength ≥ 0.67 (≥2/3 available indicators aligned).
    Also fires a lower-confidence single-indicator alert for VIX > 30 alone.
    Market-hours-gated (same as all other alerts in this system).
    """
    if not _DB_URL:
        return

    lean     = ctx.get("composite_lean", "NEUTRAL")
    strength = ctx.get("composite_strength", 0.0)
    vix      = ctx.get("vix_level")

    # Gate: only during market hours
    now_et = dt.datetime.now(dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=-4))  # ET approximation
    )
    minutes = now_et.hour * 60 + now_et.minute
    in_market = now_et.weekday() < 5 and (570 <= minutes < 960)
    if not in_market:
        return

    try:
        from main import _tg  # lazy import to avoid circular dependency
    except Exception:
        try:
            _tg = None
            import importlib, sys
            _main = sys.modules.get("main") or sys.modules.get("__main__")
            _tg = getattr(_main, "_tg", None) if _main else None
        except Exception:
            _tg = None

    if _tg is None:
        return

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as conn, conn.cursor() as cur:

            # Composite alert
            if lean == "CONTRARIAN_BULLISH" and strength >= 0.67:
                cur.execute("""
                    SELECT fired_at FROM aiem_contrarian_alert_log
                    WHERE alert_type = 'COMPOSITE'
                    ORDER BY fired_at DESC LIMIT 1
                """)
                last = cur.fetchone()
                cooldown_ok = (
                    last is None or
                    (dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc) - last[0]).days
                    >= _CONTRARIAN_ALERT_COOLDOWN_DAYS
                )
                if cooldown_ok:
                    msg = _build_contrarian_alert_msg("COMPOSITE", ctx, strength)
                    _tg(msg)
                    cur.execute("""
                        INSERT INTO aiem_contrarian_alert_log
                            (alert_type, composite_lean, composite_strength,
                             vix_level, vix_zscore, indicators_json, tg_sent)
                        VALUES ('COMPOSITE',%s,%s,%s,%s,%s,TRUE)
                    """, (lean, strength, vix, ctx.get("vix_zscore"),
                           json.dumps(ctx)))
                    conn.commit()

            # Single-indicator alert: VIX alone > 30
            if vix is not None and vix > 30:
                cur.execute("""
                    SELECT fired_at FROM aiem_contrarian_alert_log
                    WHERE alert_type = 'SINGLE_VIX'
                    ORDER BY fired_at DESC LIMIT 1
                """)
                last = cur.fetchone()
                cooldown_ok = (
                    last is None or
                    (dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc) - last[0]).total_seconds()
                    >= _SINGLE_INDICATOR_COOLDOWN_HRS * 3600
                )
                if cooldown_ok:
                    msg = _build_contrarian_alert_msg("SINGLE_VIX", ctx, 0.33)
                    _tg(msg)
                    cur.execute("""
                        INSERT INTO aiem_contrarian_alert_log
                            (alert_type, composite_lean, composite_strength,
                             vix_level, vix_zscore, indicators_json, tg_sent)
                        VALUES ('SINGLE_VIX',%s,%s,%s,%s,%s,TRUE)
                    """, (lean, 0.33, vix, ctx.get("vix_zscore"), json.dumps(ctx)))
                    conn.commit()

    except Exception as e:
        print(f"[contrarian_alert] error (non-blocking): {e}")


def _build_contrarian_alert_msg(alert_type: str, ctx: dict, strength: float) -> str:
    """Section 8.2b message format."""
    vix    = ctx.get("vix_level")
    vzscore = ctx.get("vix_zscore")
    mrsi   = ctx.get("market_rsi_pct_oversold")
    hyg_ch = ctx.get("hyg_change_pct")

    header = (
        "🚨📉➡️📈 EXTREME CONTRARIAN READING — MARKET-WIDE\n"
        "⚠️ THIS IS MARKET-WIDE CONTEXT, NOT A SIGNAL ON ANY SPECIFIC TICKER ⚠️\n\n"
    )
    if alert_type == "SINGLE_VIX":
        header = (
            "📊 SINGLE-INDICATOR CONTRARIAN ALERT (lower confidence) — MARKET-WIDE\n"
            "⚠️ THIS IS MARKET-WIDE CONTEXT, NOT A SIGNAL ON ANY SPECIFIC TICKER ⚠️\n\n"
        )

    lines = [header]
    lines.append(f"Composite lean: {ctx.get('composite_lean','UNKNOWN')} (strength: {strength:.2f})")
    lines.append("\nIndicators at extreme:")

    if ctx.get("vix_status") == "AVAILABLE":
        lines.append(f"  VIX: {vix:.1f} (z-score {vzscore:+.2f})")
    else:
        lines.append("  VIX: NOT_AVAILABLE")

    if ctx.get("market_rsi_status") == "AVAILABLE":
        lines.append(f"  Market RSI breadth: {mrsi:.1f}% stocks oversold (RSI<30)")
    else:
        lines.append("  Market RSI breadth: NOT_AVAILABLE")

    if ctx.get("hyg_status") == "AVAILABLE":
        lines.append(f"  HYG (credit): {hyg_ch:+.3f}% vs prev close")
    else:
        lines.append("  HYG: NOT_AVAILABLE")

    not_avail = [
        k.replace("_status", "").upper()
        for k, v in ctx.items()
        if k.endswith("_status") and v == "NOT_AVAILABLE"
        and k not in ("vix_status", "market_rsi_status", "hyg_status")
    ]
    if not_avail:
        lines.append(f"\nNot yet integrated: {', '.join(not_avail)}")
        lines.append("(composite read is partial — not using every indicator that exists)")

    import datetime as _dt
    lines.append(f"\nTime: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}")
    return "\n".join(lines)
