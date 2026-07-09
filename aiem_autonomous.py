# ============================================================
# AIEM AUTONOMOUS ENGINE — aiem_autonomous.py
# Completely standalone — zero Flask dependency
# Runs as a separate process managed by the aiem-process workflow
# ============================================================

import os
import time
import logging
import json        as _json_h
import threading   as _health_thr
import psycopg2
import psycopg2.pool
import requests
from datetime        import datetime, date, timedelta
from zoneinfo        import ZoneInfo
from http.server     import HTTPServer, BaseHTTPRequestHandler
from apscheduler.schedulers.blocking import BlockingScheduler

import sys as _sys
_API_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "artifacts", "stock-scanner-api")
if _API_DIR not in _sys.path:
    _sys.path.insert(0, _API_DIR)

try:
    import telegram_charts as _tg_charts
except Exception as _tg_charts_imp_err:
    _tg_charts = None
    print(f"[telegram_charts] unavailable, chart alerts disabled: {_tg_charts_imp_err}")

try:
    import behavioral_fingerprint as _bfp
except Exception as _bfp_imp_err:
    _bfp = None
    print(f"[behavioral_fingerprint] unavailable, fingerprint-match reasons disabled: {_bfp_imp_err}")

def _aiem_send_chart(kind, title, tickers, caption=None):
    """Best-effort chart-image companion to a text alert. Never raises,
    never blocks/affects the text-alert flow it follows."""
    if not _tg_charts or not tickers:
        return False
    try:
        return _tg_charts.send_ticker_chart_alert(kind, title, tickers, caption=caption)
    except Exception as e:
        log.warning(f"[chart] {kind} send failed: {e}")
        return False

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [AIEM] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('AIEM')

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────
ET = ZoneInfo('America/New_York')

MAX_MARKET_CAP          = 500_000_000
MAX_FLOAT_SHARES        = 20_000_000
MIN_PREMARKET_VOLUME    = 25_000   # applies to prevDay.v (premarket proxy)
MIN_GAP_PCT             = 2.0
MAX_PRICE               = 20.0
MIN_PRICE               = 0.50
CONFIDENCE_ALERT_THRESHOLD = 50    # lowered: volume signals can't fire premarket

# ─────────────────────────────────────────────────────────────
# ENVIRONMENT VARIABLES
# ─────────────────────────────────────────────────────────────
POLYGON_API_KEY = os.environ.get('POLYGON_API_KEY', '')
TWILIO_SID      = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_TOKEN    = os.environ.get('TWILIO_AUTH_TOKEN', '')
# TWILIO_FROM_NUMBER is the correct secret name; fall back to legacy name
TWILIO_FROM     = (os.environ.get('TWILIO_FROM_NUMBER', '')
                   or os.environ.get('TWILIO_PHONE_NUMBER', ''))
TWILIO_TO       = os.environ.get('TWILIO_TO_NUMBER', '')
DATABASE_URL    = os.environ.get('DATABASE_URL', '')
_HEALTH_PORT    = int(os.environ.get('AIEM_HEALTH_PORT', '5051'))

# ─────────────────────────────────────────────────────────────
# DATABASE POOL
# ─────────────────────────────────────────────────────────────
_AIEM_POOL     = None
_scheduler_ref        = None   # set in main() so health endpoint can read .running
_polygon_retry_fired: set = set()  # dates (str) where a retry was already queued

def _init_pool():
    global _AIEM_POOL
    if _AIEM_POOL is None:
        _AIEM_POOL = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=5, dsn=DATABASE_URL
        )
        log.info("DB pool initialized (min=1 max=5)")
        # Ensure job_log table exists
        try:
            _c = _AIEM_POOL.getconn()
            _cur = _c.cursor()
            _cur.execute("""
                CREATE TABLE IF NOT EXISTS job_log (
                    id       BIGSERIAL    PRIMARY KEY,
                    job_name TEXT         NOT NULL,
                    ran_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
                )
            """)
            _cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_ticker_reference_cache (
                    ticker       TEXT PRIMARY KEY,
                    market_cap   NUMERIC,
                    float_shares NUMERIC,
                    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # ── Watch-criteria feedback loop (added 2026-06-30) ──────────────
            # Concrete, threshold-level criteria extracted from today's missed
            # runners (RSI extreme, volume buildup, EOD range position, 5-day
            # grind streak, premarket gap+volume) that tomorrow's premarket
            # scan / missed-morning-check actually re-screen the live universe
            # for. This is what turns "lesson learned" into AIEM actually
            # going to look for the same pattern again, instead of just
            # journaling it.
            _cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_watch_criteria (
                    id               BIGSERIAL    PRIMARY KEY,
                    discovered_date  DATE         NOT NULL,
                    expires_at       DATE         NOT NULL,
                    origin_ticker    TEXT         NOT NULL,
                    origin_bucket    TEXT,
                    origin_move_pct  NUMERIC,
                    reason_cat       TEXT         NOT NULL,
                    metric_name      TEXT         NOT NULL,
                    operator         TEXT         NOT NULL,
                    threshold_value  NUMERIC      NOT NULL,
                    observed_value   NUMERIC,
                    lookback_days    INT          DEFAULT 1,
                    source_text      TEXT,
                    validation_n         INT,
                    validation_win_rate  NUMERIC,
                    validation_avg_next_day NUMERIC,
                    active           BOOLEAN      NOT NULL DEFAULT TRUE,
                    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
                )
            """)
            _cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_awc_active_expiry
                ON aiem_watch_criteria (active, expires_at)
            """)
            _cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_watch_alerts (
                    id           BIGSERIAL   PRIMARY KEY,
                    criteria_id  BIGINT      NOT NULL REFERENCES aiem_watch_criteria(id),
                    ticker       TEXT        NOT NULL,
                    alert_date   DATE        NOT NULL,
                    job_name     TEXT,
                    observed_value NUMERIC,
                    sent_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (criteria_id, ticker, alert_date)
                )
            """)
            _c.commit()
            _cur.close()
            _AIEM_POOL.putconn(_c)
            log.info("job_log + aiem_ticker_reference_cache + aiem_watch_criteria/alerts tables ready")
        except Exception as _jl_e:
            log.warning(f"job_log table init: {_jl_e}")

def _get_conn():
    global _AIEM_POOL
    if _AIEM_POOL is None:
        _init_pool()
    return _AIEM_POOL.getconn()

def _put_conn(conn):
    global _AIEM_POOL
    if _AIEM_POOL and conn:
        _AIEM_POOL.putconn(conn)


def _log_job(name: str):
    """Insert one row into job_log after each scheduler job completes."""
    conn = None
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("INSERT INTO job_log (job_name) VALUES (%s)", (name,))
        conn.commit()
        cur.close()
    except Exception as e:
        log.warning(f"_log_job({name}): {e}")
    finally:
        if conn:
            _put_conn(conn)


def _logged_job(fn):
    """Decorator: call _log_job after every scheduler job run, success or not."""
    def _w(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            try:
                _log_job(fn.__name__)
            except Exception:
                pass
    _w.__name__ = fn.__name__
    return _w


# ─────────────────────────────────────────────────────────────
# HEALTH SERVER — lightweight stdlib HTTPServer on _HEALTH_PORT
# GET /api/health → {"status","scheduler","db","jobs_fired_today","last_job"}
# ─────────────────────────────────────────────────────────────
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/api/health':
            self.send_response(404)
            self.end_headers()
            return

        health = {
            "status":          "ok",
            "timestamp":       datetime.utcnow().isoformat(),
            "scheduler":       "unknown",
            "db":              "unknown",
            "jobs_fired_today": None,
            "last_job":        None,
        }

        # Scheduler state
        try:
            health["scheduler"] = "running" if (_scheduler_ref and _scheduler_ref.running) else "stopped"
        except Exception:
            health["scheduler"] = "error"

        # DB check + job_log query
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
            cur  = conn.cursor()
            cur.execute("""
                SELECT COUNT(*), MAX(ran_at)
                FROM job_log
                WHERE ran_at::date = CURRENT_DATE
            """)
            row = cur.fetchone()
            if row:
                health["jobs_fired_today"] = row[0]
                health["last_job"] = row[1].isoformat() if row[1] else None
            cur.close()
            conn.close()
            health["db"] = "connected"
        except Exception as e:
            health["db"]     = f"error: {e}"
            health["status"] = "degraded"

        body = _json_h.dumps(health).encode()
        self.send_response(200)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise from stdlib logging


def _start_health_server():
    srv = HTTPServer(("0.0.0.0", _HEALTH_PORT), _HealthHandler)
    t = _health_thr.Thread(target=srv.serve_forever, daemon=True, name="aiem-health")
    t.start()
    log.info(f"Health endpoint: http://0.0.0.0:{_HEALTH_PORT}/api/health")


# ─────────────────────────────────────────────────────────────
# SMS — standalone (no dependency on main.py _send_sms)
# ─────────────────────────────────────────────────────────────
# QUARANTINED 2026-07-09: this function sends directly to the Telegram API via
# urllib, bypassing alert_gateway.log_alert(), the telegram_alert_ledger, and the
# entire trust/grading pipeline. aiem_autonomous.py is not currently imported or
# invoked by any running workflow, so this is dormant, not an active leak — but
# do NOT call this function again without first rewiring it through
# alert_gateway (see aiem_process.py's _tg_send for the reference pattern).
def _tg_send_QUARANTINED_DO_NOT_USE(text: str) -> bool:
    """Mirror a message to Telegram owner chat. Silent no-op when not configured."""
    import urllib.request as _ulr, json as _jmod
    token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        payload = _jmod.dumps({"chat_id": chat_id, "text": text}).encode()
        req = _ulr.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}
        )
        with _ulr.urlopen(req, timeout=8) as r:
            return _jmod.loads(r.read()).get("ok", False)
    except Exception as _e:
        log.warning(f"[telegram] {_e}")
        return False


def _aiem_send_sms(message: str):
    _tg_send_QUARANTINED_DO_NOT_USE(f"🤖 {message}")
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and TWILIO_TO):
        log.info(f"[SMS disabled] {message[:80]}")
        return
    try:
        from twilio.rest import Client as _TC
        _TC(TWILIO_SID, TWILIO_TOKEN).messages.create(
            body=message, from_=TWILIO_FROM, to=TWILIO_TO
        )
        log.info(f"SMS sent: {message[:60]}...")
    except Exception as e:
        log.error(f"SMS error: {e}")

# ─────────────────────────────────────────────────────────────
# POLYGON HELPERS
# ─────────────────────────────────────────────────────────────
POLYGON_BASE = "https://api.polygon.io"

def _poly(path: str, params: dict = None, timeout: int = 30) -> dict:
    """Authenticated Polygon GET — returns parsed JSON or {}."""
    try:
        p = {'apiKey': POLYGON_API_KEY}
        if params:
            p.update(params)
        r = requests.get(f"{POLYGON_BASE}{path}", params=p, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"Polygon error {path}: {e}")
        return {}

def _aiem_bulk_snapshot() -> list:
    """Pull ALL US stocks snapshot (~8K tickers) in one call — ~3 seconds."""
    data = _poly('/v2/snapshot/locale/us/markets/stocks/tickers',
                 {'include_otc': 'false'}, timeout=45)
    tickers = data.get('tickers', [])
    log.info(f"Bulk snapshot: {len(tickers)} tickers")
    return tickers

def _aiem_get_snapshot(ticker: str) -> dict:
    """Single-ticker snapshot for deep scoring."""
    return _poly(f'/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}',
                 timeout=10).get('ticker', {})

def _aiem_get_ohlcv(ticker: str, days: int = 5) -> list:
    """Recent daily OHLCV bars."""
    end   = date.today()
    start = end - timedelta(days=days + 5)
    return _poly(f'/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}',
                 {'adjusted': 'true'}, timeout=10).get('results', [])

def _aiem_get_grouped_daily(for_date: date = None) -> list:
    """All stocks OHLCV for a given date — used in missed runner analysis."""
    d = for_date or date.today()
    results = _poly(f'/v2/aggs/grouped/locale/us/market/stocks/{d}',
                    {'adjusted': 'true'}, timeout=45).get('results', [])
    log.info(f"Grouped daily {d}: {len(results)} stocks")
    return results

def _try_fetch_and_store_daily(conn, for_date: date) -> int:
    """
    Live fallback: fetch Polygon grouped-daily for `for_date` and upsert into
    polygon_market_daily.  Called when the nightly job missed a day (Polygon
    was briefly down).  Returns the number of new rows written.
    """
    results = _aiem_get_grouped_daily(for_date)
    if not results:
        log.warning(f"[polygon_retry] Polygon returned 0 rows for {for_date} — still down?")
        return 0
    cur  = conn.cursor()
    rows = 0
    for r in results:
        t = r.get('T', '')
        if not t or len(t) > 10:
            continue
        o, c   = r.get('o') or 0, r.get('c') or 0
        h, l   = r.get('h') or 0, r.get('l') or 0
        v, vw  = r.get('v') or 0, r.get('vw') or 0
        pc     = r.get('pc') or 0
        gap    = round(((c - pc) / pc * 100), 4) if pc > 0 else 0
        try:
            cur.execute("""
                INSERT INTO polygon_market_daily
                    (scan_date, ticker, close_price, open_price, high_price,
                     low_price, vwap, volume, prev_close, gap_pct)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (scan_date, ticker) DO NOTHING
            """, (for_date, t, c, o, h, l, vw, v, pc, gap))
            rows += cur.rowcount
        except Exception:
            pass
    conn.commit()
    log.info(f"[polygon_retry] ✅ stored {rows} rows for {for_date}")
    return rows


def _schedule_polygon_retry(label: str = ""):
    """
    Schedule a one-shot re-run of aiem_premarket_scan 15 minutes from now.
    Only fires once per calendar date so quiet market days don't loop forever.
    """
    today_str = str(date.today())
    if today_str in _polygon_retry_fired:
        log.info("[polygon_retry] retry already queued for today — skipping")
        return
    if _scheduler_ref is None:
        log.warning("[polygon_retry] scheduler not available — cannot queue retry")
        return
    _polygon_retry_fired.add(today_str)
    retry_dt = datetime.now(ET) + timedelta(minutes=15)
    try:
        _scheduler_ref.add_job(
            _logged_job(aiem_premarket_scan),
            'date',
            run_date=retry_dt,
            id='aiem_premarket_retry',
            replace_existing=True,
            misfire_grace_time=600,
        )
        log.warning(
            f"[polygon_retry] 🔄 retry scheduled for {retry_dt.strftime('%H:%M ET')}"
            + (f" — reason: {label}" if label else "")
        )
    except Exception as e:
        log.error(f"[polygon_retry] could not schedule retry: {e}")


def _aiem_get_ticker_details(ticker: str) -> dict:
    """Float, market cap, shares outstanding from Polygon reference."""
    return _poly(f'/v3/reference/tickers/{ticker}', timeout=10).get('results', {})

_TICKER_REF_CACHE_TTL_DAYS = 7

def _aiem_get_ticker_reference_cached(conn, ticker: str) -> dict:
    """Market cap + float for `ticker`, served from aiem_ticker_reference_cache
    when fresh (<7d old); falls back to a live Polygon call on cache miss/stale
    and upserts the result. Keeps cap-bucketing cheap even when the missed-mover
    candidate pool is large."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT market_cap, float_shares, updated_at
            FROM aiem_ticker_reference_cache WHERE ticker = %s
        """, (ticker,))
        row = cur.fetchone()
        if row and row[2] and (datetime.now(row[2].tzinfo) - row[2]).days < _TICKER_REF_CACHE_TTL_DAYS:
            return {'market_cap': float(row[0] or 0), 'float_shares': float(row[1] or 0)}

        details  = _aiem_get_ticker_details(ticker)
        mkt_cap  = float(details.get('market_cap') or 0)
        float_sh = float(details.get('share_class_shares_outstanding') or 0)
        cur.execute("""
            INSERT INTO aiem_ticker_reference_cache (ticker, market_cap, float_shares, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (ticker) DO UPDATE
                SET market_cap = EXCLUDED.market_cap,
                    float_shares = EXCLUDED.float_shares,
                    updated_at = NOW()
        """, (ticker, mkt_cap or None, float_sh or None))
        conn.commit()
        return {'market_cap': mkt_cap, 'float_shares': float_sh}
    except Exception as e:
        log.warning(f"ticker_reference_cache({ticker}): {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return {'market_cap': 0, 'float_shares': 0}


def _aiem_cap_bucket(market_cap: float):
    """Bucket a market cap into micro/small/mid/large. None = unknown (omit from report)."""
    if not market_cap or market_cap <= 0:
        return None
    if market_cap < 300_000_000:
        return 'micro'
    if market_cap < 2_000_000_000:
        return 'small'
    if market_cap < 10_000_000_000:
        return 'mid'
    return 'large'


_CAP_BUCKET_LABELS = {
    'micro': 'MICRO CAP (<$300M)',
    'small': 'SMALL CAP ($300M-$2B)',
    'mid':   'MID CAP ($2B-$10B)',
    'large': 'LARGE CAP (>$10B)',
}


def _aiem_behavioral_why(conn, ticker: str, move_date: date) -> dict:
    """Check whether `ticker`'s 14-dim behavioral fingerprint, computed from the
    days BEFORE `move_date`, resembles a known pre-move template (reuses the
    SAME fingerprint math + pre_move_templates library as main.py's behavioral
    engine via the shared behavioral_fingerprint module — no separate process
    call needed, both processes read the same DB table).
    Returns {'matched': bool, 'similarity': float, 'matched_ticker': str|None}."""
    if _bfp is None:
        return {'matched': False, 'similarity': 0.0, 'matched_ticker': None}
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT close_price, open_price, high_price, low_price,
                   vwap, volume, prev_close, gap_pct, rvol,
                   close_strength, range_pct
            FROM polygon_market_daily
            WHERE ticker = %s AND scan_date < %s
            ORDER BY scan_date DESC LIMIT 14
        """, (ticker, move_date))
        hist = cur.fetchall()
        if len(hist) < 3:
            return {'matched': False, 'similarity': 0.0, 'matched_ticker': None}

        rows_pre = [
            dict(close_price=r[0], open_price=r[1], high_price=r[2],
                 low_price=r[3], vwap=r[4], volume=r[5],
                 prev_close=r[6], gap_pct=r[7], rvol=r[8],
                 close_strength=r[9], range_pct=r[10])
            for r in hist
        ]
        fp = _bfp.compute_fingerprint(rows_pre)
        if fp is None:
            return {'matched': False, 'similarity': 0.0, 'matched_ticker': None}

        sim, best_match = _bfp.best_template_match(
            cur, fp, exclude_ticker=ticker, exclude_move_date=move_date
        )
        return {
            'matched': sim >= 0.92,
            'similarity': round(sim, 4),
            'matched_ticker': best_match[0] if best_match else None,
        }
    except Exception as e:
        log.warning(f"behavioral_why({ticker}): {e}")
        return {'matched': False, 'similarity': 0.0, 'matched_ticker': None}

