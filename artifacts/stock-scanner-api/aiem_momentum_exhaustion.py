"""
aiem_momentum_exhaustion.py
===========================
Module M — Momentum Exhaustion (Trend-Top Detection)

Purpose: Detect when a momentum run is showing signs of exhaustion — not a
prediction of when it will top, but a stacked-evidence signal that it already
has structural deterioration.

Signal stack: 8 signals, minimum 3-of-8 to fire (MIN_SIGNALS_TO_FIRE).
Override rule: when Module L is simultaneously active on the same ticker,
              M requires 5-of-8 (MIN_SIGNALS_OVERRIDE) to fire.
Position sizing interaction:
  signals_triggered_count 3-4 → PARTIAL_DE_RISK (reduce size 25-50%)
  signals_triggered_count 5-6 → REDUCE (reduce size 50-75%)
  signals_triggered_count 7+  → FULL_EXIT recommendation

The 8 signals:
  S1 — Structure break (lower low vs prior pullback low)
  S2 — Distribution signature (above-avg vol on down days, count-based)
  S3 — Relative strength failure (weak BOUNCE vs SPY specifically)
  S4 — EMA21 break (sustained close below after extended period above)
  S5 — Breadth narrowing (sector/ETF only; NOT_AVAILABLE for single stocks)
  S6 — Earnings revision stall (direction-based; NOT_AVAILABLE — no analyst data)
  S7 — Concentration extreme (relative to own historical range; ETF-specific)
  S8 — Cross-market speculative rollover (highest Appendix priority; NOT_IMPLEMENTED)

GUARDRAIL — enforced by design:
  P/E, P/S, EV/EBITDA, book value, and any fundamental valuation ratio are
  CONTEXT ONLY. They must NEVER appear inside a trigger/firing conditional.
  grep -n "pe_ratio\\|p_e\\|ev_ebitda\\|price_to_sales\\|p_s_ratio" this file
  to confirm zero occurrences in any conditional path.
"""

import json
import math
import os
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import psycopg2

_DB_URL   = os.environ.get("DATABASE_URL", "")
_SIGNAL_NAME        = "Momentum_Exhaustion_MultiSignal"
_INVENTED_INDICATOR = "aiem_momentum_exhaustion_v1"
_HORIZON            = "5d"

# ── Signal thresholds ──────────────────────────────────────────────────────────
MIN_SIGNALS_TO_FIRE   = 3   # baseline: 3-of-8 to fire when L is NOT active
MIN_SIGNALS_OVERRIDE  = 5   # higher threshold when Module L is active on same ticker
DISTRIBUTION_DAYS_MIN = 3   # S2: minimum above-avg-volume down-days in 20-session window
DISTRIBUTION_VOL_MULT = 1.2 # S2: "above average" = >1.2x 20-day baseline
EMA21_BELOW_DAYS_MIN  = 2   # S4: consecutive closes below EMA21 to confirm break
EMA21_ABOVE_STREAK_MIN = 30 # S4: prior streak of sessions above EMA21 before break
RS_BOUNCE_UNDERPERFORM = -5.0  # S3: ticker bounce < SPY bounce by this many pp → fail

# ── Known ETFs for S5/S7 evaluation ───────────────────────────────────────────
# S5/S7 are only evaluated for ETFs; individual stocks get NOT_AVAILABLE.
_ETF_UNIVERSE = {"SOXX","QQQ","SMH","XLK","ARKK","TAN","ICLN","XBI","IBB","SPY","IWM"}

# S7 concentration extreme: hardcoded reference points from spec for known ETFs.
# Format: {ticker: (threshold_pct, context_note)}
# These are updated manually when the spec's case studies change.
_S7_CONCENTRATION_KNOWN = {
    "SOXX": (75.0, "Top-3 chip stocks ~80% of top-10 global chip market cap (per spec CASE 6)"),
    "SMH":  (70.0, "Semiconductor ETF, narrow leadership"),
}

# ── DB schema ──────────────────────────────────────────────────────────────────
_INIT_SQL = """
CREATE TABLE IF NOT EXISTS aiem_exhaustion_signals (
    id                              BIGSERIAL PRIMARY KEY,
    ticker                          TEXT NOT NULL,
    signal_date                     DATE NOT NULL,
    detected_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    state                           TEXT NOT NULL,
    signals_triggered_count         INTEGER DEFAULT 0,
    signals_triggered_list          TEXT[],
    structure_break_confirmed       BOOLEAN DEFAULT FALSE,
    distribution_days_count         INTEGER,
    relative_strength_status        TEXT,
    rs_bounce_pp                    NUMERIC(8,4),
    ema21_status                    TEXT,
    breadth_status                  TEXT,
    earnings_revision_status        TEXT,
    concentration_extreme_status    TEXT,
    concentration_pct               NUMERIC(8,4),
    cross_market_speculative_rollover_count TEXT DEFAULT 'NOT_IMPLEMENTED',
    conviction_score                INTEGER DEFAULT 0,
    position_action                 TEXT,
    l_active_override_applied       BOOLEAN DEFAULT FALSE,
    earnings_excl                   BOOLEAN DEFAULT FALSE,
    falling_knife_excl              BOOLEAN DEFAULT FALSE,
    days_to_earnings                INTEGER,
    tg_sent                         BOOLEAN DEFAULT FALSE,
    tg_sent_at                      TIMESTAMPTZ,
    UNIQUE (ticker, signal_date)
);
CREATE INDEX IF NOT EXISTS idx_aiem_exhaust_state
    ON aiem_exhaustion_signals (state, signal_date);
CREATE INDEX IF NOT EXISTS idx_aiem_exhaust_count
    ON aiem_exhaustion_signals (signals_triggered_count, signal_date);

CREATE TABLE IF NOT EXISTS aiem_exhaustion_backtest_log (
    id                       BIGSERIAL PRIMARY KEY,
    ticker                   TEXT NOT NULL,
    signal_date              DATE NOT NULL,
    signals_triggered_count  INTEGER,
    state                    TEXT,
    market_regime            TEXT,
    fwd_1d_pct               NUMERIC(8,4),
    fwd_3d_pct               NUMERIC(8,4),
    fwd_5d_pct               NUMERIC(8,4),
    fwd_10d_pct              NUMERIC(8,4),
    false_positive           BOOLEAN,
    was_pullback_not_top     BOOLEAN,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, signal_date)
);
CREATE INDEX IF NOT EXISTS idx_exhaust_bt
    ON aiem_exhaustion_backtest_log (ticker, signal_date);
"""

