"""
aiem_standalone_scanner.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AIEM STANDALONE AUTONOMOUS SCANNER

Runs 100% independently of Flask / your website.
Connects directly to Polygon.io and the database — no Flask routing.

ARCHITECTURE
  Flask / Website  ←── completely separate, can be dead
  Database  ←──────────────────────────────────────────────────┐
  aiem_standalone_scanner.py ──→ Polygon.io                    │
       │                                                         │
       └── writes aiem_signals + aiem_predictions ─────────────┘

SCHEDULE (Eastern Time)
  04:00  Mon-Fri   Premarket scan  — early gap candidates
  07:00  Mon-Fri   Main scan       — full intelligence pipeline
  16:30  Mon-Fri   EOD feedback    — score today's picks vs close
  */5    every day Health check    — confirm Polygon reachable

INTELLIGENCE LAYERS (applied to every candidate)
  Layer A  staleness_filter.py                — catalyst decay, VWAP exhaustion
  Layer B  aiem_verification_and_trading_brain.py — PIPE fade, SPAC, delisting
  Layer C  aiem_intelligence_upgrade.py        — 6 systems: kill switch, news
                                               source, sector heat, time-of-day,
                                               float/SI, EOD feedback loop

REQUIRED ENV VARS
  POLYGON_API_KEY  — your Polygon.io API key
  DATABASE_URL     — postgres://... connection string
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID — for pick alerts (optional)
  AIEM_HMAC_SECRET — for HMAC verification (optional)
"""

import logging
import os
import signal
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except ImportError:
    import pytz
    _ET = pytz.timezone("America/New_York")

# ── Optional .env loader ──────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/aiem_standalone.log", mode="a"),
    ],
)
logger = logging.getLogger("AIEM_STANDALONE")

# ─────────────────────────────────────────────────────────────────────────────
# ENV VARS
# ─────────────────────────────────────────────────────────────────────────────
POLYGON_API_KEY  = os.environ.get("POLYGON_API_KEY",  "")
DATABASE_URL     = os.environ.get("DATABASE_URL",     "")
TG_TOKEN         = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "8609255707")

POLYGON_BASE = "https://api.polygon.io"
_SESSION     = requests.Session()
_SESSION.params = {"apiKey": POLYGON_API_KEY}   # type: ignore

# Scanner filters
MAX_PRICE          = 30.0    # $30 ceiling — micro/small-cap focus
MIN_PRICE          = 0.25    # below this = likely broken ticker
MIN_GAP_PCT        = 0.05    # 5% minimum gap to qualify
MAX_MARKET_CAP     = 2_000_000_000   # $2B cap
MAX_FLOAT_SHARES   = 50_000_000      # 50M float cap

def validate_env() -> None:
    missing = [v for v in ["POLYGON_API_KEY", "DATABASE_URL"] if not os.environ.get(v)]
    if missing:
        logger.error("MISSING REQUIRED ENV VARS: %s", missing)
        sys.exit(1)
    logger.info("✓ Environment variables validated.")

# ─────────────────────────────────────────────────────────────────────────────
# INTELLIGENCE MODULES — imported once at startup
# Wrapped in try/except: scanner works standalone even if modules are absent.
# ─────────────────────────────────────────────────────────────────────────────
_STALENESS_AVAILABLE = False
_WS_AVAILABLE        = False
_INTEL_AVAILABLE     = False

try:
    from aiem_master_part1 import (
        evaluate_signal_with_data          as _eval_staleness,
        apply_wall_street_pattern_with_data as _apply_ws,
    )
    _STALENESS_AVAILABLE = True
    _WS_AVAILABLE        = True
    logger.info("✓ aiem_master_part1 loaded (layers A + B)")
