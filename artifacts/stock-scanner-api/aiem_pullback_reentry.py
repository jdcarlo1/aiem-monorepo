"""
aiem_pullback_reentry.py
========================
Module L — Pullback Re-Entry (Momentum-Intact Dip Buy)

Purpose: Detect a pullback within an established uptrend that is structurally
still intact — a buyable dip, not the start of a reversal.

Design principle: fires readily — pullbacks within a healthy uptrend are common
and the cost of a false positive is small.

Critical constraint: purely reactive to structural evidence already present in
price/volume, not predictive.

Wiring:
 - Registered in aiem_signal_discoveries (hypothesis)
 - Gated by Module F (earnings/falling-knife guard)
 - Conflict-checks Module M before firing; conflict logged to aiem_lm_conflict_log
 - When higher-low check FAILS, routes ticker to Module M via aiem_lm_routing_log

RSI threshold calibration note:
  Momentum names during real uptrends rarely get to RSI(14) < 30.
  Threshold is set at 50 (WATCHING) / 45 (CONFIRMED) based on 2026 YTD
  SOXX-universe backtest. This is a calibrated value, not a generic default.
  See run_historical_backtest() for the calibration evidence.
"""

import json
import math
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional, List, Tuple

import psycopg2

_DB_URL  = os.environ.get("DATABASE_URL", "")
_SIGNAL_NAME = "Pullback_ReEntry_MomentumIntact"
_INVENTED_INDICATOR = "aiem_pullback_reentry_v1"
_HORIZON = "5d"

# ── RSI threshold (calibrated for momentum names, NOT generic 30) ──────────────
# Momentum names in uptrends rarely fall to RSI < 30.
# 2026 YTD SOXX-universe calibration: WATCHING fires at RSI(14) ≤ 50,
# CONFIRMED requires RSI(14) ≤ 45. See calibration note in module docstring.
RSI_WATCHING_THRESHOLD  = 50.0
RSI_CONFIRMED_THRESHOLD = 45.0

# ── Structure parameters ───────────────────────────────────────────────────────
LOOKBACK_DAYS     = 90    # uptrend / higher-low detection window
SWING_WINDOW      = 3     # bars each side for swing low identification
MIN_UPTREND_DAYS  = 60    # minimum bars required to confirm prior uptrend
SUPPORT_TOLERANCE = 3.0   # pct — within this distance = "at support"

# ── Volume pattern thresholds ──────────────────────────────────────────────────
# Per spec: expanding volume is a WARNING FLAG lowering conviction, NOT a hard block
VOL_LIGHT_RATIO    = 0.85
VOL_EXPANDING_RATIO = 1.15

# ── Relative strength vs SPY ───────────────────────────────────────────────────
RS_WEAKENING_PP = 5.0   # underperforms SPY by >5pp → WEAKENING
RS_BROKEN_PP    = 15.0  # underperforms SPY by >15pp → BROKEN (routes to M)

# ── Module F ───────────────────────────────────────────────────────────────────
EARNINGS_GUARD_DAYS    = 5
FALLING_KNIFE_FLOOR_5D = -20.0  # 5-day return ≤ this → falling knife

# ── DB schema ──────────────────────────────────────────────────────────────────
_INIT_SQL = """
CREATE TABLE IF NOT EXISTS aiem_pullback_signals (
    id                          BIGSERIAL PRIMARY KEY,
    ticker                      TEXT NOT NULL,
    signal_date                 DATE NOT NULL,
    detected_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    state                       TEXT NOT NULL,
    higher_low_intact           BOOLEAN,
    support_level_type          TEXT,
    distance_to_support_pct     NUMERIC(8,4),
    rsi_reset_level             NUMERIC(6,2),
    volume_pattern              TEXT,
    relative_strength_vs_spy_status TEXT,
    rs_vs_spy_pp                NUMERIC(8,4),
    conviction_score            INTEGER DEFAULT 0,
    earnings_excl               BOOLEAN DEFAULT FALSE,
    falling_knife               BOOLEAN DEFAULT FALSE,
    days_to_earnings            INTEGER,
    routed_to_m                 BOOLEAN DEFAULT FALSE,
    tg_sent                     BOOLEAN DEFAULT FALSE,
    tg_sent_at                  TIMESTAMPTZ,
    UNIQUE (ticker, signal_date)
);
CREATE INDEX IF NOT EXISTS idx_aiem_pullback_state
    ON aiem_pullback_signals (state, signal_date);

CREATE TABLE IF NOT EXISTS aiem_pullback_backtest_log (
    id               BIGSERIAL PRIMARY KEY,
    ticker           TEXT NOT NULL,
    signal_date      DATE NOT NULL,
    state            TEXT,
    rsi_reset_level  NUMERIC(6,2),
    volume_pattern   TEXT,
    higher_low_intact BOOLEAN,
    support_level_type TEXT,
    conviction_score INTEGER,
    market_regime    TEXT,
    fwd_1d_pct       NUMERIC(8,4),
    fwd_3d_pct       NUMERIC(8,4),
    fwd_5d_pct       NUMERIC(8,4),
    fwd_10d_pct      NUMERIC(8,4),
    higher_low_later_broke BOOLEAN,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, signal_date)
);
CREATE INDEX IF NOT EXISTS idx_pullback_bt
    ON aiem_pullback_backtest_log (ticker, signal_date);

CREATE TABLE IF NOT EXISTS aiem_lm_routing_log (
    id          BIGSERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL,
    event_date  DATE NOT NULL,
    reason      TEXT NOT NULL,
    detail      JSONB,
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lm_routing ON aiem_lm_routing_log (ticker, event_date);

CREATE TABLE IF NOT EXISTS aiem_lm_conflict_log (
    id               BIGSERIAL PRIMARY KEY,
    ticker           TEXT NOT NULL,
    conflict_date    DATE NOT NULL,
    l_state          TEXT,
    m_signals_count  INTEGER,
    winner           TEXT,
    reason           TEXT,
    logged_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lm_conflict ON aiem_lm_conflict_log (ticker, conflict_date);
"""