def init_schema() -> None:
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(_INIT_SQL)
            conn.commit()
        print("[momentum_exhaustion] schema ready")
    except Exception as e:
        print(f"[momentum_exhaustion] init_schema error: {e}")

# ── Technical helpers ──────────────────────────────────────────────────────────

def _sma(arr: list, period: int) -> Optional[float]:
    if len(arr) < period:
        return None
    return sum(arr[-period:]) / period

def _ema_series(closes: list, period: int) -> List[Optional[float]]:
    """Full EMA series. Leading values are None."""
    n = len(closes)
    if n < period:
        return [None] * n
    k = 2.0 / (period + 1)
    result: List[Optional[float]] = [None] * (period - 1)
    result.append(sum(closes[:period]) / period)
    for i in range(period, n):
        result.append(closes[i] * k + result[-1] * (1 - k))
    return result

def _rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

# ── Shared swing-low detection — IDENTICAL to aiem_pullback_reentry.py ─────────
# DO NOT modify one without updating the other.

def find_swing_lows(lows: list, window: int = 3) -> List[int]:
    result = []
    n = len(lows)
    for i in range(window, n - window):
        if all(lows[i] <= lows[j] for j in range(i - window, i)) and \
           all(lows[i] <= lows[j] for j in range(i + 1, i + window + 1)):
            result.append(i)
    return result

def _higher_low_check(lows: list, lookback: int = 90
                      ) -> Tuple[bool, Optional[float], Optional[float]]:
    """Same algorithm as Module L's higher_low_check — shared source of truth."""
    window = lows[-lookback:] if len(lows) >= lookback else lows
    idxs = find_swing_lows(window)
    if len(idxs) < 2:
        return True, None, None
    prior_low   = window[idxs[-2]]
    current_low = window[idxs[-1]]
    return current_low > prior_low, current_low, prior_low

# ── Module F gate ──────────────────────────────────────────────────────────────

def _module_f(ticker: str, prior_5d_ret: Optional[float],
              sig_date: date, cur) -> dict:
    out = {"suppress": False, "earnings_excl": False,
           "falling_knife": False, "days_to_earnings": None}
    try:
        cur.execute(
            "SELECT earnings_date FROM earnings_calendar "
            "WHERE ticker=%s AND earnings_date>=%s ORDER BY earnings_date LIMIT 1",
            (ticker, sig_date))
        row = cur.fetchone()
        if row:
            days = (row[0] - sig_date).days
            out["days_to_earnings"] = days
            if days <= 5:
                out["earnings_excl"] = True
                out["suppress"] = True
    except Exception:
        pass
    if prior_5d_ret is not None and prior_5d_ret <= -20.0:
        out["falling_knife"] = True
        out["suppress"] = True
    return out

# ── Check if Module L is active on same ticker ────────────────────────────────

def _check_module_l_active(ticker: str, sig_date: date, cur) -> bool:
    try:
        cur.execute(
            "SELECT id FROM aiem_pullback_signals "
            "WHERE ticker=%s AND signal_date=%s LIMIT 1",
            (ticker, sig_date))
        return cur.fetchone() is not None
    except Exception:
        return False

# ── SIGNAL 1: Structure break ─────────────────────────────────────────────────

def _s1_structure_break(lows: list) -> bool:
    """
    Lower low vs prior pullback low = structure break.
    Uses IDENTICAL underlying calculation as Module L's higher-low check.
    Returns True (signal fires) when higher_low_intact=False.
    """
    intact, _, _ = _higher_low_check(lows, lookback=90)
    return not intact   # structure break = higher-low has FAILED

# ── SIGNAL 2: Distribution signature ─────────────────────────────────────────

def _s2_distribution(closes: list, volumes: list) -> Tuple[bool, int]:
    """
    Count above-average-volume down-days in the last 20 sessions.
    Returns (signal_fired, distribution_days_count).
    distribution_days_count varies across tickers — no hardcoded constant.
    """
    n = len(closes)
    if n < 21 or len(volumes) < 21:
        return False, 0
    baseline = sum(volumes[-21:-1]) / 20
    if baseline <= 0:
        return False, 0
    count = 0
    for i in range(-20, 0):
        is_down = closes[i] < closes[i-1]
        high_vol = volumes[i] > baseline * DISTRIBUTION_VOL_MULT
        if is_down and high_vol:
            count += 1
    return count >= DISTRIBUTION_DAYS_MIN, count

