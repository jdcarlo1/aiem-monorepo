# ============================================================
# AIEM AUTONOMOUS ENGINE — aiem_autonomous.py
# Completely standalone — zero Flask dependency
# Runs as a separate process managed by the aiem-process workflow
# ============================================================

import os
import time
import logging
import psycopg2
import psycopg2.pool
import requests
from datetime  import datetime, date, timedelta
from zoneinfo  import ZoneInfo
from apscheduler.schedulers.blocking import BlockingScheduler

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
MIN_PREMARKET_VOLUME    = 25_000
MIN_GAP_PCT             = 2.0
MAX_PRICE               = 20.0
MIN_PRICE               = 0.50
CONFIDENCE_ALERT_THRESHOLD = 72

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

# ─────────────────────────────────────────────────────────────
# DATABASE POOL
# ─────────────────────────────────────────────────────────────
_AIEM_POOL = None

def _init_pool():
    global _AIEM_POOL
    if _AIEM_POOL is None:
        _AIEM_POOL = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=5, dsn=DATABASE_URL
        )
        log.info("DB pool initialized (min=1 max=5)")

def _get_conn():
    global _AIEM_POOL
    if _AIEM_POOL is None:
        _init_pool()
    return _AIEM_POOL.getconn()

def _put_conn(conn):
    global _AIEM_POOL
    if _AIEM_POOL and conn:
        _AIEM_POOL.putconn(conn)

# ─────────────────────────────────────────────────────────────
# SMS — standalone (no dependency on main.py _send_sms)
# ─────────────────────────────────────────────────────────────
def _aiem_send_sms(message: str):
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