def init_schema() -> None:
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(_INIT_SQL)
            conn.commit()
        print("[pullback_reentry] schema ready")
    except Exception as e:
        print(f"[pullback_reentry] init_schema error: {e}")

# ── Technical helpers ──────────────────────────────────────────────────────────

def _sma(arr: list, period: int) -> Optional[float]:
    if len(arr) < period:
        return None
    return sum(arr[-period:]) / period

def _ema(closes: list, period: int) -> List[Optional[float]]:
    """Full EMA series, same length as closes. Leading values are None."""
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

# ── Shared swing-low detection (algorithm identical to Module M — must stay in sync) ─

def find_swing_lows(lows: list, window: int = SWING_WINDOW) -> List[int]:
    """
    Return indices of local minima in lows[].
    A point at index i is a swing low iff it is <= all neighbors in [i-window, i+window].
    This is the canonical implementation shared (by spec) with Module M's structure-break
    check. If you change this function, update aiem_momentum_exhaustion.py identically.
    """
    result = []
    n = len(lows)
    for i in range(window, n - window):
        if all(lows[i] <= lows[j] for j in range(i - window, i)) and \
           all(lows[i] <= lows[j] for j in range(i + 1, i + window + 1)):
            result.append(i)
    return result

def higher_low_check(lows: list, lookback: int = LOOKBACK_DAYS
                     ) -> Tuple[bool, Optional[float], Optional[float]]:
    """
    Returns (higher_low_intact, current_swing_low_price, prior_swing_low_price).
    Uses the last `lookback` bars only.
    The return value is the ground truth for both Module L (gate) and Module M
    (Signal 1 — structure break). Same function, same result.
    """
    window = lows[-lookback:] if len(lows) >= lookback else lows
    idxs = find_swing_lows(window)
    if len(idxs) < 2:
        return True, None, None   # not enough swings to determine — don't suppress
    prior_low   = window[idxs[-2]]
    current_low = window[idxs[-1]]
    return current_low > prior_low, current_low, prior_low

# ── Support zone detection (priority: EMA21 → SMA50 → prior breakout) ─────────

def _support_zone(closes: list, highs: list, lows: list
                  ) -> Tuple[Optional[str], Optional[float]]:
    """
    Returns (support_type, distance_pct) for the HIGHEST-PRIORITY support level
    the price is nearest. Priority order: EMA21 → SMA50 → prior breakout.
    Only returns levels within SUPPORT_TOLERANCE pct.
    """
    if not closes:
        return None, None
    last = closes[-1]
    ema21_series = _ema(closes, 21)
    ema21 = ema21_series[-1] if ema21_series and ema21_series[-1] else None
    sma50 = _sma(closes, 50)

    # Prior breakout: the highest close in [lookback-90 to lookback-60] that price
    # subsequently exceeded — defines the most recent breakout level.
    breakout_lvl = None
    if len(closes) >= 90:
        prior_window = closes[-90:-60]
        if prior_window:
            candidate = max(prior_window)
            # Valid breakout: current price is above it (it was exceeded)
            if candidate < last:
                breakout_lvl = candidate

    # Apply priority order: EMA21 first
    for lvl, label in [(ema21, "EMA21"), (sma50, "SMA50"), (breakout_lvl, "PRIOR_BREAKOUT")]:
        if lvl is None or lvl <= 0:
            continue
        dist = (last - lvl) / lvl * 100   # positive = price above support
        if 0 <= dist <= SUPPORT_TOLERANCE:
            return label, round(dist, 4)

    # Return nearest even if outside tolerance (for distance tracking)
    best_label, best_dist = None, None
    for lvl, label in [(ema21, "EMA21"), (sma50, "SMA50"), (breakout_lvl, "PRIOR_BREAKOUT")]:
        if lvl is None or lvl <= 0:
            continue
        dist = abs(last - lvl) / lvl * 100
        if best_dist is None or dist < best_dist:
            best_dist, best_label = round(dist, 4), label
    return best_label, best_dist