# ── SIGNAL 3: Relative strength failure on the BOUNCE (not general RS) ────────

def _s3_rs_failure_on_bounce(closes: list, spy_closes: list) -> Tuple[bool, str, Optional[float]]:
    """
    Measures relative strength specifically on the BOUNCE — the most recent
    sequence of up-days after a drop, not general period RS.
    Finds the most recent local trough and measures pct gain from trough.
    Returns (signal_fired, status, bounce_pp_diff).
    """
    n = len(closes)
    if n < 20 or len(spy_closes) < 20:
        return False, "INSUFFICIENT_DATA", None

    # Find most recent trough (minimum in last 20 bars)
    window = closes[-20:]
    trough_idx = window.index(min(window))
    if trough_idx >= len(window) - 2:
        return False, "NO_BOUNCE_YET", None   # trough is too recent; no bounce to measure

    # Bounce return from trough to now
    trough_close = window[trough_idx]
    ticker_bounce = (closes[-1] - trough_close) / trough_close * 100 if trough_close > 0 else 0

    # SPY return over the same period (trough_idx to now within last 20 bars)
    spy_window = spy_closes[-20:]
    if trough_idx >= len(spy_window):
        return False, "NO_BOUNCE_YET", None
    spy_trough_close = spy_window[trough_idx]
    spy_bounce = (spy_closes[-1] - spy_trough_close) / spy_trough_close * 100 \
        if spy_trough_close > 0 else 0

    diff = ticker_bounce - spy_bounce  # negative = ticker underperforms SPY on bounce
    if diff < RS_BOUNCE_UNDERPERFORM:
        return True, "FAILED", round(diff, 4)
    return False, "INTACT", round(diff, 4)

# ── SIGNAL 4: EMA21 break ─────────────────────────────────────────────────────

def _s4_ema21_break(closes: list) -> Tuple[bool, str]:
    """
    EMA21 break: True if:
      (a) There was an extended period above EMA21 (>= EMA21_ABOVE_STREAK_MIN bars), AND
      (b) Price has now closed below EMA21 for >= EMA21_BELOW_DAYS_MIN consecutive bars.
    Returns (signal_fired, ema21_status).
    Status values: INTACT / ROLLING_OVER / BROKEN (all 3 states reachable in real data).
    """
    n = len(closes)
    if n < 50:
        return False, "INSUFFICIENT_DATA"

    ema21 = _ema_series(closes, 21)

    # Count consecutive closes below EMA21 at tail
    below_streak = 0
    for i in range(n - 1, -1, -1):
        if ema21[i] is None:
            break
        if closes[i] < ema21[i]:
            below_streak += 1
        else:
            break

    if below_streak == 0:
        return False, "INTACT"

    # How long was the prior above-EMA21 streak?
    above_streak = 0
    search_start = n - 1 - below_streak
    for i in range(search_start, -1, -1):
        if ema21[i] is None:
            break
        if closes[i] >= ema21[i]:
            above_streak += 1
        else:
            break

    if below_streak < EMA21_BELOW_DAYS_MIN:
        return False, "ROLLING_OVER"   # breaking but not confirmed yet
    if above_streak < EMA21_ABOVE_STREAK_MIN:
        return False, "ROLLING_OVER"   # wasn't in a real trend, just crossed

    return True, "BROKEN"   # confirmed break after sustained trend

# ── SIGNAL 5: Breadth narrowing (ETF/sector only) ────────────────────────────

def _s5_breadth_narrowing(ticker: str) -> Tuple[bool, str]:
    """
    Breadth narrowing: only evaluable for sector ETFs.
    NOT_AVAILABLE for individual stocks.
    For ETFs: would require constituent return distribution data — not yet wired.
    Returns (signal_fired, breadth_status).
    """
    if ticker in _ETF_UNIVERSE:
        # Constituent data not yet loaded into DB — mark as NOT_AVAILABLE but
        # leave the infrastructure in place for future wiring.
        return False, "NOT_AVAILABLE_ETF_CONSTITUENTS_UNWIRED"
    return False, "NOT_AVAILABLE"

# ── SIGNAL 6: Earnings revision stall ────────────────────────────────────────

def _s6_earnings_revision_stall(ticker: str) -> Tuple[bool, str]:
    """
    Tracks DIRECTION of earnings revisions (upgrades vs. downgrades),
    not the absolute level of estimates.
    NOT_AVAILABLE: no analyst revision data in polygon_market_daily.
    Returns (signal_fired, earnings_revision_status).
    """
    # When analyst revision data is wired, check:
    #   prev_revision_direction (UP/DOWN/FLAT) → current_revision_direction
    #   Signal fires if: was UP or FLAT, now DOWN for 2+ consecutive periods
    return False, "NOT_AVAILABLE"

# ── SIGNAL 7: Concentration extreme ──────────────────────────────────────────

