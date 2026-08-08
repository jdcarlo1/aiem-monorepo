"""
aiem_macro_engine.py — AIEM v3 Phase 1 + Phase 2
=================================================
Phase 1 : DB schema bootstrap for all 12 AIEM v3 tables.
Phase 2 : Macro Engine — deterministic, zero-LLM, Polygon/Tradier/Yahoo only.

Public API
----------
  init_v3_schema(conn)         — idempotent CREATE TABLE IF NOT EXISTS for all 12 tables
  compute_macro_snapshot()     — fetch live data, score 0-100, persist, return MacroSnapshot
  get_macro_gate()             — call inside paper-trade execute; returns (pass:bool, snapshot)
  get_cached_macro_snapshot()  — returns most-recent row from aiem_macro_daily (no network)

Scoring
-------
  Equity Trend   (40 pts) : SPY/QQQ/DIA/IWM vs their 20-day SMA
  Volatility     (30 pts) : VIX level + VIX/VIX-20d ratio
  Breadth        (20 pts) : IWM (small-cap) vs SPY spread (proxy for risk-on breadth)
  Credit / Risk  (10 pts) : DXY direction (dollar strength = risk-off headwind)

Regimes
-------
  BULL_STRONG    score >= 65
  BULL_MODERATE  50 <= score < 65
  NEUTRAL        35 <= score < 50
  BEAR_CAUTION   20 <= score < 35
  BEAR_SEVERE    score < 20  → hard block on paper trades

Position-size modifiers
-----------------------
  BULL_STRONG    1.25
  BULL_MODERATE  1.00
  NEUTRAL        0.75
  BEAR_CAUTION   0.50
  BEAR_SEVERE    0.00  (no trades)

Rules
-----
  • Tradier for SPY/QQQ/DIA/IWM (no Yahoo for these — Yahoo circuit-breaker risk)
  • Yahoo for ^VIX / DX-Y.NYB / ^TNX only (no Tradier feed for these indices)
  • DB session is GMT; always use AT TIME ZONE 'America/New_York' for ET dates
  • Fail-safe: if all data fetches fail, macro_score = 50 (NEUTRAL), trades proceed
  • Cache TTL: 55 minutes — compute_macro_snapshot is called at 9:00 AM ET by scheduler
    and the gate check at 9:35 AM will hit the cache.
"""



from __future__ import annotations

from aiem_broker.tradier_config import TRADIER_API_BASE

import os
import math
import json
import datetime
import threading
import traceback
from dataclasses import dataclass, asdict
from typing import Optional

import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL", "")
_TRADIER_TOKEN = os.environ.get("TRADIER_API_TOKEN", "")
_TRADIER_TOKEN_2 = os.environ.get("TRADIER_API_TOKEN_2", "")
# TOKEN_2 is the live account token (TOKEN_1 is sandbox — 401 on market history)
_TRADIER_LIVE_TOKEN = _TRADIER_TOKEN_2 or _TRADIER_TOKEN

# ── Thread safety ─────────────────────────────────────────────────────────────
_compute_lock = threading.Lock()

# ── Regime constants ──────────────────────────────────────────────────────────
_REGIME_THRESHOLDS = [
    (65, "BULL_STRONG",   1.25),
    (50, "BULL_MODERATE", 1.00),
    (35, "NEUTRAL",       0.75),
    (20, "BEAR_CAUTION",  0.50),
    ( 0, "BEAR_SEVERE",   0.00),
]

_BLOCK_BELOW = 20  # hard block when macro_score < this

