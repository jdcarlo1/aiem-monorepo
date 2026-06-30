"""
aiem_intelligence_upgrade.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AIEM Intelligence Upgrade — All 6 systems wired to real DB / Polygon data.

  1. End-of-day feedback loop   — win rate, missed opportunities, loser tags
  2. Daily loss limit / kill switch — flag file + session P&L from DB
  3. News source quality scoring — SEC 8-K → +5; Reddit → −15
  4. Sector heat awareness       — polygon_market_daily ETF change penalty
  5. Time-of-day rules           — Danger Zone (−20), Midday Chop (−10), etc.
  6. Float + short interest      — float category, squeeze candidate (+8)

Public entry point: master_evaluate_signal_with_data()
  — runs staleness_filter → WS patterns → all 6 systems and returns a full
    verdict dict with action, final_conviction, tags, notes, and sizing guide.
"""

import logging
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    _ET_TZ = ZoneInfo("America/New_York")
except ImportError:
    import pytz
    _ET_TZ = pytz.timezone("America/New_York")


# ═════════════════════════════════════════════════════════════════════════════
# SYSTEM 1 — END-OF-DAY FEEDBACK LOOP
# ═════════════════════════════════════════════════════════════════════════════

class SignalOutcome(Enum):
    WINNER  = "WINNER"
    LOSER   = "LOSER"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