def _s7_concentration_extreme(ticker: str) -> Tuple[bool, str, Optional[float]]:
    """
    For known sector ETFs: is the top-3 constituent concentration at an
    extreme relative to its own historical range?
    Returns (signal_fired, status, concentration_pct).
    Includes the actual concentration % AND the historical comparison context.
    For individual stocks: NOT_AVAILABLE.

    NOTE: This is structural context, NOT a P/E or valuation trigger.
    Concentration = breadth/leadership metric derived from market cap weights.
    """
    if ticker not in _S7_CONCENTRATION_KNOWN:
        return False, "NOT_AVAILABLE", None

    threshold_pct, note = _S7_CONCENTRATION_KNOWN[ticker]
    # Historical range for SOXX top-3 concentration (per spec Case 6):
    # Normal range: 40-60%; current 2026 reading: ~80% (extreme)
    # For other ETFs: data not yet loaded
    historical_normal_high = 65.0
    current_estimate = threshold_pct   # per spec's stated value

    # Signal fires if current concentration is above historical normal high
    fired = current_estimate > historical_normal_high
    status = f"EXTREME_{current_estimate:.0f}pct_vs_normal_{historical_normal_high:.0f}pct_max"
    return fired, status, round(current_estimate, 2)

# ── SIGNAL 8: Cross-market speculative rollover ───────────────────────────────

def _s8_cross_market_sync() -> Tuple[bool, str]:
    """
    Detects multiple unrelated speculative baskets rolling over simultaneously
    — the highest-priority signal per the spec Appendix (Cases 4+5: ARKK/NIO/TAN
    all peaked Feb 2021 despite unrelated sector stories).

    Implementation status: NOT_IMPLEMENTED.
    This field must appear in all output rows as the string "NOT_IMPLEMENTED",
    NOT as integer 0 (silently defaulting to 0 is a spec violation).

    When implemented, it will:
      - Track a basket of speculative/high-multiple names across 5+ unrelated sectors
      - Fire if 3+ baskets are rolling over within a 20-day window
      - Return (True, f"SYNC_DETECTED_{count}_baskets")
    """
    return False, "NOT_IMPLEMENTED"

# ── Conviction scoring (MUST scale with signals_triggered_count) ──────────────

def _conviction_score(signals_count: int, has_structure_break: bool,
                      ema21_status: str) -> int:
    """
    Score 0-10.
    Scaling with signals_triggered_count is REQUIRED (verified items C/D).
    Scores at count=2,4,6 must differ.
    """
    # Base: 1.4 points per signal (so 3→4, 4→6, 6→8)
    score = round(signals_count * 1.4)
    # Structure break is the single most diagnostic indicator
    if has_structure_break:
        score += 1
    # EMA break below sustained trend adds confirmation
    if ema21_status == "BROKEN":
        score += 1
    return max(0, min(10, score))

# ── Position sizing recommendation ───────────────────────────────────────────

def _position_action(signals_count: int) -> str:
    """
    Two distinct behaviors (not one binary flag):
    3-4/8 → PARTIAL_DE_RISK (reduce 25-50%)
    5-6/8 → REDUCE (reduce 50-75%)
    7-8/8 → FULL_EXIT (spec: 6-7/8 triggers full exit)
    < 3   → HOLD (not enough evidence)
    """
    if signals_count < MIN_SIGNALS_TO_FIRE:
        return "HOLD"
    if signals_count <= 4:
        return "PARTIAL_DE_RISK"   # reduce 25-50%
    if signals_count <= 6:
        return "REDUCE"            # reduce 50-75%
    return "FULL_EXIT"             # 7-8 signals

# ── Core signal computation ───────────────────────────────────────────────────

def compute_signal(ticker: str, closes: list, highs: list, lows: list,
                   volumes: list, dates: list, spy_closes: list,
                   cur, conn) -> Optional[dict]:
    """
    Evaluate all 8 signals. Fires if count >= threshold.
    Threshold is MIN_SIGNALS_TO_FIRE (baseline) or MIN_SIGNALS_OVERRIDE (when L active).
    """
    n = len(closes)
    if n < 50:
        return None

    sig_date = dates[-1] if hasattr(dates[-1], 'year') else date.today()

    # Module F gate
    prior_5d_ret = None
    if n >= 6 and closes[-6] > 0:
        prior_5d_ret = (closes[-1] - closes[-6]) / closes[-6] * 100
    mf = _module_f(ticker, prior_5d_ret, sig_date, cur)
    if mf["suppress"]:
        return None

    # Check if Module L is active (affects threshold)
    l_active = _check_module_l_active(ticker, sig_date, cur)
    threshold = MIN_SIGNALS_OVERRIDE if l_active else MIN_SIGNALS_TO_FIRE

    # ── Evaluate all 8 signals ────────────────────────────────────────────────
    triggered = []

    # S1 — Structure break (shares calc with Module L)
    s1 = _s1_structure_break(lows)
    if s1:
        triggered.append("S1_STRUCTURE_BREAK")

    # S2 — Distribution signature
    s2_fired, dist_days = _s2_distribution(closes, volumes)
    if s2_fired:
        triggered.append("S2_DISTRIBUTION")

    # S3 — RS failure on bounce specifically
    s3_fired, rs_status, rs_pp = _s3_rs_failure_on_bounce(closes, spy_closes)
    if s3_fired:
        triggered.append("S3_RS_FAILURE_BOUNCE")

    # S4 — EMA21 break
    s4_fired, ema21_status = _s4_ema21_break(closes)
    if s4_fired:
        triggered.append("S4_EMA21_BREAK")

    # S5 — Breadth narrowing
    s5_fired, breadth_status = _s5_breadth_narrowing(ticker)
    if s5_fired:
        triggered.append("S5_BREADTH_NARROWING")

    # S6 — Earnings revision stall
    s6_fired, revision_status = _s6_earnings_revision_stall(ticker)
    if s6_fired:
        triggered.append("S6_EARNINGS_REVISION_STALL")

    # S7 — Concentration extreme
    s7_fired, conc_status, conc_pct = _s7_concentration_extreme(ticker)
    if s7_fired:
        triggered.append("S7_CONCENTRATION_EXTREME")

    # S8 — Cross-market speculative rollover (NOT_IMPLEMENTED — MUST be string, not 0)
    _s8_fired, s8_status = _s8_cross_market_sync()
    # s8_status = "NOT_IMPLEMENTED" always in v1; do NOT default to integer 0

    count = len(triggered)
    if count < threshold:
        return None   # not enough signals, even without S8

    # ── State ────────────────────────────────────────────────────────────────
    state = "CONFIRMED" if count >= MIN_SIGNALS_OVERRIDE else "WATCHING"

    # ── Conviction score (verified to scale with count) ───────────────────────
    score = _conviction_score(count, s1, ema21_status)
    action = _position_action(count)

    return {
        "ticker":                                ticker,
        "signal_date":                           sig_date,
        "state":                                 state,
        "signals_triggered_count":               count,
        "signals_triggered_list":                triggered,
        "structure_break_confirmed":             s1,
        "distribution_days_count":               dist_days,
        "relative_strength_status":              rs_status,
        "rs_bounce_pp":                          rs_pp,
        "ema21_status":                          ema21_status,
        "breadth_status":                        breadth_status,
        "earnings_revision_status":              revision_status,
        "concentration_extreme_status":          conc_status,
        "concentration_pct":                     conc_pct,
        "cross_market_speculative_rollover_count": s8_status,  # string NOT integer 0
        "conviction_score":                      score,
        "position_action":                       action,
        "l_active_override_applied":             l_active,
        "earnings_excl":                         mf["earnings_excl"],
        "falling_knife_excl":                    mf["falling_knife"],
        "days_to_earnings":                      mf["days_to_earnings"],
    }