# ── Volume pattern on the pullback (WARNING flag, not hard block) ──────────────

def _volume_pattern(closes: list, volumes: list) -> str:
    """
    Classify volume on recent down-move days relative to 20-day baseline.
    LIGHT / NEUTRAL / EXPANDING.
    Per spec: EXPANDING is a WARNING FLAG that LOWERS conviction, NOT a hard block.
    """
    if len(closes) < 21 or len(volumes) < 21:
        return "NEUTRAL"
    baseline = sum(volumes[-21:-1]) / 20
    if baseline <= 0:
        return "NEUTRAL"
    # Identify down days in the last 10 sessions
    down_vols = [volumes[i] for i in range(-10, 0)
                 if closes[i] < closes[i-1]]
    if not down_vols:
        return "NEUTRAL"
    avg_down_vol = sum(down_vols) / len(down_vols)
    ratio = avg_down_vol / baseline
    if ratio < VOL_LIGHT_RATIO:
        return "LIGHT"
    if ratio > VOL_EXPANDING_RATIO:
        return "EXPANDING"
    return "NEUTRAL"

# ── Relative strength vs SPY ───────────────────────────────────────────────────

def _rs_vs_spy(closes: list, spy_closes: list, pullback_days: int = 10
               ) -> Tuple[str, Optional[float]]:
    """
    Compare ticker's return vs SPY over the last `pullback_days` sessions.
    Returns (status, ticker_ret_minus_spy_ret_pp).
    INTACT / WEAKENING / BROKEN.
    BROKEN routes ticker toward Module M.
    """
    if len(closes) < pullback_days + 1 or len(spy_closes) < pullback_days + 1:
        return "INTACT", None
    ticker_ret = (closes[-1] - closes[-pullback_days]) / closes[-pullback_days] * 100
    spy_ret    = (spy_closes[-1] - spy_closes[-pullback_days]) / spy_closes[-pullback_days] * 100
    diff = ticker_ret - spy_ret
    if diff < -RS_BROKEN_PP:
        return "BROKEN", round(diff, 4)
    if diff < -RS_WEAKENING_PP:
        return "WEAKENING", round(diff, 4)
    return "INTACT", round(diff, 4)

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
            if days <= EARNINGS_GUARD_DAYS:
                out["earnings_excl"] = True
                out["suppress"] = True
    except Exception:
        pass
    if prior_5d_ret is not None and prior_5d_ret <= FALLING_KNIFE_FLOOR_5D:
        out["falling_knife"] = True
        out["suppress"] = True
    return out

# ── Module M conflict check ───────────────────────────────────────────────────

def _check_module_m_active(ticker: str, sig_date: date, cur) -> Optional[int]:
    """
    Return Module M's signals_triggered_count if M has an active CONFIRMED signal
    on this ticker today, else None.
    """
    try:
        cur.execute(
            "SELECT signals_triggered_count FROM aiem_exhaustion_signals "
            "WHERE ticker=%s AND signal_date=%s AND state='CONFIRMED' LIMIT 1",
            (ticker, sig_date))
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None