def _db_get_past_signals(conn, lookback_days: int = 30) -> list[dict]:
    """
    Return graded AIEM predictions from the past N days.
    Uses aiem_predictions + aiem_prediction_outcomes tables.
    """
    try:
        cur = conn.cursor()
        since = date.today() - timedelta(days=lookback_days)
        cur.execute("""
            SELECT p.ticker, p.prediction_date, p.confidence_score, p.signal_basis,
                   o.entry_price, o.t1_return
            FROM aiem_predictions p
            JOIN aiem_prediction_outcomes o
              ON p.ticker = o.ticker AND p.prediction_date = o.prediction_date
            WHERE p.prediction_date >= %s
            ORDER BY p.prediction_date DESC
        """, (since,))
        rows = cur.fetchall()
        return [
            {
                "ticker":       r[0],
                "signal_date":  r[1],
                "conf":         float(r[2] or 0),
                "signal_basis": r[3] or "",
                "entry_price":  float(r[4] or 0),
                "t1_return":    float(r[5] or 0),
                "action":       "PASS",   # only PASS signals get inserted
                "tags":         [],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("[EOD] Could not fetch past signals: %s", e)
        return []


def _db_get_session_pnl(conn, session_date: date) -> float:
    """
    Returns today's approximate session P&L as sum of t1_return × entry_price.
    Returns 0.0 if nothing graded yet today.
    """
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(t1_return * NULLIF(entry_price, 0) / 100.0), 0.0)
            FROM aiem_prediction_outcomes
            WHERE prediction_date = %s AND entry_price > 0
        """, (session_date,))
        row = cur.fetchone()
        return float(row[0] or 0)
    except Exception:
        return 0.0


def run_eod_feedback_loop_with_data(conn, lookback_days: int = 30) -> dict:
    """
    Run after market close.  Pulls graded AIEM predictions, returns stats.
    AIEM appends these stats to its nightly self-analysis Telegram message.
    """
    signals = _db_get_past_signals(conn, lookback_days)

    winners  = [s for s in signals if s["t1_return"] >  2.0]
    losers   = [s for s in signals if s["t1_return"] < -2.0]
    win_rate = len(winners) / max(len(signals), 1)

    # Signals that appeared most on losing trades
    loser_tags: dict[str, int] = {}
    for s in losers:
        for tag in (s.get("signal_basis") or "").split(","):
            tag = tag.strip()
            if tag:
                loser_tags[tag] = loser_tags.get(tag, 0) + 1

    summary = {
        "period_days":    lookback_days,
        "total":          len(signals),
        "winners":        len(winners),
        "losers":         len(losers),
        "win_rate":       round(win_rate, 3),
        "top_loser_tags": sorted(loser_tags.items(), key=lambda x: -x[1])[:5],
    }
    logger.info(
        "[EOD] %d-day WR %.1f%% | W=%d L=%d | top loser tags: %s",
        lookback_days, win_rate * 100, len(winners), len(losers),
        [t for t, _ in summary["top_loser_tags"]],
    )
    return summary


# ═════════════════════════════════════════════════════════════════════════════
# SYSTEM 2 — DAILY LOSS LIMIT / KILL SWITCH
# ═════════════════════════════════════════════════════════════════════════════

DAILY_LOSS_LIMIT_USD       = -500.0
OBSERVATION_ONLY_FILENAME  = "/tmp/aiem_observation_only.flag"


def is_kill_switch_active(conn, session_date: date) -> tuple[bool, str]:
    """Returns (True, reason) when AIEM should stop trading for the session."""
    if os.path.exists(OBSERVATION_ONLY_FILENAME):
        return True, "MANUAL_KILL_SWITCH_FLAG"
    pnl = _db_get_session_pnl(conn, session_date)
    if pnl <= DAILY_LOSS_LIMIT_USD:
        reason = (f"DAILY_LOSS_LIMIT_HIT — session P&L ${pnl:.2f} "
                  f"reached limit ${DAILY_LOSS_LIMIT_USD:.2f}")
        logger.warning("[KILL SWITCH] %s", reason)
        return True, reason
    return False, ""


def activate_manual_kill_switch() -> None:
    with open(OBSERVATION_ONLY_FILENAME, "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())
    logger.warning("[KILL SWITCH] ACTIVATED — observation-only mode.")


def deactivate_kill_switch() -> None:
    if os.path.exists(OBSERVATION_ONLY_FILENAME):
        os.remove(OBSERVATION_ONLY_FILENAME)
        logger.info("[KILL SWITCH] Deactivated — new session begins.")


def get_kill_switch_status() -> dict:
    return {
        "active":   os.path.exists(OBSERVATION_ONLY_FILENAME),
        "flag_file": OBSERVATION_ONLY_FILENAME,
        "daily_loss_limit": DAILY_LOSS_LIMIT_USD,
    }


# ═════════════════════════════════════════════════════════════════════════════
# SYSTEM 3 — NEWS SOURCE QUALITY SCORING
# ═════════════════════════════════════════════════════════════════════════════

NEWS_SOURCE_SCORES: dict[str, int] = {
    "SEC_8K":           10,
    "SEC_10Q":          10,
    "SEC_10K":          10,
    "SEC_6K":            9,
    "NASDAQ_OFFICIAL":   8,
    "GLOBE_NEWSWIRE":    6,
    "PR_NEWSWIRE":       6,
    "BUSINESSWIRE":      6,
    "ACCESSWIRE":        5,
    "COMPANY_WEBSITE":   4,
    "BLOG":              2,
    "TWITTER":           1,
    "REDDIT":            0,
    "UNKNOWN":           0,
}

NEWS_SOURCE_CONVICTION_DELTA: dict[str, int] = {
    "SEC_8K":           +5,
    "SEC_6K":           +4,
    "NASDAQ_OFFICIAL":  +3,
    "GLOBE_NEWSWIRE":    0,
    "PR_NEWSWIRE":       0,
    "BUSINESSWIRE":      0,
    "ACCESSWIRE":       -2,
    "COMPANY_WEBSITE":  -3,
    "BLOG":             -8,
    "TWITTER":         -10,
    "REDDIT":          -15,
    "UNKNOWN":          -5,
}

# Map Polygon publisher names to source categories
_PUBLISHER_NAME_MAP: dict[str, str] = {
    "pr newswire":       "PR_NEWSWIRE",
    "prnewswire":        "PR_NEWSWIRE",
    "globe newswire":    "GLOBE_NEWSWIRE",
    "globenewswire":     "GLOBE_NEWSWIRE",
    "businesswire":      "BUSINESSWIRE",
    "business wire":     "BUSINESSWIRE",
    "accesswire":        "ACCESSWIRE",
    "access wire":       "ACCESSWIRE",
    "nasdaq":            "NASDAQ_OFFICIAL",
    "motley fool":       "BLOG",
    "seeking alpha":     "BLOG",
    "benzinga":          "BLOG",
    "yahoo finance":     "BLOG",
    "marketwatch":       "BLOG",
    "investorplace":     "BLOG",
    "barron":            "BLOG",
    "thestreet":         "BLOG",
    "reddit":            "REDDIT",
    "twitter":           "TWITTER",
    "stocktwits":        "TWITTER",
    "x.com":             "TWITTER",
}

_SEC_FILING_RE = re.compile(r'sec\.gov|edgar\.sec', re.IGNORECASE)
_SEC_TYPE_RE   = re.compile(r'\b(8-K|10-Q|10-K|6-K)\b', re.IGNORECASE)


def _classify_news_source(news: list) -> str:
    """
    Classify the primary news source from Polygon news items.
    Checks article_url for SEC EDGAR first, then publisher.name.
    """
    for item in (news or []):
        url   = item.get("article_url", "")
        title = item.get("title", "")

        # SEC EDGAR URL → classify by filing type
        if _SEC_FILING_RE.search(url):
            m = _SEC_TYPE_RE.search(title)
            if m:
                return f"SEC_{m.group(1).replace('-', '').upper()}"
            return "SEC_8K"   # default to 8-K

        # Publisher name lookup
        pub = item.get("publisher") or {}
        pub_name = (pub.get("name") or "").lower().strip()
        for key, source in _PUBLISHER_NAME_MAP.items():
            if key in pub_name:
                return source

    return "UNKNOWN"


def score_news_source_with_data(news: list) -> tuple[int, str, int]:
    """Returns (quality_score 0-10, source_name, conviction_delta)."""
    source = _classify_news_source(news)
    score  = NEWS_SOURCE_SCORES.get(source, 0)
    delta  = NEWS_SOURCE_CONVICTION_DELTA.get(source, -5)
    logger.info("[NEWS SOURCE] %s  score=%d  delta=%+d", source, score, delta)
    return score, source, delta


# ═════════════════════════════════════════════════════════════════════════════
# SYSTEM 4 — SECTOR HEAT AWARENESS
# ═════════════════════════════════════════════════════════════════════════════

SECTOR_ETF_MAP: dict[str, str] = {
    "biotech":       "XBI",
    "healthcare":    "XLV",
    "technology":    "QQQ",
    "semiconductor": "SOXX",
    "energy":        "XLE",
    "financials":    "XLF",
    "consumer":      "XLY",
    "utilities":     "XLU",
    "materials":     "XLB",
    "real_estate":   "IYR",
    "spac":          "IWM",   # SPAK not in polygon_market_daily; fallback
    "micro_cap":     "IWC",
    "small_cap":     "IWM",
}

SECTOR_PENALTY_RULES: list[tuple[float, int, str]] = [
    (-0.05, -15, "SECTOR_COLLAPSE"),
    (-0.03,  -8, "SECTOR_WEAK"),
    (-0.015, -4, "SECTOR_SOFT"),
    (-0.005, -2, "SECTOR_SLIGHTLY_SOFT"),
]

# SIC description keywords → internal sector name
_SIC_SECTOR_MAP: list[tuple[list[str], str]] = [
    (["biological", "pharmaceutical", "drug", "biotech",
      "therapeutics", "diagnostic substance", "in-vitro"],       "biotech"),
    (["hospital", "health service", "physician",
      "nursing", "dental", "home health care", "surgical"],      "healthcare"),
    (["semiconductor", "integrated circuit",
      "electronic component", "printed circuit"],                "semiconductor"),
    (["computer", "software", "prepackaged", "internet",
      "data processing", "information retriev"],                  "technology"),
    (["petroleum", "crude oil", "natural gas",
      "electric service", "power generation", "coal"],            "energy"),
    (["bank", "savings institution", "credit union",
      "insurance", "investment", "security dealer",
      "mortgage", "mutual fund"],                                 "financials"),
    (["retail store", "apparel", "restaurant",
      "food store", "department store", "auto dealer",
      "furniture", "specialty retail"],                           "consumer"),
    (["real estate", "land subdivision",
      "building contractor", "operative builder"],                "real_estate"),
    (["mining", "metal service", "chemical",
      "paper mill", "forest product", "steel works",
      "fabricated metal"],                                        "materials"),
]


def _get_sector_from_details(details: dict) -> str:
    """Map Polygon v3 sic_description / description → internal sector name."""
    sic_desc = (details.get("sic_description") or "").lower()
    desc     = (details.get("description")     or "").lower()
    combined = sic_desc + " " + desc
    for keywords, sector in _SIC_SECTOR_MAP:
        if any(kw in combined for kw in keywords):
            return sector
    return "small_cap"    # default: use IWM as sector proxy


def _get_sector_etf_change_with_data(sector: str, conn) -> float | None:
    """
    Fetch today's (most recent) ETF daily % change from polygon_market_daily.
    Returns None if ETF not found.
    """
    etf = SECTOR_ETF_MAP.get(sector.lower(), "IWM")
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT close_price, prev_close
            FROM polygon_market_daily
            WHERE ticker = %s
              AND scan_date = (SELECT MAX(scan_date) FROM polygon_market_daily)
        """, (etf,))
        row = cur.fetchone()
        if row and row[0] and row[1] and row[1] > 0:
            return (row[0] - row[1]) / row[1]
        return None
    except Exception as e:
        logger.warning("[SECTOR] ETF query error for %s: %s", etf, e)
        return None