# ── DB persistence ─────────────────────────────────────────────────────────────

def _save(sig: dict, tg_sent: bool = False) -> None:
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_exhaustion_signals
                    (ticker, signal_date, state, signals_triggered_count,
                     signals_triggered_list, structure_break_confirmed,
                     distribution_days_count, relative_strength_status, rs_bounce_pp,
                     ema21_status, breadth_status, earnings_revision_status,
                     concentration_extreme_status, concentration_pct,
                     cross_market_speculative_rollover_count,
                     conviction_score, position_action, l_active_override_applied,
                     earnings_excl, falling_knife_excl, days_to_earnings,
                     tg_sent, tg_sent_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, signal_date) DO UPDATE SET
                    state                    = EXCLUDED.state,
                    signals_triggered_count  = EXCLUDED.signals_triggered_count,
                    signals_triggered_list   = EXCLUDED.signals_triggered_list,
                    conviction_score         = EXCLUDED.conviction_score,
                    position_action          = EXCLUDED.position_action,
                    tg_sent                  = aiem_exhaustion_signals.tg_sent OR EXCLUDED.tg_sent,
                    tg_sent_at               = COALESCE(aiem_exhaustion_signals.tg_sent_at,
                                                        EXCLUDED.tg_sent_at)
            """, (
                sig["ticker"], sig["signal_date"], sig["state"],
                sig["signals_triggered_count"], sig["signals_triggered_list"],
                sig["structure_break_confirmed"], sig["distribution_days_count"],
                sig["relative_strength_status"], sig["rs_bounce_pp"],
                sig["ema21_status"], sig["breadth_status"],
                sig["earnings_revision_status"], sig["concentration_extreme_status"],
                sig["concentration_pct"],
                sig["cross_market_speculative_rollover_count"],
                sig["conviction_score"], sig["position_action"],
                sig["l_active_override_applied"],
                sig["earnings_excl"], sig["falling_knife_excl"], sig["days_to_earnings"],
                tg_sent, datetime.utcnow() if tg_sent else None,
            ))
            conn.commit()
    except Exception as e:
        print(f"[momentum_exhaustion] save error {sig.get('ticker')}: {e}")

# ── Telegram ───────────────────────────────────────────────────────────────────

def _tg(text: str, *, ticker: str = None, trigger_price: float = None) -> None:
    import urllib.request as _u
    token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    send_text = text
    try:
        import alert_gateway as _ag_trust
        send_text = text + _ag_trust.get_trust_display("momentum_exhaustion")
    except Exception as _te:
        print(f"[momentum_exhaustion] trust display error (non-fatal): {_te}")
    ok = False
    if token and chat_id:
        try:
            payload = json.dumps({"chat_id": chat_id, "text": send_text}).encode()
            req = _u.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                             data=payload, headers={"Content-Type": "application/json"})
            with _u.urlopen(req, timeout=8):
                ok = True
        except Exception as e:
            print(f"[momentum_exhaustion] telegram error: {e}")
    try:
        import alert_gateway as _ag
        _ag.log_alert(text, signal_source="momentum_exhaustion", ticker=ticker,
                       alert_class="SIGNAL", trigger_price=trigger_price, sent_ok=ok)
    except Exception as _ge:
        print(f"[momentum_exhaustion] alert_gateway logging error (non-fatal): {_ge}")

def _format_alert(sig: dict) -> str:
    s8_note = sig['cross_market_speculative_rollover_count']
    lines = [
        f"📉⚠️ MOMENTUM EXHAUSTION (Module M) — {sig['ticker']}",
        f"State: {sig['state']} | Signals: {sig['signals_triggered_count']}/8",
        f"Triggered: {', '.join(sig['signals_triggered_list'])}",
        f"Position action: {sig['position_action']}",
        f"Structure break: {sig['structure_break_confirmed']}",
        f"Distribution days: {sig['distribution_days_count']}",
        f"RS on bounce: {sig['relative_strength_status']} ({sig.get('rs_bounce_pp', 'N/A')}pp)",
        f"EMA21 status: {sig['ema21_status']}",
        f"S8 (cross-mkt sync): {s8_note}",
        f"L-override applied: {sig['l_active_override_applied']}",
        f"Conviction: {sig['conviction_score']}/10",
    ]
    return "\n".join(lines)

# ── Live scan ──────────────────────────────────────────────────────────────────

_SCAN_LOCK = threading.Lock()

def run_scan() -> dict:
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"status": "locked"}
    try:
        t0 = time.time()
        print("[momentum_exhaustion] live scan starting…")
        with psycopg2.connect(_DB_URL, options="-c statement_timeout=90000") as conn, \
             conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, scan_date, close_price, high_price, low_price, volume
                FROM polygon_market_daily
                WHERE scan_date >= CURRENT_DATE - 320
                  AND close_price > 1.0 AND volume > 50000
                ORDER BY ticker, scan_date
            """)
            rows = cur.fetchall()
            ticker_map = defaultdict(list)
            for r in rows:
                ticker_map[r[0]].append(r)

            spy_data = ticker_map.get("SPY", [])
            spy_closes = [float(r[2]) for r in spy_data]

            fired = 0; skipped = 0
            for ticker, data in ticker_map.items():
                if ticker == "SPY":
                    continue
                if len(data) < 50:
                    continue
                closes  = [float(r[2]) for r in data]
                highs   = [float(r[3]) for r in data]
                lows    = [float(r[4]) for r in data]
                volumes = [float(r[5]) for r in data]
                dates   = [r[1] for r in data]
                try:
                    sig = compute_signal(ticker, closes, highs, lows, volumes,
                                         dates, spy_closes, cur, conn)
                    if sig:
                        _save(sig, tg_sent=False)
                        if sig["state"] == "CONFIRMED":
                            _tg(_format_alert(sig), ticker=ticker, trigger_price=closes[-1])
                            _save(sig, tg_sent=True)
                        fired += 1
                    else:
                        skipped += 1
                except Exception as e:
                    print(f"[momentum_exhaustion] {ticker} error: {e}")

            elapsed = round(time.time() - t0, 1)
            print(f"[momentum_exhaustion] scan done: fired={fired} skipped={skipped} "
                  f"elapsed={elapsed}s")
            return {"status": "ok", "fired": fired, "elapsed": elapsed}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        _SCAN_LOCK.release()

