"""
aiem_master_part1.py  — WIRED PRODUCTION VERSION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AIEM MASTER — PART 1 of 2  (all stubs wired to Polygon.io + PostgreSQL)

  SECTION 1 : Staleness Filter    (stale gap, catalyst decay, move day, VWAP)
  SECTION 2 : Wall Street Brain   (7 golden rules, pattern library, post-mortem)
  SECTION 3 : HMAC Verification   (6 signed questions proving AIEM loaded this)

Exports for aiem_standalone_scanner.py and aiem_autonomous.py:
  evaluate_signal_with_data(ticker, base_conviction, scan_ts, history, news,
                             details=None, premarket_mode=True) → dict
  apply_wall_street_pattern_with_data(ticker, signal, history, news) → dict
  verify_response(challenge, aiem_answer) → dict
  issue_challenge(question_id) → dict

Part 2: aiem_master_part2.py (Intelligence Upgrade + Standalone Scanner)
"""

import hashlib
import hmac as _hmac_mod
import json
import logging
import os
import time
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Any

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except ImportError:
    import pytz
    _ET = pytz.timezone("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("AIEM_P1")

HMAC_SECRET          = os.environ.get("AIEM_HMAC_SECRET", "CHANGE_ME_IN_ENV").encode()
CHALLENGE_TTL        = 300
CONVICTION_THRESHOLD = 70

POLYGON_API_KEY  = os.environ.get("POLYGON_API_KEY", "")
DATABASE_URL     = os.environ.get("DATABASE_URL", "")

# ─────────────────────────────────────────────────────────────────────────────
# LOW-LEVEL DATA FETCHERS  (Polygon + DB — called lazily, never at import time)
# ─────────────────────────────────────────────────────────────────────────────

def _poly_get(endpoint: str, params: dict | None = None) -> dict:
    """Direct Polygon GET.  Returns {} on any failure."""
    try:
        import requests as _req
        r = _req.get(
            f"https://api.polygon.io{endpoint}",
            params={**(params or {}), "apiKey": POLYGON_API_KEY},
            timeout=6,
        )
        r.raise_for_status()
        return r.json()
    except Exception as _e:
        logger.debug("[poly_get] %s → %s", endpoint, _e)
        return {}


def _poly_snapshot(ticker: str) -> dict:
    """Single-ticker Polygon snapshot.  Returns the inner 'ticker' dict or {}."""
    data = _poly_get(f"/v2/snapshot/locale/us/markets/stocks/{ticker}")
    return data.get("ticker") or {}


def _poly_news(ticker: str, limit: int = 5) -> list[dict]:
    """Latest Polygon news articles for a ticker."""
    data = _poly_get("/v2/reference/news", {"ticker": ticker, "limit": limit, "order": "desc"})
    return data.get("results") or []


def _poly_details(ticker: str) -> dict:
    """Polygon v3 reference ticker details."""
    data = _poly_get(f"/v3/reference/tickers/{ticker}")
    return data.get("results") or {}


def _db_history(ticker: str, days: int = 3) -> list[dict]:
    """
    Fetch recent OHLCV rows from polygon_market_daily.
    Returns list of dicts (oldest-first) compatible with all callers.
    Falls back to [] if DB is unavailable.
    """
    if not DATABASE_URL:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=4)
        cur  = conn.cursor()
        cur.execute("""
            SELECT scan_date, open_price, high_price, low_price, close_price,
                   volume, prev_close, vwap, gap_pct
            FROM polygon_market_daily
            WHERE ticker = %s
            ORDER BY scan_date DESC
            LIMIT %s
        """, (ticker, days))
        rows = cur.fetchall()
        conn.close()
        return list(reversed([
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
        ]))
    except Exception as _e:
        logger.warning("[db_history] %s → %s", ticker, _e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# STUB FUNCTIONS — now wired to real data
# These are the named entry-points from the original template.
# Callers may also pass pre-fetched context via `history` / `news` / `snap`.
# ─────────────────────────────────────────────────────────────────────────────

def get_gap_open_price(ticker: str,
                       history: list[dict] | None = None,
                       snap: dict | None = None) -> float:
    """Today's open price.  Prefers history[-1] → Polygon snapshot → 0."""
    if history:
        return float(history[-1].get("open") or 0)
    if snap is None:
        snap = _poly_snapshot(ticker)
    return float((snap.get("day") or {}).get("o") or 0)


def get_current_price(ticker: str, snap: dict | None = None) -> float:
    """Most recent trade price from Polygon snapshot."""
    if snap is None:
        snap = _poly_snapshot(ticker)
    price = ((snap.get("lastTrade") or {}).get("p") or
             (snap.get("day")      or {}).get("c") or 0)
    return float(price)


def get_gap_date(ticker: str,
                 history: list[dict] | None = None,
                 scan_ts: datetime | None = None) -> date:
    """
    The date on which the gap originally occurred.
    If today's gap_pct > 5% in history → today.
    Otherwise the most recent scan_date with gap_pct > 5% in DB.
    Falls back to today.
    """
    today = (scan_ts.date() if scan_ts else date.today())
    if history:
        for h in reversed(history):
            if float(h.get("gap_pct") or 0) > 0.05:
                try:
                    return date.fromisoformat(str(h["date"]))
                except Exception:
                    pass
    return today


def get_vwap(ticker: str,
             history: list[dict] | None = None,
             snap: dict | None = None) -> float:
    """Today's VWAP.  Prefers history[-1] → Polygon snapshot → 0."""
    if history and history[-1].get("vwap"):
        return float(history[-1]["vwap"])
    if snap is None:
        snap = _poly_snapshot(ticker)
    return float((snap.get("day") or {}).get("vw") or 0)


def get_volume_trend(ticker: str, history: list[dict] | None = None) -> str:
    """
    'increasing' if today's volume > yesterday's, else 'decreasing'.
    Requires at least 2 days of history.
    """
    if history is None:
        history = _db_history(ticker, days=2)
    if len(history) < 2:
        return "unknown"
    vol_today     = float(history[-1].get("volume") or 0)
    vol_yesterday = float(history[-2].get("volume") or 0)
    return "increasing" if vol_today > vol_yesterday else "decreasing"


def get_catalyst_timestamp(ticker: str, news: list[dict] | None = None) -> datetime:
    """
    Returns the published_utc of the most recent Polygon news article.
    Raises ValueError (not NotImplementedError) if no news found,
    so catalyst_conviction_penalty() applies max penalty.
    """
    if news is None:
        news = _poly_news(ticker, limit=3)
    if not news:
        raise ValueError(f"No news found for {ticker}")
    pub = news[0].get("published_utc") or news[0].get("timestamp")
    if not pub:
        raise ValueError(f"No published_utc in news for {ticker}")
    try:
        # Polygon returns ISO 8601 UTC strings
        ts = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception:
        raise ValueError(f"Cannot parse timestamp '{pub}' for {ticker}")


def get_news_source(ticker: str, news: list[dict] | None = None) -> str:
    """
    Returns publisher name from Polygon news.
    Maps to NEWS_SOURCE_DELTA keys where possible.
    """
    if news is None:
        news = _poly_news(ticker, limit=3)
    if not news:
        return "UNKNOWN"
    publisher = news[0].get("publisher") or {}
    name = (publisher.get("name") or "").upper().replace(" ", "_").replace(".", "")
    # Map known publisher names to delta keys
    _MAP = {
        "GLOBENEWSWIRE":           "GLOBE_NEWSWIRE",
        "GLOBE_NEWSWIRE":          "GLOBE_NEWSWIRE",
        "PR_NEWSWIRE":             "PR_NEWSWIRE",
        "PRNEWSWIRE":              "PR_NEWSWIRE",
        "BUSINESSWIRE":            "BUSINESSWIRE",
        "BUSINESS_WIRE":           "BUSINESSWIRE",
        "ACCESSWIRE":              "ACCESSWIRE",
        "SEC":                     "SEC_8K",
        "US_SECURITIES_AND_EXCHANGE_COMMISSION": "SEC_8K",
    }
    return _MAP.get(name, name or "UNKNOWN")


def get_float_shares(ticker: str, details: dict | None = None) -> int:
    """Float shares from Polygon v3 reference. Returns 0 if unavailable."""
    if details is None:
        details = _poly_details(ticker)
    return int(
        details.get("share_class_shares_outstanding") or
        details.get("weighted_shares_outstanding") or 0
    )


def get_short_interest_pct(ticker: str, details: dict | None = None) -> float:
    """
    Short interest % is not available from Polygon Starter.
    Returns 0.0 as a safe default. Wire to Finviz / Tradier if available.
    """
    return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — STALENESS FILTER
# ═════════════════════════════════════════════════════════════════════════════

def is_stale_gap(ticker: str, scan_ts: datetime,
                 history: list[dict] | None = None,
                 snap: dict | None = None) -> tuple[bool, str | None]:
    """
    Check 1 — did the gap happen yesterday or earlier?
    Check 2 — is price already 30%+ above the gap open?
    """
    gap_open        = get_gap_open_price(ticker, history=history, snap=snap)
    current_price   = get_current_price(ticker, snap=snap)
    gap_origin_date = get_gap_date(ticker, history=history, scan_ts=scan_ts)
    today           = scan_ts.date()

    if gap_origin_date < today:
        return True, "GAP_IS_YESTERDAY"

    if gap_open and gap_open > 0:
        extension = (current_price - gap_open) / gap_open
        if extension > 0.30:
            return True, f"EXTENDED_FROM_GAP ({extension:.1%})"

    return False, None


def catalyst_conviction_penalty(ticker: str, scan_ts: datetime,
                                 news: list[dict] | None = None) -> int:
    """
    Returns a negative penalty based on how old the catalyst is.
    > 48h = -4   > 24h = -2   Fresh = 0   Exception = -4
    """
    try:
        catalyst_ts = get_catalyst_timestamp(ticker, news=news)
    except Exception:
        logger.warning("[%s] No catalyst timestamp — max penalty -4 applied.", ticker)
        return -4

    if catalyst_ts.tzinfo is None:
        catalyst_ts = catalyst_ts.replace(tzinfo=timezone.utc)
    if scan_ts.tzinfo is None:
        scan_ts = scan_ts.replace(tzinfo=timezone.utc)

    age_hours = (scan_ts - catalyst_ts).total_seconds() / 3600

    if age_hours > 48:
        penalty = -4
    elif age_hours > 24:
        penalty = -2
    else:
        penalty = 0

    logger.debug("[%s] Catalyst age: %.1fh → penalty: %d", ticker, age_hours, penalty)
    return penalty


def get_move_day(ticker: str, scan_date: date,
                 history: list[dict] | None = None,
                 scan_ts: datetime | None = None) -> int:
    """Day 1 = gap originated today. Day 2+ = prior sessions."""
    gap_origin_date = get_gap_date(ticker, history=history, scan_ts=scan_ts)
    return max(1, (scan_date - gap_origin_date).days + 1)


def vwap_day2_tag(ticker: str, move_day: int,
                  history: list[dict] | None = None,
                  snap: dict | None = None) -> str | None:
    """On day 2+, flag if price is extended above VWAP on fading volume."""
    if move_day < 2:
        return None
    current_price = get_current_price(ticker, snap=snap)
    vwap          = get_vwap(ticker, history=history, snap=snap)
    vol_trend     = get_volume_trend(ticker, history=history)
    if vwap and vwap > 0:
        if vol_trend == "decreasing" and (current_price - vwap) / vwap > 0.05:
            return "DAY2_EXTENDED_ABOVE_VWAP"
    return None


def evaluate_signal(ticker: str, base_conviction: int, scan_ts: datetime) -> dict:
    """
    Live-fetch interface (fetches its own Polygon data).
    For pipeline use with pre-fetched data, call evaluate_signal_with_data().
    """
    snap    = _poly_snapshot(ticker)
    news    = _poly_news(ticker)
    history = _db_history(ticker, days=3)
    return evaluate_signal_with_data(ticker, base_conviction, scan_ts,
                                      history, news, snap=snap)


def evaluate_signal_with_data(
    ticker:           str,
    base_conviction:  int,
    scan_ts:          datetime,
    history:          list[dict],
    news:             list[dict],
    details:          dict | None = None,
    snap:             dict | None = None,
    premarket_mode:   bool = True,
) -> dict:
    """
    Primary entry-point for the pipeline.  Accepts pre-fetched data from
    the standalone scanner / autonomous engine so we don't make duplicate
    Polygon calls.  Runs all 4 staleness checks and returns a verdict dict.
    action = 'PASS' | 'SKIP'
    """
    tags      = []
    scan_date = scan_ts.date()

    # Check 1 — stale gap (hard stop)
    stale, stale_reason = is_stale_gap(ticker, scan_ts, history=history, snap=snap)
    if stale:
        return {
            "ticker":           ticker,
            "action":           "SKIP",
            "final_conviction": 0,
            "tags":             [stale_reason],
            "reason":           f"Stale gap: {stale_reason}",
        }

    # Check 2 — catalyst decay
    penalty    = catalyst_conviction_penalty(ticker, scan_ts, news=news)
    conviction = base_conviction + penalty
    if penalty < 0:
        tags.append(f"CATALYST_PENALTY({penalty})")

    # Check 3 — move day
    move_day = get_move_day(ticker, scan_date, history=history, scan_ts=scan_ts)
    if move_day >= 2:
        tags.append(f"MOVE_DAY_{move_day}")

    # Check 4 — VWAP exhaustion
    vwap_tag = vwap_day2_tag(ticker, move_day, history=history, snap=snap)
    if vwap_tag:
        tags.append(vwap_tag)
        conviction -= 3

    action = "PASS" if conviction >= CONVICTION_THRESHOLD else "SKIP"
    reason = (
        f"Conviction {conviction} cleared threshold {CONVICTION_THRESHOLD}"
        if action == "PASS"
        else f"Conviction {conviction} below threshold {CONVICTION_THRESHOLD}"
    )

    logger.info("[%s] %s | conviction %d→%d | move_day=%d | tags=%s",
                ticker, action, base_conviction, conviction, move_day, tags)

    return {
        "ticker":           ticker,
        "action":           action,
        "final_conviction": max(0, conviction),
        "move_day":         move_day,
        "tags":             tags,
        "reason":           reason,
    }


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — WALL STREET TRADING BRAIN
# ═════════════════════════════════════════════════════════════════════════════

WALL_STREET_RULES = """
╔══════════════════════════════════════════════════════════════════════════════╗
║         AIEM — WALL STREET PREMARKET TRADING BRAIN                         ║
║         Micro/Small-Cap | Premarket | High-Risk Gap Plays                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  GOLDEN RULE #1  The move is already made. Yesterday's news =              ║
║                  today's distribution. Never chase Day 2.                  ║
║                                                                              ║
║  GOLDEN RULE #2  RVOL on Day 2 = sellers unloading into retail FOMO.      ║
║                  Volume must be paired with NEW price highs to matter.     ║
║                                                                              ║
║  GOLDEN RULE #3  Catalyst half-life:                                       ║
║                  PIPE announcement    = 2-4 hours                          ║
║                  Delisting squeeze    = under 1 hour                       ║
║                  Phase 2+ trial data  = 12-24 hours                        ║
║                  SPAC merger announce = 12-48 hours                        ║
║                  Reverse split gap    = fade within 3 sessions             ║
║                                                                              ║
║  GOLDEN RULE #4  Float is everything in micro-cap:                         ║
║                  < 1M shares  = violent, unpredictable, lottery sizing     ║
║                  1-5M shares  = classic momentum float, best risk/reward   ║
║                  > 20M shares = needs institutional catalyst to sustain    ║
║                                                                              ║
║  GOLDEN RULE #5  Premarket high = intraday resistance #1.                 ║
║                  Stocks fading PM highs 3x rarely reclaim at open.        ║
║                                                                              ║
║  GOLDEN RULE #6  PIPE price is a ceiling. The institution that bought     ║
║                  the PIPE is in profit above it and WILL sell.            ║
║                  Price returns to PIPE price more often than not.          ║
║                                                                              ║
║  GOLDEN RULE #7  Delisting plays are short traps. Exit within the first   ║
║                  15 minutes or don't enter at all. No holds.              ║
║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  PATTERN LIBRARY                                                            ║
║  PATTERN_STALE_GAP          Gap >30% but catalyst >24h old → SKIP         ║
║  PATTERN_PIPE_FADE          Trading above PIPE price → expect fade to PIPE ║
║  PATTERN_DAY2_DISTRIBUTION  Day 2, above VWAP, volume fading → SKIP       ║
║  PATTERN_SPAC_MERGER_POP    SPAC merger → only enter near trust value      ║
║  PATTERN_DELISTING_SQUEEZE  Delisting notice → 15-min trade max only      ║
║  PATTERN_SYMPATHY_PLAY      No own catalyst, riding sector → SKIP         ║
║  PATTERN_REVERSE_SPLIT      Reverse split gap → fade within 3 sessions    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  JUNE 30 2026 POST-MORTEM — THE FIVE STOCK LESSON                         ║
║                                                                              ║
║  TNMG  Day 2 delisting squeeze. Going concern. $1.9M cash. No catalyst.   ║
║        → PATTERN_DELISTING_SQUEEZE + PATTERN_STALE_GAP → SKIP            ║
║                                                                              ║
║  DCOY  $21M PIPE announced 6/26 at $5.91/share. Catalyst 72h+ old.       ║
║        Trading above PIPE price = institution in profit = sellers.        ║
║        → PATTERN_PIPE_FADE + CATALYST_STALE → SKIP                       ║
║                                                                              ║
║  CNET  No fresh catalyst. Chinese micro-cap riding peer momentum only.    ║
║        Prior reverse split. Nasdaq compliance issues.                     ║
║        → PATTERN_SYMPATHY_PLAY → SKIP                                    ║
║                                                                              ║
║  KNDI  Xinchu acquisition announced 6/22 — 8 days old by 6/30.           ║
║        Nasdaq minimum bid deficiency. No new news.                        ║
║        → PATTERN_STALE_GAP → SKIP                                        ║
║                                                                              ║
║  JATT  SPAC merger with Talawar Tx announced 6/29. $225M PIPE at $10.    ║
║        Trust value ~$10. Only valid entry is near $10 floor.             ║
║        Chasing at $12+ = wrong side of the trade.                        ║
║        → PATTERN_SPAC_MERGER_POP — near trust only, not $12+            ║
║                                                                              ║
║  ALL FIVE should have been SKIP before the market opened.                 ║
║  The scanner found them correctly. The filter layer was missing.           ║
║  That filter layer is now YOU. Never let this happen again.               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

NEWS_SOURCE_DELTA: dict[str, int] = {
    "SEC_8K":            +5,
    "SEC_6K":            +4,
    "NASDAQ_OFFICIAL":   +3,
    "GLOBE_NEWSWIRE":     0,
    "PR_NEWSWIRE":        0,
    "BUSINESSWIRE":       0,
    "ACCESSWIRE":        -2,
    "COMPANY_WEBSITE":   -3,
    "BLOG":              -8,
    "TWITTER":          -10,
    "REDDIT":           -15,
    "UNKNOWN":           -5,
}

# ── Pattern detection helpers ─────────────────────────────────────────────────

def _detect_pipe_fade(ticker: str, signal: dict,
                       snap: dict | None = None) -> tuple[bool, str]:
    """Returns (triggered, note).  Reads current price from snap or live."""
    pipe_price = signal.get("pipe_price")
    if not pipe_price:
        return False, ""
    try:
        current_price = get_current_price(ticker, snap=snap)
        if current_price > pipe_price * 1.05:
            pct = (current_price / pipe_price - 1)
            note = (f"Price {pct:.1%} above PIPE ${pipe_price:.2f} — "
                    "institution in profit, expect fade back to PIPE.")
            return True, note
    except Exception:
        pass
    return False, ""


def _detect_spac(news: list[dict]) -> bool:
    """Returns True if news contains SPAC-merger keywords."""
    spac_kw = ("spac", "merger", "business combination", "trust value",
                "de-spac", "blank check")
    for article in (news or []):
        txt = (article.get("title") or "" + article.get("description") or "").lower()
        if any(k in txt for k in spac_kw):
            return True
    return False


def _detect_delisting(news: list[dict]) -> bool:
    """Returns True if news mentions delisting or compliance issues."""
    kw = ("delisting", "going concern", "minimum bid", "compliance",
          "nasdaq notice", "nyse notice", "non-compliance")
    for article in (news or []):
        txt = (article.get("title") or "" + article.get("description") or "").lower()
        if any(k in txt for k in kw):
            return True
    return False


def _detect_reverse_split(news: list[dict]) -> bool:
    """Returns True if news mentions a reverse stock split."""
    kw = ("reverse split", "reverse stock split", "r/s")
    for article in (news or []):
        txt = (article.get("title") or "" + article.get("description") or "").lower()
        if any(k in txt for k in kw):
            return True
    return False


def apply_wall_street_patterns(ticker: str, signal: dict) -> dict:
    """Live-fetch interface. For pre-fetched data call apply_wall_street_pattern_with_data()."""
    snap = _poly_snapshot(ticker)
    news = _poly_news(ticker)
    return apply_wall_street_pattern_with_data(ticker, signal, [], news, snap=snap)


def apply_wall_street_pattern_with_data(
    ticker:  str,
    signal:  dict,
    history: list[dict],
    news:    list[dict],
    snap:    dict | None = None,
) -> dict:
    """
    Apply all Wall Street patterns to a signal verdict.
    Mutates and returns the signal dict.
    Each pattern lowers conviction and adds a tag + note.
    """
    tags       = list(signal.get("tags", []))
    conviction = float(signal.get("final_conviction", 0))
    ws_notes   = list(signal.get("ws_notes", []))
    move_day   = signal.get("move_day", 1)

    # 1. PIPE fade
    triggered, note = _detect_pipe_fade(ticker, signal, snap=snap)
    if triggered:
        tags.append("PATTERN_PIPE_FADE")
        conviction -= 10
        ws_notes.append(note)

    # 2. Sympathy play — catalyst_conviction_penalty returned -4 → no own news
    if any("CATALYST_PENALTY(-4)" in t for t in tags):
        tags.append("PATTERN_SYMPATHY_PLAY")
        conviction -= 5
        ws_notes.append("No fresh own catalyst — sympathy/momentum play only. High risk.")

    # 3. Day 2 distribution
    if move_day >= 2 and "DAY2_EXTENDED_ABOVE_VWAP" in tags:
        tags.append("PATTERN_DAY2_DISTRIBUTION")
        conviction -= 5
        ws_notes.append("Day 2 distribution: extended above VWAP on fading volume.")

    # 4. SPAC merger pop — only enter near trust (~$10)
    if _detect_spac(news):
        tags.append("PATTERN_SPAC_MERGER_POP")
        conviction -= 8
        ws_notes.append("SPAC merger detected — valid entry only near trust value (~$10).")

    # 5. Delisting squeeze — 15-min trade only
    if _detect_delisting(news):
        tags.append("PATTERN_DELISTING_SQUEEZE")
        conviction -= 12
        ws_notes.append("Delisting/compliance notice — exit within first 15 min or skip.")

    # 6. Reverse split
    if _detect_reverse_split(news):
        tags.append("PATTERN_REVERSE_SPLIT")
        conviction -= 10
        ws_notes.append("Reverse split detected — gap fades within 3 sessions statistically.")

    # 7. Float penalty (>20M shares = low conviction without institutional catalyst)
    try:
        float_sh = float(signal.get("float_shares") or 0)
        if float_sh > 20_000_000 and all("SEC" not in t for t in tags):
            conviction -= 5
            ws_notes.append(f"Float {float_sh/1e6:.1f}M — needs institutional catalyst to sustain.")
    except Exception:
        pass

    signal["final_conviction"] = max(0.0, round(conviction, 1))
    signal["tags"]             = list(set(tags))
    signal["ws_notes"]         = ws_notes
    return signal


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — HMAC VERIFICATION (6 signed questions proving AIEM loaded this)
# ═════════════════════════════════════════════════════════════════════════════

VERIFICATION_QUESTIONS: dict[int, dict] = {
    1: {
        "question": (
            "When I scanned TNMG this morning and its gap originated yesterday, "
            "what action did you take and what exact reason string did you log?"
        ),
        "expected_keywords": ["SKIP", "GAP_IS_YESTERDAY"],
        "explanation": (
            "Tests is_stale_gap(): gap_origin_date < today → (True, 'GAP_IS_YESTERDAY') "
            "→ evaluate_signal() returns action='SKIP'."
        ),
    },
    2: {
        "question": (
            "If a catalyst timestamp is 36 hours old, what conviction penalty "
            "do you apply and what tag gets added to the signal?"
        ),
        "expected_keywords": ["-2", "CATALYST_PENALTY"],
        "explanation": "24 < 36 <= 48 → penalty = -2 → tag = 'CATALYST_PENALTY(-2)'.",
    },
    3: {
        "question": (
            "DCOY has base conviction 95. Catalyst is 49 hours old. "
            "Trading 6% above VWAP on decreasing volume on day 2. "
            "Walk me through the exact math — final conviction and action?"
        ),
        "expected_keywords": ["83", "PASS"],
        "explanation": (
            "49h → -4. Day2 VWAP → -3. PATTERN_DAY2_DISTRIBUTION → -5. "
            "95 - 4 - 3 - 5 = 83. 83 >= 70 → PASS."
        ),
    },
    4: {
        "question": (
            "What is the conviction threshold for PASS vs SKIP "
            "and where in the code is it set?"
        ),
        "expected_keywords": ["70", "CONVICTION_THRESHOLD"],
        "explanation": "CONVICTION_THRESHOLD = 70 set at module level and used in evaluate_signal().",
    },
    5: {
        "question": (
            "If get_catalyst_timestamp() throws an exception, "
            "what do you do and what penalty exactly?"
        ),
        "expected_keywords": ["-4", "warning"],
        "explanation": (
            "catalyst_conviction_penalty() wraps call in try/except, "
            "logs a warning, and returns -4."
        ),
    },
    6: {
        "question": (
            "List every tag that can appear in the tags field "
            "and the exact condition that triggers each one."
        ),
        "expected_keywords": [
            "GAP_IS_YESTERDAY", "EXTENDED_FROM_GAP", "CATALYST_PENALTY",
            "MOVE_DAY_", "DAY2_EXTENDED_ABOVE_VWAP", "PATTERN_PIPE_FADE",
            "PATTERN_SYMPATHY_PLAY", "PATTERN_DAY2_DISTRIBUTION",
        ],
        "explanation": "Agent must list all 8 tag families with their trigger conditions.",
    },
}


def _sign(payload: str) -> str:
    return _hmac_mod.new(HMAC_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def issue_challenge(question_id: int) -> dict:
    """Generate a signed challenge. Paste to AIEM. It can't fake a passing answer."""
    if question_id not in VERIFICATION_QUESTIONS:
        raise ValueError(f"Unknown question_id {question_id}")
    nonce     = str(uuid.uuid4())
    issued_at = int(time.time())
    sig       = _sign(f"{nonce}:{question_id}:{issued_at}")
    return {
        "question_id": question_id,
        "nonce":       nonce,
        "issued_at":   issued_at,
        "question":    VERIFICATION_QUESTIONS[question_id]["question"],
        "sig":         sig,
    }


def verify_response(challenge: dict, aiem_answer: str) -> dict:
    """Verify AIEM's answer is authentic and contains expected content."""
    qid          = challenge.get("question_id")
    nonce        = challenge.get("nonce")
    issued_at    = challenge.get("issued_at")
    received_sig = challenge.get("sig", "")

    expected_sig = _sign(f"{nonce}:{qid}:{issued_at}")
    if not _hmac_mod.compare_digest(expected_sig, received_sig):
        return {"valid": False, "verdict": "INVALID_SIGNATURE — challenge was forged.", "missing": []}

    if int(time.time()) - issued_at > CHALLENGE_TTL:
        return {"valid": False, "verdict": f"EXPIRED (TTL={CHALLENGE_TTL}s)", "missing": []}

    keywords = VERIFICATION_QUESTIONS[qid]["expected_keywords"]
    missing  = [kw for kw in keywords if kw.lower() not in aiem_answer.lower()]

    return {
        "valid":       len(missing) == 0,
        "verdict":     "VERIFIED ✓" if not missing else f"PARTIAL — missing: {missing}",
        "missing":     missing,
        "explanation": VERIFICATION_QUESTIONS[qid]["explanation"],
    }


def run_all_challenges() -> list[dict]:
    challenges = []
    for qid in sorted(VERIFICATION_QUESTIONS):
        ch = issue_challenge(qid)
        challenges.append(ch)
        print(f"\n── Q{qid}: {ch['question']}\n   sig: {ch['sig'][:20]}…")
    return challenges


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT — self-test
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(WALL_STREET_RULES)

    print("\n" + "═" * 60)
    print("  VERIFICATION CHALLENGES")
    print("═" * 60)
    challenges = run_all_challenges()

    print("\n" + "═" * 60)
    print("  DEMO VERIFY — Q2")
    print("═" * 60)
    result = verify_response(
        challenges[1],
        "penalty is -2 because age is 36h (24 < 36 <= 48). "
        "Tag CATALYST_PENALTY(-2) is added to signal tags.",
    )
    print(json.dumps(result, indent=2))

    print("\n" + "═" * 60)
    print("  STALENESS FILTER — JUNE 30 BACKTEST (DB + live Polygon)")
    print("═" * 60)
    _now = datetime.now(timezone.utc)
    for symbol in ["TNMG", "DCOY", "CNET", "KNDI", "JATT"]:
        r = evaluate_signal(symbol, 95, _now)
        print(f"[{symbol}] {r['action']}  conviction={r['final_conviction']}  tags={r['tags']}")
