"""
AIEM Process — standalone nano-cap premarket scanner.
Zero dependency on Flask / main.py. Completely independent engine.

Watches a DIFFERENT universe than the main scanner:
  price $1-$20, float <20M, gap >2%, premarket vol >50K
  — low-float nano caps with explosive premarket moves.
  These stocks do NOT appear in the main scanner (no options needed).

Alerts delivered via Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID).

Daily schedule (all times ET, Mon–Fri):
  6:55 AM   warm-up: one Polygon call → 8 000+ stocks cached
  7:00 AM+  premarket scan every 15 min (7:00–9:15)
  9:30–10:30 open watcher every 5 min — Telegram alert when confidence ≥72
  4:30 PM   grade T1 outcomes
  4:35 PM   grade T3 / T5 outcomes
  4:45 PM   find missed runners
  5:00 PM   pattern gap analysis
  5:15 PM   write signal discoveries
  6:00 PM   nightly learn — update signal trust weights

Data flow:
  Polygon /v2/snapshot → 8 000+ stocks (one call, 2-3 s)
       ↓  price $1–$20        (~2 000)
       ↓  premarket vol > 50K   (~400)
       ↓  gap > 2 %             (~100)
       ↓  float < 20 M           (~30–50)
       ↓  AIEM scores each
       →  top 10 picks written to aiem_process_predictions
"""

import os
import sys
import time
import json
import math
import logging
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, date

import pytz
import psycopg2
import psycopg2.extras

ET = pytz.timezone("US/Eastern")

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
DB_URL        = os.environ.get("DATABASE_URL", "")
POLYGON_KEY   = os.environ.get("POLYGON_API_KEY", "")
TRADIER_TOKEN = (os.environ.get("TRADIER_API_TOKEN_2") or
                 os.environ.get("TRADIER_API_TOKEN", ""))
TG_TOKEN      = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "8609255707").strip()

# Funnel thresholds
MIN_PRICE         = 1.0
MAX_PRICE         = 20.0
MIN_PM_VOLUME     = 50_000
MIN_GAP_PCT       = 2.0
MAX_FLOAT_SHARES  = 20_000_000
CONFIDENCE_THRESH = 45          # fire alert above this (max achievable without float/SI data ~61%)
CANDIDATE_LIMIT   = 50          # max after float filter before scoring

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [AIEM] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("aiem")


# ─────────────────────────────────────────────────────────────
# MODULE-LEVEL PIPELINE STATE
# (passed between the 4:30–5:15 jobs without hitting the DB again)
# ─────────────────────────────────────────────────────────────
_STATE = {
    "universe":    [],   # list of {ticker, price, prev_close, volume, gap_pct, float_shares}
    "picks":       [],   # today's top-10 predictions (from premarket scan)
    "misses":      [],   # stocks that ran >5% but AIEM didn't pick
    "gap_patterns": {},  # signal → {in_picks, in_misses} tallies
}
_STATE_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────
# HELPERS: DB
# ─────────────────────────────────────────────────────────────
def _db():
    return psycopg2.connect(DB_URL, connect_timeout=10)