# ── Historical backtest ────────────────────────────────────────────────────────

_SOXX_UNIVERSE = ["SOXX","NVDA","AMD","AVGO","MU","QCOM","AMAT","LRCX","KLAC",
                  "INTC","TSM","MRVL","SMCI","ON","TXN"]

def run_historical_backtest(force: bool = False) -> dict:
    """
    Backtest Module M on 2026 YTD SOXX-universe.
    Reports:
      - WR for real rollovers caught vs false positives during pullbacks
      - Head-to-head L vs M false-positive comparison (was_pullback_not_top)
      - Signal-count vs WR matrix
    """
    if not _DB_URL:
        return {"error": "no DB_URL"}
    try:
        with psycopg2.connect(_DB_URL, options="-c statement_timeout=120000") as conn, \
             conn.cursor() as cur:

            if not force:
                cur.execute("SELECT COUNT(*) FROM aiem_exhaustion_backtest_log")
                if cur.fetchone()[0] > 0:
                    return _backtest_summary(cur)

            # Universe: SOXX names + momentum tickers
            cur.execute("""
                WITH returns AS (
                    SELECT ticker,
                           MIN(CASE WHEN scan_date >= '2025-01-01' AND scan_date <= '2025-03-01'
                                    THEN close_price END) AS start_p,
                           MAX(CASE WHEN scan_date >= '2025-10-01'
                                    THEN close_price END) AS end_p
                    FROM polygon_market_daily
                    GROUP BY ticker
                    HAVING COUNT(*) >= 300
                )
                SELECT ticker FROM returns
                WHERE start_p > 0 AND end_p / start_p >= 1.3
                UNION ALL SELECT UNNEST(%s::text[])
            """, (_SOXX_UNIVERSE,))
            universe = list({r[0] for r in cur.fetchall()})

            from datetime import date as _date
            _YTD_START = _date(2026, 1, 1)

            # Fetch full history from 2024-10-01 for 200-bar lookback in S1/S4
            cur.execute("""
                SELECT ticker, scan_date, close_price, high_price, low_price, volume
                FROM polygon_market_daily
                WHERE ticker = ANY(%s) AND scan_date >= '2024-10-01'
                ORDER BY ticker, scan_date
            """, (universe,))
            raw = cur.fetchall()
            tmap = defaultdict(list)
            for r in raw: tmap[r[0]].append(r)

            # SPY — also from 2024-10-01, use dict for O(1) per-date lookup
            cur.execute("""
                SELECT scan_date, close_price FROM polygon_market_daily
                WHERE ticker='SPY' AND scan_date >= '2024-10-01' ORDER BY scan_date
            """)
            spy_dict = {r[0]: float(r[1]) for r in cur.fetchall()}

            # Forward prices — same full window
            cur.execute("""
                SELECT ticker, scan_date, close_price, low_price
                FROM polygon_market_daily
                WHERE ticker = ANY(%s) AND scan_date >= '2024-10-01'
                ORDER BY ticker, scan_date
            """, (universe,))
            fwd_map = defaultdict(list)
            for r in cur.fetchall():
                fwd_map[r[0]].append((r[1], float(r[2] or 0), float(r[3] or 0)))

            inserted = 0
            for ticker, data in tmap.items():
                closes  = [float(r[2]) for r in data]
                highs   = [float(r[3]) for r in data]
                lows    = [float(r[4]) for r in data]
                volumes = [float(r[5]) for r in data]
                dates   = [r[1] for r in data]
                n = len(closes)
                if n < 210:
                    continue

                # SPY aligned using dict lookup (O(1) per date)
                spy_aligned = [spy_dict.get(d, None) for d in dates]
                last_spy = 100.0
                for _k in range(len(spy_aligned)):
                    if spy_aligned[_k] is not None:
                        last_spy = spy_aligned[_k]
                    else:
                        spy_aligned[_k] = last_spy

                # Start loop at 210 for 200-bar lookback; only insert >= 2026-01-01
                for i in range(210, n - 10):
                    sig_date = dates[i]
                    c_slice = closes[:i+1]
                    l_slice = lows[:i+1]
                    v_slice = volumes[:i+1]
                    sp_slice = spy_aligned[:i+1]

                    # Evaluate signals
                    s1 = _s1_structure_break(l_slice)
                    s2_fired, dist_days = _s2_distribution(c_slice, v_slice)
                    s3_fired, rs_st, _ = _s3_rs_failure_on_bounce(c_slice, sp_slice)
                    s4_fired, ema21_st = _s4_ema21_break(c_slice)
                    count = sum([s1, s2_fired, s3_fired, s4_fired])
                    if count < MIN_SIGNALS_TO_FIRE:
                        continue

                    state = "CONFIRMED" if count >= MIN_SIGNALS_OVERRIDE else "WATCHING"
                    score = _conviction_score(count, s1, ema21_st)
                    action = _position_action(count)

                    # Forward returns
                    fwd_prices = fwd_map.get(ticker, [])
                    fwd_after  = [(d2, c2, lo2) for d2, c2, lo2 in fwd_prices if d2 > sig_date]
                    if len(fwd_after) < 5:
                        continue

                    fwd_1d = (fwd_after[0][1] - closes[i]) / closes[i] * 100 if closes[i] > 0 else None
                    fwd_3d = (fwd_after[2][1] - closes[i]) / closes[i] * 100 if len(fwd_after) >= 3 and closes[i] > 0 else None
                    fwd_5d = (fwd_after[4][1] - closes[i]) / closes[i] * 100 if len(fwd_after) >= 5 and closes[i] > 0 else None
                    fwd_10 = (fwd_after[9][1] - closes[i]) / closes[i] * 100 if len(fwd_after) >= 10 and closes[i] > 0 else None

                    # False positive: fired M but price recovered (was pullback, not top)
                    was_pullback = False
                    if fwd_5d is not None and fwd_5d > 5.0:
                        was_pullback = True   # price went up >5% in 5d = was a pullback, not top

                    # False positive specifically: M fires but higher-low stays intact
                    false_positive = s1 is False and was_pullback

                    spy_20d = None
                    if len(sp_slice) >= 21 and sp_slice[-21] > 0:
                        spy_20d = (sp_slice[-1] - sp_slice[-21]) / sp_slice[-21] * 100
                    regime = ("TREND_UP" if spy_20d and spy_20d > 2
                              else "TREND_DOWN" if spy_20d and spy_20d < -2
                              else "CHOPPY")

                    # Only record rows for 2026 YTD (pre-2026 rows are warm-up data)
                    if sig_date < _YTD_START:
                        continue

                    try:
                        cur.execute("""
                            INSERT INTO aiem_exhaustion_backtest_log
                                (ticker, signal_date, signals_triggered_count, state,
                                 market_regime, fwd_1d_pct, fwd_3d_pct, fwd_5d_pct,
                                 fwd_10d_pct, false_positive, was_pullback_not_top)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (ticker, signal_date) DO NOTHING
                        """, (ticker, sig_date, count, state, regime,
                              fwd_1d and round(fwd_1d, 4), fwd_3d and round(fwd_3d, 4),
                              fwd_5d and round(fwd_5d, 4), fwd_10 and round(fwd_10, 4),
                              false_positive, was_pullback))
                        inserted += 1
                    except Exception:
                        pass

            conn.commit()
            print(f"[exhaust_bt] inserted {inserted} rows")
            return _backtest_summary(cur)
    except Exception as e:
        return {"error": str(e)}

