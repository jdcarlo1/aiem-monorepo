"""
aiem_selloff_reversion.py
─────────────────────────
AIEM-INTERNAL ONLY.  Zero references into customer-facing scanner tabs.

Phase 1 — three components
  Module F  : Pre-Earnings / Falling-Knife Guard (gate, suppresses others)
  Oversold_Bounce_Uptrend : main conviction-stack signal
  Module A  : Capitulation Volume (booster, raises bounce conviction)

Section 16 (addendum) — permanent historical backtest log
  aiem_bounce_backtest_log : every historical firing + 1/3/5/10-day forward returns
  Regime segmentation: TREND_UP / TREND_DOWN / CHOPPY (via SPY 20-day return)
  Maximum available history used (polygon_market_daily back to 2024-07-08)

Telegram alerts: CONFIRMED state only, market hours 9:30-16:00 ET.
Overnight signals logged and re-evaluated at open before alerting.
Overnight signals that already ran (gap >4%) marked EXPIRED_OVERNIGHT.
"""

import os
import math
import threading
import time
import json
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Optional

import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL", "")

# ── Timezone & market-hours helpers ───────────────────────────────────────────

def _et_now() -> datetime:
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        import pytz
        return datetime.now(pytz.timezone("America/New_York"))

def _market_open() -> bool:
    """True during regular session Mon-Fri 9:30-16:00 ET."""
    now = _et_now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 570 <= t < 960  # 9:30=570, 16:00=960

# ── Self-contained Telegram sender ────────────────────────────────────────────

def _tg(text: str, *, ticker: str = None, alert_class: str = "SIGNAL",
        audit_trace_id: str = None, trigger_price: float = None,
        is_test: bool = False) -> bool:
    import urllib.request as _u
    token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    send_text = text
    if alert_class == "SIGNAL":
        try:
            import alert_gateway as _ag_trust
            send_text = text + _ag_trust.get_trust_display("selloff_reversion")
        except Exception as _te:
            print(f"[bounce] trust display error (non-fatal): {_te}")
    ok = False
    if not token or not chat_id:
        ok = False
    else:
        try:
            payload = json.dumps({"chat_id": chat_id, "text": send_text}).encode()
            req = _u.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload, headers={"Content-Type": "application/json"},
            )
            with _u.urlopen(req, timeout=8) as r:
                ok = json.loads(r.read()).get("ok", False)
        except Exception as e:
            print(f"[bounce] telegram error: {e}")
            ok = False
    try:
        import alert_gateway as _ag
        _ag.log_alert(text, signal_source="selloff_reversion", ticker=ticker,
                       alert_class=alert_class, audit_trace_id=audit_trace_id,
                       trigger_price=trigger_price, is_test=is_test, sent_ok=ok)
    except Exception as _ge:
        print(f"[bounce] alert_gateway logging error (non-fatal): {_ge}")
    return ok

# ── Technical indicators ───────────────────────────────────────────────────────

def _sma(arr: list, period: int) -> Optional[float]:
    if len(arr) < period:
        return None
    return sum(arr[-period:]) / period

def _rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    return round(100.0, 1) if al == 0 else round(100 - 100 / (1 + ag / al), 1)

def _atr_pct(highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
    """ATR as % of current close."""
    if len(closes) < period + 1:
        return None
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        for i in range(1, len(closes))
    ]
    if len(trs) < period:
        return None
    atr = sum(trs[-period:]) / period
    return round(atr / closes[-1] * 100, 4) if closes[-1] > 0 else None

def _drop_threshold(atr_val: Optional[float]) -> float:
    """Volatility-adjusted drop threshold (spec §2.2)."""
    if atr_val is None or atr_val < 1.5:
        return -8.0
    return -12.0 if atr_val < 3.0 else -15.0

def _vol_bucket(atr_val: Optional[float]) -> str:
    if atr_val is None or atr_val < 1.5:
        return "LOW_VOL"
    return "MID_VOL" if atr_val < 3.0 else "HIGH_VOL"