def get_sector_conviction_penalty_with_data(
    details: dict,
    conn,
) -> tuple[int, str]:
    """Returns (conviction_penalty, tag)."""
    sector     = _get_sector_from_details(details)
    etf_change = _get_sector_etf_change_with_data(sector, conn)

    if etf_change is None:
        return 0, "SECTOR_DATA_UNAVAILABLE"

    etf_symbol = SECTOR_ETF_MAP.get(sector, "IWM")
    for threshold, penalty, tag in SECTOR_PENALTY_RULES:
        if etf_change <= threshold:
            logger.info(
                "[SECTOR] sector=%s etf=%s change=%.2f%% penalty=%d tag=%s",
                sector, etf_symbol, etf_change * 100, penalty, tag,
            )
            return penalty, tag

    return 0, "SECTOR_NEUTRAL"


# ═════════════════════════════════════════════════════════════════════════════
# SYSTEM 5 — TIME-OF-DAY RULES
# ═════════════════════════════════════════════════════════════════════════════

MARKET_OPEN      = time(9,  30)
DANGER_ZONE_END  = time(9,  45)
MORNING_END      = time(10, 30)
MIDDAY_START     = time(11, 30)
MIDDAY_END       = time(13, 30)
AFTERNOON_START  = time(14,  0)
POWER_HOUR       = time(15,  0)