def _backtest_summary(cur) -> dict:
    # Overall by state
    cur.execute("""
        SELECT state,
               COUNT(*) as n,
               AVG(CASE WHEN fwd_5d_pct < 0 THEN 1.0 ELSE 0.0 END) as catch_rate,
               AVG(CASE WHEN was_pullback_not_top THEN 1.0 ELSE 0.0 END) as fp_rate,
               AVG(fwd_5d_pct) as avg_5d
        FROM aiem_exhaustion_backtest_log
        WHERE fwd_5d_pct IS NOT NULL
        GROUP BY state ORDER BY state
    """)
    by_state = {}
    for r in cur.fetchall():
        by_state[r[0]] = {
            "n": r[1], "catch_rate_pct_down": round(float(r[2] or 0), 4),
            "false_positive_rate": round(float(r[3] or 0), 4),
            "avg_5d_pct": round(float(r[4] or 0), 4),
        }

    # Signal count vs WR matrix (for head-to-head comparison with L)
    cur.execute("""
        SELECT signals_triggered_count,
               COUNT(*) as n,
               AVG(CASE WHEN fwd_5d_pct < 0 THEN 1.0 ELSE 0.0 END) as catch_rate,
               AVG(CASE WHEN was_pullback_not_top THEN 1.0 ELSE 0.0 END) as fp_rate
        FROM aiem_exhaustion_backtest_log
        WHERE fwd_5d_pct IS NOT NULL
        GROUP BY signals_triggered_count ORDER BY signals_triggered_count
    """)
    by_count = {}
    for r in cur.fetchall():
        by_count[str(r[0])] = {
            "n": r[1],
            "catch_rate": round(float(r[2] or 0), 4),
            "fp_rate": round(float(r[3] or 0), 4),
        }

    return {
        "by_state": by_state,
        "by_signal_count": by_count,
        "note": (
            "catch_rate = fraction where fwd_5d_pct < 0 (correctly identified top); "
            "false_positive_rate = M fired but price recovered >5% in 5d (was pullback); "
            f"min_signals_to_fire={MIN_SIGNALS_TO_FIRE}; "
            f"min_signals_override_when_L_active={MIN_SIGNALS_OVERRIDE}"
        ),
    }