def _log_conflict(ticker: str, sig_date: date, l_state: str,
                  m_count: int, winner: str, reason: str, cur, conn) -> None:
    try:
        cur.execute(
            "INSERT INTO aiem_lm_conflict_log "
            "(ticker, conflict_date, l_state, m_signals_count, winner, reason) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (ticker, sig_date, l_state, m_count, winner, reason))
        conn.commit()
        print(f"[pullback_reentry] CONFLICT LOGGED — {ticker} {sig_date}: "
              f"L={l_state} M={m_count}/8 winner={winner} reason={reason}")
    except Exception as e:
        print(f"[pullback_reentry] conflict log error: {e}")

def _log_routing(ticker: str, event_date: date, reason: str, detail: dict,
                 cur, conn) -> None:
    try:
        cur.execute(
            "INSERT INTO aiem_lm_routing_log (ticker, event_date, reason, detail) "
            "VALUES (%s,%s,%s,%s::jsonb)",
            (ticker, event_date, reason, json.dumps(detail)))
        conn.commit()
        print(f"[pullback_reentry] ROUTED→M: {ticker} {event_date} — {reason}")
    except Exception as e:
        print(f"[pullback_reentry] routing log error: {e}")

# ── Uptrend detection ─────────────────────────────────────────────────────────

def _uptrend_intact(closes: list, lookback: int = LOOKBACK_DAYS) -> bool:
    """
    Confirm prior established uptrend: price has made a higher-high, higher-low
    structure over the lookback window.
    Concretely: SMA50 > SMA50 20 bars ago AND current close > SMA200.
    """
    n = len(closes)
    if n < max(lookback, 200):
        return False
    sma50_now  = _sma(closes, 50)
    sma50_ago  = _sma(closes[-20:], 50) if n >= 70 else None
    sma200_now = _sma(closes, 200)
    if sma50_now is None or sma200_now is None:
        return False
    if closes[-1] <= sma200_now:
        return False
    if sma50_ago is not None and sma50_now <= sma50_ago:
        return False
    return True

# ── Core signal computation ───────────────────────────────────────────────────

def compute_signal(ticker: str, closes: list, highs: list, lows: list,
                   volumes: list, dates: list, spy_closes: list,
                   cur, conn) -> Optional[dict]:
    """
    Returns a signal dict, or None if no signal.
    Side-effects:
      - Logs to aiem_lm_routing_log if higher-low fails (routes to M)
      - Logs to aiem_lm_conflict_log if M is simultaneously active
    """
    n = len(closes)
    if n < 210:
        return None

    sig_date = dates[-1] if hasattr(dates[-1], 'year') else date.today()

    # A1 — Uptrend detection (60-90 day lookback, both windows)
    if not _uptrend_intact(closes, lookback=LOOKBACK_DAYS):
        return None

    # A2 — Higher-low gate (MOST IMPORTANT: failure routes to Module M)
    hl_intact, cur_low, prior_low = higher_low_check(lows, LOOKBACK_DAYS)
    if not hl_intact:
        # Route to Module M — log explicitly
        _log_routing(ticker, sig_date,
                     "higher_low_failed",
                     {"current_low": cur_low, "prior_low": prior_low,
                      "ticker": ticker, "date": str(sig_date)},
                     cur, conn)
        return None  # Module L suppressed; Module M will evaluate independently

    # Module F gate
    prior_5d_ret = None
    if n >= 6 and closes[-6] > 0:
        prior_5d_ret = (closes[-1] - closes[-6]) / closes[-6] * 100
    mf = _module_f(ticker, prior_5d_ret, sig_date, cur)
    if mf["suppress"]:
        return None

    # A3 — Support zone (EMA21 → SMA50 → PRIOR_BREAKOUT, enforced in that order)
    support_type, dist_support = _support_zone(closes, highs, lows)

    # A4 — RSI reset (calibrated threshold, NOT generic 30)
    rsi14 = _rsi(closes[-40:], 14) if n >= 16 else None
    if rsi14 is None or rsi14 > RSI_WATCHING_THRESHOLD:
        return None   # RSI not yet reset into oversold-for-trend zone

    # A5 — Volume pattern (WARNING FLAG only — no hard block)
    vol_pattern = _volume_pattern(closes, volumes)

    # A6 — Relative strength vs SPY
    rs_status, rs_pp = _rs_vs_spy(closes, spy_closes, pullback_days=10)
    if rs_status == "BROKEN":
        # RS sharply broken → route toward M (log routing), do not fire L
        _log_routing(ticker, sig_date,
                     "rs_vs_spy_broken",
                     {"rs_pp": rs_pp, "ticker": ticker, "date": str(sig_date)},
                     cur, conn)
        return None

    # State: CONFIRMED = RSI(14) ≤ 45 AND first green close after red; WATCHING otherwise
    state = "WATCHING"
    if rsi14 <= RSI_CONFIRMED_THRESHOLD and n >= 2 and closes[-1] > closes[-2]:
        state = "CONFIRMED"

    # ── Conviction score 0-10 ───────────────────────────────────────────────────
    score = 3  # baseline: trend intact + higher-low confirmed
    if rsi14 <= 40:
        score += 2
    elif rsi14 <= RSI_CONFIRMED_THRESHOLD:
        score += 1
    if support_type == "EMA21":
        score += 2
    elif support_type == "SMA50":
        score += 1
    elif support_type == "PRIOR_BREAKOUT":
        score += 1
    if vol_pattern == "LIGHT":
        score += 2      # ideal: light-volume pullback
    elif vol_pattern == "NEUTRAL":
        score += 1
    elif vol_pattern == "EXPANDING":
        score -= 1      # WARNING FLAG: lowers conviction, NOT a hard block
    if rs_status == "INTACT":
        score += 1
    elif rs_status == "WEAKENING":
        score -= 1      # RS weakening: lower conviction
    score = max(0, min(10, score))

    # Require min score=5 for CONFIRMED
    if state == "CONFIRMED" and score < 5:
        state = "WATCHING"

    # ── Module M conflict check ─────────────────────────────────────────────────
    m_count = _check_module_m_active(ticker, sig_date, cur)
    if m_count is not None:
        # Explicit conflict: both L and M active on same ticker same day
        _log_conflict(ticker, sig_date, state, m_count, "M",
                      f"M active ({m_count}/8 signals); L suppressed per spec", cur, conn)
        return None  # M wins; L does not fire

    return {
        "ticker":                       ticker,
        "signal_date":                  sig_date,
        "state":                        state,
        "higher_low_intact":            True,
        "support_level_type":           support_type,
        "distance_to_support_pct":      dist_support,
        "rsi_reset_level":              rsi14,
        "volume_pattern":               vol_pattern,
        "relative_strength_vs_spy_status": rs_status,
        "rs_vs_spy_pp":                 rs_pp,
        "conviction_score":             score,
        "earnings_excl":                mf["earnings_excl"],
        "falling_knife":                mf["falling_knife"],
        "days_to_earnings":             mf["days_to_earnings"],
        "routed_to_m":                  False,
    }

# ── DB persistence ─────────────────────────────────────────────────────────────

def _save(sig: dict, tg_sent: bool = False) -> None:
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_pullback_signals
                    (ticker, signal_date, state, higher_low_intact, support_level_type,
                     distance_to_support_pct, rsi_reset_level, volume_pattern,
                     relative_strength_vs_spy_status, rs_vs_spy_pp,
                     conviction_score, earnings_excl, falling_knife, days_to_earnings,
                     routed_to_m, tg_sent, tg_sent_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, signal_date) DO UPDATE SET
                    state            = EXCLUDED.state,
                    conviction_score = EXCLUDED.conviction_score,
                    tg_sent          = aiem_pullback_signals.tg_sent OR EXCLUDED.tg_sent,
                    tg_sent_at       = COALESCE(aiem_pullback_signals.tg_sent_at, EXCLUDED.tg_sent_at)
            """, (
                sig["ticker"], sig["signal_date"], sig["state"],
                sig["higher_low_intact"], sig["support_level_type"],
                sig["distance_to_support_pct"], sig["rsi_reset_level"],
                sig["volume_pattern"], sig["relative_strength_vs_spy_status"],
                sig["rs_vs_spy_pp"], sig["conviction_score"],
                sig["earnings_excl"], sig["falling_knife"], sig["days_to_earnings"],
                sig["routed_to_m"], tg_sent,
                datetime.utcnow() if tg_sent else None,
            ))
            conn.commit()
    except Exception as e:
        print(f"[pullback_reentry] save error {sig.get('ticker')}: {e}")

# ── Telegram ───────────────────────────────────────────────────────────────────

def _tg(text: str) -> None:
    import urllib.request as _u
    token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = _u.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                         data=payload, headers={"Content-Type": "application/json"})
        with _u.urlopen(req, timeout=8):
            pass
    except Exception as e:
        print(f"[pullback_reentry] telegram error: {e}")

def _format_alert(sig: dict) -> str:
    lines = [
        f"📉➡️📈 PULLBACK RE-ENTRY (Module L) — {sig['ticker']}",
        f"State: {sig['state']}",
        f"Higher-low intact: {sig['higher_low_intact']}",
        f"Support: {sig['support_level_type']} | Distance: "
        f"{sig['distance_to_support_pct']:.2f}%" if sig.get('distance_to_support_pct') else "Support: N/A",
        f"RSI(14): {sig['rsi_reset_level']} (threshold={RSI_WATCHING_THRESHOLD})",
        f"Volume pattern: {sig['volume_pattern']} (EXPANDING=warning, not block)",
        f"RS vs SPY: {sig['relative_strength_vs_spy_status']} ({sig.get('rs_vs_spy_pp', 'N/A')}pp)",
        f"Conviction: {sig['conviction_score']}/10",
    ]
    return "\n".join(lines)

# ── Live scan ──────────────────────────────────────────────────────────────────

_SCAN_LOCK = threading.Lock()

def run_scan() -> dict:
    """
    Scan all tickers in polygon_market_daily for Module L signals.
    Uses last 320 days of OHLCV + SPY data for RS computation.
    """
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"status": "locked"}
    try:
        t0 = time.time()
        print("[pullback_reentry] live scan starting…")
        with psycopg2.connect(_DB_URL, options="-c statement_timeout=90000") as conn, \
             conn.cursor() as cur:

            # Fetch all OHLCV data
            cur.execute("""
                SELECT ticker, scan_date, close_price, high_price, low_price, volume
                FROM polygon_market_daily
                WHERE scan_date >= CURRENT_DATE - 320
                  AND close_price > 1.0 AND volume > 50000
                ORDER BY ticker, scan_date
            """)
            rows = cur.fetchall()

            # Build per-ticker maps
            from collections import defaultdict
            ticker_map = defaultdict(list)
            for r in rows:
                ticker_map[r[0]].append(r)

            # SPY closes for RS computation
            spy_data = ticker_map.get("SPY", [])
            spy_closes = [float(r[2]) for r in spy_data]

            fired = 0; routed = 0; skipped = 0
            for ticker, data in ticker_map.items():
                if ticker == "SPY":
                    continue
                if len(data) < 210:
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
                            _tg(_format_alert(sig))
                            _save(sig, tg_sent=True)
                        fired += 1
                    else:
                        skipped += 1
                except Exception as e:
                    print(f"[pullback_reentry] {ticker} error: {e}")

            elapsed = round(time.time() - t0, 1)
            print(f"[pullback_reentry] scan done: fired={fired} skipped={skipped} "
                  f"elapsed={elapsed}s")
            return {"status": "ok", "fired": fired, "elapsed": elapsed}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        _SCAN_LOCK.release()

# ── Historical backtest (2026 YTD, SOXX-style names) ─────────────────────────

_SOXX_UNIVERSE = ["SOXX","NVDA","AMD","AVGO","MU","QCOM","AMAT","LRCX","KLAC",
                  "INTC","TSM","MRVL","SMCI","ON","TXN"]

def run_historical_backtest(force: bool = False) -> dict:
    """
    Backtest Module L on 2026 YTD SOXX-universe data.
    Reports:
      - WR separately for CONFIRMED vs WATCHING
      - False-positive rate: how often L fired but higher-low later broke down
    RSI threshold calibration: sweeps RSI thresholds 35/40/45/50/55 to find optimal.
    """
    if not _DB_URL:
        return {"error": "no DB_URL"}
    try:
        with psycopg2.connect(_DB_URL, options="-c statement_timeout=120000") as conn, \
             conn.cursor() as cur:

            if not force:
                cur.execute("SELECT COUNT(*) FROM aiem_pullback_backtest_log")
                if cur.fetchone()[0] > 0:
                    return _backtest_summary(cur)

            from datetime import date as _date
            _YTD_START = _date(2026, 1, 1)

            # SOXX-universe + any ticker with >30% gain in polygon_market_daily 2025
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
                UNION ALL
                SELECT UNNEST(%s::text[])
            """, (_SOXX_UNIVERSE,))
            universe = list({r[0] for r in cur.fetchall()})
            print(f"[pullback_bt] universe: {len(universe)} tickers")

            # Fetch full history from 2024-10-01 so 200-bar uptrend check has enough data
            cur.execute("""
                SELECT ticker, scan_date, close_price, high_price, low_price, volume
                FROM polygon_market_daily
                WHERE ticker = ANY(%s)
                  AND scan_date >= '2024-10-01'
                ORDER BY ticker, scan_date
            """, (universe,))
            raw = cur.fetchall()
            from collections import defaultdict
            tmap = defaultdict(list)
            for r in raw: tmap[r[0]].append(r)

            # SPY for RS — also from 2024-10-01
            cur.execute("""
                SELECT scan_date, close_price FROM polygon_market_daily
                WHERE ticker='SPY' AND scan_date >= '2024-10-01'
                ORDER BY scan_date
            """)
            spy_dict = {r[0]: float(r[1]) for r in cur.fetchall()}

            # Forward price map (all data through most recent)
            all_tickers = list(tmap.keys())
            cur.execute("""
                SELECT ticker, scan_date, close_price, low_price
                FROM polygon_market_daily
                WHERE ticker = ANY(%s) AND scan_date >= '2024-10-01'
                ORDER BY ticker, scan_date
            """, (all_tickers,))
            fwd_map = defaultdict(list)
            for r in cur.fetchall():
                fwd_map[r[0]].append((r[1], float(r[2] or 0), float(r[3] or 0)))

            inserted = 0
            # RSI threshold calibration sweep
            rsi_thresholds = [35, 40, 45, 50, 55]
            calibration = {t: {"n": 0, "wins": 0} for t in rsi_thresholds}

            for ticker, data in tmap.items():
                closes  = [float(r[2]) for r in data]
                highs   = [float(r[3]) for r in data]
                lows    = [float(r[4]) for r in data]
                volumes = [float(r[5]) for r in data]
                dates   = [r[1] for r in data]
                n = len(closes)
                if n < 210:
                    continue

                # SPY aligned using dict lookup (O(1) per date, not O(n^2))
                spy_aligned = [spy_dict.get(d, None) for d in dates]
                # Forward-fill None gaps
                last_spy = 100.0
                for k in range(len(spy_aligned)):
                    if spy_aligned[k] is not None:
                        last_spy = spy_aligned[k]
                    else:
                        spy_aligned[k] = last_spy

                # Start loop at 210 (enough for 200-bar uptrend check)
                # but only insert rows for signal_date >= 2026-01-01
                for i in range(210, n - 10):
                    sig_date = dates[i]
                    c_slice  = closes[:i+1]
                    h_slice  = highs[:i+1]
                    l_slice  = lows[:i+1]
                    v_slice  = volumes[:i+1]
                    sp_slice = spy_aligned[:i+1]

                    if not _uptrend_intact(c_slice):
                        continue
                    hl_intact, cur_low, prior_low = higher_low_check(l_slice)
                    rsi14 = _rsi(c_slice[-40:], 14) if len(c_slice) >= 16 else None
                    if rsi14 is None:
                        continue

                    # RSI calibration: track WR for each threshold
                    fwd_prices = fwd_map.get(ticker, [])
                    fwd_after  = [(d2, c2, lo2) for d2, c2, lo2 in fwd_prices if d2 > sig_date]
                    if len(fwd_after) < 5:
                        continue
                    fwd_5d = (fwd_after[4][1] - closes[i]) / closes[i] * 100 if closes[i] > 0 else None
                    for thresh in rsi_thresholds:
                        if rsi14 <= thresh and hl_intact:
                            calibration[thresh]["n"] += 1
                            if fwd_5d is not None and fwd_5d > 0:
                                calibration[thresh]["wins"] += 1

                    if not hl_intact or rsi14 > RSI_WATCHING_THRESHOLD:
                        continue

                    vol_pat = _volume_pattern(c_slice, v_slice)
                    rs_status, rs_pp = _rs_vs_spy(c_slice, sp_slice)
                    support_type, dist_sup = _support_zone(c_slice, h_slice, l_slice)
                    state = ("CONFIRMED" if rsi14 <= RSI_CONFIRMED_THRESHOLD
                             and len(c_slice) >= 2 and c_slice[-1] > c_slice[-2]
                             else "WATCHING")

                    # Score
                    score = 3
                    if rsi14 <= 40: score += 2
                    elif rsi14 <= RSI_CONFIRMED_THRESHOLD: score += 1
                    if support_type == "EMA21": score += 2
                    elif support_type in ("SMA50", "PRIOR_BREAKOUT"): score += 1
                    if vol_pat == "LIGHT": score += 2
                    elif vol_pat == "NEUTRAL": score += 1
                    elif vol_pat == "EXPANDING": score -= 1
                    if rs_status == "INTACT": score += 1
                    elif rs_status == "WEAKENING": score -= 1
                    score = max(0, min(10, score))
                    if state == "CONFIRMED" and score < 5:
                        state = "WATCHING"

                    # Forward returns
                    fwd_1d = (fwd_after[0][1] - closes[i]) / closes[i] * 100 if closes[i] > 0 else None
                    fwd_3d = (fwd_after[2][1] - closes[i]) / closes[i] * 100 if len(fwd_after) >= 3 and closes[i] > 0 else None
                    fwd_5d = (fwd_after[4][1] - closes[i]) / closes[i] * 100 if len(fwd_after) >= 5 and closes[i] > 0 else None
                    fwd_10 = (fwd_after[9][1] - closes[i]) / closes[i] * 100 if len(fwd_after) >= 10 and closes[i] > 0 else None

                    # Did higher-low later break? (false-positive check)
                    hl_later_broke = False
                    for fi in range(min(10, len(fwd_after))):
                        sub_lows = l_slice + [fwd_after[j][2] for j in range(fi+1)]
                        hl_fi, _, _ = higher_low_check(sub_lows)
                        if not hl_fi:
                            hl_later_broke = True
                            break

                    # SPY regime
                    spy_20d = None
                    if len(sp_slice) >= 21:
                        spy_20d = (sp_slice[-1] - sp_slice[-21]) / sp_slice[-21] * 100 if sp_slice[-21] > 0 else None
                    regime = ("TREND_UP" if spy_20d and spy_20d > 2
                              else "TREND_DOWN" if spy_20d and spy_20d < -2
                              else "CHOPPY")

                    # Only record rows for 2026 YTD (pre-2026 rows are warm-up data)
                    if sig_date < _YTD_START:
                        continue

                    try:
                        cur.execute("""
                            INSERT INTO aiem_pullback_backtest_log
                                (ticker, signal_date, state, rsi_reset_level, volume_pattern,
                                 higher_low_intact, support_level_type, conviction_score,
                                 market_regime, fwd_1d_pct, fwd_3d_pct, fwd_5d_pct, fwd_10d_pct,
                                 higher_low_later_broke)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (ticker, signal_date) DO NOTHING
                        """, (ticker, sig_date, state, round(rsi14, 2), vol_pat,
                              hl_intact, support_type, score, regime,
                              fwd_1d and round(fwd_1d, 4), fwd_3d and round(fwd_3d, 4),
                              fwd_5d and round(fwd_5d, 4), fwd_10 and round(fwd_10, 4),
                              hl_later_broke))
                        inserted += 1
                    except Exception:
                        pass

            conn.commit()
            print(f"[pullback_bt] inserted {inserted} rows")

            # Calibration output
            print("\n[pullback_bt] RSI THRESHOLD CALIBRATION:")
            for thresh in rsi_thresholds:
                cd = calibration[thresh]
                wr = cd["wins"] / cd["n"] if cd["n"] else 0
                print(f"  RSI≤{thresh}: n={cd['n']} WR={wr:.1%}")
            print(f"[pullback_bt] Selected threshold: WATCHING≤{RSI_WATCHING_THRESHOLD} "
                  f"CONFIRMED≤{RSI_CONFIRMED_THRESHOLD}")

            return _backtest_summary(cur)
    except Exception as e:
        return {"error": str(e)}