class TimeOfDayZone(Enum):
    PREMARKET        = "PREMARKET"
    DANGER_ZONE      = "DANGER_ZONE"
    POWER_HOUR_OPEN  = "POWER_HOUR_OPEN"
    MIDDAY_CHOP      = "MIDDAY_CHOP"
    AFTERNOON        = "AFTERNOON"
    POWER_HOUR_CLOSE = "POWER_HOUR_CLOSE"
    AFTER_HOURS      = "AFTER_HOURS"


TIME_OF_DAY_RULES: dict[TimeOfDayZone, dict] = {
    TimeOfDayZone.PREMARKET: {
        "conviction_delta": 0,
        "note":             "Premarket — scanner runs, no execution until open.",
        "allow_entry":      False,
        "override_threshold": 70,
    },
    TimeOfDayZone.DANGER_ZONE: {
        "conviction_delta": -20,
        "note":             ("DANGER ZONE 9:30-9:45 — Day 2 gap traps. "
                             "Retail FOMO peak. Wide spreads. Halts likely. "
                             "Need conviction ≥ 85 to enter."),
        "allow_entry":      True,
        "override_threshold": 85,
    },
    TimeOfDayZone.POWER_HOUR_OPEN: {
        "conviction_delta": 0,
        "note":             "9:45-10:30 — Safer entry after initial volatility.",
        "allow_entry":      True,
        "override_threshold": 70,
    },
    TimeOfDayZone.MIDDAY_CHOP: {
        "conviction_delta": -10,
        "note":             ("MIDDAY CHOP 11:30-1:30 — Low volume, wide spreads. "
                             "Fake moves. Avoid new entries."),
        "allow_entry":      False,
        "override_threshold": 80,
    },
    TimeOfDayZone.AFTERNOON: {
        "conviction_delta": -5,
        "note":             "2:00-3:00 PM — Reduced activity. High-conviction only.",
        "allow_entry":      True,
        "override_threshold": 75,
    },
    TimeOfDayZone.POWER_HOUR_CLOSE: {
        "conviction_delta": +5,
        "note":             "3:00-4:00 PM — Power hour. Momentum can resume.",
        "allow_entry":      True,
        "override_threshold": 70,
    },
    TimeOfDayZone.AFTER_HOURS: {
        "conviction_delta": 0,
        "note":             "After hours — no execution. Scanner and learning only.",
        "allow_entry":      False,
        "override_threshold": 70,
    },
}