def _aiem_get_news(ticker: str) -> list:
    """Recent news/catalyst for information-lag detection."""
    cutoff = (datetime.now() - timedelta(hours=12)).strftime('%Y-%m-%dT%H:%M:%SZ')
    return _poly('/v2/reference/news',
                 {'ticker': ticker, 'published_utc.gte': cutoff, 'limit': 5},
                 timeout=10).get('results', [])

# ─────────────────────────────────────────────────────────────
# SIGNAL TRUST WEIGHTS
# ─────────────────────────────────────────────────────────────
def _load_trust_weights(conn) -> dict:
    """Load AIEM's learned signal weights from DB (context=AIEM_MICROCAP)."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT signal_name, trust_weight, rolling_win_rate, n_outcomes_observed
            FROM signal_trust_weights
            WHERE context_bucket = 'AIEM_MICROCAP'
            ORDER BY trust_weight DESC
        """)
        rows = cur.fetchall()
        weights = {}
        for name, trust, win_rate, n in rows:
            weights[name] = {
                'trust':    float(trust    or 1.0),
                'win_rate': float(win_rate or 0.5),
                'n':        n or 0,
            }
        log.info(f"Loaded {len(weights)} signal trust weights")
        return weights
    except Exception as e:
        log.error(f"Trust weight load error: {e}")
        return {}

# ─────────────────────────────────────────────────────────────
# PRICING INEFFICIENCY DETECTION
# Six types AIEM hunts in the microcap universe
# ─────────────────────────────────────────────────────────────
def _detect_pricing_inefficiencies(ticker: str, snap: dict,
                                   details: dict, news: list) -> dict:
    inefficiencies = {}
    try:
        day        = snap.get('day', {})
        prev_day   = snap.get('prevDay', {})
        last_quote = snap.get('lastQuote', {})
        last_trade = snap.get('lastTrade', {})

        current_price = last_trade.get('p') or day.get('c') or 0
        prev_close    = prev_day.get('c') or 0
        volume        = day.get('v') or 0
        bid           = last_quote.get('P') or 0
        ask           = last_quote.get('p') or 0
        float_shares  = details.get('share_class_shares_outstanding') or 0
        short_int     = details.get('short_interest') or 0
        gap_pct       = ((current_price - prev_close) / prev_close * 100) if prev_close else 0

        # 1. INFORMATION LAG — news hit recently but price barely moved
        if news and news[0].get('published_utc'):
            try:
                age_hrs = (datetime.now() - datetime.fromisoformat(
                    news[0]['published_utc'].replace('Z', '+00:00')
                ).replace(tzinfo=None)).total_seconds() / 3600
                if age_hrs < 2 and gap_pct < 15:
                    inefficiencies['information_lag'] = {
                        'score': 18,
                        'description': f"News {age_hrs:.1f}hrs old, price only +{gap_pct:.1f}% — lag opportunity",
                    }
            except Exception:
                pass

        # 2. BID/ASK SPREAD TIGHTENING — smart money accumulating
        if bid > 0 and ask > 0:
            spread_pct = (ask - bid) / bid * 100
            if spread_pct < 0.3:
                inefficiencies['spread_tightening'] = {
                    'score': 15,
                    'description': f"Spread {spread_pct:.2f}% — unusually tight for microcap",
                }
            elif spread_pct < 0.8:
                inefficiencies['spread_tightening'] = {
                    'score': 8,
                    'description': f"Spread {spread_pct:.2f}% — tightening, watch closely",
                }

        # 3. SHORT SQUEEZE GEOMETRY — mathematical squeeze setup
        if float_shares > 0 and short_int > 0:
            short_pct    = short_int / float_shares * 100
            vol_vs_float = volume / float_shares * 100 if float_shares > 0 else 0
            if short_pct >= 20 and vol_vs_float >= 10:
                inefficiencies['squeeze_geometry'] = {
                    'score': 22,
                    'description': f"SQUEEZE: {short_pct:.1f}% short, {vol_vs_float:.1f}% float traded",
                }
            elif short_pct >= 15:
                inefficiencies['squeeze_geometry'] = {
                    'score': 14,
                    'description': f"Short {short_pct:.1f}% of float — squeeze fuel building",
                }

        # 4. CATALYST MISPRICING — significant catalyst, stock hasn't repriced
        if news and gap_pct < 20:
            keywords = ['fda', 'approval', 'contract', 'partnership', 'merger',
                        'acquisition', 'revenue', 'milestone', 'grant', 'award']
            titles = ' '.join(n.get('title', '').lower() for n in news)
            hits   = [k for k in keywords if k in titles]
            if hits:
                inefficiencies['catalyst_mispricing'] = {
                    'score': 20,
                    'description': f"Catalyst ({', '.join(hits[:2])}) but only {gap_pct:.1f}% move — underpriced",
                }

        # 5. HALT PATTERN — extreme gap + very low volume = just resumed
        if gap_pct >= 20 and volume < 100_000:
            inefficiencies['halt_pattern'] = {
                'score': 16,
                'description': f"Gap {gap_pct:.1f}% with {volume:,} vol — possible halt resume",
            }

        # 6. DARK POOL DIVERGENCE — price far from VWAP on low public volume
        vwap      = day.get('vw') or current_price
        price_dev = abs(current_price - vwap) / vwap * 100 if vwap else 0
        if price_dev > 3 and volume < 50_000:
            inefficiencies['dark_pool_divergence'] = {
                'score': 12,
                'description': f"Price {price_dev:.1f}% from VWAP with low volume — dark pool suspected",
            }

    except Exception as e:
        log.error(f"Inefficiency detection error {ticker}: {e}")

    return inefficiencies

# ─────────────────────────────────────────────────────────────
# AIEM MICROCAP SCORER
# Trust-weighted, inefficiency-aware confidence engine
# ─────────────────────────────────────────────────────────────
def _aiem_score_microcap(ticker: str, snap: dict, details: dict,
                          news: list, trust_weights: dict) -> tuple:
    """
    Returns (confidence_score 0-100, signal_basis, reasoning_text, predicted_move).
    Signals accumulate weighted scores; inefficiencies add bonus weight.
    """
    raw_score = 0.0
    max_score = 0.0
    signals   = {}
    reasoning = []

    try:
        day        = snap.get('day', {})
        prev_day   = snap.get('prevDay', {})
        last_trade = snap.get('lastTrade', {})

        price      = last_trade.get('p') or day.get('c') or 0
        prev_close = prev_day.get('c') or price or 1
        volume     = day.get('v') or 0
        avg_vol    = prev_day.get('v') or 1

        float_sh = details.get('share_class_shares_outstanding') or 999_000_000
        mkt_cap  = details.get('market_cap') or 999_000_000_000

        gap_pct   = ((price - prev_close) / prev_close * 100) if prev_close else 0
        vol_ratio = volume / avg_vol if avg_vol > 0 else 0

        def _add(name, base_weight, condition, desc):
            nonlocal raw_score, max_score
            t = trust_weights.get(name, {}).get('trust', 1.0)
            w = base_weight * t
            max_score += w
            if condition:
                signals[name] = round(w, 3)
                raw_score    += w
                if desc:
                    reasoning.append(desc)

        _add('gap_explosive',   20, gap_pct >= 20,          f"Explosive gap +{gap_pct:.1f}%")
        _add('gap_large',       15, 10 <= gap_pct < 20,     f"Large gap +{gap_pct:.1f}%")
        _add('gap_moderate',    10, 5  <= gap_pct < 10,     f"Moderate gap +{gap_pct:.1f}%")
        _add('gap_small',        5, 2  <= gap_pct < 5,      f"Small gap +{gap_pct:.1f}%")

        _add('volume_extreme',  22, vol_ratio >= 5,          f"Volume {vol_ratio:.1f}x avg — extreme")
        _add('volume_high',     16, 3 <= vol_ratio < 5,      f"Volume {vol_ratio:.1f}x avg — high")
        _add('volume_elevated',  8, 1.5 <= vol_ratio < 3,    f"Volume {vol_ratio:.1f}x avg")

        _add('float_micro',     20, float_sh < 5_000_000,   f"Micro float {float_sh/1e6:.1f}M — explosive")
        _add('float_low',       14, 5_000_000  <= float_sh < 10_000_000, f"Low float {float_sh/1e6:.1f}M")
        _add('float_medium',     7, 10_000_000 <= float_sh < 20_000_000, f"Float {float_sh/1e6:.1f}M")

        _add('mktcap_nano',     18, mkt_cap < 50_000_000,   f"Nano cap ${mkt_cap/1e6:.0f}M")
        _add('mktcap_micro',    12, 50_000_000  <= mkt_cap < 150_000_000, f"Micro cap ${mkt_cap/1e6:.0f}M")
        _add('mktcap_small',     6, 150_000_000 <= mkt_cap < 500_000_000, f"Small cap ${mkt_cap/1e6:.0f}M")

        _add('price_sweet_spot', 8, 1.0 <= price <= 10.0,   f"Price ${price:.2f} in breakout zone")
        _add('catalyst_present',15, len(news) > 0,
             f"Catalyst: {news[0].get('title','')[:50]}" if news else '')

        # Gap + volume combo — strongest single microcap setup
        _add('gap_vol_combo',   25, gap_pct >= 10 and vol_ratio >= 3,
             f"POWER COMBO: gap {gap_pct:.1f}% + vol {vol_ratio:.1f}x")

        # Pricing inefficiency bonus
        for name, data in _detect_pricing_inefficiencies(ticker, snap, details, news).items():
            t = trust_weights.get(name, {}).get('trust', 1.0)
            w = data['score'] * t
            max_score += w
            raw_score += w
            signals[name] = round(w, 3)
            reasoning.append(data['description'])

    except Exception as e:
        log.error(f"Score error {ticker}: {e}")

    confidence = (raw_score / max_score * 100) if max_score > 0 else 0
    confidence = min(100.0, round(confidence, 1))

    if confidence >= 85:
        predicted = "VERY HIGH CONVICTION — strong breakout expected"
    elif confidence >= 72:
        predicted = "HIGH CONVICTION — breakout setup confirmed"
    elif confidence >= 58:
        predicted = "MODERATE — watch open behavior closely"
    else:
        predicted = "LOW — monitor only"

    return confidence, ", ".join(signals.keys()), " | ".join(reasoning) or "Insufficient signals", predicted