# ── Snapshot dataclass ────────────────────────────────────────────────────────
@dataclass
class MacroSnapshot:
    snapshot_date: str          # YYYY-MM-DD (ET)
    computed_at: str            # ISO timestamp (UTC)
    macro_score: float          # 0-100
    regime: str
    position_size_modifier: float
    # equity trend sub-score (40 pts)
    spy_close: Optional[float]
    spy_sma20: Optional[float]
    qqq_close: Optional[float]
    qqq_sma20: Optional[float]
    dia_close: Optional[float]
    dia_sma20: Optional[float]
    iwm_close: Optional[float]
    iwm_sma20: Optional[float]
    equity_score: float         # 0-40
    # volatility sub-score (30 pts)
    vix: Optional[float]
    vix_sma20: Optional[float]
    vol_score: float            # 0-30
    # breadth sub-score (20 pts)
    breadth_score: float        # 0-20
    # credit/dollar sub-score (10 pts)
    dxy: Optional[float]
    dxy_prev: Optional[float]
    credit_score: float         # 0-10
    # metadata
    data_quality: str           # FULL / PARTIAL / FALLBACK
    block_trades: bool
    warning: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_line(self) -> str:
        regime_emoji = {
            "BULL_STRONG": "🟢", "BULL_MODERATE": "🟡",
            "NEUTRAL": "⚪", "BEAR_CAUTION": "🟠", "BEAR_SEVERE": "🔴",
        }.get(self.regime, "⚪")
        return (
            f"{regime_emoji} Macro {self.macro_score:.0f}/100 [{self.regime}] "
            f"| VIX={self.vix or '?'} "
            f"| SPY vs SMA20={'✅' if (self.spy_close or 0) > (self.spy_sma20 or 0) else '❌'} "
            f"| Block={'YES ⛔' if self.block_trades else 'No'}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — DB SCHEMA (all 12 AIEM v3 tables)
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
-- ── Phase 2: Macro Engine ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_macro_daily (
    id                    BIGSERIAL PRIMARY KEY,
    snapshot_date         DATE        NOT NULL,
    computed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    macro_score           NUMERIC(6,2) NOT NULL,
    regime                TEXT        NOT NULL,
    position_size_modifier NUMERIC(5,3) NOT NULL,
    spy_close             NUMERIC(10,4),
    spy_sma20             NUMERIC(10,4),
    qqq_close             NUMERIC(10,4),
    qqq_sma20             NUMERIC(10,4),
    dia_close             NUMERIC(10,4),
    dia_sma20             NUMERIC(10,4),
    iwm_close             NUMERIC(10,4),
    iwm_sma20             NUMERIC(10,4),
    equity_score          NUMERIC(5,2) NOT NULL DEFAULT 0,
    vix                   NUMERIC(8,3),
    vix_sma20             NUMERIC(8,3),
    vol_score             NUMERIC(5,2) NOT NULL DEFAULT 0,
    breadth_score         NUMERIC(5,2) NOT NULL DEFAULT 0,
    dxy                   NUMERIC(8,3),
    dxy_prev              NUMERIC(8,3),
    credit_score          NUMERIC(5,2) NOT NULL DEFAULT 0,
    data_quality          TEXT        NOT NULL DEFAULT 'FULL',
    block_trades          BOOLEAN     NOT NULL DEFAULT FALSE,
    warning               TEXT,
    raw_payload           JSONB,
    CONSTRAINT aiem_macro_daily_uq UNIQUE (snapshot_date)
);

-- ── Phase 3: Discovery Memory ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_discovery_memory (
    id               BIGSERIAL PRIMARY KEY,
    discovery_date   DATE        NOT NULL,
    session_id       TEXT        NOT NULL,
    ticker           TEXT        NOT NULL,
    discovery_type   TEXT        NOT NULL,  -- e.g. 'gap_breakout', 'rvol_spike'
    raw_signal       JSONB       NOT NULL,
    confidence       NUMERIC(5,3),
    promoted_to_pick BOOLEAN     NOT NULL DEFAULT FALSE,
    promotion_reason TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aiem_disc_mem_date ON aiem_discovery_memory (discovery_date);
CREATE INDEX IF NOT EXISTS idx_aiem_disc_mem_ticker ON aiem_discovery_memory (ticker);

-- ── Phase 4a: Trend Scores ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_trend_scores (
    id           BIGSERIAL PRIMARY KEY,
    score_date   DATE        NOT NULL,
    ticker       TEXT        NOT NULL,
    trend_score  NUMERIC(5,2) NOT NULL,
    adx          NUMERIC(8,3),
    sma20        NUMERIC(10,4),
    sma50        NUMERIC(10,4),
    close        NUMERIC(10,4),
    above_sma20  BOOLEAN,
    above_sma50  BOOLEAN,
    momentum_5d  NUMERIC(8,4),
    momentum_20d NUMERIC(8,4),
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT aiem_trend_scores_uq UNIQUE (score_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_aiem_trend_date ON aiem_trend_scores (score_date);

-- ── Phase 4b: Sector Leadership ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_sector_leadership (
    id              BIGSERIAL PRIMARY KEY,
    score_date      DATE        NOT NULL,
    sector          TEXT        NOT NULL,
    leadership_rank INTEGER     NOT NULL,
    relative_perf_5d NUMERIC(8,4),
    relative_perf_20d NUMERIC(8,4),
    etf_ticker      TEXT,
    etf_close       NUMERIC(10,4),
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT aiem_sector_leadership_uq UNIQUE (score_date, sector)
);
CREATE INDEX IF NOT EXISTS idx_aiem_sector_date ON aiem_sector_leadership (score_date);

-- ── Phase 4c: Technical Scores (per-ticker, pre-computed) ─────────────────────
CREATE TABLE IF NOT EXISTS aiem_technical_scores (
    id             BIGSERIAL PRIMARY KEY,
    score_date     DATE        NOT NULL,
    ticker         TEXT        NOT NULL,
    technical_score NUMERIC(5,2) NOT NULL,
    rsi_14         NUMERIC(8,3),
    macd_hist      NUMERIC(10,5),
    bb_pct         NUMERIC(8,4),
    cmf_20         NUMERIC(8,4),
    atr_pct        NUMERIC(8,4),
    rvol           NUMERIC(8,3),
    gap_pct        NUMERIC(8,4),
    close_strength NUMERIC(8,4),
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT aiem_technical_scores_uq UNIQUE (score_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_aiem_tech_date ON aiem_technical_scores (score_date);
CREATE INDEX IF NOT EXISTS idx_aiem_tech_ticker ON aiem_technical_scores (ticker);

-- ── Phase 6a: Risk Scores ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_risk_scores (
    id             BIGSERIAL PRIMARY KEY,
    score_date     DATE        NOT NULL,
    ticker         TEXT        NOT NULL,
    risk_score     NUMERIC(5,2) NOT NULL,
    concentration_pct NUMERIC(8,4),
    corr_spy_20d   NUMERIC(8,4),
    max_dd_5d      NUMERIC(8,4),
    gap_risk       NUMERIC(8,4),
    risk_gate_pass BOOLEAN     NOT NULL DEFAULT TRUE,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT aiem_risk_scores_uq UNIQUE (score_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_aiem_risk_date ON aiem_risk_scores (score_date);

-- ── Phase 6b: Portfolio State ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_portfolio_state (
    id                   BIGSERIAL PRIMARY KEY,
    state_date           DATE        NOT NULL,
    open_positions       INTEGER     NOT NULL DEFAULT 0,
    total_notional       NUMERIC(14,2) NOT NULL DEFAULT 0,
    sector_concentration JSONB,
    largest_position_pct NUMERIC(8,4),
    drawdown_7d          NUMERIC(8,4),
    win_rate_7d          NUMERIC(8,4),
    win_rate_30d         NUMERIC(8,4),
    kelly_fraction       NUMERIC(8,4),
    portfolio_heat       NUMERIC(8,4),
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT aiem_portfolio_state_uq UNIQUE (state_date)
);

-- ── Phase 7: Counterfactual Results ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_counterfactual_results (
    id              BIGSERIAL PRIMARY KEY,
    analysis_date   DATE        NOT NULL,
    trade_id        BIGINT,
    ticker          TEXT        NOT NULL,
    actual_return   NUMERIC(10,5),
    cf_return       NUMERIC(10,5),
    cf_type         TEXT        NOT NULL,  -- 'no_macro_gate', 'no_trend_filter', etc.
    lesson          TEXT,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aiem_cf_date ON aiem_counterfactual_results (analysis_date);

-- ── Phase 7: Strategy Memory ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_strategy_memory (
    id            BIGSERIAL PRIMARY KEY,
    memory_key    TEXT        NOT NULL UNIQUE,
    memory_value  JSONB       NOT NULL,
    version       INTEGER     NOT NULL DEFAULT 1,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Phase 5: Decision History ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_decision_history (
    id                    BIGSERIAL PRIMARY KEY,
    decision_date         DATE        NOT NULL,
    ticker                TEXT        NOT NULL,
    decision              TEXT        NOT NULL,  -- 'EXECUTE', 'BLOCK_MACRO', 'BLOCK_TREND', etc.
    macro_score           NUMERIC(6,2),
    trend_score           NUMERIC(6,2),
    technical_score       NUMERIC(6,2),
    risk_score            NUMERIC(6,2),
    final_confidence      NUMERIC(6,3),
    position_size_pct     NUMERIC(8,4),
    block_reason          TEXT,
    decision_payload      JSONB,
    outcome_return        NUMERIC(10,5),
    outcome_resolved      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aiem_dec_date ON aiem_decision_history (decision_date);
CREATE INDEX IF NOT EXISTS idx_aiem_dec_ticker ON aiem_decision_history (ticker);
CREATE INDEX IF NOT EXISTS idx_aiem_dec_decision ON aiem_decision_history (decision);

-- ── Phase 8: Verification Logs ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_verification_logs (
    id              BIGSERIAL PRIMARY KEY,
    run_date        DATE        NOT NULL,
    run_type        TEXT        NOT NULL,  -- 'daily', 'weekly', 'on_demand'
    module          TEXT        NOT NULL,
    test_name       TEXT        NOT NULL,
    result          TEXT        NOT NULL,  -- 'PASS', 'FAIL', 'WARN', 'SKIP'
    expected_value  TEXT,
    actual_value    TEXT,
    details         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aiem_verif_date ON aiem_verification_logs (run_date);
CREATE INDEX IF NOT EXISTS idx_aiem_verif_result ON aiem_verification_logs (result);

-- ── Phase 8: System Health ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiem_system_health (
    id             BIGSERIAL PRIMARY KEY,
    check_time     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    module         TEXT        NOT NULL,
    status         TEXT        NOT NULL,  -- 'OK', 'DEGRADED', 'DOWN'
    latency_ms     INTEGER,
    detail         TEXT,
    CONSTRAINT aiem_system_health_uq UNIQUE (check_time, module)
);
CREATE INDEX IF NOT EXISTS idx_aiem_health_time ON aiem_system_health (check_time DESC);
CREATE INDEX IF NOT EXISTS idx_aiem_health_module ON aiem_system_health (module);
"""


def init_v3_schema(conn=None) -> None:
    """
    Idempotent: runs all CREATE TABLE IF NOT EXISTS statements.
    Pass an existing psycopg2 connection, or one will be opened internally.
    """
    _own = conn is None
    try:
        if _own:
            conn = psycopg2.connect(_DB_URL, connect_timeout=10)
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
        conn.commit()
        print("[aiem_v3_schema] all 12 tables ensured OK")
    except Exception as e:
        print(f"[aiem_v3_schema] schema init error: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if _own and conn:
            try:
                conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — MACRO ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _tradier_history(symbol: str, days: int = 30) -> list[dict]:
    """
    Fetch daily OHLC history from Tradier sandbox/live.
    Returns list of {date, open, high, low, close, volume} dicts.
    """
    import urllib.request
    import urllib.parse

    token = _TRADIER_LIVE_TOKEN
    if not token:
        return []

    end_dt   = datetime.date.today()
    start_dt = end_dt - datetime.timedelta(days=days + 15)  # buffer for weekends
    params   = urllib.parse.urlencode({
        "symbol":    symbol,
        "interval":  "daily",
        "start":     start_dt.isoformat(),
        "end":       end_dt.isoformat(),
        "session_filter": "open",
    })
    url = f"{TRADIER_API_BASE}/v1/markets/history?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept":        "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        day_data = (body.get("history") or {}).get("day") or []
        if isinstance(day_data, dict):
            day_data = [day_data]
        return sorted(day_data, key=lambda r: r.get("date", ""))
    except Exception as e:
        print(f"[macro] Tradier history {symbol} error: {e}")
        return []


def _yahoo_last(symbol: str) -> Optional[float]:
    """
    Fetch last close from Yahoo Finance for index symbols (^VIX, DX-Y.NYB, ^TNX).
    Returns None on failure — caller must handle gracefully.
    """
    import urllib.request
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}?interval=1d&range=5d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        closes = (
            data.get("chart", {})
                .get("result", [{}])[0]
                .get("indicators", {})
                .get("quote", [{}])[0]
                .get("close", [])
        )
        closes = [c for c in closes if c is not None]
        return float(closes[-1]) if closes else None
    except Exception as e:
        print(f"[macro] Yahoo {symbol} error: {e}")
        return None


def _yahoo_history_closes(symbol: str, days: int = 25) -> list[float]:
    """Returns list of closes (oldest → newest) or [] on failure."""
    import urllib.request
    try:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.request.quote(symbol)}?interval=1d&range=60d"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        closes = (
            data.get("chart", {})
                .get("result", [{}])[0]
                .get("indicators", {})
                .get("quote", [{}])[0]
                .get("close", [])
        )
        closes = [float(c) for c in closes if c is not None]
        return closes[-days:]
    except Exception as e:
        print(f"[macro] Yahoo history {symbol} error: {e}")
        return []


def _sma(values: list[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _score_regime(score: float):
    """Returns (regime_str, position_size_modifier)."""
    for threshold, regime, modifier in _REGIME_THRESHOLDS:
        if score >= threshold:
            return regime, modifier
    return "BEAR_SEVERE", 0.0


def _compute_equity_score(
    spy_rows, qqq_rows, dia_rows, iwm_rows
) -> tuple[float, dict]:
    """
    40-point sub-score: each ETF contributes 10 pts if close > SMA20.
    Partial credit: 5 pts if within 1% below SMA20.
    """
    instruments = {
        "SPY": spy_rows,
        "QQQ": qqq_rows,
        "DIA": dia_rows,
        "IWM": iwm_rows,
    }
    details: dict = {}
    total = 0.0
    for name, rows in instruments.items():
        closes = [float(r.get("close", 0)) for r in rows if r.get("close")]
        sma20  = _sma(closes, 20)
        close  = closes[-1] if closes else None
        details[name.lower() + "_close"] = close
        details[name.lower() + "_sma20"] = sma20
        if close is None or sma20 is None:
            total += 5.0  # neutral if data missing
        elif close >= sma20:
            total += 10.0
        elif close >= sma20 * 0.99:  # within 1% below
            total += 5.0
        else:
            total += 0.0
    return total, details


def _compute_vol_score(vix: Optional[float], vix_closes: list[float]) -> tuple[float, dict]:
    """
    30-point sub-score.
    VIX < 15           → 30 pts (low volatility, favorable)
    15 <= VIX < 20     → 25 pts
    20 <= VIX < 25     → 18 pts
    25 <= VIX < 30     → 10 pts
    30 <= VIX < 40     →  5 pts
    VIX >= 40          →  0 pts

    Bonus/Penalty: VIX < VIX_SMA20 adds 5, VIX > VIX_SMA20*1.2 subtracts 5.
    Final clamped to [0, 30].
    """
    if vix is None:
        return 15.0, {"vix": None, "vix_sma20": None}  # neutral

    vix_sma20 = _sma(vix_closes, 20)

    if vix < 15:
        base = 30.0
    elif vix < 20:
        base = 25.0
    elif vix < 25:
        base = 18.0
    elif vix < 30:
        base = 10.0
    elif vix < 40:
        base = 5.0
    else:
        base = 0.0

    adj = 0.0
    if vix_sma20:
        if vix < vix_sma20:
            adj = +3.0
        elif vix > vix_sma20 * 1.2:
            adj = -5.0

    score = max(0.0, min(30.0, base + adj))
    return score, {"vix": vix, "vix_sma20": vix_sma20}


def _compute_breadth_score(spy_rows: list, iwm_rows: list) -> float:
    """
    20-point sub-score: IWM vs SPY spread (small-cap leadership = risk-on).
    IWM 5d return - SPY 5d return:
      > +1%   → 20 pts (strong small-cap leadership)
      > 0%    → 15 pts
      > -1%   → 10 pts
      > -2%   → 5 pts
      <= -2%  →  0 pts (large-cap flight = risk-off)
    """
    def _5d_return(rows):
        closes = [float(r.get("close", 0)) for r in rows if r.get("close")]
        if len(closes) < 6:
            return None
        return (closes[-1] / closes[-6] - 1) * 100

    spy_ret = _5d_return(spy_rows)
    iwm_ret = _5d_return(iwm_rows)

    if spy_ret is None or iwm_ret is None:
        return 10.0  # neutral

    spread = iwm_ret - spy_ret
    if spread > 1.0:
        return 20.0
    elif spread > 0.0:
        return 15.0
    elif spread > -1.0:
        return 10.0
    elif spread > -2.0:
        return 5.0
    else:
        return 0.0


def _compute_credit_score(dxy: Optional[float], dxy_prev: Optional[float]) -> float:
    """
    10-point sub-score: DXY direction.
    DXY falling (dollar weakening) → risk-on → 10 pts.
    DXY flat (< 0.3% change)       → 6 pts.
    DXY rising (dollar strengthening) → risk-off → 0-3 pts.
    """
    if dxy is None or dxy_prev is None:
        return 5.0  # neutral

    change_pct = (dxy / dxy_prev - 1) * 100
    if change_pct < -0.3:
        return 10.0
    elif change_pct < 0.1:
        return 6.0
    elif change_pct < 0.5:
        return 3.0
    else:
        return 0.0


def compute_macro_snapshot() -> MacroSnapshot:
    """
    Fetch all data, compute scores, persist to DB, return MacroSnapshot.
    Thread-safe: only one compute runs at a time (uses _compute_lock).
    """
    with _compute_lock:
        return _do_compute()


def _do_compute() -> MacroSnapshot:
    import datetime as _dt
    now_utc = _dt.datetime.utcnow()
    # ET date for snapshot_date (prod DB is GMT; we must derive ET ourselves)
    et_offset = _dt.timedelta(hours=-4)  # EDT; fine for summer; close enough for date
    et_now    = now_utc + et_offset
    snapshot_date = et_now.date().isoformat()

    warnings: list[str] = []
    data_quality = "FULL"

    # ── Fetch Tradier data for equity ETFs ─────────────────────────────────────
    print("[macro] fetching SPY/QQQ/DIA/IWM from Tradier …")
    spy_rows = _tradier_history("SPY", 35)
    qqq_rows = _tradier_history("QQQ", 35)
    dia_rows = _tradier_history("DIA", 35)
    iwm_rows = _tradier_history("IWM", 35)

    missing_etfs = [s for s, r in [("SPY", spy_rows), ("QQQ", qqq_rows),
                                    ("DIA", dia_rows), ("IWM", iwm_rows)] if not r]
    if missing_etfs:
        warnings.append(f"ETF data missing: {missing_etfs}")
        data_quality = "PARTIAL"

    # ── Fetch Yahoo for VIX / DXY / TNX ───────────────────────────────────────
    print("[macro] fetching ^VIX / DX-Y.NYB from Yahoo …")
    vix_closes = _yahoo_history_closes("^VIX", 25)
    vix        = vix_closes[-1] if vix_closes else _yahoo_last("^VIX")
    dxy_closes = _yahoo_history_closes("DX-Y.NYB", 3)
    dxy        = dxy_closes[-1] if len(dxy_closes) >= 1 else None
    dxy_prev   = dxy_closes[-2] if len(dxy_closes) >= 2 else None

    if vix is None:
        warnings.append("VIX unavailable — vol score defaulting to neutral")
        data_quality = "PARTIAL"

    # ── Score sub-components ──────────────────────────────────────────────────
    equity_score, eq_details = _compute_equity_score(spy_rows, qqq_rows, dia_rows, iwm_rows)
    vol_score,    vol_details = _compute_vol_score(vix, vix_closes)
    breadth_score = _compute_breadth_score(spy_rows, iwm_rows)
    credit_score  = _compute_credit_score(dxy, dxy_prev)

    macro_score = equity_score + vol_score + breadth_score + credit_score

    # ── Full fallback if everything failed ────────────────────────────────────
    if not spy_rows and not qqq_rows and vix is None:
        macro_score  = 50.0
        data_quality = "FALLBACK"
        warnings.append("All data sources failed — defaulting to NEUTRAL 50, trades proceed")
        print("[macro] WARNING: all data unavailable, using fallback score 50")

    macro_score = round(max(0.0, min(100.0, macro_score)), 2)
    regime, pos_modifier = _score_regime(macro_score)
    block_trades = macro_score < _BLOCK_BELOW

    warning_str = "; ".join(warnings) if warnings else None

    snapshot = MacroSnapshot(
        snapshot_date=snapshot_date,
        computed_at=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        macro_score=macro_score,
        regime=regime,
        position_size_modifier=pos_modifier,
        spy_close=eq_details.get("spy_close"),
        spy_sma20=eq_details.get("spy_sma20"),
        qqq_close=eq_details.get("qqq_close"),
        qqq_sma20=eq_details.get("qqq_sma20"),
        dia_close=eq_details.get("dia_close"),
        dia_sma20=eq_details.get("dia_sma20"),
        iwm_close=eq_details.get("iwm_close"),
        iwm_sma20=eq_details.get("iwm_sma20"),
        equity_score=equity_score,
        vix=vix,
        vix_sma20=vol_details.get("vix_sma20"),
        vol_score=vol_score,
        breadth_score=breadth_score,
        dxy=dxy,
        dxy_prev=dxy_prev,
        credit_score=credit_score,
        data_quality=data_quality,
        block_trades=block_trades,
        warning=warning_str,
    )

    # ── Persist to DB ──────────────────────────────────────────────────────────
    _persist_snapshot(snapshot)
    print(f"[macro] {snapshot.summary_line()}")
    return snapshot


def _persist_snapshot(s: MacroSnapshot) -> None:
    try:
        conn = psycopg2.connect(_DB_URL, connect_timeout=6)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_macro_daily (
                    snapshot_date, computed_at, macro_score, regime, position_size_modifier,
                    spy_close, spy_sma20, qqq_close, qqq_sma20,
                    dia_close, dia_sma20, iwm_close, iwm_sma20, equity_score,
                    vix, vix_sma20, vol_score, breadth_score,
                    dxy, dxy_prev, credit_score,
                    data_quality, block_trades, warning, raw_payload
                ) VALUES (
                    %(snapshot_date)s, %(computed_at)s, %(macro_score)s,
                    %(regime)s, %(position_size_modifier)s,
                    %(spy_close)s, %(spy_sma20)s, %(qqq_close)s, %(qqq_sma20)s,
                    %(dia_close)s, %(dia_sma20)s, %(iwm_close)s, %(iwm_sma20)s,
                    %(equity_score)s, %(vix)s, %(vix_sma20)s, %(vol_score)s,
                    %(breadth_score)s, %(dxy)s, %(dxy_prev)s, %(credit_score)s,
                    %(data_quality)s, %(block_trades)s, %(warning)s, %(raw_payload)s
                )
                ON CONFLICT (snapshot_date) DO UPDATE SET
                    computed_at            = EXCLUDED.computed_at,
                    macro_score            = EXCLUDED.macro_score,
                    regime                 = EXCLUDED.regime,
                    position_size_modifier = EXCLUDED.position_size_modifier,
                    spy_close=EXCLUDED.spy_close, spy_sma20=EXCLUDED.spy_sma20,
                    qqq_close=EXCLUDED.qqq_close, qqq_sma20=EXCLUDED.qqq_sma20,
                    dia_close=EXCLUDED.dia_close, dia_sma20=EXCLUDED.dia_sma20,
                    iwm_close=EXCLUDED.iwm_close, iwm_sma20=EXCLUDED.iwm_sma20,
                    equity_score=EXCLUDED.equity_score,
                    vix=EXCLUDED.vix, vix_sma20=EXCLUDED.vix_sma20,
                    vol_score=EXCLUDED.vol_score, breadth_score=EXCLUDED.breadth_score,
                    dxy=EXCLUDED.dxy, dxy_prev=EXCLUDED.dxy_prev,
                    credit_score=EXCLUDED.credit_score,
                    data_quality=EXCLUDED.data_quality,
                    block_trades=EXCLUDED.block_trades,
                    warning=EXCLUDED.warning,
                    raw_payload=EXCLUDED.raw_payload
            """, {**s.to_dict(), "raw_payload": json.dumps(s.to_dict())})
        conn.commit()
    except Exception as e:
        print(f"[macro] persist error (non-fatal): {e}")
        traceback.print_exc()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_cached_macro_snapshot() -> Optional[MacroSnapshot]:
    """
    Returns today's MacroSnapshot from DB without making any network calls.
    Returns None if no row exists for today.
    """
    import datetime as _dt
    try:
        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT * FROM aiem_macro_daily
                WHERE snapshot_date = (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')::date
                ORDER BY computed_at DESC LIMIT 1
            """)
            row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return MacroSnapshot(
            snapshot_date=str(row["snapshot_date"]),
            computed_at=str(row["computed_at"]),
            macro_score=float(row["macro_score"]),
            regime=row["regime"],
            position_size_modifier=float(row["position_size_modifier"]),
            spy_close=_f(row["spy_close"]), spy_sma20=_f(row["spy_sma20"]),
            qqq_close=_f(row["qqq_close"]), qqq_sma20=_f(row["qqq_sma20"]),
            dia_close=_f(row["dia_close"]), dia_sma20=_f(row["dia_sma20"]),
            iwm_close=_f(row["iwm_close"]), iwm_sma20=_f(row["iwm_sma20"]),
            equity_score=float(row["equity_score"] or 0),
            vix=_f(row["vix"]), vix_sma20=_f(row["vix_sma20"]),
            vol_score=float(row["vol_score"] or 0),
            breadth_score=float(row["breadth_score"] or 0),
            dxy=_f(row["dxy"]), dxy_prev=_f(row["dxy_prev"]),
            credit_score=float(row["credit_score"] or 0),
            data_quality=row["data_quality"],
            block_trades=bool(row["block_trades"]),
            warning=row["warning"],
        )
    except Exception as e:
        print(f"[macro] get_cached error: {e}")
        return None


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


# ── Cache TTL: 55 minutes ─────────────────────────────────────────────────────
_CACHE_TTL_SECONDS = 55 * 60
_cache_lock = threading.Lock()
_cached_snapshot: Optional[MacroSnapshot] = None
_cache_ts: float = 0.0


def get_macro_gate() -> tuple[bool, MacroSnapshot]:
    """
    Main entry point for the paper-trade execute function.

    Returns (trade_allowed: bool, snapshot: MacroSnapshot).
    trade_allowed = False means BEAR_SEVERE — do NOT execute any trades.

    Logic:
      1. Try in-process cache (55-min TTL) to avoid network calls at 9:35 AM
         when 9:00 AM pre-compute already ran.
      2. Try DB cache (today's row).
      3. If neither, compute live (fallback — takes ~8s).
    """
    import time
    global _cached_snapshot, _cache_ts

    with _cache_lock:
        now = time.monotonic()
        if _cached_snapshot is not None and (now - _cache_ts) < _CACHE_TTL_SECONDS:
            snap = _cached_snapshot
            return (not snap.block_trades), snap

    # Try DB cache first (cheaper than network)
    snap = get_cached_macro_snapshot()
    if snap is None:
        print("[macro] no DB cache — computing live …")
        snap = compute_macro_snapshot()

    with _cache_lock:
        _cached_snapshot = snap
        _cache_ts = time.monotonic() if snap else 0.0

    return (not snap.block_trades), snap


def log_decision(
    ticker: str,
    decision: str,
    macro_snap: Optional[MacroSnapshot],
    *,
    trend_score: Optional[float] = None,
    technical_score: Optional[float] = None,
    risk_score: Optional[float] = None,
    final_confidence: Optional[float] = None,
    position_size_pct: Optional[float] = None,
    block_reason: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    """
    Write a row to aiem_decision_history.  Non-blocking — ignores errors.
    """
    try:
        conn = psycopg2.connect(_DB_URL, connect_timeout=4)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_decision_history (
                    decision_date, ticker, decision,
                    macro_score, trend_score, technical_score, risk_score,
                    final_confidence, position_size_pct, block_reason, decision_payload
                ) VALUES (
                    (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')::date,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                ticker, decision,
                macro_snap.macro_score if macro_snap else None,
                trend_score, technical_score, risk_score,
                final_confidence, position_size_pct, block_reason,
                json.dumps(payload or {}),
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[macro] log_decision error (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Deferred schema init — called from main.py _DEFERRED_INITS list
# ─────────────────────────────────────────────────────────────────────────────

def _deferred_v3_schema_init() -> None:
    print("[aiem_v3] deferred schema init starting …")
    init_v3_schema()
    print("[aiem_v3] deferred schema init done")


# ─────────────────────────────────────────────────────────────────────────────
# Admin endpoint helpers
# ─────────────────────────────────────────────────────────────────────────────

def admin_get_latest_macro() -> dict:
    """Returns the latest macro snapshot as a JSON-serialisable dict."""
    snap = get_cached_macro_snapshot()
    if snap is None:
        return {"error": "no macro snapshot available for today", "advice": "POST /stock-api/admin/macro/refresh"}
    d = snap.to_dict()
    d["summary"] = snap.summary_line()
    return d


def admin_refresh_macro() -> dict:
    """Force-recomputes macro and updates DB + in-process cache."""
    global _cached_snapshot, _cache_ts
    snap = compute_macro_snapshot()
    with _cache_lock:
        _cached_snapshot = snap
        import time
        _cache_ts = time.monotonic()
    d = snap.to_dict()
    d["summary"] = snap.summary_line()
    return d