def get_time_of_day_zone(dt: datetime) -> TimeOfDayZone:
    """Classify UTC datetime into a trading zone (converts to Eastern)."""
    try:
        et = dt.astimezone(_ET_TZ)
        t  = et.time()
    except Exception:
        t  = dt.time()   # fallback: assume already ET

    if t < MARKET_OPEN:       return TimeOfDayZone.PREMARKET
    if t < DANGER_ZONE_END:   return TimeOfDayZone.DANGER_ZONE
    if t < MORNING_END:       return TimeOfDayZone.POWER_HOUR_OPEN
    if t < MIDDAY_START:      return TimeOfDayZone.POWER_HOUR_OPEN
    if t < MIDDAY_END:        return TimeOfDayZone.MIDDAY_CHOP
    if t < POWER_HOUR:        return TimeOfDayZone.AFTERNOON
    if t <= time(16, 0):      return TimeOfDayZone.POWER_HOUR_CLOSE
    return TimeOfDayZone.AFTER_HOURS


def apply_time_of_day(
    conviction: float,
    scan_dt: datetime,
    premarket_mode: bool = False,
) -> tuple[float, str, bool, int]:
    """
    Returns (adjusted_conviction, zone_name, allow_entry, threshold).
    premarket_mode=True: never blocks on allow_entry=False (we're building
    the watchlist, not executing a trade right now).
    """
    zone  = get_time_of_day_zone(scan_dt)
    rules = TIME_OF_DAY_RULES[zone]

    adjusted  = conviction + rules["conviction_delta"]
    allow     = True if premarket_mode else rules["allow_entry"]
    threshold = rules.get("override_threshold", 70)

    logger.info(
        "[TIME] zone=%s delta=%+d conviction=%.0f→%.0f allow=%s",
        zone.value, rules["conviction_delta"], conviction, adjusted, allow,
    )
    return max(0.0, adjusted), zone.value, allow, threshold


# ═════════════════════════════════════════════════════════════════════════════
# SYSTEM 6 — FLOAT + SHORT INTEREST INTELLIGENCE
# ═════════════════════════════════════════════════════════════════════════════

class FloatCategory(Enum):
    ULTRA_LOW = "ULTRA_LOW"   # < 1M shares
    LOW       = "LOW"         # 1-5M shares
    MEDIUM    = "MEDIUM"      # 5-20M shares
    HIGH      = "HIGH"        # 20-100M shares
    VERY_HIGH = "VERY_HIGH"   # > 100M shares
    UNKNOWN   = "UNKNOWN"


def _classify_float(float_shares: int) -> FloatCategory:
    if float_shares <= 0:         return FloatCategory.UNKNOWN
    if float_shares < 1_000_000:  return FloatCategory.ULTRA_LOW
    if float_shares < 5_000_000:  return FloatCategory.LOW
    if float_shares < 20_000_000: return FloatCategory.MEDIUM
    if float_shares < 100_000_000:return FloatCategory.HIGH
    return FloatCategory.VERY_HIGH


FLOAT_CONVICTION_RULES: dict[FloatCategory, dict] = {
    FloatCategory.ULTRA_LOW: {
        "delta":            -10,
        "max_position_pct":  0.005,
        "note": "ULTRA-LOW FLOAT (<1M) — violent moves. Halts likely. Lottery sizing.",
    },
    FloatCategory.LOW: {
        "delta":            +5,
        "max_position_pct":  0.02,
        "note": "LOW FLOAT (1-5M) — classic momentum float. Respect halts.",
    },
    FloatCategory.MEDIUM: {
        "delta":             0,
        "max_position_pct":  0.03,
        "note": "MEDIUM FLOAT (5-20M) — tradeable. Standard sizing.",
    },
    FloatCategory.HIGH: {
        "delta":            -5,
        "max_position_pct":  0.04,
        "note": "HIGH FLOAT (20-100M) — needs strong institutional catalyst.",
    },
    FloatCategory.VERY_HIGH: {
        "delta":           -15,
        "max_position_pct":  0.05,
        "note": "VERY HIGH FLOAT (>100M) — micro-cap scanner should rarely see this.",
    },
    FloatCategory.UNKNOWN: {
        "delta":             0,
        "max_position_pct":  0.02,
        "note": "Float unknown — default sizing.",
    },
}