def _backtest_summary(cur) -> dict:
    cur.execute("""
        SELECT
            state,
            COUNT(*) as n,
            AVG(CASE WHEN fwd_5d_pct > 0 THEN 1.0 ELSE 0.0 END) as wr_5d,
            AVG(fwd_5d_pct) as avg_5d,
            AVG(CASE WHEN higher_low_later_broke THEN 1.0 ELSE 0.0 END) as fp_rate
        FROM aiem_pullback_backtest_log
        WHERE fwd_5d_pct IS NOT NULL
        GROUP BY state
        ORDER BY state
    """)
    rows = cur.fetchall()
    result = {}
    for r in rows:
        result[r[0]] = {
            "n": r[1], "wr_5d": round(float(r[2] or 0), 4),
            "avg_5d_pct": round(float(r[3] or 0), 4),
            "false_positive_rate": round(float(r[4] or 0), 4),
        }
    return {"backtest": result, "note": (
        "false_positive_rate = fraction of firings where higher-low later broke; "
        f"RSI thresholds: WATCHING<={RSI_WATCHING_THRESHOLD} CONFIRMED<={RSI_CONFIRMED_THRESHOLD}"
    )}

# ── BH-FDR registration ────────────────────────────────────────────────────────

def register_signal() -> None:
    conditions = {
        "uptrend": "SMA50_rising + close>SMA200 over 60-90d lookback",
        "higher_low": "current_swing_low > prior_swing_low (shared calc with Module M)",
        "support": "within 3% of EMA21|SMA50|PRIOR_BREAKOUT (priority order)",
        "rsi_reset": f"RSI(14) <= {RSI_WATCHING_THRESHOLD} (calibrated; NOT generic 30)",
        "volume": "LIGHT/NEUTRAL preferred; EXPANDING=warning flag, not block",
        "rs_spy": "INTACT or WEAKENING; BROKEN routes to Module M",
        "_structural": "WATCHING->CONFIRMED state machine",
    }
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) as n,
                       AVG(CASE WHEN fwd_5d_pct > 0 THEN 1.0 ELSE 0.0 END) as wr
                FROM aiem_pullback_backtest_log
                WHERE state='CONFIRMED' AND fwd_5d_pct IS NOT NULL
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
                      f"Module_L; RSI_thresh={RSI_WATCHING_THRESHOLD}; "
                      f"bt_n={bt_n}; wr={bt_wr}; p={p_val}", existing[0]))
            else:
                cur.execute("""
                    INSERT INTO aiem_signal_discoveries
                        (hypothesis_text, conditions_json, status, horizon,
                         invented_indicator, signal_n, signal_win_rate, p_value,
                         notes, discovered_at)
                    VALUES (%s,%s::jsonb,'hypothesis',%s,%s,%s,%s,%s,%s,NOW())
                """, (_SIGNAL_NAME, json.dumps(conditions), _HORIZON, _INVENTED_INDICATOR,
                      bt_n or None, bt_wr, p_val,
                      f"Module_L pullback reentry; RSI_thresh={RSI_WATCHING_THRESHOLD}; "
                      f"bt_n={bt_n}; wr={bt_wr}; p={p_val}"))
            conn.commit()
        print(f"[pullback_reentry] registered {_SIGNAL_NAME}: n={bt_n} wr={bt_wr} p={p_val}")
    except Exception as e:
        print(f"[pullback_reentry] register_signal error: {e}")