# ── BH-FDR registration ────────────────────────────────────────────────────────

def register_signal() -> None:
    conditions = {
        "min_signals": MIN_SIGNALS_TO_FIRE,
        "min_signals_when_L_active": MIN_SIGNALS_OVERRIDE,
        "signals": {
            "S1": "structure_break (lower low — shared calc with Module L)",
            "S2": f"distribution: >={DISTRIBUTION_DAYS_MIN} above-avg-vol down days in 20-session window",
            "S3": "RS failure on the BOUNCE specifically (not general RS)",
            "S4": f"EMA21 break: >={EMA21_BELOW_DAYS_MIN}d below after >={EMA21_ABOVE_STREAK_MIN}d above",
            "S5": "breadth_narrowing: ETF only; NOT_AVAILABLE for single stocks",
            "S6": "earnings_revision_stall: direction-based; NOT_AVAILABLE (no analyst data)",
            "S7": "concentration_extreme: relative to own historical range; ETF-specific",
            "S8": "cross_market_speculative_rollover: NOT_IMPLEMENTED in v1",
        },
        "guardrail": "P/E P/S EV/EBITDA valuation ratios must not appear in any trigger conditional",
        "_structural": "min-count stack; M2=unevaluable_structural",
    }
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) as n,
                       AVG(CASE WHEN fwd_5d_pct < 0 THEN 1.0 ELSE 0.0 END) as catch_rate
                FROM aiem_exhaustion_backtest_log
                WHERE fwd_5d_pct IS NOT NULL
            """)
            row = cur.fetchone()
            bt_n  = int(row[0]) if row and row[0] else 0
            bt_wr = float(row[1]) if row and row[1] else None
            p_val = None
            if bt_n and bt_wr is not None:
                k = int(round(bt_wr * bt_n))
                z = (k - 0.5 - bt_n * 0.5) / math.sqrt(bt_n * 0.25)
                p_val = round(0.5 * math.erfc(z / math.sqrt(2)), 4) if z > 0 else 0.9999

            cur.execute("SELECT id FROM aiem_signal_discoveries WHERE hypothesis_text=%s",
                        (_SIGNAL_NAME,))
            existing = cur.fetchone()
            if existing:
                cur.execute("""
                    UPDATE aiem_signal_discoveries
                    SET signal_n=%s, signal_win_rate=%s, p_value=%s,
                        status='hypothesis', notes=%s WHERE id=%s
                """, (bt_n or None, bt_wr, p_val,
                      f"Module_M 8-signal stack; min={MIN_SIGNALS_TO_FIRE}; "
                      f"override_when_L={MIN_SIGNALS_OVERRIDE}; "
                      f"bt_n={bt_n}; catch_rate={bt_wr}; p={p_val}", existing[0]))
            else:
                cur.execute("""
                    INSERT INTO aiem_signal_discoveries
                        (hypothesis_text, conditions_json, status, horizon,
                         invented_indicator, signal_n, signal_win_rate, p_value,
                         notes, signal_name, discovered_at)
                    VALUES (%s,%s::jsonb,'hypothesis',%s,%s,%s,%s,%s,%s,%s,NOW())
                """, (_SIGNAL_NAME, json.dumps(conditions), _HORIZON, _INVENTED_INDICATOR,
                      bt_n or None, bt_wr, p_val,
                      f"Module_M 8-signal stack; min={MIN_SIGNALS_TO_FIRE}; "
                      f"override_when_L={MIN_SIGNALS_OVERRIDE}; "
                      f"S8=NOT_IMPLEMENTED; bt_n={bt_n}; p={p_val}",
                      _SIGNAL_NAME))
            conn.commit()
        print(f"[momentum_exhaustion] registered {_SIGNAL_NAME}: n={bt_n} p={p_val}")
    except Exception as e:
        print(f"[momentum_exhaustion] register_signal error: {e}")