def get_float_and_si_with_data(details: dict) -> dict:
    """
    Float from Polygon v3 reference (share_class_shares_outstanding).
    Short interest: not available without paid feed — returns 0.0 with a note.
    """
    # Polygon v3 /reference/tickers/{ticker} → results.share_class_shares_outstanding
    float_shares = int(
        details.get("share_class_shares_outstanding") or
        details.get("weighted_shares_outstanding")    or 0
    )
    si_pct = 0.0   # SI requires Yahoo/paid feed; noted in memory as limitation

    float_cat = _classify_float(float_shares)
    rules     = FLOAT_CONVICTION_RULES[float_cat]

    # Squeeze candidate: high SI + low float. Can't reliably detect without SI.
    # Mark as candidate if float < 2M regardless (thin float = squeeze potential).
    squeeze_candidate = float_shares > 0 and float_shares < 2_000_000
    squeeze_delta     = +8 if squeeze_candidate else 0

    return {
        "float_shares":       float_shares,
        "float_category":     float_cat.value,
        "short_interest_pct": si_pct,
        "conviction_delta":   rules["delta"] + squeeze_delta,
        "max_position_pct":   rules["max_position_pct"],
        "squeeze_candidate":  squeeze_candidate,
        "note":               rules["note"],
    }


# ═════════════════════════════════════════════════════════════════════════════
# AIEM TRADING INSTRUCTIONS — embedded so AIEM has them in every context
# ═════════════════════════════════════════════════════════════════════════════

AIEM_TRADING_INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════════════════════╗
║          AIEM — COMPLETE INTELLIGENCE INSTRUCTIONS v2.0                    ║
║          Micro/Small-Cap | Premarket | High-Risk Gap Scanner               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  SYSTEM 1 — END-OF-DAY FEEDBACK LOOP                                       ║
║  After market close, run_eod_feedback_loop_with_data() is called.          ║
║  If pass win rate drops below 40%, raise staleness penalties.              ║
║  If MOVE_DAY_2 leads loser tags, the filter needs tightening.              ║
║                                                                              ║
║  SYSTEM 2 — KILL SWITCH RULES                                              ║
║  is_kill_switch_active() checked before every signal evaluation.           ║
║  Session P&L hits -$500 → BLOCKED. Observe only.                          ║
║  Kill switch resets at start of each new session automatically.            ║
║                                                                              ║
║  SYSTEM 3 — NEWS SOURCE QUALITY                                            ║
║  SEC 8-K = +5. Reddit/Twitter = near-disqualifying (-10 to -15).          ║
║  A gap on a paid PR wire with no SEC filing = yellow flag.                 ║
║                                                                              ║
║  SYSTEM 4 — SECTOR HEAT                                                    ║
║  Never fight the sector. XBI down 4% = no biotech longs.                  ║
║  Sector ETF change from polygon_market_daily (prev_close → close).        ║
║                                                                              ║
║  SYSTEM 5 — TIME OF DAY                                                    ║
║  9:30-9:45 AM DANGER ZONE: need conviction ≥ 85 to enter.                 ║
║  11:30-1:30 PM MIDDAY CHOP: no new entries.                               ║
║  3:00-4:00 PM POWER HOUR: +5 conviction bonus.                            ║
║                                                                              ║
║  SYSTEM 6 — FLOAT AND SHORT INTEREST                                       ║
║  Float < 1M: ultra-dangerous, 0.5% max position. Lottery sizing.          ║
║  Float 1-5M: +5 conviction, classic momentum float.                       ║
║  Float < 2M always tagged SQUEEZE_CANDIDATE (thin float = violence).      ║
║                                                                              ║
║  CONCENTRATION RULE                                                        ║
║  If top 5 picks all share the same pattern tag → systemic scanner bias.   ║
║  Flag it. Pick zero. Review filter settings.                               ║
║                                                                              ║
║  THE FIVE STOCK LESSON — JUNE 30 2026                                      ║
║  TNMG, DCOY, CNET, KNDI, JATT: all Day 2 plays, no fresh catalysts.      ║
║  All five should have been SKIP before market open.                        ║
║  The scanner found them correctly. The filter layer was missing.           ║
║  That filter layer is now live. Never let this happen again.              ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