except Exception as _e:
    logger.warning("aiem_master_part1 not loaded (%s) — falling back to legacy modules", _e)
    # Legacy fallback: try original split files
    try:
        from staleness_filter import evaluate_signal_with_data as _eval_staleness
        _STALENESS_AVAILABLE = True
        logger.info("✓ staleness_filter loaded (legacy)")
    except Exception as _e2:
        logger.warning("staleness_filter not loaded (%s) — skipping layer A", _e2)
    try:
        from aiem_verification_and_trading_brain import (
            apply_wall_street_pattern_with_data as _apply_ws,
        )
        _WS_AVAILABLE = True
        logger.info("✓ aiem_verification_and_trading_brain loaded (legacy)")
    except Exception as _e3:
        logger.warning("aiem_verification_and_trading_brain not loaded (%s) — skipping layer B", _e3)

try:
    from aiem_intelligence_upgrade import (
        is_kill_switch_active,
        deactivate_kill_switch,
        score_news_source_with_data,
        get_sector_conviction_penalty_with_data,
        get_float_and_si_with_data,
        apply_time_of_day,
        run_eod_feedback_loop_with_data,
        CONVICTION_BASE_THRESHOLD,
    )
    _INTEL_AVAILABLE = True
    logger.info("✓ aiem_intelligence_upgrade loaded")
except Exception as _e:
    logger.warning("aiem_intelligence_upgrade not loaded (%s) — skipping layer C", _e)
    CONVICTION_BASE_THRESHOLD = 70

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE — direct psycopg2, no Flask/pool dependency
# ─────────────────────────────────────────────────────────────────────────────
try:
    import psycopg2
    import psycopg2.extras
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logger.warning("psycopg2 not installed — DB writes disabled.")