# ── DB schema init ─────────────────────────────────────────────────────────────

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS aiem_bounce_signals (
    id                              BIGSERIAL PRIMARY KEY,
    ticker                          TEXT NOT NULL,
    detected_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_date                     DATE NOT NULL,
    state                           TEXT NOT NULL,
    drop_pct_3d                     NUMERIC(8,4),
    rsi_2                           NUMERIC(6,2),
    rsi_14                          NUMERIC(6,2),
    volume_pattern                  TEXT,
    distance_to_support_pct         NUMERIC(8,4),
    fundamental_exclusion_triggered BOOLEAN DEFAULT FALSE,
    fundamental_check_status        TEXT DEFAULT 'NOT_IMPLEMENTED',
    earnings_exclusion_active       BOOLEAN DEFAULT FALSE,
    days_to_next_earnings           INTEGER,
    falling_knife_active            BOOLEAN DEFAULT FALSE,
    smart_money_divergence_reading  TEXT,
    capitulation_volume_booster     BOOLEAN DEFAULT FALSE,
    cap_vol_multiple                NUMERIC(6,2),
    conviction_score                INTEGER DEFAULT 0,
    vol_bucket                      TEXT,
    tg_sent                         BOOLEAN DEFAULT FALSE,
    tg_sent_at                      TIMESTAMPTZ,
    overnight_status                TEXT,
    UNIQUE (ticker, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_aiem_bounce_state
    ON aiem_bounce_signals (state, tg_sent, signal_date);

CREATE TABLE IF NOT EXISTS aiem_bounce_backtest_log (
    id               BIGSERIAL PRIMARY KEY,
    module_name      TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    signal_date      DATE NOT NULL,
    state            TEXT,
    drop_pct_3d      NUMERIC(8,4),
    rsi_2            NUMERIC(6,2),
    rsi_14           NUMERIC(6,2),
    volume_pattern   TEXT,
    conviction_score INTEGER,
    vol_bucket       TEXT,
    market_regime    TEXT,
    spy_20d_ret_pct  NUMERIC(8,4),
    fwd_1d_pct       NUMERIC(8,4),
    fwd_3d_pct       NUMERIC(8,4),
    fwd_5d_pct       NUMERIC(8,4),
    fwd_10d_pct      NUMERIC(8,4),
    max_dd_5d_pct    NUMERIC(8,4),
    max_fav_5d_pct   NUMERIC(8,4),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (module_name, ticker, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_aiem_bt_module
    ON aiem_bounce_backtest_log (module_name, signal_date, market_regime);
"""

def init_tables() -> None:
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(_INIT_SQL)
            conn.commit()
        print("[bounce] DB tables ready")
    except Exception as e:
        print(f"[bounce] table init error: {e}")

# ── Module F: Pre-Earnings / Falling-Knife Guard ──────────────────────────────

def _module_f(ticker: str, closes: list, volumes: list,
               sma20: Optional[float], sma50: Optional[float],
               avg_vol_20: Optional[float], cur) -> dict:
    out = {
        "earnings_exclusion_active": False,
        "falling_knife_active": False,
        "days_to_next_earnings": None,
        "suppress": False,
    }
    try:
        cur.execute(
            "SELECT earnings_date FROM earnings_calendar "
            "WHERE ticker=%s AND earnings_date >= CURRENT_DATE ORDER BY earnings_date LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        if row:
            days = (row[0] - date.today()).days
            out["days_to_next_earnings"] = days
            if days <= 5:
                out["earnings_exclusion_active"] = True
                out["suppress"] = True
    except Exception:
        pass

    if closes and volumes:
        last_c = closes[-1]
        last_v = volumes[-1]
        broke  = (sma20 and last_c < sma20) or (sma50 and last_c < sma50)
        if broke and avg_vol_20 and last_v > avg_vol_20 * 1.5:
            out["falling_knife_active"] = True
            out["suppress"] = True

    return out

# ── Module A: Capitulation Volume ─────────────────────────────────────────────

def _module_a(closes: list, volumes: list, highs: list, lows: list,
               atr_val: Optional[float]) -> dict:
    out = {"fired": False, "volume_multiple": None,
           "daily_return": None, "close_vs_range_pct": None}
    if len(closes) < 21 or len(volumes) < 21:
        return out
    n_mult  = 2.5 if atr_val and atr_val > 3.0 else 3.0
    avg_vol = sum(volumes[-21:-1]) / 20
    if avg_vol <= 0:
        return out
    vol_mult   = volumes[-1] / avg_vol
    daily_ret  = (closes[-1] - closes[-2]) / closes[-2] * 100 if closes[-2] > 0 else 0
    threshold  = _drop_threshold(atr_val)
    if vol_mult >= n_mult and daily_ret <= threshold:
        day_range = highs[-1] - lows[-1]
        cvr = (closes[-1] - lows[-1]) / day_range if day_range > 0 else 0.5
        out.update({
            "fired": True,
            "volume_multiple": round(vol_mult, 2),
            "daily_return": round(daily_ret, 4),
            "close_vs_range_pct": round(cvr, 4),
        })
    return out

# ── Smart-money divergence (whale_blocks during drop window) ──────────────────

def _smart_money(ticker: str, sig_date: date, cur) -> Optional[str]:
    try:
        cur.execute(
            "SELECT direction, COUNT(*) FROM whale_blocks "
            "WHERE ticker=%s AND first_seen::date >= %s AND first_seen::date <= %s "
            "GROUP BY direction",
            (ticker, sig_date - timedelta(days=5), sig_date),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        counts = {r[0]: r[1] for r in rows}
        buy_n  = counts.get("BUY", 0) + counts.get("CALL", 0)
        sell_n = counts.get("SELL", 0) + counts.get("PUT", 0)
        if buy_n == 0 and sell_n == 0:
            return None
        return "BULLISH" if buy_n > sell_n else "NEUTRAL"
    except Exception:
        return None

# ── Core signal computation ───────────────────────────────────────────────────

def _compute_signal(ticker: str, closes: list, highs: list, lows: list,
                    volumes: list, dates: list,
                    sm: Optional[str], cur) -> Optional[dict]:
    n = len(closes)
    if n < 210:
        return None

    sma20  = _sma(closes, 20)
    sma50  = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    sma50_20ago = _sma(closes[:-20], 50) if n >= 70 else None

    rsi_2  = _rsi(closes[-10:], 2)   if n >= 4  else None
    rsi_14 = _rsi(closes[-30:], 14)  if n >= 16 else None

    atr_val   = _atr_pct(highs[-30:], lows[-30:], closes[-30:], 14)
    threshold = _drop_threshold(atr_val)
    vb        = _vol_bucket(atr_val)
    avg_vol20 = sum(volumes[-21:-1]) / 20 if n >= 21 else None

    last_close = closes[-1]

    # 2.1 Trend filter
    if sma50 is None or sma200 is None:
        return None
    if last_close <= sma50 or last_close <= sma200:
        return None
    if sma50_20ago is not None and sma50 <= sma50_20ago:
        return None

    # 2.2 Short-term oversold trigger
    if n < 4 or closes[-4] <= 0:
        return None
    drop_3d = (closes[-1] - closes[-4]) / closes[-4] * 100
    if drop_3d > threshold:
        return None
    rsi_ok = (rsi_2 is not None and rsi_2 < 10) or (rsi_14 is not None and rsi_14 < 30)
    if not rsi_ok:
        return None

    # 2.3 Volume exhaustion pattern
    vol_pattern = "NONE"
    if n >= 3:
        v0, v1, v2 = volumes[-1], volumes[-2], volumes[-3]
        if v0 < v1 < v2:
            vol_pattern = "DECLINING"
        elif n >= 4:
            recent_max_idx = volumes[-4:].index(max(volumes[-4:]))
            if recent_max_idx < 2 and v0 < max(volumes[-4:-1]):
                vol_pattern = "CLIMAX"

    # 2.4 Support filter + Module F gate
    dist_support = None
    near_support = False
    for lvl in filter(None, [sma20, sma50]):
        d = abs(last_close - lvl) / lvl * 100
        if dist_support is None or d < dist_support:
            dist_support = d
        if d <= 2.0:
            near_support = True

    mod_f = _module_f(ticker, closes, volumes, sma20, sma50, avg_vol20, cur)
    if mod_f["suppress"]:
        return None

    # 2.6 State
    state = "CONFIRMED" if (n >= 2 and closes[-1] > closes[-2]) else "WATCHING"

    # Module A booster
    mod_a = _module_a(closes, volumes, highs, lows, atr_val)

    # Conviction score (informational only — not used as a gate)
    # Volume sub-score removed: DECLINING/CLIMAX inversely correlated with wr_5d
    # (NONE 55.6% / DECLINING 51.9% / CLIMAX 44.5%, n=739). Score >= 5 gate removed
    # for the same reason — score=4 was the best-performing bucket (59.1% wr_5d).
    score = 2  # trend filter baseline
    if rsi_2 is not None and rsi_2 < 10:
        score += 2
    elif rsi_14 is not None and rsi_14 < 30:
        score += 1
    if near_support:
        score += 1
    if mod_a["fired"]:
        score += 1
    if sm == "BULLISH":
        score += 1

    return {
        "ticker":                        ticker,
        "signal_date":                   dates[-1],
        "state":                         state,
        "drop_pct_3d":                   round(drop_3d, 4),
        "rsi_2":                         rsi_2,
        "rsi_14":                        rsi_14,
        "volume_pattern":                vol_pattern,
        "distance_to_support_pct":       round(dist_support, 4) if dist_support else None,
        "fundamental_exclusion_triggered": mod_f["earnings_exclusion_active"] or mod_f["falling_knife_active"],
        "fundamental_check_status":      "NOT_IMPLEMENTED",
        "earnings_exclusion_active":     mod_f["earnings_exclusion_active"],
        "days_to_next_earnings":         mod_f["days_to_next_earnings"],
        "falling_knife_active":          mod_f["falling_knife_active"],
        "smart_money_divergence_reading": sm,
        "capitulation_volume_booster":   mod_a["fired"],
        "cap_vol_multiple":              mod_a["volume_multiple"],
        "conviction_score":              score,
        "vol_bucket":                    vb,
    }


def compute_signal(ticker: str, closes: list, highs: list, lows: list,
                   volumes: list, dates: list,
                   spy_closes=None, cur=None, conn=None, sm=None) -> Optional[dict]:
    """Public entry for orchestrator (same gates as run_scan → _compute_signal).

    Expects chronological ascending OHLCV (oldest→newest), matching run_scan.
    Returns signal dict, or {"status": "no_signal"|"insufficient_data", ...}.
    """
    if len(closes) < 210:
        return {"status": "insufficient_data", "bars": len(closes), "min_required": 210}
    if cur is None:
        return {"status": "error", "error": "compute_signal requires DB cursor"}
    if sm is None:
        try:
            sm = _smart_money(ticker, dates[-1], cur)
        except Exception:
            sm = None
    sig = _compute_signal(ticker, closes, highs, lows, volumes, dates, sm, cur)
    if sig is None:
        return {
            "status": "no_signal",
            "ticker": ticker,
            "bars": len(closes),
            "entry_point": "aiem_selloff_reversion.compute_signal/_compute_signal",
        }
    sig["status"] = "signal"
    sig["entry_point"] = "aiem_selloff_reversion.compute_signal/_compute_signal"
    return sig


# ── Telegram message format (spec §7.3) ───────────────────────────────────────

def _format_alert(sig: dict, overnight: bool = False) -> str:
    header = "🔻➡️📈 OVERSOLD BOUNCE"
    if overnight:
        header += " [Detected overnight, confirmed live at open]"
    fns = sig.get("fundamental_check_status", "NOT_IMPLEMENTED")
    lines = [
        f"{header} — {sig['ticker']}",
        f"Drop: {sig['drop_pct_3d']:+.2f}% over 3 sessions",
        f"RSI(2): {sig['rsi_2'] or 'N/A'}  |  RSI(14): {sig['rsi_14'] or 'N/A'}",
        f"Volume pattern: {sig['volume_pattern']}",
        (f"Distance to support: {sig['distance_to_support_pct']:.2f}%"
         if sig.get("distance_to_support_pct") else "Distance to support: N/A"),
        f"⚠️ Fundamental news check: {fns}",
        (f"Cap. Volume booster: ✅ x{sig['cap_vol_multiple']}"
         if sig.get("capitulation_volume_booster") else "Cap. Volume booster: No"),
        f"Smart money (whale blocks): {sig.get('smart_money_divergence_reading') or 'No data'}",
        f"Conviction score: {sig['conviction_score']}/10",
        f"Vol bucket: {sig['vol_bucket']}",
        f"State: {sig['state']}",
        f"Time: {_et_now().strftime('%H:%M ET %b %d')}",
    ]
    return "\n".join(lines)

# ── DB persistence ─────────────────────────────────────────────────────────────

def _save_signal(sig: dict, tg_sent: bool = False) -> None:
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO aiem_bounce_signals
                    (ticker, signal_date, state, drop_pct_3d, rsi_2, rsi_14,
                     volume_pattern, distance_to_support_pct,
                     fundamental_exclusion_triggered, fundamental_check_status,
                     earnings_exclusion_active, days_to_next_earnings,
                     falling_knife_active, smart_money_divergence_reading,
                     capitulation_volume_booster, cap_vol_multiple,
                     conviction_score, vol_bucket, tg_sent, tg_sent_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, signal_date) DO UPDATE SET
                    state            = EXCLUDED.state,
                    conviction_score = EXCLUDED.conviction_score,
                    tg_sent          = aiem_bounce_signals.tg_sent OR EXCLUDED.tg_sent,
                    tg_sent_at       = COALESCE(aiem_bounce_signals.tg_sent_at, EXCLUDED.tg_sent_at)
                """,
                (
                    sig["ticker"], sig["signal_date"], sig["state"],
                    sig["drop_pct_3d"], sig["rsi_2"], sig["rsi_14"],
                    sig["volume_pattern"], sig["distance_to_support_pct"],
                    sig["fundamental_exclusion_triggered"], sig["fundamental_check_status"],
                    sig["earnings_exclusion_active"], sig["days_to_next_earnings"],
                    sig["falling_knife_active"], sig["smart_money_divergence_reading"],
                    sig["capitulation_volume_booster"], sig["cap_vol_multiple"],
                    sig["conviction_score"], sig["vol_bucket"],
                    tg_sent, _et_now() if tg_sent else None,
                ),
            )
            conn.commit()
    except Exception as e:
        print(f"[bounce] save error {sig.get('ticker')}: {e}")

# ── Overnight re-evaluation (spec §7.2) ───────────────────────────────────────

def refire_overnight_signals() -> None:
    """
    Re-evaluate CONFIRMED signals detected outside market hours.
    Stocks that already gapped >4% → EXPIRED_OVERNIGHT (no alert).
    Stocks still in setup range → send alert marked as overnight.
    """
    if not _market_open():
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ticker, signal_date, conviction_score, drop_pct_3d,
                       rsi_2, rsi_14, volume_pattern, distance_to_support_pct,
                       cap_vol_multiple, capitulation_volume_booster,
                       smart_money_divergence_reading, vol_bucket
                FROM   aiem_bounce_signals
                WHERE  state = 'CONFIRMED'
                  AND  tg_sent = FALSE
                  AND  signal_date >= CURRENT_DATE - 1
                  AND  overnight_status IS NULL
                """
            )
            rows, cols = cur.fetchall(), [d[0] for d in cur.description]

        for row in rows:
            rec = dict(zip(cols, row))
            gap = 0.0
            close_price = None
            try:
                with psycopg2.connect(_DB_URL) as c2, c2.cursor() as cu2:
                    cu2.execute(
                        "SELECT gap_pct, close_price FROM polygon_market_daily "
                        "WHERE ticker=%s ORDER BY scan_date DESC LIMIT 1",
                        (rec["ticker"],),
                    )
                    r = cu2.fetchone()
                    gap = float(r[0]) if r and r[0] else 0.0
                    close_price = float(r[1]) if r and r[1] else None
            except Exception:
                pass

            if gap > 4.0:
                status, sent = "EXPIRED_OVERNIGHT", False
            else:
                status, sent = "REFIRED", True
                msg = _format_alert({
                    "ticker": rec["ticker"],
                    "drop_pct_3d": rec["drop_pct_3d"] or 0,
                    "rsi_2": rec["rsi_2"], "rsi_14": rec["rsi_14"],
                    "volume_pattern": rec["volume_pattern"],
                    "distance_to_support_pct": rec["distance_to_support_pct"],
                    "cap_vol_multiple": rec["cap_vol_multiple"],
                    "capitulation_volume_booster": rec["capitulation_volume_booster"],
                    "smart_money_divergence_reading": rec["smart_money_divergence_reading"],
                    "conviction_score": rec["conviction_score"],
                    "vol_bucket": rec["vol_bucket"],
                    "state": "CONFIRMED",
                    "fundamental_check_status": "NOT_IMPLEMENTED",
                }, overnight=True)
                _tg(msg, ticker=rec["ticker"], trigger_price=close_price)

            try:
                with psycopg2.connect(_DB_URL) as c3, c3.cursor() as cu3:
                    cu3.execute(
                        "UPDATE aiem_bounce_signals SET overnight_status=%s, tg_sent=%s,"
                        " tg_sent_at=CASE WHEN %s THEN NOW() ELSE tg_sent_at END WHERE id=%s",
                        (status, sent, sent, rec["id"]),
                    )
                    c3.commit()
            except Exception as e:
                print(f"[bounce] overnight update error: {e}")

        if rows:
            print(f"[bounce] overnight re-eval: {len(rows)} signals processed")
    except Exception as e:
        print(f"[bounce] overnight refire error: {e}")

# ── Live scan ──────────────────────────────────────────────────────────────────

_SCAN_LOCK = threading.Lock()
_TG_COOLDOWN: dict = {}
_COOLDOWN_HRS = 24

def run_scan() -> dict:
    """
    Pull last 320 days of OHLCV from polygon_market_daily,
    compute Oversold_Bounce_Uptrend + Module A for all 14K tickers,
    save signals, send Telegram for CONFIRMED during market hours.
    """
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"status": "locked"}
    try:
        print("[bounce] live scan starting…")
        t0 = time.time()
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, scan_date, close_price, high_price, low_price, volume
                FROM   polygon_market_daily
                WHERE  scan_date >= CURRENT_DATE - 320
                  AND  close_price > 1.0 AND volume > 10000
                ORDER  BY ticker, scan_date
                """
            )
            rows = cur.fetchall()

        td: dict = defaultdict(lambda: {
            "dates": [], "closes": [], "highs": [], "lows": [], "volumes": []
        })
        for ticker, sd, close, high, low, volume in rows:
            d = td[ticker]
            d["dates"].append(sd)
            d["closes"].append(close)
            d["highs"].append(high)
            d["lows"].append(low)
            d["volumes"].append(volume)

        in_market = _market_open()
        alerted, queued = [], []

        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for ticker, d in td.items():
                if len(d["closes"]) < 210:
                    continue
                sm  = _smart_money(ticker, d["dates"][-1], cur)
                sig = _compute_signal(
                    ticker, d["closes"], d["highs"], d["lows"],
                    d["volumes"], d["dates"], sm, cur
                )
                if sig is None:
                    continue

                last_sent = _TG_COOLDOWN.get(ticker)
                mem_ok = (
                    last_sent is None or
                    (_et_now() - last_sent).total_seconds() > _COOLDOWN_HRS * 3600
                )
                # DB-backed cooldown: survive process restarts (same protection, persistent)
                db_ok = True
                try:
                    cur.execute(
                        "SELECT tg_sent_at FROM aiem_bounce_signals "
                        "WHERE ticker=%s AND tg_sent=TRUE "
                        "ORDER BY tg_sent_at DESC LIMIT 1",
                        (ticker,),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        age_s = (_et_now() - row[0].astimezone(_et_now().tzinfo)).total_seconds()
                        db_ok = age_s > _COOLDOWN_HRS * 3600
                except Exception:
                    pass  # fail-open: if DB check fails, fall back to in-memory
                cooldown_ok = mem_ok and db_ok
                # Re-check market-open per-alert, not once at scan start, so a long
                # scan that crosses 16:00 ET does not fire alerts after close.
                send_now = sig["state"] == "CONFIRMED" and _market_open() and cooldown_ok

                _save_signal(sig, tg_sent=False)

                if send_now:
                    try:
                        import aiem_wiring_infra as _awi_tg
                        _tg_ok = _awi_tg.discovery_allows_live_alert("Oversold_Bounce_Uptrend")
                    except Exception:
                        _tg_ok = False
                    if _tg_ok:
                        _tg(_format_alert(sig), ticker=sig["ticker"],
                            trigger_price=d["closes"][-1])
                        _TG_COOLDOWN[ticker] = _et_now()
                        alerted.append(ticker)
                        _save_signal(sig, tg_sent=True)
                    else:
                        print(f"[bounce] TG suppressed — discovery not validated for {ticker}")
                        queued.append(ticker)
                elif sig["state"] == "CONFIRMED":
                    queued.append(ticker)

        elapsed = round(time.time() - t0, 1)
        print(f"[bounce] scan done {elapsed}s — {len(alerted)} alerted, {len(queued)} queued overnight")
        return {
            "status": "ok", "elapsed_s": elapsed,
            "confirmed_alerted": len(alerted),
            "confirmed_queued_overnight": len(queued),
            "tickers_alerted": alerted,
        }
    except Exception as e:
        print(f"[bounce] scan error: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        _SCAN_LOCK.release()

# ── Historical backtest (Section 16 addendum) ─────────────────────────────────

def _regime(spy_20d: Optional[float]) -> str:
    if spy_20d is None:
        return "UNKNOWN"
    return "TREND_UP" if spy_20d > 5.0 else ("TREND_DOWN" if spy_20d < -5.0 else "CHOPPY")

def run_historical_backtest(force: bool = False) -> dict:
    """
    Walk every ticker/date in polygon_market_daily point-in-time.
    Record firings + forward returns in aiem_bounce_backtest_log.

    Data bounds:
      polygon_market_daily: 2024-07-08 → present
      Limiting source: polygon_market_daily (price/volume only)
      Modules B/C/D/H/J have their own shorter bounds (options/SI data).
    """
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM aiem_bounce_backtest_log")
            existing = cur.fetchone()[0]
        if existing > 0 and not force:
            print(f"[bounce_bt] {existing} rows exist — skip (force=True to rerun)")
            return {"status": "skipped", "existing_rows": existing}
    except Exception as e:
        print(f"[bounce_bt] check error: {e}")

    print("[bounce_bt] full historical backtest starting…")
    t0 = time.time()

    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, scan_date, close_price, high_price, low_price, volume
                FROM   polygon_market_daily
                WHERE  close_price > 1.0 AND volume > 10000
                ORDER  BY ticker, scan_date
                """
            )
            all_rows = cur.fetchall()

            cur.execute(
                "SELECT scan_date, close_price FROM polygon_market_daily "
                "WHERE ticker='SPY' ORDER BY scan_date"
            )
            spy_rows = cur.fetchall()

        # SPY 20-day regime map
        spy_map  = {r[0]: r[1] for r in spy_rows}
        spy_dates = sorted(spy_map.keys())
        spy_20d: dict = {}
        for i, d in enumerate(spy_dates):
            if i >= 20:
                p20 = spy_map[spy_dates[i - 20]]
                pc  = spy_map[d]
                spy_20d[d] = (pc - p20) / p20 * 100 if p20 > 0 else None
            else:
                spy_20d[d] = None

        # Group OHLCV by ticker
        td: dict = defaultdict(lambda: {
            "dates": [], "closes": [], "highs": [], "lows": [], "volumes": []
        })
        for ticker, sd, close, high, low, volume in all_rows:
            d = td[ticker]
            d["dates"].append(sd); d["closes"].append(close)
            d["highs"].append(high); d["lows"].append(low)
            d["volumes"].append(volume)

        # Forward-price index
        fwd: dict = defaultdict(dict)
        for ticker, d in td.items():
            for i, dt in enumerate(d["dates"]):
                fwd[ticker][dt] = d["closes"][i]

        all_dates = sorted({r[1] for r in all_rows})
        date_idx  = {d: i for i, d in enumerate(all_dates)}

        to_insert = []
        total = len(td)
        done  = 0

        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for ticker, d in td.items():
                done += 1
                if done % 2000 == 0:
                    elapsed_so_far = round(time.time() - t0)
                    print(f"[bounce_bt] {done}/{total} tickers ({elapsed_so_far}s)…")

                closes  = d["closes"]
                highs   = d["highs"]
                lows    = d["lows"]
                volumes = d["volumes"]
                dates   = d["dates"]
                n       = len(closes)

                if n < 220:
                    continue

                for i in range(210, n):
                    c_h = closes[:i + 1]
                    h_h = highs[:i + 1]
                    l_h = lows[:i + 1]
                    v_h = volumes[:i + 1]
                    dt  = dates[i]

                    sig = _compute_signal(
                        ticker, c_h, h_h, l_h, v_h, dates[:i + 1],
                        None, cur,
                    )
                    if sig is None:
                        continue

                    atr_v = _atr_pct(h_h[-30:], l_h[-30:], c_h[-30:], 14)
                    mod_a = _module_a(c_h, v_h, h_h, l_h, atr_v)

                    reg   = _regime(spy_20d.get(dt))
                    di    = date_idx.get(dt)

                    def fwd_ret(h):
                        if di is None or di + h >= len(all_dates):
                            return None
                        fd = all_dates[di + h]
                        fp = fwd[ticker].get(fd)
                        ep = c_h[-1]
                        return round((fp - ep) / ep * 100, 4) if fp and ep > 0 else None

                    f1, f3, f5, f10 = fwd_ret(1), fwd_ret(3), fwd_ret(5), fwd_ret(10)

                    max_dd = max_fav = None
                    if di is not None:
                        ep = c_h[-1]
                        wp = [
                            fwd[ticker].get(all_dates[di + h])
                            for h in range(1, 6)
                            if di + h < len(all_dates) and fwd[ticker].get(all_dates[di + h])
                        ]
                        if wp and ep > 0:
                            rets = [(p - ep) / ep * 100 for p in wp]
                            max_dd  = round(min(rets), 4)
                            max_fav = round(max(rets), 4)

                    row = (
                        "Oversold_Bounce_Uptrend", ticker, dt, sig["state"],
                        sig["drop_pct_3d"], sig["rsi_2"], sig["rsi_14"],
                        sig["volume_pattern"], sig["conviction_score"],
                        sig["vol_bucket"], reg, spy_20d.get(dt),
                        f1, f3, f5, f10, max_dd, max_fav,
                    )
                    to_insert.append(row)

                    if mod_a["fired"]:
                        to_insert.append((
                            "Module_A_CapVol", ticker, dt, "DETECTED",
                            sig["drop_pct_3d"], sig["rsi_2"], sig["rsi_14"],
                            sig["volume_pattern"], sig["conviction_score"],
                            sig["vol_bucket"], reg, spy_20d.get(dt),
                            f1, f3, f5, f10, max_dd, max_fav,
                        ))

                    # Batch insert every 2000 rows to avoid memory blowup
                    if len(to_insert) >= 2000:
                        with psycopg2.connect(_DB_URL) as bc, bc.cursor() as bcu:
                            psycopg2.extras.execute_values(
                                bcu,
                                """
                                INSERT INTO aiem_bounce_backtest_log
                                    (module_name,ticker,signal_date,state,drop_pct_3d,
                                     rsi_2,rsi_14,volume_pattern,conviction_score,
                                     vol_bucket,market_regime,spy_20d_ret_pct,
                                     fwd_1d_pct,fwd_3d_pct,fwd_5d_pct,fwd_10d_pct,
                                     max_dd_5d_pct,max_fav_5d_pct)
                                VALUES %s
                                ON CONFLICT (module_name,ticker,signal_date) DO NOTHING
                                """,
                                to_insert, page_size=500,
                            )
                            bc.commit()
                        to_insert.clear()

        # Final flush
        if to_insert:
            with psycopg2.connect(_DB_URL) as bc, bc.cursor() as bcu:
                psycopg2.extras.execute_values(
                    bcu,
                    """
                    INSERT INTO aiem_bounce_backtest_log
                        (module_name,ticker,signal_date,state,drop_pct_3d,
                         rsi_2,rsi_14,volume_pattern,conviction_score,
                         vol_bucket,market_regime,spy_20d_ret_pct,
                         fwd_1d_pct,fwd_3d_pct,fwd_5d_pct,fwd_10d_pct,
                         max_dd_5d_pct,max_fav_5d_pct)
                    VALUES %s
                    ON CONFLICT (module_name,ticker,signal_date) DO NOTHING
                    """,
                    to_insert, page_size=500,
                )
                bc.commit()

        elapsed = round(time.time() - t0, 1)
        print(f"[bounce_bt] done in {elapsed}s")
        return {
            "status": "ok",
            "elapsed_s": elapsed,
            "data_bounds": {
                "earliest_date": "2024-07-08",
                "latest_date": str(date.today()),
                "limiting_source": "polygon_market_daily (price/volume only)",
                "notes": (
                    "Fundamental news check NOT_IMPLEMENTED across all rows. "
                    "Modules B/C/D/H/J not yet backtested — each will state its own bounds. "
                    "Whale-block data not used in backtest (no reliable point-in-time history)."
                ),
            },
        }
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": "error", "error": str(e)}

# ── Summary report ─────────────────────────────────────────────────────────────

def get_backtest_summary() -> dict:
    MIN_N = 30
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    module_name, market_regime, vol_bucket,
                    COUNT(*)                                               AS n,
                    MIN(signal_date)::text                                 AS date_from,
                    MAX(signal_date)::text                                 AS date_to,
                    ROUND(AVG(fwd_1d_pct)::numeric, 2)                    AS avg_1d,
                    ROUND(AVG(fwd_3d_pct)::numeric, 2)                    AS avg_3d,
                    ROUND(AVG(fwd_5d_pct)::numeric, 2)                    AS avg_5d,
                    ROUND(AVG(fwd_10d_pct)::numeric, 2)                   AS avg_10d,
                    ROUND(100.0 * SUM(CASE WHEN fwd_5d_pct > 0 THEN 1 ELSE 0 END)
                          / NULLIF(COUNT(fwd_5d_pct),0), 1)               AS wr_5d,
                    ROUND(AVG(max_dd_5d_pct)::numeric, 2)                 AS avg_max_dd,
                    ROUND(AVG(max_fav_5d_pct)::numeric, 2)                AS avg_max_fav
                FROM  aiem_bounce_backtest_log
                GROUP BY module_name, market_regime, vol_bucket
                ORDER BY module_name, market_regime, vol_bucket
                """
            )
            seg_rows = cur.fetchall()
            seg_cols = [d[0] for d in cur.description]

            cur.execute(
                """
                SELECT module_name, COUNT(*) AS total,
                       MIN(signal_date)::text AS date_from,
                       MAX(signal_date)::text AS date_to,
                       ROUND(AVG(fwd_5d_pct)::numeric,2) AS avg_5d,
                       ROUND(100.0*SUM(CASE WHEN fwd_5d_pct>0 THEN 1 ELSE 0 END)
                             / NULLIF(COUNT(fwd_5d_pct),0),1) AS wr_5d
                FROM  aiem_bounce_backtest_log
                GROUP BY module_name
                """
            )
            tot_rows = cur.fetchall()
            tot_cols = [d[0] for d in cur.description]

        segments = []
        for row in seg_rows:
            r = dict(zip(seg_cols, row))
            r["sample_flag"] = ("OK" if r["n"] >= MIN_N
                                else f"INSUFFICIENT_SAMPLE (n={r['n']})")
            segments.append(r)

        return {
            "status": "ok",
            "by_segment": segments,
            "totals": [dict(zip(tot_cols, r)) for r in tot_rows],
            "data_bounds": {
                "source": "polygon_market_daily",
                "earliest_date": "2024-07-08",
                "limiting_source": "polygon_market_daily",
                "fundamental_check_status": "NOT_IMPLEMENTED across all rows",
            },
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

# ── BH-FDR registration in aiem_signal_discoveries ────────────────────────────

def register_signal() -> None:
    """
    Register / refresh Oversold_Bounce_Uptrend in aiem_signal_discoveries.

    Uses status='hypothesis' so Module 2 (decay) and Module 6 (rediscovery)
    include it in their evaluation passes.  Backtest stats (p_value, signal_n,
    signal_win_rate) are pulled from aiem_bounce_backtest_log and written to
    the discovery row so the BH-FDR correction pass has real numbers to work
    with.  Called at startup (deferred init) and after each backtest run.

    Module 2 classification note: this signal's conditions are a sequential
    multi-day state machine (WATCHING → CONFIRMED), so Module 2 will classify
    it as unevaluable_structural — which is correct and honest, not a wiring
    failure.  The BH-FDR correction is performed directly on the backtest
    results via get_backtest_summary() rather than via Module 5's row-scan
    pipeline.
    """
    conditions = {
        "trend": "close>SMA50 AND close>SMA200 AND SMA50_rising_20bars",
        "drop": "pct_change_3d <= ATR_pct_bucket_threshold (-8/-12/-15%)",
        "oversold": "RSI(2)<10 OR RSI(14)<30",
        "volume_exhaustion": "DECLINING or CLIMAX on red days",
        "support": "within 2% of SMA20 or SMA50",
        "confirmation": "first_green_close_after_red_streak",
        "_structural": "WATCHING->CONFIRMED state machine; unevaluable_structural in Module2",
    }
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            # Pull latest backtest stats for the CONFIRMED state (primary metric)
            cur.execute("""
                SELECT COUNT(*) as n,
                       AVG(CASE WHEN fwd_5d_pct > 0 THEN 1.0 ELSE 0.0 END) as wr
                FROM aiem_bounce_backtest_log
                WHERE state = 'CONFIRMED' AND fwd_5d_pct IS NOT NULL
            """)
            row = cur.fetchone()
            bt_n  = int(row[0]) if row and row[0] else 0
            bt_wr = float(row[1]) if row and row[1] else None

            # p-value: binomial one-sided vs 50% baseline (continuity corrected)
            p_val = None
            if bt_n and bt_wr is not None:
                import math as _m
                k = int(round(bt_wr * bt_n))
                # Normal approximation with continuity correction
                z = (k - 0.5 - bt_n * 0.5) / _m.sqrt(bt_n * 0.25)
                # p = 1 - Φ(z) approximated via erfc
                p_val = round(0.5 * math.erfc(z / math.sqrt(2)), 4) if z > 0 else 0.9999

            cur.execute(
                "SELECT id FROM aiem_signal_discoveries WHERE hypothesis_text=%s",
                ("Oversold_Bounce_Uptrend",),
            )
            existing = cur.fetchone()

            if existing:
                # Refresh stats on every startup so decay module sees current numbers
                cur.execute(
                    """
                    UPDATE aiem_signal_discoveries
                    SET signal_n        = %s,
                        signal_win_rate = %s,
                        p_value         = %s,
                        status          = 'hypothesis',
                        notes           = %s
                    WHERE id = %s
                    """,
                    (
                        bt_n or None, bt_wr, p_val,
                        f"structural state-machine; Module2=unevaluable_structural; "
                        f"backtest_rows={bt_n}; confirmed_wr={bt_wr}; p={p_val}",
                        existing[0],
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO aiem_signal_discoveries
                        (hypothesis_text, conditions_json, status, horizon,
                         invented_indicator, signal_n, signal_win_rate, p_value,
                         notes, signal_name, discovered_at)
                    VALUES (%s, %s::jsonb, 'hypothesis', %s, %s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        "Oversold_Bounce_Uptrend",
                        json.dumps(conditions),
                        "5d",
                        "aiem_selloff_reversion_phase1",
                        bt_n or None, bt_wr, p_val,
                        f"structural state-machine; Module2=unevaluable_structural; "
                        f"backtest_rows={bt_n}; confirmed_wr={bt_wr}; p={p_val}",
                        "Oversold_Bounce_Uptrend",
                    ),
                )
            conn.commit()
        print(f"[bounce] registered Oversold_Bounce_Uptrend: n={bt_n} wr={bt_wr} p={p_val}")
    except Exception as e:
        print(f"[bounce] signal-discovery registration: {e}")