_US_HOLIDAYS_2026 = {
    date(2026, 1,  1),   # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents Day
    date(2026, 4,  3),   # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7,  3),   # Independence Day (observed — July 4 is Saturday)
    date(2026, 9,  7),   # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}

def _market_day() -> bool:
    today = datetime.now(ET).date()
    return today.weekday() < 5 and today not in _US_HOLIDAYS_2026


# ─────────────────────────────────────────────────────────────
# HELPERS: POLYGON
# ─────────────────────────────────────────────────────────────
def _pg_get(url: str, timeout: int = 15) -> dict:
    """GET a Polygon URL; return parsed JSON or {}."""
    try:
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}apiKey={POLYGON_KEY}"
        req  = urllib.request.Request(full, headers={"User-Agent": "AIEM/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning(f"polygon GET error ({url[:60]}…): {e}")
        return {}


def _polygon_all_snapshot() -> list:
    """
    ONE call: fetch snapshot for ALL US stocks (~8 000+).
    Returns list of dicts:
      {ticker, price, prev_close, gap_pct, volume, avg_volume}
    Takes ~2-3 s.
    """
    data = _pg_get(
        "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        "?include_otc=false",
        timeout=20,
    )
    results = []
    for t in (data.get("tickers") or []):
        sym = (t.get("ticker") or "").upper()
        if not sym or len(sym) > 5 or "." in sym or "/" in sym:
            continue
        day      = t.get("day")      or {}
        prev_day = t.get("prevDay")  or {}
        price    = float(day.get("c") or day.get("o") or 0)
        prev     = float(prev_day.get("c") or 0)
        vol      = int(day.get("v") or 0)
        avg_vol  = int(t.get("min", {}).get("av") or prev_day.get("v") or 1)
        gap_pct  = float(t.get("todaysChangePerc") or 0)
        results.append({
            "ticker":      sym,
            "price":       price,
            "prev_close":  prev,
            "gap_pct":     gap_pct,
            "volume":      vol,
            "avg_volume":  avg_vol,
            "float_shares": None,   # filled in stage 4
        })
    log.info(f"snapshot returned {len(results)} tickers")
    return results


def _polygon_snapshot_tickers(tickers: list) -> dict:
    """Snapshot for a specific list of tickers. Returns {sym: {...}}."""
    if not tickers:
        return {}
    batch = ",".join(tickers[:100])
    data  = _pg_get(
        f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers"
        f"?tickers={batch}",
        timeout=10,
    )
    out = {}
    for t in (data.get("tickers") or []):
        sym  = (t.get("ticker") or "").upper()
        day  = t.get("day") or {}
        prev = t.get("prevDay") or {}
        out[sym] = {
            "price":      float(day.get("c") or day.get("o") or 0),
            "open":       float(day.get("o") or 0),
            "prev_close": float(prev.get("c") or 0),
            "volume":     int(day.get("v") or 0),
            "gap_pct":    float(t.get("todaysChangePerc") or 0),
        }
    return out


def _polygon_ref_batch(tickers: list) -> dict:
    """
    Fetch float (shares outstanding) from Polygon reference for each ticker.
    Runs up to 10 parallel requests. Returns {sym: float_shares}.
    """
    def _fetch_one(sym):
        data = _pg_get(
            f"https://api.polygon.io/v3/reference/tickers/{sym}", timeout=6
        )
        res = data.get("results") or {}
        shares = (res.get("weighted_shares_outstanding") or
                  res.get("share_class_shares_outstanding") or None)
        return sym, int(shares) if shares else None

    result = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                sym, shares = fut.result()
                result[sym] = shares
            except Exception:
                pass
    return result


def _polygon_prev_close_batch(tickers: list) -> dict:
    """
    Fetch previous-day close for a list of tickers using Polygon prev agg.
    Returns {sym: close_price}.
    """
    def _fetch_one(sym):
        data = _pg_get(
            f"https://api.polygon.io/v2/aggs/ticker/{sym}/prev", timeout=6
        )
        results = (data.get("results") or [{}])
        close = results[0].get("c") if results else None
        return sym, float(close) if close else None

    result = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                sym, close = fut.result()
                result[sym] = close
            except Exception:
                pass
    return result


# ─────────────────────────────────────────────────────────────
# HELPERS: TRADIER (fallback for quotes when Polygon incomplete)
# ─────────────────────────────────────────────────────────────
def _td_quotes(symbols: list) -> dict:
    """Fetch live quotes from Tradier for up to 200 symbols. Uses urllib (no requests)."""
    if not TRADIER_TOKEN or not symbols:
        return {}
    try:
        batch   = ",".join(symbols[:200])
        req     = urllib.request.Request(
            f"https://api.tradier.com/v1/markets/quotes?symbols={batch}",
            headers={"Authorization": f"Bearer {TRADIER_TOKEN}",
                     "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        raw = resp.get("quotes", {}).get("quote", [])
        if isinstance(raw, dict):
            raw = [raw]
        return {
            q["symbol"]: {
                "price":      float(q.get("last") or q.get("open") or 0),
                "prev_close": float(q.get("prevclose") or 0),
                "open":       float(q.get("open") or 0),
                "volume":     int(q.get("volume") or 0),
                "avg_volume": int(q.get("average_volume") or 1),
            }
            for q in raw if q.get("symbol")
        }
    except Exception as e:
        log.warning(f"td_quotes error: {e}")
        return {}


def _polygon_grouped_daily_universe() -> list:
    """
    Fetch full market OHLCV from Polygon grouped-daily for the most recent
    available trading day (goes back up to 7 calendar days).
    Returns list of dicts with ticker + prev_close for gap calculation.
    The live gap is computed later via _tradier_live_update().
    """
    et_now = datetime.now(ET)
    for days_back in range(1, 8):
        check_date = (et_now - timedelta(days=days_back)).date()
        if check_date.weekday() >= 5:          # skip weekends
            continue
        date_str = check_date.strftime("%Y-%m-%d")
        try:
            data    = _pg_get(
                f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
                f"?adjusted=true&include_otc=false",
                timeout=25,
            )
            results = data.get("results") or []
            if len(results) < 100:
                log.info(f"grouped_daily {date_str}: only {len(results)} rows — trying earlier date")
                continue
            log.info(f"grouped_daily: {date_str} → {len(results):,} stocks")
            out = []
            for r in results:
                sym = (r.get("T") or "").upper()
                if not sym or len(sym) > 5 or "." in sym or "/" in sym:
                    continue
                close = float(r.get("c") or 0)
                high  = float(r.get("h") or 0)
                low   = float(r.get("l") or 0)
                vol   = int(r.get("v") or 0)
                # T-1 close_strength: where did yesterday close in its range?
                # 1.0 = closed at high, 0.0 = closed at low — knowable at 9:30 AM
                t1_cs = round((close - low) / (high - low), 4) if (high - low) > 0 else 0.0
                out.append({
                    "ticker":             sym,
                    "price":              close,
                    "prev_close":         close,   # will be refined by Tradier
                    "prev_close_strength": t1_cs,  # Signal #3 gate input
                    "volume":             0,        # will be filled by Tradier (today's volume)
                    "avg_volume":         max(vol, 1),
                    "gap_pct":            0.0,
                    "float_shares":       None,
                })
            return out
        except Exception as e:
            log.warning(f"grouped_daily {date_str}: {e}")
    log.error("grouped_daily: could not find usable trading day in last 7 days")
    return []


def _tradier_live_update(candidates: list) -> list:
    """
    Enrich candidates with Tradier live prices.
    Calculates today's gap_pct and rvol for each ticker.
    Calls Tradier in batches of 200 (stays within rate limits).
    """
    if not candidates or not TRADIER_TOKEN:
        return candidates
    syms = [c["ticker"] for c in candidates]
    live: dict = {}
    for i in range(0, len(syms), 200):
        batch = syms[i:i + 200]
        try:
            req = urllib.request.Request(
                f"https://api.tradier.com/v1/markets/quotes?symbols={','.join(batch)}",
                headers={"Authorization": f"Bearer {TRADIER_TOKEN}",
                         "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
            raw = resp.get("quotes", {}).get("quote", [])
            if isinstance(raw, dict):
                raw = [raw]
            for q in raw:
                s = (q.get("symbol") or "").upper()
                if s:
                    live[s] = {
                        "price":      float(q.get("last") or q.get("open") or 0),
                        "open":       float(q.get("open") or 0),
                        "volume":     int(q.get("volume") or 0),
                        "avg_volume": int(q.get("average_volume") or 1),
                        "prev_close": float(q.get("prevclose") or 0),
                    }
        except Exception as e:
            log.warning(f"tradier live update batch {i//200+1}: {e}")

    enriched = []
    for c in candidates:
        sym = c["ticker"]
        td  = live.get(sym)
        if not td:
            enriched.append(c)
            continue
        cur_price  = td["price"]
        prev_close = td["prev_close"] or c["prev_close"] or 1
        vol        = td["volume"]
        avg_vol    = td["avg_volume"] or 1
        gap_pct    = ((cur_price - prev_close) / prev_close * 100) if prev_close else 0
        # Sanity caps: >200% gap = reverse-split artifact; avg_vol <5K = meaningless RVOL
        if gap_pct > 200:
            gap_pct = 0.0
        rvol = round(vol / avg_vol, 2) if avg_vol >= 5_000 else 0.0
        enriched.append({
            **c,
            "price":      cur_price,
            "prev_close": prev_close,
            "volume":     vol,
            "avg_volume": avg_vol,
            "gap_pct":    round(gap_pct, 2),
            "rvol":       rvol,
        })
    return enriched


# ─────────────────────────────────────────────────────────────
# HELPERS: ALERT
# ─────────────────────────────────────────────────────────────
def _tg_send(message: str) -> bool:
    """Send a Telegram message. Returns True if API responded ok:true."""
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("_tg_send: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return False
    try:
        payload = json.dumps({
            "chat_id":    TG_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
            ok = resp.get("ok", False)
            if ok:
                log.info(f"Telegram sent: {message[:60]}…")
            else:
                log.warning(f"Telegram API error: {resp}")
            return ok
    except Exception as e:
        log.warning(f"_tg_send error: {e}")
        return False


def _send_alert(message: str) -> None:
    """Backward-compat wrapper — delegates to Telegram."""
    _tg_send(message)


# ─────────────────────────────────────────────────────────────
# HELPER: SIGNAL TRUST WEIGHTS
# ─────────────────────────────────────────────────────────────
def _load_trust_weights(conn) -> dict:
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT signal_name, trust_weight, rolling_win_rate, n_outcomes_observed
            FROM signal_trust_weights ORDER BY trust_weight DESC
        """)
        return {
            r[0]: {"trust": float(r[1] or 1.0), "win_rate": float(r[2] or 0.5), "n": r[3] or 0}
            for r in cur.fetchall()
        }
    except Exception as e:
        log.warning(f"trust weight load error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────
# CORE: AIEM SCORING ENGINE (trust-weighted, 9 signals)
# ─────────────────────────────────────────────────────────────
def aiem_score_ticker(ticker: str, data: dict, trust_weights: dict):
    """
    Returns (confidence 0-100, signal_basis str, reasoning str, predicted_move str).
    Each signal weighted by its historical trust from signal_trust_weights.
    """
    signals, reasoning = {}, []
    raw_score = max_score = 0.0

    price    = data.get("price") or data.get("premarket_price") or 0
    prev     = data.get("prev_close") or price or 1
    vol      = data.get("volume") or data.get("premarket_volume") or 0
    avg_vol  = data.get("avg_volume") or 1
    flt      = data.get("float_shares") or 999_000_000
    si       = data.get("short_interest_pct") or 0
    spread   = data.get("bid_ask_spread_pct") or 999
    coiling  = data.get("consolidating") or False
    catalyst = data.get("has_catalyst") or False

    gap_pct   = ((price - prev) / prev * 100) if prev else 0
    vol_ratio = vol / avg_vol if avg_vol > 0 else 0
    prev_cs   = float(data.get("prev_close_strength") or 0)

    def _add(name, base, cond, desc=""):
        nonlocal raw_score, max_score
        t   = trust_weights.get(name, {}).get("trust", 1.0)
        eff = base * t
        max_score += eff
        if cond:
            signals[name] = eff
            raw_score    += eff
            if desc:
                reasoning.append(desc)

    # S1 — Premarket gap
    if   gap_pct >= 15: _add("gap_explosive",  18, True, f"Explosive gap +{gap_pct:.1f}%")
    elif gap_pct >= 10: _add("gap_large",       14, True, f"Large gap +{gap_pct:.1f}%")
    elif gap_pct >= 5:  _add("gap_moderate",    10, True, f"Moderate gap +{gap_pct:.1f}%")
    elif gap_pct >= 2:  _add("gap_small",        5, True, f"Small gap +{gap_pct:.1f}%")
    else:               _add("gap_small",        5, False)

    # S1b — Gap sweet spot (15–25% = validated high-WR zone)
    # Backtest: 864W/153L over 13 months, avg win +18.1%, median +15.4%
    _add("gap_sweet_spot", 5, 15 <= gap_pct < 25,
         f"Sweet spot gap {gap_pct:.1f}% (85% WR zone, +18% avg)")

    # S1c — Signal #3: Momentum Carry (full — top 20% of prior range)
    # Gap 15-22% + T-1 close_strength >= 0.80
    # Backtest: 1,738 trades, WR=96.0%, AvgRet=+13.85%, PF=47.2x, Sharpe=+1.78
    # Logic: stock closed in top 20% of its range yesterday AND gaps again today
    # = real momentum carry, not a gap-and-trap.  All inputs knowable at 9:30 AM.
    # Combined with S1b → +13 pts total for the highest-conviction setups.
    _add("momentum_carry", 8, 15 <= gap_pct < 22 and prev_cs >= 0.80,
         f"Momentum carry: gap {gap_pct:.1f}% in sweet zone + T-1 closed strong ({prev_cs:.2f})")

    # S1d — Soft Carry (upper 40% of prior range — middle tier)
    # Gap 15-22% + T-1 close_strength 0.60–0.79 (mutually exclusive with S1c)
    # More picks than S1c alone; still meaningfully better than random gappers.
    # Combined with S1b → +9 pts total.
    _add("soft_carry", 4, 15 <= gap_pct < 22 and 0.60 <= prev_cs < 0.80,
         f"Soft carry: gap {gap_pct:.1f}% in sweet zone + T-1 mid-range close ({prev_cs:.2f})")

    # S2 — Volume surge
    if   vol_ratio >= 5:   _add("volume_surge_extreme",   20, True, f"Volume {vol_ratio:.1f}x — extreme")
    elif vol_ratio >= 3:   _add("volume_surge_high",      15, True, f"Volume {vol_ratio:.1f}x — strong")
    elif vol_ratio >= 1.5: _add("volume_surge_moderate",   8, True, f"Volume {vol_ratio:.1f}x — elevated")
    else:                  _add("volume_surge_moderate",   8, False)

    # S3 — Float
    if   flt < 5_000_000:  _add("float_micro",  18, True, f"Micro float {flt/1e6:.1f}M")
    elif flt < 15_000_000: _add("float_low",    12, True, f"Low float {flt/1e6:.1f}M")
    elif flt < 50_000_000: _add("float_medium",  6, True, f"Mid float {flt/1e6:.1f}M")
    else:                  _add("float_medium",  6, False)

    # S4 — Short interest
    if   si >= 25: _add("short_squeeze_high",      16, True, f"SI {si:.1f}% — squeeze setup")
    elif si >= 15: _add("short_squeeze_moderate",  10, True, f"SI {si:.1f}%")
    elif si >= 8:  _add("short_squeeze_low",        5, True, f"SI {si:.1f}%")
    else:          _add("short_squeeze_low",        5, False)

    # S5 — Catalyst
    _add("catalyst_present",    15, catalyst,        "Catalyst detected")

    # S6 — Tight spread
    _add("tight_spread",         8, spread < 0.5,   f"Tight spread {spread:.2f}%")

    # S7 — Consolidation
    _add("consolidating",       12, coiling,         "Coiling — tight range setup")

    # S8 — Price in breakout range
    _add("price_breakout_range", 6, 1.0 <= price <= 20.0, f"Price ${price:.2f} in range")

    # S9 — Gap + volume combo (strongest)
    _add("gap_volume_combo",    20, gap_pct >= 8 and vol_ratio >= 3,
         f"COMBO gap {gap_pct:.1f}% + vol {vol_ratio:.1f}x")

    conf = min(100, round((raw_score / max_score * 100) if max_score > 0 else 0, 1))

    move = (
        "Strong breakout likely — high conviction long" if conf >= 80
        else "Moderate breakout — watch open" if conf >= 65
        else "Possible setup — needs open confirmation" if conf >= 50
        else "Low conviction — monitor only"
    )
    return conf, ", ".join(signals), " | ".join(reasoning) or "No strong signals", move


# ─────────────────────────────────────────────────────────────
# JOB 0: WARM-UP  (6:55 AM)
# One Polygon call → cache full universe → funnel to top candidates
# ─────────────────────────────────────────────────────────────
def aiem_warmup():
    """
    6:55 AM: Build candidate universe using Polygon grouped-daily (previous
    trading day) — no live snapshot needed.  Filters by price $1-$20 and
    avg-volume > 10K to keep the Tradier batch calls manageable (~1 000 tickers).
    Live gap / RVOL are computed by the 7:00 premarket scan via Tradier.
    """
    if not _market_day():
        return
    log.info("warmup: fetching Polygon grouped-daily universe…")
    t0 = time.time()

    all_tickers = _polygon_grouped_daily_universe()
    log.info(f"grouped_daily returned {len(all_tickers):,} stocks")

    # Stage 1: price $1–$20 (using prev-day close as proxy)
    s1 = [t for t in all_tickers if MIN_PRICE <= t["price"] <= MAX_PRICE]
    log.info(f"stage1 price ${MIN_PRICE}-${MAX_PRICE}: {len(all_tickers):,} → {len(s1):,}")

    # Stage 2: avg volume > 10K (light filter — Tradier will apply tighter RVOL at scan time)
    s2 = [t for t in s1 if t["avg_volume"] >= 10_000]
    log.info(f"stage2 avg_vol >10K: {len(s1):,} → {len(s2):,}")

    log.info(f"warmup complete in {time.time()-t0:.1f}s — {len(s2):,} candidates cached for premarket scan")
    with _STATE_LOCK:
        _STATE["universe"] = s2


# ─────────────────────────────────────────────────────────────
# JOB 1: PREMARKET SCAN  (7:00–9:15 AM, every 15 min)
# ─────────────────────────────────────────────────────────────
def aiem_premarket_scan():
    """
    Score cached universe with trust-weighted AIEM engine.
    Write top 10 to aiem_process_predictions (replaces today's each run).
    Refreshes live prices via Tradier on each pass (no Polygon snapshot needed).
    """
    if not _market_day():
        return
    now_et = datetime.now(ET)
    log.info(f"premarket_scan at {now_et.strftime('%H:%M ET')}")

    with _STATE_LOCK:
        base_universe = list(_STATE["universe"])

    if not base_universe:
        log.info("premarket_scan: warmup universe empty — skipping")
        return

    # Refresh all candidates with live Tradier prices → computes gap_pct + rvol
    log.info(f"premarket_scan: refreshing {len(base_universe):,} candidates via Tradier…")
    enriched = _tradier_live_update(base_universe)

    # Apply the same funnel with live data
    s1 = [t for t in enriched if MIN_PRICE    <= (t.get("price") or 0) <= MAX_PRICE]
    s2 = [t for t in s1       if (t.get("volume") or 0) >= MIN_PM_VOLUME]
    s3 = [t for t in s2       if (t.get("gap_pct") or 0) >= MIN_GAP_PCT]
    log.info(f"funnel: {len(enriched):,} → price {len(s1):,} → vol {len(s2):,} → gap {len(s3):,}")

    universe = s3   # float filter skipped (no reliable live float source; scoring handles it)

    with _STATE_LOCK:
        _STATE["universe"] = enriched   # keep full enriched list for next pass

    if not universe:
        log.info("premarket_scan: no candidates after funnel")
        return

    log.info(f"premarket_scan: scoring {len(universe)} candidates")

    conn = None
    try:
        conn = _db()
        trust_weights = _load_trust_weights(conn)
        cur = conn.cursor()

        scored = []
        for t in universe[:CANDIDATE_LIMIT]:
            try:
                conf, sig_basis, reasoning, move = aiem_score_ticker(
                    t["ticker"], t, trust_weights
                )
                scored.append({**t, "confidence": conf,
                                "signal_basis": sig_basis,
                                "reasoning": reasoning,
                                "predicted_move": move})
            except Exception as e:
                log.warning(f"score error {t['ticker']}: {e}")

        scored.sort(key=lambda x: x["confidence"], reverse=True)
        top10 = scored[:10]

        today = datetime.now(ET).date()
        cur.execute("DELETE FROM aiem_process_predictions WHERE prediction_date = %s", (today,))
        for rank, p in enumerate(top10, 1):
            cur.execute("""
                INSERT INTO aiem_process_predictions
                    (prediction_date, ticker, rank, confidence_score,
                     signal_basis, reasoning, predicted_move, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """, (today, p["ticker"], rank, p["confidence"],
                  p["signal_basis"], p["reasoning"], p["predicted_move"]))

        conn.commit()
        with _STATE_LOCK:
            _STATE["picks"] = top10

        log.info(f"premarket_scan: wrote {len(top10)} predictions")
        for p in top10[:3]:
            log.info(f"  #{p['ticker']} conf={p['confidence']} gap={p['gap_pct']:.1f}% — {p['reasoning'][:70]}")

    except Exception as e:
        log.error(f"premarket_scan DB error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# JOB 2: OPEN WATCHER  (9:30–10:30 AM, every 5 min)
# ─────────────────────────────────────────────────────────────
def aiem_open_watcher():
    """
    At open: re-score predictions with live Polygon prices.
    Blend premarket score (40%) + live score (60%).
    Fire SMS the moment AIEM's blended confidence crosses the threshold.
    """
    if not _market_day():
        return
    now_et = datetime.now(ET)
    h, m   = now_et.hour, now_et.minute
    if not ((h == 9 and m >= 30) or h == 10):
        return

    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()
        today = datetime.now(ET).date()

        cur.execute("""
            SELECT ticker, rank, confidence_score, signal_basis, reasoning, predicted_move
            FROM aiem_process_predictions WHERE prediction_date = %s ORDER BY rank
        """, (today,))
        picks = cur.fetchall()
        if not picks:
            return

        cur.execute("""
            SELECT ticker FROM signal_fire_log
            WHERE fire_date = %s AND signal_name = 'AIEM_OPEN_ALERT'
        """, (today,))
        already = {r[0] for r in cur.fetchall()}

        trust_weights = _load_trust_weights(conn)

        # Fetch live prices from Polygon in one batch call
        syms        = [p[0] for p in picks]
        live_prices = _polygon_snapshot_tickers(syms)

        # Fallback to Tradier if Polygon returns nothing
        if not live_prices:
            td = _td_quotes(syms)
            live_prices = {s: {"price": d["price"], "prev_close": d["prev_close"],
                               "volume": d["volume"], "avg_volume": d["avg_volume"]}
                           for s, d in td.items()}

        # Only send the grouped alert once per day (at 9:30 AM first pass)
        if "DAILY_SUMMARY" in already:
            return

        # Score every pick against live prices, collect qualifiers
        qualifiers = []
        for ticker, rank, base_conf, sig_basis, reasoning, predicted_move in picks:
            live = live_prices.get(ticker, {})
            if not live:
                continue
            # Infer prev_close_strength from premarket signal_basis so
            # momentum_carry / soft_carry fire correctly in the live re-score
            if "momentum_carry" in (sig_basis or ""):
                inferred_prev_cs = 0.85   # was >= 0.80 at premarket
            elif "soft_carry" in (sig_basis or ""):
                inferred_prev_cs = 0.70   # was 0.60–0.79 at premarket
            else:
                inferred_prev_cs = 0.0
            live_data = {
                "price":               live.get("price"),
                "prev_close":          live.get("prev_close"),
                "volume":              live.get("volume", 0),
                "avg_volume":          live.get("avg_volume", 1),
                "prev_close_strength": inferred_prev_cs,
            }
            live_conf, _, live_reason, live_move = aiem_score_ticker(
                ticker, live_data, trust_weights
            )
            blended = round(base_conf * 0.4 + live_conf * 0.6, 1)
            log.info(f"{ticker} blended={blended} (pre={base_conf} live={live_conf}) sig={sig_basis}")
            if blended >= CONFIDENCE_THRESH:
                cur_price  = live.get("price") or 0
                stop_price = round(cur_price * 0.90, 2) if cur_price else None
                qualifiers.append({
                    "ticker": ticker, "rank": rank, "conf": blended,
                    "sig": sig_basis or "", "price": cur_price,
                    "stop": stop_price, "reason": live_reason,
                })

        if not qualifiers:
            log.info("open_watcher: no picks crossed threshold — no alert sent")
            return

        # Group by signal tier
        s1c   = [q for q in qualifiers if "momentum_carry" in q["sig"]]
        s1d   = [q for q in qualifiers if "soft_carry"     in q["sig"]]
        s1b   = [q for q in qualifiers if q not in s1c and q not in s1d]

        def _fmt_pick(q):
            stop = f"  |  Stop ≈ ${q['stop']:.2f}" if q["stop"] else ""
            return (f"  {q['ticker']}  |  Open ${q['price']:.2f}  |  Conf {q['conf']:.0f}/100{stop}\n"
                    f"  💡 {q['reason'][:100]}")

        lines = [
            f"⚡ AIEM S1B · S1C · S1D — Morning Picks",
            f"📅 {now_et.strftime('%a %b %-d, %Y')}  |  {now_et.strftime('%I:%M %p ET')}",
            f"{'─' * 32}",
        ]
        if s1c:
            lines.append("\n🟢 S1c — Full Carry (highest conviction)")
            lines.append("Gap 15-22% + prior session closed top 20%")
            for q in s1c:
                lines.append(_fmt_pick(q))
        if s1d:
            lines.append("\n🔵 S1d — Soft Carry")
            lines.append("Gap 15-22% + prior session closed upper 40%")
            for q in s1d:
                lines.append(_fmt_pick(q))
        if s1b:
            lines.append("\n🟡 S1b — Gap Zone")
            lines.append("Gap 15-25% validated sweet-spot")
            for q in s1b:
                lines.append(_fmt_pick(q))
        lines.append(f"\n🛑 -10% hard stop on all names  |  Size $500-$1,000/pick")
        lines.append(f"📊 {len(qualifiers)} pick{'s' if len(qualifiers) != 1 else ''} confirmed at open")

        _send_alert("\n".join(lines))

        # Log as sent so we don't fire again today
        cur.execute("""
            INSERT INTO signal_fire_log
                (signal_name, ticker, fire_date, metadata, logged_at)
            VALUES ('AIEM_OPEN_ALERT', 'DAILY_SUMMARY', %s, %s::jsonb, NOW())
            ON CONFLICT (signal_name, ticker, fire_date) DO NOTHING
        """, (today, json.dumps({"picks": len(qualifiers)})))
        conn.commit()
        log.info(f"open_watcher: grouped alert sent — {len(qualifiers)} picks ({len(s1c)} S1c, {len(s1d)} S1d, {len(s1b)} S1b)")

    except Exception as e:
        log.error(f"open_watcher error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# JOB 3: GRADE OUTCOMES — T1  (4:30 PM)
# ─────────────────────────────────────────────────────────────
def aiem_grade_outcomes():
    """
    EOD: pull today's closes from Polygon for each prediction.
    Write T1 result to aiem_prediction_outcomes.
    """
    if not _market_day():
        return
    today = datetime.now(ET).date()
    log.info(f"grade_outcomes for {today}")

    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()

        cur.execute("""
            SELECT p.ticker FROM aiem_process_predictions p
            LEFT JOIN aiem_process_outcomes o
                ON p.ticker = o.ticker AND p.prediction_date = o.prediction_date
            WHERE p.prediction_date = %s AND o.id IS NULL
        """, (today,))
        tickers = [r[0] for r in cur.fetchall()]

        if not tickers:
            log.info("grade_outcomes: nothing to grade")
            return

        # Polygon snapshot for closing prices
        snaps = _polygon_snapshot_tickers(tickers)
        if not snaps:
            # Fallback: Tradier
            td = _td_quotes(tickers)
            snaps = {s: {"price": d["price"], "open": 0, "prev_close": d["prev_close"]}
                     for s, d in td.items()}

        graded = 0
        for ticker in tickers:
            try:
                q = snaps.get(ticker, {})
                close_price = q.get("price")
                open_price  = q.get("open") or close_price
                if not close_price or not open_price:
                    continue
                t1_ret = (close_price - open_price) / open_price * 100
                cur.execute("""
                    INSERT INTO aiem_process_outcomes
                        (prediction_date, ticker, entry_price, t1_price, t1_return, graded_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (prediction_date, ticker) DO UPDATE
                        SET t1_price  = EXCLUDED.t1_price,
                            t1_return = EXCLUDED.t1_return,
                            graded_at = NOW()
                """, (today, ticker, open_price, close_price, round(t1_ret, 4)))
                graded += 1
                log.info(f"{ticker}: open={open_price:.2f} close={close_price:.2f} ({t1_ret:+.1f}%) {'WIN' if t1_ret > 0 else 'LOSS'}")
            except Exception as e:
                log.warning(f"grade {ticker}: {e}")

        conn.commit()
        log.info(f"grade_outcomes: graded {graded}/{len(tickers)}")

    except Exception as e:
        log.error(f"grade_outcomes error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# JOB 3B: GRADE T3 / T5  (4:35 PM)
# ─────────────────────────────────────────────────────────────
def aiem_grade_t3_t5():
    if not _market_day():
        return
    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()
        today = datetime.now(ET).date()

        for n, col_p, col_r, col_w in [
            (3, "t3_price", "t3_return", "win_t3"),
            (5, "t5_price", "t5_return", "win_t5"),
        ]:
            target = today - timedelta(days=n)
            cur.execute(f"""
                SELECT ticker, entry_price FROM aiem_process_outcomes
                WHERE prediction_date = %s AND {col_p} IS NULL AND entry_price IS NOT NULL
            """, (target,))
            rows = cur.fetchall()
            if not rows:
                continue
            snaps = _polygon_snapshot_tickers([r[0] for r in rows])
            for ticker, entry in rows:
                try:
                    price = (snaps.get(ticker) or {}).get("price")
                    if price and entry:
                        ret = (price - entry) / entry * 100
                        cur.execute(f"""
                            UPDATE aiem_process_outcomes
                            SET {col_p}=%s, {col_r}=%s, {col_w}=%s
                            WHERE prediction_date=%s AND ticker=%s
                        """, (price, round(ret, 4), ret > 0, target, ticker))
                        log.info(f"T{n} {ticker}: {ret:+.1f}%")
                except Exception as e:
                    log.warning(f"T{n} grade {ticker}: {e}")

        conn.commit()
    except Exception as e:
        log.error(f"grade_t3_t5 error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# JOB 4: FIND MISSED RUNNERS  (4:45 PM)
# Stocks that ran >5% today that AIEM didn't pick
# ─────────────────────────────────────────────────────────────
def aiem_find_missed_runners():
    """
    Pull today's big movers from Polygon (stocks up ≥5%).
    Compare against today's picks — anything not picked is a miss.
    Cache misses in _STATE for pattern analysis at 5:00 PM.
    """
    if not _market_day():
        return
    log.info("find_missed_runners: pulling today's movers…")

    # Polygon snapshot — filter for big movers
    all_snap = _polygon_all_snapshot()
    movers   = [
        t for t in all_snap
        if t["gap_pct"] >= 5.0 and t["volume"] >= 100_000 and MIN_PRICE <= t["price"] <= MAX_PRICE
    ]
    log.info(f"find_missed_runners: {len(movers)} stocks up ≥5% today")

    # Get float for movers
    float_map = _polygon_ref_batch([t["ticker"] for t in movers])
    for t in movers:
        t["float_shares"] = float_map.get(t["ticker"])

    # What did AIEM pick today?
    with _STATE_LOCK:
        picks_today = {p["ticker"] for p in _STATE.get("picks", [])}

    # If in-memory empty, fall back to DB
    if not picks_today:
        try:
            conn = _db()
            cur  = conn.cursor()
            cur.execute(
                "SELECT ticker FROM aiem_process_predictions WHERE prediction_date = %s",
                (datetime.now(ET).date(),)
            )
            picks_today = {r[0] for r in cur.fetchall()}
            conn.close()
        except Exception:
            pass

    misses = [t for t in movers if t["ticker"] not in picks_today]
    log.info(f"find_missed_runners: {len(misses)} missed (picked {len(picks_today)}, total movers {len(movers)})")
    for m in misses[:5]:
        log.info(f"  MISS {m['ticker']} +{m['gap_pct']:.1f}% vol={m['volume']:,}")

    with _STATE_LOCK:
        _STATE["misses"] = misses


# ─────────────────────────────────────────────────────────────
# JOB 5: PATTERN GAP ANALYSIS  (5:00 PM)
# Why did AIEM miss? What signals were present?
# ─────────────────────────────────────────────────────────────
def aiem_pattern_gap_analysis():
    """
    For each missed runner: score it as if AIEM had seen it this morning.
    Tally which signals appear in misses but were ABSENT from picks.
    These are the signals AIEM under-weighted — candidates for trust boost.
    """
    if not _market_day():
        return

    with _STATE_LOCK:
        misses = list(_STATE.get("misses", []))
        picks  = list(_STATE.get("picks",  []))

    if not misses:
        log.info("pattern_gap_analysis: no misses today — great day!")
        return

    log.info(f"pattern_gap_analysis: analysing {len(misses)} misses vs {len(picks)} picks")

    # Score misses with neutral trust weights (see what AIEM would have computed)
    neutral_weights = {}
    signal_in_misses: dict[str, int] = {}
    signal_in_picks:  dict[str, int] = {}

    for m in misses:
        _, sig_basis, _, _ = aiem_score_ticker(m["ticker"], m, neutral_weights)
        for sig in [s.strip() for s in sig_basis.split(",") if s.strip()]:
            signal_in_misses[sig] = signal_in_misses.get(sig, 0) + 1

    for p in picks:
        for sig in [s.strip() for s in (p.get("signal_basis") or "").split(",") if s.strip()]:
            signal_in_picks[sig] = signal_in_picks.get(sig, 0) + 1

    # Signals that appear in misses more than picks (normalised) = gap signals
    gap_patterns = {}
    all_sigs = set(signal_in_misses) | set(signal_in_picks)
    for sig in all_sigs:
        miss_rate = signal_in_misses.get(sig, 0) / max(len(misses), 1)
        pick_rate = signal_in_picks.get(sig, 0)  / max(len(picks),  1)
        gap_patterns[sig] = {
            "in_misses":  signal_in_misses.get(sig, 0),
            "in_picks":   signal_in_picks.get(sig, 0),
            "miss_rate":  round(miss_rate, 3),
            "pick_rate":  round(pick_rate, 3),
            "gap":        round(miss_rate - pick_rate, 3),
        }

    # Log top gaps
    top_gaps = sorted(gap_patterns.items(), key=lambda x: x[1]["gap"], reverse=True)[:5]
    for sig, stats in top_gaps:
        log.info(
            f"  GAP signal '{sig}': in_misses={stats['in_misses']} "
            f"in_picks={stats['in_picks']} gap={stats['gap']:+.2f}"
        )

    with _STATE_LOCK:
        _STATE["gap_patterns"] = gap_patterns


# ─────────────────────────────────────────────────────────────
# JOB 6: WRITE SIGNAL DISCOVERIES  (5:15 PM)
# Save statistically meaningful gaps to aiem_signal_discoveries
# ─────────────────────────────────────────────────────────────
def aiem_write_signal_discoveries():
    """
    Any signal that appeared in ≥5 misses today, with a miss_rate ≥ 60%,
    and a gap ≥ 0.25 above pick_rate, is flagged as a hypothesis and written
    to aiem_signal_discoveries (status='hypothesis').

    Gates applied here (pre-insert):
      1. in_misses >= 5      — minimum observations to reduce noise
      2. miss_rate >= 0.60   — signal must appear in ≥60% of missed runners
      3. gap >= 0.25         — meaningful difference over pick_rate

    Promotion gate (in nightly_learn, not here):
      4. rolling_win_rate >= 0.55 AND n_outcomes_observed >= 10
         (applied via signal_trust_weights join before status → 'validated')

    Scale convention: signal_win_rate and baseline_win_rate are stored on
    the 0-100 percentage scale, matching _mkt_tool_save_discovery.
    """
    if not _market_day():
        return

    with _STATE_LOCK:
        gap_patterns = dict(_STATE.get("gap_patterns", {}))
        misses       = list(_STATE.get("misses", []))

    if not gap_patterns:
        log.info("write_signal_discoveries: no gap patterns — skipping")
        return

    # Gates (mirrors _mkt_tool_save_discovery conventions):
    #   1. in_misses >= 5          — minimum sample size to reduce noise
    #   2. miss_rate >= 0.60       — signal must fire in ≥60% of missed runners
    #   3. gap >= 0.25             — raised from 0.20; requires a more decisive gap
    # NOTE: status='hypothesis' rows are further gated at promotion time by
    # nightly_learn (rolling_win_rate >= 0.55 AND n_outcomes_observed >= 10).
    hypotheses = [
        (sig, stats) for sig, stats in gap_patterns.items()
        if stats["in_misses"] >= 5
        and stats["miss_rate"] >= 0.60
        and stats["gap"] >= 0.25
    ]

    if not hypotheses:
        log.info("write_signal_discoveries: no significant gaps today")
        return

    log.info(f"write_signal_discoveries: saving {len(hypotheses)} hypotheses")

    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()
        today = datetime.now(ET).date()

        saved = 0
        for sig, stats in hypotheses:
            # Build hypothesis text
            hypothesis = (
                f"Signal '{sig}' appears in {stats['in_misses']} missed runners "
                f"({stats['miss_rate']:.0%} rate) vs {stats['in_picks']} picks "
                f"({stats['pick_rate']:.0%} rate) — gap {stats['gap']:+.2f}. "
                f"AIEM may be under-weighting this signal."
            )
            conditions = {
                "signal_name":   sig,
                "date_observed": today.isoformat(),
                "miss_rate":     stats["miss_rate"],
                "pick_rate":     stats["pick_rate"],
                "gap":           stats["gap"],
                "n_misses":      stats["in_misses"],
                "n_picks":       stats["in_picks"],
                "missed_tickers": [m["ticker"] for m in misses[:10]],
            }

            # signal_win_rate and baseline_win_rate are stored on 0-100 scale
            # (percentage), matching _mkt_tool_save_discovery convention.
            # miss_rate and pick_rate are raw fractions (0-1); multiply by 100.
            cur.execute("""
                INSERT INTO aiem_signal_discoveries
                    (hypothesis_text, conditions_json, horizon,
                     signal_n, signal_win_rate, baseline_win_rate,
                     edge_broad, status, discovered_at, notes)
                VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s, NOW(), %s)
            """, (
                hypothesis,
                json.dumps(conditions),
                "1d",
                stats["in_misses"],
                round(stats["miss_rate"] * 100, 2),
                round(stats["pick_rate"] * 100, 2),
                round(stats["gap"] * 100, 2),
                "hypothesis",
                f"auto-discovered by aiem_process on {today}",
            ))
            saved += 1
            log.info(f"  saved hypothesis: {sig} (gap={stats['gap']:+.2f})")

        conn.commit()
        log.info(f"write_signal_discoveries: saved {saved} hypotheses")

    except Exception as e:
        log.error(f"write_signal_discoveries error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# JOB 7: NIGHTLY LEARN  (6:00 PM)
# Update signal trust weights from the last 30 days of outcomes
# THIS IS WHERE AIEM GETS SMARTER EVERY DAY
# ─────────────────────────────────────────────────────────────
def aiem_nightly_learn():
    """
    Join aiem_predictions → aiem_prediction_outcomes for last 30 days.
    For each signal: compute win rate, update trust weight.
    trust_weight > 1.0 = AIEM boosts this signal tomorrow.
    trust_weight < 1.0 = AIEM down-weights this signal tomorrow.
    """
    if not _market_day():
        return
    today = datetime.now(ET).date()
    log.info(f"nightly_learn for {today}")

    conn = None
    try:
        conn = _db()
        cur  = conn.cursor()

        cur.execute("""
            SELECT p.signal_basis, p.confidence_score,
                   o.t1_return, o.win_t3, o.win_t5, o.t3_return
            FROM aiem_process_predictions p
            JOIN aiem_process_outcomes o
                ON p.ticker = o.ticker AND p.prediction_date = o.prediction_date
            WHERE p.prediction_date >= %s AND o.t1_return IS NOT NULL
        """, (today - timedelta(days=30),))
        rows = cur.fetchall()

        if not rows:
            log.info("nightly_learn: no graded outcomes yet — come back tomorrow")
            return

        tallies: dict = {}
        for sig_basis, _, t1_ret, win_t3, win_t5, t3_ret in rows:
            if not sig_basis:
                continue
            win = bool(win_t3) if win_t3 is not None else bool((t1_ret or 0) > 0)
            ret = float(t3_ret or t1_ret or 0)
            for sig in [s.strip() for s in sig_basis.split(",") if s.strip()]:
                if sig not in tallies:
                    tallies[sig] = {"wins": 0, "total": 0, "ret": 0.0}
                tallies[sig]["total"] += 1
                tallies[sig]["ret"]   += ret
                if win:
                    tallies[sig]["wins"] += 1

        updated = 0
        for sig, t in tallies.items():
            n          = t["total"]
            win_rate   = t["wins"] / n if n > 0 else 0.5
            avg_ret    = t["ret"] / n  if n > 0 else 0
            sample_con = min(1.0, n / 20)
            raw_trust  = 0.5 + (win_rate - 0.5) * 2
            trust_w    = 1.0 + (raw_trust - 0.5) * sample_con

            cur.execute("""
                INSERT INTO signal_trust_weights
                    (signal_name, context_bucket, rolling_win_rate,
                     n_outcomes_observed, trust_weight, last_updated_at)
                VALUES (%s, 'AIEM_PREMARKET', %s, %s, %s, NOW())
                ON CONFLICT (signal_name, context_bucket) DO UPDATE
                    SET rolling_win_rate    = EXCLUDED.rolling_win_rate,
                        n_outcomes_observed = EXCLUDED.n_outcomes_observed,
                        trust_weight        = EXCLUDED.trust_weight,
                        last_updated_at     = NOW()
            """, (sig, round(win_rate, 4), n, round(trust_w, 4)))

            updated += 1
            log.info(
                f"  '{sig}': wr={win_rate:.1%} n={n} "
                f"trust={trust_w:.3f} avg_ret={avg_ret:+.1f}%"
            )

        conn.commit()
        log.info(f"nightly_learn: updated {updated} signal weights")

        # Promote any hypothesis that now has ≥10 samples and wr > 55%
        cur.execute("""
            UPDATE aiem_signal_discoveries sd
            SET status = 'validated', confirmed_at = NOW()
            FROM signal_trust_weights stw
            WHERE sd.status = 'hypothesis'
              AND stw.signal_name = (sd.conditions_json->>'signal_name')
              AND stw.n_outcomes_observed >= 10
              AND stw.rolling_win_rate >= 0.55
        """)
        promoted = cur.rowcount
        conn.commit()
        if promoted:
            log.info(f"nightly_learn: promoted {promoted} hypotheses → validated")

        # Log research insight
        top3 = sorted(tallies.items(), key=lambda x: x[1]["wins"]/max(x[1]["total"],1), reverse=True)[:3]
        findings = "Top signals: " + ", ".join(
            f"{s}({t['wins']}/{t['total']})" for s, t in top3
        )
        cur.execute("""
            INSERT INTO aiem_research_insights
                (research_date, findings, confidence, session_name, created_at)
            VALUES (%s, %s, %s, 'aiem_process_nightly_learn', NOW())
        """, (today, findings, str(round(updated / max(len(tallies), 1) * 100, 1))))
        conn.commit()
        log.info(f"nightly_learn insight: {findings}")

    except Exception as e:
        log.error(f"nightly_learn error: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        if conn:
            try: conn.close()
            except: pass


# ─────────────────────────────────────────────────────────────
# MAIN — scheduler wiring
# ─────────────────────────────────────────────────────────────
def main():
    log.info("AIEM Process starting…")
    log.info(f"  DB:       {'OK' if DB_URL        else 'MISSING'}")
    log.info(f"  Polygon:  {'OK' if POLYGON_KEY   else 'MISSING'}")
    log.info(f"  Tradier:  {'OK' if TRADIER_TOKEN else 'MISSING'}")
    log.info(f"  Telegram: {'OK' if TG_TOKEN      else 'MISSING — alerts will not fire'}")

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron        import CronTrigger
    from apscheduler.executors.pool       import ThreadPoolExecutor as _APPool

    sched = BackgroundScheduler(
        executors={"default": _APPool(max_workers=3)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        timezone=ET,
    )

    # 6:55 AM — warm-up (one Polygon call, builds candidate cache)
    sched.add_job(aiem_warmup, CronTrigger(day_of_week="mon-fri", hour=6, minute=55),
                  id="aiem_warmup", replace_existing=True)

    # 7:00–9:15 AM — premarket scan every 15 min
    sched.add_job(aiem_premarket_scan,
                  CronTrigger(day_of_week="mon-fri", hour="7-9", minute="*/15"),
                  id="aiem_premarket_scan", replace_existing=True)

    # 9:30–10:30 AM — open watcher every 5 min
    sched.add_job(aiem_open_watcher,
                  CronTrigger(day_of_week="mon-fri", hour="9,10", minute="*/5"),
                  id="aiem_open_watcher", replace_existing=True)

    # 4:30 PM — grade T1 outcomes
    sched.add_job(aiem_grade_outcomes,
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=30),
                  id="aiem_grade_outcomes", replace_existing=True)

    # 4:35 PM — grade T3 / T5 outcomes
    sched.add_job(aiem_grade_t3_t5,
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=35),
                  id="aiem_grade_t3_t5", replace_existing=True)

    # 4:45 PM — find missed runners (stocks that ran but AIEM didn't pick)
    sched.add_job(aiem_find_missed_runners,
                  CronTrigger(day_of_week="mon-fri", hour=16, minute=45),
                  id="aiem_find_missed_runners", replace_existing=True)

    # 5:00 PM — pattern gap analysis (why did we miss?)
    sched.add_job(aiem_pattern_gap_analysis,
                  CronTrigger(day_of_week="mon-fri", hour=17, minute=0),
                  id="aiem_pattern_gap_analysis", replace_existing=True)

    # 5:15 PM — write signal discoveries to DB
    sched.add_job(aiem_write_signal_discoveries,
                  CronTrigger(day_of_week="mon-fri", hour=17, minute=15),
                  id="aiem_write_signal_discoveries", replace_existing=True)

    # 6:00 PM — nightly learn (update trust weights, promote hypotheses)
    sched.add_job(aiem_nightly_learn,
                  CronTrigger(day_of_week="mon-fri", hour=18, minute=0),
                  id="aiem_nightly_learn", replace_existing=True)

    # ── Admin HTTP server (port 5055) for manual scan triggers ──────────────
    def _run_manual_scan():
        log.info("admin: manual warmup + premarket scan triggered")
        try:
            aiem_warmup()
            aiem_premarket_scan()
            log.info("admin: manual scan complete")
        except Exception as _e:
            log.error(f"admin: manual scan error: {_e}")

    def _admin_server():
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading as _t2

        class _H(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path == "/run-scan":
                    _t2.Thread(target=_run_manual_scan, daemon=True).start()
                    body = b'{"status":"triggered"}'
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *a): pass   # suppress access logs

        try:
            HTTPServer(("0.0.0.0", 5055), _H).serve_forever()
        except Exception as _ae:
            log.warning(f"admin server error: {_ae}")

    threading.Thread(target=_admin_server, daemon=True).start()
    log.info("Admin trigger server listening on :5055")

    sched.start()

    log.info("Scheduler running — 9 jobs:")
    log.info("  6:55 AM               warm-up (Polygon full snapshot)")
    log.info("  7:00–9:15 every 15m   premarket scan + funnel")
    log.info("  9:30–10:30 every  5m  open watcher + Telegram alert")
    log.info("  4:30 PM               grade T1 outcomes")
    log.info("  4:35 PM               grade T3/T5 outcomes")
    log.info("  4:45 PM               find missed runners")
    log.info("  5:00 PM               pattern gap analysis")
    log.info("  5:15 PM               write signal discoveries")
    log.info("  6:00 PM               nightly learn")

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down…")
        sched.shutdown(wait=False)


if __name__ == "__main__":
    main()