def _get_conn():
    """Open a fresh DB connection. Always close after use."""
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def ensure_tables_exist() -> None:
    if not DB_AVAILABLE:
        return
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_signals (
                id               SERIAL PRIMARY KEY,
                ticker           VARCHAR(20)  NOT NULL,
                scan_dt          TIMESTAMP,
                action           VARCHAR(20),
                base_conviction  REAL,
                final_conviction REAL,
                gap_pct          REAL,
                price            REAL,
                vwap             REAL,
                market_cap       BIGINT,
                catalyst_source  VARCHAR(40),
                catalyst_age_h   REAL,
                tags             TEXT,
                notes            TEXT,
                signal_date      DATE,
                entry_price      REAL,
                close_price      REAL,
                outcome          VARCHAR(20),
                pct_move         REAL,
                created_at       TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS aiem_health_log (
                id         SERIAL PRIMARY KEY,
                status     VARCHAR(20),
                detail     TEXT,
                checked_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS aiem_scan_log (
                id             SERIAL PRIMARY KEY,
                scan_type      VARCHAR(50),
                tickers_found  INTEGER,
                tickers_passed INTEGER,
                scan_dt        TIMESTAMP DEFAULT NOW()
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✓ DB tables verified.")
    except Exception as e:
        logger.error("[DB] Table creation failed: %s", e)


def _db_write_signal(signal: dict) -> None:
    if not DB_AVAILABLE:
        return
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        today = signal.get("signal_date") or date.today().isoformat()
        cur.execute("""
            INSERT INTO aiem_signals (
                ticker, scan_dt, action, base_conviction, final_conviction,
                gap_pct, price, vwap, market_cap, catalyst_source,
                catalyst_age_h, tags, notes, signal_date, entry_price
            ) VALUES (
                %(ticker)s, %(scan_dt)s, %(action)s, %(base_conviction)s,
                %(final_conviction)s, %(gap_pct)s, %(price)s, %(vwap)s,
                %(market_cap)s, %(catalyst_source)s, %(catalyst_age_h)s,
                %(tags)s, %(notes)s, %(signal_date)s, %(price)s
            )
            ON CONFLICT DO NOTHING
        """, {
            **signal,
            "tags":         ",".join(signal.get("tags", [])),
            "notes":        " | ".join(signal.get("notes", []))[:500],
            "signal_date":  today,
        })
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error("[DB] Signal write failed for %s: %s", signal.get("ticker"), e)


def _db_write_predictions(picks: list[dict]) -> None:
    """
    Write final picks to aiem_predictions so the website dashboard can display them.
    This is the same table aiem_autonomous.py writes to.
    Uses INSERT ... ON CONFLICT DO NOTHING so both processes can coexist peacefully.
    """
    if not DB_AVAILABLE or not picks:
        return
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        today = date.today()
        for i, p in enumerate(picks, 1):
            sig_basis = ",".join(t for t in p.get("tags", []) if not t.startswith("ZONE_"))[:200]
            reasoning = " | ".join(p.get("notes", []))[:400]
            cur.execute("""
                INSERT INTO aiem_predictions
                    (prediction_date, ticker, rank, confidence_score, signal_basis,
                     reasoning, predicted_move)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (today, p["ticker"], i, round(p["final_conviction"]),
                  sig_basis, reasoning, "GAP_PLAY"))
        conn.commit()
        cur.close()
        conn.close()
        logger.info("[DB] Wrote %d picks to aiem_predictions", len(picks))
    except Exception as e:
        logger.error("[DB] aiem_predictions write failed: %s", e)


def _db_health(status: str, detail: str = "") -> None:
    if not DB_AVAILABLE:
        return
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO aiem_health_log (status, detail) VALUES (%s, %s)",
            (status, detail[:400]),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def _db_scan_log(scan_type: str, found: int, passed: int) -> None:
    if not DB_AVAILABLE:
        return
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO aiem_scan_log (scan_type, tickers_found, tickers_passed) VALUES (%s,%s,%s)",
            (scan_type, found, passed),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def _get_multiday_history(ticker: str, conn, days: int = 3) -> list[dict]:
    """
    Pull recent OHLCV from polygon_market_daily.
    Returns a list of dicts (oldest first) compatible with staleness_filter.
    """
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT scan_date, open_price, high_price, low_price, close_price,
                   volume, prev_close, vwap, gap_pct
            FROM polygon_market_daily
            WHERE ticker = %s
            ORDER BY scan_date DESC
            LIMIT %s
        """, (ticker, days))
        rows = cur.fetchall()
        result = [
            {
                "date":       str(r[0]),
                "open":       float(r[1] or 0),
                "high":       float(r[2] or 0),
                "low":        float(r[3] or 0),
                "close":      float(r[4] or 0),
                "volume":     float(r[5] or 0),
                "prev_close": float(r[6] or 0),
                "vwap":       float(r[7] or 0),
                "gap_pct":    float(r[8] or 0),
            }
            for r in rows
        ]
        return list(reversed(result))   # oldest first
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# POLYGON — direct API calls, no Flask intermediary
# ─────────────────────────────────────────────────────────────────────────────

def _poly(endpoint: str, params: dict | None = None, timeout: int = 10) -> dict:
    """Direct GET to Polygon. Returns JSON or {}."""
    try:
        r = _SESSION.get(f"{POLYGON_BASE}{endpoint}", params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug("[POLYGON] %s failed: %s", endpoint, e)
        return {}


def get_bulk_snapshot_candidates() -> list[dict]:
    """
    Fetch all US equity snapshots from Polygon and filter for gap candidates.
    Returns up to 50 candidates sorted by gap_pct descending.
    """
    data = _poly("/v2/snapshot/locale/us/markets/stocks/gainers",
                 {"include_otc": "false"})
    tickers_raw = data.get("tickers") or []

    # Also try the broader bulk snapshot for more coverage
    bulk = _poly("/v2/snapshot/locale/us/markets/stocks",
                 {"include_otc": "false"})
    tickers_raw = tickers_raw + (bulk.get("tickers") or [])

    seen       = set()
    candidates = []
    for t in tickers_raw:
        sym = t.get("ticker", "")
        if not sym or sym in seen or len(sym) > 5:
            continue
        seen.add(sym)

        day      = t.get("day",     {})
        prev_day = t.get("prevDay", {})

        prev_c   = float(prev_day.get("c") or 0)
        open_p   = float(day.get("o") or 0)
        close_p  = float(day.get("c") or open_p)
        price    = float((t.get("lastTrade") or {}).get("p") or close_p)
        volume   = float(day.get("v") or 0)

        if not prev_c or not open_p:
            continue
        if not (MIN_PRICE <= price <= MAX_PRICE):
            continue

        gap_pct = (open_p - prev_c) / prev_c
        if gap_pct < MIN_GAP_PCT:
            continue

        vwap = float(day.get("vw") or 0)
        candidates.append({
            "ticker":     sym,
            "gap_pct":    round(gap_pct, 4),
            "open_price": open_p,
            "close_price":close_p,
            "price":      price,
            "prev_close": prev_c,
            "volume":     volume,
            "vwap":       vwap,
        })

    candidates.sort(key=lambda x: -x["gap_pct"])
    logger.info("[POLYGON] %d gap candidates (≥%.0f%% gap, price $%.2f-$%.2f)",
                len(candidates), MIN_GAP_PCT * 100, MIN_PRICE, MAX_PRICE)
    return candidates[:50]


def get_ticker_details(ticker: str) -> dict:
    data = _poly(f"/v3/reference/tickers/{ticker}")
    return data.get("results", {})


def get_news_items(ticker: str, limit: int = 3) -> list[dict]:
    data = _poly("/v2/reference/news",
                 {"ticker": ticker, "limit": limit, "order": "desc"})
    return data.get("results") or []


def get_previous_close(ticker: str) -> float:
    data = _poly(f"/v2/aggs/ticker/{ticker}/prev")
    results = (data.get("results") or [{}])
    return float(results[0].get("c") or 0)


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────

def _tg_send(msg: str) -> None:
    if not TG_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg[:4096]},
            timeout=8,
        )
    except Exception as e:
        logger.warning("[TG] Send failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# MARKET CALENDAR
# ─────────────────────────────────────────────────────────────────────────────

_HOLIDAYS_2026 = {
    date(2026, 1,  1),   # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents Day
    date(2026, 4,  3),   # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 7,  3),   # Independence Day observed
    date(2026, 9,  7),   # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 11, 27),  # Day after Thanksgiving (early close → skip)
    date(2026, 12, 25),  # Christmas
}


def _is_market_day() -> bool:
    today   = date.today()
    weekday = today.weekday()
    return weekday < 5 and today not in _HOLIDAYS_2026


# ─────────────────────────────────────────────────────────────────────────────
# CORE SCORING — runs all 3 intelligence layers
# ─────────────────────────────────────────────────────────────────────────────

def score_signal(candidate: dict, scan_dt: datetime, db_conn) -> dict:
    """
    Score one gap candidate through all available intelligence layers.

    Layer A — staleness_filter    (catalyst decay, VWAP exhaustion, move-day)
    Layer B — WS patterns         (PIPE fade, delisting squeeze, SPAC, etc.)
    Layer C — Intelligence upgrade (kill switch, news source, sector heat,
                                    time-of-day, float/SI)

    Falls back to simplified inline scoring when modules are unavailable.
    Returns a full verdict dict.
    """
    ticker   = candidate["ticker"]
    gap_pct  = candidate["gap_pct"]
    price    = candidate["price"]
    today    = scan_dt.date()

    # Fetch auxiliary data from Polygon and DB (done once per ticker)
    news    = get_news_items(ticker, limit=5)
    details = get_ticker_details(ticker)
    history = _get_multiday_history(ticker, db_conn, days=3)

    # Market cap + float filters
    mkt_cap    = float(details.get("market_cap") or 0)
    float_sh   = float(details.get("share_class_shares_outstanding") or
                        details.get("weighted_shares_outstanding") or 0)
    if mkt_cap  > MAX_MARKET_CAP  and mkt_cap  > 0:
        return _skip(ticker, scan_dt, candidate, "MARKET_CAP_TOO_LARGE",
                     f"mkt_cap=${mkt_cap/1e6:.0f}M > ${MAX_MARKET_CAP/1e6:.0f}M limit")
    if float_sh > MAX_FLOAT_SHARES and float_sh > 0:
        return _skip(ticker, scan_dt, candidate, "FLOAT_TOO_LARGE",
                     f"float={float_sh/1e6:.1f}M > {MAX_FLOAT_SHARES/1e6:.0f}M limit")

    # ── Base conviction from gap size ───────────────────────────────────────
    base = 70.0
    if   gap_pct >= 1.00: base += 15
    elif gap_pct >= 0.50: base += 10
    elif gap_pct >= 0.20: base +=  5

    conviction = base
    tags:  list[str] = [f"GAP_{int(gap_pct*100)}PCT"]
    notes: list[str] = [f"Gap {gap_pct*100:.1f}% | price ${price:.2f}"]

    # ── Layer A: Staleness filter ────────────────────────────────────────────
    if _STALENESS_AVAILABLE:
        verdict = _eval_staleness(
            ticker, conviction, history, news, scan_dt, premarket_mode=True,
        )
        if verdict["action"] == "SKIP":
            return _skip(ticker, scan_dt, candidate,
                         "STALENESS_" + (verdict.get("reason", "SKIP")),
                         verdict.get("reason", "staleness filter"))
        conviction = verdict["final_conviction"]
        tags.extend(t for t in verdict.get("tags", []) if t not in tags)
        notes.append(f"Staleness ✓ → {conviction:.0f}")

    # ── Layer B: Wall Street patterns ────────────────────────────────────────
    if _WS_AVAILABLE:
        ws_input = {"final_conviction": conviction, "tags": tags, "notes": notes,
                    "action": "PASS", "reason": "", "ws_notes": []}
        ws = _apply_ws(ticker, ws_input, history, news)
        conviction = float(ws["final_conviction"])
        tags.extend(t for t in ws.get("tags", []) if t not in tags)
        notes.extend(ws.get("ws_notes", []))
        if conviction < CONVICTION_BASE_THRESHOLD:
            return _skip(ticker, scan_dt, candidate, "WS_PATTERN",
                         " | ".join(ws.get("ws_notes", ["pattern penalty"])))
        notes.append(f"WS patterns ✓ → {conviction:.0f}")

    # ── Layer C: Intelligence upgrade ────────────────────────────────────────
    if _INTEL_AVAILABLE:
        # Kill switch
        killed, kill_reason = is_kill_switch_active(db_conn, today)
        if killed:
            return _skip(ticker, scan_dt, candidate, "KILL_SWITCH", kill_reason)

        # News source quality
        src_score, src_name, src_delta = score_news_source_with_data(news)
        conviction += src_delta
        tags.append(f"SOURCE_{src_name}")
        if src_delta != 0:
            notes.append(f"Source '{src_name}' (q={src_score}) → {src_delta:+d}")

        # Sector heat
        sec_penalty, sec_tag = get_sector_conviction_penalty_with_data(details, db_conn)
        conviction += sec_penalty
        if sec_tag not in ("SECTOR_NEUTRAL", "SECTOR_DATA_UNAVAILABLE"):
            tags.append(sec_tag)
            notes.append(f"Sector: {sec_tag} → {sec_penalty:+d}")

        # Float + SI
        float_data = get_float_and_si_with_data(details)
        conviction += float_data["conviction_delta"]
        tags.append(f"FLOAT_{float_data['float_category']}")
        if float_data["squeeze_candidate"]:
            tags.append("SQUEEZE_CANDIDATE")

        # Time of day
        conviction, zone_name, allow_entry, threshold = apply_time_of_day(
            conviction, scan_dt, premarket_mode=True,
        )
        tags.append(f"ZONE_{zone_name}")
    else:
        threshold = CONVICTION_BASE_THRESHOLD

    # ── Final verdict ────────────────────────────────────────────────────────
    conviction = max(0.0, round(conviction, 1))
    action = "PASS" if conviction >= threshold else "SKIP"

    signal = {
        "ticker":           ticker,
        "scan_dt":          scan_dt.isoformat(),
        "signal_date":      today.isoformat(),
        "action":           action,
        "base_conviction":  base,
        "final_conviction": conviction,
        "gap_pct":          round(gap_pct * 100, 2),
        "price":            price,
        "vwap":             candidate.get("vwap") or 0,
        "market_cap":       int(mkt_cap),
        "catalyst_source":  tags[1] if len(tags) > 1 else "UNKNOWN",
        "catalyst_age_h":   None,
        "tags":             list(set(tags)),
        "notes":            notes,
        "allow_entry":      True,
    }
    logger.info("[SIGNAL] %s  %s  conviction=%.0f  gap=%.1f%%  tags=%s",
                ticker, action, conviction, gap_pct * 100,
                [t for t in signal["tags"] if t.startswith("PATTERN_") or "SOURCE" in t])
    return signal


def _skip(ticker: str, scan_dt: datetime, candidate: dict,
          reason: str, note: str) -> dict:
    return {
        "ticker":           ticker,
        "scan_dt":          scan_dt.isoformat(),
        "signal_date":      scan_dt.date().isoformat(),
        "action":           "SKIP",
        "base_conviction":  70,
        "final_conviction": 0,
        "gap_pct":          round(candidate.get("gap_pct", 0) * 100, 2),
        "price":            candidate.get("price", 0),
        "vwap":             candidate.get("vwap", 0),
        "market_cap":       0,
        "catalyst_source":  "UNKNOWN",
        "catalyst_age_h":   None,
        "tags":             [reason],
        "notes":            [note],
        "allow_entry":      False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULED JOBS
# ─────────────────────────────────────────────────────────────────────────────

def job_premarket_scan() -> None:
    """04:00 AM ET — Early premarket gap scan. Logs candidates, no Telegram yet."""
    if not _is_market_day():
        logger.info("[PREMARKET] Not a market day — skipping.")
        return

    # Reset kill switch for the new session
    if _INTEL_AVAILABLE:
        try:
            from aiem_intelligence_upgrade import deactivate_kill_switch
            deactivate_kill_switch()
        except Exception:
            pass

    logger.info("=" * 60)
    logger.info("[PREMARKET SCAN] %s", datetime.now().isoformat())
    logger.info("=" * 60)

    candidates = get_bulk_snapshot_candidates()
    if not candidates:
        logger.warning("[PREMARKET] No candidates from Polygon.")
        _db_health("PREMARKET_EMPTY", "No gap candidates")
        return

    scan_dt = datetime.now(timezone.utc)
    passed = skipped = 0

    if DB_AVAILABLE:
        db_conn = _get_conn()
    else:
        db_conn = None

    try:
        for c in candidates[:10]:
            try:
                sig = score_signal(c, scan_dt, db_conn)
                _db_write_signal(sig)
                if sig["action"] == "PASS":
                    passed += 1
                else:
                    skipped += 1
                time.sleep(0.3)   # rate-limit Polygon calls
            except Exception as e:
                logger.error("[PREMARKET] Error on %s: %s", c["ticker"], e)
    finally:
        if db_conn:
            db_conn.close()

    logger.info("[PREMARKET] Done — %d passed, %d skipped", passed, skipped)
    _db_scan_log("PREMARKET", len(candidates), passed)
    _db_health("PREMARKET_OK", f"found={len(candidates)} passed={passed}")


def job_morning_scan() -> None:
    """07:00 AM ET — Full intelligence scan. Sends Telegram picks."""
    if not _is_market_day():
        logger.info("[MORNING] Not a market day — skipping.")
        return

    logger.info("=" * 60)
    logger.info("[MORNING SCAN] %s", datetime.now().isoformat())
    logger.info("=" * 60)

    candidates = get_bulk_snapshot_candidates()
    if not candidates:
        logger.warning("[MORNING] No candidates from Polygon.")
        _db_health("MORNING_EMPTY", "No gap candidates at 7 AM")
        return

    scan_dt = datetime.now(timezone.utc)
    picks   = []

    if DB_AVAILABLE:
        db_conn = _get_conn()
    else:
        db_conn = None

    try:
        for c in candidates[:20]:
            try:
                sig = score_signal(c, scan_dt, db_conn)
                _db_write_signal(sig)
                if sig["action"] == "PASS":
                    picks.append(sig)
                time.sleep(0.4)
            except Exception as e:
                logger.error("[MORNING] Error on %s: %s", c["ticker"], e)
    finally:
        if db_conn:
            db_conn.close()

    # Sort by conviction, take top 5
    picks.sort(key=lambda x: -x["final_conviction"])
    top_picks = picks[:5]

    logger.info("[MORNING] %d picks generated:", len(picks))
    for p in top_picks:
        logger.info("  ★ %s  conviction=%.0f  gap=%.1f%%  tags=%s",
                    p["ticker"], p["final_conviction"], p["gap_pct"],
                    [t for t in p["tags"] if not t.startswith("ZONE_")])

    # Write top picks to aiem_predictions for the website dashboard
    if top_picks:
        _db_write_predictions(top_picks)

    # Telegram alert
    if top_picks:
        _send_morning_picks_tg(top_picks)

    _db_scan_log("MORNING", len(candidates), len(picks))
    _db_health("MORNING_OK", f"candidates={len(candidates)} picks={len(picks)}")


def _send_morning_picks_tg(picks: list[dict]) -> None:
    lines = ["🤖 AIEM MORNING PICKS\n"]
    for p in picks:
        src  = next((t.replace("SOURCE_","") for t in p["tags"] if t.startswith("SOURCE_")), "?")
        flt  = next((t.replace("FLOAT_","") for t in p["tags"] if t.startswith("FLOAT_")), "?")
        pats = [t.replace("PATTERN_","") for t in p["tags"] if t.startswith("PATTERN_")]
        line = (f"★ ${p['ticker']}  "
                f"conf={p['final_conviction']:.0f}  "
                f"gap={p['gap_pct']:.1f}%  "
                f"src={src}  float={flt}")
        if pats:
            line += f"\n  ⚠ {', '.join(pats)}"
        lines.append(line)

    # Concentration alert: if all picks share the same pattern tag
    all_patterns = [t for p in picks for t in p["tags"] if t.startswith("PATTERN_")]
    if len(set(all_patterns)) == 1 and len(all_patterns) >= 3:
        lines.append(f"\n⚠️ CONCENTRATION WARNING: all picks tagged {all_patterns[0]}"
                     " — possible systemic scanner bias.")

    _tg_send("\n".join(lines))


def job_eod_feedback() -> None:
    """16:30 ET — Score today's aiem_signals picks against actual closes."""
    if not _is_market_day():
        return

    logger.info("=" * 60)
    logger.info("[EOD FEEDBACK] %s", datetime.now().isoformat())
    logger.info("=" * 60)

    if not DB_AVAILABLE:
        logger.warning("[EOD] DB unavailable — skipping.")
        return

    conn = _get_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT id, ticker, action, entry_price, signal_date
            FROM aiem_signals
            WHERE signal_date = %s AND outcome IS NULL AND action = 'PASS'
        """, (date.today(),))
        signals = cur.fetchall()
    except Exception as e:
        logger.error("[EOD] Fetch failed: %s", e)
        conn.close()
        return
    finally:
        cur.close()

    logger.info("[EOD] Scoring %d PASS signals", len(signals))
    wins = losses = neutral = 0

    for s in signals:
        ticker      = s["ticker"]
        entry_price = float(s.get("entry_price") or 0)
        close_data  = _poly(f"/v2/aggs/ticker/{ticker}/prev")
        if not close_data.get("results"):
            continue
        close_price = float((close_data["results"][0]).get("c") or 0)
        if not entry_price or not close_price:
            continue

        pct_move = (close_price - entry_price) / entry_price
        if   pct_move >  0.02: outcome = "WINNER";  wins    += 1
        elif pct_move < -0.02: outcome = "LOSER";   losses  += 1
        else:                  outcome = "NEUTRAL";  neutral += 1

        try:
            cur2 = conn.cursor()
            cur2.execute("""
                UPDATE aiem_signals
                SET close_price = %s, outcome = %s, pct_move = %s
                WHERE id = %s
            """, (close_price, outcome, round(pct_move, 4), s["id"]))
            conn.commit()
            cur2.close()
        except Exception as e:
            logger.error("[EOD] Update failed for %s: %s", ticker, e)

        time.sleep(0.2)

    conn.close()

    total    = wins + losses + neutral
    win_rate = wins / total if total > 0 else 0
    summary  = f"total={total} wins={wins} losses={losses} WR={win_rate:.1%}"
    logger.info("[EOD SUMMARY] %s", summary)
    _db_health("EOD_OK", summary)

    # Telegram EOD summary with feedback loop stats
    msg  = f"📊 AIEM EOD FEEDBACK\n"
    msg += f"{wins}W / {losses}L / {neutral} neutral\n"
    msg += f"Win rate: {win_rate:.1%}  (today, {total} picks)\n"

    if _INTEL_AVAILABLE and DB_AVAILABLE:
        try:
            fb_conn = _get_conn()
            fb = run_eod_feedback_loop_with_data(fb_conn, lookback_days=14)
            fb_conn.close()
            msg += (f"\n📈 14-day: {fb['win_rate']:.1%} WR | "
                    f"W={fb['winners']} L={fb['losers']}\n")
            if fb["top_loser_tags"]:
                msg += "⚠ Top loser tags: " + ", ".join(t for t, _ in fb["top_loser_tags"][:3])
        except Exception as e:
            logger.warning("[EOD] Feedback loop stats failed: %s", e)

    _tg_send(msg)


def job_health_check() -> None:
    """Every 5 min — confirm scanner and Polygon are alive."""
    data   = _poly("/v1/marketstatus/now")
    status = (data.get("market") or "polygon_unreachable") if data else "polygon_unreachable"
    _db_health("ALIVE", f"market_status={status}")
    logger.debug("[HEALTH] alive — market=%s", status)


# ─────────────────────────────────────────────────────────────────────────────
# GRACEFUL SHUTDOWN
# ─────────────────────────────────────────────────────────────────────────────

_scheduler: BlockingScheduler | None = None


def _handle_shutdown(signum, frame):
    logger.info("[SHUTDOWN] signal %d received — shutting down.", signum)
    if _scheduler:
        _scheduler.shutdown(wait=False)
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT,  _handle_shutdown)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("━" * 60)
    logger.info("  AIEM STANDALONE SCANNER  v2.0")
    logger.info("  Flask / website status : IRRELEVANT")
    logger.info("  Polygon connection     : DIRECT")
    logger.info("  Database connection    : DIRECT")
    logger.info("  Layers loaded          : staleness=%s  ws=%s  intel=%s",
                _STALENESS_AVAILABLE, _WS_AVAILABLE, _INTEL_AVAILABLE)
    logger.info("━" * 60)

    validate_env()
    ensure_tables_exist()

    global _scheduler
    _scheduler = BlockingScheduler(
        timezone   = "America/New_York",
        job_defaults = {
            "coalesce":          True,
            "max_instances":     1,
            "misfire_grace_time": 600,
        },
    )

    _scheduler.add_job(
        job_premarket_scan,
        CronTrigger(day_of_week="mon-fri", hour=4, minute=0),
        id="premarket_scan",
        name="Premarket Gap Scan (4:00 AM ET)",
    )
    _scheduler.add_job(
        job_morning_scan,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=0),
        id="morning_scan",
        name="Morning Full Intelligence Scan (7:00 AM ET)",
    )
    _scheduler.add_job(
        job_eod_feedback,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30),
        id="eod_feedback",
        name="EOD Feedback Loop (4:30 PM ET)",
    )
    _scheduler.add_job(
        job_health_check,
        CronTrigger(minute="*/5"),
        id="health_check",
        name="Health Check (every 5 min)",
    )

    logger.info("✓ Jobs scheduled:")
    for job in _scheduler.get_jobs():
        logger.info("    • %s", job.name)
    logger.info("✓ Scanner running. Flask can be up, down, or dead — doesn't matter.")
    logger.info("━" * 60)

    _scheduler.start()


if __name__ == "__main__":
    main()