def _aiem_get_ticker_details(ticker: str) -> dict:
    """Float, market cap, shares outstanding from Polygon reference."""
    return _poly(f'/v3/reference/tickers/{ticker}', timeout=10).get('results', {})

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
# JOB 1: PREMARKET SCAN — every 15 min 7:00–9:30 AM ET
# ─────────────────────────────────────────────────────────────
def aiem_premarket_scan():
    now_et = datetime.now(ET)
    log.info(f"=== PREMARKET SCAN {now_et.strftime('%H:%M ET')} ===")

    conn = None
    try:
        conn = _get_conn()

        # Step 1: Bulk scan entire market (8K+ tickers, ~3 seconds)
        all_tickers = _aiem_bulk_snapshot()
        if not all_tickers:
            log.warning("Bulk snapshot empty — Polygon may be down")
            return

        # Step 2: Filter to microcap universe (price, gap, volume)
        candidates = []
        for t in all_tickers:
            try:
                day    = t.get('day', {})
                prev   = t.get('prevDay', {})
                ticker = t.get('ticker', '')
                if not ticker or len(ticker) > 5:
                    continue
                price   = t.get('lastTrade', {}).get('p') or day.get('c') or 0
                prev_cl = prev.get('c') or price or 1
                volume  = day.get('v') or 0
                gap_pct = ((price - prev_cl) / prev_cl * 100) if prev_cl else 0
                if price < MIN_PRICE or price > MAX_PRICE:
                    continue
                if gap_pct < MIN_GAP_PCT:
                    continue
                if volume < MIN_PREMARKET_VOLUME:
                    continue
                candidates.append({'ticker': ticker, 'snap': t,
                                   'gap': gap_pct, 'volume': volume})
            except Exception:
                continue

        log.info(f"Universe: {len(all_tickers)} → {len(candidates)} microcap candidates")
        if not candidates:
            log.warning("No microcap candidates passed filters")
            return

        # Step 3: Sort by gap×volume signal strength; take top 50 for deep score
        candidates.sort(key=lambda x: x['gap'] * (x['volume'] / 100_000), reverse=True)
        top_candidates = candidates[:50]

        # Step 4: Load trust weights
        trust_weights = _load_trust_weights(conn)

        # Step 5: Deep score each finalist (adds Polygon ticker-details + news)
        scored = []
        for c in top_candidates:
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
                conf, sig_basis, reasoning, predicted = _aiem_score_microcap(
                    ticker, c['snap'], details, news, trust_weights
                )
                scored.append({
                    'ticker':    ticker,
                    'conf':      conf,
                    'sig_basis': sig_basis,
                    'reasoning': reasoning,
                    'predicted': predicted,
                    'gap':       c['gap'],
                    'volume':    c['volume'],
                })
                log.info(f"  {ticker}: conf={conf:.1f} gap={c['gap']:.1f}% vol={c['volume']:,}")
                time.sleep(0.1)  # be nice to Polygon
            except Exception as e:
                log.error(f"Deep score error {ticker}: {e}")
                continue

        if not scored:
            log.warning("No tickers survived deep scoring")
            return

        scored.sort(key=lambda x: x['conf'], reverse=True)
        top_picks = scored[:10]

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
# JOB 3: GRADE OUTCOMES — 4:30 PM ET
# ─────────────────────────────────────────────────────────────
def aiem_grade_outcomes():
    today = date.today()
    log.info(f"=== GRADING OUTCOMES {today} ===")

    conn = None
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        cur.execute("""
            SELECT p.ticker, p.confidence_score
            FROM aiem_predictions p
            LEFT JOIN aiem_prediction_outcomes o
                ON p.ticker = o.ticker AND p.prediction_date = o.prediction_date
            WHERE p.prediction_date = %s AND o.id IS NULL
        """, (today,))
        ungraded = cur.fetchall()

        if not ungraded:
            log.info("Nothing to grade today")
            return

        graded = wins = 0
        for ticker, conf in ungraded:
            try:
                snap        = _aiem_get_snapshot(ticker)
                day         = snap.get('day', {})
                entry_price = day.get('o') or 0
                t1_price    = day.get('c') or 0
                if not entry_price or not t1_price:
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
                log.info(f"  {ticker}: {t1_return:+.1f}% {'WIN' if win_t1 else 'LOSS'}")
                time.sleep(0.1)

            except Exception as e:
                log.error(f"Grade error {ticker}: {e}")

        conn.commit()
        wr = (wins / graded * 100) if graded > 0 else 0
        log.info(f"Graded {graded} predictions — win rate today: {wr:.1f}%")

        # Also update T3/T5 for older predictions
        _grade_t3_t5(conn)

    except Exception as e:
        log.error(f"grade_outcomes error: {e}")
        if conn:
            try: conn.rollback()
            except Exception: pass
    finally:
        _put_conn(conn)


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
# JOB 4: MISSED RUNNER ANALYSIS — 4:45 PM ET
# The most important learning job: what did AIEM miss and why?
# ─────────────────────────────────────────────────────────────
def aiem_missed_runner_analysis():
    today = date.today()
    log.info(f"=== MISSED RUNNER ANALYSIS {today} ===")

    conn = None
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        # What did AIEM flag today?
        cur.execute("SELECT ticker FROM aiem_predictions WHERE prediction_date = %s", (today,))
        flagged = {row[0] for row in cur.fetchall()}

        # Get all stocks that moved 20%+ today
        daily_data = _aiem_get_grouped_daily(today)
        big_movers = []
        for stock in daily_data:
            ticker   = stock.get('T', '')
            o, c, v  = stock.get('o') or 0, stock.get('c') or 0, stock.get('v') or 0
            if o > 0 and c > 0:
                move_pct = (c - o) / o * 100
                if move_pct >= 20 and ticker not in flagged:
                    big_movers.append({'ticker': ticker, 'open': o,
                                       'close': c, 'volume': v, 'move_pct': move_pct})

        big_movers.sort(key=lambda x: x['move_pct'], reverse=True)
        log.info(f"{len(big_movers)} missed runners (20%+ AIEM didn't flag)")

        discoveries = 0
        for runner in big_movers[:20]:
            ticker   = runner['ticker']
            move_pct = runner['move_pct']
            try:
                details  = _aiem_get_ticker_details(ticker)
                news     = _aiem_get_news(ticker)
                ohlcv    = _aiem_get_ohlcv(ticker, days=2)

                float_sh = details.get('share_class_shares_outstanding') or 0
                mkt_cap  = details.get('market_cap') or 0

                patterns = []
                if float_sh and float_sh < 10_000_000:
                    patterns.append(f"low_float_{float_sh/1e6:.1f}M")
                if mkt_cap and mkt_cap < 100_000_000:
                    patterns.append(f"nano_cap_{mkt_cap/1e6:.0f}M")
                if news:
                    patterns.append("had_catalyst")
                if runner['volume'] > 1_000_000:
                    patterns.append(f"high_vol_{runner['volume']/1e6:.1f}M_shares")
                if ohlcv and len(ohlcv) >= 2:
                    prev_vol  = ohlcv[-2].get('v', 1)
                    vol_ratio = runner['volume'] / prev_vol if prev_vol > 0 else 0
                    if vol_ratio >= 3:
                        patterns.append(f"vol_surge_{vol_ratio:.1f}x")

                pattern_str    = " | ".join(patterns) if patterns else "unknown_pattern"
                discovery_text = (
                    f"MISSED RUNNER: {ticker} moved +{move_pct:.1f}% today. "
                    f"Pattern: {pattern_str}. "
                    f"Open=${runner['open']:.2f} Close=${runner['close']:.2f}. "
                    f"AIEM did not flag this — learn from it."
                )

                # Write to aiem_research_insights (correct schema for this DB)
                cur.execute("""
                    INSERT INTO aiem_research_insights
                        (research_date, findings, confidence, session_name)
                    VALUES (%s, %s, %s, 'AIEM_MISSED_RUNNER')
                """, (today, discovery_text, min(99, int(round(move_pct)))))

                discoveries += 1
                log.info(f"  MISSED: {ticker} +{move_pct:.1f}% — {pattern_str}")
                time.sleep(0.1)

            except Exception as e:
                log.error(f"Missed runner analysis error {ticker}: {e}")
                continue

        conn.commit()
        log.info(f"Logged {discoveries} missed runner findings")

        if big_movers:
            top3 = big_movers[:3]
            msg  = (f"🤖 AIEM EOD — {today}\n"
                    f"Missed runners: {len(big_movers)}\n")
            for r in top3:
                msg += f"  ${r['ticker']} +{r['move_pct']:.1f}%\n"
            msg += "AIEM analyzing to catch these tomorrow."
            _aiem_send_sms(msg)

    except Exception as e:
        log.error(f"missed_runner_analysis error: {e}")
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
    scheduler.add_job(aiem_premarket_scan,       'cron',
                      hour='7-9', minute='0,15,30,45',
                      id='aiem_premarket', replace_existing=True)

    # Open watcher: every 5 min 9:30–10:30 AM
    scheduler.add_job(aiem_open_watcher,         'cron',
                      hour='9,10', minute='*/5',
                      id='aiem_open_watch', replace_existing=True)

    # Missed morning safety net: 9:45 AM
    scheduler.add_job(aiem_missed_morning_check, 'cron',
                      hour=9, minute=45,
                      id='aiem_morning_check', replace_existing=True)

    # Grade T1 outcomes: 4:30 PM
    scheduler.add_job(aiem_grade_outcomes,       'cron',
                      hour=16, minute=30,
                      id='aiem_grade', replace_existing=True)

    # Missed runner analysis: 4:45 PM
    scheduler.add_job(aiem_missed_runner_analysis, 'cron',
                      hour=16, minute=45,
                      id='aiem_missed', replace_existing=True)

    # Nightly learn + weight update: 6:00 PM
    scheduler.add_job(aiem_nightly_learn,        'cron',
                      hour=18, minute=0,
                      id='aiem_learn', replace_existing=True)

    log.info("Scheduler jobs registered:")
    log.info("  7:00–9:30 AM  premarket_scan    (every 15 min)")
    log.info("  9:30–10:30 AM open_watcher      (every 5 min)")
    log.info("  9:45 AM       missed_morning_check")
    log.info("  4:30 PM       grade_outcomes")
    log.info("  4:45 PM       missed_runner_analysis")
    log.info("  6:00 PM       nightly_learn")
    log.info("AIEM is live. Watching markets autonomously.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("AIEM autonomous engine stopped")


if __name__ == '__main__':
    main()