# ═════════════════════════════════════════════════════════════════════════════
# MASTER EVALUATE — All 6 systems + staleness + WS patterns combined
# ═════════════════════════════════════════════════════════════════════════════

CONVICTION_BASE_THRESHOLD = 70


def master_evaluate_signal_with_data(
    ticker: str,
    base_conviction: float,
    scan_dt: datetime,
    history: list,
    news: list,
    details: dict,
    conn,
    extra: dict | None = None,
    premarket_mode: bool = True,
) -> dict:
    """
    Complete AIEM signal evaluation pipeline.

    Runs in order:
      Layer A  — staleness_filter.evaluate_signal_with_data()
      Layer B  — aiem_verification_and_trading_brain.apply_wall_street_pattern_with_data()
      Gate 0   — kill switch
      System 3 — news source quality
      System 4 — sector heat
      System 6 — float + short interest
      System 5 — time of day (determines effective threshold)

    Args:
        ticker          : stock symbol
        base_conviction : score after _score_multiday() (0-100)
        scan_dt         : UTC datetime of current scan
        history         : rows from _get_multiday_context (oldest→newest)
        news            : Polygon news items from _aiem_get_news
        details         : Polygon v3 reference dict from _aiem_get_ticker_details
        conn            : psycopg2 DB connection (read-only queries for sector ETF)
        extra           : optional dict; may include entry_price, pipe_price
        premarket_mode  : True = don't block on allow_entry=False (building watchlist)

    Returns full verdict dict with action, final_conviction, tags, notes,
    position sizing, and which systems fired.
    """
    from staleness_filter import evaluate_signal_with_data as _stale
    from aiem_verification_and_trading_brain import (
        apply_wall_street_pattern_with_data as _ws,
    )

    extra  = extra or {}
    tags:  list = []
    notes: list = []
    today  = scan_dt.date() if hasattr(scan_dt, "date") else scan_dt

    result: dict[str, Any] = {
        "ticker":           ticker,
        "scan_dt":          scan_dt.isoformat() if hasattr(scan_dt, "isoformat") else str(scan_dt),
        "base_conviction":  base_conviction,
        "final_conviction": base_conviction,
        "action":           "EVALUATING",
        "allow_entry":      True,
        "tags":             tags,
        "notes":            notes,
        "max_position_pct": 0.02,
        "systems_applied":  [],
    }

    # ── Layer A: Staleness filter ──────────────────────────────────────────
    stale_verdict = _stale(
        ticker, base_conviction, history, news, scan_dt,
        premarket_mode=premarket_mode,
    )
    if stale_verdict["action"] == "SKIP":
        result.update({
            "action":           "SKIP",
            "final_conviction": 0,
            "tags":             stale_verdict["tags"],
            "notes":            [f"Staleness: {stale_verdict['reason']}"],
        })
        result["systems_applied"].append("staleness_filter → SKIP")
        return result

    conviction = stale_verdict["final_conviction"]
    tags.extend(stale_verdict.get("tags", []))
    result["systems_applied"].append("staleness_filter ✓")

    # ── Layer B: Wall Street patterns ──────────────────────────────────────
    ws_result = _ws(ticker, stale_verdict, history, news)
    conviction = ws_result["final_conviction"]
    tags.extend([t for t in ws_result.get("tags", []) if t not in tags])
    notes.extend(ws_result.get("ws_notes", []))
    result["systems_applied"].append("wall_street_patterns ✓")

    if conviction < CONVICTION_BASE_THRESHOLD:
        result.update({
            "action":           "SKIP",
            "final_conviction": max(0, conviction),
            "tags":             list(set(tags)),
            "notes":            notes,
        })
        result["systems_applied"].append("ws_patterns → SKIP")
        return result

    # ── Gate 0: Kill switch ────────────────────────────────────────────────
    killed, kill_reason = is_kill_switch_active(conn, today)
    if killed:
        result.update({
            "action":           "BLOCKED",
            "final_conviction": 0,
            "tags":             ["KILL_SWITCH_ACTIVE"],
            "notes":            [kill_reason],
        })
        result["systems_applied"].append("kill_switch → BLOCKED")
        logger.warning("[MASTER] %s BLOCKED: %s", ticker, kill_reason)
        return result
    result["systems_applied"].append("kill_switch ✓")

    # ── System 3: News source quality ──────────────────────────────────────
    src_score, src_name, src_delta = score_news_source_with_data(news)
    conviction += src_delta
    tags.append(f"SOURCE_{src_name}")
    if src_delta != 0:
        notes.append(f"News source '{src_name}' (quality {src_score}/10) → {src_delta:+d}")
    result["systems_applied"].append(f"news_source={src_name} ✓")

    # ── System 4: Sector heat ──────────────────────────────────────────────
    sector_penalty, sector_tag = get_sector_conviction_penalty_with_data(details, conn)
    conviction += sector_penalty
    if sector_tag not in ("SECTOR_NEUTRAL", "SECTOR_DATA_UNAVAILABLE"):
        tags.append(sector_tag)
        notes.append(f"Sector: {sector_tag} → {sector_penalty:+d}")
    result["systems_applied"].append(f"sector_heat={sector_tag} ✓")

    # ── System 6: Float + short interest ───────────────────────────────────
    float_data = get_float_and_si_with_data(details)
    conviction += float_data["conviction_delta"]
    tags.append(f"FLOAT_{float_data['float_category']}")
    notes.append(float_data["note"])
    result["max_position_pct"] = float_data["max_position_pct"]
    if float_data["squeeze_candidate"]:
        tags.append("SQUEEZE_CANDIDATE")
    result["systems_applied"].append(
        f"float={float_data['float_category']} ✓"
        + (" [SQUEEZE]" if float_data["squeeze_candidate"] else "")
    )

    # ── System 5: Time of day ──────────────────────────────────────────────
    conviction, zone_name, allow_entry, threshold = apply_time_of_day(
        conviction, scan_dt, premarket_mode=premarket_mode,
    )
    tags.append(f"ZONE_{zone_name}")
    result["allow_entry"] = allow_entry
    result["systems_applied"].append(f"time_of_day={zone_name} ✓")

    if not allow_entry:
        result.update({
            "action":           "NO_ENTRY",
            "final_conviction": max(0, conviction),
            "tags":             list(set(tags)),
            "notes":            notes + [f"Entry not allowed in zone {zone_name}"],
        })
        return result

    # ── Final verdict ──────────────────────────────────────────────────────
    final = max(0, round(conviction, 1))
    result["final_conviction"] = final
    result["tags"]             = list(set(tags))
    result["notes"]            = notes
    result["action"]           = "PASS" if final >= threshold else "SKIP"
    result["notes"].append(
        f"Conviction {final} {'≥' if final >= threshold else '<'} "
        f"zone threshold {threshold} ({zone_name})"
    )

    logger.info(
        "[MASTER] %s %s  conviction=%.0f  zone=%s  float=%s  src=%s  tags=%s",
        ticker, result["action"], final, zone_name,
        float_data["float_category"], src_name,
        [t for t in result["tags"] if t.startswith("PATTERN_")],
    )

    # Log to signal_fire_log (best-effort; never blocks the pipeline)
    try:
        _db_log_master_signal(conn, result, today, extra.get("entry_price"))
    except Exception as e:
        logger.debug("[MASTER] Signal log skipped for %s: %s", ticker, e)

    return result


def _db_log_master_signal(conn, result: dict, signal_date: date,
                           entry_price: float | None) -> None:
    """Persist master eval result to signal_fire_log for the feedback loop."""
    import json as _json
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO signal_fire_log
            (signal_name, ticker, fire_date, fire_price, metadata)
        VALUES ('AIEM_MASTER_EVAL', %s, %s, %s, %s)
        ON CONFLICT (signal_name, ticker, fire_date) DO UPDATE
            SET fire_price = EXCLUDED.fire_price,
                metadata   = EXCLUDED.metadata
    """, (
        result["ticker"],
        signal_date,
        entry_price or 0,
        _json.dumps({
            "action":           result["action"],
            "final_conviction": result["final_conviction"],
            "tags":             result["tags"],
            "systems_applied":  result["systems_applied"],
        }),
    ))
    conn.commit()