# ─────────────────────────────────────────────────────────────
# MULTI-DAY CONTEXT — continuation vs exhaustion classifier
# This is the core of AIEM's self-learning: it must distinguish
# a stock that is BUILDING (buy) from one that already EXPLODED (fade).
# ─────────────────────────────────────────────────────────────
def _get_multiday_context(tickers: list, conn) -> dict:
    """Batch-fetch last 10 trading days for all candidates in one query."""
    if not tickers:
        return {}
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, scan_date, close_price, open_price, volume,
                   gap_pct, rvol, vwap
            FROM polygon_market_daily
            WHERE ticker = ANY(%s)
              AND scan_date >= (SELECT MAX(scan_date) FROM polygon_market_daily)
                              - INTERVAL '12 days'
            ORDER BY ticker, scan_date ASC
        """, (tickers,))
        cols = ['ticker', 'scan_date', 'close_price', 'open_price',
                'volume', 'gap_pct', 'rvol', 'vwap']
        result: dict = {}
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            result.setdefault(d['ticker'], []).append(d)
        return result
    except Exception as e:
        log.error(f"multiday_context error: {e}")
        return {}


def _score_multiday(history: list) -> dict:
    """
    Given N days of OHLCV history (sorted oldest→newest), classify the most
    recent day's move as CONTINUATION (reward) or EXHAUSTION (penalise + cap).

    Returns dict: bonus (int), penalty (int), cap (0-100), label (str)

    AIEM calls this for every candidate. Over time, as outcomes grade out
    and trust weights update, the signals here gain/lose influence — but the
    exhaustion cap is a hard rule that overrides low trust because a stock
    that gapped 107% yesterday almost never continues the next day.
    """
    out = {'bonus': 0, 'penalty': 0, 'cap': 100, 'label': ''}
    if len(history) < 2:
        return out

    yesterday  = history[-1]
    prior      = history[:-1]

    yesterday_gap = float(yesterday.get('gap_pct') or 0)
    prior_gaps    = [abs(float(d.get('gap_pct') or 0)) for d in prior]
    avg_prior_gap = sum(prior_gaps) / len(prior_gaps) if prior_gaps else 0

    # Count consecutive up-closes ending on yesterday
    up_days = 0
    for i in range(len(history) - 1, 0, -1):
        c = float(history[i].get('close_price') or 0)
        p = float(history[i-1].get('close_price') or 0)
        if c > 0 and p > 0 and c > p:
            up_days += 1
        else:
            break

    # Volume trend: is vol building day over day? (last 3 days)
    vols = [float(d.get('volume') or 0) for d in history[-3:]]
    vol_rising = (len(vols) == 3 and vols[0] > 0 and vols[2] > vols[0] * 1.15)

    # Spike test: yesterday's gap is 3× the average of prior days
    # → single-day explosion, not a trend
    is_spike = (yesterday_gap > 15 and
                yesterday_gap > max(avg_prior_gap * 3.0, 8.0))

    # ── CONTINUATION: quiet multi-day buildup — exactly what AIEM should buy
    if up_days >= 3 and not is_spike:
        out['bonus'] = 20
        out['label'] = f"CONTINUATION {up_days}d buildup"
        if vol_rising:
            out['bonus'] += 8
            out['label'] += "+vol"
    elif up_days == 2 and not is_spike:
        out['bonus'] = 10
        out['label'] = "2d buildup"

    # ── EXHAUSTION: single-day explosion, overextended — AIEM should avoid
    if is_spike:
        out['penalty'] = 30
        out['label']   = (f"EXHAUSTION {yesterday_gap:.0f}% spike "
                          f"(prior avg {avg_prior_gap:.1f}%)")
        if yesterday_gap >= 50:
            out['cap']    = 55   # hard cap: never rank these as top picks
            out['label'] += " ← FADE RISK"
        elif yesterday_gap >= 30:
            out['cap']    = 65
            out['label'] += " ← HIGH FADE RISK"

    return out


# ─────────────────────────────────────────────────────────────
# JOB 1: PREMARKET SCAN — every 15 min 7:00–9:30 AM ET
# ─────────────────────────────────────────────────────────────
def aiem_premarket_scan():
    now_et = datetime.now(ET)
    log.info(f"=== PREMARKET SCAN {now_et.strftime('%H:%M ET')} ===")

    conn = None
    try:
        conn = _get_conn()

        # ── Layer D · RegimeDetector ─────────────────────────────────────────
        # regime_detector.py: real SPY + VIX data, 15-min cache, never raises.
        # sit_out → abort scan entirely.  reduce_exposure → apply multiplier.
        _regime_conf_mult = 1.0
        _regime_pos_mult  = 1.0
        _regime_label     = "unknown"
        try:
            from regime_detector import get_current_regime as _get_regime
            _reg = _get_regime("")   # db_url arg unused; module uses yfinance
            _regime_label    = _reg.get("regime", "unknown")
            _regime_conf_mult = _reg.get("multipliers", {}).get("confidence_multiplier", 1.0)
            _regime_pos_mult  = _reg.get("multipliers", {}).get("position_size_multiplier", 1.0)
            if _regime_label == "sit_out":
                log.warning(f"[regime] 🛑 sit_out — "
                            f"{_reg.get('note') or _reg.get('recommendation','')} "
                            f"— aborting premarket scan")
                return
            log.info(f"[regime] {_regime_label}  "
                     f"conf_mult={_regime_conf_mult}  pos_mult={_regime_pos_mult}")
        except Exception as _rd_err:
            log.warning(f"[regime] unavailable ({_rd_err}) — full exposure default")

        # Step 1: Pull candidates from polygon_market_daily (no snapshot API needed).
        # The Polygon Starter tier doesn't support bulk snapshots; the DB is populated
        # nightly from the grouped-daily endpoint which IS included in Starter.
        cur = conn.cursor()
        cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
        latest_date = cur.fetchone()[0]
        if not latest_date:
            log.warning("polygon_market_daily is empty — attempting live Polygon fetch")
            # Polygon may have been down during the nightly backfill; try now.
            _yesterday = date.today() - timedelta(days=1)
            _rows = _try_fetch_and_store_daily(conn, _yesterday)
            if _rows == 0:
                _schedule_polygon_retry("DB empty + live fetch also returned 0")
                return
            cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
            latest_date = cur.fetchone()[0]
            if not latest_date:
                _schedule_polygon_retry("DB still empty after fetch attempt")
                return

        # If the DB is more than 2 calendar days stale on a weekday, try to
        # backfill the missing date rather than scanning on stale data.
        _today = date.today()
        if _today.weekday() < 5 and (_today - latest_date).days > 2:
            log.warning(f"[polygon_retry] DB stale: latest={latest_date}, today={_today} "
                        f"({(_today - latest_date).days}d gap) — fetching missing data")
            _missing = latest_date + timedelta(days=1)
            _rows = _try_fetch_and_store_daily(conn, _missing)
            if _rows > 0:
                cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
                latest_date = cur.fetchone()[0]
            else:
                _schedule_polygon_retry(f"stale DB ({(_today - latest_date).days}d) + fetch returned 0")
                return

        cur.execute("""
            SELECT ticker, close_price, open_price, high_price, volume,
                   vwap, gap_pct, rvol, close_strength, range_pct, prev_close
            FROM polygon_market_daily
            WHERE scan_date = %s
              AND close_price BETWEEN %s AND %s
              AND gap_pct    >= %s
              AND volume     >= %s
            ORDER BY gap_pct * COALESCE(rvol, 1) DESC
            LIMIT 200
        """, (latest_date, MIN_PRICE, MAX_PRICE, MIN_GAP_PCT, MIN_PREMARKET_VOLUME))
        _cols = ['ticker','close_price','open_price','high_price','volume',
                 'vwap','gap_pct','rvol','close_strength','range_pct','prev_close']
        candidates = [dict(zip(_cols, r)) for r in cur.fetchall()]

        log.info(f"DB scan {latest_date}: {len(candidates)} microcap candidates "
                 f"(gap≥{MIN_GAP_PCT}%, vol≥{MIN_PREMARKET_VOLUME:,}, price ${MIN_PRICE}-${MAX_PRICE})")
        if not candidates:
            log.warning("No candidates from polygon_market_daily — market may have been quiet")
            _schedule_polygon_retry("0 candidates in DB for latest_date")
            return

        # Step 2: Load trust weights
        trust_weights = _load_trust_weights(conn)

        # Step 2.5: Batch-fetch multi-day context for all candidates in ONE query.
        # This is what lets AIEM distinguish a 3-day buildup (buy) from a
        # single-day explosion (fade). TNMG +107% in one day = exhaustion.
        top_tickers  = [c['ticker'] for c in candidates[:100]]
        multiday_ctx = _get_multiday_context(top_tickers, conn)
        log.info(f"Multi-day context loaded for {len(multiday_ctx)} tickers")

        # ── Layer D · FeatureEngine ──────────────────────────────────────────
        # feature_engineering.py: volume trends + MA-relative features.
        # Batch-fetch 30 days of polygon_market_daily history for top 50 tickers
        # in a single query so we don't loop-query inside the scoring loop.
        _poly_hist: dict = {}
        _FEAT_ENG_OK = False
        try:
            import pandas as _pd
            from feature_engineering import build_feature_row as _build_feat
            _feat_tickers = [c['ticker'] for c in candidates[:50]]
            _fh_cur = conn.cursor()
            _fh_cur.execute("""
                SELECT ticker, scan_date, close_price, volume, rvol, gap_pct
                FROM polygon_market_daily
                WHERE ticker = ANY(%s)
                  AND scan_date >= CURRENT_DATE - INTERVAL '30 days'
                ORDER BY ticker, scan_date
            """, (_feat_tickers,))
            for _row in _fh_cur.fetchall():
                _t = _row[0]
                _poly_hist.setdefault(_t, []).append({
                    'date': _row[1], 'close_price': _row[2],
                    'volume': _row[3], 'rvol': _row[4], 'gap_pct': _row[5],
                })
            _FEAT_ENG_OK = True
            log.info(f"[feat_eng] ✅ history loaded for {len(_poly_hist)} tickers")
        except Exception as _fe_err:
            log.warning(f"[feat_eng] feature_engineering unavailable ({_fe_err}) — skipping")

        # Step 3: Deep score each candidate — adds ticker details + news from Polygon API
        # Layer A+B: try consolidated master first, fall back to split modules
        try:
            from aiem_master_part1 import (
                evaluate_signal_with_data           as _eval_staleness,
                apply_wall_street_pattern_with_data as _apply_ws,
            )
            log.info("[scan] aiem_master_part1 loaded (layers A+B consolidated)")
        except Exception as _mp1_err:
            log.warning("[scan] aiem_master_part1 unavailable (%s) — using split modules", _mp1_err)
            from staleness_filter import evaluate_signal_with_data as _eval_staleness
            from aiem_verification_and_trading_brain import (
                apply_wall_street_pattern_with_data as _apply_ws,
            )
        # Layer C: Intelligence upgrade (kill switch, news quality, sector heat,
        #          float/SI, time-of-day danger zones)
        try:
            from aiem_intelligence_upgrade import (
                is_kill_switch_active                   as _is_kill_switch,
                score_news_source_with_data             as _score_news_src,
                get_sector_conviction_penalty_with_data as _sector_penalty,
                get_float_and_si_with_data              as _float_si,
                apply_time_of_day                       as _time_of_day,
            )
            _INTEL_AVAILABLE = True
            log.info("[scan] aiem_intelligence_upgrade loaded (layer C — 5 systems)")
        except Exception as _iu_err:
            log.warning("[scan] aiem_intelligence_upgrade unavailable (%s) — layer C skipped", _iu_err)
            _INTEL_AVAILABLE = False
        _scan_ts = datetime.now(ZoneInfo("UTC"))
        scored = []
        for c in candidates[:50]:
            ticker = c['ticker']
            try:
                details  = _aiem_get_ticker_details(ticker)
                mkt_cap  = details.get('market_cap') or 0
                float_sh = details.get('share_class_shares_outstanding') or 0
                if mkt_cap  > MAX_MARKET_CAP  and mkt_cap  > 0:
                    continue
                if float_sh > MAX_FLOAT_SHARES and float_sh > 0:
                    continue

                news = _aiem_get_news(ticker)

                # Build a synthetic snapshot from DB fields for the scorer
                snap = {
                    'day': {
                        'c':  c['close_price'],
                        'o':  c['open_price'],
                        'h':  c['high_price'],
                        'v':  c['volume'],
                        'vw': c['vwap'],
                    },
                    'prevDay': {
                        'c': c['prev_close'],
                        'v': c['volume'],
                    },
                    'lastTrade': {'p': c['close_price']},
                    'lastQuote': {},
                }

                # ── Layer D · FeatureEngine ──────────────────────────────
                # Compute volume trend (3d/5d) + MA-20 relative from history.
                _feat_row = {}
                if _FEAT_ENG_OK and ticker in _poly_hist:
                    try:
                        _mdf = _pd.DataFrame(_poly_hist[ticker])
                        _mdf = _mdf.rename(columns={'close_price': 'close'})
                        _feat_row = _build_feat(
                            {'rvol': c.get('rvol'), 'gap_pct': c['gap_pct'],
                             'vol_oi': None, 'otm_pct': None, 'days_out': None,
                             'trade_date': latest_date, 'conviction': None},
                            _mdf,
                        )
                    except Exception:
                        _feat_row = {}
                conf, sig_basis, reasoning, predicted = _aiem_score_microcap(
                    ticker, snap, details, news, trust_weights
                )

                # Apply multi-day adjustment: penalise exhaustion, reward buildup
                md       = _score_multiday(multiday_ctx.get(ticker, []))
                adj_conf = max(0.0, min(float(md['cap']),
                                        conf + md['bonus'] - md['penalty']))
                adj_conf = round(adj_conf, 1)
                if md['label']:
                    reasoning = f"[{md['label']}] {reasoning}"
                log.info(f"  {ticker}: raw={conf:.1f}→adj={adj_conf:.1f} "
                         f"gap={c['gap_pct']:.1f}% rvol={c.get('rvol') or 0:.1f}x"
                         + (f" ({md['label']})" if md['label'] else ""))

                # Staleness filter: stale gap extension, catalyst decay,
                # move-day awareness, and VWAP day-2 exhaustion check.
                # Any SKIP verdict means the candidate is removed before ranking.
                verdict = _eval_staleness(
                    ticker, adj_conf,
                    multiday_ctx.get(ticker, []),
                    news, _scan_ts,
                    premarket_mode=True,
                )
                if verdict["action"] == "SKIP":
                    log.info(f"  {ticker}: ⛔ STALENESS SKIP — "
                             f"{verdict['reason']} | tags={verdict['tags']}")
                    continue
                adj_conf  = verdict["final_conviction"]
                if verdict["tags"]:
                    reasoning = (f"[STALENESS:{','.join(verdict['tags'])}] "
                                 f"{reasoning}")

                # Wall Street pattern layer — PIPE fade, delisting squeeze,
                # sympathy play, day-2 distribution, SPAC merger pop.
                # Runs after staleness filter; may reduce conviction further.
                ws = _apply_ws(ticker, verdict,
                               multiday_ctx.get(ticker, []), news)
                adj_conf = ws["final_conviction"]
                if adj_conf < 70:
                    ws_note = "; ".join(ws.get("ws_notes", []))
                    log.info(f"  {ticker}: ⛔ WS PATTERN SKIP "
                             f"conviction={adj_conf} | {ws_note}")
                    continue
                ws_pat = [t for t in ws.get("tags", [])
                          if t.startswith("PATTERN_")]
                if ws_pat:
                    reasoning = f"[WS:{','.join(ws_pat)}] {reasoning}"

                # ── Layer C: Intelligence upgrade ─────────────────────────────
                # Kill switch, news source quality, sector heat, float/SI,
                # time-of-day danger zones.  Runs after A+B; any SKIP/BLOCK
                # removes the candidate before it reaches the final ranking.
                if _INTEL_AVAILABLE:
                    # 1. Kill switch — hard block if daily loss limit is active
                    _killed, _kill_reason = _is_kill_switch(conn, date.today())
                    if _killed:
                        log.info(f"  {ticker}: 🛑 KILL SWITCH — {_kill_reason}")
                        continue

                    # 2. News source quality  (+5 SEC 8-K  /  −15 Reddit  etc.)
                    _src_score, _src_name, _src_delta = _score_news_src(news)
                    adj_conf = max(0.0, adj_conf + _src_delta)
                    if _src_delta != 0:
                        reasoning = f"[SRC:{_src_name}{_src_delta:+d}] {reasoning}"

                    # 3. Sector heat penalty
                    _sec_penalty, _sec_tag = _sector_penalty(details, conn)
                    adj_conf = max(0.0, adj_conf + _sec_penalty)
                    if _sec_tag not in ("SECTOR_NEUTRAL", "SECTOR_DATA_UNAVAILABLE"):
                        reasoning = f"[{_sec_tag}] {reasoning}"

                    # 4. Float + short interest  (squeeze candidate = +8)
                    _fd = _float_si(details)
                    adj_conf = max(0.0, adj_conf + _fd["conviction_delta"])
                    if _fd.get("squeeze_candidate"):
                        reasoning = f"[SQUEEZE] {reasoning}"

                    # 5. Time-of-day danger zones
                    adj_conf, _zone, _allow_entry, _threshold = _time_of_day(
                        adj_conf, _scan_ts, premarket_mode=True,
                    )
                    if not _allow_entry:
                        log.info(f"  {ticker}: ⛔ TIME ZONE {_zone} — entry not allowed")
                        continue
                    if adj_conf < 70:
                        log.info(f"  {ticker}: ⛔ LAYER C DROP conviction={adj_conf:.1f} zone={_zone}")
                        continue

                    log.info(
                        f"  {ticker}: ✅ Layer C  conviction={adj_conf:.1f}  zone={_zone}"
                        f"  src={_src_name}({_src_delta:+d})  sec={_sec_tag}"
                        f"  float={_fd['float_category']}"
                        + ("  🔥SQUEEZE" if _fd.get('squeeze_candidate') else "")
                    )

                # ── Layer D · Apply regime confidence multiplier ─────────────
                if _regime_conf_mult != 1.0:
                    adj_conf = round(adj_conf * _regime_conf_mult, 1)
                    if adj_conf < 70:
                        log.info(f"  {ticker}: ⛔ REGIME DROP "
                                 f"conviction={adj_conf:.1f} regime={_regime_label}")
                        continue
                    reasoning = f"[REGIME:{_regime_label.upper()}] {reasoning}"

                # ── Layer D · RiskEngine — Kelly position sizing ──────────────
                # Maps conviction score to an approximate win rate, then uses
                # Kelly criterion (1/4 fractional) to size the paper position.
                # Multiplied by regime position_size_multiplier for risk parity.
                _kelly_pct = 0.0
                try:
                    from position_sizing import kelly_position_size as _kelly_fn
                    # Map 60–100 conviction → 40–68% win rate
                    _mapped_wr = min(0.70, max(0.40,
                                    0.40 + (adj_conf - 60) * 0.003))
                    _kr = _kelly_fn(
                        win_rate=_mapped_wr,
                        avg_win_pct=4.0,
                        avg_loss_pct=2.5,
                        n_samples=50,
                        fractional_multiplier=0.25,
                    )
                    _kelly_pct = round(
                        float(_kr.recommended_fraction) * 100 * _regime_pos_mult, 2
                    )
                    log.info(f"  {ticker}: 💰 Kelly={_kelly_pct:.1f}%  "
                             f"wr={_mapped_wr:.0%}  regime_pos={_regime_pos_mult}")
                except Exception as _ks_err:
                    log.debug(f"  {ticker}: Kelly unavailable ({_ks_err})")

                scored.append({
                    'ticker':        ticker,
                    'conf':          adj_conf,
                    'sig_basis':     sig_basis,
                    'reasoning':     reasoning,
                    'predicted':     predicted,
                    'gap':           c['gap_pct'],
                    'volume':        c['volume'],
                    'rvol':          c.get('rvol') or 1.0,
                    'kelly_size_pct': _kelly_pct,
                    'regime':        _regime_label,
                    'vol_trend_3d':  _feat_row.get('volume_trend_3d'),
                    'vol_trend_5d':  _feat_row.get('volume_trend_5d'),
                    'ma20_relative': _feat_row.get('ma20_relative'),
                })
                time.sleep(0.1)
            except Exception as e:
                log.error(f"Deep score error {ticker}: {e}")
                continue

        if not scored:
            log.warning("No tickers survived deep scoring")
            # All candidates were filtered out — could be Polygon returning bad
            # data (empty details/news) during an outage, causing scoring to fail.
            # Schedule one retry 15 min later (once per day).
            _schedule_polygon_retry("0 tickers survived deep scoring")
            return

        scored.sort(key=lambda x: x['conf'], reverse=True)
        top_picks = scored[:10]

        # ── Layer D · PortfolioEngine — paper capital allocation ──────────────
        # portfolio_allocator.py: risk-parity + fractional-Kelly + correlation.
        # Derives signal stats from each pick's conviction score, splits a fixed
        # paper budget across picks, and stamps paper_alloc_usd onto each pick.
        try:
            from portfolio_allocator import allocate_portfolio as _alloc_portfolio
            import pandas as _pd_pa
            _sig_stats = {}
            for _p in top_picks:
                _wr = min(0.70, max(0.40, 0.40 + (_p['conf'] - 60) * 0.003))
                _sig_stats[_p['ticker']] = {
                    'win_rate':     _wr,
                    'avg_win_pct':  4.0,
                    'avg_loss_pct': 2.5,
                    'volatility':   max(0.05, 1.0 / max(_p.get('rvol') or 1.0, 0.1)),
                }
            _pa_budget = 10_000.0 * _regime_pos_mult
            _pa_result = _alloc_portfolio(
                _sig_stats,
                returns_history=_pd_pa.DataFrame(),
                total_paper_capital=_pa_budget,
            )
            _paper_alloc = _pa_result.get('dollar_allocation', {})
            log.info(
                f"[portfolio] PortfolioEngine  capital=${_pa_budget:,.0f}  "
                f"regime={_regime_label}: "
                + "  ".join(
                    f"{t}=${v:.0f}" for t, v in list(_paper_alloc.items())[:6]
                )
            )
            for _p in top_picks:
                _p['paper_alloc_usd'] = round(
                    _paper_alloc.get(_p['ticker'], 0.0), 2
                )
        except Exception as _pa_err:
            log.warning(f"[portfolio] portfolio_allocator unavailable ({_pa_err})")
            for _p in top_picks:
                _p['paper_alloc_usd'] = 0.0

        # Step 6: Write to aiem_predictions (replace today's set)
        today = date.today()
        cur   = conn.cursor()
        cur.execute("DELETE FROM aiem_predictions WHERE prediction_date = %s", (today,))
        for rank, pick in enumerate(top_picks, 1):
            cur.execute("""
                INSERT INTO aiem_predictions
                    (prediction_date, ticker, rank, confidence_score,
                     signal_basis, reasoning, predicted_move, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """, (today, pick['ticker'], rank, pick['conf'],
                  pick['sig_basis'], pick['reasoning'], pick['predicted']))
        conn.commit()
        log.info(f"Wrote {len(top_picks)} predictions for {today}")
        for p in top_picks[:5]:
            log.info(f"  #{p['ticker']} conf={p['conf']:.1f} — {p['reasoning'][:80]}")

        # ── T004: prospective miss-pattern scan ─────────────────────────────
        # Re-screen TODAY's premarket candidate universe (the same gap/volume
        # gated `candidates` pulled in Step 1) for any still-active watch
        # criteria extracted from a recent EOD missed-runner review.
        try:
            _aiem_scan_watch_criteria(conn, cur, "premarket_scan", candidates=candidates)
        except Exception as _wc_e:
            log.warning(f"[watch_scan] premarket_scan hook failed: {_wc_e}")

    except Exception as e:
        log.error(f"premarket_scan error: {e}")
        if conn:
            try: conn.rollback()
            except Exception: pass
    finally:
        _put_conn(conn)


# ─────────────────────────────────────────────────────────────
# JOB 2: OPEN WATCHER — every 5 min 9:30–10:30 AM ET
# AIEM decides autonomously when to fire an alert
# ─────────────────────────────────────────────────────────────
def aiem_open_watcher():
    now_et = datetime.now(ET)
    hour   = now_et.hour
    minute = now_et.minute
    if not ((hour == 9 and minute >= 30) or hour == 10):
        return

    conn = None
    try:
        conn  = _get_conn()
        cur   = conn.cursor()
        today = date.today()

        cur.execute("""
            SELECT ticker, rank, confidence_score, signal_basis, reasoning, predicted_move
            FROM aiem_predictions
            WHERE prediction_date = %s
            ORDER BY rank ASC
        """, (today,))
        picks = cur.fetchall()
        if not picks:
            log.info("No predictions for today — skipping open watch")
            return

        # Who already received an alert today?
        # signal_fire_log unique key: (signal_name, ticker, fire_date)
        cur.execute("""
            SELECT ticker FROM signal_fire_log
            WHERE fire_date = %s AND signal_name = 'AIEM_ALERT'
        """, (today,))
        alerted = {row[0] for row in cur.fetchall()}

        trust_weights    = _load_trust_weights(conn)
        mins_since_open  = (hour - 9) * 60 + max(0, minute - 30)

        for pick in picks:
            ticker, rank, base_conf, sig_basis, reasoning, predicted = pick
            if ticker in alerted:
                continue
            try:
                live_snap    = _aiem_get_snapshot(ticker)
                live_details = _aiem_get_ticker_details(ticker)
                live_news    = _aiem_get_news(ticker)

                live_conf, live_sigs, live_reason, live_predicted = _aiem_score_microcap(
                    ticker, live_snap, live_details, live_news, trust_weights
                )

                # Blend premarket + live; weight live more as time progresses
                live_weight = min(0.8, 0.4 + mins_since_open / 100)
                blended     = base_conf * (1 - live_weight) + live_conf * live_weight

                log.info(f"{ticker}: blended={blended:.1f} "
                         f"(pre={base_conf:.1f} live={live_conf:.1f}) "
                         f"@ {now_et.strftime('%H:%M')}")

                if blended >= CONFIDENCE_ALERT_THRESHOLD:
                    time_label = f"{mins_since_open}min after open" if mins_since_open > 0 else "AT OPEN"
                    msg = (
                        f"🤖 AIEM ALERT — ${ticker}\n"
                        f"Confidence: {blended:.0f}/100\n"
                        f"Time: {now_et.strftime('%H:%M ET')} ({time_label})\n"
                        f"Setup: {live_predicted}\n"
                        f"Why: {live_reason[:150]}\n"
                        f"Rank: #{rank} today's picks"
                    )
                    _aiem_send_sms(msg)
                    log.info(f"ALERT FIRED: {ticker} conf={blended:.0f}")
                    _aiem_send_chart("open_watcher", f"${ticker} — AIEM Alert", [ticker])

                    # Log to signal_fire_log — unique on (signal_name, ticker, fire_date)
                    cur.execute("""
                        INSERT INTO signal_fire_log
                            (signal_name, ticker, fire_date, metadata)
                        VALUES ('AIEM_ALERT', %s, %s, %s)
                        ON CONFLICT (signal_name, ticker, fire_date) DO NOTHING
                    """, (ticker, today, f"conf={blended:.1f} at {now_et.strftime('%H:%M')}"))
                    conn.commit()

                time.sleep(0.2)

            except Exception as e:
                log.error(f"Open watch error {ticker}: {e}")
                continue

    except Exception as e:
        log.error(f"open_watcher error: {e}")
        if conn:
            try: conn.rollback()
            except Exception: pass
    finally:
        _put_conn(conn)


# ─────────────────────────────────────────────────────────────
# JOB 3: GRADE PREDICTIONS — compute only, no Telegram sends.
# Called by the combined 4:30 PM aiem_eod_report() job below.
# ─────────────────────────────────────────────────────────────
def _aiem_grade_predictions(conn, cur, today) -> dict:
    result = {
        'graded': 0, 'wins': 0, 'graded_details': [],
        'w_picks': [], 'l_picks': [], 'avg_ret': 0.0,
        'top_loss_sigs': [], 'chart_tickers': [],
    }
    log.info(f"=== GRADING OUTCOMES {today} ===")

    cur.execute("""
        SELECT p.ticker, p.confidence_score, p.signal_basis
        FROM aiem_predictions p
        LEFT JOIN aiem_prediction_outcomes o
            ON p.ticker = o.ticker AND p.prediction_date = o.prediction_date
        WHERE p.prediction_date = %s AND o.id IS NULL
    """, (today,))
    ungraded = cur.fetchall()

    if not ungraded:
        log.info("Nothing to grade today")
        _grade_t3_t5(conn)
        return result

    graded        = wins = 0
    graded_details = []   # (ticker, t1_return, signal_basis)
    for ticker, conf, sig_basis in ungraded:
        try:
            snap        = _aiem_get_snapshot(ticker)
            day         = snap.get('day', {})
            entry_price = day.get('o') or 0
            t1_price    = day.get('c') or 0
            if not entry_price or not t1_price:
                # Polygon single-ticker snapshot 403s on today's data on this
                # plan tier — that was silently stalling EVERY prediction
                # ("nothing to grade today" even with real picks open).
                # Fall back to a Yahoo quote before giving up on this ticker.
                snap        = _aiem_get_quote_fallback(ticker)
                day         = snap.get('day', {})
                entry_price = day.get('o') or 0
                t1_price    = day.get('c') or 0
                if not entry_price or not t1_price:
                    log.warning(f"  {ticker}: no price from Polygon or Yahoo fallback — skipped")
                    continue

            t1_return = (t1_price - entry_price) / entry_price * 100
            win_t1    = t1_return > 0

            cur.execute("""
                INSERT INTO aiem_prediction_outcomes
                    (prediction_date, ticker, entry_price, t1_price, t1_return, graded_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (prediction_date, ticker) DO UPDATE
                    SET t1_price  = EXCLUDED.t1_price,
                        t1_return = EXCLUDED.t1_return,
                        graded_at = NOW()
            """, (today, ticker, entry_price, t1_price, round(t1_return, 4)))

            graded += 1
            if win_t1:
                wins += 1
            graded_details.append((ticker, t1_return, sig_basis or ''))
            log.info(f"  {ticker}: {t1_return:+.1f}% {'WIN' if win_t1 else 'LOSS'}")
            time.sleep(0.1)

        except Exception as e:
            log.error(f"Grade error {ticker}: {e}")

    conn.commit()
    wr = (wins / graded * 100) if graded > 0 else 0
    log.info(f"Graded {graded} predictions — win rate today: {wr:.1f}%")

    result['graded'] = graded
    result['wins']   = wins
    result['graded_details'] = graded_details

    if graded_details:
        w_picks = [(t, r, s) for t, r, s in graded_details if r  > 0]
        l_picks = [(t, r, s) for t, r, s in graded_details if r <= 0]
        avg_ret = sum(r for _, r, _ in graded_details) / len(graded_details)

        # Which signals appeared most on losers?
        loss_sig_freq: dict = {}
        for _, _, sb in l_picks:
            for sig in sb.split(','):
                sig = sig.strip()
                if sig:
                    loss_sig_freq[sig] = loss_sig_freq.get(sig, 0) + 1
        top_loss_sigs = sorted(loss_sig_freq.items(), key=lambda x: -x[1])[:3]

        chart_tickers = [t for t, _, _ in sorted(w_picks, key=lambda x: -x[1])[:3]]
        chart_tickers += [t for t, _, _ in sorted(l_picks, key=lambda x: x[1])[:3]]

        result.update({
            'w_picks': w_picks, 'l_picks': l_picks, 'avg_ret': avg_ret,
            'top_loss_sigs': top_loss_sigs, 'chart_tickers': chart_tickers,
        })
        log.info(f"Grading computed: {len(w_picks)}W/{len(l_picks)}L avg={avg_ret:+.1f}%")

    # Also update T3/T5 for older predictions
    _grade_t3_t5(conn)
    return result


def _grade_t3_t5(conn):
    """Fill T3 and T5 return columns for predictions 3 and 5 days old."""
    today = date.today()
    try:
        cur = conn.cursor()
        for days, col_p, col_r, col_w in [
            (3, 't3_price', 't3_return', 'win_t3'),
            (5, 't5_price', 't5_return', 'win_t5'),
        ]:
            target = today - timedelta(days=days)
            cur.execute(f"""
                SELECT ticker, entry_price FROM aiem_prediction_outcomes
                WHERE prediction_date = %s AND {col_p} IS NULL AND entry_price IS NOT NULL
            """, (target,))
            for ticker, entry_price in cur.fetchall():
                try:
                    snap  = _aiem_get_snapshot(ticker)
                    price = snap.get('day', {}).get('c') or 0
                    if not price:
                        snap  = _aiem_get_quote_fallback(ticker)
                        price = snap.get('day', {}).get('c') or 0
                    if price and entry_price:
                        ret = (price - float(entry_price)) / float(entry_price) * 100
                        cur.execute(f"""
                            UPDATE aiem_prediction_outcomes
                            SET {col_p}=%s, {col_r}=%s, {col_w}=%s
                            WHERE prediction_date=%s AND ticker=%s
                        """, (price, round(ret, 4), ret > 0, target, ticker))
                        log.info(f"  T{days} graded {ticker}: {ret:+.1f}%")
                    time.sleep(0.1)
                except Exception as e:
                    log.error(f"T{days} grade error {ticker}: {e}")
        conn.commit()
    except Exception as e:
        log.error(f"grade_t3_t5 error: {e}")


# ─────────────────────────────────────────────────────────────
# JOB 4: MISSED RUNNER ANALYSIS — compute only, no Telegram sends.
# Called by the combined 4:30 PM aiem_eod_report() job below.
# The most important learning job: what did AIEM miss and why?
# ─────────────────────────────────────────────────────────────
_MISSED_RUNNER_CAP_LOOKUP_LIMIT = 150  # safety cap on Polygon cap-bucketing calls/day

def _aiem_get_today_movers_yahoo() -> list:
    """
    Same-day fallback full-market mover feed.

    Polygon's grouped-daily endpoint (`_aiem_get_grouped_daily`) returns
    403 NOT_AUTHORIZED for the *current* calendar day on this account's
    plan tier — historical dates work fine (confirmed by direct testing),
    only `today` is blocked. That silently starved the missed-runner job
    of any candidates every single day (big_movers always == []), which
    is why no EOD Telegram report was ever sent — not a one-off outage.

    Yahoo's predefined screeners return live regularMarketChangePercent/
    Open/PreviousClose/Volume directly from the quotes payload (no
    per-ticker calls needed), normalized here to Polygon's {T,o,c,v}
    shape so the rest of the pipeline doesn't need to change.
    """
    import requests as _r
    out, seen = [], set()
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; StockScannerBot/1.0)"}
    for scr in ("day_gainers", "day_losers", "most_actives",
                "small_cap_gainers", "aggressive_small_caps"):
        try:
            url = (f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
                   f"?formatted=false&lang=en-US&region=US&scrIds={scr}&count=100")
            resp   = _r.get(url, headers=hdrs, timeout=8)
            quotes = (resp.json().get("finance", {}).get("result", [{}])[0].get("quotes", []))
            for q in quotes:
                sym = q.get("symbol", "")
                if not sym or sym in seen or "^" in sym or "/" in sym or "." in sym:
                    continue
                prev_close = q.get("regularMarketPreviousClose") or 0
                close      = q.get("regularMarketPrice") or 0
                vol        = q.get("regularMarketVolume") or 0
                open_px    = q.get("regularMarketOpen") or prev_close
                if not close or not open_px:
                    continue
                seen.add(sym)
                out.append({'T': sym, 'o': open_px, 'c': close, 'v': vol})
        except Exception as e:
            log.warning(f"[movers_fallback] {scr} failed: {e}")
    log.info(f"[movers_fallback] Yahoo screeners returned {len(out)} same-day candidates")
    return out


def _aiem_get_quote_fallback(ticker: str) -> dict:
    """
    Yahoo single-quote fallback for grading's entry/T1 price lookup.

    `_aiem_get_snapshot()` (Polygon single-ticker snapshot) returns 403 on
    this account's plan for *today's* data — same root-cause as the
    same-day grouped-daily 403 documented in `_aiem_get_today_movers_yahoo`.
    That silently stalled grading every day (every prediction stuck
    "ungraded" forever, surfacing as the misleading "nothing to grade
    today" log line even when predictions existed). This mirrors Polygon's
    `{day: {o, c}}` shape so callers don't need to branch.
    """
    import requests as _r
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; StockScannerBot/1.0)"}
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&range=1d")
    try:
        resp = _r.get(url, headers=hdrs, timeout=8)
        result = resp.json().get("chart", {}).get("result") or []
        if not result:
            return {}
        meta  = result[0].get("meta", {})
        quote = (result[0].get("indicators", {}).get("quote") or [{}])[0]
        opens  = [v for v in (quote.get("open")  or []) if v]
        closes = [v for v in (quote.get("close") or []) if v]
        open_px  = opens[0] if opens else meta.get("regularMarketOpen")
        close_px = closes[-1] if closes else meta.get("regularMarketPrice")
        if not open_px or not close_px:
            return {}
        return {'day': {'o': open_px, 'c': close_px}}
    except Exception as e:
        log.warning(f"[grade_fallback] Yahoo quote failed for {ticker}: {e}")
        return {}


def _calc_rsi(closes: list, period: int = 14) -> float:
    """Classic Wilder RSI off a list of closes (oldest→newest). Returns None if
    not enough bars. Pure function, no I/O — used for the missed-runner
    predictability check below."""
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(-period, 0):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses += -delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


_LOOKBACK_DAYS_MAX = 7   # user-requested: check EVERY day back to a week, not just yesterday

def _aiem_predictability_check(ohlcv: list, runner: dict) -> dict:
    """
    Could this move have been called BEFORE it happened? Walks back DAY BY
    DAY over the last 1-7 trading days (ohlcv excludes today's bar, sliced
    [:-1] by the caller) and evaluates EACH day independently — its own
    RSI(14) as of that day's close, its own volume buildup vs the 10 days
    before IT, its own move %, and its own EOD close-range position. A
    precursor that only showed up 4 or 5 days back (not just "yesterday")
    must not be missed just because the most recent day looked quiet.
    Also keeps the multi-day "slow grinder" cumulative-streak check, now
    extended to the same 7-day window.
    """
    out = {'verdict': 'no_precursor', 'reasons': [], 'rsi': None,
           'prior_day_move_pct': None, 'volume_buildup_x': None,
           'eod_range_position': None, 'daily_lookback': [], 'strongest_day': None}
    if not ohlcv or len(ohlcv) < 3:
        return out

    n = len(ohlcv)
    max_back = min(_LOOKBACK_DAYS_MAX, n - 1)

    # days_back=1 is yesterday (closest to today's move), days_back=7 is a
    # full week prior. Indexing is done directly against ohlcv (not a
    # separately-filtered closes/vols list) so RSI/volume/range for a given
    # day always line up with THAT day's actual bar.
    for days_back in range(1, max_back + 1):
        pos = n - days_back   # 0-indexed position of "that day" in ohlcv
        if pos < 1:
            continue
        bar = ohlcv[pos]
        day_c = bar.get('c') or 0
        day_o, day_h, day_l = bar.get('o') or 0, bar.get('h') or 0, bar.get('l') or 0

        close_window = [b.get('c') or 0 for b in ohlcv[:pos + 1] if b.get('c')]
        day_rsi = _calc_rsi(close_window, period=14) if len(close_window) >= 15 else None

        prev_close = ohlcv[pos - 1].get('c') or 0
        day_move_pct = ((day_c - prev_close) / prev_close * 100) if prev_close > 0 else None

        day_vol = bar.get('v') or 0
        baseline_vols = [b.get('v') or 0 for b in ohlcv[max(0, pos - 10):pos]]
        baseline_avg  = (sum(baseline_vols) / len(baseline_vols)) if baseline_vols else 0
        day_buildup_x = (day_vol / baseline_avg) if baseline_avg > 0 else 0

        day_range_pos = ((day_c - day_l) / (day_h - day_l)) if day_h > day_l else None

        day_flags = []
        if day_rsi is not None and day_rsi >= 70:
            day_flags.append(f"RSI(14)={day_rsi:.0f} (overbought)")
        elif day_rsi is not None and day_rsi <= 30:
            day_flags.append(f"RSI(14)={day_rsi:.0f} (oversold)")
        if day_buildup_x >= 2:
            day_flags.append(f"volume {day_buildup_x:.1f}x baseline")
        if day_move_pct is not None and abs(day_move_pct) >= 5:
            day_flags.append(f"moved {day_move_pct:+.1f}% that day")
        if day_range_pos is not None and day_range_pos >= 0.85 and day_buildup_x >= 1.5:
            day_flags.append(f"closed top {round((1 - day_range_pos) * 100)}% of range on {day_buildup_x:.1f}x vol")
        elif (day_range_pos is not None and day_range_pos <= 0.15 and day_buildup_x >= 1.5
              and (day_move_pct or 0) < 0):
            day_flags.append(f"closed bottom of range on {day_buildup_x:.1f}x vol (capitulation)")

        day_record = {
            'days_back': days_back,
            'rsi': round(day_rsi, 1) if day_rsi is not None else None,
            'volume_buildup_x': round(day_buildup_x, 1) if day_buildup_x else None,
            'day_move_pct': round(day_move_pct, 1) if day_move_pct is not None else None,
            'range_position': round(day_range_pos, 2) if day_range_pos is not None else None,
            'flags': day_flags,
        }
        out['daily_lookback'].append(day_record)
        if day_flags:
            label = "yesterday" if days_back == 1 else f"{days_back} days ago"
            out['reasons'].append(f"{label}: {'; '.join(day_flags)}")

        # Keep the original "yesterday" fields populated so existing report
        # text/branches that read them directly (discovery_text, detail
        # blocks) keep working unchanged.
        if days_back == 1:
            out['rsi']                 = day_record['rsi']
            out['prior_day_move_pct']  = day_record['day_move_pct']
            out['volume_buildup_x']    = day_record['volume_buildup_x']
            out['eod_range_position']  = day_record['range_position']

    flagged_days = [d for d in out['daily_lookback'] if d['flags']]
    if flagged_days:
        out['strongest_day'] = max(flagged_days, key=lambda d: len(d['flags']))

    # ── "Slow grinder" multi-day climb: quietly up several days in a row
    # BEFORE the headline move, each day modest on its own (so a single-day
    # gap scan would miss it), but the cumulative climb was conspicuous.
    # Window matches the same 7-day lookback used above.
    closes = [b.get('c') or 0 for b in ohlcv if b.get('c')]
    grind_days = min(_LOOKBACK_DAYS_MAX, len(closes) - 1)
    if grind_days >= 3:
        streak = 0
        for i in range(-1, -grind_days - 1, -1):
            if closes[i] > closes[i - 1]:
                streak += 1
            else:
                break
        if streak >= 3:
            start_close = closes[-streak - 1]
            cum_gain = (closes[-1] - start_close) / start_close * 100 if start_close > 0 else 0
            daily_moves_ok = all(
                abs((closes[i] - closes[i - 1]) / closes[i - 1] * 100) <= 10
                for i in range(-1, -streak - 1, -1) if closes[i - 1] > 0
            )
            out['grind_streak_days'] = streak
            out['grind_cum_pct'] = round(cum_gain, 1)
            if cum_gain >= 8 and daily_moves_ok:
                out['reasons'].append(
                    f"was a slow grinder — up {streak} straight days into today "
                    f"(+{cum_gain:.1f}% cumulative, no single day over 10%) — quietly building before the breakout"
                )

    out['verdict'] = 'predictable' if out['reasons'] else 'no_precursor'
    return out


# ── Premarket threshold heuristics (first-pass; tune later via the
# Signal Discovery Engine's test_new_signal backtester once enough
# graded samples accumulate) ──
_PREMARKET_VOL_BUILDUP_X  = 2.0   # premarket volume vs a "quiet" 25K floor
_PREMARKET_GAP_PCT        = 3.0   # abs gap vs prior close, in %

# ── "What we learned / what changes next time" per reason category.
# User explicitly asked the EOD report explain the lesson AND the concrete
# corrective action — not just log a pattern name. Kept as one dict so the
# per-ticker detail blocks and the aggregate narrative both stay in sync.
_REASON_LESSONS = {
    "premarket volume+gap (same morning)": (
        "the move was already visible in the premarket tape hours before the open",
        "promote this premarket volume+gap check into the live pre-9:30 scan so it fires as a real-time alert instead of only showing up in this postmortem",
    ),
    "multi-day slow grinder (7-day lookback)": (
        "it was quietly grinding higher for several days before the breakout, not a one-day surprise",
        "add a rolling 7-day streak/cumulative-gain screen to the nightly watchlist builder so grinders get flagged before they break out, not after",
    ),
    "strong/weak EOD close (1-7 day lookback)": (
        "a close earlier in the last week already sat at the extreme of its daily range on elevated volume — a 'coiled' tell",
        "add an EOD-range-position screen across the full 1-7 day lookback to the after-close job so coiled closes from any day this past week carry straight into tomorrow's watchlist",
    ),
    "behavioral fingerprint match": (
        "it matched a known historical behavioral fingerprint",
        "the fingerprint matched but didn't trigger a live alert — raise this fingerprint's trust weight now that it's confirmed predictive again",
    ),
    "pre-market gap (vs prior close)": (
        "it gapped meaningfully versus the prior close",
        "tighten the live gap-alert threshold so opens like this fire an alert at/before the bell instead of being caught in review",
    ),
    "news catalyst": (
        "a news catalyst drove the move",
        "speed up the news-to-alert pipeline so catalyst-driven names get flagged within minutes of the headline, not at 4:30 PM",
    ),
    "volume surge": (
        "volume surged the same day as the move",
        "same-day volume is reactive by nature — lean harder on the pre-move buildup signals (premarket, grind, EOD close) for an earlier catch",
    ),
    "pre-move setup (RSI/volume buildup)": (
        "RSI and/or volume buildup already signaled a setup the day before",
        "this RSI/volume pre-move pattern keeps recurring — promote it to a standing pre-market watchlist filter instead of a postmortem-only note",
    ),
    "no clear precursor": (
        "there was no detectable precursor in price, volume, RSI, premarket, or multi-day history",
        "no model change tonight — log it to the discovery pool in case a pattern emerges once more samples like this accumulate",
    ),
}


def _aiem_get_premarket_minutes_yahoo(ticker: str) -> list:
    """
    Today's intraday 1-minute bars INCLUDING premarket, via Yahoo's chart
    API. Polygon's minute aggs are 403 NOT_AUTHORIZED for the *current*
    calendar day on this account's plan (same restriction documented in
    `_aiem_get_today_movers_yahoo` for grouped-daily) — Yahoo has no such
    same-day block, so it's the only way to see THIS MORNING's premarket
    tape on the same day a stock actually runs.
    """
    import requests as _r
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; StockScannerBot/1.0)"}
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1m&range=1d&includePrePost=true")
    try:
        resp = _r.get(url, headers=hdrs, timeout=8)
        result = resp.json().get("chart", {}).get("result") or []
        if not result:
            return []
        r0 = result[0]
        meta = r0.get("meta", {})
        regular_start = (meta.get("currentTradingPeriod") or {}).get("regular", {}).get("start")
        timestamps = r0.get("timestamp") or []
        quote = (r0.get("indicators", {}).get("quote") or [{}])[0]
        opens, highs, lows, closes, vols = (quote.get("open") or [], quote.get("high") or [],
                                             quote.get("low") or [], quote.get("close") or [],
                                             quote.get("volume") or [])
        bars = []
        for i, t in enumerate(timestamps):
            if regular_start and t >= regular_start:
                break   # stop at the open — premarket only
            if i < len(closes) and closes[i] is not None:
                bars.append({'t': t, 'o': opens[i] if i < len(opens) else None,
                             'h': highs[i] if i < len(highs) else None,
                             'l': lows[i] if i < len(lows) else None,
                             'c': closes[i], 'v': vols[i] if i < len(vols) else 0})
        return bars
    except Exception as e:
        log.warning(f"[premarket_yahoo] {ticker} failed: {e}")
        return []


def _aiem_check_premarket_signal(ticker: str, prior_close: float) -> dict:
    """
    Did THIS MORNING's premarket tape already show the move coming, before
    the 9:30 open? Sums premarket volume and measures the gap off
    yesterday's close. This is the literal "what did Polygon/Yahoo's
    premarket data show earlier today" check.
    """
    out = {'has_data': False, 'premarket_volume': 0, 'premarket_gap_pct': None,
           'flagged': False, 'reason': None}
    bars = _aiem_get_premarket_minutes_yahoo(ticker)
    if not bars or not prior_close:
        return out
    out['has_data'] = True
    pm_vol = sum(b['v'] or 0 for b in bars)
    last_px = next((b['c'] for b in reversed(bars) if b.get('c')), None)
    out['premarket_volume'] = int(pm_vol)
    if last_px and prior_close > 0:
        gap_pct = (last_px - prior_close) / prior_close * 100
        out['premarket_gap_pct'] = round(gap_pct, 1)
        if pm_vol >= MIN_PREMARKET_VOLUME * _PREMARKET_VOL_BUILDUP_X and abs(gap_pct) >= _PREMARKET_GAP_PCT:
            out['flagged'] = True
            out['reason'] = (f"premarket volume was already {int(pm_vol):,} shares with a "
                              f"{gap_pct:+.1f}% gap before the 9:30 bell — the move was visible hours early")
    return out


_WATCH_CRITERIA_EXPIRY_TRADING_DAYS = 3   # per architect plan: criteria stay live ~3 trading days
_WATCH_MAX_MATCHES_PER_CRITERION = 5      # per-criterion cap so a loose generic bar can't flood
_WATCH_MAX_ALERTS_PER_RUN = 25            # global cap on inserted/alerted matches per scan run


def _aiem_effective_watch_threshold(crit: dict) -> float:
    """For non-gap metrics, threshold_value is a generic retrospective
    detection bar (e.g. rsi_14 >= 70) used to EXPLAIN a missed runner —
    it is common across a broad universe, not a rare/selective filter.
    Tighten it to the missed-runner's own observed_value so the prospective
    scan only fires when today's candidate is AT LEAST as extreme as what
    actually happened, keeping matches rare and meaningful instead of
    flooding on every name that barely clears the generic bar."""
    op  = crit['operator']
    thr = float(crit['threshold_value'])
    obs = crit.get('observed_value')
    if obs is None:
        return thr
    obs = float(obs)
    return max(thr, obs) if op == '>=' else min(thr, obs)


def _aiem_watch_match_margin(op: str, val: float, thr: float) -> float:
    """How far past the threshold a match sits — used to rank matches so
    only the most extreme (rarest) names get capped-in, not every name
    that merely clears the bar."""
    return (val - thr) if op == '>=' else (thr - val)


def _aiem_add_trading_days(d, n: int):
    """Calendar-day walk that skips Sat/Sun (market-holiday precision isn't
    needed for a "stay live ~3 trading days" expiry window)."""
    cur = d
    added = 0
    while added < n:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


def _aiem_extract_watch_criteria(predictability: dict, runner: dict, premkt: dict, today) -> list:
    """
    Turn TODAY's miss into concrete, re-screenable (metric, operator,
    threshold, observed_value) rows instead of just a postmortem sentence.
    The threshold is the SAME detection bar the predictability/premarket
    checks already use internally (so tomorrow's prospective scan is
    re-running the identical rule), while observed_value records exactly
    what this specific miss showed (RSI=94, vol=27x, etc.) for the report.
    """
    ticker, move_pct = runner['ticker'], runner['move_pct']
    rows = []

    def add(metric, operator, threshold, observed, lookback_days, source_text):
        if observed is None:
            return
        rows.append({
            'metric_name': metric, 'operator': operator, 'threshold_value': threshold,
            'observed_value': observed, 'lookback_days': lookback_days, 'source_text': source_text,
        })

    for day in predictability.get('daily_lookback', []):
        if not day['flags']:
            continue
        db = day['days_back']
        tag = "yesterday" if db == 1 else f"{db} days before"
        if day['rsi'] is not None and day['rsi'] >= 70:
            add('rsi_14', '>=', 70, day['rsi'], db,
                f"{ticker} RSI(14)={day['rsi']} {tag} its +{move_pct:.1f}% breakout")
        elif day['rsi'] is not None and day['rsi'] <= 30:
            add('rsi_14', '<=', 30, day['rsi'], db,
                f"{ticker} RSI(14)={day['rsi']} {tag} its +{move_pct:.1f}% breakout")
        if day['volume_buildup_x'] and day['volume_buildup_x'] >= 2:
            add('volume_buildup_x', '>=', 2, day['volume_buildup_x'], db,
                f"{ticker} volume was {day['volume_buildup_x']}x baseline {tag} its +{move_pct:.1f}% breakout")
        if day['range_position'] is not None and day['range_position'] >= 0.85:
            add('eod_range_position', '>=', 0.85, day['range_position'], db,
                f"{ticker} closed in the top of its daily range {tag} its +{move_pct:.1f}% breakout")
        elif day['range_position'] is not None and day['range_position'] <= 0.15:
            add('eod_range_position', '<=', 0.15, day['range_position'], db,
                f"{ticker} closed at the bottom of its range (capitulation) {tag} its +{move_pct:.1f}% breakout")

    if predictability.get('grind_streak_days', 0) >= 3:
        add('grind_streak_days', '>=', 3, predictability['grind_streak_days'], predictability['grind_streak_days'],
            f"{ticker} was on a {predictability['grind_streak_days']}-day up-streak "
            f"(+{predictability.get('grind_cum_pct')}% cumulative) before its +{move_pct:.1f}% breakout")

    if premkt.get('flagged'):
        add('premarket_gap_pct', '>=', _PREMARKET_GAP_PCT, abs(premkt.get('premarket_gap_pct') or 0), 0,
            f"{ticker} premarket gap {premkt.get('premarket_gap_pct')}% on {premkt.get('premarket_volume')} shares "
            f"the same morning as its +{move_pct:.1f}% breakout")

    return rows


def _aiem_save_watch_criteria(conn, cur, criteria_rows: list, runner: dict, bucket: str,
                               reason_cat: str, today) -> int:
    """INSERT the extracted criteria so tomorrow's premarket scan / missed-
    morning-check can actually re-screen the live universe for them — this
    is what turns "lesson learned" into a real prospective watch, not just
    a sentence in the EOD report."""
    if not criteria_rows:
        return 0
    expires_at = _aiem_add_trading_days(today, _WATCH_CRITERIA_EXPIRY_TRADING_DAYS)
    saved = 0
    for row in criteria_rows:
        try:
            cur.execute("""
                INSERT INTO aiem_watch_criteria
                    (discovered_date, expires_at, origin_ticker, origin_bucket, origin_move_pct,
                     reason_cat, metric_name, operator, threshold_value, observed_value,
                     lookback_days, source_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (today, expires_at, runner['ticker'], bucket, round(runner['move_pct'], 2),
                  reason_cat, row['metric_name'], row['operator'], row['threshold_value'],
                  row['observed_value'], row['lookback_days'], row['source_text']))
            saved += 1
        except Exception as e:
            log.warning(f"[watch_criteria] insert failed for {runner['ticker']}/{row['metric_name']}: {e}")
    if saved:
        conn.commit()
    return saved


_WATCH_VALIDATION_LOOKBACK_DAYS = 180   # SQL-backed metrics
_WATCH_VALIDATION_SAMPLE_TICKERS = 300  # Python-sampled metrics (rsi_14, grind_streak_days)
_WATCH_VALIDATION_MIN_N = 15            # below this, the row gets labeled "experimental"


def _aiem_validate_via_python_sample(cur, metric_name: str, operator: str, threshold_value: float) -> dict:
    """
    rsi_14 and grind_streak_days aren't single-row computations, so they
    can't be expressed as a plain SQL window-function query the way
    eod_range_position/premarket_gap_pct/volume_buildup_x can. Instead,
    sample a bounded set of tickers (random, must have enough history),
    walk each one's own daily closes day-by-day computing the metric AS OF
    that day (same logic as `_aiem_predictability_check`/`_calc_rsi`), and
    tally how often the next day's close was higher when the metric met
    the criterion. Bounded sample + bounded lookback keeps this "lightweight"
    (no coupling to main.py, completes in low single-digit seconds).
    """
    cur.execute("""
        SELECT ticker FROM polygon_market_daily
        WHERE scan_date >= CURRENT_DATE - INTERVAL '%s days'
        GROUP BY ticker HAVING COUNT(*) >= 20
        ORDER BY RANDOM() LIMIT %s
    """ % (_WATCH_VALIDATION_LOOKBACK_DAYS, _WATCH_VALIDATION_SAMPLE_TICKERS))
    tickers = [r[0] for r in cur.fetchall()]

    n = wins = 0
    ret_sum = 0.0
    for t in tickers:
        cur.execute("""
            SELECT close_price FROM polygon_market_daily
            WHERE ticker = %s AND scan_date >= CURRENT_DATE - INTERVAL '%s days'
            ORDER BY scan_date
        """ % ("%s", _WATCH_VALIDATION_LOOKBACK_DAYS), (t,))
        closes = [float(r[0]) for r in cur.fetchall() if r[0]]
        if len(closes) < 17:
            continue
        for i in range(15, len(closes) - 1):
            if metric_name == 'rsi_14':
                val = _calc_rsi(closes[:i + 1], period=14)
                if val is None:
                    continue
            else:  # grind_streak_days
                streak, j = 0, i
                while j > 0 and closes[j] > closes[j - 1]:
                    streak += 1
                    j -= 1
                val = streak
            passes = (val >= threshold_value) if operator == '>=' else (val <= threshold_value)
            if not passes or closes[i] <= 0:
                continue
            n += 1
            nxt_ret = (closes[i + 1] - closes[i]) / closes[i]
            ret_sum += nxt_ret
            if nxt_ret > 0:
                wins += 1
    if n == 0:
        return {'n': 0, 'win_rate': None, 'avg_next_day': None}
    return {'n': n, 'win_rate': round(wins / n, 3), 'avg_next_day': round(ret_sum / n, 4)}


def _aiem_validate_watch_criterion(conn, cur, metric_name: str, operator: str, threshold_value: float) -> dict:
    """
    Lightweight LOCAL backtest (no main.py coupling) against
    `polygon_market_daily`: historically, how often did THIS exact
    metric/operator/threshold precede a positive next-day close? Returns
    {n, win_rate, avg_next_day}; n < _WATCH_VALIDATION_MIN_N means the
    caller should label the criterion "experimental" rather than trusted.
    """
    cmp_sql = '>=' if operator == '>=' else '<='
    try:
        if metric_name == 'eod_range_position':
            cur.execute(f"""
                WITH daily AS (
                    SELECT close_price, high_price, low_price,
                           LEAD(close_price) OVER (PARTITION BY ticker ORDER BY scan_date) AS next_close
                    FROM polygon_market_daily
                    WHERE scan_date >= CURRENT_DATE - INTERVAL '{_WATCH_VALIDATION_LOOKBACK_DAYS} days'
                      AND high_price > low_price AND close_price > 0
                )
                SELECT COUNT(*),
                       AVG(CASE WHEN next_close > close_price THEN 1.0 ELSE 0.0 END),
                       AVG((next_close - close_price) / close_price)
                FROM daily
                WHERE next_close IS NOT NULL
                  AND ((close_price - low_price) / (high_price - low_price)) {cmp_sql} %s
            """, (threshold_value,))
        elif metric_name == 'premarket_gap_pct':
            cur.execute(f"""
                WITH daily AS (
                    SELECT close_price, gap_pct,
                           LEAD(close_price) OVER (PARTITION BY ticker ORDER BY scan_date) AS next_close
                    FROM polygon_market_daily
                    WHERE scan_date >= CURRENT_DATE - INTERVAL '{_WATCH_VALIDATION_LOOKBACK_DAYS} days'
                      AND close_price > 0
                )
                SELECT COUNT(*),
                       AVG(CASE WHEN next_close > close_price THEN 1.0 ELSE 0.0 END),
                       AVG((next_close - close_price) / close_price)
                FROM daily
                WHERE next_close IS NOT NULL AND ABS(gap_pct) {cmp_sql} %s
            """, (threshold_value,))
        elif metric_name == 'volume_buildup_x':
            cur.execute(f"""
                WITH daily AS (
                    SELECT close_price, volume,
                           AVG(volume) OVER (PARTITION BY ticker ORDER BY scan_date
                                              ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS baseline_vol,
                           LEAD(close_price) OVER (PARTITION BY ticker ORDER BY scan_date) AS next_close
                    FROM polygon_market_daily
                    WHERE scan_date >= CURRENT_DATE - INTERVAL '{_WATCH_VALIDATION_LOOKBACK_DAYS} days'
                      AND close_price > 0
                )
                SELECT COUNT(*),
                       AVG(CASE WHEN next_close > close_price THEN 1.0 ELSE 0.0 END),
                       AVG((next_close - close_price) / close_price)
                FROM daily
                WHERE next_close IS NOT NULL AND baseline_vol > 0
                  AND (volume / baseline_vol) {cmp_sql} %s
            """, (threshold_value,))
        elif metric_name in ('rsi_14', 'grind_streak_days'):
            return _aiem_validate_via_python_sample(cur, metric_name, operator, threshold_value)
        else:
            return {'n': 0, 'win_rate': None, 'avg_next_day': None}

        row = cur.fetchone()
        n = row[0] or 0
        if n == 0:
            return {'n': 0, 'win_rate': None, 'avg_next_day': None}
        return {'n': n, 'win_rate': round(float(row[1]), 3) if row[1] is not None else None,
                'avg_next_day': round(float(row[2]), 4) if row[2] is not None else None}
    except Exception as e:
        log.warning(f"[watch_validate] {metric_name} {operator} {threshold_value} validation failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return {'n': 0, 'win_rate': None, 'avg_next_day': None}


# ─────────────────────────────────────────────────────────────
# T004: PROSPECTIVE SCAN — turn yesterday's "lesson learned" into AIEM
# actually re-screening TODAY's live candidate universe for the exact
# pattern it missed, instead of only journaling it in the EOD postmortem.
# ─────────────────────────────────────────────────────────────
def _aiem_load_active_watch_criteria(conn, cur) -> list:
    """Load all non-expired, active watch criteria extracted from recent
    missed-runner reviews (up to ~3 trading days old)."""
    today = date.today()
    try:
        cur.execute("""
            SELECT id, discovered_date, expires_at, origin_ticker, origin_bucket,
                   origin_move_pct, reason_cat, metric_name, operator, threshold_value,
                   observed_value, validation_n, validation_win_rate, validation_avg_next_day
            FROM aiem_watch_criteria
            WHERE active = TRUE AND expires_at >= %s
            ORDER BY discovered_date DESC
        """, (today,))
        cols = ['id', 'discovered_date', 'expires_at', 'origin_ticker', 'origin_bucket',
                'origin_move_pct', 'reason_cat', 'metric_name', 'operator', 'threshold_value',
                'observed_value', 'validation_n', 'validation_win_rate', 'validation_avg_next_day']
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        log.warning(f"[watch_scan] load active criteria failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def _aiem_watch_scan_premarket_gap(crits: list, candidates) -> list:
    """premarket_gap_pct criteria: cross-check against the candidate list
    aiem_premarket_scan already pulled (gap/volume-gated) rather than
    re-querying — that list IS today's premarket gap universe. Returns
    [] when no candidate list is available (e.g. called from a job that
    didn't run a premarket DB pull)."""
    matches = []
    if not candidates:
        return matches
    for crit in crits:
        op  = crit['operator']
        thr = float(crit['threshold_value'])
        for c in candidates:
            gap = c.get('gap_pct')
            if gap is None:
                continue
            val = abs(float(gap))
            passes = (val >= thr) if op == '>=' else (val <= thr)
            if passes:
                matches.append((crit, c['ticker'], round(val, 4)))
    return matches


def _aiem_watch_scan_sql_metric(cur, crits: list, metric_name: str, latest_date) -> list:
    """eod_range_position / volume_buildup_x: ONE broad SQL pass over the
    full polygon_market_daily universe for the latest trading day,
    deliberately NOT gated by MIN_GAP_PCT (a grind-streak or volume-buildup
    setup often has little or no gap). Price-banded to the system's
    tradable range so results stay relevant to the live candidate universe."""
    computed = []
    if metric_name == 'eod_range_position':
        cur.execute("""
            SELECT ticker, close_price, high_price, low_price
            FROM polygon_market_daily
            WHERE scan_date = %s AND close_price BETWEEN %s AND %s
              AND high_price > low_price
        """, (latest_date, MIN_PRICE, MAX_PRICE))
        for ticker, close, high, low in cur.fetchall():
            if not (close and high and low) or high <= low:
                continue
            computed.append((ticker, (float(close) - float(low)) / (float(high) - float(low))))
    elif metric_name == 'volume_buildup_x':
        cur.execute("""
            WITH daily AS (
                SELECT ticker, scan_date, volume,
                       AVG(volume) OVER (PARTITION BY ticker ORDER BY scan_date
                                          ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS baseline_vol
                FROM polygon_market_daily
                WHERE scan_date >= %s - INTERVAL '20 days'
                  AND close_price BETWEEN %s AND %s
            )
            SELECT ticker, volume, baseline_vol FROM daily
            WHERE scan_date = %s AND baseline_vol > 0
        """, (latest_date, MIN_PRICE, MAX_PRICE, latest_date))
        for ticker, volume, baseline_vol in cur.fetchall():
            if not baseline_vol or volume is None:
                continue
            computed.append((ticker, float(volume) / float(baseline_vol)))

    matches = []
    for crit in crits:
        op  = crit['operator']
        thr = _aiem_effective_watch_threshold(crit)
        crit_matches = []
        for ticker, val in computed:
            passes = (val >= thr) if op == '>=' else (val <= thr)
            if passes:
                margin = _aiem_watch_match_margin(op, val, thr)
                crit_matches.append((margin, ticker, round(val, 4)))
        crit_matches.sort(key=lambda m: m[0], reverse=True)
        for _margin, ticker, val in crit_matches[:_WATCH_MAX_MATCHES_PER_CRITERION]:
            matches.append((crit, ticker, val))
    return matches


def _aiem_watch_scan_python_metric(cur, crits: list, metric_name: str, latest_date) -> list:
    """rsi_14 / grind_streak_days: these need a per-ticker walk over recent
    closes (same logic as _aiem_predictability_check / _calc_rsi), not a
    single SQL aggregate. Bounded to the top-600-by-volume tickers in the
    tradable price band on the latest day to keep this lightweight."""
    cur.execute("""
        SELECT ticker FROM polygon_market_daily
        WHERE scan_date = %s AND close_price BETWEEN %s AND %s
        ORDER BY volume DESC NULLS LAST LIMIT 600
    """, (latest_date, MIN_PRICE, MAX_PRICE))
    tickers = [r[0] for r in cur.fetchall()]

    computed = []
    for t in tickers:
        cur.execute("""
            SELECT close_price FROM polygon_market_daily
            WHERE ticker = %s AND scan_date <= %s
            ORDER BY scan_date DESC LIMIT 20
        """, (t, latest_date))
        closes = [float(r[0]) for r in cur.fetchall() if r[0]][::-1]
        if len(closes) < 15:
            continue
        if metric_name == 'rsi_14':
            val = _calc_rsi(closes, period=14)
            if val is None:
                continue
        else:  # grind_streak_days
            streak, j = 0, len(closes) - 1
            while j > 0 and closes[j] > closes[j - 1]:
                streak += 1
                j -= 1
            val = streak
        computed.append((t, val))

    matches = []
    for crit in crits:
        op  = crit['operator']
        thr = _aiem_effective_watch_threshold(crit)
        crit_matches = []
        for ticker, val in computed:
            passes = (val >= thr) if op == '>=' else (val <= thr)
            if passes:
                margin = _aiem_watch_match_margin(op, val, thr)
                crit_matches.append((margin, ticker, val))
        crit_matches.sort(key=lambda m: m[0], reverse=True)
        for _margin, ticker, val in crit_matches[:_WATCH_MAX_MATCHES_PER_CRITERION]:
            matches.append((crit, ticker, val))
    return matches


def _aiem_send_watch_alert_telegram(new_alerts: list, job_name: str):
    """One combined Telegram message per scan run, clearly separated from
    the normal ranked trading picks — this is a 'we've seen this exact
    setup before and it just reappeared' flag, never blended into
    confidence_score / the trading ranking."""
    lines = [f"🧠 Yesterday's Miss Pattern Match ({job_name})"]
    for a in new_alerts[:15]:
        crit = a['criteria']
        ov   = a['observed_value']
        val_str = f"{ov:.2f}" if isinstance(ov, (int, float)) else str(ov)
        n_val = crit.get('validation_n') or 0
        tag   = "validated" if n_val >= _WATCH_VALIDATION_MIN_N else "experimental"
        wr    = crit.get('validation_win_rate')
        wr_str = f", hist WR {float(wr):.0%} n={n_val}" if wr is not None else ""
        mv = crit.get('origin_move_pct')
        mv_str = f"{float(mv):+.1f}%" if mv is not None else "?"
        lines.append(
            f"• {a['ticker']}: {crit['metric_name']} {crit['operator']} {crit['threshold_value']} "
            f"(now {val_str}) — same setup as {crit['origin_ticker']} {mv_str} on "
            f"{crit['discovered_date']} [{tag}{wr_str}]"
        )
    if len(new_alerts) > 15:
        lines.append(f"...and {len(new_alerts) - 15} more")
    _tg_send_QUARANTINED_DO_NOT_USE("\n".join(lines))


def _aiem_scan_watch_criteria(conn, cur, job_name: str, candidates: list = None) -> list:
    """
    Prospective scan: for every still-active watch criterion (a concrete
    pattern extracted from a missed runner in the EOD report), re-screen
    TODAY's live universe for tickers matching it RIGHT NOW. This is what
    turns the EOD "lesson learned" into AIEM actually looking for the same
    setup again, instead of only describing it in a postmortem.

    New matches are deduped via aiem_watch_alerts (UNIQUE criteria_id+
    ticker+alert_date) BEFORE the Telegram send, so a crash mid-send can
    never cause the same match to be re-alerted on the next 15-min run.
    """
    active = _aiem_load_active_watch_criteria(conn, cur)
    if not active:
        return []

    today = date.today()
    try:
        cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
        latest_date = cur.fetchone()[0]
    except Exception as e:
        log.warning(f"[watch_scan] latest_date lookup failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        latest_date = None

    by_metric: dict = {}
    for crit in active:
        by_metric.setdefault(crit['metric_name'], []).append(crit)

    all_matches = []   # (crit, ticker, observed) across every metric, pre-coalesce
    for metric_name, crits in by_metric.items():
        try:
            if metric_name == 'premarket_gap_pct':
                matches = _aiem_watch_scan_premarket_gap(crits, candidates)
            elif metric_name in ('eod_range_position', 'volume_buildup_x') and latest_date:
                matches = _aiem_watch_scan_sql_metric(cur, crits, metric_name, latest_date)
            elif metric_name in ('rsi_14', 'grind_streak_days') and latest_date:
                matches = _aiem_watch_scan_python_metric(cur, crits, metric_name, latest_date)
            else:
                matches = []
        except Exception as m_e:
            log.warning(f"[watch_scan] {metric_name} scan failed: {m_e}")
            try:
                conn.rollback()
            except Exception:
                pass
            matches = []
        all_matches.extend(matches)

    # ── Coalesce: several active criteria rows can independently match the
    # SAME ticker on the SAME metric (e.g. two separate missed-runner days
    # both produced a volume_buildup_x criterion). Keep only the strongest
    # (highest-margin) match per (ticker, metric_name), then cap the total
    # alerts for this run — a loose generic detection bar must never be able
    # to flood Telegram/DB with near-universe-wide matches.
    best_by_key: dict = {}
    for crit, ticker, observed in all_matches:
        op  = crit['operator']
        thr = (float(crit['threshold_value']) if crit['metric_name'] == 'premarket_gap_pct'
               else _aiem_effective_watch_threshold(crit))
        margin = _aiem_watch_match_margin(op, float(observed), thr)
        key = (ticker, crit['metric_name'])
        prev = best_by_key.get(key)
        if prev is None or margin > prev[0]:
            best_by_key[key] = (margin, crit, ticker, observed)

    ranked = sorted(best_by_key.values(), key=lambda m: m[0], reverse=True)
    if len(ranked) > _WATCH_MAX_ALERTS_PER_RUN:
        log.info(f"[watch_scan] {job_name}: {len(ranked)} coalesced matches, "
                 f"capping to top {_WATCH_MAX_ALERTS_PER_RUN} by margin")
    ranked = ranked[:_WATCH_MAX_ALERTS_PER_RUN]

    new_alerts = []
    for _margin, crit, ticker, observed in ranked:
        try:
            cur.execute("""
                INSERT INTO aiem_watch_alerts (criteria_id, ticker, alert_date, job_name, observed_value)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (criteria_id, ticker, alert_date) DO NOTHING
                RETURNING id
            """, (crit['id'], ticker, today, job_name, observed))
            inserted = cur.fetchone()
            conn.commit()
            if inserted:
                new_alerts.append({'criteria': crit, 'ticker': ticker, 'observed_value': observed})
        except Exception as ins_e:
            log.warning(f"[watch_scan] dedupe insert failed {ticker}/{crit['metric_name']}: {ins_e}")
            try:
                conn.rollback()
            except Exception:
                pass

    if new_alerts:
        try:
            _aiem_send_watch_alert_telegram(new_alerts, job_name)
        except Exception as tg_e:
            log.warning(f"[watch_scan] telegram send failed: {tg_e}")
        log.info(f"[watch_scan] {job_name}: {len(new_alerts)} new pattern-match alert(s) sent")
    else:
        log.info(f"[watch_scan] {job_name}: 0 new matches across {len(active)} active criteria")

    return new_alerts


def _aiem_find_missed_runners(conn, cur, today) -> dict:
    result = {
        'big_movers': [], 'buckets': {'micro': [], 'small': [], 'mid': [], 'large': []},
        'bucket_lines': {'micro': [], 'small': [], 'mid': [], 'large': []},
        'discoveries': 0, 'all_discovery_texts': [], 'reason_freq': {},
    }
    log.info(f"=== MISSED RUNNER ANALYSIS {today} (4 cap-tier buckets) ===")

    # What did AIEM flag today?
    cur.execute("SELECT ticker FROM aiem_predictions WHERE prediction_date = %s", (today,))
    flagged = {row[0] for row in cur.fetchall()}

    # Pull today's full mover list. Polygon's same-day grouped-daily is
    # plan-restricted (see _aiem_get_today_movers_yahoo docstring) — fall
    # back to the Yahoo screener feed whenever Polygon comes back empty.
    daily_data = _aiem_get_grouped_daily(today)
    if not daily_data:
        log.warning("[missed_runner] Polygon grouped-daily empty for today — using Yahoo fallback")
        daily_data = _aiem_get_today_movers_yahoo()

    # ALL of today's movers, gainers first — this is the pool the daily
    # top-10-per-cap-bucket review draws from. Deliberately NOT gated on a
    # 20% threshold or "AIEM didn't flag it" here: the user wants the top 10
    # biggest movers in EACH of the 4 cap tiers reviewed every single day,
    # regardless of size or whether AIEM already caught them, so the
    # predictability learning loop has a consistent daily sample instead of
    # only firing on rare 20%+ misses (which can be 0-4 names on a quiet day).
    all_movers = []
    for stock in daily_data:
        ticker   = stock.get('T', '')
        o, c, v  = stock.get('o') or 0, stock.get('c') or 0, stock.get('v') or 0
        if o > 0 and c > 0:
            move_pct = (c - o) / o * 100
            all_movers.append({'ticker': ticker, 'open': o, 'close': c,
                               'volume': v, 'move_pct': move_pct,
                               'was_flagged': ticker in flagged})

    all_movers.sort(key=lambda x: x['move_pct'], reverse=True)

    # Headline "missed runner" alert count — still the 20%+/unflagged subset,
    # kept for the alerting language elsewhere in the report.
    big_movers = [m for m in all_movers if m['move_pct'] >= 20 and not m['was_flagged']]
    result['big_movers'] = big_movers
    log.info(f"{len(big_movers)} missed runners (20%+ AIEM didn't flag) out of {len(all_movers)} total movers today")

    if not all_movers:
        return result

    # ── Step 1: bucket the top movers by market cap (cached lookups) ───────
    buckets   = {'micro': [], 'small': [], 'mid': [], 'large': []}
    unknown_n = 0
    for runner in all_movers[:_MISSED_RUNNER_CAP_LOOKUP_LIMIT]:
        ref     = _aiem_get_ticker_reference_cached(conn, runner['ticker'])
        mkt_cap = ref.get('market_cap') or 0
        bucket  = _aiem_cap_bucket(mkt_cap)
        if bucket is None:
            unknown_n += 1
            continue
        runner['market_cap']   = mkt_cap
        runner['float_shares'] = ref.get('float_shares') or 0
        buckets[bucket].append(runner)

    for b in buckets:
        buckets[b].sort(key=lambda x: x['move_pct'], reverse=True)
        buckets[b] = buckets[b][:10]   # always review the top 10/bucket, every day

    if unknown_n:
        log.info(f"{unknown_n} candidates had no resolvable market cap — omitted from buckets")
    for b, names in buckets.items():
        if len(names) < 10:
            log.info(f"[missed_runner] {b} bucket only had {len(names)}/10 — fewer qualifying movers today")

    # ── Step 2: deep "why" per bucketed candidate ───────────────────────────
    discoveries  = 0
    bucket_lines = {b: [] for b in buckets}
    bucket_detail_lines = {b: [] for b in buckets}
    all_discovery_texts = []
    reason_freq: dict = {}
    predictable_n = surprise_n = 0
    premkt_n = grind_n = eod_n = 0
    for bucket, runners in buckets.items():
        for runner in runners:
            ticker   = runner['ticker']
            move_pct = runner['move_pct']
            try:
                news  = _aiem_get_news(ticker)
                # 30 calendar days of history — enough bars for RSI(14) +
                # a 10-day volume baseline, not just yesterday's single bar.
                ohlcv = _aiem_get_ohlcv(ticker, days=30)

                gap_pct = None
                if ohlcv and len(ohlcv) >= 2:
                    prev_close = ohlcv[-2].get('c') or 0
                    if prev_close > 0:
                        gap_pct = (runner['open'] - prev_close) / prev_close * 100

                vol_ratio = 0
                if ohlcv and len(ohlcv) >= 2:
                    prev_vol  = ohlcv[-2].get('v', 1)
                    vol_ratio = runner['volume'] / prev_vol if prev_vol > 0 else 0

                why = _aiem_behavioral_why(conn, ticker, today)

                # Pre-move history check: drop today's own bar (if Polygon
                # already has it) so the predictability read only looks at
                # what was knowable BEFORE the move happened.
                prior_bars = ohlcv[:-1] if (ohlcv and ohlcv[-1].get('c') == runner['close']) else ohlcv
                predictability = _aiem_predictability_check(prior_bars, runner)

                # THIS MORNING's premarket tape, checked separately (Yahoo,
                # since Polygon blocks same-day) — "could we have seen it
                # before the 9:30 bell, not just in yesterday's bars?"
                prior_close_for_pm = prior_bars[-1].get('c') if prior_bars else None
                premkt = _aiem_check_premarket_signal(ticker, prior_close_for_pm)
                if premkt.get('flagged'):
                    predictability['reasons'].append(premkt['reason'])
                    predictability['verdict'] = 'predictable'
                if predictability['verdict'] == 'predictable':
                    predictable_n += 1
                else:
                    surprise_n += 1

                patterns = []
                if why.get('matched'):
                    patterns.append(f"fingerprint_match_sim{why['similarity']:.2f}_like_{why.get('matched_ticker')}")
                if gap_pct is not None and abs(gap_pct) >= 5:
                    patterns.append(f"gap_{gap_pct:+.1f}pct_at_open")
                if news:
                    patterns.append("had_catalyst")
                if runner['float_shares'] and runner['float_shares'] < 10_000_000:
                    patterns.append(f"low_float_{runner['float_shares']/1e6:.1f}M")
                if vol_ratio >= 3:
                    patterns.append(f"vol_surge_{vol_ratio:.1f}x")
                patterns.extend(predictability['reasons'])

                pattern_str = " | ".join(patterns) if patterns else "quiet_move_no_clear_precursor"
                lead_reason = patterns[0] if patterns else "no clear precursor found"
                # Bucket the lead reason into a coarse category for the narrative.
                # Order matters — most "should-have-caught-it-earliest" signal wins,
                # since that's the most actionable lesson for next time.
                if premkt.get('flagged'):
                    reason_cat = "premarket volume+gap (same morning)"
                    premkt_n += 1
                elif predictability.get('grind_streak_days', 0) >= 3 and any('slow grinder' in r for r in predictability['reasons']):
                    reason_cat = "multi-day slow grinder (7-day lookback)"
                    grind_n += 1
                elif any('closed top' in r or 'closed bottom' in r for r in predictability['reasons']):
                    reason_cat = "strong/weak EOD close (1-7 day lookback)"
                    eod_n += 1
                elif why.get('matched'):
                    reason_cat = "behavioral fingerprint match"
                elif gap_pct is not None and abs(gap_pct) >= 5:
                    reason_cat = "pre-market gap (vs prior close)"
                elif news:
                    reason_cat = "news catalyst"
                elif vol_ratio >= 3:
                    reason_cat = "volume surge"
                elif predictability['reasons']:
                    reason_cat = "pre-move setup (RSI/volume buildup)"
                else:
                    reason_cat = "no clear precursor"
                reason_freq[reason_cat] = reason_freq.get(reason_cat, 0) + 1
                lesson, action = _REASON_LESSONS.get(
                    reason_cat, ("no clear takeaway from this one", "no model change triggered")
                )

                # Save the concrete, re-screenable criteria behind today's miss so
                # tomorrow's premarket scan can actually look for this setup again
                # — not just narrate it. Errors here must never break the report.
                try:
                    watch_rows = _aiem_extract_watch_criteria(predictability, runner, premkt, today)
                    n_saved = _aiem_save_watch_criteria(conn, cur, watch_rows, runner, bucket, reason_cat, today)
                    if n_saved:
                        log.info(f"[watch_criteria] saved {n_saved} criteria from {ticker} miss (expires "
                                 f"{_aiem_add_trading_days(today, _WATCH_CRITERIA_EXPIRY_TRADING_DAYS)})")
                except Exception as wc_e:
                    log.warning(f"[watch_criteria] extraction/save failed for {ticker}: {wc_e}")

                verdict_str = (
                    f"PREDICTABLE — {'; '.join(predictability['reasons'])}"
                    if predictability['verdict'] == 'predictable'
                    else "TRUE SURPRISE — no precursor in price/volume/RSI history"
                )
                flag_str = "AIEM already flagged this one" if runner['was_flagged'] else "AIEM did NOT flag this — missed"
                pm_str = (
                    f"Premarket today: {premkt['premarket_volume']:,} shares, "
                    f"gap {premkt['premarket_gap_pct']:+.1f}% vs prior close."
                    if premkt.get('has_data') and premkt.get('premarket_gap_pct') is not None
                    else "Premarket today: no data."
                )
                grind_str = (
                    f"7-day grind: {predictability['grind_streak_days']}-day up-streak, "
                    f"+{predictability['grind_cum_pct']:.1f}% cumulative."
                    if predictability.get('grind_streak_days', 0) >= 3
                    else ""
                )
                # Day-by-day lookback string: which of the last 1-7 days actually
                # carried a flag, so the report names the day, not just "yesterday".
                lookback_parts = []
                for d in predictability.get('daily_lookback', []):
                    if not d['flags']:
                        continue
                    day_label = "y'day" if d['days_back'] == 1 else f"{d['days_back']}d ago"
                    lookback_parts.append(f"{day_label}: {', '.join(d['flags'])}")
                lookback_str = "; ".join(lookback_parts) or "no day in the last week showed a flagged metric"
                discovery_text = (
                    f"TOP-10 REVIEW [{bucket.upper()}]: {ticker} moved +{move_pct:.1f}% today "
                    f"(MC ${runner['market_cap']/1e6:,.0f}M). Same-day pattern: {pattern_str}. "
                    f"Open=${runner['open']:.2f} Close=${runner['close']:.2f}. "
                    f"1-7 day lookback — {lookback_str}. "
                    f"Yesterday specifically: move {predictability['prior_day_move_pct']}%, "
                    f"RSI(14)={predictability['rsi']}, "
                    f"vol buildup={predictability['volume_buildup_x']}x. "
                    f"{pm_str} {grind_str} "
                    f"Verdict: {verdict_str}. {flag_str}. "
                    f"Lesson: {lesson}. Next time: {action}."
                )
                all_discovery_texts.append(discovery_text)

                bucket_lines[bucket].append(
                    f"{'✅' if runner['was_flagged'] else '❌'} ${ticker} +{move_pct:.1f}% | "
                    f"MC ${runner['market_cap']/1e6:,.0f}M | "
                    f"{'🟢' if predictability['verdict']=='predictable' else '🔴'} {lead_reason}"
                )

                # Full per-ticker detail block for the bucket Telegram sends —
                # the terse bucket_lines emoji-row was the "trash" version;
                # this spells out exactly what was knowable and when, walking
                # the FULL 1-7 day lookback (not just "yesterday") plus this
                # morning's premarket and the 7-day grind streak.
                detail_block = [
                    f"{'✅' if runner['was_flagged'] else '❌'} ${ticker} +{move_pct:.1f}%  (MC ${runner['market_cap']/1e6:,.0f}M, "
                    f"open ${runner['open']:.2f} → close ${runner['close']:.2f})",
                    f"   {flag_str}",
                    f"   Same-day signals: {pattern_str}",
                    f"   1-7 day lookback: {lookback_str}",
                    f"   {pm_str}",
                ]
                if grind_str:
                    detail_block.append(f"   {grind_str}")
                if predictability.get('strongest_day'):
                    sd = predictability['strongest_day']
                    label = "yesterday" if sd['days_back'] == 1 else f"{sd['days_back']} days ago"
                    detail_block.append(f"   🎯 Strongest precursor was {label}: {'; '.join(sd['flags'])}")
                detail_block.append(f"   Verdict: {verdict_str}")
                detail_block.append(f"   📖 Lesson: {lesson}")
                detail_block.append(f"   🔧 Next time: {action}")
                bucket_detail_lines[bucket].append("\n".join(detail_block))

                discoveries += 1
                log.info(f"  TOP10 [{bucket}]: {ticker} +{move_pct:.1f}% — {pattern_str} — {verdict_str} — {flag_str}")
                time.sleep(0.1)

            except Exception as e:
                log.error(f"Missed runner analysis error {ticker}: {e}")
                continue

    # aiem_research_insights.research_date is UNIQUE across the WHOLE table
    # (one row per calendar day, shared by every job/process that writes here —
    # not per session_name). A loop of per-ticker INSERTs would abort the
    # transaction after the first row every single day. Write ONE combined
    # row instead, and on conflict APPEND rather than overwrite so we never
    # clobber another job's insight already saved for today.
    if all_discovery_texts:
        combined_findings = "\n".join(all_discovery_texts)
        top_move = max(r['move_pct'] for runners in buckets.values() for r in runners) if discoveries else 0
        try:
            cur.execute("""
                INSERT INTO aiem_research_insights
                    (research_date, findings, confidence, session_name)
                VALUES (%s, %s, %s, 'AIEM_MISSED_RUNNER')
                ON CONFLICT (research_date) DO UPDATE
                    SET findings = aiem_research_insights.findings || E'\\n' || EXCLUDED.findings
            """, (today, combined_findings, min(99, int(round(top_move)))))
            conn.commit()
        except Exception as e:
            log.error(f"missed_runner findings upsert failed: {e}")
            conn.rollback()

    log.info(f"Logged {discoveries} missed runner findings across 4 cap tiers")

    # ── T003: lightweight local validation of today's freshly-extracted
    # watch criteria. One backtest per UNIQUE (metric, operator, threshold)
    # combo — not per ticker — since the same combo (e.g. rsi_14 >= 70) can
    # come from several misses today and only needs validating once.
    try:
        cur.execute("""
            SELECT DISTINCT metric_name, operator, threshold_value
            FROM aiem_watch_criteria WHERE discovered_date = %s
        """, (today,))
        combos = cur.fetchall()
        for metric_name, operator, threshold_value in combos:
            stats = _aiem_validate_watch_criterion(conn, cur, metric_name, operator, float(threshold_value))
            cur.execute("""
                UPDATE aiem_watch_criteria
                SET validation_n = %s, validation_win_rate = %s, validation_avg_next_day = %s
                WHERE discovered_date = %s AND metric_name = %s AND operator = %s AND threshold_value = %s
            """, (stats['n'], stats['win_rate'], stats['avg_next_day'],
                  today, metric_name, operator, threshold_value))
            tag = "experimental" if stats['n'] < _WATCH_VALIDATION_MIN_N else "validated"
            log.info(f"[watch_validate] {metric_name} {operator} {threshold_value}: "
                      f"n={stats['n']} win_rate={stats['win_rate']} avg_next_day={stats['avg_next_day']} ({tag})")
        if combos:
            conn.commit()
    except Exception as v_e:
        log.warning(f"[watch_validate] batch validation pass failed: {v_e}")
        try:
            conn.rollback()
        except Exception:
            pass

    result.update({
        'buckets': buckets, 'bucket_lines': bucket_lines,
        'bucket_detail_lines': bucket_detail_lines,
        'discoveries': discoveries, 'all_discovery_texts': all_discovery_texts,
        'reason_freq': reason_freq,
        'predictable_n': predictable_n, 'surprise_n': surprise_n,
        'premkt_n': premkt_n, 'grind_n': grind_n, 'eod_n': eod_n,
    })
    return result


def _aiem_build_narrative(grade: dict, missed: dict, today) -> str:
    """
    Plain-English "what we learned / how we'll improve" paragraph, synthesized
    from today's grading results + missed-runner findings. Appended to the
    combined 4:30 PM EOD report so the report explains itself instead of just
    listing numbers.
    """
    graded         = grade.get('graded', 0)
    review_buckets = missed.get('buckets', {})
    reviewed_n     = sum(len(v) for v in review_buckets.values())

    if not graded and not reviewed_n:
        return ("📚 What we learned: nothing to grade and no movers to review today — "
                "a quiet day for the learning loop. No model changes triggered.")

    parts = []
    if graded:
        wins = grade.get('wins', 0)
        wr   = wins / graded * 100
        avg_ret = grade.get('avg_ret', 0.0)
        if wr >= 60:
            parts.append(f"today's {graded} picks ran at a strong {wr:.0f}% win rate (avg {avg_ret:+.1f}%)")
        elif wr >= 45:
            parts.append(f"today's {graded} picks ran about even ({wr:.0f}% win rate, avg {avg_ret:+.1f}%)")
        else:
            parts.append(f"today's {graded} picks struggled ({wr:.0f}% win rate, avg {avg_ret:+.1f}%)")
        top_loss_sigs = grade.get('top_loss_sigs', [])
        if top_loss_sigs:
            sig_str = ", ".join(s for s, _ in top_loss_sigs)
            parts.append(f"the losers leaned on {sig_str} — those signals get a trust-weight haircut tonight at 6 PM")
    else:
        parts.append("no predictions were open to grade today")

    buckets  = missed.get('buckets', {})
    reviewed = sum(len(v) for v in buckets.values())
    if reviewed:
        cap_str   = ", ".join(f"{len(v)} {k}" for k, v in buckets.items())
        missed_n  = sum(1 for v in buckets.values() for r in v if not r.get('was_flagged'))
        parts.append(f"reviewed the day's top movers across all 4 cap tiers ({cap_str}) — "
                     f"{missed_n} of those {reviewed} weren't on AIEM's radar at all")
        reason_freq = missed.get('reason_freq') or {}
        if reason_freq:
            top_reason = max(reason_freq.items(), key=lambda x: x[1])[0]
            top_lesson, top_action = _REASON_LESSONS.get(
                top_reason, ("no clear takeaway", "no model change triggered")
            )
            parts.append(f"the most common reason was '{top_reason}' ({top_lesson}) — going forward: {top_action}")
        predictable_n = missed.get('predictable_n', 0)
        surprise_n    = missed.get('surprise_n', 0)
        if predictable_n or surprise_n:
            parts.append(
                f"of all {reviewed} reviewed, {predictable_n} showed a precursor in yesterday's "
                f"volume/price/RSI history (should have been caught) vs {surprise_n} that were "
                f"genuine surprises with no warning"
            )
        # Self-critique, broken out by WHEN the warning was actually available —
        # this morning's premarket tape, yesterday's close, or a multi-day grind
        # — so the lesson is "we could have caught it at point X, and here's the
        # fix" not just a count.
        premkt_n = missed.get('premkt_n', 0)
        grind_n  = missed.get('grind_n', 0)
        eod_n    = missed.get('eod_n', 0)
        if premkt_n:
            _, pm_action = _REASON_LESSONS["premarket volume+gap (same morning)"]
            parts.append(
                f"{premkt_n} of them flashed a premarket volume+gap buildup THIS MORNING before "
                f"the bell — that's the earliest possible catch and AIEM missed it live; going forward: {pm_action}"
            )
        if eod_n:
            _, eod_action = _REASON_LESSONS["strong/weak EOD close (1-7 day lookback)"]
            parts.append(
                f"{eod_n} closed at the top/bottom of their range on elevated volume on some day "
                f"in the last week (not always just yesterday) — a classic 'coiled' close; going forward: {eod_action}"
            )
        if grind_n:
            _, grind_action = _REASON_LESSONS["multi-day slow grinder (7-day lookback)"]
            parts.append(
                f"{grind_n} were quietly grinding higher for 3-7 straight days before today's breakout — "
                f"yes, AIEM could have flagged these as far back as 7 days ago by watching the streak; "
                f"going forward: {grind_action}"
            )
        if not (premkt_n or eod_n or grind_n) and surprise_n:
            parts.append("none of today's true surprises had a premarket, prior-close, or multi-day tell — "
                         "these were genuinely unpredictable from price/volume alone, so no model change triggered for them")
    else:
        parts.append("no movers to review today — markets were closed or data was unavailable")

    return "📚 What we learned: " + "; ".join(parts) + "."


# ─────────────────────────────────────────────────────────────
# JOB 3+4 COMBINED: EOD REPORT — 4:30 PM ET
# Grades today's predictions AND finds missed runners in ONE job,
# then sends ONE combined Telegram report (with a narrated
# "what we learned / how we'll improve" paragraph) instead of the
# old separate 4:30 PM grade + 4:45 PM missed-runner sends.
# ─────────────────────────────────────────────────────────────
def aiem_eod_report():
    today = date.today()
    log.info(f"=== EOD REPORT {today} (grading + missed runners, combined) ===")

    conn = None
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        grade  = _aiem_grade_predictions(conn, cur, today)
        missed = _aiem_find_missed_runners(conn, cur, today)
        narrative = _aiem_build_narrative(grade, missed, today)

        msg = f"🤖 AIEM EOD REPORT — {today}\n\n"

        if grade['graded']:
            wr = grade['wins'] / grade['graded'] * 100
            msg += f"📊 GRADING: {grade['wins']}W / {grade['graded']-grade['wins']}L  ({wr:.0f}% WR, avg {grade['avg_ret']:+.1f}%)\n"
            if grade['l_picks']:
                worst = min(grade['l_picks'], key=lambda x: x[1])
                msg += f"   Worst: ${worst[0]} {worst[1]:+.1f}%\n"
            if grade['w_picks']:
                best = max(grade['w_picks'], key=lambda x: x[1])
                msg += f"   Best: ${best[0]} {best[1]:+.1f}%\n"
        else:
            msg += "📊 GRADING: nothing to grade today\n"

        b = missed['buckets']
        n_reviewed = sum(len(v) for v in b.values())
        n_missed_in_review = sum(1 for v in b.values() for r in v if not r.get('was_flagged'))
        msg += "\n"
        if n_reviewed:
            msg += (f"🔍 DAILY TOP-10 REVIEW: {n_reviewed} stocks "
                    f"(micro={len(b['micro'])} small={len(b['small'])} mid={len(b['mid'])} large={len(b['large'])})\n")
            msg += f"   {n_missed_in_review} were NOT on AIEM's radar (missed) | {n_reviewed-n_missed_in_review} already flagged\n"
            msg += (f"   Predictability: {missed.get('predictable_n',0)} had a "
                    f"pre-move precursor | {missed.get('surprise_n',0)} true surprises\n")
            for r in missed['big_movers'][:3]:
                msg += f"   ${r['ticker']} +{r['move_pct']:.1f}% (missed, 20%+)\n"
        else:
            msg += "🔍 DAILY TOP-10 REVIEW: no movers today — market closed or data unavailable\n"

        msg += f"\n{narrative}\n\nTrust weights update 6 PM → tomorrow's picks adjusted."

        _aiem_send_sms(msg)
        log.info("Combined EOD report sent")

        # ── Supplementary visuals/detail — kept as separate sends (charts are
        # images; bucket breakdowns can be long) but all part of this one job ──
        if grade.get('chart_tickers'):
            _aiem_send_chart("eod_report", f"AIEM Outcomes — {today}", grade['chart_tickers'])

        if n_reviewed:
            top3 = missed['big_movers'][:3]
            if top3:
                _aiem_send_chart("missed_runners", f"AIEM Missed Runners — {today}",
                                  [r['ticker'] for r in top3])
            for bucket in ('micro', 'small', 'mid', 'large'):
                lines = missed.get('bucket_detail_lines', {}).get(bucket) or missed['bucket_lines'][bucket]
                if not lines:
                    continue
                header = f"🤖 {_CAP_BUCKET_LABELS[bucket]} — Daily Top-10 Review — {today}\n\n"
                # Telegram caps messages at 4096 chars — these detailed blocks
                # (premarket + EOD + grind + RSI per ticker) can blow past that
                # for a full 10-name bucket, so chunk on ticker boundaries
                # instead of letting Telegram silently drop an oversized send.
                chunk = header
                for line in lines:
                    if len(chunk) + len(line) + 2 > 3800:
                        _tg_send_QUARANTINED_DO_NOT_USE(chunk.rstrip())
                        time.sleep(0.3)
                        chunk = header
                    chunk += line + "\n\n"
                if chunk.strip() and chunk != header:
                    _tg_send_QUARANTINED_DO_NOT_USE(chunk.rstrip())
                time.sleep(0.3)

    except Exception as e:
        log.error(f"eod_report error: {e}")
        if conn:
            try: conn.rollback()
            except Exception: pass
    finally:
        _put_conn(conn)


# ─────────────────────────────────────────────────────────────
# JOB 5: NIGHTLY LEARN — 6:00 PM ET
# AIEM updates signal trust weights from graded outcomes
# ─────────────────────────────────────────────────────────────
def aiem_nightly_learn():
    today = date.today()
    log.info(f"=== NIGHTLY LEARN {today} ===")

    conn = None
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        # Join predictions → outcomes over last 60 days
        cur.execute("""
            SELECT p.signal_basis, p.confidence_score, p.ticker,
                   o.t1_return, o.win_t3, o.t3_return, o.win_t5, o.t5_return
            FROM aiem_predictions p
            JOIN aiem_prediction_outcomes o
                ON p.ticker = o.ticker AND p.prediction_date = o.prediction_date
            WHERE p.prediction_date >= %s AND o.t1_return IS NOT NULL
        """, (today - timedelta(days=60),))
        rows = cur.fetchall()

        if not rows:
            log.info("No graded outcomes yet — skipping weight update")
            return

        # Tally per signal name
        tallies = {}
        for sig_basis, conf, ticker, t1_ret, win_t3, t3_ret, win_t5, t5_ret in rows:
            if not sig_basis:
                continue
            win = bool(win_t3) if win_t3 is not None else (float(t1_ret or 0) > 0)
            ret = float(t3_ret or t1_ret or 0)
            for sig in (s.strip() for s in sig_basis.split(',') if s.strip()):
                if sig not in tallies:
                    tallies[sig] = {'wins': 0, 'total': 0, 'total_ret': 0.0}
                tallies[sig]['total']     += 1
                tallies[sig]['total_ret'] += ret
                if win:
                    tallies[sig]['wins'] += 1

        # Update signal_trust_weights
        updated = 0
        for sig_name, t in tallies.items():
            n        = t['total']
            win_rate = t['wins'] / n if n > 0 else 0.5
            avg_ret  = t['total_ret'] / n if n > 0 else 0

            # Conservative early (low n), more confident later (high n)
            sample_conf  = min(1.0, n / 30)
            raw_trust    = 0.5 + (win_rate - 0.5) * 2
            trust_weight = 1.0 + (raw_trust - 0.5) * sample_conf
            trust_weight = max(0.3, min(2.0, trust_weight))

            cur.execute("""
                INSERT INTO signal_trust_weights
                    (signal_name, context_bucket, rolling_win_rate,
                     n_outcomes_observed, trust_weight, last_updated_at)
                VALUES (%s, 'AIEM_MICROCAP', %s, %s, %s, NOW())
                ON CONFLICT (signal_name, context_bucket) DO UPDATE
                    SET rolling_win_rate    = EXCLUDED.rolling_win_rate,
                        n_outcomes_observed = EXCLUDED.n_outcomes_observed,
                        trust_weight        = EXCLUDED.trust_weight,
                        last_updated_at     = NOW()
            """, (sig_name, round(win_rate, 4), n, round(trust_weight, 4)))

            updated += 1
            log.info(f"  {sig_name}: wr={win_rate:.1%} n={n} "
                     f"trust={trust_weight:.3f} avg_ret={avg_ret:+.1f}%")

        # Mine pattern frequencies from recent missed-runner findings
        cur.execute("""
            SELECT findings FROM aiem_research_insights
            WHERE session_name = 'AIEM_MISSED_RUNNER'
              AND research_date >= %s
        """, (today - timedelta(days=7),))
        missed_rows = cur.fetchall()

        pattern_freq = {}
        for (desc,) in missed_rows:
            for pat in ['low_float', 'nano_cap', 'had_catalyst',
                        'high_vol', 'vol_surge', 'squeeze']:
                if pat in (desc or ''):
                    pattern_freq[pat] = pattern_freq.get(pat, 0) + 1

        new_insights = []
        for pattern, freq in pattern_freq.items():
            if freq >= 3:
                insight = (f"PATTERN DISCOVERY: '{pattern}' appeared in "
                           f"{freq} missed runners this week — "
                           f"AIEM should weight this signal higher")
                new_insights.append(insight)
                log.info(f"  {insight}")
                cur.execute("""
                    INSERT INTO aiem_research_insights
                        (research_date, findings, confidence, session_name)
                    VALUES (%s, %s, %s, 'AIEM_PATTERN_DISCOVERY')
                """, (today, insight, min(95, freq * 15)))

        conn.commit()
        log.info(f"nightly_learn: updated {updated} weights, "
                 f"{len(new_insights)} pattern discoveries")

        top_sigs = sorted(tallies.items(),
                          key=lambda x: x[1]['wins'] / max(x[1]['total'], 1),
                          reverse=True)[:3]
        sig_summary = ", ".join(
            f"{s}({d['wins']}/{d['total']})" for s, d in top_sigs
        )
        _aiem_send_sms(
            f"🤖 AIEM NIGHTLY — {today}\n"
            f"Weights updated: {updated}\n"
            f"Top signals: {sig_summary}\n"
            f"Discoveries: {len(new_insights)}\n"
            f"Ready for tomorrow."
        )

    except Exception as e:
        log.error(f"nightly_learn error: {e}")
        if conn:
            try: conn.rollback()
            except Exception: pass
    finally:
        _put_conn(conn)


# ─────────────────────────────────────────────────────────────
# JOB 5b: MORNING BRIEF — 8:00 AM ET
# Always fires. Sends today's top premarket picks to Telegram.
# If the DB is empty (scan hasn't run yet), triggers a scan first.
# ─────────────────────────────────────────────────────────────
def aiem_morning_brief():
    conn = None
    try:
        conn  = _get_conn()
        cur   = conn.cursor()
        today = date.today()

        cur.execute("""
            SELECT ticker, rank, confidence_score, signal_basis, reasoning, predicted_move
            FROM aiem_predictions
            WHERE prediction_date = %s
            ORDER BY rank ASC LIMIT 5
        """, (today,))
        picks = cur.fetchall()

        if not picks:
            log.info("No picks in DB at 8 AM — running emergency premarket scan now")
            _put_conn(conn)
            conn = None
            aiem_premarket_scan()
            conn = _get_conn()
            cur  = conn.cursor()
            cur.execute("""
                SELECT ticker, rank, confidence_score, signal_basis, reasoning, predicted_move
                FROM aiem_predictions
                WHERE prediction_date = %s
                ORDER BY rank ASC LIMIT 5
            """, (today,))
            picks = cur.fetchall()

        if not picks:
            _aiem_send_sms("🤖 AIEM 8 AM: No picks found today — market may be quiet or data unavailable.")
            log.warning("morning_brief: no picks after emergency scan")
            return

        lines = [f"🤖 AIEM Morning Picks — {today.strftime('%a %b %d')}",
                 "━━━━━━━━━━━━━━━━━━━━"]
        for ticker, rank, conf, sig_basis, reasoning, predicted in picks:
            lines.append(f"#{rank} ${ticker}  {conf:.0f}/100")
            lines.append(f"   {predicted}")
            short_reason = (reasoning or '')[:80]
            if short_reason:
                lines.append(f"   {short_reason}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔔 Watch open at 9:30 AM ET")

        _aiem_send_sms("\n".join(lines))
        log.info(f"morning_brief sent: {len(picks)} picks")

        _aiem_send_chart(
            "morning_brief",
            f"AIEM Morning Picks — {today.strftime('%a %b %d')}",
            [p[0] for p in picks],
        )

    except Exception as e:
        log.error(f"morning_brief error: {e}")
    finally:
        _put_conn(conn)


# ─────────────────────────────────────────────────────────────
# JOB 6: MISSED MORNING CHECK — 9:45 AM ET
# Safety net: if AIEM has zero predictions by 9:45, run emergency scan
# ─────────────────────────────────────────────────────────────
def aiem_missed_morning_check():
    conn = None
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM aiem_predictions WHERE prediction_date = %s",
                    (date.today(),))
        count = cur.fetchone()[0]
        if count == 0:
            log.warning("NO PREDICTIONS AT 9:45 AM — running emergency scan")
            _aiem_send_sms("⚠️ AIEM: No premarket predictions at 9:45 AM — emergency scan running")
            aiem_premarket_scan()
        else:
            log.info(f"Morning check OK — {count} predictions on file")

        # ── T004: prospective miss-pattern scan ─────────────────────────────
        # No premarket `candidates` list available at this point in the day,
        # so only the broad (non-gap) metrics — eod_range_position,
        # volume_buildup_x, rsi_14, grind_streak_days — get re-screened here;
        # premarket_gap_pct criteria are covered by the 7:00-9:30 AM
        # aiem_premarket_scan hook instead. Dedup via aiem_watch_alerts keeps
        # this safe to run again at 9:45 even if premarket already alerted.
        try:
            _aiem_scan_watch_criteria(conn, cur, "missed_morning_check")
        except Exception as _wc_e:
            log.warning(f"[watch_scan] missed_morning_check hook failed: {_wc_e}")
    except Exception as e:
        log.error(f"missed_morning_check error: {e}")
    finally:
        _put_conn(conn)


# ─────────────────────────────────────────────────────────────
# MAIN — BlockingScheduler (standalone process, not embedded in Flask)
# ─────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("AIEM AUTONOMOUS ENGINE STARTING")
    log.info("Standalone process — zero Flask dependency")
    log.info("=" * 60)

    try:
        _init_pool()
        log.info("DB pool ready")
    except Exception as e:
        log.error(f"DB pool failed — cannot start: {e}")
        return

    if not POLYGON_API_KEY:
        log.error("POLYGON_API_KEY not set — cannot start")
        return

    scheduler = BlockingScheduler(
        timezone=ET,
        job_defaults={
            'coalesce':           True,
            'max_instances':      1,
            'misfire_grace_time': 3600,   # 1-hr grace — never miss a morning
        }
    )

    # Premarket scan: every 15 min 7:00–9:30 AM
    scheduler.add_job(_logged_job(aiem_premarket_scan),       'cron',
                      hour='7-9', minute='0,15,30,45',
                      id='aiem_premarket', replace_existing=True)

    # Morning brief: 8:00 AM — always sends top picks to Telegram
    scheduler.add_job(_logged_job(aiem_morning_brief),        'cron',
                      day_of_week='mon-fri', hour=8, minute=0,
                      id='aiem_morning_brief', replace_existing=True)

    # Open watcher: every 5 min 9:30–10:30 AM
    scheduler.add_job(_logged_job(aiem_open_watcher),         'cron',
                      hour='9,10', minute='*/5',
                      id='aiem_open_watch', replace_existing=True)

    # Missed morning safety net: 9:45 AM
    scheduler.add_job(_logged_job(aiem_missed_morning_check), 'cron',
                      hour=9, minute=45,
                      id='aiem_morning_check', replace_existing=True)

    # Combined EOD report (grading + missed runners + narrative): 4:30 PM
    # Replaces the old separate 4:30 PM grade + 4:45 PM missed-runner jobs
    # so the user gets ONE Telegram report instead of two staggered ones.
    scheduler.add_job(_logged_job(aiem_eod_report),           'cron',
                      hour=16, minute=30,
                      id='aiem_eod_report', replace_existing=True)

    # Nightly learn + weight update: 6:00 PM
    scheduler.add_job(_logged_job(aiem_nightly_learn),        'cron',
                      hour=18, minute=0,
                      id='aiem_learn', replace_existing=True)

    log.info("Scheduler jobs registered:")
    log.info("  7:00–9:30 AM  premarket_scan    (every 15 min)")
    log.info("  8:00 AM       morning_brief     (Telegram picks — always fires)")
    log.info("  9:30–10:30 AM open_watcher      (every 5 min)")
    log.info("  9:45 AM       missed_morning_check")
    log.info("  4:30 PM       eod_report        (grading + missed runners + narrative, combined)")
    log.info("  6:00 PM       nightly_learn")

    global _scheduler_ref
    _scheduler_ref = scheduler
    _start_health_server()

    log.info("AIEM is live. Watching markets autonomously.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("AIEM autonomous engine stopped")


if __name__ == '__main__':
    main()
