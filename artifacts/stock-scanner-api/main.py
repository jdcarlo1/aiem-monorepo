from flask import Flask, request, jsonify
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import math
import threading

from scanner import analyze_ticker, scan_tickers, WATCHLIST_DEFAULT, fetch_stock_data
from portfolio import get_portfolio, add_position, remove_position, get_portfolio_value
from backtest import backtest_strategy
from alerts import get_alerts, add_alert, delete_alert
from analytics import run_historical_analytics
from prop_signal import prop_signal
from smart_money import scan_smart_money, compute_smart_money, DEFAULT_LEADERBOARD
from congress_trades import get_congress_trades
from insider_trades import fetch_insider_trades
from email_alerts import (
    init_db, subscribe, unsubscribe, get_active_subscribers,
    send_daily_digest, smtp_configured, subscriber_count,
)
from historical_performance import init_score_history_table, save_scan_scores
from signal_outcomes import init_signal_outcomes_table, store_bull_flow_signals, get_signal_outcomes
import execution
import pnl

app = Flask(__name__)
CORS(app)

# ── Scan result cache (pre-warmed every 15 min during market hours) ───────────
import threading as _threading
app._sm_cache: dict = {}          # key = frozen sorted ticker tuple → {"result": ..., "ts": datetime}
app._sm_cache_lock = _threading.Lock()
_SM_CACHE_TTL_SECS = 1200        # 20 minutes

# ── init DB & scheduler ──────────────────────────────────────────────────────
init_db()
init_score_history_table()
init_signal_outcomes_table()

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    import pytz

    _ET = pytz.timezone("US/Eastern")

    def _do_scan_and_save(session: str) -> None:
        """Run a Smart Money scan, save scores to history, then email subscribers."""
        result  = scan_smart_money(DEFAULT_LEADERBOARD)
        signals = result.get("leaderboard", [])
        # Persist every scan so historical performance can build up over time
        save_scan_scores(signals)
        base_url = os.getenv("PUBLIC_URL", "")
        out = send_daily_digest(signals, base_url, session=session)
        print(f"[scheduler] {session} scan → {out}")

    def _run_premarket_scan():
        """9:00 AM ET — overnight OI; options haven't opened yet."""
        try:
            _do_scan_and_save("premarket")
        except Exception as e:
            print(f"[scheduler] pre-market scan error: {e}")

    def _run_morning_scan():
        """9:45 AM ET — first real options data 15 min after open."""
        try:
            _do_scan_and_save("morning")
        except Exception as e:
            print(f"[scheduler] morning scan error: {e}")

    def _run_preclose_scan():
        """3:30 PM ET — 30 min before close. Last chance to act."""
        try:
            _do_scan_and_save("preclose")
        except Exception as e:
            print(f"[scheduler] pre-close scan error: {e}")

    def _run_eod_scan():
        """4:15 PM ET — full-day final options flow summary."""
        try:
            _do_scan_and_save("eod")
        except Exception as e:
            print(f"[scheduler] EOD scan error: {e}")

    _scheduler = BackgroundScheduler(timezone=_ET)
    # Pre-market: Mon-Fri 9:00 AM ET  (overnight OI — who loaded up positions yesterday)
    _scheduler.add_job(
        _run_premarket_scan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=_ET),
        id="premarket_scan",
        replace_existing=True,
    )
    # Morning: Mon-Fri 9:45 AM ET  (market opens 9:30, data ready by 9:45)
    _scheduler.add_job(
        _run_morning_scan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=45, timezone=_ET),
        id="morning_scan",
        replace_existing=True,
    )
    # Pre-close: Mon-Fri 3:30 PM ET  (30 min before 4 PM close — still time to act)
    _scheduler.add_job(
        _run_preclose_scan,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=_ET),
        id="preclose_scan",
        replace_existing=True,
    )

    # EOD unusual-calls auto-scan — populates unusual_calls_log so the EOD SWEEP tab
    # has data without a user needing to manually open the Unusual Calls tab first.
    def _run_unusual_calls_scan(label: str):
        try:
            import yfinance as yf
            from concurrent.futures import ThreadPoolExecutor, as_completed as _asc
            _ETF_SET = {
                "SPY","QQQ","IWM","DIA","MDY","VTI","VOO",
                "XLF","XLE","XLK","XLY","XLI","XLV","XLB","XLP","XLU","XLRE",
                "SMH","SOXX","XBI","IBB","KRE","XRT","ITB","JETS","KWEB","DRAM",
                "TQQQ","SPXL","SOXL","UDOW","LABU","FNGU","TECL","UPRO","TNA","FAS","ERX",
                "SQQQ","SPXS","SOXS","SDOW","TZA","FAZ","ERY",
                "GLD","IAU","SLV","USO","UNG","GDX","GDXJ","OIH",
                "TLT","HYG","LQD","TBT","TMF","SHY","IEF","JNK",
                "EEM","EFA","FXI","EWJ","EWZ","EWY","IEMG","ARKK","IBIT","FBTC",
            }
            def _scan_one(ticker):
                hits = []
                try:
                    is_etf  = ticker in _ETF_SET
                    min_voi = 1.5 if is_etf else 3.0
                    min_prem= 250_000 if is_etf else 500_000
                    max_exp = 60 if is_etf else 30
                    tk = yf.Ticker(ticker)
                    price = tk.fast_info.get("lastPrice") or tk.fast_info.get("regularMarketPrice") or 0
                    if not price: return hits
                    for exp in (tk.options or []):
                        from datetime import datetime as _dt2
                        days = (_dt2.strptime(exp, "%Y-%m-%d") - _dt2.now()).days + 1
                        if not (1 <= days <= max_exp): continue
                        chain = tk.option_chain(exp).calls
                        for _, row in chain.iterrows():
                            try:
                                vol = int(row.get("volume") or 0)
                                oi  = int(row.get("openInterest") or 0)
                                if oi < 10 or vol < 50: continue
                                voi = vol / oi
                                if voi < min_voi: continue
                                strike = float(row["strike"])
                                otm_pct = round((strike - price) / price * 100, 2)
                                if otm_pct < -5 or otm_pct > 30: continue
                                bid = float(row.get("bid") or 0)
                                ask = float(row.get("ask") or 0)
                                mid = (bid + ask) / 2 if bid and ask else float(row.get("lastPrice") or 0)
                                prem = int(mid * vol * 100)
                                if prem < min_prem: continue
                                iv = round(float(row.get("impliedVolatility") or 0) * 100, 1)
                                urgency = "EXPIRING" if days <= 3 else "SHORT" if days <= 7 else "NEAR"
                                hits.append({"ticker": ticker, "price": price, "strike": strike,
                                             "expiry": exp, "days_out": days, "volume": vol, "oi": oi,
                                             "vol_oi": round(voi, 2), "prem": prem, "otm_pct": otm_pct,
                                             "iv": iv, "urgency": urgency})
                            except Exception: pass
                except Exception: pass
                return hits
            all_hits = []
            with ThreadPoolExecutor(max_workers=8) as ex:
                for fut in _asc({ex.submit(_scan_one, t): t for t in DEFAULT_LEADERBOARD}):
                    all_hits.extend(fut.result() or [])
            _save_unusual_calls_to_db(all_hits)
            print(f"[scheduler] {label} unusual-calls scan → {len(all_hits)} hits saved")
        except Exception as e:
            import traceback
            print(f"[scheduler] {label} unusual-calls scan error: {e}\n{traceback.format_exc()}")

    _scheduler.add_job(
        lambda: _run_unusual_calls_scan("pre-close"),
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=_ET),
        id="preclose_unusual_calls",
        replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _run_unusual_calls_scan("eod-1"),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=_ET),
        id="eod_unusual_calls_1",
        replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _run_unusual_calls_scan("eod-2"),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=_ET),
        id="eod_unusual_calls_2",
        replace_existing=True,
    )

    # EOD: Mon-Fri 4:15 PM ET  (market closes 4:00, options data settled by 4:15)
    _scheduler.add_job(
        _run_eod_scan,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=_ET),
        id="eod_scan",
        replace_existing=True,
    )
    # Outcomes: Mon-Fri 4:30 PM ET — after market close, fetch closing prices for open AI trade log entries
    def _run_outcomes_update():
        try:
            _update_ai_trade_outcomes()
        except Exception as e:
            print(f"[scheduler] outcomes update error: {e}")
    _scheduler.add_job(
        _run_outcomes_update,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=_ET),
        id="outcomes_update",
        replace_existing=True,
    )
    # Signal snapshot: Mon-Fri 4:00 PM ET — snapshot today's signals for multi-day persistence tracking
    def _run_signal_snapshot():
        try:
            _save_signal_snapshot()
        except Exception as e:
            print(f"[scheduler] signal snapshot error: {e}")
    _scheduler.add_job(
        _run_signal_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=_ET),
        id="signal_snapshot",
        replace_existing=True,
    )
    # Daily vol snapshot: Mon-Fri 4:05 PM ET — store IV skew + short interest for future percentile ranking
    def _run_daily_vol_snapshot():
        try:
            _save_daily_vol_snapshot()
        except Exception as e:
            print(f"[scheduler] daily vol snapshot error: {e}")
    _scheduler.add_job(
        _run_daily_vol_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=5, timezone=_ET),
        id="daily_vol_snapshot",
        replace_existing=True,
    )
    # SPY cache refresh: Mon-Fri 9:05 AM ET — pre-warm SPY 1y cache before market opens
    def _run_spy_cache_refresh():
        try:
            _refresh_spy_1y_cache()
        except Exception as e:
            print(f"[scheduler] SPY cache refresh error: {e}")
    _scheduler.add_job(
        _run_spy_cache_refresh,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=5, timezone=_ET),
        id="spy_cache_refresh",
        replace_existing=True,
    )
    # Micro-cap net flow pre-warm: every 30 min during market hours (9:45 AM – 3:30 PM ET)
    def _run_microcap_prewarm():
        try:
            from datetime import datetime as _dt
            out = _run_microcap_flow_scan()
            app._nfmc_cache    = out
            app._nfmc_cache_ts = _dt.now()
            print(f"[scheduler] micro-cap pre-warm → scanned {out['scanned']} tickers, {len(out['results'])} positive")
        except Exception as e:
            print(f"[scheduler] micro-cap pre-warm error: {e}")
    _scheduler.add_job(
        _run_microcap_prewarm,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/30", timezone=_ET),
        id="microcap_prewarm",
        replace_existing=True,
    )
    # Micro-cap options scan: Mon-Fri 10:30 AM ET — after market data settles post-open
    def _run_microcap_options_auto():
        try:
            hits = _run_microcap_options_scan()
            _save_microcap_calls_to_db(hits)
            print(f"[scheduler] micro-cap options scan → {len(hits)} unusual calls saved")
        except Exception as e:
            print(f"[scheduler] micro-cap options scan error: {e}")
    _scheduler.add_job(
        _run_microcap_options_auto,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=30, timezone=_ET),
        id="microcap_options_auto",
        replace_existing=True,
    )
    # AI Trades auto-generation: Mon-Fri 10:00 AM ET — caches are warm after 9:45 AM morning scan
    def _run_ai_trades_auto():
        try:
            import threading as _thr
            if not getattr(app, "_ait_generating", False):
                _thr.Thread(target=_ai_trades_worker, daemon=True).start()
                print("[scheduler] AI trades auto-generation started")
        except Exception as e:
            print(f"[scheduler] AI trades auto error: {e}")
    _scheduler.add_job(
        _run_ai_trades_auto,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=_ET),
        id="ai_trades_auto",
        replace_existing=True,
    )
    # AI Short Calls auto-generation: Mon-Fri 10:15 AM ET — after scanner caches warm
    def _run_ai_short_calls_auto():
        try:
            import threading as _thr2
            from datetime import datetime as _dt2
            def _worker():
                try:
                    with app.app_context() if hasattr(app, "app_context") else __import__("contextlib").nullcontext():
                        resp = ai_short_calls()
                        data = resp.get_json() if hasattr(resp, "get_json") else {}
                        picks = data.get("picks", [])
                        if picks:
                            _save_ai_short_calls_to_log(picks, str(_dt2.now().date()))
                            print(f"[scheduler] AI short calls saved {len(picks)} picks")
                        else:
                            print("[scheduler] AI short calls: no picks returned")
                except Exception as _we:
                    print(f"[scheduler] AI short calls worker error: {_we}")
            _thr2.Thread(target=_worker, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] AI short calls auto error: {e}")
    _scheduler.add_job(
        _run_ai_short_calls_auto,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=15, timezone=_ET),
        id="ai_short_calls_auto",
        replace_existing=True,
    )
    # AI Short Calls outcomes: Mon-Fri 4:32 PM ET — alongside AI trade outcomes
    def _run_sc_outcomes():
        try:
            _update_ai_short_call_outcomes()
        except Exception as e:
            print(f"[scheduler] short call outcomes error: {e}")
    _scheduler.add_job(
        _run_sc_outcomes,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=32, timezone=_ET),
        id="sc_outcomes_update",
        replace_existing=True,
    )
    # Scan cache warmer — every 15 min during market hours so on-demand scans feel instant
    def _warm_sm_cache():
        try:
            from datetime import datetime as _dtw
            from pytz import timezone as _tzw
            _et_now = _dtw.now(_tzw("America/New_York"))
            _wday = _et_now.weekday()          # 0=Mon … 4=Fri
            _h, _m = _et_now.hour, _et_now.minute
            _mins = _h * 60 + _m
            if _wday > 4 or _mins < 570 or _mins > 970:   # 9:30 AM (570) – 4:10 PM (970) ET
                return
            print("[cache_warmer] pre-warming smart money scan…")
            _warm_result = scan_smart_money(DEFAULT_LEADERBOARD)
            _cache_key = tuple(sorted(DEFAULT_LEADERBOARD))
            with app._sm_cache_lock:
                app._sm_cache[_cache_key] = {"result": _warm_result, "ts": _dtw.now()}
            print(f"[cache_warmer] cached {len(_warm_result.get('leaderboard', []))} tickers")
        except Exception as _we:
            print(f"[cache_warmer] error: {_we}")

    _scheduler.add_job(
        _warm_sm_cache,
        "interval",
        minutes=15,
        id="sm_cache_warmer",
        replace_existing=True,
    )

    _scheduler.start()
    print("[scheduler] APScheduler started — scans at 9:00 AM, 9:45 AM, 3:30 PM, 4:00 PM, 4:05 PM & 4:15 PM ET + EOD unusual-calls auto-scan at 3:30 PM, 4:00 PM, 4:15 PM ET + outcomes at 4:30 PM, Mon–Fri + micro-cap pre-warm every 30 min + AI trades at 10:00 AM + AI short calls at 10:15 AM + scan cache warmer every 15 min")
except Exception as _e:
    print(f"[scheduler] Could not start scheduler: {_e}")


def _safe(v):
    """Replace NaN/Infinity with None so jsonify never emits invalid JSON."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _safe(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_safe(i) for i in v]
    return v

PORT = int(os.environ.get("STOCK_API_PORT", 5050))


@app.route("/stock-api/", methods=["GET"])
def health_root():
    return jsonify({"status": "ok"}), 200


@app.route("/stock-api/stock/analyze", methods=["GET"])
def analyze():
    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    result = analyze_ticker(ticker)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ── Daily Top-10 — DB-backed cache ───────────────────────────────────────────
import json as _json
import psycopg2 as _psycopg2

_DB_URL = os.getenv("DATABASE_URL", "")
_daily_top10_mem: dict = {"date": None, "data": None}


def _init_daily_top10_table():
    """Create the daily_top10 table if it doesn't exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS daily_top10 (
        scan_date DATE PRIMARY KEY,
        payload   JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[daily_top10] init table error: {e}")


def _load_top10_from_db(today: str):
    """Load today's (or latest available) top10 from DB."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM daily_top10 WHERE scan_date = %s", (today,))
            row = cur.fetchone()
            if row:
                return row[0]
            # Fallback: most recent row from any date
            cur.execute("SELECT payload FROM daily_top10 ORDER BY scan_date DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                payload = row[0]
                payload["stale"] = True
                return payload
    except Exception as e:
        print(f"[daily_top10] db load error: {e}")
    return None


def _save_top10_to_db(today: str, payload: dict):
    """Persist today's top10 to DB."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO daily_top10 (scan_date, payload) VALUES (%s, %s) ON CONFLICT (scan_date) DO UPDATE SET payload = EXCLUDED.payload, created_at = NOW()",
                (today, _json.dumps(payload))
            )
            conn.commit()
    except Exception as e:
        print(f"[daily_top10] db save error: {e}")


def _compute_daily_top10():
    """Return today's top 10. Checks memory → DB → fresh scan."""
    from datetime import date as _date
    today = str(_date.today())

    # 1. In-memory cache (fastest)
    if _daily_top10_mem["date"] == today and _daily_top10_mem["data"]:
        return _daily_top10_mem["data"]

    # 2. DB cache (survives server restarts)
    db_payload = _load_top10_from_db(today)
    if db_payload and not db_payload.get("stale") and db_payload.get("top10"):
        _daily_top10_mem["date"] = today
        _daily_top10_mem["data"] = db_payload
        return db_payload

    # 3. Fresh scan — clear yfinance cache first to fix crumb issues
    try:
        import yfinance as yf
        try:
            yf.utils.get_crumb(reuse_session=False)
        except Exception:
            pass
    except Exception:
        pass

    results = scan_tickers(DEFAULT_LEADERBOARD)
    scored  = [r for r in results if not r.get("error") and r.get("score") is not None and (r.get("score") or 0) >= 8]
    scored.sort(key=lambda r: r["score"], reverse=True)
    top10 = scored[:15]
    for i, r in enumerate(top10):
        r["rank"] = i + 1

    if not top10 and db_payload and db_payload.get("top10"):
        # Scan failed entirely — serve stale DB data rather than empty list
        print("[daily_top10] scan returned empty, serving stale DB data")
        return db_payload

    payload = {"top10": top10, "date": today, "total_scanned": len(DEFAULT_LEADERBOARD)}
    _daily_top10_mem["date"] = today
    _daily_top10_mem["data"] = payload
    _save_top10_to_db(today, payload)
    return payload


_init_daily_top10_table()


# ── Whale Blocks persistent table ─────────────────────────────────────────────
def _init_whale_blocks_table():
    sql = """
    CREATE TABLE IF NOT EXISTS whale_blocks (
        id          SERIAL PRIMARY KEY,
        ticker      TEXT NOT NULL,
        direction   TEXT NOT NULL,
        strike      NUMERIC NOT NULL,
        expiry      TEXT NOT NULL,
        days_out    INTEGER,
        prem_m      NUMERIC NOT NULL,
        volume      INTEGER,
        otm_pct     NUMERIC,
        category    TEXT,
        tier        TEXT,
        price       NUMERIC,
        first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (ticker, direction, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[whale_blocks] init table error: {e}")


def _save_whale_blocks_to_db(blocks: list):
    if not blocks:
        return
    sql = """
    INSERT INTO whale_blocks (ticker, direction, strike, expiry, days_out, prem_m, volume, otm_pct, category, tier, price)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker, direction, strike, expiry) DO UPDATE
        SET days_out   = EXCLUDED.days_out,
            prem_m     = EXCLUDED.prem_m,
            volume     = EXCLUDED.volume,
            otm_pct    = EXCLUDED.otm_pct,
            category   = EXCLUDED.category,
            tier       = EXCLUDED.tier,
            price      = EXCLUDED.price;
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for b in blocks:
                cur.execute(sql, (
                    b["ticker"], b["direction"], b["strike"], b["expiry"],
                    b["days_out"], b["prem_m"], b["volume"], b["otm_pct"],
                    b["category"], b["tier"], b["price"]
                ))
            conn.commit()
        print(f"[whale_blocks] saved {len(blocks)} blocks to DB")
    except Exception as e:
        print(f"[whale_blocks] save error: {e}")


_init_whale_blocks_table()


def _init_unusual_calls_log_table():
    sql = """
    CREATE TABLE IF NOT EXISTS unusual_calls_log (
        id          SERIAL PRIMARY KEY,
        ticker      TEXT NOT NULL,
        price       NUMERIC NOT NULL,
        strike      NUMERIC NOT NULL,
        expiry      TEXT NOT NULL,
        days_out    INTEGER,
        volume      INTEGER,
        oi          INTEGER,
        vol_oi      NUMERIC,
        prem        BIGINT,
        otm_pct     NUMERIC,
        iv          NUMERIC,
        urgency     TEXT,
        first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (ticker, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[unusual_calls_log] init table error: {e}")


def _save_unusual_calls_to_db(hits: list):
    if not hits:
        return
    sql = """
    INSERT INTO unusual_calls_log (ticker, price, strike, expiry, days_out, volume, oi, vol_oi, prem, otm_pct, iv, urgency)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker, strike, expiry) DO UPDATE
        SET price    = EXCLUDED.price,
            days_out = EXCLUDED.days_out,
            volume   = EXCLUDED.volume,
            oi       = EXCLUDED.oi,
            vol_oi   = EXCLUDED.vol_oi,
            prem     = EXCLUDED.prem,
            otm_pct  = EXCLUDED.otm_pct,
            iv       = EXCLUDED.iv,
            urgency  = EXCLUDED.urgency,
            last_seen = NOW();
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for h in hits:
                cur.execute(sql, (
                    h["ticker"], h["price"], h["strike"], h["expiry"],
                    h["days_out"], h["volume"], h["oi"], h["vol_oi"],
                    h["prem"], h["otm_pct"], h["iv"], h["urgency"]
                ))
            conn.commit()
        print(f"[unusual_calls_log] saved {len(hits)} signals to DB")
    except Exception as e:
        print(f"[unusual_calls_log] save error: {e}")


_init_unusual_calls_log_table()


# ── Micro-cap unusual call options scan ───────────────────────────────────────

def _init_microcap_calls_table():
    sql = """
    CREATE TABLE IF NOT EXISTS unusual_calls_microcap_log (
        id          SERIAL PRIMARY KEY,
        ticker      TEXT NOT NULL,
        price       NUMERIC NOT NULL,
        strike      NUMERIC NOT NULL,
        expiry      TEXT NOT NULL,
        days_out    INTEGER,
        volume      INTEGER,
        oi          INTEGER,
        vol_oi      NUMERIC,
        prem        BIGINT,
        otm_pct     NUMERIC,
        iv          NUMERIC,
        urgency     TEXT,
        cap_tier    TEXT,
        first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (ticker, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[microcap_calls] init table error: {e}")


def _save_microcap_calls_to_db(hits: list):
    if not hits:
        return
    sql = """
    INSERT INTO unusual_calls_microcap_log (ticker, price, strike, expiry, days_out, volume, oi, vol_oi, prem, otm_pct, iv, urgency, cap_tier)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker, strike, expiry) DO UPDATE
        SET price     = EXCLUDED.price,
            days_out  = EXCLUDED.days_out,
            volume    = EXCLUDED.volume,
            oi        = EXCLUDED.oi,
            vol_oi    = EXCLUDED.vol_oi,
            prem      = EXCLUDED.prem,
            otm_pct   = EXCLUDED.otm_pct,
            iv        = EXCLUDED.iv,
            urgency   = EXCLUDED.urgency,
            cap_tier  = EXCLUDED.cap_tier,
            last_seen = NOW();
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for h in hits:
                cur.execute(sql, (
                    h["ticker"], h["price"], h["strike"], h["expiry"],
                    h["days_out"], h["volume"], h["oi"], h["vol_oi"],
                    h["prem"], h["otm_pct"], h["iv"], h["urgency"],
                    h.get("cap_tier", "micro"),
                ))
            conn.commit()
        print(f"[microcap_calls] saved {len(hits)} signals to DB")
    except Exception as e:
        print(f"[microcap_calls] save error: {e}")


def _run_microcap_options_scan() -> list:
    """Scan micro/small-cap tickers for unusual call option activity.
    Uses lower thresholds than large-cap to match the smaller size of these stocks.
    Only processes tickers that actually have options data on Yahoo Finance.
    """
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor, as_completed as _asc
    from datetime import datetime as _dt2

    tickers = _get_microcap_tickers()

    def _scan_one(ticker):
        hits = []
        try:
            tk    = yf.Ticker(ticker)
            price = 0.0
            try:
                price = float(tk.fast_info.get("lastPrice") or tk.fast_info.get("regularMarketPrice") or 0)
            except Exception:
                pass
            if price <= 0:
                return hits

            cap_tier = "micro"
            try:
                mc = tk.fast_info.market_cap
                if mc:
                    if mc < 50_000_000:      cap_tier = "nano"
                    elif mc < 300_000_000:   cap_tier = "micro"
                    elif mc < 2_000_000_000: cap_tier = "small"
                    else:                    cap_tier = "mid"
            except Exception:
                pass

            min_voi  = 2.0
            min_prem = 20_000 if cap_tier in ("nano", "micro") else 60_000
            min_vol  = 25
            max_exp  = 90

            opts = tk.options
            if not opts:
                return hits

            for exp in opts:
                days = (_dt2.strptime(exp, "%Y-%m-%d") - _dt2.now()).days + 1
                if not (1 <= days <= max_exp):
                    continue
                try:
                    chain = tk.option_chain(exp).calls
                    for _, row in chain.iterrows():
                        try:
                            vol = int(row.get("volume") or 0)
                            oi  = int(row.get("openInterest") or 0)
                            if oi < 5 or vol < min_vol:
                                continue
                            voi = vol / oi
                            if voi < min_voi:
                                continue
                            strike  = float(row["strike"])
                            otm_pct = round((strike - price) / price * 100, 2)
                            if otm_pct < -10 or otm_pct > 50:
                                continue
                            bid  = float(row.get("bid") or 0)
                            ask  = float(row.get("ask") or 0)
                            mid  = (bid + ask) / 2 if bid and ask else float(row.get("lastPrice") or 0)
                            prem = int(mid * vol * 100)
                            if prem < min_prem:
                                continue
                            iv      = round(float(row.get("impliedVolatility") or 0) * 100, 1)
                            urgency = ("EXPIRING" if days <= 3 else
                                       "SHORT"    if days <= 7 else
                                       "NEAR"     if days <= 14 else "MEDIUM")
                            hits.append({
                                "ticker": ticker, "price": price, "strike": strike,
                                "expiry": exp, "days_out": days, "volume": vol, "oi": oi,
                                "vol_oi": round(voi, 2), "prem": prem, "otm_pct": otm_pct,
                                "iv": iv, "urgency": urgency, "cap_tier": cap_tier,
                            })
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
        return hits

    all_hits = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_scan_one, t): t for t in tickers}
        for fut in _asc(futs):
            all_hits.extend(fut.result() or [])

    all_hits.sort(key=lambda x: x["prem"], reverse=True)
    return all_hits


_init_microcap_calls_table()


# ── My Trades — personal trade journal ───────────────────────────────────────

def _init_my_trades_table():
    sql = """
    CREATE TABLE IF NOT EXISTS my_trades (
        id                  SERIAL PRIMARY KEY,
        ticker              TEXT NOT NULL,
        strike              NUMERIC NOT NULL,
        expiry              TEXT NOT NULL,
        vol_oi              NUMERIC,
        prem                BIGINT,
        otm_pct             NUMERIC,
        urgency             TEXT,
        signal_detected_at  TIMESTAMPTZ,
        saved_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        entry_price         NUMERIC,
        exit_price          NUMERIC,
        contracts           INTEGER DEFAULT 1,
        notes               TEXT,
        status              TEXT DEFAULT 'open',
        UNIQUE (ticker, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[my_trades] init table error: {e}")

_init_my_trades_table()


@app.route("/stock-api/my-trades", methods=["GET"])
def get_my_trades():
    """Return all saved trades, newest first."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, strike::float, expiry, vol_oi::float, prem::bigint,
                       otm_pct::float, urgency,
                       signal_detected_at AT TIME ZONE 'UTC' AS signal_detected_at,
                       saved_at AT TIME ZONE 'UTC' AS saved_at,
                       entry_price::float, exit_price::float,
                       contracts, notes, status
                FROM my_trades
                ORDER BY saved_at DESC
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                for f in ("signal_detected_at", "saved_at"):
                    if r.get(f): r[f] = r[f].isoformat()
        return jsonify({"trades": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "trades": [], "total": 0}), 500


@app.route("/stock-api/my-trades", methods=["POST"])
def save_my_trade():
    """Save a signal as a personal trade. Idempotent on (ticker, strike, expiry)."""
    body = request.get_json(silent=True) or {}
    ticker  = str(body.get("ticker", "")).upper().strip()
    strike  = body.get("strike")
    expiry  = body.get("expiry", "")
    if not ticker or strike is None or not expiry:
        return jsonify({"error": "ticker, strike, expiry required"}), 400
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO my_trades (ticker, strike, expiry, vol_oi, prem, otm_pct, urgency, signal_detected_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, strike, expiry) DO NOTHING
                RETURNING id
            """, (
                ticker, strike, expiry,
                body.get("vol_oi"), body.get("prem"), body.get("otm_pct"),
                body.get("urgency"), body.get("signal_detected_at"),
            ))
            row = cur.fetchone()
            conn.commit()
        return jsonify({"ok": True, "created": row is not None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/my-trades/<int:trade_id>", methods=["PATCH"])
def update_my_trade(trade_id):
    """Update entry/exit prices, contracts, notes, and status."""
    body = request.get_json(silent=True) or {}
    allowed = ["entry_price", "exit_price", "contracts", "notes", "status"]
    sets, vals = [], []
    for k in allowed:
        if k in body:
            sets.append(f"{k} = %s")
            vals.append(body[k])
    if not sets:
        return jsonify({"error": "nothing to update"}), 400
    vals.append(trade_id)
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE my_trades SET {', '.join(sets)} WHERE id = %s", vals)
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/my-trades/<int:trade_id>", methods=["DELETE"])
def delete_my_trade(trade_id):
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM my_trades WHERE id = %s", (trade_id,))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── AI Trade Log — DB-backed track record ────────────────────────────────────

def _init_ai_trade_log_table():
    create_sql = """
    CREATE TABLE IF NOT EXISTS ai_trade_log (
        id              SERIAL PRIMARY KEY,
        trade_date      DATE NOT NULL,
        ticker          TEXT NOT NULL,
        direction       TEXT NOT NULL,
        setup_type      TEXT,
        conviction      TEXT,
        price_at_signal FLOAT,
        entry_strike    FLOAT,
        expiry          TEXT,
        target_price    FLOAT,
        stop_loss       FLOAT,
        signals_aligned JSONB,
        thesis          TEXT,
        risk_level      TEXT,
        t1_price        FLOAT,
        t3_price        FLOAT,
        t5_price        FLOAT,
        t10_price       FLOAT,
        t1_pct          FLOAT,
        t3_pct          FLOAT,
        t5_pct          FLOAT,
        t10_pct         FLOAT,
        t1_win          BOOL,
        t3_win          BOOL,
        t5_win          BOOL,
        t10_win         BOOL,
        expiry_price    FLOAT,
        expiry_pct      FLOAT,
        expiry_win      BOOL,
        outcome         TEXT NOT NULL DEFAULT 'OPEN',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(trade_date, ticker, direction)
    );
    """
    migrate_sql = [
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS expiry_price    FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS expiry_pct      FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS expiry_win      BOOL",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS source          TEXT DEFAULT 'AI_TRADE'",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS option_premium    FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS breakeven_price  FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS total_premium_usd FLOAT",
    ]
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(create_sql)
            for m in migrate_sql:
                try:
                    cur.execute(m)
                except Exception:
                    pass
            conn.commit()
    except Exception as e:
        print(f"[ai_trade_log] init table error: {e}")


def _fetch_options_flow_usd(ticker: str, strike: float, expiry_str: str) -> float | None:
    """Fetch real options market dollar flow for a given ticker/strike/expiry.
    Returns volume * lastPrice * 100 (total $ spent today), or open_interest * lastPrice * 100
    if volume is zero. Returns None on any error."""
    import sys
    try:
        import yfinance as _yf
        tk = _yf.Ticker(ticker)
        available = tk.options  # tuple of expiry date strings
        if not available:
            print(f"[options_flow] {ticker}: no option dates available", file=sys.stderr)
            return None
        # Find nearest available expiry to the AI-generated date
        from datetime import datetime as _dtt
        target = _dtt.strptime(expiry_str, "%Y-%m-%d").date()
        best = min(available, key=lambda d: abs((_dtt.strptime(d, "%Y-%m-%d").date() - target).days))
        chain = tk.option_chain(best)
        calls = chain.calls
        if calls.empty:
            print(f"[options_flow] {ticker}: empty calls chain for {best}", file=sys.stderr)
            return None
        # Find nearest strike
        idx = (calls["strike"] - float(strike)).abs().argsort().iloc[0]
        row = calls.iloc[idx]
        last_price = float(row.get("lastPrice") or 0)
        volume     = int(row.get("volume") or 0)
        oi         = int(row.get("openInterest") or 0)
        qty = volume if volume > 0 else oi
        if qty <= 0 or last_price <= 0:
            print(f"[options_flow] {ticker} strike={strike} {best}: vol={volume} oi={oi} last={last_price} — no flow", file=sys.stderr)
            return None
        result = round(qty * last_price * 100, 2)
        print(f"[options_flow] {ticker} strike={strike} {best}: vol={volume} oi={oi} last={last_price} → ${result:,.0f}", file=sys.stderr)
        return result
    except Exception as e:
        print(f"[options_flow] {ticker} error: {e}", file=sys.stderr)
        return None


def _save_ai_trades_to_log(trades: list, trade_date: str):
    """Persist today's AI trade picks. Skips if already logged for this date."""
    if not trades:
        return
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for t in trades:
                opt_prem   = t.get("option_premium")
                strike     = t.get("entry_strike")
                expiry     = t.get("expiry")
                breakeven  = round(strike + opt_prem, 2) if (strike and opt_prem) else None
                total_prem = _fetch_options_flow_usd(t.get("ticker",""), strike, expiry) if (strike and expiry) else None
                cur.execute("""
                    INSERT INTO ai_trade_log
                        (trade_date, ticker, direction, setup_type, conviction,
                         price_at_signal, entry_strike, expiry, target_price, stop_loss,
                         signals_aligned, thesis, risk_level,
                         option_premium, breakeven_price, total_premium_usd)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (trade_date, ticker, direction) DO NOTHING
                """, (
                    trade_date,
                    t.get("ticker"),
                    t.get("direction"),
                    t.get("setup_type"),
                    t.get("conviction"),
                    t.get("price"),
                    strike,
                    expiry,
                    t.get("target_price"),
                    t.get("stop_loss"),
                    _json.dumps(t.get("signals_aligned", [])),
                    t.get("thesis"),
                    t.get("risk_level"),
                    opt_prem,
                    breakeven,
                    total_prem,
                ))
            conn.commit()
        print(f"[ai_trade_log] saved {len(trades)} trades for {trade_date}")
    except Exception as e:
        print(f"[ai_trade_log] save error: {e}")


def _parse_expiry_date(expiry_str, trade_date):
    """Parse an options expiry string into a date. Returns None if unparseable."""
    if not expiry_str:
        return None
    from datetime import date as _d, timedelta as _td
    import re as _re
    s = expiry_str.strip()
    # Try common explicit formats
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            pass
    # "Jan 17" or "January 17" without year — assume nearest future occurrence
    for fmt in ("%b %d", "%B %d"):
        try:
            parsed = _dt.strptime(s, fmt)
            year = trade_date.year
            d = parsed.replace(year=year).date()
            if d < trade_date:
                d = d.replace(year=year + 1)
            return d
        except ValueError:
            pass
    # Relative keywords
    s_lower = s.lower()
    if "weekly" in s_lower or "0dte" in s_lower:
        # next Friday from trade_date
        days_ahead = (4 - trade_date.weekday()) % 7 or 7
        return trade_date + _td(days=days_ahead)
    if "monthly" in s_lower:
        return trade_date + _td(days=30)
    # Try to extract a date-like substring
    m = _re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", s)
    if m:
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y"):
            try:
                return _dt.strptime(m.group(1), fmt).date()
            except ValueError:
                pass
    return None


def _update_ai_trade_outcomes():
    """Fetch closing prices for open AI trade log entries and mark win/loss at expiry."""
    import yfinance as _yf
    from datetime import date as _date2, timedelta as _td2
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, trade_date, price_at_signal, direction, target_price, stop_loss,
                       expiry, expiry_price,
                       t1_price, t3_price, t5_price, t10_price,
                       breakeven_price, entry_strike
                FROM ai_trade_log
                WHERE outcome = 'OPEN'
                ORDER BY trade_date DESC
                LIMIT 100
            """)
            rows = cur.fetchall()
            if not rows:
                return
            today = _date2.today()

            def _fetch_close(ticker, target_dt):
                """Fetch the first available closing price on or after target_dt."""
                try:
                    hist = _yf.Ticker(ticker).history(
                        start=str(target_dt),
                        end=str(target_dt + _td2(days=7))
                    )["Close"]
                    if hist.empty:
                        return None
                    return float(hist.iloc[0])
                except Exception:
                    return None

            def _win(close_price, pct, direction, breakeven=None, strike=None):
                """
                For LONG CALLs: WIN = stock closed above the break-even price.
                Break-even = entry_strike + option_premium.
                If breakeven not stored, fall back to a 2% stock-move threshold.
                """
                if close_price is None or pct is None:
                    return None
                if direction == "BULLISH":
                    if breakeven and breakeven > 0:
                        return close_price >= breakeven
                    return pct >= 2.0   # fallback: stock up ≥2%
                elif direction == "BEARISH":
                    return pct <= -2.0
                else:
                    return abs(pct) < 3.0

            for row in rows:
                id_, ticker, trade_date, p0, direction, target, stoploss, expiry_str, exp_p, t1p, t3p, t5p, t10p, bkeven, strike = row
                updates = {}

                # ── Expiry date outcome (primary) ────────────────────────────
                expiry_date = _parse_expiry_date(expiry_str, trade_date)
                if expiry_date and expiry_date <= today and exp_p is None:
                    close = _fetch_close(ticker, expiry_date)
                    if close is not None and p0:
                        pct = round((close - p0) / p0 * 100, 2)
                        updates["expiry_price"] = close
                        updates["expiry_pct"]   = pct
                        updates["expiry_win"]   = _win(close, pct, direction, bkeven, strike)

                # ── Fixed T+n checkpoints (supplemental context) ─────────────
                for n, col_p, col_pct, col_win, existing in [
                    (1,  "t1_price",  "t1_pct",  "t1_win",  t1p),
                    (3,  "t3_price",  "t3_pct",  "t3_win",  t3p),
                    (5,  "t5_price",  "t5_pct",  "t5_win",  t5p),
                    (10, "t10_price", "t10_pct", "t10_win", t10p),
                ]:
                    if existing is not None:
                        continue
                    target_dt = trade_date + _td2(days=n)
                    if target_dt > today:
                        continue
                    close = _fetch_close(ticker, target_dt)
                    if close is not None and p0:
                        pct = round((close - p0) / p0 * 100, 2)
                        updates[col_p]   = close
                        updates[col_pct] = pct
                        updates[col_win] = _win(close, pct, direction, bkeven, strike)

                if updates:
                    # Primary outcome = expiry result if available, else T+5
                    exp_win_val = updates.get("expiry_win") if "expiry_win" in updates else None
                    t5_close    = updates.get("t5_price")
                    t5_pct_val  = updates.get("t5_pct")
                    outcome = "OPEN"
                    if exp_win_val is not None:
                        outcome = "WIN" if exp_win_val else "LOSS"
                    elif t5_pct_val is not None:
                        outcome = "WIN" if _win(t5_close, t5_pct_val, direction, bkeven, strike) else "LOSS"
                    updates["outcome"] = outcome
                    set_sql = ", ".join(f"{k} = %s" for k in updates)
                    cur.execute(f"UPDATE ai_trade_log SET {set_sql} WHERE id = %s",
                                list(updates.values()) + [id_])
            conn.commit()
        print(f"[ai_trade_log] outcomes updated for {len(rows)} trades")
    except Exception as e:
        print(f"[ai_trade_log] update_outcomes error: {e}")


_init_ai_trade_log_table()


# ── AI SHORT CALLS LOG ──────────────────────────────────────────────────────

def _init_ai_short_calls_log_table():
    """Create ai_short_calls_log table for daily short-call pick history."""
    sql = """
    CREATE TABLE IF NOT EXISTS ai_short_calls_log (
        id                SERIAL PRIMARY KEY,
        trade_date        DATE NOT NULL,
        rank              INT,
        ticker            TEXT NOT NULL,
        strike            FLOAT,
        expiry            TEXT,
        days_out          INT,
        vol_oi            FLOAT,
        prem              BIGINT,
        stock_price       FLOAT,
        otm_pct           FLOAT,
        breakeven         FLOAT,
        conviction        TEXT,
        urgency           TEXT,
        thesis            TEXT,
        why_it_stands_out TEXT,
        outcome           TEXT NOT NULL DEFAULT 'OPEN',
        t1_price          FLOAT,
        t3_price          FLOAT,
        t5_price          FLOAT,
        t1_pct            FLOAT,
        t3_pct            FLOAT,
        t5_pct            FLOAT,
        t1_win            BOOL,
        t3_win            BOOL,
        t5_win            BOOL,
        expiry_price      FLOAT,
        expiry_pct        FLOAT,
        expiry_win        BOOL,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(trade_date, ticker, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[ai_short_calls_log] init error: {e}")


def _save_ai_short_calls_to_log(picks: list, trade_date: str):
    """Persist today's AI short-call picks. Skips rows already logged."""
    if not picks:
        return
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for i, p in enumerate(picks):
                cur.execute("""
                    INSERT INTO ai_short_calls_log
                        (trade_date, rank, ticker, strike, expiry, days_out, vol_oi,
                         prem, stock_price, otm_pct, breakeven, conviction, urgency,
                         thesis, why_it_stands_out)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (trade_date, ticker, strike, expiry) DO NOTHING
                """, (
                    trade_date,
                    i + 1,
                    p.get("ticker"),
                    p.get("strike"),
                    p.get("expiry"),
                    p.get("days_out"),
                    p.get("vol_oi"),
                    int(p.get("prem") or 0),
                    p.get("stock_price"),
                    p.get("otm_pct"),
                    p.get("breakeven"),
                    p.get("conviction"),
                    p.get("urgency"),
                    p.get("thesis"),
                    p.get("why_it_stands_out"),
                ))
            conn.commit()
        print(f"[ai_short_calls_log] saved {len(picks)} picks for {trade_date}")
    except Exception as e:
        print(f"[ai_short_calls_log] save error: {e}")


def _update_ai_short_call_outcomes():
    """Fetch closing prices for open short-call log entries and mark win/loss."""
    import yfinance as _yf
    from datetime import date as _date3, timedelta as _td3
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, trade_date, stock_price, strike, breakeven, expiry,
                       t1_price, t3_price, t5_price, expiry_price
                FROM ai_short_calls_log
                WHERE outcome = 'OPEN'
                ORDER BY trade_date DESC
                LIMIT 100
            """)
            rows = cur.fetchall()
            if not rows:
                return
            today = _date3.today()

            def _fetch_close(ticker, target_dt):
                try:
                    hist = _yf.Ticker(ticker).history(
                        start=str(target_dt),
                        end=str(target_dt + _td3(days=7))
                    )["Close"]
                    return float(hist.iloc[0]) if not hist.empty else None
                except Exception:
                    return None

            def _scwin(close, p0, bkeven):
                """WIN = stock closed above breakeven. Fallback: up >=2%."""
                if close is None or p0 is None:
                    return None
                if bkeven and bkeven > 0:
                    return close >= bkeven
                return (close - p0) / p0 * 100 >= 2.0

            for row in rows:
                id_, ticker, trade_date, p0, strike, bkeven, expiry_str, t1p, t3p, t5p, exp_p = row
                updates = {}

                # Expiry outcome
                exp_date = _parse_expiry_date(expiry_str, trade_date)
                if exp_date and exp_date <= today and exp_p is None:
                    close = _fetch_close(ticker, exp_date)
                    if close is not None and p0:
                        pct = round((close - p0) / p0 * 100, 2)
                        updates["expiry_price"] = close
                        updates["expiry_pct"]   = pct
                        updates["expiry_win"]   = _scwin(close, p0, bkeven)

                # T+1, T+3, T+5
                for n, col_p, col_pct, col_win, existing in [
                    (1, "t1_price", "t1_pct", "t1_win", t1p),
                    (3, "t3_price", "t3_pct", "t3_win", t3p),
                    (5, "t5_price", "t5_pct", "t5_win", t5p),
                ]:
                    if existing is not None:
                        continue
                    target_dt = trade_date + _td3(days=n)
                    if target_dt > today:
                        continue
                    close = _fetch_close(ticker, target_dt)
                    if close is not None and p0:
                        pct = round((close - p0) / p0 * 100, 2)
                        updates[col_p]   = close
                        updates[col_pct] = pct
                        updates[col_win] = _scwin(close, p0, bkeven)

                if updates:
                    exp_win_val = updates.get("expiry_win")
                    t5_pct_val  = updates.get("t5_pct")
                    t5_close    = updates.get("t5_price")
                    outcome = "OPEN"
                    if exp_win_val is not None:
                        outcome = "WIN" if exp_win_val else "LOSS"
                    elif t5_pct_val is not None:
                        outcome = "WIN" if _scwin(t5_close, p0, bkeven) else "LOSS"
                    updates["outcome"] = outcome
                    set_sql = ", ".join(f"{k} = %s" for k in updates)
                    cur.execute(f"UPDATE ai_short_calls_log SET {set_sql} WHERE id = %s",
                                list(updates.values()) + [id_])
            conn.commit()
        print(f"[ai_short_calls_log] outcomes updated for {len(rows)} entries")
    except Exception as e:
        print(f"[ai_short_calls_log] update_outcomes error: {e}")


_init_ai_short_calls_log_table()


def _init_signal_history_table():
    """Create signal_history table for multi-day persistence tracking."""
    create_sql = """
    CREATE TABLE IF NOT EXISTS signal_history (
        id           SERIAL PRIMARY KEY,
        ticker       TEXT NOT NULL,
        signal_date  DATE NOT NULL,
        comp_score   FLOAT,
        smart_cp     FLOAT,
        call_verdict TEXT,
        dp_prem_m    FLOAT,
        iv_rank      FLOAT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(ticker, signal_date)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(create_sql)
            conn.commit()
    except Exception as e:
        print(f"[signal_history] init table error: {e}")

_init_signal_history_table()


def _init_daily_vol_snapshots_table():
    """Create daily_vol_snapshots table for IV skew & short interest percentile tracking."""
    try:
        import psycopg2 as _pg2_dvs
        with _pg2_dvs.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_vol_snapshots (
                    id            SERIAL PRIMARY KEY,
                    ticker        TEXT NOT NULL,
                    snap_date     DATE NOT NULL,
                    iv_skew       FLOAT,
                    short_float   FLOAT,
                    pc_oi_ratio   FLOAT,
                    pc_prem_ratio FLOAT,
                    rs_vs_spy     FLOAT,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(ticker, snap_date)
                );
            """)
            conn.commit()
    except Exception as e:
        print(f"[daily_vol_snapshots] init error: {e}")

_init_daily_vol_snapshots_table()


# Module-level SPY 1y cache — avoids rate-limit collisions during concurrent ticker fetches
_spy_1y_cache: dict = {"return_pct": None, "rets_arr": None, "date": None}

def _refresh_spy_1y_cache():
    """Fetch SPY 1-year history once; cache return % and daily returns array."""
    from datetime import date as _spy_d
    try:
        import yfinance as _yf_spy
        _raw = _yf_spy.download("SPY", period="1y", interval="1d", progress=False, auto_adjust=True)["Close"]
        _h = _raw.iloc[:, 0] if hasattr(_raw, "columns") else _raw
        _c = _h.dropna()
        if len(_c) >= 50:
            _spy_1y_cache["return_pct"] = round((float(_c.iloc[-1]) / float(_c.iloc[0]) - 1) * 100, 1)
            _spy_1y_cache["rets_arr"] = _c.pct_change().dropna().values
            _spy_1y_cache["date"] = _spy_d.today()
            print(f"[spy_cache] 1y return={_spy_1y_cache['return_pct']}%, {len(_c)} rows")
    except Exception as _e:
        print(f"[spy_cache] refresh error: {_e}")

_refresh_spy_1y_cache()


def _save_daily_vol_snapshot():
    """Store today's vol signals for IV skew + short interest percentile tracking."""
    from datetime import date as _dvs_date
    import psycopg2 as _pg2_snap
    today = _dvs_date.today()
    vc = getattr(app, "_vc_cache", None)
    if not vc:
        print("[daily_vol_snapshots] no vol-crush cache — skipping")
        return
    try:
        with _pg2_snap.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            for r in vc.get("results", []):
                cur.execute("""
                    INSERT INTO daily_vol_snapshots
                        (ticker, snap_date, iv_skew, short_float, pc_oi_ratio, pc_prem_ratio, rs_vs_spy)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, snap_date) DO UPDATE SET
                        iv_skew=EXCLUDED.iv_skew, short_float=EXCLUDED.short_float,
                        pc_oi_ratio=EXCLUDED.pc_oi_ratio, pc_prem_ratio=EXCLUDED.pc_prem_ratio,
                        rs_vs_spy=EXCLUDED.rs_vs_spy
                """, (
                    r["ticker"], today,
                    r.get("iv_skew"), r.get("short_float_pct"),
                    r.get("put_call_oi_ratio"), r.get("pc_premium_ratio"),
                    r.get("rs_vs_spy"),
                ))
            conn.commit()
        print(f"[daily_vol_snapshots] saved {len(vc.get('results', []))} rows for {today}")
    except Exception as e:
        print(f"[daily_vol_snapshots] save error: {e}")


def _save_signal_snapshot():
    """Snapshot today's composite + call-intent + darkpool signals for persistence tracking."""
    from datetime import date as _snap_date
    today = _snap_date.today()
    cs = getattr(app, "_cs_cache", None)
    ci = getattr(app, "_ci_cache", None)
    dp = getattr(app, "_dp_cache", None)
    if not cs:
        print("[signal_history] no composite cache — skipping snapshot")
        return
    ci_map = {r["ticker"]: r for r in (ci or {}).get("results", [])}
    dp_map = {r["ticker"]: r for r in (dp or {}).get("results", [])}
    rows_to_insert = []
    for r in cs.get("results", []):
        t = r["ticker"]
        comp = r.get("components", {})
        rows_to_insert.append((
            t, today,
            r.get("score"),
            comp.get("smart_cp"),
            ci_map.get(t, {}).get("verdict"),
            dp_map.get(t, {}).get("premium_m"),
            comp.get("iv_rank"),
        ))
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for row in rows_to_insert:
                cur.execute("""
                    INSERT INTO signal_history
                        (ticker, signal_date, comp_score, smart_cp, call_verdict, dp_prem_m, iv_rank)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, signal_date) DO UPDATE SET
                        comp_score=EXCLUDED.comp_score, smart_cp=EXCLUDED.smart_cp,
                        call_verdict=EXCLUDED.call_verdict, dp_prem_m=EXCLUDED.dp_prem_m,
                        iv_rank=EXCLUDED.iv_rank
                """, row)
            conn.commit()
        print(f"[signal_history] saved {len(rows_to_insert)} rows for {today}")
    except Exception as e:
        print(f"[signal_history] save error: {e}")


@app.route("/stock-api/daily-top10", methods=["GET"])
def daily_top10_route():
    """Return today's top 10 highest-scoring stocks. DB-backed, survives restarts."""
    try:
        data = _compute_daily_top10()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/stock/scan", methods=["POST"])
def scan():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", WATCHLIST_DEFAULT[:10])
    if not isinstance(tickers, list) or len(tickers) == 0:
        tickers = WATCHLIST_DEFAULT[:10]
    tickers = [t.strip().upper() for t in tickers[:20]]
    results = scan_tickers(tickers)
    return jsonify({"results": results})


@app.route("/stock-api/stock/watchlist", methods=["GET"])
def default_watchlist():
    return jsonify({"tickers": WATCHLIST_DEFAULT})


@app.route("/stock-api/portfolio", methods=["GET"])
def portfolio():
    p = get_portfolio()
    tickers = [pos["ticker"] for pos in p.get("positions", [])]
    prices = {}
    if tickers:
        try:
            import yfinance as yf
            data = yf.download(tickers, period="1d", progress=False, auto_adjust=True)
            if not data.empty:
                if hasattr(data.columns, "levels"):
                    for t in tickers:
                        try:
                            prices[t] = float(data["Close"][t].dropna().iloc[-1])
                        except Exception:
                            pass
                else:
                    try:
                        prices[tickers[0]] = float(data["Close"].dropna().iloc[-1])
                    except Exception:
                        pass
        except Exception:
            pass
    result = get_portfolio_value(prices)
    return jsonify(result)


@app.route("/stock-api/portfolio/buy", methods=["POST"])
def buy():
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker", "").strip().upper()
    shares = float(body.get("shares", 0))
    price = float(body.get("price", 0))
    if not ticker or shares <= 0 or price <= 0:
        return jsonify({"error": "ticker, shares, and price are required"}), 400
    result = add_position(ticker, shares, price)
    return jsonify(result)


@app.route("/stock-api/portfolio/sell", methods=["POST"])
def sell():
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker", "").strip().upper()
    shares = float(body.get("shares", 0))
    price = float(body.get("price", 0))
    if not ticker or shares <= 0 or price <= 0:
        return jsonify({"error": "ticker, shares, and price are required"}), 400
    result = remove_position(ticker, shares, price)
    return jsonify(result)


@app.route("/stock-api/backtest", methods=["POST"])
def backtest():
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker", "").strip().upper()
    buy_threshold = float(body.get("buy_threshold", 6.5))
    sell_threshold = float(body.get("sell_threshold", 4.5))
    initial_cash = float(body.get("initial_cash", 10000.0))
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    df = fetch_stock_data(ticker, period="2y")
    if df is None or df.empty:
        return jsonify({"error": f"Could not fetch data for {ticker}"}), 404
    result = backtest_strategy(df, buy_threshold=buy_threshold, sell_threshold=sell_threshold, initial_cash=initial_cash)
    result["ticker"] = ticker
    return jsonify(result)


@app.route("/stock-api/analytics/historical", methods=["POST"])
def historical_analytics():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", WATCHLIST_DEFAULT[:8])
    if not isinstance(tickers, list) or len(tickers) == 0:
        tickers = WATCHLIST_DEFAULT[:8]
    tickers = [t.strip().upper() for t in tickers[:15]]
    result = run_historical_analytics(tickers)
    return jsonify(result)


@app.route("/stock-api/alerts", methods=["GET"])
def list_alerts():
    return jsonify({"alerts": get_alerts()})


@app.route("/stock-api/alerts", methods=["POST"])
def create_alert():
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker", "").strip().upper()
    alert_type = body.get("type", "price")
    value = body.get("value")
    direction = body.get("direction", "above")
    if not ticker or value is None:
        return jsonify({"error": "ticker and value are required"}), 400
    if alert_type not in ("price", "rsi", "score"):
        return jsonify({"error": "type must be price, rsi, or score"}), 400
    if direction not in ("above", "below"):
        return jsonify({"error": "direction must be above or below"}), 400
    result = add_alert(ticker, alert_type, float(value), direction)
    return jsonify(result)


@app.route("/stock-api/alerts/<int:alert_id>", methods=["DELETE"])
def remove_alert(alert_id):
    result = delete_alert(alert_id)
    return jsonify(result)


@app.route("/stock-api/prop/scan", methods=["POST"])
def prop_scan():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", WATCHLIST_DEFAULT[:10])
    if not isinstance(tickers, list) or len(tickers) == 0:
        tickers = WATCHLIST_DEFAULT[:10]
    tickers = [t.strip().upper() for t in tickers[:20]]

    signals = []
    for ticker in tickers:
        try:
            df = fetch_stock_data(ticker, period="2y")
            if df is None or df.empty:
                continue
            sig = prop_signal(df)
            if sig is None:
                continue
            close_series = df["Close"].squeeze().dropna()
            if close_series.empty:
                continue
            price = float(close_series.iloc[-1])
            sig["ticker"] = ticker
            sig["price"] = round(price, 2)
            signals.append(sig)
        except Exception:
            continue

    signals.sort(key=lambda x: x["score"], reverse=True)

    positions = execution.get_positions()
    enriched_positions = {}
    for ticker, pos in positions.items():
        try:
            df = fetch_stock_data(ticker, period="5d")
            current_price = float(df["Close"].iloc[-1]) if df is not None and not df.empty else pos["entry"]
        except Exception:
            current_price = pos["entry"]
        pnl_unrealized = round((current_price - pos["entry"]) * pos["size"], 2)
        enriched_positions[ticker] = {**pos, "current_price": round(current_price, 2), "unrealized_pnl": pnl_unrealized}

    return jsonify(_safe({
        "signals": signals,
        "positions": enriched_positions,
        "cash": round(execution.get_cash(), 2),
        "realized_pnl": pnl.total_pnl(),
        "trades": pnl.get_trades()[-20:],
    }))


@app.route("/stock-api/prop/trade/<ticker>/<action>", methods=["POST"])
def prop_trade(ticker, action):
    ticker = ticker.strip().upper()
    action = action.lower()
    if action not in ("buy", "sell"):
        return jsonify({"error": "action must be buy or sell"}), 400

    df = fetch_stock_data(ticker, period="5d")
    if df is None or df.empty:
        return jsonify({"error": f"Could not fetch price for {ticker}"}), 404

    price = float(df["Close"].iloc[-1])

    if action == "buy":
        success = execution.enter_trade(ticker, price, size=10)
        if not success:
            return jsonify({"error": "Insufficient cash", "cash": execution.get_cash()}), 400
        return jsonify({"status": "ok", "action": "buy", "ticker": ticker, "price": price, "cash": execution.get_cash()})
    else:
        trade_pnl = execution.exit_trade(ticker, price)
        if trade_pnl is None:
            return jsonify({"error": f"No open position for {ticker}"}), 400
        pnl.log_trade(ticker, trade_pnl, "manual sell")
        return jsonify({"status": "ok", "action": "sell", "ticker": ticker, "price": price, "pnl": trade_pnl, "cash": execution.get_cash()})


@app.route("/stock-api/prop/reset", methods=["POST"])
def prop_reset():
    execution.reset()
    pnl.clear_trades()
    return jsonify({"status": "ok", "cash": execution.get_cash()})


@app.route("/stock-api/smart-money/scan", methods=["POST"])
def smart_money_scan_route():
    from datetime import datetime as _dts
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    force_refresh = body.get("force_refresh", False)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:50]]
    cache_key = tuple(sorted(tickers))

    # Serve from cache if fresh and not force-refreshing
    if not force_refresh:
        with app._sm_cache_lock:
            entry = app._sm_cache.get(cache_key)
        if entry:
            age = (_dts.now() - entry["ts"]).total_seconds()
            if age < _SM_CACHE_TTL_SECS:
                resp = dict(entry["result"])
                resp["cached"] = True
                resp["cache_age_secs"] = int(age)
                return jsonify(_safe(resp))

    # Live fetch — then store in cache
    result = scan_smart_money(tickers)
    result["cached"] = False
    result["cache_age_secs"] = 0
    with app._sm_cache_lock:
        app._sm_cache[cache_key] = {"result": result, "ts": _dts.now()}
    return jsonify(_safe(result))


@app.route("/stock-api/smart-money/cache-status", methods=["GET"])
def sm_cache_status():
    from datetime import datetime as _dts
    with app._sm_cache_lock:
        entries = list(app._sm_cache.items())
    status = []
    for key, val in entries:
        age = int((_dts.now() - val["ts"]).total_seconds())
        status.append({"tickers_count": len(key), "age_secs": age, "fresh": age < _SM_CACHE_TTL_SECS})
    return jsonify({"entries": status, "count": len(status)})


@app.route("/stock-api/smart-money/detail/<ticker>", methods=["GET"])
def smart_money_detail(ticker):
    ticker = ticker.strip().upper()
    df = fetch_stock_data(ticker, period="1y")
    if df is None or df.empty:
        return jsonify({"error": f"Could not fetch data for {ticker}"}), 404
    spy_ret = 0.0
    try:
        spy_df = fetch_stock_data("SPY", period="35d")
        if spy_df is not None and len(spy_df) > 21:
            sc = spy_df["Close"].squeeze().astype(float).dropna()
            spy_ret = float((sc.iloc[-1] - sc.iloc[-21]) / sc.iloc[-21])
    except Exception:
        pass
    from smart_money import fetch_options_data
    opts = fetch_options_data(ticker)
    result = compute_smart_money(ticker, df, spy_ret, opts)
    if result is None:
        return jsonify({"error": f"Insufficient data for {ticker}"}), 404
    return jsonify(_safe(result))


@app.route("/stock-api/congress/trades", methods=["GET"])
def congress_trades_route():
    force = request.args.get("refresh", "").lower() == "true"
    trades = get_congress_trades(force=force)
    return jsonify({"trades": trades, "count": len(trades)})


@app.route("/stock-api/alerts/subscribe", methods=["POST"])
def email_subscribe():
    body  = request.get_json(silent=True) or {}
    email = body.get("email", "").strip()
    result = subscribe(email)
    return jsonify(result), (200 if result["ok"] else 400)


@app.route("/stock-api/alerts/unsubscribe/<token>", methods=["GET"])
def email_unsubscribe(token):
    result = unsubscribe(token)
    if result["ok"]:
        return (
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px;"
            "background:#0f172a;color:#fff'>"
            "<h2>✅ Unsubscribed</h2>"
            "<p style='color:#94a3b8'>You've been removed from StockScanner AI daily signals.</p>"
            "</body></html>"
        ), 200
    return jsonify(result), 404


@app.route("/stock-api/alerts/count", methods=["GET"])
def email_count():
    return jsonify({
        "subscribers": subscriber_count(),
        "smtp_configured": smtp_configured(),
    })


@app.route("/stock-api/alerts/test-digest", methods=["POST"])
def test_digest():
    """Send a test digest right now. Pass ?session=morning or ?session=eod (default)."""
    session  = request.args.get("session", "eod")
    result   = scan_smart_money(DEFAULT_LEADERBOARD[:10])
    signals  = result.get("leaderboard", [])
    base_url = os.getenv("PUBLIC_URL", "")
    out      = send_daily_digest(signals, base_url, session=session)
    return jsonify(out)


@app.route("/stock-api/bull-flow/top10", methods=["POST"])
def bull_flow_top10():
    """
    Rank tickers by most bullish options activity today.
    Returns top 10 sorted by call premium (highest dollar flow first).
    Each row: ticker, price, strike, expiry, premium_m, call_put_ratio, call_vol_oi, total_call_vol
    """
    import yfinance as yf
    from smart_money import fetch_options_data
    from datetime import datetime as _dt

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:50]]

    # 5-minute cache — prevents concurrent tab-open requests from hammering yfinance
    _bf_cache = getattr(app, "_bf_cache", None)
    _bf_ts    = getattr(app, "_bf_cache_ts", None)
    _bf_key   = getattr(app, "_bf_cache_key", None)
    if (_bf_cache and _bf_ts and _bf_key == tickers
            and (_dt.now() - _bf_ts).total_seconds() < 300):
        return jsonify(_bf_cache)

    # Force a fresh Yahoo Finance crumb/session before bulk fetching
    try:
        yf.utils.get_crumb(reuse_session=False)
    except Exception:
        pass

    def _get_row(ticker):
        try:
            from datetime import date, datetime
            today      = date.today()
            today_str  = today.isoformat()

            opts = fetch_options_data(ticker)
            if not opts or opts.get("top_prem_value", 0) <= 0:
                return None

            expiry = opts.get("top_prem_expiry")
            if not expiry or expiry <= today_str:
                return None

            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0:
                hist  = tkr.history(period="1d")
                price = float(hist["Close"].iloc[-1]) if not hist.empty else 0
            prem_k = float(opts.get("top_prem_value", 0))
            if prem_k < 500:   # minimum $500K — institutional smart money only
                return None

            # Days to earnings
            days_to_earnings = None
            try:
                cal = tkr.calendar
                earn = None
                if isinstance(cal, dict):
                    earn = cal.get("Earnings Date")
                    if isinstance(earn, list) and earn:
                        earn = earn[0]
                elif cal is not None and hasattr(cal, "columns") and "Earnings Date" in cal.columns:
                    earn = cal.loc["Earnings Date", cal.columns[0]]
                if earn is not None:
                    if hasattr(earn, "date"):
                        earn = earn.date()
                    elif isinstance(earn, str):
                        earn = datetime.strptime(earn[:10], "%Y-%m-%d").date()
                    diff = (earn - today).days
                    if 0 <= diff <= 365:
                        days_to_earnings = diff
            except Exception:
                pass

            # Short float %
            short_float_pct = None
            try:
                info = tkr.info
                sfp  = float(info.get("shortPercentOfFloat") or 0) * 100
                if 0 < sfp < 100:
                    short_float_pct = round(sfp, 1)
            except Exception:
                pass

            return {
                "ticker":           ticker,
                "price":            round(price, 2),
                "strike":           opts.get("top_prem_strike"),
                "expiry":           expiry,
                "premium_m":        round(prem_k / 1000, 2),
                "premium_k":        round(prem_k, 1),
                "call_put_ratio":   round(float(opts.get("call_put_ratio", 0)), 2),
                "call_vol_oi":      round(float(opts.get("call_vol_oi", 0)), 2),
                "total_call_vol":   int(opts.get("total_call_vol", 0)),
                "days_to_earnings": days_to_earnings,
                "short_float_pct":  short_float_pct,
            }
        except Exception:
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_get_row, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                rows.append(r)

    rows.sort(key=lambda x: x["premium_m"], reverse=True)
    top40 = rows[:40]
    for i, r in enumerate(top40):
        r["rank"] = i + 1

    # Persist signals for the outcome tracker
    try:
        store_bull_flow_signals(top40, session="manual")
    except Exception as _se:
        print(f"[bull_flow] signal store error: {_se}")

    out = {"results": top40, "scanned": len(tickers), "returned": len(top40)}
    app._bf_cache     = out
    app._bf_cache_ts  = _dt.now()
    app._bf_cache_key = tickers
    return jsonify(out)


# ── Net Equity Flow ──────────────────────────────────────────────────────────

@app.route("/stock-api/net-flow", methods=["POST"])
def net_flow_scan():
    """
    Scan tickers for today's net equity flow using intraday 1-min bars.
    Tick rule: bars where close >= open = buy flow; else = sell flow.
    Returns tickers with positive net flow sorted largest to smallest.
    """
    import yfinance as yf
    from datetime import datetime as _dt
    import threading

    if not hasattr(app, "_nf_lock"):
        app._nf_lock = threading.Lock()

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD[:50])
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD[:50]
    tickers = [t.strip().upper() for t in tickers[:50]]

    _nf_cache = getattr(app, "_nf_cache", None)
    _nf_ts    = getattr(app, "_nf_cache_ts", None)
    _nf_key   = getattr(app, "_nf_cache_key", None)
    if (_nf_cache and _nf_ts and _nf_key == tickers
            and (_dt.now() - _nf_ts).total_seconds() < 300):
        return jsonify(_nf_cache)

    with app._nf_lock:
        _nf_cache = getattr(app, "_nf_cache", None)
        _nf_ts    = getattr(app, "_nf_cache_ts", None)
        _nf_key   = getattr(app, "_nf_cache_key", None)
        if (_nf_cache and _nf_ts and _nf_key == tickers
                and (_dt.now() - _nf_ts).total_seconds() < 300):
            return jsonify(_nf_cache)

        def _compute_flow(ticker):
            try:
                hist = yf.Ticker(ticker).history(period="1d", interval="1m")
                if hist.empty or len(hist) < 5:
                    return None
                inflow = outflow = 0.0
                for _, row in hist.iterrows():
                    if row["Volume"] <= 0:
                        continue
                    avg = (float(row["Open"]) + float(row["Close"])) / 2
                    dv  = avg * float(row["Volume"])
                    if float(row["Close"]) >= float(row["Open"]):
                        inflow  += dv
                    else:
                        outflow += dv
                net = inflow - outflow
                last_price = float(hist["Close"].iloc[-1])
                total      = inflow + outflow
                return {
                    "ticker":       ticker,
                    "price":        round(last_price, 2),
                    "inflow_m":     round(inflow   / 1_000_000, 1),
                    "outflow_m":    round(outflow  / 1_000_000, 1),
                    "net_m":        round(net       / 1_000_000, 1),
                    "total_vol_m":  round(total    / 1_000_000, 1),
                    "flow_ratio":   round(inflow / max(outflow, 1), 3),
                }
            except Exception:
                return None

        rows = []
        with ThreadPoolExecutor(max_workers=10) as ex:
            for r in ex.map(_compute_flow, tickers):
                if r and r["net_m"] > 0:
                    rows.append(r)

        rows.sort(key=lambda x: x["net_m"], reverse=True)
        for i, r in enumerate(rows):
            r["rank"] = i + 1

        out = {"results": rows, "scanned": len(tickers)}
        app._nf_cache     = out
        app._nf_cache_ts  = _dt.now()
        app._nf_cache_key = tickers
        return jsonify(out)


@app.route("/stock-api/net-flow/single", methods=["GET"])
def net_flow_single():
    """Net equity flow for a single ticker — used by Stock Lookup."""
    import yfinance as yf
    ticker = request.args.get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    try:
        hist = yf.Ticker(ticker).history(period="1d", interval="1m")
        if hist.empty or len(hist) < 5:
            return jsonify({"error": "no data"}), 404
        inflow = outflow = 0.0
        bars = []
        for ts, row in hist.iterrows():
            if row["Volume"] <= 0:
                continue
            avg = (float(row["Open"]) + float(row["Close"])) / 2
            dv  = avg * float(row["Volume"])
            if float(row["Close"]) >= float(row["Open"]):
                inflow  += dv
                bars.append({"t": str(ts)[:16], "v": round(dv / 1_000_000, 2), "dir": "buy"})
            else:
                outflow += dv
                bars.append({"t": str(ts)[:16], "v": round(dv / 1_000_000, 2), "dir": "sell"})
        net   = inflow - outflow
        total = inflow + outflow
        return jsonify({
            "ticker":      ticker,
            "price":       round(float(hist["Close"].iloc[-1]), 2),
            "inflow_m":    round(inflow  / 1_000_000, 1),
            "outflow_m":   round(outflow / 1_000_000, 1),
            "net_m":       round(net     / 1_000_000, 1),
            "total_vol_m": round(total   / 1_000_000, 1),
            "flow_ratio":  round(inflow / max(outflow, 1), 3),
            "bars":        bars[-60:],   # last 60 min for chart
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Micro-Cap Net Flow ────────────────────────────────────────────────────────

# ── Micro-cap universe (~350 actively-traded small/micro-caps across all sectors)
# Curated for liquidity and trading interest — covers NASDAQ, NYSE, AMEX.
# On small floats even $1-5M net inflow is a strong accumulation signal.
_MICRO_CAP_UNIVERSE = sorted(set([
    # ── EV / Clean Energy ──────────────────────────────────────────────────
    "EVGO","BLNK","CHPT","FCEL","PLUG","NKLA","WKHS","ZEV","PTRA",
    "GOEV","RIDE","HYLN","MULN","SOLO","MVST","HLBZ","LCID","FFIE",
    "SOLO","KNDI","AYRO","SHPW","CBAT","CENN","IDAI","TEVN",
    # ── Space / Defense small-cap ──────────────────────────────────────────
    "RCAT","LUNR","BBAI","AEVA","SPIR","LIDR","ASTS","MNTS",
    "VORB","KULR","ATRO","RKLB","SATL","SFET","BWXT","CPI","CODA",
    "KTOS","LOAR","SLDP","ACHR","JOBY",
    # ── Biotech / Gene therapy / RNA ───────────────────────────────────────
    "RCKT","FOLD","DNLI","ARQT","VERA","KYMR","MGNX","SNDX",
    "PRLD","AXNX","BLUE","NTLA","GOSS","ARVN","IMVT","EDIT",
    "CRSP","BEAM","FIXX","FATE","ALDX","AGEN","ADAP","AKRO",
    "APLT","AVXL","BHVN","BLCM","BOLT","BTAI","BXRX","CGEM",
    "CHRS","CLBS","CMPS","CNCE","CPRX","CRBP","CRIS","CRNX",
    "CTMX","ENTA","FGEN","FHTX","FLXN","FMTX","FREQ","GBIO",
    "GERN","GLYC","GRTS","GRTX","HOOK","HRTX","IDRA","IMAB",
    "IMCR","IMGN","IMTX","INAB","INBX","IOVA","JANX","JNCE",
    "KALA","KNSA","KPTI","KRTX","KURA","LQDA","MIRM","MNKD",
    "NVAX","OCGN","ONCT","PACB","PALI","PRAX","SAGE","SAVA",
    "SGMO","STTK","SYRS","TGTX","TTOO","TYRA","VCNX","VERV",
    "VSTM","XNCR","RAPT","RXST","TRIL","RCUS","RVMD","SMMT",
    "VRNA","VYGR","KDNY","ALVO","CELC","NRIX","PLRX","PMVP",
    "RLMD","SCPH","SURF","SVRA","TDUP","TIRX","UROS","VACC",
    "VGVS","ZLAB","OMER","ORPH","PTGX","PULM","RIGL","RPRX",
    "SABS","SILK","SNSE","SPPI","SSTI","TOCA","TRDA","TYME",
    "XOMA","ZNTH","ACRS","ALEC","AMRX","ANTE","ARMO","AUTL",
    "BDTX","BFLY","BHAT","BIOR","BJRI","BLPH","CALA","CGEN",
    "CCCC","CLPT","CODA","COHU","CORT","CSIQ","CTSO","CYCN",
    "DERM","EPZM","ETNB","FDMT","FBIO","GCBC","GLDD","HARP",
    "HGEN","HLTH","HMHC","HSKA","IDEX","INFI","INSM","IRWD",
    "ITCI","ITOS","PCVX","PERL","PHAT","PIRS","PRME","PSTV",
    "PTCT","QGEN","RCEL","RGLS","RMTI","SEER","SENS","SLNO",
    "SRNE","STVN","TCDA","TPIC","TRVN","VTVT","ZAFG","AKBA",
    # ── Growth tech / SaaS small-cap ───────────────────────────────────────
    "HIMS","OPEN","BYND","LMND","ROOT","COUR","SKIN","PSFE",
    "BARK","TIGR","ACMR","VNET","WOLF","XPOF","LAUR","PRGO",
    "GETY","OPAD","ATIP","CERE","JMIA","NOVA","CLOV","PAYO",
    "DAVE","RELY","MAPS","PRPL","GNUS","MVIS","CELH","SOUN",
    "CXAI","SMAR","DOMO","PAGS","SPRK","AMPL","BIGC","BRZE",
    "CFLT","DOCN","DUOL","FROG","GLBE","GTLB","JAMF","LSPD",
    "MNTV","NCNO","NEWR","OMER","OSPN","PLTR","PSTG","RSKD",
    "RELY","SDGR","SMAR","SOUN","SPSC","SWAG","TASK","TOST",
    "TSPX","TTWO","TUYA","UPLD","VRNS","XMTR","YEXT","ZETA",
    # ── Fintech / Consumer finance ─────────────────────────────────────────
    "UPST","LC","EEFT","GDOT","INBK","MFIN","NRDS","PRAA",
    "QFIN","RPAY","SOFI","TREE","CURO","ATLC","EVRI","FLYW",
    "FUTU","HOOD","JFIN","KATX","LPLA","MGNI","MKTW","NRDS",
    "OPEN","OPK","PAYSIGN","PFSI","PRAA","RDFN","RDVT","RIOT",
    "STER","STRS","UWMC","VLTA","WRLD",
    # ── Consumer / Lifestyle ───────────────────────────────────────────────
    "SFIX","ACMR","COOK","XPER","PRPL","GNUS","CELH","CENT",
    "LOVE","LAZR","FLWS","GOED","JILL","LESL","LOVE","MGRX",
    "MNST","NKLA","OPAD","PAYA","PETZ","PRPL","PUBM","PTON",
    "RDFN","RENT","RVLV","SAMG","SFIX","SSYS","SWIM","TLRY",
    "TORRID","TPVG","TPIC","TRTN","TSRI","TTGT","TWST","TYRA",
    "VLCN","VVOS","WETG","WKME","XELA","XPOF","ZVIA","ZYME",
    # ── Mining / Rare earths / Commodities ─────────────────────────────────
    "MP","GATO","MAG","TMST","AEYE","AZEK","CATO","CEIX","CLNE",
    "CLF","CMP","CORE","CTRA","DUNE","ELEV","FANG","GATO","GFI",
    "GOLD","HL","HLIO","KORE","MTAL","NOVAGOLD","PAAS","RIO",
    "SAND","SILV","SVM","TECK","TRQ","UEC","UUUU","WDFC","WFG",
    # ── Healthcare / Medical devices ───────────────────────────────────────
    "SILK","INMD","OSUR","AXNX","HSKA","SEER","FLXN","PACB",
    "ATEC","AMED","AMWL","AXDX","BEAT","CHNG","CLFD","CNMD",
    "CSTL","DXCM","EHTH","EMED","FRHC","HAIN","HAYW","HMSY",
    "HOLX","INSP","IPAR","ISEE","KIDS","LHCG","LNTH","MASI",
    "MCRB","MDCO","MDGL","MDVX","MELI","MGNX","MMSI","MNMD",
    "NTRA","NVCR","OFIX","OMCL","OPCH","ORGO","OSUR","PDCO",
    "PETS","PFGC","PGNY","PINC","PRCT","PRTK","QDEL","RGEN",
    "RLMD","RMBS","RNST","RPAY","RRTS","RXDX","SANA","SDGR",
    "SEAS","SEER","SENS","SILK","SOLY","SPNE","SRNE","SRTX",
    "SSYS","STAA","STEP","STGW","SWAV","SXCL","SYBX","SYNH",
]))


def _get_microcap_tickers() -> list:
    """Return the curated micro-cap ticker universe (~350 stocks)."""
    return list(_MICRO_CAP_UNIVERSE)


def _run_microcap_flow_scan() -> dict:
    """Core scan: fetch intraday flow + market cap for all tickers.

    Returns results split into cap_tier buckets:
      micro  → $50M–$300M market cap
      small  → $300M–$2B
      mid    → >$2B  (included but shown separately)
      nano   → <$50M (OTC/illiquid — included but flagged)
      unknown → market cap unavailable
    """
    import yfinance as yf

    tickers = _get_microcap_tickers()

    def _compute_flow_mc(ticker):
        try:
            t_obj = yf.Ticker(ticker)
            hist  = t_obj.history(period="1d", interval="1m")
            if hist.empty or len(hist) < 5:
                return None

            # Grab market cap via fast_info (lightweight — reuses same session)
            market_cap = None
            try:
                market_cap = t_obj.fast_info.market_cap
                if market_cap and market_cap <= 0:
                    market_cap = None
            except Exception:
                pass

            inflow = outflow = 0.0
            for _, row in hist.iterrows():
                if row["Volume"] <= 0:
                    continue
                avg = (float(row["Open"]) + float(row["Close"])) / 2
                dv  = avg * float(row["Volume"])
                if float(row["Close"]) >= float(row["Open"]):
                    inflow  += dv
                else:
                    outflow += dv

            net   = inflow - outflow
            total = inflow + outflow
            if net <= 0:
                return None

            last_price  = float(hist["Close"].iloc[-1])
            net_m       = net / 1_000_000
            mktcap_m    = round(market_cap / 1_000_000, 1) if market_cap else None
            net_pct     = round(net / market_cap * 100, 2) if market_cap and market_cap > 0 else None

            # Tier classification (standard Wall Street definitions)
            if market_cap is None:
                cap_tier = "unknown"
            elif market_cap < 50_000_000:
                cap_tier = "nano"
            elif market_cap < 300_000_000:
                cap_tier = "micro"
            elif market_cap < 2_000_000_000:
                cap_tier = "small"
            else:
                cap_tier = "mid"

            return {
                "ticker":         ticker,
                "price":          round(last_price, 2),
                "inflow_m":       round(inflow  / 1_000_000, 2),
                "outflow_m":      round(outflow / 1_000_000, 2),
                "net_m":          round(net_m, 3),
                "total_vol_m":    round(total   / 1_000_000, 2),
                "flow_ratio":     round(inflow / max(outflow, 1), 3),
                "market_cap_m":   mktcap_m,
                "net_pct_mktcap": net_pct,
                "cap_tier":       cap_tier,
            }
        except Exception:
            return None

    raw = []
    with ThreadPoolExecutor(max_workers=14) as ex:
        for r in ex.map(_compute_flow_mc, tickers):
            if r:
                raw.append(r)

    # Rank within each tier separately by net_pct_mktcap (most meaningful signal)
    def _rank_tier(rows):
        rows.sort(key=lambda x: (x["net_pct_mktcap"] or 0), reverse=True)
        for i, r in enumerate(rows):
            r["rank"] = i + 1
        return rows

    micro  = _rank_tier([r for r in raw if r["cap_tier"] == "micro"])
    small  = _rank_tier([r for r in raw if r["cap_tier"] == "small"])
    nano   = _rank_tier([r for r in raw if r["cap_tier"] == "nano"])
    mid    = _rank_tier([r for r in raw if r["cap_tier"] == "mid"])
    unk    = _rank_tier([r for r in raw if r["cap_tier"] == "unknown"])

    return {
        "micro":   micro,
        "small":   small,
        "nano":    nano,
        "mid":     mid,
        "unknown": unk,
        "scanned": len(tickers),
    }


@app.route("/stock-api/net-flow/microcap", methods=["POST"])
def net_flow_microcap():
    """Net equity flow scan for the dynamic micro-cap universe (200-400 tickers).

    Results are pre-cached every 30 min by the scheduler during market hours.
    Cache TTL is 30 min; first cold load takes ~40-90 s depending on ticker count.
    """
    from datetime import datetime as _dt

    if not hasattr(app, "_nfmc_lock"):
        app._nfmc_lock = threading.Lock()

    _CACHE_TTL = 1800  # 30 minutes

    _cache = getattr(app, "_nfmc_cache", None)
    _ts    = getattr(app, "_nfmc_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < _CACHE_TTL:
        return jsonify(_cache)

    with app._nfmc_lock:
        _cache = getattr(app, "_nfmc_cache", None)
        _ts    = getattr(app, "_nfmc_cache_ts", None)
        if _cache and _ts and (_dt.now() - _ts).total_seconds() < _CACHE_TTL:
            return jsonify(_cache)

        out = _run_microcap_flow_scan()
        app._nfmc_cache    = out
        app._nfmc_cache_ts = _dt.now()
        return jsonify(out)


@app.route("/stock-api/net-flow/microcap/tickers", methods=["GET"])
def microcap_ticker_count():
    """Returns the current size of the dynamic micro-cap ticker universe."""
    tickers = _get_microcap_tickers()
    return jsonify({"count": len(tickers), "tickers": tickers})


# ── Multi-day flow streak scan ────────────────────────────────────────────────

def _run_multiday_flow_scan(n_days: int = 40) -> dict:
    """
    For every ticker in the micro-cap universe, fetch `period='60d' interval='1d'`
    daily OHLCV (~42 trading days) and compute per-day net flow using the same
    buy/sell candle logic as the intraday scan (close >= open → inflow; else → outflow).

    Looks back up to ~3 months so 1-week (5d), 2-week (10d), and 3-week (15d)
    institutional accumulation streaks are all detectable.

    Returns only tickers with streak >= 2 consecutive positive-net days,
    sorted by streak desc then by cumulative % of market cap.
    """
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tickers = _get_microcap_tickers()

    def _compute(ticker):
        try:
            t_obj = yf.Ticker(ticker)
            hist  = t_obj.history(period="60d", interval="1d")
            if hist.empty or len(hist) < 2:
                return None

            market_cap = None
            try:
                market_cap = t_obj.fast_info.market_cap
                if market_cap and market_cap <= 0:
                    market_cap = None
            except Exception:
                pass

            # Build per-day flow list (oldest → newest)
            daily = []
            for dt, row in hist.iterrows():
                vol = float(row.get("Volume", 0))
                if vol <= 0:
                    continue
                avg   = (float(row["Open"]) + float(row["Close"])) / 2
                dv    = avg * vol
                is_up = float(row["Close"]) >= float(row["Open"])
                net_m = round(dv / 1_000_000 if is_up else -dv / 1_000_000, 3)
                date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                daily.append({"date": date_str, "net_m": net_m, "positive": net_m > 0})

            if not daily:
                return None

            # Count consecutive positive days from the most recent day backwards
            streak = 0
            for d in reversed(daily):
                if d["positive"]:
                    streak += 1
                else:
                    break

            if streak < 2:
                return None

            # Accumulated net flow over the streak window
            streak_days     = daily[-streak:]
            streak_net_vals = [d["net_m"] for d in streak_days]
            total_net_m     = round(sum(streak_net_vals), 3)
            avg_daily_net_m = round(total_net_m / streak, 3)
            min_daily_net_m = round(min(streak_net_vals), 3)

            # Consistency: how even is the buying across days?
            # min/avg ratio — 1.0 = perfectly even, 0.0 = all in one day
            consistency = round(min_daily_net_m / avg_daily_net_m, 2) if avg_daily_net_m > 0 else 0.0

            mktcap_m    = round(market_cap / 1_000_000, 1) if market_cap else None
            total_pct   = round(total_net_m / (market_cap / 1_000_000) * 100, 2) \
                          if market_cap and market_cap > 0 else None
            avg_pct_day = round(avg_daily_net_m / (market_cap / 1_000_000) * 100, 3) \
                          if market_cap and market_cap > 0 else None

            # Tier
            if market_cap is None:
                cap_tier = "unknown"
            elif market_cap < 50_000_000:
                cap_tier = "nano"
            elif market_cap < 300_000_000:
                cap_tier = "micro"
            elif market_cap < 2_000_000_000:
                cap_tier = "small"
            else:
                cap_tier = "mid"

            last_price = round(float(hist["Close"].iloc[-1]), 2)

            return {
                "ticker":           ticker,
                "price":            last_price,
                "streak":           streak,
                "total_net_m":      total_net_m,
                "avg_daily_net_m":  avg_daily_net_m,
                "min_daily_net_m":  min_daily_net_m,
                "consistency":      consistency,      # 0–1; ≥0.4 = institutional-like
                "market_cap_m":     mktcap_m,
                "total_pct_mktcap": total_pct,
                "avg_pct_per_day":  avg_pct_day,      # avg % of mktcap flowing in per day
                "cap_tier":         cap_tier,
                "days":             daily[-n_days:],  # last N daily dots for UI
            }
        except Exception:
            return None

    results = []
    with ThreadPoolExecutor(max_workers=14) as ex:
        futures = {ex.submit(_compute, t): t for t in tickers}
        for f in as_completed(futures):
            r = f.result()
            if r:
                results.append(r)

    # Sort: longest streak first, then cumulative % of mktcap descending
    results.sort(key=lambda x: (-x["streak"], -(x["total_pct_mktcap"] or 0)))
    for i, r in enumerate(results, 1):
        r["rank"] = i

    return {"results": results, "scanned": len(tickers), "found": len(results)}


@app.route("/stock-api/net-flow/multiday", methods=["POST", "GET"])
def net_flow_multiday():
    """Multi-day accumulation streak scan. Cached for 2 hours (daily data)."""
    from datetime import datetime as _dt

    if not hasattr(app, "_nfmd_lock"):
        app._nfmd_lock = threading.Lock()

    _CACHE_TTL = 7200   # 2 hours — daily candles change slowly

    _cache = getattr(app, "_nfmd_cache", None)
    _ts    = getattr(app, "_nfmd_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < _CACHE_TTL:
        return jsonify(_cache)

    with app._nfmd_lock:
        _cache = getattr(app, "_nfmd_cache", None)
        _ts    = getattr(app, "_nfmd_cache_ts", None)
        if _cache and _ts and (_dt.now() - _ts).total_seconds() < _CACHE_TTL:
            return jsonify(_cache)

        out = _run_multiday_flow_scan()
        app._nfmd_cache    = out
        app._nfmd_cache_ts = _dt.now()
        return jsonify(out)


# ── AI Signal Analysis ────────────────────────────────────────────────────────

@app.route("/stock-api/net-flow/ai-signal", methods=["POST"])
def net_flow_ai_signal():
    """Analyze streak rows with OpenAI and return structured conviction signals."""
    import os, json, re
    from openai import OpenAI

    body = request.get_json(silent=True) or {}
    rows = body.get("rows", [])

    if not rows:
        return jsonify({"error": "No streak data provided — run the Accumulation Streak scan first."}), 400

    # Sort by streak desc, then consistency desc; cap at 30
    rows = sorted(rows, key=lambda r: (-r.get("streak", 0), -r.get("consistency", 0)))[:30]

    # Build compact, LLM-readable context
    stock_lines = []
    for r in rows:
        parts = [
            f"ticker={r.get('ticker')}",
            f"streak={r.get('streak')}d",
            f"avg_flow=${r.get('avg_daily_net_m', 0):.2f}M/day",
            f"consistency={r.get('consistency', 0):.2f}",
        ]
        if r.get("min_daily_net_m") is not None:
            parts.append(f"min_day=${r.get('min_daily_net_m', 0):.2f}M")
        if r.get("total_pct_mktcap"):
            parts.append(f"pct_mktcap={r.get('total_pct_mktcap'):.3f}%")
        if r.get("market_cap_m"):
            parts.append(f"mktcap=${r.get('market_cap_m'):.0f}M")
        if r.get("cap_tier"):
            parts.append(f"tier={r.get('cap_tier')}")
        stock_lines.append("  " + " | ".join(parts))

    stock_block = "\n".join(stock_lines)

    prompt = f"""You are an institutional flow analyst specializing in micro-cap and small-cap accumulation patterns.

Analyze each stock for signs of sustained institutional accumulation using multi-day net flow data.

Field definitions:
- streak: consecutive trading days with positive net flow (no breaks)
- avg_flow: average daily net inflow in $M
- min_day: weakest single day inflow — if close to avg_flow, buying is EVEN (institutional pattern); near 0 = one big day dominated (retail spike or event)
- consistency: min_day / avg_day ratio (0-1) — 1.0 = perfectly smooth increments, 0.0 = one giant day did all the work
- pct_mktcap: cumulative inflow as % of market cap over the streak period
- tier: nano (<$50M mktcap), micro ($50-300M), small ($300M-1B), mid ($1-5B)

Stocks to analyze:
{stock_block}

Signal classification — assign exactly ONE per stock:
- CONVICTION: streak ≥ 10d AND consistency ≥ 0.65 — textbook stealth accumulation, likely institutional loading
- BUILDING: streak 5-9d OR (streak ≥ 10d but consistency 0.40-0.64) — pattern forming, early positioning
- WATCH: streak 3-4d with consistency ≥ 0.40 — too short to confirm but worth monitoring
- NOISE: consistency < 0.35 regardless of streak, or streak < 3 — likely event-driven or retail, not sustained

Key insight: true institutional accumulation shows SMOOTH increments (high consistency). A 7-day streak where day 1 = $10M and days 2-7 = $0.1M each is a retail spike, not accumulation. True accumulation: $1.0M, $0.9M, $1.1M, $1.0M, $0.95M... every day.

Also: for nano-caps ($20-50M mktcap), even $0.3M/day sustained over 10 days represents meaningful size — harder to hide than mid-cap flow.

Return ONLY valid JSON, zero markdown fences or extra text:
{{"signals":[{{"ticker":"XXXX","signal":"CONVICTION","thesis":"1-2 punchy sentences specific to the numbers — what this pattern implies for the stock.","confidence":85}}]}}"""

    try:
        client = OpenAI(
            api_key=os.environ["AI_INTEGRATIONS_OPENAI_API_KEY"],
            base_url=os.environ["AI_INTEGRATIONS_OPENAI_BASE_URL"],
        )
        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=2500,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if the model adds them anyway
        raw = re.sub(r'^```(?:json)?\s*', '', raw).strip()
        raw = re.sub(r'\s*```$',          '', raw).strip()

        result = json.loads(raw)
        result["model"]    = "gpt-5.4"
        result["analyzed"] = len(rows)
        return jsonify(result)

    except json.JSONDecodeError as exc:
        return jsonify({"error": f"AI returned invalid JSON: {exc}", "raw": raw[:500]}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/stock-api/market/overview", methods=["GET"])
def market_overview():
    import yfinance as yf
    from datetime import date

    _cache = getattr(app, "_mo_cache", None)
    _ts    = getattr(app, "_mo_cache_ts", None)
    if _cache and _ts and _ts == date.today().isoformat():
        return jsonify(_cache)

    SECTORS = [
        ("XLK",  "Technology"),
        ("XLF",  "Financials"),
        ("XLE",  "Energy"),
        ("XLV",  "Healthcare"),
        ("XLY",  "Cons. Disc."),
        ("XLP",  "Cons. Staples"),
        ("XLI",  "Industrials"),
        ("XLB",  "Materials"),
        ("XLRE", "Real Estate"),
        ("XLU",  "Utilities"),
        ("XLC",  "Comm. Services"),
    ]
    INDICES = [
        ("SPY", "S&P 500"),
        ("QQQ", "Nasdaq 100"),
        ("DIA", "Dow Jones"),
        ("IWM", "Russell 2000"),
        ("VIX", "VIX"),
    ]

    def get_chg(ticker):
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if len(hist) < 2:
                return None
            prev  = float(hist["Close"].iloc[-2])
            close = float(hist["Close"].iloc[-1])
            chg   = round((close - prev) / prev * 100, 2)
            return {"price": round(close, 2), "change_pct": chg}
        except Exception:
            return None

    sectors = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(get_chg, sym): (sym, name) for sym, name in SECTORS}
        for fut, (sym, name) in futs.items():
            r = fut.result()
            if r:
                sectors.append({"ticker": sym, "name": name, **r})
    sectors.sort(key=lambda x: x["change_pct"], reverse=True)

    indices = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(get_chg, sym): (sym, label) for sym, label in INDICES}
        for fut, (sym, label) in futs.items():
            r = fut.result()
            if r:
                indices.append({"ticker": sym, "label": label, **r})
    idx_order = {sym: i for i, (sym, _) in enumerate(INDICES)}
    indices.sort(key=lambda x: idx_order.get(x["ticker"], 99))

    # Advance / Decline using DEFAULT_LEADERBOARD
    ad_up, ad_down, ad_unch = 0, 0, 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(get_chg, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                if r["change_pct"] > 0.1:   ad_up   += 1
                elif r["change_pct"] < -0.1: ad_down += 1
                else:                        ad_unch += 1

    out = {
        "sectors": sectors,
        "indices": indices,
        "advance_decline": {"up": ad_up, "down": ad_down, "unchanged": ad_unch},
        "as_of": date.today().isoformat(),
    }
    app._mo_cache = out; app._mo_cache_ts = date.today().isoformat()
    return jsonify(out)


@app.route("/stock-api/ai-analyze", methods=["POST"])
def ai_analyze_route():
    """Generate Claude AI swing analysis for a stock."""
    body    = request.get_json(silent=True) or {}
    ticker  = body.get("ticker", "").upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400

    rsi          = body.get("rsi")
    macd         = body.get("macd")
    vol_ratio    = body.get("volume_ratio")
    price        = body.get("price")
    change_pct   = body.get("change_pct")
    score_val    = body.get("score")
    rating       = body.get("rating", "Neutral")
    sector       = body.get("sector", "")
    sma50        = body.get("sma50")
    sma200       = body.get("sma200")

    def _f(v, d=2):
        try: return round(float(v), d)
        except: return None

    prompt = f"""You are a professional swing trader. Provide a concise, actionable swing trade analysis for {ticker}.

Data:
- Sector: {sector or "N/A"}
- Price: ${_f(price) or "N/A"} ({_f(change_pct, 2) or 0:+.2f}% today)
- RSI (14): {_f(rsi, 1) or "N/A"} {"[OVERBOUGHT]" if rsi and float(rsi) > 70 else "[OVERSOLD]" if rsi and float(rsi) < 30 else ""}
- MACD: {_f(macd, 3) or "N/A"} {"[BULLISH]" if macd and float(macd) > 0 else "[BEARISH]"}
- Volume Ratio: {_f(vol_ratio, 1) or "N/A"}x {"[ELEVATED]" if vol_ratio and float(vol_ratio) >= 1.5 else ""}
- SMA 50: ${_f(sma50) or "N/A"} | SMA 200: ${_f(sma200) or "N/A"}
- Composite Score: {_f(score_val, 1) or "N/A"}/10 — {rating}

Write 3–4 sentences covering: (1) technical setup & momentum, (2) risk/reward, (3) swing trade thesis. Be direct and data-driven. Under 90 words."""

    try:
        import anthropic as _anthropic
        base_url = os.getenv("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
        api_key  = os.getenv("AI_INTEGRATIONS_ANTHROPIC_API_KEY", "placeholder")
        client   = _anthropic.Anthropic(base_url=base_url, api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text if msg.content else "Analysis unavailable."
        return jsonify({"analysis": text, "ticker": ticker})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/squeeze/detector", methods=["POST"])
def squeeze_detector():
    import yfinance as yf
    from smart_money import fetch_options_data, _f

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:50]]

    def _get_squeeze_row(ticker):
        try:
            tkr  = yf.Ticker(ticker)
            info = tkr.info
            sfp  = _f(info.get("shortPercentOfFloat", 0)) * 100
            if sfp < 5:
                return None
            sr   = _f(info.get("shortRatio", 0))
            opts = fetch_options_data(ticker)
            cpr  = _f(opts.get("call_put_ratio", 0)) if opts else 0
            pk   = _f(opts.get("top_prem_value", 0)) if opts else 0
            price = _f(getattr(tkr.fast_info, "last_price", 0) or 0)
            short_score   = min(sfp * 2, 50)
            flow_score    = min(cpr * 10, 50)
            squeeze_score = round(short_score + flow_score, 1)
            return {
                "ticker":          ticker,
                "price":           round(price, 2),
                "short_float_pct": round(sfp, 1),
                "short_ratio":     round(sr, 1),
                "call_put_ratio":  round(cpr, 2),
                "premium_m":       round(pk / 1000, 2),
                "squeeze_score":   squeeze_score,
            }
        except Exception:
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_get_squeeze_row, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                rows.append(r)
    rows.sort(key=lambda x: x["squeeze_score"], reverse=True)
    top = rows[:20]
    for i, r in enumerate(top):
        r["rank"] = i + 1
    return jsonify({"results": top, "scanned": len(tickers)})


@app.route("/stock-api/insider/trades", methods=["GET"])
def insider_trades_route():
    days    = int(request.args.get("days", 30))
    tickers = DEFAULT_LEADERBOARD
    trades  = fetch_insider_trades(tickers, days=days)
    return jsonify({"trades": trades, "count": len(trades)})


@app.route("/stock-api/ai/thesis", methods=["POST"])
def ai_thesis():
    body             = request.get_json(silent=True) or {}
    ticker           = body.get("ticker", "")
    cpr              = float(body.get("call_put_ratio", 0))
    premium_m        = float(body.get("premium_m", 0))
    days_to_earnings = body.get("days_to_earnings")
    short_float_pct  = body.get("short_float_pct")
    strike           = body.get("strike")
    expiry           = body.get("expiry")

    if not ticker:
        return jsonify({"error": "ticker required"}), 400

    parts = []
    if cpr >= 8:
        parts.append(f"An extraordinary {cpr:.1f}x call/put ratio places {ticker} among the highest-conviction institutional signals — fewer than 2% of scanned tickers ever reach this threshold.")
    elif cpr >= 5:
        parts.append(f"A {cpr:.1f}x call/put ratio on {ticker} is a rare institutional signal. At this level, the options tape is overwhelmingly positioned for upside — this is not retail speculation.")
    elif cpr >= 3:
        parts.append(f"A {cpr:.1f}x call/put ratio signals strong institutional bias toward {ticker}. Smart money is leaning aggressively bullish on this name.")
    elif cpr >= 2:
        parts.append(f"With a {cpr:.1f}x call/put ratio, {ticker}'s options flow is skewing clearly bullish — more than double call volume vs puts signals real directional conviction.")
    else:
        parts.append(f"Options flow in {ticker} shows a {cpr:.1f}x call/put ratio — calls are outpacing puts, suggesting a modestly bullish institutional lean.")

    if premium_m >= 10:
        parts.append(f"The ${premium_m:.1f}M in call premium is the size of a hedge fund position. Bets of this magnitude are placed deliberately — someone is taking a significant directional view backed by strong conviction.")
    elif premium_m >= 5:
        parts.append(f"${premium_m:.1f}M in call premium signals an institutional-sized position. This is a deliberate, meaningful directional bet — not noise.")
    elif premium_m >= 1:
        parts.append(f"${premium_m:.1f}M in call premium represents real money behind this thesis. Worth tracking how price responds over the next 1-3 sessions.")

    if days_to_earnings is not None:
        dte = int(days_to_earnings)
        if dte <= 5:
            parts.append(f"⚠️ Earnings in {dte} day{'s' if dte != 1 else ''} — this may be a high-risk earnings directional bet. IV crush post-announcement is a real risk regardless of direction.")
        elif dte <= 21:
            parts.append(f"With earnings {dte} days out, this call position is likely being built ahead of a catalyst. Traders often front-run earnings 2-3 weeks in advance when they have conviction.")
        elif dte <= 45:
            parts.append(f"Earnings are {dte} days away — a medium-term swing position potentially anticipating a pre-earnings run or a positive catalyst at the event.")

    if short_float_pct and float(short_float_pct) >= 20:
        parts.append(f"🔥 Critical: {float(short_float_pct):.1f}% of the float is short. Bullish options accumulation on a heavily-shorted stock is a classic squeeze setup — short covering could dramatically amplify any upside move.")
    elif short_float_pct and float(short_float_pct) >= 10:
        parts.append(f"With {float(short_float_pct):.1f}% short float, any positive catalyst could trigger short covering that amplifies gains beyond the initial move.")

    if strike and expiry:
        parts.append(f"Target strike: ${strike} expiring {expiry}. Monitor for follow-through price action and volume over the next 1-3 sessions as confirmation of the thesis.")

    return jsonify({"ticker": ticker, "thesis": " ".join(parts[:4])})


@app.route("/stock-api/outcomes", methods=["GET"])
def signal_outcomes_route():
    """Return stored bull-flow signals with T+3, T+5, T+10 price outcomes."""
    outcomes = get_signal_outcomes(limit=60)

    # Compute win rates
    t3_results  = [o["t3_win"]  for o in outcomes if o["t3_win"]  is not None]
    t5_results  = [o["t5_win"]  for o in outcomes if o["t5_win"]  is not None]
    t10_results = [o["t10_win"] for o in outcomes if o["t10_win"] is not None]

    def win_rate(lst):
        return round(sum(lst) / len(lst) * 100, 1) if lst else None

    return jsonify({
        "outcomes":  outcomes,
        "count":     len(outcomes),
        "win_rates": {
            "t3":  win_rate(t3_results),
            "t5":  win_rate(t5_results),
            "t10": win_rate(t10_results),
        },
    })


@app.route("/stock-api/breakout/radar", methods=["POST"])
def breakout_radar():
    import yfinance as yf
    import numpy as np
    import pandas as pd

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:60]]

    def _score(ticker):
        try:
            tkr  = yf.Ticker(ticker)
            hist = tkr.history(period="1y")
            if hist is None or len(hist) < 50:
                return None

            close  = hist["Close"]
            volume = hist["Volume"]

            price     = float(close.iloc[-1])
            high_52w  = float(close.rolling(252).max().iloc[-1])
            pct_52w   = round((price - high_52w) / high_52w * 100, 1)   # 0 = at 52w high

            sma50  = float(close.rolling(50).mean().iloc[-1])
            sma200 = float(close.rolling(200).mean().iloc[-1])

            # EMA for MACD
            ema12  = close.ewm(span=12, adjust=False).mean()
            ema26  = close.ewm(span=26, adjust=False).mean()
            macd   = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            macd_val   = float(macd.iloc[-1])
            signal_val = float(signal.iloc[-1])
            macd_prev  = float(macd.iloc[-2])
            sig_prev   = float(signal.iloc[-2])
            macd_cross = macd_prev < sig_prev and macd_val > signal_val  # fresh crossover

            # RSI
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            last_loss = float(loss.iloc[-1])
            last_gain = float(gain.iloc[-1])
            if last_loss == 0:
                rsi = 100.0
            elif last_gain == 0:
                rsi = 0.0
            else:
                rs  = last_gain / last_loss
                rsi = float(100 - 100 / (1 + rs))
            if np.isnan(rsi):
                rsi = 50.0

            # Volume surge
            avg_vol    = float(volume.iloc[-21:-1].mean())
            today_vol  = float(volume.iloc[-1])
            vol_ratio  = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0

            # ── Scoring ──────────────────────────────────────────────
            score = 0

            # MACD (0-25)
            if macd_cross:
                score += 25
            elif macd_val > signal_val:
                score += 15
            elif macd_val > 0:
                score += 5

            # RSI (0-25)
            if 55 <= rsi <= 70:
                score += 25
            elif 50 <= rsi < 55:
                score += 15
            elif 70 < rsi <= 80:
                score += 10
            elif 45 <= rsi < 50:
                score += 5

            # Volume surge (0-25)
            if vol_ratio >= 2.0:
                score += 25
            elif vol_ratio >= 1.5:
                score += 18
            elif vol_ratio >= 1.2:
                score += 10

            # 52W high proximity (0-25): pct_52w is 0 at high, negative below
            if pct_52w >= -3:
                score += 25
            elif pct_52w >= -7:
                score += 18
            elif pct_52w >= -12:
                score += 12
            elif pct_52w >= -20:
                score += 6

            if score < 20:
                return None

            def _safe(v, default=0.0):
                try:
                    f = float(v)
                    return default if (np.isnan(f) or np.isinf(f)) else f
                except Exception:
                    return default

            return {
                "ticker":            ticker,
                "price":             round(_safe(price), 2),
                "breakout_score":    score,
                "rsi":               round(_safe(rsi, 50.0), 1),
                "macd_bullish":      bool(macd_val > signal_val),
                "macd_cross":        bool(macd_cross),
                "volume_ratio":      round(_safe(vol_ratio), 2),
                "pct_from_52w_high": round(_safe(pct_52w), 1),
                "above_sma50":       bool(price > sma50),
                "above_sma200":      bool(price > sma200),
                "golden_cross":      bool(sma50 > sma200),
            }
        except Exception:
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(_score, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                rows.append(r)

    rows.sort(key=lambda x: x["breakout_score"], reverse=True)
    top = rows[:20]
    for i, r in enumerate(top):
        r["rank"] = i + 1

    return jsonify({"results": top, "scanned": len(tickers)})


@app.route("/stock-api/convergence", methods=["GET"])
def convergence():
    """Stocks with BOTH unusual volume AND unusual call flow — smart money convergence signal."""
    import yfinance as yf
    from smart_money import fetch_options_data

    try:
        yf.utils.get_crumb(reuse_session=False)
    except Exception:
        pass

    tickers = DEFAULT_LEADERBOARD
    results = []

    def _check(ticker):
        try:
            tkr = yf.Ticker(ticker)
            info = tkr.fast_info
            price = float(getattr(info, "last_price", 0) or 0)
            avg_vol = float(getattr(info, "three_month_average_volume", 1) or 1)
            today_vol = float(getattr(info, "last_volume", 0) or 0)
            vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

            if vol_ratio < 1.2 or price <= 0:
                return None

            opts = fetch_options_data(ticker)
            if not opts:
                return None

            call_put_ratio = float(opts.get("call_put_ratio", 0))
            prem = float(opts.get("top_prem_value", 0))

            if call_put_ratio < 1.3 or prem <= 0:
                return None

            convergence_score = min(round(vol_ratio * call_put_ratio, 1), 10.0)
            return {
                "ticker": ticker,
                "price": round(price, 2),
                "vol_ratio": round(vol_ratio, 2),
                "call_put_ratio": round(call_put_ratio, 2),
                "premium_m": round(prem / 1000, 2),
                "convergence_score": convergence_score,
                "expiry": opts.get("top_prem_expiry"),
                "strike": opts.get("top_prem_strike"),
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_check, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: x["convergence_score"], reverse=True)
    for i, r in enumerate(results[:15]):
        r["rank"] = i + 1
    return jsonify({"results": results[:15], "scanned": len(tickers)})


@app.route("/stock-api/premarket", methods=["GET"])
def premarket():
    """Pre-market movers — price change and volume vs average."""
    import yfinance as yf
    from datetime import datetime as _pdt

    _cache = getattr(app, "_pm_cache", None)
    _ts    = getattr(app, "_pm_cache_ts", None)
    if _cache and _ts and (_pdt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    tickers = DEFAULT_LEADERBOARD[:35]
    results = []

    def _get(ticker):
        try:
            tkr = yf.Ticker(ticker)
            info = tkr.fast_info
            price = float(getattr(info, "last_price", 0) or 0)
            prev_close = float(getattr(info, "previous_close", 0) or 0)
            if price <= 0 or prev_close <= 0:
                return None
            change_pct = (price - prev_close) / prev_close * 100
            if abs(change_pct) < 0.2:
                return None
            avg_vol = float(getattr(info, "three_month_average_volume", 1) or 1)
            today_vol = float(getattr(info, "last_volume", 0) or 0)
            vol_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
            mkt_cap = float(getattr(info, "market_cap", 0) or 0)
            return {
                "ticker": ticker,
                "price": round(price, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "vol_ratio": vol_ratio,
                "mkt_cap_b": round(mkt_cap / 1e9, 1) if mkt_cap else None,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_get, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    gainers = [r for r in results if r["change_pct"] > 0][:10]
    losers  = [r for r in results if r["change_pct"] < 0][:10]
    out = {"gainers": gainers, "losers": losers, "scanned": len(tickers)}
    app._pm_cache = out; app._pm_cache_ts = _pdt.now()
    return jsonify(out)


@app.route("/stock-api/darkpool", methods=["GET"])
def darkpool():
    """Dark Pool Radar — uses FINRA Reg SHO daily short volume as off-exchange proxy."""
    import requests as _req
    from datetime import datetime, timedelta

    _cache = getattr(app, "_dp_cache", None)
    _ts    = getattr(app, "_dp_cache_ts", None)
    if _cache and _ts and (datetime.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    def _fetch_date(date_str):
        combined = {}
        for code in ["FNSQ", "FNYX"]:
            url = f"https://cdn.finra.org/equity/regsho/daily/{code}shvol{date_str}.txt"
            try:
                r = _req.get(url, timeout=12)
                if r.status_code != 200:
                    continue
                for line in r.text.strip().split("\n")[1:]:
                    parts = line.strip().split("|")
                    if len(parts) < 5:
                        continue
                    sym = parts[1].strip()
                    try:
                        sv = int(float(parts[2])); tv = int(float(parts[4]))
                    except Exception:
                        continue
                    if sym in combined:
                        combined[sym]["sv"] += sv
                        combined[sym]["tv"] += tv
                    else:
                        combined[sym] = {"sv": sv, "tv": tv}
            except Exception:
                continue
        return combined

    raw = {}
    date_used = None
    now = datetime.now()
    for days_back in range(6):
        d = now - timedelta(days=days_back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        raw = _fetch_date(date_str)
        if raw:
            date_used = d.strftime("%b %d, %Y")
            break

    if not raw:
        return jsonify({"results": [], "date": None, "total_in_db": 0})

    candidates = []
    for ticker in DEFAULT_LEADERBOARD:
        if ticker not in raw:
            continue
        d = raw[ticker]
        tv = d["tv"]; sv = d["sv"]
        if tv < 50000:
            continue
        pct = sv / tv * 100
        if pct < 50:
            continue
        score = min(round(max(pct - 40, 0) / 40 * 10, 1), 10.0)
        if pct >= 70:
            signal = "EXTREME"
        elif pct >= 62:
            signal = "HIGH"
        elif pct >= 54:
            signal = "ELEVATED"
        else:
            signal = "NOTABLE"
        candidates.append({
            "ticker": ticker,
            "short_vol": sv,
            "total_vol": tv,
            "short_pct": round(pct, 1),
            "score": score,
            "signal": signal,
            "call_put_ratio": None,
            "bias": "UNKNOWN",
        })

    # Cross-reference options C/P ratio AND OBV trend for full accumulation/distribution picture
    def _get_signals(ticker):
        import yfinance as yf
        cp = None; bias = "UNKNOWN"; flow = "UNKNOWN"
        try:
            opts = fetch_options_data(ticker)
            if opts:
                cp_raw = float(opts.get("call_put_ratio", 0))
                cp = round(cp_raw, 2)
                bias = "BULLISH" if cp_raw >= 1.5 else "BEARISH" if cp_raw <= 0.7 else "NEUTRAL"
        except Exception:
            pass
        try:
            hist = yf.Ticker(ticker).history(period="20d")
            closes = hist["Close"].tolist()
            vols   = hist["Volume"].tolist()
            if len(closes) >= 10:
                obv = 0; obv_series = [0]
                for i in range(1, len(closes)):
                    if closes[i] > closes[i - 1]:
                        obv += vols[i]
                    elif closes[i] < closes[i - 1]:
                        obv -= vols[i]
                    obv_series.append(obv)
                recent = obv_series[-10:]
                denom = max(abs(recent[0]), 1)
                slope = (recent[-1] - recent[0]) / denom
                flow = "INFLOW" if slope > 0.03 else "OUTFLOW" if slope < -0.03 else "NEUTRAL"
        except Exception:
            pass
        return cp, bias, flow

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_get_signals, r["ticker"]): r for r in candidates}
        for fut in as_completed(futures):
            row = futures[fut]
            cp, bias, flow = fut.result()
            row["call_put_ratio"] = cp
            row["bias"] = bias
            row["flow"] = flow

    # Cross-reference unusual_calls_log: tickers with strong call sweeps in DB = confirmed BULLISH
    try:
        with _psycopg2.connect(_DB_URL) as _conn, _conn.cursor() as _cur:
            _cur.execute("""
                SELECT DISTINCT ticker FROM unusual_calls_log
                WHERE last_seen >= NOW() - INTERVAL '5 days'
                  AND vol_oi >= 5 AND prem >= 300000
            """)
            _sweep_bullish = {row[0] for row in _cur.fetchall()}
    except Exception:
        _sweep_bullish = set()

    for r in candidates:
        if r["ticker"] in _sweep_bullish:
            r["bias"] = "BULLISH"
            if r["flow"] == "UNKNOWN":
                r["flow"] = "INFLOW"

    def _bullish_score(r):
        s = 0
        if r["flow"]  == "INFLOW":   s += 30
        elif r["flow"] == "OUTFLOW": s -= 30
        if r["bias"]  == "BULLISH":  s += 20
        elif r["bias"] == "BEARISH": s -= 20
        s += r["short_pct"]
        return s

    def _conviction(r):
        b = r["bias"]; f = r["flow"]
        if b == "BULLISH"  and f == "INFLOW":  return "STRONG BUY"
        if b == "BULLISH"  and f != "OUTFLOW": return "BUY"
        if b != "BEARISH"  and f == "INFLOW":  return "INFLOW"
        if b == "BEARISH"  and f == "OUTFLOW": return "STRONG SELL"
        if b == "BEARISH"  and f != "INFLOW":  return "SELL"
        if b != "BULLISH"  and f == "OUTFLOW": return "OUTFLOW"
        return "WATCH"

    for r in candidates:
        r["conviction"] = _conviction(r)
    results = sorted(candidates, key=_bullish_score, reverse=True)
    # Only BULLISH — confirmed via live C/P ratio OR unusual_calls_log sweep in last 5 days
    bullish_only = [r for r in results if r["bias"] == "BULLISH"]
    for i, r in enumerate(bullish_only[:25]):
        r["rank"] = i + 1

    out = {"results": bullish_only[:25], "date": date_used, "total_in_db": len(raw), "total_candidates": len(candidates)}
    app._dp_cache = out
    app._dp_cache_ts = datetime.now()
    return jsonify(out)


@app.route("/stock-api/options-intent", methods=["GET"])
def options_intent():
    """Classify put options as HEDGE (OTM+long-dated) vs BEARISH BET (near-money+short-dated)."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_oi_cache", None)
    _ts    = getattr(app, "_oi_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    now = _dt.now()

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0:
                return None
            exps = tkr.options
            if not exps:
                return None

            hedge_prem = 0.0; bear_prem = 0.0
            hedge_vol  = 0;   bear_vol  = 0
            top_bear   = {"strike": None, "expiry": None, "prem": 0.0}

            for exp in exps[:8]:
                try:
                    days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                    puts = tkr.option_chain(exp).puts
                    for _, row in puts.iterrows():
                        strike = float(row.get("strike", 0) or 0)
                        vol    = int(row.get("volume", 0) or 0)
                        oi     = int(row.get("openInterest", 0) or 0)
                        last   = float(row.get("lastPrice", 0) or 0)
                        if strike <= 0 or last <= 0:
                            continue
                        otm_pct = (price - strike) / price * 100
                        prem    = (vol + oi) * last * 100
                        if otm_pct > 5 and days_out > 60:
                            hedge_prem += prem; hedge_vol += vol
                        elif -3 < otm_pct < 3 and days_out < 45:
                            bear_prem += prem;  bear_vol  += vol
                            if prem > top_bear["prem"]:
                                top_bear = {"strike": round(strike, 2), "expiry": exp, "prem": prem}
                except Exception:
                    continue

            total = hedge_prem + bear_prem
            if total < 1000:
                return None

            hedge_pct = round(hedge_prem / total * 100, 1)
            bear_pct  = round(bear_prem  / total * 100, 1)
            verdict   = ("BEARISH BET" if bear_pct >= 60
                        else "HEDGE"      if hedge_pct >= 60
                        else "MIXED")
            return {
                "ticker":          ticker,
                "price":           round(price, 2),
                "hedge_prem_m":    round(hedge_prem / 1e6, 2),
                "bear_prem_m":     round(bear_prem  / 1e6, 2),
                "hedge_pct":       hedge_pct,
                "bear_pct":        bear_pct,
                "verdict":         verdict,
                "top_bear_strike": top_bear["strike"],
                "top_bear_expiry": top_bear["expiry"],
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
        rows = [r for fut in as_completed(futures) if (r := fut.result()) is not None]

    rows.sort(key=lambda x: x["bear_prem_m"], reverse=True)
    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    app._oi_cache = out
    app._oi_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/vol-crush", methods=["GET"])
def vol_crush():
    """IV rank vs 1-year realized vol range — flags when implied vol is historically inflated."""
    import yfinance as yf, numpy as np
    from datetime import datetime as _dt

    _cache = getattr(app, "_vc_cache", None)
    _ts    = getattr(app, "_vc_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps = tkr.options
            if not exps: return None
            chain = tkr.option_chain(exps[0])
            puts  = chain.puts; calls = chain.calls
            atm   = min(sorted(puts["strike"].tolist()), key=lambda s: abs(s - price))
            ivp   = puts[puts["strike"]  == atm]["impliedVolatility"].values
            ivc   = calls[calls["strike"] == atm]["impliedVolatility"].values
            iv_vals = [v for v in list(ivp) + list(ivc) if v and v > 0]
            if not iv_vals: return None
            current_iv = float(np.mean(iv_vals))
            hist_full = tkr.history(period="1y")
            hist = hist_full["Close"]
            if len(hist) < 50: return None
            rets = hist.pct_change().dropna()
            hv_s = rets.rolling(21).std() * np.sqrt(252)
            hv_s = hv_s.dropna()
            hv30 = float(hv_s.iloc[-1])
            hv_min = float(hv_s.min()); hv_max = float(hv_s.max())
            iv_rank = round((current_iv - hv_min) / (hv_max - hv_min) * 100, 1) if (hv_max - hv_min) > 0 else 50.0
            iv_rank = max(0.0, min(100.0, iv_rank))
            iv_hv   = round(current_iv / hv30, 2) if hv30 > 0 else None

            # RSI (14-period)
            rsi = None
            try:
                gains  = rets.where(rets > 0, 0).rolling(14).mean()
                losses = (-rets.where(rets < 0, 0)).rolling(14).mean()
                rs = gains.iloc[-1] / losses.iloc[-1] if losses.iloc[-1] > 0 else 100
                rsi = round(100 - 100 / (1 + rs), 1)
            except Exception: pass

            # SMA50 position (% above / below)
            sma50_pct = None
            try:
                sma50 = float(hist.rolling(50).mean().iloc[-1])
                sma50_pct = round((price - sma50) / sma50 * 100, 1)
            except Exception: pass

            # 5-day vs 20-day volume trend
            vol_trend_5d = None
            try:
                vol = hist_full["Volume"].dropna()
                avg5  = float(vol.tail(5).mean())
                avg20 = float(vol.tail(20).mean())
                if avg20 > 0:
                    vol_trend_5d = round(avg5 / avg20, 2)
            except Exception: pass

            earnings_date = None
            short_float_pct = None
            short_ratio = None
            days_since_earnings = None
            days_to_earnings = None
            analyst_target_pct = None
            analyst_recommendation = None
            net_upgrades_7d = None
            instit_own_pct = None
            try:
                info = tkr.info
                ed = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
                if ed:
                    ed_dt = _dt.fromtimestamp(int(ed))
                    earnings_date = ed_dt.strftime("%Y-%m-%d")
                    diff_days = (_dt.now() - ed_dt).days
                    if 0 <= diff_days <= 10:
                        days_since_earnings = diff_days
                    elif diff_days < 0:
                        days_to_earnings = abs(diff_days)
                sfp = info.get("shortPercentOfFloat")
                if sfp and sfp > 0:
                    short_float_pct = round(float(sfp) * 100, 1)
                sr = info.get("shortRatio")
                if sr and sr > 0:
                    short_ratio = round(float(sr), 1)
                tgt = info.get("targetMeanPrice")
                if tgt and price > 0:
                    analyst_target_pct = round((float(tgt) - price) / price * 100, 1)
                rec = info.get("recommendationKey") or ""
                if rec:
                    analyst_recommendation = rec.lower().replace("_", " ")
                hip = info.get("heldPercentInstitutions")
                if hip and float(hip) > 0:
                    instit_own_pct = round(float(hip) * 100, 1)
            except Exception: pass

            # Analyst price target dispersion (how much analysts disagree)
            analyst_dispersion_pct = None
            try:
                _tgt_lo = info.get("targetLowPrice")
                _tgt_hi = info.get("targetHighPrice")
                _tgt_mn = info.get("targetMeanPrice")
                if _tgt_lo and _tgt_hi and _tgt_mn and float(_tgt_mn) > 0:
                    analyst_dispersion_pct = round((float(_tgt_hi) - float(_tgt_lo)) / float(_tgt_mn) * 100, 1)
            except Exception: pass

            # Analyst revision velocity (last 7 days)
            try:
                from datetime import timedelta as _td_vc
                updn = tkr.upgrades_downgrades
                if updn is not None and not updn.empty:
                    cutoff = _dt.now() - _td_vc(days=7)
                    recent = updn[updn.index.tz_localize(None) >= cutoff] if updn.index.tz is not None else updn[updn.index >= cutoff]
                    if not recent.empty:
                        action_col = next((c for c in recent.columns if "action" in c.lower()), None)
                        if action_col:
                            acts = recent[action_col].str.lower()
                            ups = int(acts.str.contains("up|rais|init|strong", na=False).sum())
                            dns = int(acts.str.contains("down|lower|cut|reduc|under", na=False).sum())
                            net_upgrades_7d = ups - dns
            except Exception: pass

            # Options bid/ask liquidity (ATM call spread % of mid — >10% = illiquid/avoid)
            options_liquidity_pct = None
            try:
                atm_call = calls[calls["strike"] == atm]
                if not atm_call.empty:
                    bid_c = float(atm_call["bid"].values[0] or 0)
                    ask_c = float(atm_call["ask"].values[0] or 0)
                    mid_c = (bid_c + ask_c) / 2
                    if mid_c > 0.5:
                        options_liquidity_pct = round((ask_c - bid_c) / mid_c * 100, 1)
            except Exception: pass

            # Earnings beat streak (how many of last 4 quarters beat consensus estimate)
            earnings_beat_streak = None
            try:
                # Try multiple yfinance APIs (changed across versions)
                eq = None
                for _attr in ("quarterly_earnings", "earnings", "earnings_history"):
                    try:
                        eq = getattr(tkr, _attr, None)
                        if eq is not None and hasattr(eq, "empty") and not eq.empty:
                            break
                        eq = None
                    except Exception:
                        eq = None
                if eq is not None and not eq.empty:
                    # Normalize column names (yfinance returns different caps/spacing)
                    eq.columns = [c.strip().title() for c in eq.columns]
                    est_col = next((c for c in eq.columns if "Estim" in c), None)
                    act_col = next((c for c in eq.columns if "Actual" in c or "Earn" in c), None)
                    if est_col and act_col:
                        recent_q = eq.tail(4)
                        beats = int((recent_q[act_col] > recent_q[est_col]).sum())
                        total_q = int(len(recent_q))
                        if total_q >= 2:
                            earnings_beat_streak = f"{beats}/{total_q}"
            except Exception: pass

            # Beta to SPY (30-day rolling — uses pre-fetched _spy_rets_arr from outer scope)
            spy_beta = None
            if _spy_rets_arr is not None and len(_spy_rets_arr) >= 20:
                try:
                    common = min(len(_spy_rets_arr), len(rets))
                    t_r = rets.values[-common:]
                    s_r = _spy_rets_arr[-common:]
                    cov_val = float(np.cov(t_r, s_r)[0][1])
                    var_spy = float(np.var(s_r))
                    if var_spy > 0:
                        spy_beta = round(cov_val / var_spy, 2)
                except Exception: pass

            # ── QUANT HEDGE-FUND SIGNALS ───────────────────────────────────────────────

            # Q1. Volatility skew (OTM put IV vs OTM call IV) + IV term structure
            iv_skew = None
            iv_term_structure = None
            try:
                put_otm = puts[puts["strike"] < price * 0.92]["strike"]
                call_otm = calls[calls["strike"] > price * 1.08]["strike"]
                if not put_otm.empty and not call_otm.empty:
                    p25_k = float(put_otm.max())
                    c25_k = float(call_otm.min())
                    p25_iv = float(puts[puts["strike"] == p25_k]["impliedVolatility"].values[0])
                    c25_iv = float(calls[calls["strike"] == c25_k]["impliedVolatility"].values[0])
                    if p25_iv > 0 and c25_iv > 0:
                        iv_skew = round((p25_iv - c25_iv) * 100, 1)
                if len(exps) >= 2:
                    _ch2 = tkr.option_chain(exps[1])
                    _atm2 = min(sorted(_ch2.calls["strike"].tolist()), key=lambda s: abs(s - price))
                    _iv2 = [v for _c in [_ch2.puts, _ch2.calls]
                            for v in _c[_c["strike"] == _atm2]["impliedVolatility"].values if v and v > 0]
                    if _iv2:
                        iv_term_structure = round((current_iv - float(np.mean(_iv2))) * 100, 1)
            except Exception: pass

            # Q2. Dealer Gamma Exposure (GEX) via Black-Scholes gamma approximation
            gex_m = None
            gex_regime = None
            try:
                from datetime import datetime as _dt_gex
                _exp_dt = _dt_gex.strptime(exps[0], "%Y-%m-%d")
                _T = max((_exp_dt - _dt.now()).days, 1) / 365.0
                _rf = 0.05
                def _bs_gamma(S, K, T, r, sigma):
                    if sigma <= 0 or T <= 0 or K <= 0: return 0.0
                    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
                    return float(np.exp(-0.5 * d1 ** 2) / np.sqrt(2 * np.pi) / (S * sigma * np.sqrt(T)))
                _gex = 0.0
                for _, _row in calls.iterrows():
                    _K, _iv_v, _oi = _row["strike"], _row["impliedVolatility"], _row.get("openInterest") or 0
                    if _iv_v > 0 and _K > 0 and _oi > 0:
                        _gex += _bs_gamma(price, _K, _T, _rf, _iv_v) * _oi * 100 * price
                for _, _row in puts.iterrows():
                    _K, _iv_v, _oi = _row["strike"], _row["impliedVolatility"], _row.get("openInterest") or 0
                    if _iv_v > 0 and _K > 0 and _oi > 0:
                        _gex -= _bs_gamma(price, _K, _T, _rf, _iv_v) * _oi * 100 * price
                gex_m = round(_gex / 1e6, 1)
                gex_regime = "LONG_GAMMA" if _gex > 0 else "SHORT_GAMMA"
            except Exception: pass

            # Q3. IV premium vs Realized Vol (>20% = rich → sell premium; <-10% = cheap → buy vol)
            iv_rv_premium = None
            try:
                if iv_hv and iv_hv > 0:
                    iv_rv_premium = round((iv_hv - 1.0) * 100, 1)
            except Exception: pass

            # Q4. Factor scoring: momentum (12-1 month), quality (ROE), value (forward P/E)
            momentum_12_1 = None
            factor_roe = None
            factor_fpe = None
            try:
                if len(hist) >= 60:  # need at least ~3 months; use oldest available as 12m proxy
                    _p1m  = float(hist.iloc[-21]) if len(hist) >= 21 else float(hist.iloc[-1])
                    _p12m = float(hist.iloc[0])   # oldest bar in the 1-year window
                    if _p12m > 0:
                        momentum_12_1 = round((_p1m / _p12m - 1) * 100, 1)
            except Exception: pass
            try:
                _finfo = info  # reuse already-fetched tkr.info from earnings block
            except NameError:
                try: _finfo = tkr.info
                except Exception: _finfo = {}
            try:
                _roe = _finfo.get("returnOnEquity")
                if _roe is not None and abs(float(_roe)) < 5:
                    factor_roe = round(float(_roe) * 100, 1)
                _fpe = _finfo.get("forwardPE")
                if _fpe is not None and 0 < float(_fpe) < 500:
                    factor_fpe = round(float(_fpe), 1)
            except Exception: pass

            # Q5. Cross-asset correlation (30d: ticker vs its sector ETF)
            sector_corr = None
            try:
                _sec_etf = _TICKER_TO_SECTOR_ETF.get(ticker)
                _sec_rets = _sector_rets_map.get(_sec_etf)
                if _sec_rets is not None and len(_sec_rets) >= 20 and len(rets) >= 20:
                    _common = min(len(_sec_rets), len(rets))
                    _cm = np.corrcoef(rets.values[-_common:], _sec_rets[-_common:])
                    sector_corr = round(float(_cm[0, 1]), 2)
            except Exception: pass

            # Q6. News sentiment (keyword-scoring of recent headlines — fast, no extra API)
            news_sentiment = None
            news_headline = None
            try:
                _news_items = tkr.news
                if _news_items:
                    _pos_w = {"beat","surge","rally","upgrade","buy","strong","record","growth","profit",
                              "bullish","raises","gains","breakout","soars","lifts","boost"}
                    _neg_w = {"miss","fall","decline","downgrade","sell","weak","loss","warning",
                              "cut","bearish","lowers","drops","slumps","disappoints","concern","probe"}
                    _ns = 0; _nc = 0
                    for _ni in _news_items[:6]:
                        _t = (_ni.get("title") or "").lower()
                        _ns += sum(1 for w in _pos_w if w in _t) - sum(1 for w in _neg_w if w in _t)
                        _nc += 1
                    if _nc > 0:
                        news_sentiment = round(_ns / _nc, 1)
                    news_headline = (_news_items[0].get("title") or "")[:90] if _news_items else None
            except Exception: pass

            # Q7. Put/Call OI ratio + flow persistence (cumulative across 4 exps)
            put_call_oi_ratio = None
            call_vol_oi_ratio = None
            put_vol_oi_ratio  = None
            pc_premium_ratio  = None
            try:
                _tot_put_oi = 0; _tot_call_oi = 0
                _tot_call_vol = 0; _tot_put_vol = 0
                _tot_call_prem = 0.0; _tot_put_prem = 0.0
                for _exp_oi in exps[:4]:
                    try:
                        _ch_oi = tkr.option_chain(_exp_oi)
                        _tot_put_oi    += int(_ch_oi.puts["openInterest"].fillna(0).sum())
                        _tot_call_oi   += int(_ch_oi.calls["openInterest"].fillna(0).sum())
                        _tot_call_vol  += int(_ch_oi.calls["volume"].fillna(0).sum())
                        _tot_put_vol   += int(_ch_oi.puts["volume"].fillna(0).sum())
                        _tot_call_prem += float((_ch_oi.calls["volume"].fillna(0) * _ch_oi.calls["lastPrice"].fillna(0)).sum()) * 100
                        _tot_put_prem  += float((_ch_oi.puts["volume"].fillna(0)  * _ch_oi.puts["lastPrice"].fillna(0)).sum()) * 100
                    except Exception: continue
                if _tot_call_oi > 0:
                    put_call_oi_ratio = round(_tot_put_oi / _tot_call_oi, 2)
                    call_vol_oi_ratio = round(_tot_call_vol / _tot_call_oi, 3)
                if _tot_put_oi > 0:
                    put_vol_oi_ratio = round(_tot_put_vol / _tot_put_oi, 3)
                if _tot_call_prem > 0:
                    pc_premium_ratio = round(_tot_put_prem / _tot_call_prem, 2)
            except Exception: pass

            # Q8. Earnings implied move (IV-based expected ±% move into earnings)
            earnings_impl_move_pct = None
            try:
                if days_to_earnings and days_to_earnings > 0 and current_iv > 0:
                    earnings_impl_move_pct = round(current_iv * (max(days_to_earnings, 1) / 252) ** 0.5 * 100, 1)
            except Exception: pass

            # Q9. 52-week range percentile (0%=at 52w low, 100%=at 52w high)
            week52_range_pct = None
            try:
                _52h = float(info.get("fiftyTwoWeekHigh") or 0)
                _52l = float(info.get("fiftyTwoWeekLow")  or 0)
                if _52h > _52l > 0 and price > 0:
                    week52_range_pct = round((price - _52l) / (_52h - _52l) * 100, 1)
            except Exception: pass

            # Q10. Borrow cost proxy — inferred from short interest (no extra API)
            borrow_cost_proxy = None
            try:
                if short_float_pct is not None:
                    if short_float_pct >= 20:
                        borrow_cost_proxy = "HIGH_BORROW"
                    elif short_float_pct >= 10:
                        borrow_cost_proxy = "ELEVATED_BORROW"
                    else:
                        borrow_cost_proxy = "EASY_BORROW"
            except Exception: pass

            # Q11. EPS revision trend (forward vs trailing EPS — direction of estimate revisions)
            eps_revision_trend = None
            try:
                _feps = info.get("forwardEps")
                _teps = info.get("trailingEps")
                if _feps is not None and _teps is not None and abs(float(_teps)) > 0.01:
                    _fwd_growth = round((float(_feps) / float(_teps) - 1) * 100, 1)
                    if _fwd_growth > 15:
                        eps_revision_trend = f"RISING(+{_fwd_growth}%_fwd_growth)"
                    elif _fwd_growth < -15:
                        eps_revision_trend = f"DECLINING({_fwd_growth}%_fwd_growth)"
                    else:
                        eps_revision_trend = f"STABLE({_fwd_growth:+.1f}%_fwd_growth)"
            except Exception: pass

            # Q12. Historical earnings price reaction proxy (avg of top-4 absolute 1-day moves in
            #      the past year — for most stocks these are almost exclusively earnings days)
            hist_earn_reaction_pct = None
            try:
                _abs_daily = (abs(rets) * 100).dropna().sort_values(ascending=False)
                _top4 = _abs_daily.iloc[:4].tolist()
                if len(_top4) >= 2:
                    hist_earn_reaction_pct = round(sum(_top4) / len(_top4), 1)
            except Exception: pass

            # Q13. Short squeeze risk score (composite — higher = more squeeze potential)
            squeeze_risk = None
            try:
                _sq_pts = 0
                if short_float_pct is not None and short_float_pct >= 15: _sq_pts += 1
                if borrow_cost_proxy in ("HIGH_BORROW", "ELEVATED_BORROW"):        _sq_pts += 1
                if rsi is not None and rsi > 60:                                    _sq_pts += 1
                if vol_trend_5d is not None and vol_trend_5d >= 1.3:               _sq_pts += 1
                if short_float_pct is not None:
                    squeeze_risk = ("EXTREME" if _sq_pts == 4 else
                                    "HIGH"    if _sq_pts == 3 else
                                    "MEDIUM"  if _sq_pts == 2 else "LOW")
            except Exception: pass

            # Q14. Relative strength vs SPY (stock 1-year return minus SPY 1-year return)
            rs_vs_spy = None
            try:
                if len(hist) >= 200 and _spy_1y_return is not None:
                    _stock_1y = round((float(hist.iloc[-1]) / float(hist.iloc[0]) - 1) * 100, 1)
                    rs_vs_spy = round(_stock_1y - _spy_1y_return, 1)
            except Exception: pass

            # Q15. Money flow ratio (up-day vs down-day average volume — accumulation vs distribution)
            money_flow_ratio = None
            try:
                _vol_s = hist_full["Volume"].dropna()
                _cidx = rets.index.intersection(_vol_s.index)
                if len(_cidx) >= 30:
                    _rv = _vol_s.loc[_cidx]; _rm = rets.loc[_cidx]
                    _up_v = float(_rv[_rm > 0].mean()); _dn_v = float(_rv[_rm < 0].mean())
                    if _dn_v > 0:
                        money_flow_ratio = round(_up_v / _dn_v, 2)
            except Exception: pass

            # Q16. Insider transaction net (open-market purchases vs sales last 30 days)
            # yfinance columns: Text (has "Sale at price X" / "Purchase at price X"), Shares, Start Date
            insider_net = None
            try:
                from datetime import timedelta as _td_ins
                ins = tkr.insider_transactions
                if ins is not None and not ins.empty:
                    _cutoff_ins = _dt.now() - _td_ins(days=30)
                    _cols_lower = {c.lower(): c for c in ins.columns}
                    # Date: 'Start Date' column (not the index)
                    _date_c = (_cols_lower.get('start date') or _cols_lower.get('date') or
                               next((c for c in ins.columns if 'date' in c.lower()), None))
                    # Text: 'Text' column has "Sale at price X" / "Purchase at price X"
                    _txt_c = (_cols_lower.get('text') or _cols_lower.get('transaction') or
                              next((c for c in ins.columns if any(k in c.lower() for k in ['text', 'desc'])), None))
                    _shr_c = (_cols_lower.get('shares') or
                              next((c for c in ins.columns if 'share' in c.lower()), None))
                    if _date_c and _txt_c and _shr_c:
                        _recent_ins = ins[ins[_date_c] >= _cutoff_ins]
                        if not _recent_ins.empty:
                            _buys  = _recent_ins[_recent_ins[_txt_c].astype(str).str.contains('Purchase|Buy|Acqui', case=False, na=False)]
                            _sells = _recent_ins[_recent_ins[_txt_c].astype(str).str.contains('Sale|Sell|Dispo', case=False, na=False)]
                            _buy_sh  = int(_buys[_shr_c].fillna(0).abs().sum())
                            _sell_sh = int(_sells[_shr_c].fillna(0).abs().sum())
                            if _buy_sh + _sell_sh > 0:
                                _net_ins = _buy_sh - _sell_sh
                                insider_net = ("BUYING"  if _net_ins >  1000 else
                                               "SELLING" if _net_ins < -1000 else "NEUTRAL")
            except Exception: pass

            # Q17. Dividend yield + days to ex-dividend date
            div_yield_pct = None
            ex_div_days = None
            try:
                _dy = info.get("dividendYield")
                if _dy and float(_dy) > 0:
                    _dy_f = float(_dy)
                    # yfinance returns as decimal (0.035) or percentage (3.5) depending on version
                    div_yield_pct = round(_dy_f if _dy_f > 1 else _dy_f * 100, 2)
                _exd = info.get("exDividendDate")
                if _exd:
                    _exd_dt = _dt.fromtimestamp(int(_exd))
                    _dd = (_exd_dt - _dt.now()).days
                    if -5 <= _dd <= 90:
                        ex_div_days = _dd
            except Exception: pass

            # Q18. Tail risk put concentration (crash hedging proxy — % of put vol in deep OTM strikes)
            tail_risk_put_pct = None
            try:
                _deep_k = price * 0.85
                _deep_puts = puts[puts["strike"] < _deep_k]
                _tot_pvol = int(puts["volume"].fillna(0).sum())
                if _tot_pvol > 100:
                    _deep_pvol = int(_deep_puts["volume"].fillna(0).sum())
                    tail_risk_put_pct = round(_deep_pvol / _tot_pvol * 100, 1)
            except Exception: pass

            verdict = ("HIGH FEAR" if iv_rank >= 80 else "ELEVATED" if iv_rank >= 60 else "NORMAL" if iv_rank >= 30 else "LOW IV")
            return {
                "ticker": ticker, "price": round(price, 2),
                "current_iv": round(current_iv * 100, 1),
                "hv_30": round(hv30 * 100, 1), "iv_hv_ratio": iv_hv, "iv_rank": iv_rank,
                "verdict": verdict,
                "earnings_date": earnings_date, "days_since_earnings": days_since_earnings,
                "days_to_earnings": days_to_earnings,
                "short_float_pct": short_float_pct, "short_ratio": short_ratio,
                "instit_own_pct": instit_own_pct,
                "rsi": rsi, "sma50_pct": sma50_pct, "vol_trend_5d": vol_trend_5d,
                "net_upgrades_7d": net_upgrades_7d,
                "options_liquidity_pct": options_liquidity_pct,
                "earnings_beat_streak": earnings_beat_streak,
                "spy_beta": spy_beta,
                "iv_skew": iv_skew,
                "iv_term_structure": iv_term_structure,
                "gex_m": gex_m, "gex_regime": gex_regime,
                "iv_rv_premium": iv_rv_premium,
                "momentum_12_1": momentum_12_1,
                "factor_roe": factor_roe, "factor_fpe": factor_fpe,
                "sector_corr": sector_corr,
                "news_sentiment": news_sentiment, "news_headline": news_headline,
                "analyst_target_pct": analyst_target_pct,
                "analyst_recommendation": analyst_recommendation,
                "put_call_oi_ratio": put_call_oi_ratio,
                "earnings_impl_move_pct": earnings_impl_move_pct,
                "call_vol_oi_ratio": call_vol_oi_ratio,
                "put_vol_oi_ratio": put_vol_oi_ratio,
                "week52_range_pct": week52_range_pct,
                "borrow_cost_proxy": borrow_cost_proxy,
                "eps_revision_trend": eps_revision_trend,
                "hist_earn_reaction_pct": hist_earn_reaction_pct,
                "squeeze_risk": squeeze_risk,
                "analyst_dispersion_pct": analyst_dispersion_pct,
                "pc_premium_ratio": pc_premium_ratio,
                "rs_vs_spy": rs_vs_spy,
                "money_flow_ratio": money_flow_ratio,
                "insider_net": insider_net,
                "div_yield_pct": div_yield_pct,
                "ex_div_days": ex_div_days,
                "tail_risk_put_pct": tail_risk_put_pct,
            }
        except Exception: return None

    # Pre-fetch SPY returns once (shared across all tickers for beta calculation)
    # Use module-level cached SPY data (avoids rate-limit collisions with 20 concurrent ticker fetches)
    _spy_rets_arr = _spy_1y_cache.get("rets_arr")
    _spy_1y_return = _spy_1y_cache.get("return_pct")

    # Pre-fetch sector ETF returns for cross-asset correlation (Q5 in _analyze)
    _TICKER_TO_SECTOR_ETF = {
        "AAPL":"XLK","MSFT":"XLK","NVDA":"XLK","GOOGL":"XLK","META":"XLK",
        "AMD":"XLK","INTC":"XLK","MU":"XLK","ORCL":"XLK","QQQ":"XLK",
        "JPM":"XLF","BAC":"XLF","GS":"XLF","WFC":"XLF",
        "JNJ":"XLV","UNH":"XLV","MRNA":"XLV","PFE":"XLV","ABBV":"XLV",
        "XOM":"XLE","CVX":"XLE","USO":"XLE",
        "LMT":"XLI","CAT":"XLI","BA":"XLI",
        "AMZN":"XLY","TSLA":"XLY","COST":"XLY",
        "NFLX":"XLC","DIS":"XLC","CMCSA":"XLC",
        "IWM":"IWM",
    }
    _sector_rets_map = {}
    try:
        _sec_etfs = list(set(_TICKER_TO_SECTOR_ETF.values()))
        _sec_raw2 = yf.download(_sec_etfs, period="60d", interval="1d", progress=False, auto_adjust=True)["Close"]
        for _etf in _sec_etfs:
            try:
                _ser2 = (_sec_raw2[_etf].dropna() if hasattr(_sec_raw2, "columns") and _etf in _sec_raw2.columns
                         else _sec_raw2.dropna())
                if len(_ser2) >= 20:
                    _sector_rets_map[_etf] = _ser2.pct_change().dropna().values
            except Exception:
                pass
    except Exception:
        pass

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
        rows = [r for fut in as_completed(futures) if (r := fut.result()) is not None]
    rows.sort(key=lambda x: x["iv_rank"], reverse=True)

    # Enrich with IV skew percentile + short float trend from daily_vol_snapshots history
    try:
        import psycopg2 as _pg2_vc
        _snap_tickers = [r["ticker"] for r in rows]
        with _pg2_vc.connect(os.environ["DATABASE_URL"]) as _conn_vc, _conn_vc.cursor() as _cur_vc:
            _cur_vc.execute("""
                SELECT ticker, iv_skew, short_float, pc_oi_ratio
                FROM daily_vol_snapshots
                WHERE ticker = ANY(%s) AND snap_date >= CURRENT_DATE - INTERVAL '252 days'
                ORDER BY ticker, snap_date
            """, (_snap_tickers,))
            _snap_rows = _cur_vc.fetchall()
        _hist_map: dict = {}
        for _hr in _snap_rows:
            _ht = _hr[0]
            if _ht not in _hist_map:
                _hist_map[_ht] = []
            _hist_map[_ht].append({"iv_skew": _hr[1], "short_float": _hr[2], "pc_oi_ratio": _hr[3]})
        for r in rows:
            t = r["ticker"]
            if t in _hist_map and len(_hist_map[t]) >= 30:
                _skews = [h["iv_skew"] for h in _hist_map[t] if h["iv_skew"] is not None]
                _sfps  = [h["short_float"] for h in _hist_map[t] if h["short_float"] is not None]
                _pcrs  = [h["pc_oi_ratio"] for h in _hist_map[t] if h["pc_oi_ratio"] is not None]
                if _skews and r.get("iv_skew") is not None:
                    r["iv_skew_pctl"] = int(sum(1 for s in _skews if s <= r["iv_skew"]) / len(_skews) * 100)
                if len(_sfps) >= 5 and r.get("short_float_pct") is not None:
                    r["short_float_trend"] = round(r["short_float_pct"] - _sfps[-5], 1)
                if len(_pcrs) >= 5 and r.get("put_call_oi_ratio") is not None:
                    r["pc_ratio_trend"] = round(r["put_call_oi_ratio"] - _pcrs[-5], 2)
    except Exception:
        pass

    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    app._vc_cache = out; app._vc_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/call-intent", methods=["GET"])
def call_intent():
    """Classify calls: FOMO (near-money+short-dated) vs ACCUMULATION (OTM+long-dated)."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_ci_cache", None)
    _ts    = getattr(app, "_ci_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    now = _dt.now()

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps = tkr.options
            if not exps: return None
            fomo_prem = 0.0; accum_prem = 0.0
            fomo_vol_prem = 0.0; accum_vol_prem = 0.0
            fomo_oi_prem  = 0.0; accum_oi_prem  = 0.0
            top_accum = {"strike": None, "expiry": None, "prem": 0.0, "otm_pct": 0.0}
            for exp in exps[:8]:
                try:
                    days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                    calls = tkr.option_chain(exp).calls
                    for _, row in calls.iterrows():
                        strike = float(row.get("strike", 0) or 0)
                        vol    = int(row.get("volume", 0) or 0)
                        oi     = int(row.get("openInterest", 0) or 0)
                        last   = float(row.get("lastPrice", 0) or 0)
                        if strike <= 0 or last <= 0: continue
                        otm_pct   = (strike - price) / price * 100
                        vol_prem  = vol * last * 100
                        oi_prem   = oi  * last * 100
                        prem      = vol_prem + oi_prem
                        if otm_pct > 5 and days_out > 60:
                            accum_prem     += prem
                            accum_vol_prem += vol_prem
                            accum_oi_prem  += oi_prem
                            if prem > top_accum["prem"]:
                                top_accum = {"strike": round(strike, 2), "expiry": exp,
                                             "prem": prem, "otm_pct": round(otm_pct, 1)}
                        elif -3 < otm_pct < 3 and days_out < 45:
                            fomo_prem     += prem
                            fomo_vol_prem += vol_prem
                            fomo_oi_prem  += oi_prem
                except Exception: continue
            total = fomo_prem + accum_prem
            if total < 1000: return None
            fomo_pct  = round(fomo_prem  / total * 100, 1)
            accum_pct = round(accum_prem / total * 100, 1)
            verdict   = ("ACCUMULATION" if accum_pct >= 60 else "FOMO" if fomo_pct >= 60 else "MIXED")

            # ── LEAPS WHALE SCANNER ──────────────────────────────────────────
            # Separate pass: scan ALL expirations 180–365 days out for a single
            # strike with ≥$10M in day's volume premium — institutional LEAPS block
            leaps_whale = {"strike": None, "expiry": None, "prem_m": 0.0,
                           "days_out": 0, "direction": "CALL"}
            for exp in exps:
                try:
                    days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                    if not (180 <= days_out <= 365): continue
                    for direction, chain in [("CALL", tkr.option_chain(exp).calls),
                                             ("PUT",  tkr.option_chain(exp).puts)]:
                        for _, row in chain.iterrows():
                            strike = float(row.get("strike", 0) or 0)
                            vol    = int(row.get("volume", 0) or 0)
                            last   = float(row.get("lastPrice", 0) or 0)
                            if strike <= 0 or last <= 0 or vol <= 0: continue
                            single_prem_m = vol * last * 100 / 1e6
                            if single_prem_m >= 10.0 and single_prem_m > leaps_whale["prem_m"]:
                                leaps_whale = {
                                    "strike":    round(strike, 2),
                                    "expiry":    exp,
                                    "prem_m":    round(single_prem_m, 1),
                                    "days_out":  days_out,
                                    "direction": direction,
                                }
                except Exception: continue
            # ─────────────────────────────────────────────────────────────────

            return {"ticker": ticker, "price": round(price, 2),
                    "fomo_prem_m":      round(fomo_prem      / 1e6, 2),
                    "accum_prem_m":     round(accum_prem     / 1e6, 2),
                    "accum_vol_m":      round(accum_vol_prem / 1e6, 2),
                    "accum_oi_m":       round(accum_oi_prem  / 1e6, 2),
                    "fomo_vol_m":       round(fomo_vol_prem  / 1e6, 2),
                    "fomo_oi_m":        round(fomo_oi_prem   / 1e6, 2),
                    "fomo_pct": fomo_pct, "accum_pct": accum_pct, "verdict": verdict,
                    "top_accum_strike": top_accum["strike"], "top_accum_expiry": top_accum["expiry"],
                    "top_accum_otm_pct": top_accum["otm_pct"],
                    "leaps_whale": leaps_whale if leaps_whale["strike"] else None}
        except Exception: return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
        rows = [r for fut in as_completed(futures) if (r := fut.result()) is not None]
    rows.sort(key=lambda x: x["accum_prem_m"], reverse=True)
    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    app._ci_cache = out; app._ci_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/smart-vs-retail", methods=["GET"])
def smart_vs_retail():
    """Compare large-block (institutional) vs small-contract (retail) options flow."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_svr_cache", None)
    _ts    = getattr(app, "_svr_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps = tkr.options
            if not exps: return None
            sc = 0.0; sp = 0.0; rc = 0.0; rp = 0.0
            for exp in exps[:4]:
                try:
                    chain = tkr.option_chain(exp)
                    for side, df in [("c", chain.calls), ("p", chain.puts)]:
                        for _, row in df.iterrows():
                            vol  = int(row.get("volume", 0) or 0)
                            last = float(row.get("lastPrice", 0) or 0)
                            if vol <= 0 or last <= 0: continue
                            prem = vol * last * 100
                            if last >= 3.0 and vol >= 30:
                                if side == "c": sc += prem
                                else:           sp += prem
                            elif last < 2.0 or vol < 15:
                                if side == "c": rc += prem
                                else:           rp += prem
                except Exception: continue
            if (sc + sp) < 1000 and (rc + rp) < 1000: return None
            s_cp = round(sc / sp, 2) if sp > 0 else 9.9
            r_cp = round(rc / rp, 2) if rp > 0 else 9.9
            sb = s_cp >= 1.5; sbear = s_cp <= 0.7
            rb = r_cp >= 1.5; rbear = r_cp <= 0.7
            if   sb    and rbear: div, strength = "SMART BULLISH",  "STRONG"
            elif sbear and rb:    div, strength = "SMART BEARISH",  "STRONG"
            elif sb    and not rb: div, strength = "SMART BULLISH", "MODERATE"
            elif sbear and not rbear: div, strength = "SMART BEARISH", "MODERATE"
            elif rb    and not sb: div, strength = "RETAIL BULLISH","MODERATE"
            elif rbear and not sbear: div, strength = "RETAIL BEARISH","MODERATE"
            elif sb or rb:         div, strength = "ALIGNED",       "WEAK"
            else:                  div, strength = "NEUTRAL",       "WEAK"
            return {"ticker": ticker, "price": round(price, 2),
                    "smart_prem_m": round((sc + sp) / 1e6, 2), "retail_prem_m": round((rc + rp) / 1e6, 2),
                    "smart_cp": s_cp, "retail_cp": r_cp, "divergence": div, "signal_strength": strength}
        except Exception: return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
        rows = [r for fut in as_completed(futures) if (r := fut.result()) is not None]
    rows.sort(key=lambda x: (x["signal_strength"] == "STRONG", x["smart_prem_m"]), reverse=True)
    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    app._svr_cache = out; app._svr_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/max-pain", methods=["GET"])
def max_pain():
    """Max pain strike for nearest expiry — where price tends to drift before expiration."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_mp_cache", None)
    _ts    = getattr(app, "_mp_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    now = _dt.now()

    def _max_pain(puts_df, calls_df):
        strikes = sorted(set(list(puts_df["strike"]) + list(calls_df["strike"])))
        if not strikes: return None
        best = strikes[0]; lo = float("inf")
        for s in strikes:
            cp = sum(max(0.0, float(s) - float(k)) * float(oi or 0)
                     for k, oi in zip(calls_df["strike"], calls_df["openInterest"].fillna(0)))
            pp = sum(max(0.0, float(k) - float(s)) * float(oi or 0)
                     for k, oi in zip(puts_df["strike"],  puts_df["openInterest"].fillna(0)))
            if (cp + pp) < lo: lo = cp + pp; best = s
        return float(best)

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps = tkr.options
            if not exps: return None
            exp  = exps[0]
            days = (_dt.strptime(exp, "%Y-%m-%d") - now).days
            chain = tkr.option_chain(exp)
            mp = _max_pain(chain.puts, chain.calls)
            if mp is None: return None
            dist = round((price - mp) / mp * 100, 2)
            return {"ticker": ticker, "price": round(price, 2), "max_pain": round(mp, 2),
                    "distance_pct": dist, "direction": "ABOVE PAIN" if dist > 0 else "BELOW PAIN",
                    "nearest_expiry": exp, "days_to_exp": days}
        except Exception: return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
        rows = [r for fut in as_completed(futures) if (r := fut.result()) is not None]
    rows.sort(key=lambda x: abs(x["distance_pct"]), reverse=True)
    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    app._mp_cache = out; app._mp_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/gamma-wall", methods=["GET"])
def gamma_wall():
    """OI by strike for major tickers — shows dealer gamma concentration and flip points."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_gw_cache", None)
    _ts    = getattr(app, "_gw_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA", "META", "AMZN", "MSFT", "GOOGL"]

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps  = tkr.options
            if not exps: return None
            exp   = exps[0]
            chain = tkr.option_chain(exp)
            puts  = chain.puts; calls = chain.calls
            lo = price * 0.90; hi = price * 1.10
            all_s = sorted(set(list(puts["strike"]) + list(calls["strike"])))
            strike_data = []; max_oi = 0; wall = price
            for s in all_s:
                if not (lo <= s <= hi): continue
                c_oi = int(calls[calls["strike"] == s]["openInterest"].sum() or 0)
                p_oi = int(puts[puts["strike"]   == s]["openInterest"].sum() or 0)
                tot  = c_oi + p_oi
                if tot > max_oi: max_oi = tot; wall = s
                strike_data.append({"strike": round(float(s), 2), "call_oi": c_oi,
                                     "put_oi": p_oi, "total_oi": tot, "net_gamma": c_oi - p_oi})
            if not strike_data: return None
            flip = None
            for i in range(1, len(strike_data)):
                if strike_data[i-1]["net_gamma"] >= 0 and strike_data[i]["net_gamma"] < 0:
                    flip = strike_data[i]["strike"]; break
            return {"ticker": ticker, "price": round(price, 2), "wall_strike": round(float(wall), 2),
                    "wall_distance_pct": round((float(wall) - price) / price * 100, 2),
                    "expiry": exp, "strikes": strike_data, "flip_strike": flip}
        except Exception: return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_analyze, t): t for t in TICKERS}
        rows = [r for fut in as_completed(futures) if (r := fut.result()) is not None]
    rows.sort(key=lambda x: x["ticker"])
    out = {"results": rows}
    app._gw_cache = out; app._gw_cache_ts = _dt.now()
    return jsonify(out)


def _enrich_technical_signals(tickers_data):
    """
    Optional enrichment: MACD, Support/Resistance, Volume Profile POC, and VWAP.
    Runs in-process on tickers that already have price data.
    All failures are silent — if this function crashes, nothing else breaks.
    """
    import sys
    try:
        import yfinance as yf
        import numpy as np
    except Exception:
        return

    candidates = [t for t, v in tickers_data.items() if v.get("price")]
    if not candidates:
        return

    print(f"[enrich_tech] enriching {len(candidates)} tickers with MACD/S-R/POC/VWAP", file=sys.stderr, flush=True)

    try:
        raw = yf.download(
            candidates,
            period="6mo",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )
    except Exception as e:
        print(f"[enrich_tech] batch download error: {e}", file=sys.stderr)
        return

    for ticker in candidates:
        try:
            v = tickers_data[ticker]
            price = float(v["price"])

            try:
                if len(candidates) == 1:
                    df = raw
                else:
                    lvl0 = raw.columns.get_level_values(0)
                    df = raw[ticker] if ticker in lvl0 else None
                if df is None or df.empty or len(df) < 30:
                    continue
                close = df["Close"].dropna()
                volume = df["Volume"].dropna()
                if len(close) < 30:
                    continue
            except Exception:
                continue

            # ── MACD (12/26/9 EMA) ────────────────────────────────────────────
            try:
                ema12 = close.ewm(span=12, adjust=False).mean()
                ema26 = close.ewm(span=26, adjust=False).mean()
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9, adjust=False).mean()
                macd_val  = float(macd_line.iloc[-1])
                sig_val   = float(signal_line.iloc[-1])
                macd_prev = float(macd_line.iloc[-2]) if len(macd_line) >= 2 else macd_val
                sig_prev  = float(signal_line.iloc[-2]) if len(signal_line) >= 2 else sig_val

                if macd_prev < sig_prev and macd_val > sig_val:
                    macd_status = "BULLISH_CROSS"
                elif macd_val > sig_val:
                    macd_status = "BULLISH"
                elif macd_prev > sig_prev and macd_val < sig_val:
                    macd_status = "BEARISH_CROSS"
                else:
                    macd_status = "BEARISH"

                macd_div = None
                if len(close) >= 20:
                    price_low_recent = float(close.iloc[-5:].min())
                    price_low_prior  = float(close.iloc[-20:-5].min())
                    macd_low_recent  = float(macd_line.iloc[-5:].min())
                    macd_low_prior   = float(macd_line.iloc[-20:-5].min())
                    if price_low_recent < price_low_prior and macd_low_recent > macd_low_prior:
                        macd_div = "BULLISH_DIV"
                    elif price_low_recent > price_low_prior and macd_low_recent < macd_low_prior:
                        macd_div = "BEARISH_DIV"

                v["tech_macd"] = macd_status
                if macd_div:
                    v["tech_macd_div"] = macd_div
            except Exception:
                pass

            # ── Support / Resistance (swing highs/lows, 60-day window) ────────
            try:
                window = 5
                recent_close = close.iloc[-60:] if len(close) >= 60 else close
                swing_lows, swing_highs = [], []
                for i in range(window, len(recent_close) - window):
                    val = float(recent_close.iloc[i])
                    if all(val <= float(recent_close.iloc[i - j]) for j in range(1, window + 1)) and \
                       all(val <= float(recent_close.iloc[i + j]) for j in range(1, window + 1)):
                        swing_lows.append(val)
                    if all(val >= float(recent_close.iloc[i - j]) for j in range(1, window + 1)) and \
                       all(val >= float(recent_close.iloc[i + j]) for j in range(1, window + 1)):
                        swing_highs.append(val)

                supports    = [s for s in swing_lows  if s < price * 0.99]
                resistances = [r for r in swing_highs if r > price * 1.01]

                sr_parts = []
                if supports:
                    nearest_sup = max(supports)
                    sup_dist = round((price - nearest_sup) / price * 100, 1)
                    v["tech_support_dist_pct"] = sup_dist
                    sr_parts.append("AT_SUPPORT" if sup_dist <= 2 else f"ABOVE_SUPPORT({sup_dist}%_below)")
                if resistances:
                    nearest_res = min(resistances)
                    res_dist = round((nearest_res - price) / price * 100, 1)
                    v["tech_resistance_dist_pct"] = res_dist
                    sr_parts.append(f"BELOW_RESISTANCE({res_dist}%_above)")
                if sr_parts:
                    v["tech_sr_context"] = "_".join(sr_parts)
            except Exception:
                pass

            # ── Volume Profile / Point of Control (90-day) ───────────────────
            try:
                vol_w   = volume.iloc[-90:] if len(volume) >= 90 else volume
                close_w = close.iloc[-90:]  if len(close)  >= 90 else close
                idx = vol_w.index.intersection(close_w.index)
                if len(idx) >= 20:
                    c_arr = close_w.loc[idx].values.astype(float)
                    v_arr = vol_w.loc[idx].values.astype(float)
                    bins  = 20
                    pmin, pmax = c_arr.min(), c_arr.max()
                    if pmax > pmin:
                        bsize = (pmax - pmin) / bins
                        vol_buckets = {}
                        for cv, vv in zip(c_arr, v_arr):
                            b = min(int((cv - pmin) / bsize), bins - 1)
                            vol_buckets[b] = vol_buckets.get(b, 0) + vv
                        poc_b     = max(vol_buckets, key=vol_buckets.get)
                        poc_price = pmin + (poc_b + 0.5) * bsize
                        poc_dist  = round((price - poc_price) / poc_price * 100, 1)
                        v["tech_poc_dist_pct"] = poc_dist
                        v["tech_poc_context"]  = (
                            "AT_POC(high_volume_magnet)" if abs(poc_dist) <= 2 else
                            f"ABOVE_POC({poc_dist:+.1f}%)" if poc_dist > 0 else
                            f"BELOW_POC({poc_dist:+.1f}%)"
                        )
            except Exception:
                pass

            # ── VWAP (20-day volume-weighted average price) ───────────────────
            try:
                if len(close) >= 20 and len(volume) >= 20:
                    c20 = close.iloc[-20:]
                    v20 = volume.iloc[-20:]
                    idx2 = c20.index.intersection(v20.index)
                    if len(idx2) >= 10:
                        c_vals = c20.loc[idx2].values.astype(float)
                        v_vals = v20.loc[idx2].values.astype(float)
                        vwap      = float(np.average(c_vals, weights=v_vals))
                        vwap_dist = round((price - vwap) / vwap * 100, 1)
                        v["tech_vwap_dist_pct"] = vwap_dist
                        v["tech_vwap_context"]  = (
                            "ABOVE_VWAP(buyers_in_control)" if vwap_dist >  1.5 else
                            "BELOW_VWAP(sellers_in_control)" if vwap_dist < -1.5 else
                            "AT_VWAP(decision_point)"
                        )
            except Exception:
                pass

        except Exception:
            continue

    print("[enrich_tech] done", file=sys.stderr, flush=True)


def _ai_trades_worker():
    """Background worker: generate AI trade setups and store in app._ait_cache."""
    import sys
    from datetime import datetime as _dt, date as _date, timedelta as _timedelta
    from openai import OpenAI

    if getattr(app, "_ait_generating", False):
        return
    app._ait_generating = True
    print("[ai_trades_bg] starting generation…", file=sys.stderr, flush=True)

    try:
        oai = OpenAI(
            base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL"),
            api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
            timeout=90.0,
        )
    except Exception as e:
        print(f"[ai_trades_bg] OpenAI init error: {e}", file=sys.stderr, flush=True)
        app._ait_generating = False
        return

    tickers_data = {}

    def _add(ticker, key, val):
        if val is None: return
        if ticker not in tickers_data:
            tickers_data[ticker] = {"ticker": ticker}
        if key not in tickers_data[ticker]:
            tickers_data[ticker][key] = val

    active_sources = []

    # 1. Composite Score Board — already aggregates many signals
    cs = getattr(app, "_cs_cache", None)
    if cs:
        active_sources.append("Composite Score Board")
        for r in cs.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "composite_score", r.get("score"))
            _add(t, "bias", r.get("bias"))
            comp = r.get("components", {})
            _add(t, "iv_rank", comp.get("iv_rank"))
            _add(t, "smart_cp", comp.get("smart_cp"))
            _add(t, "retail_cp", comp.get("retail_cp"))
            _add(t, "accum_pct", comp.get("accum_pct"))
            ta = comp.get("top_accum") or {}
            _add(t, "top_accum_strike", ta.get("strike"))
            _add(t, "top_accum_otm_pct", ta.get("otm_pct"))
            _add(t, "top_accum_expiry", ta.get("expiry"))
            _add(t, "nearest_exp", r.get("nearest_exp"))

    # 2. Vol Crush Detector (+ RSI, SMA50%, volume trend, analyst revisions, days-since-earnings,
    #    options liquidity, earnings beat streak, SPY beta)
    vc = getattr(app, "_vc_cache", None)
    if vc:
        active_sources.append("Vol Crush + Price Structure")
        for r in vc.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "iv_rank", r.get("iv_rank"))
            _add(t, "current_iv", r.get("current_iv"))
            _add(t, "hv_30", r.get("hv_30"))
            _add(t, "iv_verdict", r.get("verdict"))
            _add(t, "earnings_date", r.get("earnings_date"))
            _add(t, "days_since_earnings", r.get("days_since_earnings"))
            _add(t, "short_float_pct", r.get("short_float_pct"))
            _add(t, "short_ratio", r.get("short_ratio"))
            _add(t, "rsi", r.get("rsi"))
            _add(t, "sma50_pct", r.get("sma50_pct"))
            _add(t, "vol_trend_5d", r.get("vol_trend_5d"))
            _add(t, "net_upgrades_7d", r.get("net_upgrades_7d"))
            _add(t, "options_liquidity_pct", r.get("options_liquidity_pct"))
            _add(t, "earnings_beat_streak", r.get("earnings_beat_streak"))
            _add(t, "spy_beta", r.get("spy_beta"))
            _add(t, "iv_skew", r.get("iv_skew"))
            _add(t, "iv_term_structure", r.get("iv_term_structure"))
            _add(t, "gex_m", r.get("gex_m"))
            _add(t, "gex_regime", r.get("gex_regime"))
            _add(t, "iv_rv_premium", r.get("iv_rv_premium"))
            _add(t, "momentum_12_1", r.get("momentum_12_1"))
            _add(t, "factor_roe", r.get("factor_roe"))
            _add(t, "factor_fpe", r.get("factor_fpe"))
            _add(t, "sector_corr", r.get("sector_corr"))
            _add(t, "news_sentiment", r.get("news_sentiment"))
            _add(t, "news_headline", r.get("news_headline"))
            _add(t, "days_to_earnings", r.get("days_to_earnings"))
            _add(t, "analyst_target_pct", r.get("analyst_target_pct"))
            _add(t, "analyst_recommendation", r.get("analyst_recommendation"))
            _add(t, "put_call_oi_ratio", r.get("put_call_oi_ratio"))
            _add(t, "earnings_impl_move_pct", r.get("earnings_impl_move_pct"))
            _add(t, "call_vol_oi_ratio", r.get("call_vol_oi_ratio"))
            _add(t, "put_vol_oi_ratio", r.get("put_vol_oi_ratio"))
            _add(t, "week52_range_pct", r.get("week52_range_pct"))
            _add(t, "borrow_cost_proxy", r.get("borrow_cost_proxy"))
            _add(t, "eps_revision_trend", r.get("eps_revision_trend"))
            _add(t, "hist_earn_reaction_pct", r.get("hist_earn_reaction_pct"))
            _add(t, "squeeze_risk", r.get("squeeze_risk"))
            _add(t, "analyst_dispersion_pct", r.get("analyst_dispersion_pct"))
            _add(t, "pc_premium_ratio", r.get("pc_premium_ratio"))
            _add(t, "rs_vs_spy", r.get("rs_vs_spy"))
            _add(t, "money_flow_ratio", r.get("money_flow_ratio"))
            _add(t, "insider_net", r.get("insider_net"))
            _add(t, "div_yield_pct", r.get("div_yield_pct"))
            _add(t, "ex_div_days", r.get("ex_div_days"))
            _add(t, "tail_risk_put_pct", r.get("tail_risk_put_pct"))
            _add(t, "iv_skew_pctl", r.get("iv_skew_pctl"))
            _add(t, "short_float_trend", r.get("short_float_trend"))
            _add(t, "pc_ratio_trend", r.get("pc_ratio_trend"))
            _add(t, "instit_own_pct", r.get("instit_own_pct"))

    # 3. Call Intent Decoder
    ci = getattr(app, "_ci_cache", None)
    if ci:
        active_sources.append("Call Intent Decoder")
        for r in ci.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "call_verdict", r.get("verdict"))
            _add(t, "accum_pct", r.get("accum_pct"))
            _add(t, "fomo_pct", r.get("fomo_pct"))
            _add(t, "accum_prem_m", r.get("accum_prem_m"))
            _add(t, "top_accum_strike", r.get("top_accum_strike"))
            _add(t, "call_vol_oi", r.get("call_vol_oi"))
            _add(t, "top_accum_expiry", r.get("top_accum_expiry"))
            _add(t, "top_accum_otm_pct", r.get("top_accum_otm_pct"))

    # 4. Put Intent / Bear Flow
    oi = getattr(app, "_oi_cache", None)
    if oi:
        active_sources.append("Put Intent / Bear Flow")
        for r in oi.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "put_verdict", r.get("verdict"))
            _add(t, "hedge_pct", r.get("hedge_pct"))
            _add(t, "bear_pct", r.get("bear_pct"))
            _add(t, "top_bear_strike", r.get("top_bear_strike"))

    # 5. Smart vs Retail
    svr = getattr(app, "_svr_cache", None)
    if svr:
        active_sources.append("Smart vs Retail")
        for r in svr.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "smart_cp", r.get("smart_cp"))
            _add(t, "retail_cp", r.get("retail_cp"))
            _add(t, "divergence", r.get("divergence"))
            _add(t, "signal_strength", r.get("signal_strength"))
            _add(t, "smart_prem_m", r.get("smart_prem_m"))
            _add(t, "retail_prem_m", r.get("retail_prem_m"))

    # 6. Max Pain / Pinning
    mp = getattr(app, "_mp_cache", None)
    if mp:
        active_sources.append("Max Pain / Pinning")
        for r in mp.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "max_pain", r.get("max_pain"))
            _add(t, "mp_dist_pct", r.get("distance_pct"))
            _add(t, "mp_direction", r.get("direction"))
            _add(t, "nearest_exp", r.get("nearest_expiry"))
            _add(t, "days_to_exp", r.get("days_to_exp"))

    # 7. Gamma Wall
    gw = getattr(app, "_gw_cache", None)
    if gw:
        active_sources.append("Gamma Wall")
        for r in gw.get("results", []):
            t = r["ticker"]
            _add(t, "gamma_wall_strike", r.get("wall_strike"))
            _add(t, "gamma_wall_dist_pct", r.get("wall_distance_pct"))
            _add(t, "gamma_flip_strike", r.get("flip_strike"))

    # 8. Dark Pool
    dp = getattr(app, "_dp_cache", None)
    if dp:
        active_sources.append("Dark Pool Flow")
        for r in dp.get("results", []):
            t = r["ticker"]
            _add(t, "dark_pool_prem_m", r.get("premium_m"))
            _add(t, "dark_pool_cp_ratio", r.get("call_put_ratio"))

    # 9. Live Signal Feed (notable real-time alerts per ticker)
    sf = getattr(app, "_sf_cache", None)
    if sf:
        active_sources.append("Live Signal Feed")
        for ev in sf.get("events", []):
            t = ev["ticker"]
            if t not in tickers_data:
                tickers_data[t] = {"ticker": t}
            existing = tickers_data[t].get("live_alerts", [])
            existing.append(f"[{ev['type']}] {ev['msg']}")
            tickers_data[t]["live_alerts"] = existing

    # 10. Pre-market movers — inject gap direction per ticker
    pm = getattr(app, "_pm_cache", None)
    if pm:
        active_sources.append("Pre-market Movers")
        for r in pm.get("gainers", []) + pm.get("losers", []):
            t = r["ticker"]
            _add(t, "premarket_chg_pct", r.get("change_pct"))
            _add(t, "premarket_vol_ratio", r.get("vol_ratio"))

    # 11. Market overview — sector momentum + index context
    mo = getattr(app, "_mo_cache", None)
    sector_context = ""
    index_context = ""
    if mo:
        active_sources.append("Sector / Index Momentum")
        sectors_sorted = sorted(mo.get("sectors", []), key=lambda x: x.get("change_pct", 0), reverse=True)
        top2 = [f"{s['name']} {s['change_pct']:+.1f}%" for s in sectors_sorted[:2]]
        bot2 = [f"{s['name']} {s['change_pct']:+.1f}%" for s in sectors_sorted[-2:]]
        sector_context = f"Leading: {', '.join(top2)} | Lagging: {', '.join(bot2)}"
        spy = next((x for x in mo.get("indices", []) if x["ticker"] == "SPY"), None)
        qqq = next((x for x in mo.get("indices", []) if x["ticker"] == "QQQ"), None)
        vix = next((x for x in mo.get("indices", []) if x["ticker"] == "VIX"), None)
        idx_parts = []
        if spy: idx_parts.append(f"SPY {spy['change_pct']:+.2f}%")
        if qqq: idx_parts.append(f"QQQ {qqq['change_pct']:+.2f}%")
        if vix: idx_parts.append(f"VIX ${vix['price']:.1f}")
        index_context = " | ".join(idx_parts)
        ad = mo.get("advance_decline", {})
        if ad:
            index_context += f" | A/D {ad.get('up',0)}/{ad.get('down',0)}"

    # 12. Compute implied move per ticker from current_iv + days_to_exp
    for t, v in tickers_data.items():
        iv = v.get("current_iv")
        dte = v.get("days_to_exp")
        if iv and dte and dte > 0:
            impl_move = round(float(iv) / 100 * (float(dte) / 252) ** 0.5 * 100, 1)
            _add(t, "implied_move_pct", impl_move)

    # 13. Macro calendar — days to next key market events
    def _macro_context():
        from datetime import date as _date
        today = _date.today()
        FED_DATES_2026 = [
            _date(2026, 1, 29), _date(2026, 3, 19), _date(2026, 5, 7),
            _date(2026, 6, 18), _date(2026, 7, 29), _date(2026, 9, 17),
            _date(2026, 11, 5), _date(2026, 12, 10),
        ]
        CPI_APPROX_2026 = [
            _date(2026, 1, 14), _date(2026, 2, 11), _date(2026, 3, 11),
            _date(2026, 4, 10), _date(2026, 5, 13), _date(2026, 6, 10),
            _date(2026, 7, 14), _date(2026, 8, 12), _date(2026, 9, 9),
            _date(2026, 10, 14), _date(2026, 11, 12), _date(2026, 12, 9),
        ]
        import calendar as _cal
        def _next_monthly_opex():
            y, m = today.year, today.month
            for _ in range(3):
                weeks = _cal.monthcalendar(y, m)
                fridays = [w[4] for w in weeks if w[4] != 0]
                opex = _date(y, m, fridays[2])
                if opex >= today: return opex
                m += 1
                if m > 12: m = 1; y += 1
            return None
        parts = []
        fed_next = next((d for d in sorted(FED_DATES_2026) if d >= today), None)
        if fed_next: parts.append(f"Fed={( fed_next - today).days}d")
        cpi_next = next((d for d in sorted(CPI_APPROX_2026) if d >= today), None)
        if cpi_next: parts.append(f"CPI={( cpi_next - today).days}d")
        opex = _next_monthly_opex()
        if opex: parts.append(f"OPEX={( opex - today).days}d")
        return " | ".join(parts) if parts else ""
    macro_context = _macro_context()

    # 14. Multi-day signal persistence — query signal_history for 3-day rolling confirmation
    try:
        with _psycopg2.connect(_DB_URL) as _ph_conn, _ph_conn.cursor() as _ph_cur:
            _ph_cur.execute("""
                SELECT ticker,
                       COUNT(DISTINCT signal_date) AS days,
                       AVG(comp_score)              AS avg_score
                FROM signal_history
                WHERE signal_date >= CURRENT_DATE - INTERVAL '5 days'
                  AND (comp_score >= 60
                       OR call_verdict IN ('HEAVY_ACCUMULATION','STRONG_ACCUMULATION','ACCUMULATION'))
                GROUP BY ticker
                HAVING COUNT(DISTINCT signal_date) >= 2
            """)
            _ph_rows = _ph_cur.fetchall()
        if _ph_rows:
            active_sources.append("Multi-day Signal Persistence")
            for _ph_t, _ph_days, _ph_avg in _ph_rows:
                _add(_ph_t, "persistence_days", int(_ph_days))
                _add(_ph_t, "persistence_avg_score", round(float(_ph_avg or 0), 1))
    except Exception:
        pass

    # 14b. Sector ETF flow confirmation — institutional call buying confirmed at the sector level
    _sector_etf_bullish: set = set()
    try:
        with _psycopg2.connect(_DB_URL) as _se_conn, _se_conn.cursor() as _se_cur:
            _se_cur.execute("""
                SELECT DISTINCT ticker
                FROM unusual_calls_log
                WHERE ticker IN ('XLK','XLF','XLV','XLE','XLI','XLY','XLC','XLB','XLP','XLU','XLRE','QQQ','IWM')
                  AND last_seen >= NOW() - INTERVAL '28 hours'
                  AND prem >= 500000
            """)
            for (_se_etf,) in _se_cur.fetchall():
                _sector_etf_bullish.add(_se_etf)
    except Exception:
        pass

    _TICKER_TO_ETF_MAP = {
        "AAPL":"XLK","MSFT":"XLK","NVDA":"XLK","GOOGL":"XLK","META":"XLK","AMD":"XLK",
        "INTC":"XLK","MU":"XLK","ORCL":"XLK","CRM":"XLK","ADBE":"XLK","QCOM":"XLK",
        "TXN":"XLK","AVGO":"XLK","AMAT":"XLK","LRCX":"XLK","KLAC":"XLK","NOW":"XLK",
        "JPM":"XLF","BAC":"XLF","GS":"XLF","WFC":"XLF","MS":"XLF","C":"XLF",
        "BLK":"XLF","V":"XLF","MA":"XLF","AXP":"XLF","SCHW":"XLF","COF":"XLF",
        "JNJ":"XLV","UNH":"XLV","MRNA":"XLV","PFE":"XLV","ABBV":"XLV","LLY":"XLV",
        "AMGN":"XLV","GILD":"XLV","CVS":"XLV","BMY":"XLV","MDT":"XLV","ISRG":"XLV",
        "XOM":"XLE","CVX":"XLE","SLB":"XLE","OXY":"XLE","MPC":"XLE","COP":"XLE","HAL":"XLE",
        "LMT":"XLI","CAT":"XLI","BA":"XLI","GE":"XLI","HON":"XLI","RTX":"XLI","NOC":"XLI","DE":"XLI",
        "AMZN":"XLY","TSLA":"XLY","COST":"XLY","MCD":"XLY","NKE":"XLY","HD":"XLY","LOW":"XLY","GM":"XLY",
        "NFLX":"XLC","DIS":"XLC","CMCSA":"XLC","T":"XLC","VZ":"XLC","ATVI":"XLC","EA":"XLC",
        "NEE":"XLU","DUK":"XLU","SO":"XLU","AEP":"XLU","D":"XLU",
        "PLD":"XLRE","AMT":"XLRE","SPG":"XLRE","EQIX":"XLRE",
        "APD":"XLB","LIN":"XLB","ECL":"XLB","NEM":"XLB","FCX":"XLB",
    }
    if _sector_etf_bullish:
        active_sources.append("Sector ETF Flow")
        for _se_t in list(tickers_data.keys()):
            _etf_match = _TICKER_TO_ETF_MAP.get(_se_t)
            if _etf_match and _etf_match in _sector_etf_bullish:
                _add(_se_t, "sector_etf_flow", f"CONFIRMED({_etf_match}_bullish)")

    # 14c. Dark pool premium trend (3-day) — is institutional DP activity accelerating or fading?
    try:
        with _psycopg2.connect(_DB_URL) as _dpt_conn, _dpt_conn.cursor() as _dpt_cur:
            _dpt_cur.execute("""
                SELECT ticker, signal_date, dp_prem_m
                FROM signal_history
                WHERE dp_prem_m IS NOT NULL AND dp_prem_m > 0
                  AND signal_date >= CURRENT_DATE - INTERVAL '5 days'
                ORDER BY ticker, signal_date
            """)
            _dpt_rows = _dpt_cur.fetchall()
        _dp_hist: dict = {}
        for _dpt_t, _dpt_d, _dpt_v in _dpt_rows:
            if _dpt_t not in _dp_hist:
                _dp_hist[_dpt_t] = []
            _dp_hist[_dpt_t].append(float(_dpt_v))
        if _dp_hist:
            active_sources.append("Dark Pool Trend")
            for _dpt_t, _dpt_vals in _dp_hist.items():
                if len(_dpt_vals) >= 2:
                    _dp_today_v = _dpt_vals[-1]
                    _dp_prior_v = sum(_dpt_vals[:-1]) / len(_dpt_vals[:-1])
                    if _dp_today_v >= _dp_prior_v * 1.25:
                        _dp_tag = "ACCELERATING"
                    elif _dp_today_v <= _dp_prior_v * 0.75:
                        _dp_tag = "FADING"
                    else:
                        _dp_tag = "STEADY"
                    _add(_dpt_t, "dp_trend", _dp_tag)
                    _add(_dpt_t, "dp_3d_avg_m", round(_dp_prior_v, 2))
    except Exception:
        pass

    # 14d. Multi-day UC flow streak — same unusual call contract returning on 3+ distinct days
    try:
        with _psycopg2.connect(_DB_URL) as _uc_conn, _uc_conn.cursor() as _uc_cur:
            _uc_cur.execute("""
                SELECT ticker,
                       MAX(EXTRACT(EPOCH FROM (last_seen - first_seen)) / 86400.0) AS max_streak_days,
                       COUNT(*) AS active_contracts
                FROM unusual_calls_log
                WHERE last_seen >= NOW() - INTERVAL '36 hours'
                  AND first_seen <= NOW() - INTERVAL '48 hours'
                GROUP BY ticker
                HAVING MAX(EXTRACT(EPOCH FROM (last_seen - first_seen)) / 86400.0) >= 2
            """)
            _uc_streak_rows = _uc_cur.fetchall()
        if _uc_streak_rows:
            active_sources.append("Multi-day UC Streak")
            for _us_t, _us_days, _us_contracts in _uc_streak_rows:
                _add(_us_t, "uc_streak_days", round(float(_us_days), 1))
                _add(_us_t, "uc_streak_contracts", int(_us_contracts))
    except Exception:
        pass

    # 15. Market regime detection — VIX level + SPY 5d/20d trend
    market_regime = "UNKNOWN"
    try:
        import yfinance as _yf_mr
        _mr_data = _yf_mr.download(["^VIX", "SPY"], period="30d", interval="1d", progress=False, auto_adjust=True)["Close"]
        _vix_ser = _mr_data["^VIX"].dropna()
        _spy_ser = _mr_data["SPY"].dropna()
        vix_now = float(_vix_ser.iloc[-1]) if len(_vix_ser) >= 1 else 20.0
        if len(_spy_ser) >= 10:
            spy_5d_chg = float(_spy_ser.iloc[-1]) / float(_spy_ser.iloc[-6]) - 1
            spy_20d_chg = float(_spy_ser.iloc[-1]) / float(_spy_ser.iloc[0]) - 1
            if vix_now > 30:
                market_regime = f"HIGH_FEAR(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
            elif vix_now > 20:
                if spy_5d_chg > 0.01:
                    market_regime = f"RECOVERY(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
                elif spy_5d_chg < -0.01:
                    market_regime = f"CORRECTION(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
                else:
                    market_regime = f"CHOP(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
            else:
                if spy_5d_chg > 0.01:
                    market_regime = f"BULL_TREND(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%,20d={spy_20d_chg*100:+.1f}%)"
                elif spy_5d_chg < -0.02:
                    market_regime = f"PULLBACK(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
                else:
                    market_regime = f"RANGING(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
        active_sources.append(f"Market Regime")
    except Exception:
        market_regime = "UNKNOWN"

    # 16. Self-learning win rates from ai_trade_log (which setup_types + directions actually work)
    win_rate_context = ""
    try:
        with _psycopg2.connect(_DB_URL) as _wl_conn, _wl_conn.cursor() as _wl_cur:
            _wl_cur.execute("""
                SELECT setup_type, direction,
                       COUNT(*) FILTER (WHERE outcome = 'WIN')  AS wins,
                       COUNT(*) FILTER (WHERE outcome = 'LOSS') AS losses,
                       COUNT(*) AS total
                FROM ai_trade_log
                WHERE outcome IN ('WIN','LOSS')
                GROUP BY setup_type, direction
                HAVING COUNT(*) >= 3
                ORDER BY (COUNT(*) FILTER (WHERE outcome = 'WIN'))::float / NULLIF(COUNT(*),0) DESC
            """)
            _wl_rows = _wl_cur.fetchall()
        if _wl_rows:
            active_sources.append("Historical Win Rates")
            _wl_parts = []
            for _s_type, _s_dir, _s_wins, _s_losses, _s_total in _wl_rows:
                _wr = round(_s_wins / _s_total * 100)
                _wl_parts.append(f"{_s_type}({_s_dir})={_wr}%_wr({_s_wins}/{_s_total})")
            win_rate_context = " | ".join(_wl_parts)
    except Exception:
        win_rate_context = ""

    # 16b. Signal combination win rates — which signal tags predict wins from historical trade log
    combo_win_context = ""
    try:
        with _psycopg2.connect(_DB_URL) as _cw_conn, _cw_conn.cursor() as _cw_cur:
            _cw_cur.execute("""
                SELECT signals_aligned, outcome
                FROM ai_trade_log
                WHERE outcome IN ('WIN','LOSS')
                  AND signals_aligned IS NOT NULL
                  AND jsonb_array_length(signals_aligned) > 0
                ORDER BY trade_date DESC
                LIMIT 300
            """)
            _cw_rows = _cw_cur.fetchall()
        if len(_cw_rows) >= 5:
            _combo_tags = [
                ("persist3d+",  lambda ss: any("persist=" in s and s.split("persist=")[-1].split("d")[0].isdigit() and int(s.split("persist=")[-1].split("d")[0]) >= 3 for s in ss if "persist=" in s)),
                ("MACD_CROSS",  lambda ss: any("BULLISH_CROSS" in s for s in ss)),
                ("ABOVE_POC",   lambda ss: any("ABOVE_POC" in s for s in ss)),
                ("ABOVE_VWAP",  lambda ss: any("ABOVE_VWAP" in s for s in ss)),
                ("PC_BULLISH",  lambda ss: any("BULLISH_ROTATION" in s for s in ss)),
                ("INST_LOADED", lambda ss: any("HIGH_CONVICTION" in s for s in ss)),
                ("UC_STREAK",   lambda ss: any("uc_streak" in s for s in ss)),
                ("SECTOR_CONF", lambda ss: any("SECTOR_CONFIRMED" in s or "sector_etf_flow" in s for s in ss)),
                ("SHORTS_EXIT", lambda ss: any("SHORTS_COVERING" in s for s in ss)),
            ]
            _cstats = {k: [0, 0] for k, _ in _combo_tags}
            for _sa, _oc in _cw_rows:
                _sigs = [str(s) for s in (_sa if isinstance(_sa, list) else [])]
                for _tag, _fn in _combo_tags:
                    try:
                        if _fn(_sigs):
                            _cstats[_tag][1] += 1
                            if _oc == "WIN":
                                _cstats[_tag][0] += 1
                    except Exception:
                        pass
            _cparts = []
            for _tag, _ in _combo_tags:
                _w, _tot = _cstats[_tag]
                if _tot >= 3:
                    _cparts.append(f"{_tag}:{round(_w / _tot * 100)}%({_w}/{_tot})")
            if _cparts:
                combo_win_context = "SIGNAL_COMBO_WIN_RATES: " + " | ".join(_cparts)
    except Exception:
        combo_win_context = ""

    # 17. Macro cross-asset signals (yield curve, DXY, credit spreads, crude, gold)
    macro_cross_asset = ""
    try:
        import yfinance as _yf_macro
        _mcr = _yf_macro.download(
            ["^TNX", "^IRX", "UUP", "HYG", "LQD", "USO", "GLD", "^VIX", "^VIX3M"],
            period="5d", interval="1d", progress=False, auto_adjust=True
        )["Close"]
        def _mlast(sym):
            try:
                col = _mcr[sym].dropna() if hasattr(_mcr, "columns") and sym in _mcr.columns else None
                return float(col.iloc[-1]) if col is not None and len(col) > 0 else None
            except Exception: return None
        def _m5d(sym):
            try:
                col = _mcr[sym].dropna() if hasattr(_mcr, "columns") and sym in _mcr.columns else None
                return round((float(col.iloc[-1]) / float(col.iloc[0]) - 1) * 100, 1) if col is not None and len(col) >= 2 else None
            except Exception: return None
        _tnx = _mlast("^TNX"); _irx = _mlast("^IRX"); _uup5 = _m5d("UUP")
        _hyg5 = _m5d("HYG"); _lqd5 = _m5d("LQD")
        _uso5 = _m5d("USO"); _gld5 = _m5d("GLD")
        _parts17 = []
        if _tnx is not None and _irx is not None:
            _curve = round(_tnx - _irx, 2)
            _ctag = "INVERTED(recession_risk)" if _curve < 0 else "STEEP(risk_on)" if _curve > 1.5 else "FLAT"
            _parts17.append(f"YieldCurve(10y-3m)={_curve:+.2f}%({_ctag})")
        if _uup5 is not None:
            _dxy_tag = "STRONG_USD(headwind_equities)" if _uup5 > 0.5 else "WEAK_USD(tailwind_equities)" if _uup5 < -0.5 else "STABLE"
            _parts17.append(f"USD5d={_uup5:+.2f}%({_dxy_tag})")
        if _hyg5 is not None and _lqd5 is not None:
            _cs = round(_hyg5 - _lqd5, 2)
            _cstag = "WIDENING(risk_off)" if _cs < -0.3 else "TIGHTENING(risk_on)" if _cs > 0.3 else "STABLE"
            _parts17.append(f"CreditSpread5d={_cs:+.2f}%({_cstag})")
        if _uso5 is not None:
            _parts17.append(f"Crude5d={_uso5:+.1f}%")
        if _gld5 is not None:
            _gld_tag = "FLIGHT_TO_SAFETY" if _gld5 > 1.5 else "risk_on_rotation" if _gld5 < -1.5 else ""
            _parts17.append(f"Gold5d={_gld5:+.1f}%" + (f"({_gld_tag})" if _gld_tag else ""))
        _vix_lvl  = _mlast("^VIX")
        _vix3m_lvl = _mlast("^VIX3M")
        if _vix_lvl is not None and _vix3m_lvl is not None and _vix3m_lvl > 0:
            _vts = round(_vix_lvl - _vix3m_lvl, 2)
            _vts_tag = ("BACKWARDATION(crisis/event_risk)" if _vts > 2
                        else "contango(calm/risk_on)" if _vts < -1
                        else "flat")
            _parts17.append(f"VIX_TermStructure={_vts:+.2f}({_vts_tag})")
        if _parts17:
            macro_cross_asset = " | ".join(_parts17)
            active_sources.append("Macro Cross-Asset")
    except Exception:
        macro_cross_asset = ""

    # 18. Unusual Calls — inject per-ticker best premium entry (highest single-strike premium)
    uc = getattr(app, "_unusual_calls_cache", None)
    if uc:
        active_sources.append("Unusual Call Flow")
        uc_by_ticker = {}
        for hit in uc.get("hits", []):
            t = hit["ticker"]
            prem = hit.get("prem", 0)
            if t not in uc_by_ticker or prem > uc_by_ticker[t].get("prem", 0):
                uc_by_ticker[t] = hit
        for t, hit in uc_by_ticker.items():
            if t not in tickers_data:
                tickers_data[t] = {"ticker": t}
            _add(t, "uc_prem_m", round(hit["prem"] / 1_000_000, 2))
            _add(t, "uc_vol_oi", hit.get("vol_oi"))
            _add(t, "uc_strike", hit.get("strike"))
            _add(t, "uc_expiry", hit.get("expiry"))
            _add(t, "uc_otm_pct", hit.get("otm_pct"))
            _add(t, "uc_urgency", hit.get("urgency"))

    # Optional technical enrichment: MACD, Support/Resistance, Volume Profile POC, VWAP
    # Runs silently — any failure leaves existing signals untouched
    try:
        _enrich_technical_signals(tickers_data)
    except Exception as _et_err:
        import sys as _sys
        print(f"[ai_trades_bg] tech enrichment skipped: {_et_err}", file=_sys.stderr)

    # Only use tickers where we have enough signal depth (3+ fields beyond ticker key)
    rich = {t: v for t, v in tickers_data.items() if len(v) >= 3}

    # Helper: warm all tab caches in background threads via internal HTTP
    def _start_cache_warming():
        if getattr(app, "_cache_warming", False):
            return
        app._cache_warming = True
        def _do_warm():
            try:
                import urllib.request as _ur
                warm_paths = [
                    f"/stock-api/vol-crush",
                    f"/stock-api/call-intent",
                    f"/stock-api/options-intent",
                    f"/stock-api/smart-vs-retail",
                    f"/stock-api/max-pain",
                    f"/stock-api/gamma-wall",
                    f"/stock-api/darkpool",
                    f"/stock-api/premarket",
                    f"/stock-api/market/overview",
                    f"/stock-api/signal-feed",
                    f"/stock-api/composite-score",
                ]
                def _fetch(path):
                    try: _ur.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=90)
                    except Exception: pass
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futs = [ex.submit(_fetch, p) for p in warm_paths]
                    for f in as_completed(futs):
                        try: f.result()
                        except: pass
                # After warming, retry AI trades generation once
                import time as _time
                _time.sleep(2)
                if not getattr(app, "_ait_generating", False):
                    import threading as _thr2
                    _thr2.Thread(target=_ai_trades_worker, daemon=True).start()
            finally:
                app._cache_warming = False
        import threading
        threading.Thread(target=_do_warm, daemon=True).start()

    # Need at least 2 signal sources AND some rich tickers to proceed
    if len(active_sources) < 2 or len(rich) < 3:
        already_warming = getattr(app, "_cache_warming", False)
        if not already_warming:
            _start_cache_warming()
        import sys
        print(f"[ai_trades_bg] not enough signals ({len(active_sources)} sources, {len(rich)} tickers) — aborting", file=sys.stderr)
        app._ait_generating = False
        return  # background worker exits; HTTP handler will return loading state

    # Sort by composite score descending, fall back to alphabetical
    sorted_tickers = sorted(rich.values(), key=lambda x: x.get("composite_score", 50), reverse=True)

    # Premium gate: tickers with $500K+ unusual call premium come first (sorted by premium size).
    # Only fall back to non-premium tickers if fewer than 5 premium tickers exist.
    uc_qualified   = sorted(
        [v for v in rich.values() if (v.get("uc_prem_m") or 0) >= 0.5],
        key=lambda x: x.get("uc_prem_m", 0), reverse=True
    )
    uc_fallback    = [v for v in sorted_tickers if (v.get("uc_prem_m") or 0) < 0.5]
    candidate_pool = uc_qualified + uc_fallback  # AI sees premium tickers first

    # Build compact signal block — top 15 tickers, one line each, key fields only
    sig_lines = []
    for v in candidate_pool[:15]:
        parts = [f"{v['ticker']} ${v.get('price','?')}"]
        if v.get("composite_score") is not None:
            parts.append(f"score={v['composite_score']}/100({v.get('bias','?')})")
        if v.get("persistence_days") is not None:
            parts.append(f"persist={v['persistence_days']}d(avg_score={v.get('persistence_avg_score','?')})")
        if v.get("iv_rank") is not None:
            parts.append(f"iv_rank={v['iv_rank']}%({v.get('iv_verdict','')})")
        if v.get("implied_move_pct") is not None:
            parts.append(f"impl_move=±{v['implied_move_pct']}%")
        if v.get("rsi") is not None:
            rsi_tag = "overbought" if v["rsi"] > 70 else "oversold" if v["rsi"] < 30 else "neutral"
            parts.append(f"rsi={v['rsi']}({rsi_tag})")
        if v.get("sma50_pct") is not None:
            parts.append(f"sma50={v['sma50_pct']:+.1f}%")
        if v.get("vol_trend_5d") is not None:
            vt = v["vol_trend_5d"]
            vt_tag = "surging" if vt >= 1.5 else "declining" if vt < 0.7 else "normal"
            parts.append(f"vol_trend={vt}x({vt_tag})")
        if v.get("divergence"):
            parts.append(f"SmartVsRetail={v['divergence']}({v.get('signal_strength','?')}) scp={v.get('smart_cp','?')} rcp={v.get('retail_cp','?')}")
        if v.get("call_verdict"):
            vol_oi_tag = f" vol/oi={v['call_vol_oi']}x" if v.get("call_vol_oi") else ""
            parts.append(f"calls={v['call_verdict']} accum={v.get('accum_pct','?')}%{vol_oi_tag}")
        if v.get("put_verdict"):
            parts.append(f"puts={v['put_verdict']} bear={v.get('bear_pct','?')}%")
        if v.get("max_pain"):
            parts.append(f"mp=${v['max_pain']}({v.get('mp_dist_pct',0):+.1f}% {v.get('mp_direction','?')} {v.get('days_to_exp','?')}d)")
        if v.get("gamma_wall_strike"):
            parts.append(f"gwall=${v['gamma_wall_strike']}({v.get('gamma_wall_dist_pct',0):+.1f}%)")
        if v.get("dark_pool_prem_m"):
            parts.append(f"dp=${v['dark_pool_prem_m']}M cp={v.get('dark_pool_cp_ratio','?')}")
        if v.get("uc_prem_m") is not None:
            pm = v["uc_prem_m"]
            pm_tag = "WHALE" if pm >= 5 else "INSTITUTIONAL" if pm >= 1 else "NOTABLE"
            parts.append(f"uc_prem=${pm}M({pm_tag}) uc_vol_oi={v.get('uc_vol_oi','?')}x strike={v.get('uc_strike','?')} exp={v.get('uc_expiry','?')} otm={v.get('uc_otm_pct','?')}%({v.get('uc_urgency','?')})")
        if v.get("top_accum_strike"):
            parts.append(f"topstrike=${v['top_accum_strike']} exp={v.get('top_accum_expiry','?')}")
        if v.get("days_since_earnings") is not None:
            parts.append(f"post_earnings={v['days_since_earnings']}d_ago(IV_crush_window)")
        elif v.get("earnings_date"):
            parts.append(f"earnings={v['earnings_date']}")
        if v.get("net_upgrades_7d") is not None and v["net_upgrades_7d"] != 0:
            tag = f"+{v['net_upgrades_7d']} upgrades" if v["net_upgrades_7d"] > 0 else f"{v['net_upgrades_7d']} downgrades"
            parts.append(f"analysts({tag}_7d)")
        if v.get("short_float_pct") is not None:
            si_str = f"short={v['short_float_pct']}%"
            if v.get("short_ratio") is not None:
                si_str += f"/{v['short_ratio']}d-to-cover"
            parts.append(si_str)
        if v.get("premarket_chg_pct") is not None:
            vol_tag = f" vol×{v['premarket_vol_ratio']}" if v.get("premarket_vol_ratio") else ""
            parts.append(f"premarket={v['premarket_chg_pct']:+.2f}%{vol_tag}")
        if v.get("options_liquidity_pct") is not None:
            liq = v["options_liquidity_pct"]
            liq_tag = "liquid" if liq < 5 else "ILLIQUID_AVOID" if liq > 12 else "ok"
            parts.append(f"opt_spread={liq}%({liq_tag})")
        if v.get("earnings_beat_streak"):
            parts.append(f"earn_beat={v['earnings_beat_streak']}_qtrs")
        if v.get("spy_beta") is not None:
            b = v["spy_beta"]
            b_tag = "high_beta" if b >= 1.5 else "low_beta" if b <= 0.6 else ""
            parts.append(f"beta={b}x" + (f"({b_tag})" if b_tag else ""))
        if v.get("iv_skew") is not None:
            sk = v["iv_skew"]
            sk_tag = "FEAR_PREMIUM" if sk > 8 else "CALL_SKEW(demand)" if sk < -3 else "balanced"
            parts.append(f"iv_skew={sk:+.1f}pp({sk_tag})")
        if v.get("iv_term_structure") is not None:
            ts = v["iv_term_structure"]
            ts_tag = "BACKWARDATION(event_risk)" if ts > 5 else "contango(calm)" if ts < -3 else "flat"
            parts.append(f"iv_ts={ts:+.1f}pp({ts_tag})")
        if v.get("gex_m") is not None:
            gr = v.get("gex_regime", "")
            parts.append(f"GEX=${v['gex_m']}M({gr})")
        if v.get("iv_rv_premium") is not None:
            ivp = v["iv_rv_premium"]
            ivp_tag = "RICH_SELL_PREM" if ivp > 20 else "CHEAP_BUY_VOL" if ivp < -10 else "fair"
            parts.append(f"iv_rv={ivp:+.1f}%({ivp_tag})")
        if v.get("momentum_12_1") is not None:
            mo = v["momentum_12_1"]
            mo_tag = "strong_momentum" if mo > 15 else "weak_momentum" if mo < -15 else "neutral"
            parts.append(f"mom12_1={mo:+.1f}%({mo_tag})")
        if v.get("factor_roe") is not None:
            parts.append(f"ROE={v['factor_roe']}%")
        if v.get("factor_fpe") is not None:
            fpe = v["factor_fpe"]
            fpe_tag = "CHEAP" if fpe < 15 else "EXPENSIVE" if fpe > 35 else "fair"
            parts.append(f"fwd_PE={fpe}x({fpe_tag})")
        if v.get("sector_corr") is not None:
            sc = v["sector_corr"]
            sc_tag = "IDIOSYNCRATIC" if sc < 0.5 else "sector_driven" if sc > 0.85 else ""
            parts.append(f"sector_corr={sc}" + (f"({sc_tag})" if sc_tag else ""))
        if v.get("news_sentiment") is not None:
            ns = v["news_sentiment"]
            ns_tag = "BULLISH_NEWS" if ns > 0.5 else "BEARISH_NEWS" if ns < -0.5 else "neutral_news"
            hdl = f" [{v['news_headline'][:50]}]" if v.get("news_headline") else ""
            parts.append(f"news={ns}({ns_tag}){hdl}")
        if v.get("days_to_earnings") is not None:
            dte_val = v["days_to_earnings"]
            dte_tag = "IMMINENT(<7d)" if dte_val <= 7 else "SOON(<30d)" if dte_val <= 30 else f"{dte_val}d_away"
            earn_part = f"earn_in={dte_val}d({dte_tag})"
            if v.get("earnings_impl_move_pct") is not None:
                earn_part += f" impl_earn_move=±{v['earnings_impl_move_pct']}%"
            parts.append(earn_part)
        if v.get("analyst_target_pct") is not None:
            tgt = v["analyst_target_pct"]
            tgt_tag = "STRONG_BUY_CONSENSUS" if tgt > 25 else "BUY_CONSENSUS" if tgt > 10 else "FULLY_VALUED" if tgt < 0 else "modest_upside"
            rec_str = f"/{v['analyst_recommendation']}" if v.get("analyst_recommendation") else ""
            parts.append(f"analyst_tgt={tgt:+.1f}%{rec_str}({tgt_tag})")
        if v.get("put_call_oi_ratio") is not None:
            pcoi = v["put_call_oi_ratio"]
            pcoi_tag = "HEAVY_PUT_OI(bearish_positioning)" if pcoi > 1.5 else "HEAVY_CALL_OI(bullish_positioning)" if pcoi < 0.6 else "balanced_OI"
            parts.append(f"pc_oi_ratio={pcoi}({pcoi_tag})")
        if v.get("week52_range_pct") is not None:
            w52 = v["week52_range_pct"]
            w52_tag = "NEAR_52W_HIGH(breakout_zone)" if w52 >= 90 else "NEAR_52W_LOW(support_bounce)" if w52 <= 10 else "mid_range"
            parts.append(f"52w_range={w52:.0f}%({w52_tag})")
        if v.get("borrow_cost_proxy") in ("HIGH_BORROW", "ELEVATED_BORROW"):
            parts.append(f"borrow={v['borrow_cost_proxy']}(puts_may_be_synthetic_shorts)")
        if v.get("call_vol_oi_ratio") is not None and v.get("put_vol_oi_ratio") is not None:
            cvoi = v["call_vol_oi_ratio"]; pvoi = v["put_vol_oi_ratio"]
            c_tag = "FRESH(one_day)" if cvoi > 0.25 else "STRUCTURAL(multi_week)" if cvoi < 0.05 else "mixed"
            p_tag = "FRESH(one_day)" if pvoi > 0.25 else "STRUCTURAL(multi_week)" if pvoi < 0.05 else "mixed"
            parts.append(f"flow_persist(calls={cvoi}/{c_tag} puts={pvoi}/{p_tag})")
        if v.get("eps_revision_trend"):
            parts.append(f"eps_trend={v['eps_revision_trend']}")
        if v.get("hist_earn_reaction_pct") is not None:
            her = v["hist_earn_reaction_pct"]
            her_tag = "LARGE_MOVER" if her >= 8 else "moderate" if her >= 3 else "small_mover"
            parts.append(f"hist_earn_move=±{her}%({her_tag})")
        if v.get("squeeze_risk") in ("HIGH", "EXTREME"):
            sq = v["squeeze_risk"]
            parts.append(f"squeeze_risk={sq}(short_squeeze_imminent_danger_for_bears)")
        if v.get("analyst_dispersion_pct") is not None:
            ad = v["analyst_dispersion_pct"]
            ad_tag = "HIGH_DISAGREEMENT(prefer_straddle)" if ad >= 30 else "MODERATE_DISAGREEMENT" if ad >= 15 else "CONSENSUS(directional_ok)"
            parts.append(f"analyst_dispersion={ad}%({ad_tag})")
        if v.get("pc_premium_ratio") is not None:
            pcp = v["pc_premium_ratio"]
            pcp_tag = "HEAVY_PUT_SPEND(institutional_fear)" if pcp > 1.5 else "HEAVY_CALL_SPEND(risk_on)" if pcp < 0.6 else "balanced_spend"
            parts.append(f"pc_prem_ratio={pcp}({pcp_tag})")
        if v.get("rs_vs_spy") is not None:
            rs = v["rs_vs_spy"]
            rs_tag = "BEATING_MARKET" if rs > 20 else "LAGGING_MARKET" if rs < -20 else "in_line_with_SPY"
            parts.append(f"rs_vs_spy={rs:+.1f}%({rs_tag})")
        if v.get("money_flow_ratio") is not None:
            mf = v["money_flow_ratio"]
            mf_tag = "ACCUMULATION" if mf > 1.3 else "DISTRIBUTION" if mf < 0.8 else "neutral_flow"
            parts.append(f"money_flow={mf}({mf_tag})")
        if v.get("insider_net") and v["insider_net"] != "NEUTRAL":
            ins = v["insider_net"]
            ins_tag = "INSIDER_BUYING(high_conviction_bull)" if ins == "BUYING" else "insider_selling(neutral)"
            parts.append(f"insider={ins}({ins_tag})")
        if v.get("div_yield_pct") is not None:
            parts.append(f"div_yield={v['div_yield_pct']}%")
        if v.get("ex_div_days") is not None:
            exd = v["ex_div_days"]
            exd_tag = "IMMINENT_EXDIV(early_assign_risk_on_calls)" if exd <= 7 else f"ex_div_in_{exd}d"
            parts.append(f"ex_div={exd}d({exd_tag})")
        if v.get("tail_risk_put_pct") is not None:
            trp = v["tail_risk_put_pct"]
            if trp > 20:
                trp_tag = "CRASH_HEDGING_ACTIVE(extreme)" if trp > 40 else "CRASH_HEDGING(elevated)"
                parts.append(f"tail_risk_puts={trp}%({trp_tag})")
        if v.get("iv_skew_pctl") is not None:
            skp = v["iv_skew_pctl"]
            skp_tag = "EXTREME_HISTORICAL_FEAR" if skp >= 90 else "HIGH_HISTORICAL_FEAR" if skp >= 75 else "below_avg_fear" if skp <= 25 else "avg_fear"
            parts.append(f"iv_skew_pctl={skp}th({skp_tag})")
        if v.get("short_float_trend") is not None:
            sft = v["short_float_trend"]
            sft_tag = "SHORTS_BUILDING(bear_conviction)" if sft > 1 else "SHORTS_COVERING(squeeze_trigger)" if sft < -1 else "short_stable"
            parts.append(f"short_trend={sft:+.1f}pp({sft_tag})")
        if v.get("pc_ratio_trend") is not None:
            pct = v["pc_ratio_trend"]
            pct_tag = "BULLISH_ROTATION(calls_dominating)" if pct < -0.2 else "BEARISH_ROTATION(puts_building)" if pct > 0.2 else "stable"
            parts.append(f"pc_ratio_mom={pct:+.2f}({pct_tag})")
        if v.get("instit_own_pct") is not None:
            iop = v["instit_own_pct"]
            iop_tag = "HIGH_CONVICTION(smart_money_loaded)" if iop >= 70 else "MODERATE" if iop >= 40 else "LOW_INST_OWN"
            parts.append(f"instit_own={iop}%({iop_tag})")
        if v.get("uc_streak_days") is not None:
            usd = v["uc_streak_days"]
            usc = v.get("uc_streak_contracts", 1)
            usd_tag = "PERSISTENT_WHALE(5d+)" if usd >= 5 else "MULTI_DAY_INSTITUTIONAL(3-5d)" if usd >= 3 else "RETURNING_BUYER(2-3d)"
            parts.append(f"uc_streak={usd:.0f}d({usd_tag}) contracts={usc}")
        if v.get("sector_etf_flow"):
            parts.append(f"sector_etf_flow={v['sector_etf_flow']}")
        if v.get("dp_trend") and v["dp_trend"] != "STEADY":
            dp3d = v.get("dp_3d_avg_m", "")
            dp3d_str = f"(3d_avg=${dp3d}M)" if dp3d else ""
            parts.append(f"dp_trend={v['dp_trend']}{dp3d_str}")
        if v.get("tech_macd"):
            m = v["tech_macd"]
            m_tag = ("BULLISH_CROSS(fresh_buy_signal)" if m == "BULLISH_CROSS" else
                     "BULLISH(above_signal)"           if m == "BULLISH"        else
                     "BEARISH_CROSS(momentum_warning)" if m == "BEARISH_CROSS"  else
                     "BEARISH(below_signal)")
            div_str = f"+{v['tech_macd_div']}" if v.get("tech_macd_div") else ""
            parts.append(f"macd={m_tag}{div_str}")
        if v.get("tech_sr_context"):
            parts.append(f"sr={v['tech_sr_context']}")
        if v.get("tech_poc_context"):
            poc_d = v.get("tech_poc_dist_pct", 0)
            parts.append(f"poc={v['tech_poc_context']}")
        if v.get("tech_vwap_context"):
            vd = v.get("tech_vwap_dist_pct", 0)
            vwap_label = v["tech_vwap_context"].split("(")[0]
            parts.append(f"vwap={vd:+.1f}%({vwap_label})")
        if v.get("live_alerts"):
            parts.append(f"alerts=[{'; '.join(v['live_alerts'][:2])}]")
        sig_lines.append(" | ".join(parts))

    sig_text = "\n".join(sig_lines)

    macro_line = f"MACRO: {macro_context}" if macro_context else ""
    sector_line = f"SECTORS: {sector_context}" if sector_context else ""
    index_line = f"INDICES: {index_context}" if index_context else ""
    regime_line = f"MARKET_REGIME: {market_regime}" if market_regime and market_regime != "UNKNOWN" else ""
    winrate_line = f"YOUR_HISTORICAL_WIN_RATES: {win_rate_context}" if win_rate_context else ""
    combo_winrate_line = combo_win_context if combo_win_context else ""
    macro_cross_line = f"MACRO_CROSS_ASSET: {macro_cross_asset}" if macro_cross_asset else ""
    context_block = "\n".join(x for x in [macro_line, sector_line, index_line, regime_line, winrate_line, combo_winrate_line, macro_cross_line] if x)

    system_msg = (
        "You are an elite institutional options trader operating at hedge-fund quant level. "
        "You receive 50+ data points per ticker across 21 sources including vol surface, dealer gamma, factor scores, macro cross-asset signals, analyst consensus, and earnings intelligence. "
        "CRITICAL RULES:\n"
        "1. NEVER recommend a setup where opt_spread>12% (ILLIQUID_AVOID) — wide spreads destroy edge.\n"
        "2. In HIGH_FEAR or CORRECTION regimes: avoid LONG CALL; prefer PUT spreads or IRON CONDORs on tickers with iv_rv=RICH_SELL_PREM.\n"
        "3. In BULL_TREND regime: prefer LONG CALL on high-beta (beta≥1.5) names with vol_trend surging and mom12_1>0.\n"
        "4. In RANGING/CHOP regime: prefer IRON CONDOR on IV_rank≥60 + iv_rv=RICH_SELL_PREM tickers; avoid directional plays.\n"
        "5. If YOUR_HISTORICAL_WIN_RATES provided: strongly prefer setup_types with high win rates from your own history.\n"
        "6. persist=3d+ is your highest-conviction filter — multi-day institutional building is rare and reliable.\n"
        "7. earn_beat=3/4 or 4/4 gives fundamental tailwind; earn_beat=0/4 is a headwind.\n"
        "8. GEX=LONG_GAMMA(suppressive) → mean-reversion setups; SHORT_GAMMA(amplifying) → directional/momentum setups.\n"
        "9. iv_skew=FEAR_PREMIUM (>8pp) → institutional crash hedging; use PUT spreads or add protection.\n"
        "10. iv_rv=RICH_SELL_PREM (>20%) → premium selling edge; CHEAP_BUY_VOL (<-10%) → long vol edge.\n"
        "11. MACRO_CROSS_ASSET: YieldCurve=INVERTED → rotate defensive; CreditSpread=WIDENING → reduce risk; Gold=FLIGHT_TO_SAFETY → avoid long equities; VIX_TermStructure=BACKWARDATION → event risk priced, vol may spike further.\n"
        "12. sector_corr=IDIOSYNCRATIC (<0.5) → ticker moves on its own; prefer over highly correlated names.\n"
        "13. news=BEARISH_NEWS with BULL_TREND → fade the news; news=BULLISH_NEWS with momentum = confirmation.\n"
        f"14. EXPIRY RULE: TODAY'S REAL DATE IS {str(_date.today())}. ALL expiry dates you output MUST be in YYYY-MM-DD format AND must fall between {str(_date.today() + _timedelta(days=21))} (earliest) and {str(_date.today() + _timedelta(days=90))} (latest). NEVER output a date from 2024 or any year other than the current year/next year. Never recommend weekly or 0DTE expirations. EXCEPTION: If a ticker shows a single block options trade with premium ≥$10M at an expiry 180–365 days out (LEAPS territory), you MAY recommend that longer expiry — this is whale/institutional positioning and is extremely bullish or bearish. In that case set setup_type to LONG CALL or LONG PUT (not a spread), set conviction to HIGH, and explicitly note the whale block in signals_aligned (e.g. '$20M LEAPS call block, 9mo out').\n"
        "15. EARNINGS PROXIMITY: If earn_in≤7d (IMMINENT), prefer STRADDLE or avoid entirely unless conviction is extreme. If earn_in=8-30d (SOON), IV is likely elevated — check iv_rv; if RICH, sell spreads; if CHEAP, buy vol. impl_earn_move shows the options market's expected ±% move into earnings — compare to earn_beat history.\n"
        "16. ANALYST CONSENSUS: analyst_tgt=STRONG_BUY_CONSENSUS (>25% upside) combined with institutional accumulation (accum_pct≥60%) = highest fundamental + flow alignment. analyst_tgt=FULLY_VALUED (<0% upside) is a headwind for LONG CALL setups.\n"
        "17. PUT/CALL OI RATIO: pc_oi_ratio>1.5 (HEAVY_PUT_OI) = institutions are hedged/bearish positioned; <0.6 (HEAVY_CALL_OI) = bullish positioning. Use as directional confirmation or contrarian signal in conjunction with other factors.\n"
        "18. 52-WEEK RANGE: 52w_range≥90% (NEAR_52W_HIGH) = breakout zone → momentum continuation setups; ≤10% (NEAR_52W_LOW) = support test → mean-reversion bounce or put-selling setups. NEVER recommend LONG CALL on a stock at 52w low without strong catalyst evidence.\n"
        "19. BORROW COST: borrow=HIGH_BORROW means stock is expensive to short. This means heavy put OI on high-short-interest names may be synthetic short hedges by short sellers, NOT directional bearish bets. Do not read put OI as bearish conviction if borrow=HIGH_BORROW.\n"
        "20. FLOW PERSISTENCE: flow_persist shows call/put vol-to-OI ratios. calls=STRUCTURAL(multi_week) means call OI has been building over multiple days — institutional conviction. calls=FRESH(one_day) means today's activity only — could be noise or a hedge. Weight STRUCTURAL flow 2x vs FRESH flow in your conviction score.\n"
        "21. EPS REVISION TREND: eps_trend=RISING means analysts are raising forward earnings estimates — a strong fundamental tailwind. eps_trend=DECLINING means estimates are being cut — a headwind even if flow looks bullish. When eps_trend=DECLINING and call flow is present, reduce conviction; the flow may be a short-term trade against a deteriorating fundamental trend.\n"
        "22. HISTORICAL EARNINGS REACTION: hist_earn_move=±X% is the average absolute price move this stock has made on past earnings days. Use this to calibrate STRADDLE pricing: if impl_earn_move < hist_earn_move, the straddle is cheap (buy vol); if impl_earn_move > hist_earn_move by >50%, the market is overpricing earnings risk (sell premium). This is one of the highest-edge signals for earnings-event trades.\n"
        "23. SHORT SQUEEZE RISK: squeeze_risk=HIGH or EXTREME means the stock has: high short float (≥15%), hard-to-borrow conditions, rising price momentum (RSI>60), AND volume surging. In this scenario: (a) NEVER recommend LONG PUT or BEAR PUT SPREAD — short squeeze could cause catastrophic loss. (b) Consider LONG CALL as a squeeze-capture setup. (c) For bearish plays, use far OTM puts only with strict stop loss.\n"
        "24. ANALYST DISPERSION: analyst_dispersion≥30% (HIGH_DISAGREEMENT) means analysts have wildly different price targets — the outcome is binary and uncertain. In this case: prefer STRADDLE over directional setups, even if flow is one-directional. analyst_dispersion<15% (CONSENSUS) means the fundamental story is clear — directional plays are appropriate.\n"
        "25. PUT/CALL PREMIUM RATIO (DOLLAR-WEIGHTED): pc_prem_ratio measures dollars spent on puts vs calls. CRITICAL INTERPRETATION — high put spend (pc_prem_ratio>1.5) almost always reflects HEDGING by institutions protecting long stock positions, NOT directional bearish bets. Do NOT use pc_prem_ratio to justify a bearish setup. Use it only to gauge overall market fear level: HEAVY_PUT_SPEND = elevated hedging = slightly higher uncertainty for calls. HEAVY_CALL_SPEND(<0.6) = clean risk-on environment = strong confirmation for LONG CALL entries.\n"
        "26. RELATIVE STRENGTH VS SPY: rs_vs_spy is the stock's 1-year return minus SPY's 1-year return. BEATING_MARKET(>+20%) = institutions are actively accumulating; strong confirmation for LONG CALL. LAGGING_MARKET(<-20%) = the stock is a structural underperformer — a powerful headwind even with bullish call flow; reduce conviction or skip. Use RS to confirm momentum: only go high-conviction LONG CALL on stocks with positive RS alignment.\n"
        "27. MONEY FLOW RATIO: money_flow is average volume on up-price days divided by average volume on down-price days over the past 30 sessions. ACCUMULATION(>1.3) = institutions consistently buying on strength AND dips — confirms bullish setups. DISTRIBUTION(<0.8) = sellers are dominant even on green days — confirms bearish or reduces bullish conviction. This is a structural signal; it takes weeks to shift, so treat it as a high-weight baseline.\n"
        "28. INSIDER TRANSACTIONS: insider=BUYING means company officers or directors purchased shares on the open market in the last 30 days — one of the most reliable long-term bullish signals in finance (insiders only buy with their own money when they believe the stock is undervalued). Add +1 conviction tier when insider=BUYING aligns with bullish call flow. insider=SELLING is NEUTRAL — insiders sell for taxes, diversification, estate planning; never use it as a bearish signal alone.\n"
        "29. DIVIDEND YIELD & EX-DIVIDEND DATE: CRITICAL RULE — if ex_div<=7d, DO NOT recommend LONG CALL — the option holder may exercise early to capture the dividend, creating assignment risk. Skip that ticker and pick the next best signal instead. div_yield>3% acts as a price floor: income buyers support the stock on dips.\n"
        "30. TAIL RISK PUT CONCENTRATION: tail_risk_puts is the % of total put volume in deep OTM strikes (>15% below spot). CRASH_HEDGING_ACTIVE(>40%) = institutions are paying for disaster protection, not making directional bets — this is a macro risk-off signal. When tail_risk_puts>30%, do NOT sell premium structures (IRON CONDOR, BULL PUT SPREAD) — institutions may know about an upcoming systemic risk event. The signal does NOT mean the stock will definitely fall; it means smart money is buying insurance at scale.\n"
        "31. IV SKEW PERCENTILE (when available after 30+ days of data): iv_skew_pctl ranks today's IV skew vs the past year for this specific stock. EXTREME_HISTORICAL_FEAR(>=90th percentile) = put premium is at historically extreme levels for this stock — highest edge to SELL PUT SPREADS when bullish, or BUY CALL SPREADS as mean-reversion plays. Below_avg_fear(<=25th percentile) = options are historically cheap — favor LONG options (calls or straddles) over premium selling.\n"
        "32. SHORT INTEREST TREND (when available after 5+ sessions of data): short_trend shows change in short float vs 5 sessions ago. SHORTS_BUILDING(>+1pp) = new bearish institutional conviction entering the stock — validates bearish setups and contradicts bullish flow. SHORTS_COVERING(<-1pp) = short sellers are exiting — potential squeeze trigger forming; combine with squeeze_risk=HIGH or EXTREME for maximum conviction LONG CALL setup (short covering can accelerate a move by 2-3x).\n"
        "34. MACD MOMENTUM: macd=BULLISH_CROSS is the strongest technical signal — momentum just flipped bullish; this is the optimal LONG CALL entry timing. BULLISH means momentum is positive but the cross happened days ago (still valid, lower urgency). BEARISH_CROSS is a warning — momentum turning down, reduce conviction on LONG CALL even with bullish flow. BULLISH_DIV = price made a lower low but MACD held a higher low — institutional accumulation on the dip, high-conviction reversal setup even if the stock looks weak on the surface.\n"
        "35. SUPPORT/RESISTANCE LEVELS: sr=AT_SUPPORT means price is within 2% of a confirmed historical swing low — institutions have defended this exact level before; this is the optimal LONG CALL entry (risk/reward is best here, stop loss is well-defined just below support). ABOVE_SUPPORT(X%_below) shows a cushion below. BELOW_RESISTANCE(X%_above) means a supply zone overhead — if resistance is <3% away, the stock needs to break through first; if >5% away, the trade has room to run before hitting resistance.\n"
        "36. VOLUME PROFILE / POINT OF CONTROL: poc=AT_POC means price is sitting at the highest-traded-volume level of the past 90 days — this acts as both a support magnet AND a breakout launch pad. ABOVE_POC = buyers have pushed price above where 90% of volume traded, confirming institutional demand at lower levels. BELOW_POC = sellers are in control of the distribution; avoid LONG CALL unless other signals are overwhelming. Prefer ABOVE_POC with MACD=BULLISH for highest technical confirmation.\n"
        "37. VWAP (20-DAY): vwap=ABOVE_VWAP means buyers have consistently paid above the average cost basis over the past month — structural bullish; strong confirmation for LONG CALL. BELOW_VWAP is a headwind; institutions are underwater on recent buys. AT_VWAP = decision point, watch for directional resolution. Highest conviction entry: price ABOVE_VWAP + MACD=BULLISH + sr=AT_SUPPORT or ABOVE_SUPPORT — this triple-confirmation setup means technical, momentum, and price structure all agree.\n"
        "38. P/C RATIO MOMENTUM: pc_ratio_mom tracks the 5-day change in the put/call OI ratio. BULLISH_ROTATION (dropping >0.2) means institutions have been steadily closing puts and opening calls over the past week — this is the single most reliable leading indicator that smart money is shifting bullish BEFORE price moves. A single-day low pc_oi_ratio could be noise; a 5-day declining trend is institutional conviction. BEARISH_ROTATION (rising >0.2) means put positioning is building — confirm with other bearish signals before skipping a bullish setup, but treat it as a caution flag. Stable = no rotation in progress.\n"
        "39. INSTITUTIONAL OWNERSHIP: instit_own is the % of shares held by institutional investors (mutual funds, hedge funds, pension funds) per the latest 13F filings. HIGH_CONVICTION (≥70%) means professional money managers dominate the shareholder base — this stock is well-researched and institutionally validated; they will not sell easily on small dips, providing price support. LOW_INST_OWN (<40%) means retail dominates — higher volatility, less predictable behavior. When instit_own=HIGH_CONVICTION aligns with unusual call buying, the interpretation is: EXISTING INSTITUTIONAL OWNERS are adding to their already-large positions — the highest possible conviction signal for LONG CALL.\n"
        "40. MULTI-DAY UC STREAK: uc_streak tracks how many days the same unusual call contract (same strike + expiry) has been actively traded. PERSISTENT_WHALE (5d+) means a single institution has deployed capital into the same options position for 5+ consecutive trading days — this is the rarest and highest-conviction signal in the entire system; they are building a large directional position and cannot do it in one day without moving the market. MULTI_DAY_INSTITUTIONAL (3-5d) = strong conviction, institutional accumulation confirmed. RETURNING_BUYER (2-3d) = same buyer returning, early confirmation. A uc_streak of ANY length combined with uc_prem=WHALE is your absolute highest-conviction setup — override other hesitations when these two align.\n"
        "41. SECTOR ETF FLOW CONFIRMATION: sector_etf_flow=CONFIRMED(XLK_bullish) means the sector ETF itself had $500K+ unusual call buying TODAY — the entire technology sector is seeing institutional inflows, not just this one stock. This is the most powerful confirmation signal in the system: when a sector-level ETF AND an individual stock both show unusual institutional call buying on the same day, the probability that the move is real (not noise or a hedge) is dramatically higher. A stock pick without sector_etf_flow is still valid; a pick WITH sector_etf_flow gets +1 conviction tier automatically. If two picks are otherwise equal, always prefer the one with sector_etf_flow=CONFIRMED.\n"
        "42. DARK POOL TREND: dp_trend tracks whether dark pool premium is ACCELERATING (today's DP flow is 25%+ above 3-day average — institutional buying is intensifying, fresh capital entering), FADING (DP flow dropped 25%+ — institutions may be taking profits or reducing exposure), or STEADY (consistent ongoing accumulation). ACCELERATING combined with any bullish signal stack is a powerful confirmation — institutions are stepping up their buying pace. FADING on an otherwise bullish stock is a caution flag — the smart money that drove the setup may be lightening up. Treat dp_trend=ACCELERATING as equivalent to a +0.5 conviction boost.\n"
        "43. SIGNAL COMBINATION WIN RATES: SIGNAL_COMBO_WIN_RATES shows your actual historical win rate when specific signal tags appeared in past winning vs losing trades. This is YOUR OWN PERFORMANCE DATA — the highest-weight signal in the system. When SIGNAL_COMBO_WIN_RATES shows persist3d+:84%(21/25), it means that out of your 25 past trades where signal had 3+ days persistence, 21 won. USE THIS TO OVERRIDE rule-based weights: if your data shows MACD_CROSS wins 75% of the time but ABOVE_POC wins only 52%, weight MACD_CROSS heavier in your conviction scoring for this session. This self-learning feedback loop means the AI gets smarter every day as more outcomes are logged.\n"
        "33. UNUSUAL CALL PREMIUM GATE (MANDATORY): Every recommended ticker MUST have a uc_prem signal present in its data AND uc_prem ≥ 0.50M ($500K). Tickers without a uc_prem field, or with uc_prem < 0.50M, must be SKIPPED entirely — no exceptions. This ensures every pick has documented institutional unusual call activity backing it. Prefer picks with uc_prem ≥ 1.0M (INSTITUTIONAL) or ≥ 5.0M (WHALE) when available — these represent the highest-conviction smart money flows. If fewer than 5 tickers meet the $500K threshold, fill remaining slots ONLY from the next-highest uc_prem tickers; do NOT recommend tickers with no unusual call flow.\n"
        "ABSOLUTE MANDATE — ALL 5 SETUPS MUST BE: direction=BULLISH, setup_type=LONG CALL only. No spreads. No puts. No iron condors. No straddles. No neutral. No bearish. Every single output must be a naked long call buy. If you cannot find 5 strong bullish setups, pick the 5 best available bullish signals regardless. Never output anything other than LONG CALL.\n"
        "Output ONLY a JSON array of exactly 5 setups. No markdown. No text outside the array."
    )

    user_msg = f"""⚠ TODAY IS {str(_date.today())}. All expiry dates in your JSON response MUST be after {str(_date.today())} and formatted as YYYY-MM-DD. Do not use any date from 2024 or earlier.

SOURCES ({len(active_sources)}): {', '.join(active_sources)}
TICKERS SCANNED: {len(rich)}
{context_block}

TICKER SIGNALS (score-ranked, highest composite first):
{sig_text}

SIGNAL KEY:
- score: composite conviction 0-100 | persist: consecutive days signal has been building (3d+ = very high conviction)
- iv_rank: IV percentile vs 1yr HV | impl_move: options market's priced-in ±% move to expiry
- rsi: momentum (>70 overbought, <30 oversold) | sma50: % above/below 50-day SMA
- vol_trend: 5d vs 20d avg volume ratio (≥1.5x = institutional accumulation surge)
- beta: 30-day beta to SPY (≥1.5 = amplified SPY moves, ≤0.6 = defensive)
- SmartVsRetail: institutional vs retail C/P divergence | calls/puts: intent verdict
- vol/oi: call volume-to-open-interest ratio (>2x = concentrated new institutional position)
- opt_spread: ATM call bid/ask spread % of mid (<5%=liquid, >12%=ILLIQUID_AVOID — do NOT recommend)
- earn_beat: quarters beat vs missed EPS estimate (3/4 or 4/4 = serial earnings beater)
- uc_prem: unusual call premium in $M — NOTABLE=<$1M, INSTITUTIONAL=$1-5M, WHALE=$5M+ | uc_vol_oi: vol/OI ratio on the unusual strike
- mp: max pain & distance | gwall: gamma wall | dp: dark pool premium
- earnings: next earnings | post_earnings: days since = IV crush window (sell premium while IV deflates)
- analysts: net upgrades minus downgrades in last 7 days
- short: short float % / days-to-cover | premarket: gap % & relative volume
- MACRO: days to Fed/CPI/OPEX | SECTORS: sector rotation leaders/laggards
- MARKET_REGIME: current market environment → drives which setup_types to use (see rules above)
- YOUR_HISTORICAL_WIN_RATES: actual win rates from your past trades logged in this system
- iv_skew: put IV minus call IV at ~25-delta (pp) → positive=fear/downside hedging; FEAR_PREMIUM>8pp=institutional crash protection active
- iv_ts: near-term IV minus far-term IV (pp) → BACKWARDATION>5pp=event/earnings risk priced near-term
- GEX: dealer gamma exposure in $M → LONG_GAMMA=suppresses moves/mean-revert; SHORT_GAMMA=amplifies moves/momentum
- iv_rv: IV premium over realized vol % → RICH_SELL_PREM>20%=edge selling premium; CHEAP_BUY_VOL<-10%=edge buying vol
- mom12_1: 12-month minus 1-month price momentum % (Fama-French factor) → >15%=strong; <-15%=weak
- ROE: return on equity % (quality factor) | fwd_PE: forward P/E (value factor — CHEAP<15x, EXPENSIVE>35x)
- sector_corr: 30d correlation to sector ETF → IDIOSYNCRATIC<0.5=name-specific catalyst; >0.85=sector-driven
- news: keyword sentiment score from recent headlines (-=bearish, +=bullish)
- MACRO_CROSS_ASSET: YieldCurve(10y-3m)=curve shape; DXY=dollar; CreditSpread5d=HYG vs LQD; Crude5d; Gold5d; VIX_TermStructure=VIX minus VIX3M (>+2=BACKWARDATION=crisis risk; <-1=contango=calm)
- earn_in: days until next earnings event | impl_earn_move: IV-based expected ±% move into earnings | earn_beat: past quarters beat rate
- analyst_tgt: analyst mean price target vs current price % upside/downside | analyst_recommendation: consensus rating
- pc_oi_ratio: total put OI ÷ total call OI across near-term expirations (>1.5=bearish positioned; <0.6=bullish positioned)
- pc_ratio_mom: 5-day change in pc_oi_ratio (negative=BULLISH_ROTATION=institutions shifting to calls; positive=BEARISH_ROTATION=put positioning building)
- instit_own: % of shares held by institutions per latest 13F filings (≥70%=HIGH_CONVICTION smart money loaded; <40%=retail dominated)
- uc_streak: days the same unusual call contract (ticker+strike+expiry) has been continuously active (5d+=PERSISTENT_WHALE; 3-5d=MULTI_DAY_INSTITUTIONAL; 2-3d=RETURNING_BUYER)
- 52w_range: where price sits in its 52-week high/low range (0%=at annual low, 100%=at annual high; ≥90%=breakout zone; ≤10%=support test)
- borrow: short borrow cost proxy from short interest (HIGH_BORROW≥20% float short = puts may be synthetic hedges by short sellers, not directional bets)
- flow_persist: call/put vol÷OI ratio across 4 expirations (STRUCTURAL<0.05=built over weeks=institutional conviction; FRESH>0.25=today only=may be noise)
- eps_trend: forward vs trailing EPS direction (RISING=estimates going up=tailwind; DECLINING=estimates cut=headwind even if flow bullish)
- hist_earn_move: average absolute % price reaction on past earnings days; compare to impl_earn_move to assess straddle value (hist>impl=buy vol; impl>hist×1.5=sell premium)
- squeeze_risk: composite short squeeze risk (HIGH/EXTREME = high short float + hard borrow + rising RSI + surging vol → danger zone for bears, opportunity for LONG CALL)
- analyst_dispersion: spread of analyst price targets as % of mean (≥30%=HIGH_DISAGREEMENT=prefer straddle; <15%=CONSENSUS=directional ok)
- pc_prem_ratio: actual dollars spent on puts ÷ call premium today (>1.5=HEAVY_PUT_SPEND=institutional fear; <0.6=HEAVY_CALL_SPEND=risk-on; cross-check vs pc_oi_ratio)
- rs_vs_spy: stock 1-year return minus SPY 1-year return (>+20%=BEATING_MARKET=institutional accumulation; <-20%=LAGGING_MARKET=headwind — only go high-conviction LONG CALL on positive RS)
- money_flow: up-day vs down-day avg volume ratio over 30 sessions (>1.3=ACCUMULATION; <0.8=DISTRIBUTION — structural signal, high weight)
- insider: net insider open-market buys vs sells last 30d (BUYING=high-conviction bullish; SELLING=neutral — only BUYING counts as a signal)
- div_yield: annual dividend yield % | ex_div: days to ex-dividend (<=7d=IMMINENT_EXDIV → avoid LONG CALL / COVERED CALL, early assign risk; use BULL CALL SPREAD instead)
- tail_risk_puts: % of put vol in deep OTM strikes >15% below spot (>40%=CRASH_HEDGING_ACTIVE=risk-off; >30% = do NOT sell premium structures)
- iv_skew_pctl: today's IV skew ranked vs 1-year history for this stock (>=90th=EXTREME_HISTORICAL_FEAR=sell put prem / buy call spreads; <=25th=historically cheap vol=buy options)
- short_trend: change in short float vs 5 sessions ago in pp (>+1=SHORTS_BUILDING=bear conviction; <-1=SHORTS_COVERING=squeeze trigger — combine with squeeze_risk=HIGH for max conviction LONG CALL)

PRIORITY WEIGHTING (use in order):
1. opt_spread>12% → SKIP (non-negotiable liquidity gate)
2. MARKET_REGIME + MACRO_CROSS_ASSET → determines valid setup_types for current environment
3. GEX regime → LONG_GAMMA=mean-revert setups; SHORT_GAMMA=directional/momentum setups
4. YOUR_HISTORICAL_WIN_RATES → bias toward setup_types that have worked in your own history
5. persist=3d+ (multi-day confirmation — strongest signal)
6. Smart vs Retail divergence (institutional vs retail misalignment)
7. iv_rv + iv_skew (vol surface edge — where premium is rich/cheap + where fear is concentrated)
8. score≥75 + vol_trend≥1.5x + beta + mom12_1 (accumulation surge + factor confirmation)
9. call vol/oi >2x (concentrated unusual new activity)
10. post_earnings IV crush + earn_beat + ROE + fwd_PE (fundamental quality + vol edge)
11. sector_corr=IDIOSYNCRATIC (name-specific, not sector noise)
12. analyst upgrades + premarket gap + news sentiment confirmation

Return a JSON array of exactly 5 objects. ALL 5 must be BULLISH direction, setup_type LONG CALL only — no spreads, no puts, nothing else. Sort by conviction (HIGH first). Each must have ALL fields:
ticker (string), price (number), setup_type ("LONG CALL"), direction ("BULLISH"), conviction ("HIGH"|"MEDIUM"), entry_strike (number), expiry (YYYY-MM-DD), target_price (number), stop_loss (number), option_premium (number — estimated option ask price per share based on current IV, strike proximity, and days to expiry; this is the cost to buy 1 share of the option, not per contract), signals_aligned (list of 4-5 short strings naming exact signals used), thesis (2 sentences max), risk_level ("LOW"|"MEDIUM"|"HIGH")

JSON array only. No markdown. Start immediately with ["""

    def _call_openai_streaming():
        """Stream the response so we capture content even if the proxy truncates."""
        import sys
        chunks = []
        finish = "unknown"
        stream = oai.chat.completions.create(
            model="gpt-4o-mini",
            max_completion_tokens=1500,
            stream=True,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                chunks.append(delta)
            if chunk.choices and chunk.choices[0].finish_reason:
                finish = chunk.choices[0].finish_reason
        raw = "".join(chunks).strip()
        print(f"[ai_trades] finish={finish} raw_len={len(raw)}", file=sys.stderr, flush=True)
        return raw, finish

    def _extract_json(raw):
        if "```" in raw:
            for part in raw.split("```"):
                stripped = part.lstrip("json").strip()
                if stripped.startswith("["):
                    return stripped
        if not raw.startswith("["):
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start >= 0 and end > start:
                return raw[start:end]
        return raw

    try:
        raw, finish = _call_openai_streaming()
        raw = _extract_json(raw)

        # Retry once with a pause if empty (rate-limit or transient hiccup)
        if not raw:
            import time as _time, sys
            print("[ai_trades] empty on first attempt — retrying in 8s", file=sys.stderr, flush=True)
            _time.sleep(8)
            raw, finish = _call_openai_streaming()
            raw = _extract_json(raw)

        if not raw:
            import sys
            print(f"[ai_trades_bg] OpenAI returned no content (finish={finish}) — aborting", file=sys.stderr)
            return  # background worker exits; stale cache stays; user can retry

        try:
            trades = _json.loads(raw)
        except Exception:
            from json_repair import repair_json as _rj
            trades = _json.loads(_rj(raw))

        # Validate expiry dates — catch and fix any past dates the AI hallucinated
        import datetime as _dtfix
        _today_fix = _dtfix.date.today()
        _fallback_exp = str(_today_fix + _dtfix.timedelta(days=45))
        for _tr in trades:
            try:
                _exp = _tr.get("expiry", "")
                if not _exp or _dtfix.date.fromisoformat(_exp) <= _today_fix:
                    print(f"[ai_trades] fixing bad expiry '{_exp}' for {_tr.get('ticker')} → {_fallback_exp}")
                    _tr["expiry"] = _fallback_exp
            except Exception:
                _tr["expiry"] = _fallback_exp

        # Post-filter: drop any picks the AI made that lack real uc_prem >= $500K.
        # Build a set of tickers confirmed to have unusual call premium >= 0.5M.
        _uc_now = getattr(app, "_unusual_calls_cache", None)
        if _uc_now:
            _uc_prem_map = {}
            for _h in _uc_now.get("hits", []):
                _t = _h["ticker"]
                _p = _h.get("prem", 0)
                if _p > _uc_prem_map.get(_t, 0):
                    _uc_prem_map[_t] = _p
            _qualified = {t for t, p in _uc_prem_map.items() if p >= 500_000}
            _filtered = [tr for tr in trades if tr.get("ticker") in _qualified]
            # Only apply filter if it leaves at least 2 picks; otherwise keep all (data may be stale)
            if len(_filtered) >= 2:
                trades = _filtered
                print(f"[ai_trades] premium filter: {len(trades)} picks kept (had {len(_uc_prem_map)} uc tickers, {len(_qualified)} ≥$500K)")
            else:
                print(f"[ai_trades] premium filter skipped — only {len(_filtered)} qualified picks (keeping all {len(trades)})")

        out = {
            "trades": trades,
            "generated_at": _dt.now().isoformat(),
            "tickers_scanned": len(rich),
            "signal_sources": active_sources,
            "signal_source_count": len(active_sources),
        }
        app._ait_cache = out
        app._ait_cache_ts = _dt.now()
        import sys
        print(f"[ai_trades_bg] done — {len(trades)} setups cached", file=sys.stderr, flush=True)
        try:
            from datetime import date as _date_now
            _save_ai_trades_to_log(trades, str(_date_now.today()))
        except Exception as _se:
            print(f"[ai_trade_log] background save error: {_se}")
    except Exception as e:
        import sys
        print(f"[ai_trades_bg] exception: {e}", file=sys.stderr, flush=True)
    finally:
        app._ait_generating = False


@app.route("/stock-api/ai-trades", methods=["GET"])
def ai_trades():
    """Return AI trade setups instantly from cache; regenerates in background."""
    from datetime import datetime as _dt
    import threading as _thr

    _cache = getattr(app, "_ait_cache", None)
    _ts    = getattr(app, "_ait_cache_ts", None)
    _gen   = getattr(app, "_ait_generating", False)
    _stale = not (_ts and (_dt.now() - _ts).total_seconds() < 3600)

    if _stale and not _gen:
        _thr.Thread(target=_ai_trades_worker, daemon=True).start()

    if _cache:
        return jsonify({**_cache, **({"refreshing": True} if _stale else {})})

    return jsonify({
        "loading": True,
        "trades": [],
        "tickers_scanned": 0,
        "signal_sources": [],
        "error": "AI is analyzing all 40 signals — takes ~30 sec on first load. Tap Regenerate then wait a moment.",
    })


@app.route("/stock-api/check-subscription", methods=["POST"])
def check_subscription():
    """Check if an email has an active Pro subscription (or is an admin)."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"subscribed": False, "error": "Email required"})
    ADMIN_EMAILS = {"joeldcarlo@gmail.com"}
    if email in ADMIN_EMAILS:
        return jsonify({"subscribed": True, "admin": True})
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT active, paid FROM sm_subscribers WHERE LOWER(email) = %s LIMIT 1", (email,))
            row = cur.fetchone()
            if row and row[0] and row[1]:
                return jsonify({"subscribed": True})
            return jsonify({"subscribed": False})
    except Exception as e:
        return jsonify({"subscribed": False, "error": str(e)})


@app.route("/stock-api/ai-trades/regenerate", methods=["POST"])
def ai_trades_regenerate():
    """Force-refresh AI trade setups in background; returns immediately."""
    import threading as _thr
    app._ait_cache_ts = None
    if not getattr(app, "_ait_generating", False):
        _thr.Thread(target=_ai_trades_worker, daemon=True).start()
    return jsonify({"status": "generating", "message": "AI generation started."})


@app.route("/stock-api/ai-trades/backfill-flow", methods=["POST"])
def ai_trades_backfill_flow():
    """Backfill total_premium_usd for today's trades where it is NULL. Uses delays to avoid rate limits."""
    import time as _time
    from datetime import date as _d
    today = str(_d.today())
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""SELECT id, ticker, entry_strike, expiry FROM ai_trade_log
                           WHERE total_premium_usd IS NULL AND entry_strike IS NOT NULL
                           AND expiry IS NOT NULL AND trade_date = %s""", (today,))
            rows = cur.fetchall()
        updated = 0
        for (rid, ticker, strike, expiry) in rows:
            _time.sleep(1.5)
            val = _fetch_options_flow_usd(ticker, strike, expiry)
            if val is not None:
                with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                    cur.execute("UPDATE ai_trade_log SET total_premium_usd = %s WHERE id = %s", (val, rid))
                    conn.commit()
                updated += 1
        return jsonify({"status": "ok", "checked": len(rows), "updated": updated})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/stock-api/signal-feed", methods=["GET"])
def signal_feed():
    """Real-time notable signal events — dark pool, smart money, vol crush, max pain."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_sf_cache", None)
    _ts    = getattr(app, "_sf_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 600:
        return jsonify(_cache)

    FOCUS = ["SPY","QQQ","NVDA","AAPL","MSFT","META","GOOGL","AMZN","TSLA","AMD","ORCL","MU","NFLX","CRM","PLTR","AVGO","ARM","IWM","MSTR","SMCI"]
    now   = _dt.now()
    events = []

    def _check(ticker):
        evs = []
        try:
            import numpy as np
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return evs
            exps  = tkr.options
            if not exps: return evs
            chain = tkr.option_chain(exps[0])

            # Vol crush check
            atm  = min(chain.puts["strike"].tolist(), key=lambda s: abs(s - price))
            ivp  = chain.puts[chain.puts["strike"]  == atm]["impliedVolatility"].values
            ivc  = chain.calls[chain.calls["strike"] == atm]["impliedVolatility"].values
            ivv  = [v for v in list(ivp)+list(ivc) if v and v > 0]
            if ivv:
                cur_iv = float(np.mean(ivv))
                hist   = tkr.history(period="1y")["Close"]
                if len(hist) >= 40:
                    hv_s = hist.pct_change().dropna().rolling(21).std() * np.sqrt(252)
                    hv_s = hv_s.dropna()
                    iv_rank = (cur_iv - float(hv_s.min())) / max(float(hv_s.max()) - float(hv_s.min()), 0.001) * 100
                    if iv_rank >= 85:
                        evs.append({"ticker": ticker, "price": round(price,2), "type": "VOL SPIKE",
                                    "icon": "🌡️", "color": "#f87171",
                                    "msg": f"IV rank {iv_rank:.0f}% — premium at 1-year extreme"})
                    elif iv_rank <= 15:
                        evs.append({"ticker": ticker, "price": round(price,2), "type": "IV FLOOR",
                                    "icon": "📉", "color": "#60a5fa",
                                    "msg": f"IV rank {iv_rank:.0f}% — cheapest options in a year"})

            # Smart money divergence
            sc = sp = rc = rp = 0.0
            for exp in exps[:3]:
                try:
                    ch = tkr.option_chain(exp)
                    for side, df in [("c", ch.calls), ("p", ch.puts)]:
                        for _, row in df.iterrows():
                            vol  = int(row.get("volume", 0) or 0)
                            last = float(row.get("lastPrice", 0) or 0)
                            if vol <= 0 or last <= 0: continue
                            prem = vol * last * 100
                            if last >= 3.0 and vol >= 30:
                                if side == "c": sc += prem
                                else: sp += prem
                            elif last < 2.0 or vol < 15:
                                if side == "c": rc += prem
                                else: rp += prem
                except Exception: continue
            s_cp = sc/sp if sp > 0 else 9.9
            r_cp = rc/rp if rc > 0 else 0.1
            if s_cp >= 2.0 and r_cp <= 0.6:
                evs.append({"ticker": ticker, "price": round(price,2), "type": "SMART BULL",
                            "icon": "🏦", "color": "#4ade80",
                            "msg": f"Institutions C/P={s_cp:.1f}× while retail is {r_cp:.1f}× — classic divergence"})
            elif s_cp <= 0.5 and r_cp >= 1.8:
                evs.append({"ticker": ticker, "price": round(price,2), "type": "SMART BEAR",
                            "icon": "⚠️", "color": "#f87171",
                            "msg": f"Smart money putting at C/P={s_cp:.1f}× while retail buys calls {r_cp:.1f}×"})

            # Max pain distance
            puts_df = chain.puts; calls_df = chain.calls
            strikes = sorted(set(list(puts_df["strike"]) + list(calls_df["strike"])))
            best = strikes[0]; lo = float("inf")
            for s in strikes:
                cp = sum(max(0.0, float(s)-float(k)) * float(o or 0) for k, o in zip(calls_df["strike"], calls_df["openInterest"].fillna(0)))
                pp = sum(max(0.0, float(k)-float(s)) * float(o or 0) for k, o in zip(puts_df["strike"],  puts_df["openInterest"].fillna(0)))
                if (cp+pp) < lo: lo = cp+pp; best = s
            mp = float(best)
            dist = (price - mp) / mp * 100
            if abs(dist) >= 8:
                evs.append({"ticker": ticker, "price": round(price,2), "type": "MAX PAIN GAP",
                            "icon": "📍", "color": "#fbbf24",
                            "msg": f"Price {dist:+.1f}% from max pain ${mp:.2f} — gravitational pull expected by {exps[0]}"})

        except Exception: pass
        return evs

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_check, t): t for t in FOCUS}
        for f in as_completed(futs): events.extend(f.result())

    events.sort(key=lambda x: (x["type"] == "SMART BULL" or x["type"] == "SMART BEAR"), reverse=True)
    out = {"events": events[:30], "generated_at": now.isoformat()}
    app._sf_cache = out; app._sf_cache_ts = now
    return jsonify(out)


@app.route("/stock-api/composite-score", methods=["GET"])
def composite_score():
    """0–100 composite score per ticker combining IV rank, smart money, call accumulation, max pain alignment."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_cs_cache", None)
    _ts    = getattr(app, "_cs_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    now = _dt.now()

    def _score(ticker):
        try:
            import numpy as np
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps  = tkr.options
            if not exps: return None
            chain = tkr.option_chain(exps[0])

            score = 50.0; reasons = []; components = {}

            # IV rank (0–25 pts)
            atm  = min(chain.puts["strike"].tolist(), key=lambda s: abs(s - price))
            ivp  = chain.puts[chain.puts["strike"]  == atm]["impliedVolatility"].values
            ivc  = chain.calls[chain.calls["strike"] == atm]["impliedVolatility"].values
            ivv  = [v for v in list(ivp)+list(ivc) if v and v > 0]
            iv_rank = 50.0
            if ivv:
                cur_iv = float(np.mean(ivv))
                hist   = tkr.history(period="1y")["Close"]
                if len(hist) >= 40:
                    hv_s = hist.pct_change().dropna().rolling(21).std() * np.sqrt(252)
                    hv_s = hv_s.dropna()
                    iv_rank = max(0.0, min(100.0, (cur_iv - float(hv_s.min())) / max(float(hv_s.max()) - float(hv_s.min()), 0.001) * 100))
            iv_pts = round((100 - iv_rank) / 100 * 25, 1)  # low IV = cheaper options = bullish edge
            score += iv_pts - 12.5
            components["iv_rank"] = round(iv_rank, 1)
            components["iv_score"] = iv_pts

            # Smart money (0–30 pts)
            sc = sp = rc = rp = 0.0
            accum_prem = fomo_prem = 0.0
            top_accum = {"strike": None, "expiry": None, "otm_pct": 0.0, "prem": 0.0}
            for exp in exps[:5]:
                try:
                    days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                    ch = tkr.option_chain(exp)
                    for side, df in [("c", ch.calls), ("p", ch.puts)]:
                        for _, row in df.iterrows():
                            vol  = int(row.get("volume", 0) or 0)
                            last = float(row.get("lastPrice", 0) or 0)
                            oi   = int(row.get("openInterest", 0) or 0)
                            if vol <= 0 or last <= 0: continue
                            prem = vol * last * 100
                            if last >= 3.0 and vol >= 30:
                                if side == "c": sc += prem
                                else:           sp += prem
                            elif last < 2.0 or vol < 15:
                                if side == "c": rc += prem
                                else:           rp += prem
                    for _, row in ch.calls.iterrows():
                        strike = float(row.get("strike", 0) or 0)
                        vol    = int(row.get("volume", 0) or 0)
                        oi     = int(row.get("openInterest", 0) or 0)
                        last   = float(row.get("lastPrice", 0) or 0)
                        if strike <= 0 or last <= 0: continue
                        otm = (strike - price) / price * 100
                        p   = (vol + oi) * last * 100
                        if otm > 5 and days_out > 60:
                            accum_prem += p
                            if p > top_accum["prem"]:
                                top_accum = {"strike": round(strike,2), "expiry": exp, "otm_pct": round(otm,1), "prem": p}
                        elif -3 < otm < 3 and days_out < 45:
                            fomo_prem += p
                except Exception: continue

            s_cp = sc/sp if sp > 0 else 1.0
            r_cp = rc/rp if rp > 0 else 1.0
            sm_pts = min(30.0, max(0.0, (s_cp - 1.0) * 10.0))
            score += sm_pts - 15
            components["smart_cp"] = round(s_cp, 2)
            components["retail_cp"] = round(r_cp, 2)
            components["sm_score"] = round(sm_pts, 1)

            total_call = accum_prem + fomo_prem
            accum_pct  = accum_prem / total_call * 100 if total_call > 0 else 50.0
            accum_pts  = round((accum_pct - 50) / 50 * 15, 1)
            score += accum_pts
            components["accum_pct"] = round(accum_pct, 1)
            components["accum_score"] = accum_pts
            components["top_accum"] = top_accum

            # Max pain (0–15 pts for being below pain = bullish pull)
            mp_strike = None; mp_pts = 0.0
            try:
                strikes2 = sorted(set(list(chain.puts["strike"]) + list(chain.calls["strike"])))
                best = strikes2[0]; lo = float("inf")
                for s in strikes2:
                    cp2 = sum(max(0.0, float(s)-float(k)) * float(o or 0) for k, o in zip(chain.calls["strike"], chain.calls["openInterest"].fillna(0)))
                    pp2 = sum(max(0.0, float(k)-float(s)) * float(o or 0) for k, o in zip(chain.puts["strike"],  chain.puts["openInterest"].fillna(0)))
                    if (cp2+pp2) < lo: lo = cp2+pp2; best = s
                mp_strike = float(best)
                dist = (price - mp_strike) / mp_strike * 100
                mp_pts = min(15.0, max(-15.0, -dist * 1.5))
                score += mp_pts
            except Exception: pass
            components["max_pain"] = round(mp_strike, 2) if mp_strike else None
            components["mp_score"] = round(mp_pts, 1)

            score = round(max(0.0, min(100.0, score)), 1)
            bias  = "STRONG BULL" if score >= 75 else "BULLISH" if score >= 60 else "NEUTRAL" if score >= 40 else "BEARISH" if score >= 25 else "STRONG BEAR"

            return {"ticker": ticker, "price": round(price, 2), "score": score, "bias": bias,
                    "components": components, "nearest_exp": exps[0]}
        except Exception: return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_score, t): t for t in DEFAULT_LEADERBOARD}
        rows = [r for f in as_completed(futs) if (r := f.result()) is not None]
    rows.sort(key=lambda x: x["score"], reverse=True)
    out = {"results": rows, "scanned": len(DEFAULT_LEADERBOARD)}
    app._cs_cache = out; app._cs_cache_ts = now
    return jsonify(out)


@app.route("/stock-api/ai-trade-log", methods=["GET"])
def ai_trade_log():
    """Return full AI trade history with win/loss outcomes and aggregate stats."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, trade_date, ticker, direction, setup_type, conviction,
                       price_at_signal, entry_strike, expiry, target_price, stop_loss,
                       signals_aligned, thesis, risk_level,
                       t1_price, t3_price, t5_price, t10_price,
                       t1_pct, t3_pct, t5_pct, t10_pct,
                       t1_win, t3_win, t5_win, t10_win,
                       expiry_price, expiry_pct, expiry_win,
                       outcome, created_at,
                       COALESCE(source, 'AI_TRADE') AS source,
                       option_premium, breakeven_price, total_premium_usd
                FROM ai_trade_log
                ORDER BY trade_date DESC, id DESC
            """)
            rows = cur.fetchall()
            cols = ["id","trade_date","ticker","direction","setup_type","conviction",
                    "price_at_signal","entry_strike","expiry","target_price","stop_loss",
                    "signals_aligned","thesis","risk_level",
                    "t1_price","t3_price","t5_price","t10_price",
                    "t1_pct","t3_pct","t5_pct","t10_pct",
                    "t1_win","t3_win","t5_win","t10_win",
                    "expiry_price","expiry_pct","expiry_win",
                    "outcome","created_at","source",
                    "option_premium","breakeven_price","total_premium_usd"]
            trades = []
            for row in rows:
                d = dict(zip(cols, row))
                d["trade_date"] = str(d["trade_date"]) if d["trade_date"] else None
                d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
                d["signals_aligned"] = d["signals_aligned"] if isinstance(d["signals_aligned"], list) else []
                trades.append(d)

            # Compute win rates
            def _wr(key):
                vals = [t[key] for t in trades if t[key] is not None]
                if not vals: return None
                return round(sum(1 for v in vals if v) / len(vals) * 100, 1)

            win_rates = {
                "expiry": _wr("expiry_win"),
                "t1": _wr("t1_win"), "t3": _wr("t3_win"),
                "t5": _wr("t5_win"), "t10": _wr("t10_win"),
            }

            # By direction breakdown (kept for backward compat)
            by_dir = {}
            for d in ["BULLISH", "BEARISH", "NEUTRAL"]:
                sub = [t for t in trades if t["direction"] == d]
                exp_wins = [t["expiry_win"] for t in sub if t["expiry_win"] is not None]
                t5s = [t["t5_win"] for t in sub if t["t5_win"] is not None]
                by_dir[d] = {
                    "count": len(sub),
                    "win_rate_expiry": round(sum(1 for v in exp_wins if v) / len(exp_wins) * 100, 1) if exp_wins else None,
                    "win_rate_t5": round(sum(1 for v in t5s if v) / len(t5s) * 100, 1) if t5s else None,
                }

            # By source breakdown — AI_TRADE vs MULTI_SIGNAL vs BOTH
            by_src = {}
            for s in ["AI_TRADE", "MULTI_SIGNAL", "BOTH"]:
                sub = [t for t in trades if t.get("source") == s]
                exp_wins = [t["expiry_win"] for t in sub if t["expiry_win"] is not None]
                t5s = [t["t5_win"] for t in sub if t["t5_win"] is not None]
                by_src[s] = {
                    "count": len(sub),
                    "win_rate_expiry": round(sum(1 for v in exp_wins if v) / len(exp_wins) * 100, 1) if exp_wins else None,
                    "win_rate_t5": round(sum(1 for v in t5s if v) / len(t5s) * 100, 1) if t5s else None,
                }

            return jsonify({
                "trades": trades,
                "count": len(trades),
                "win_rates": win_rates,
                "by_direction": by_dir,
                "by_source": by_src,
            })
    except Exception as e:
        return jsonify({"error": str(e), "trades": [], "count": 0,
                        "win_rates": {"t1": None, "t3": None, "t5": None, "t10": None},
                        "by_direction": {}, "by_source": {}}), 500


@app.route("/stock-api/multi-signal/log", methods=["POST"])
def multi_signal_log():
    """Persist a multi-signal AI thesis call to the ai_trade_log table."""
    try:
        from datetime import datetime as _msl_dt
        import pytz
        body    = request.get_json(force=True) or {}
        ticker  = (body.get("ticker") or "").upper().strip()
        signals = body.get("signals") or []
        score   = int(body.get("score", len(signals)))
        price   = float(body.get("price") or 0)
        thesis  = (body.get("thesis") or "").strip()

        if not ticker or not thesis:
            return jsonify({"error": "ticker and thesis required"}), 400

        conviction = "HIGH" if score >= 10 else ("MEDIUM" if score >= 6 else "LOW")
        today = _msl_dt.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d")

        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ai_trade_log
                    (trade_date, ticker, direction, setup_type, conviction,
                     price_at_signal, signals_aligned, thesis, risk_level, source)
                VALUES (%s, %s, 'BULLISH', 'MULTI_SIGNAL', %s, %s, %s, %s, 'MEDIUM', 'MULTI_SIGNAL')
                ON CONFLICT (trade_date, ticker, direction)
                DO UPDATE SET
                    source          = CASE WHEN ai_trade_log.source = 'AI_TRADE' THEN 'BOTH' ELSE 'MULTI_SIGNAL' END,
                    signals_aligned = EXCLUDED.signals_aligned,
                    thesis          = EXCLUDED.thesis,
                    setup_type      = CASE WHEN ai_trade_log.source = 'AI_TRADE' THEN ai_trade_log.setup_type ELSE 'MULTI_SIGNAL' END
            """, (today, ticker, conviction, price, _json.dumps(signals), thesis))
            conn.commit()

        return jsonify({"ok": True, "ticker": ticker, "date": today})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _run_whale_scan_background():
    """Live whale scan — runs in background thread, populates cache + DB when done."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import yfinance as yf
    from datetime import datetime as _dt

    if getattr(app, "_whale_scan_running", False):
        return
    app._whale_scan_running = True
    try:
        now = _dt.now()
        MIN_PREM_M = 5.0

        def _scan_whale(ticker):
            blocks = []
            try:
                tkr   = yf.Ticker(ticker)
                price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
                if price <= 0: return blocks
                exps = tkr.options
                if not exps: return blocks
                for exp in exps:
                    try:
                        days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                        if not (30 <= days_out <= 365): continue
                        for direction, chain in [("CALL", tkr.option_chain(exp).calls),
                                                 ("PUT",  tkr.option_chain(exp).puts)]:
                            for _, row in chain.iterrows():
                                strike = float(row.get("strike", 0) or 0)
                                vol    = int(row.get("volume", 0) or 0)
                                last   = float(row.get("lastPrice", 0) or 0)
                                if strike <= 0 or last <= 0 or vol <= 0: continue
                                if strike < price * 0.15: continue
                                pre_otm = (strike - price) / price * 100
                                if pre_otm < -75: continue
                                prem_m = vol * last * 100 / 1e6
                                if prem_m >= MIN_PREM_M:
                                    otm_pct  = round(pre_otm, 1)
                                    category = "LEAPS" if days_out >= 180 else "AGGRESSIVE" if days_out <= 90 else "MEDIUM"
                                    tier     = "MEGA_WHALE" if prem_m >= 20 else "WHALE" if prem_m >= 10 else "BIG_BLOCK"
                                    blocks.append({
                                        "ticker":    ticker,
                                        "price":     round(price, 2),
                                        "direction": direction,
                                        "strike":    round(strike, 2),
                                        "expiry":    exp,
                                        "days_out":  days_out,
                                        "prem_m":    round(prem_m, 1),
                                        "volume":    vol,
                                        "otm_pct":   otm_pct,
                                        "category":  category,
                                        "tier":      tier,
                                    })
                    except Exception: continue
            except Exception: pass
            return blocks

        all_blocks = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(_scan_whale, t): t for t in DEFAULT_LEADERBOARD}
            for fut in as_completed(futures):
                all_blocks.extend(fut.result() or [])

        all_blocks.sort(key=lambda x: x["prem_m"], reverse=True)
        out = {"blocks": all_blocks[:60], "total": len(all_blocks), "scanned": len(DEFAULT_LEADERBOARD)}
        app._whale_cache    = out
        app._whale_cache_ts = _dt.now()
        _save_whale_blocks_to_db(all_blocks)
    finally:
        app._whale_scan_running = False


@app.route("/stock-api/whale-activity", methods=["GET"])
def whale_activity():
    """Scan for large institutional options blocks ($5M+ single-strike) across 30-365 day expirations."""
    from datetime import datetime as _dt
    import threading

    _cache = getattr(app, "_whale_cache", None)
    _ts    = getattr(app, "_whale_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    # Cache is cold — return recent DB blocks immediately, kick off live scan in background
    db_blocks = []
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, direction, strike::float, expiry, days_out, prem_m::float,
                       volume, otm_pct::float, category, tier, price::float
                FROM whale_blocks
                WHERE first_seen >= NOW() - INTERVAL '3 days'
                ORDER BY prem_m DESC
                LIMIT 60
            """)
            cols = ["ticker","direction","strike","expiry","days_out","prem_m","volume","otm_pct","category","tier","price"]
            db_blocks = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        pass

    # Kick off live scan in background (only one at a time)
    if not getattr(app, "_whale_scan_running", False):
        threading.Thread(target=_run_whale_scan_background, daemon=True).start()

    if db_blocks:
        out = {"blocks": db_blocks, "total": len(db_blocks), "scanned": len(DEFAULT_LEADERBOARD), "source": "db"}
        return jsonify(out)

    # No DB data either — wait for live scan synchronously (first ever run)
    _run_whale_scan_background()
    _cache = getattr(app, "_whale_cache", None)
    if _cache:
        return jsonify(_cache)
    return jsonify({"blocks": [], "total": 0, "scanned": len(DEFAULT_LEADERBOARD)})


@app.route("/stock-api/whale-history", methods=["GET"])
def whale_history():
    """Return all-time whale block log from DB, newest first."""
    ticker = request.args.get("ticker", "").upper().strip()
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            if ticker:
                cur.execute("""
                    SELECT ticker, direction, strike::float, expiry, days_out, prem_m::float,
                           volume, otm_pct::float, category, tier, price::float,
                           first_seen AT TIME ZONE 'UTC' AS first_seen
                    FROM whale_blocks
                    WHERE ticker = %s
                    ORDER BY first_seen DESC
                    LIMIT 500
                """, (ticker,))
            else:
                cur.execute("""
                    SELECT ticker, direction, strike::float, expiry, days_out, prem_m::float,
                           volume, otm_pct::float, category, tier, price::float,
                           first_seen AT TIME ZONE 'UTC' AS first_seen
                    FROM whale_blocks
                    ORDER BY first_seen DESC
                    LIMIT 500
                """)
            cols = ["ticker","direction","strike","expiry","days_out","prem_m",
                    "volume","otm_pct","category","tier","price","first_seen"]
            rows = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                d["first_seen"] = d["first_seen"].strftime("%Y-%m-%d %H:%M UTC") if d["first_seen"] else None
                rows.append(d)
        return jsonify({"blocks": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "blocks": [], "total": 0}), 500


# ── Trade Watchlist ───────────────────────────────────────────────────────────

def _init_trade_watchlist_table():
    sql = """
    CREATE TABLE IF NOT EXISTS trade_watchlist (
        id           SERIAL PRIMARY KEY,
        ticker       TEXT NOT NULL,
        strike       NUMERIC NOT NULL,
        expiry       TEXT NOT NULL,
        option_type  TEXT NOT NULL DEFAULT 'CALL',
        entry_price  NUMERIC,
        contracts    INTEGER DEFAULT 1,
        notes        TEXT,
        saved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[trade_watchlist] init error: {e}")

_init_trade_watchlist_table()


@app.route("/stock-api/trade-watchlist", methods=["GET"])
def get_trade_watchlist():
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, strike::float, expiry, option_type,
                       entry_price::float, contracts, notes,
                       saved_at AT TIME ZONE 'UTC' AS saved_at
                FROM trade_watchlist
                WHERE saved_at > NOW() - INTERVAL '30 days'
                ORDER BY saved_at DESC
            """)
            cols = ["id","ticker","strike","expiry","option_type","entry_price","contracts","notes","saved_at"]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        # enrich with current stock price
        tickers = list({r["ticker"] for r in rows})
        prices = {}
        if tickers:
            import yfinance as yf
            from concurrent.futures import ThreadPoolExecutor
            def _get_price(t):
                try:
                    h = yf.Ticker(t).history(period="1d", interval="1m")
                    return t, float(h["Close"].iloc[-1]) if not h.empty else None
                except Exception:
                    return t, None
            with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as ex:
                for t, p in ex.map(_get_price, tickers):
                    prices[t] = p

        from datetime import datetime as _dt2, timezone as _tz
        now = _dt2.now(_tz.utc)
        for r in rows:
            r["saved_at"] = r["saved_at"].strftime("%Y-%m-%d %H:%M UTC") if r["saved_at"] else None
            r["current_price"] = prices.get(r["ticker"])
            # days until expiry
            try:
                exp_dt = _dt2.strptime(r["expiry"], "%Y-%m-%d").date()
                r["days_to_expiry"] = (exp_dt - now.date()).days
            except Exception:
                r["days_to_expiry"] = None
            # days held
            try:
                saved_dt = _dt2.strptime(r["saved_at"], "%Y-%m-%d %H:%M UTC")
                r["days_held"] = (now.replace(tzinfo=None) - saved_dt).days
            except Exception:
                r["days_held"] = 0
            # OTM/ITM %
            if r["current_price"] and r["strike"]:
                r["strike_vs_price_pct"] = round((r["strike"] / r["current_price"] - 1) * 100, 1)
            else:
                r["strike_vs_price_pct"] = None
            # total cost
            if r["entry_price"] and r["contracts"]:
                r["total_cost"] = round(r["entry_price"] * r["contracts"] * 100, 2)
            else:
                r["total_cost"] = None

        return jsonify({"trades": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "trades": [], "count": 0}), 500


@app.route("/stock-api/trade-watchlist", methods=["POST"])
def add_trade_watchlist():
    body = request.get_json(force=True) or {}
    ticker = body.get("ticker", "").upper().strip()
    strike = body.get("strike")
    expiry = body.get("expiry", "").strip()
    option_type = body.get("option_type", "CALL").upper()
    entry_price = body.get("entry_price")
    contracts = body.get("contracts", 1)
    notes = body.get("notes", "").strip()
    if not ticker or not strike or not expiry:
        return jsonify({"error": "ticker, strike, and expiry are required"}), 400
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trade_watchlist (ticker, strike, expiry, option_type, entry_price, contracts, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (ticker, strike, expiry, option_type, entry_price, contracts, notes or None))
            new_id = cur.fetchone()[0]
            conn.commit()
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/trade-watchlist/<int:trade_id>", methods=["DELETE"])
def delete_trade_watchlist(trade_id):
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM trade_watchlist WHERE id = %s", (trade_id,))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/unusual-calls", methods=["GET"])
def unusual_calls():
    """Scan for unusual near-term call activity (1-30 days) with Vol/OI >= 3x — pure bullish bets, not hedges."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import yfinance as yf
    from datetime import datetime as _dt
    import threading

    # Ensure a single scan lock exists
    if not hasattr(app, "_uc_lock"):
        app._uc_lock = threading.Lock()

    _cache = getattr(app, "_unusual_calls_cache", None)
    _ts    = getattr(app, "_unusual_calls_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 900:
        return jsonify(_cache)

    # Only one scan at a time — concurrent requests block here until the scan
    # finishes, then the re-check returns the fresh cache instead of re-scanning.
    with app._uc_lock:
        # Re-check after acquiring lock — another thread may have just finished
        _cache = getattr(app, "_unusual_calls_cache", None)
        _ts    = getattr(app, "_unusual_calls_cache_ts", None)
        if _cache and _ts and (_dt.now() - _ts).total_seconds() < 900:
            return jsonify(_cache)

        now = _dt.now()

        # ETF tickers get calibrated thresholds: lower vol/OI (they carry massive OI) and
        # wider expiry window (institutional ETF plays tend to go 30-60 days out).
        _ETF_SET = {
            "SPY","QQQ","IWM","DIA","MDY","VTI","VOO",
            "XLF","XLE","XLK","XLY","XLI","XLV","XLB","XLP","XLU","XLRE",
            "SMH","SOXX","XBI","IBB","KRE","XRT","ITB","JETS","KWEB","DRAM",
            "TQQQ","SPXL","SOXL","UDOW","LABU","FNGU","TECL","UPRO","TNA","FAS","ERX",
            "SQQQ","SPXS","SOXS","SDOW","TZA","FAZ","ERY",
            "SSO","QLD","DDM","UWM","SDS","QID","DXD","TWM",
            "VXX","UVXY","SVXY","VIXY","SVOL",
            "GLD","IAU","SLV","USO","UNG","GDX","GDXJ","OIH",
            "TLT","HYG","LQD","TBT","TMF","SHY","IEF","JNK",
            "EEM","EFA","FXI","EWJ","EWZ","EWY","IEMG",
            "ARKK","ARKG","ARKW","ARKF",
            "IBIT","FBTC","BITB","ARKB","WGMI",
        }

        def _scan_unusual(ticker):
            hits    = []
            is_etf  = ticker in _ETF_SET
            # ETFs: 1.5× vol/OI (vs 3× stocks), $250K premium (vs $500K), 1-60d expiry (vs 1-30d)
            min_voi  = 1.5  if is_etf else 3.0
            min_prem = 250_000 if is_etf else 500_000
            max_days = 60   if is_etf else 30
            try:
                tkr   = yf.Ticker(ticker)
                price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
                if price <= 0: return hits
                exps = tkr.options
                if not exps: return hits
                for exp in exps:
                    try:
                        days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                        if not (1 <= days_out <= max_days): continue
                        calls = tkr.option_chain(exp).calls
                        for _, row in calls.iterrows():
                            strike = float(row.get("strike", 0) or 0)
                            vol    = int(row.get("volume", 0) or 0)
                            oi     = int(row.get("openInterest", 0) or 0)
                            last   = float(row.get("lastPrice", 0) or 0)
                            iv     = float(row.get("impliedVolatility", 0) or 0)
                            if strike <= 0 or last <= 0 or vol < 50: continue
                            if strike < price * 0.15: continue
                            pre_otm = (strike - price) / price * 100
                            if pre_otm < -15: continue   # skip deep ITM — hedges
                            if pre_otm > 50: continue    # skip lottery-ticket far OTM
                            vol_oi = round(vol / max(oi, 1), 2)
                            if vol_oi < min_voi: continue
                            prem = round(vol * last * 100, 0)
                            if prem < min_prem: continue
                            urgency = "EXPIRING" if days_out <= 7 else "NEAR" if days_out <= 14 else "SHORT"
                            hits.append({
                                "ticker":   ticker,
                                "price":    round(price, 2),
                                "strike":   round(strike, 2),
                                "expiry":   exp,
                                "days_out": days_out,
                                "volume":   vol,
                                "oi":       oi,
                                "vol_oi":   vol_oi,
                                "prem":     int(prem),
                                "otm_pct":  round(pre_otm, 1),
                                "iv":       round(iv * 100, 1),
                                "urgency":  urgency,
                                "is_etf":   is_etf,
                            })
                    except Exception: continue
            except Exception: pass
            return hits

        all_hits = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_scan_unusual, t): t for t in DEFAULT_LEADERBOARD}
            for fut in as_completed(futures):
                all_hits.extend(fut.result() or [])

        all_hits.sort(key=lambda x: x["vol_oi"], reverse=True)

        # If live scan returned nothing (rate limited / cold start), fall back to DB
        if not all_hits:
            try:
                with _psycopg2.connect(_DB_URL) as _conn, _conn.cursor() as _cur:
                    _cur.execute("""
                        SELECT ticker, price::float, strike::float, expiry, days_out,
                               volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                               iv::float, urgency
                        FROM unusual_calls_log
                        WHERE last_seen >= CURRENT_DATE
                          AND vol_oi >= 3
                          AND prem >= 100000
                        ORDER BY vol_oi DESC LIMIT 80
                    """)
                    _cols = ["ticker","price","strike","expiry","days_out","volume","oi","vol_oi","prem","otm_pct","iv","urgency"]
                    for _row in _cur.fetchall():
                        _d = dict(zip(_cols, _row))
                        _d["is_etf"] = _d["ticker"] in _ETF_SET
                        all_hits.append(_d)
            except Exception:
                pass

        out = {"hits": all_hits[:80], "total": len(all_hits), "scanned": len(DEFAULT_LEADERBOARD)}
        app._unusual_calls_cache    = out
        app._unusual_calls_cache_ts = _dt.now()
        if all_hits:
            _save_unusual_calls_to_db(all_hits)
        return jsonify(out)


@app.route("/stock-api/unusual-calls/microcap", methods=["GET"])
def unusual_calls_microcap():
    """Return micro/small-cap unusual call options from DB, newest first."""
    days_back = min(int(request.args.get("days", 3)), 30)
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, price::float, strike::float, expiry, days_out,
                       volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                       iv::float, urgency, cap_tier,
                       first_seen AT TIME ZONE 'UTC' AS first_seen,
                       last_seen  AT TIME ZONE 'UTC' AS last_seen
                FROM unusual_calls_microcap_log
                WHERE last_seen >= NOW() - (%(days)s || ' days')::INTERVAL
                ORDER BY prem DESC
                LIMIT 200
            """, {"days": days_back})
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if r.get("first_seen"): r["first_seen"] = r["first_seen"].isoformat()
                if r.get("last_seen"):  r["last_seen"]  = r["last_seen"].isoformat()
        return jsonify({"signals": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "signals": [], "total": 0}), 500


@app.route("/stock-api/unusual-calls-log", methods=["GET"])
def unusual_calls_log():
    """Return all-time unusual calls history from DB, newest first."""
    ticker = request.args.get("ticker", "").upper().strip()
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            if ticker:
                cur.execute("""
                    SELECT ticker, price::float, strike::float, expiry, days_out,
                           volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                           iv::float, urgency,
                           first_seen AT TIME ZONE 'UTC' AS first_seen,
                           last_seen  AT TIME ZONE 'UTC' AS last_seen
                    FROM unusual_calls_log
                    WHERE ticker = %s
                      AND prem >= 500000
                    ORDER BY first_seen DESC
                    LIMIT 500
                """, (ticker,))
            else:
                cur.execute("""
                    SELECT ticker, price::float, strike::float, expiry, days_out,
                           volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                           iv::float, urgency,
                           first_seen AT TIME ZONE 'UTC' AS first_seen,
                           last_seen  AT TIME ZONE 'UTC' AS last_seen
                    FROM unusual_calls_log
                    WHERE prem >= 500000
                    ORDER BY first_seen DESC
                    LIMIT 500
                """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if r.get("first_seen"): r["first_seen"] = r["first_seen"].isoformat()
                if r.get("last_seen"):  r["last_seen"]  = r["last_seen"].isoformat()
        return jsonify({"signals": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "signals": [], "total": 0}), 500


@app.route("/stock-api/eod-sweeps", methods=["GET"])
def eod_sweeps():
    """
    End-of-day institutional sweep detector.
    Finds aggressive bullish naked calls placed in the last 90 min of trading
    (3:00–4:30 PM ET = 19:00–20:30 UTC) — signals institutions positioning for next day.
    """
    import math as _math
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    bust   = request.args.get("bust", "0") == "1"
    _cache = getattr(app, "_eod_sweeps_cache", None)
    _ts    = getattr(app, "_eod_sweeps_cache_ts", None)
    if not bust and _cache and _ts and (_dt.now() - _ts).total_seconds() < 120:
        return jsonify(_cache)

    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            # First try today's EOD data only
            cur.execute("""
                SELECT ticker, price::float, strike::float, expiry, days_out,
                       vol_oi::float, prem::bigint, otm_pct::float, iv::float,
                       urgency, last_seen
                FROM unusual_calls_log
                WHERE last_seen::date = CURRENT_DATE
                  AND EXTRACT(HOUR FROM last_seen AT TIME ZONE 'UTC') BETWEEN 14 AND 23
                  AND days_out BETWEEN 1 AND 15
                  AND vol_oi  >= 5
                  AND prem    >= 300000
                  AND otm_pct BETWEEN -2 AND 25
                  AND strike  >= price * 0.97
                ORDER BY last_seen DESC, vol_oi DESC
            """)
            rows_today = cur.fetchall()

            if rows_today:
                rows_raw = rows_today
            else:
                # Fall back to most recent trading day (up to 5 days)
                cur.execute("""
                    SELECT ticker, price::float, strike::float, expiry, days_out,
                           vol_oi::float, prem::bigint, otm_pct::float, iv::float,
                           urgency, last_seen
                    FROM unusual_calls_log
                    WHERE last_seen >= NOW() - INTERVAL '5 days'
                      AND EXTRACT(HOUR FROM last_seen AT TIME ZONE 'UTC') BETWEEN 18 AND 23
                      AND days_out BETWEEN 1 AND 15
                      AND vol_oi  >= 5
                      AND prem    >= 300000
                      AND otm_pct BETWEEN -2 AND 25
                      AND strike  >= price * 0.97
                    ORDER BY last_seen DESC, vol_oi DESC
                """)
                rows_raw = cur.fetchall()

        cols = ["ticker","price","strike","expiry","days_out","vol_oi","prem","otm_pct","iv","urgency","last_seen"]
        rows = [dict(zip(cols, r)) for r in rows_raw]

        if not rows:
            out = {"signals": [], "generated_at": _dt.now().isoformat(), "total": 0,
                   "note": "No EOD sweeps found in the last 2 days. Run a scan at market close (3:30–4:15 PM ET) to capture institutional positioning."}
            return jsonify(out)

        from collections import defaultdict as _dd
        by_ticker = _dd(list)
        for r in rows:
            by_ticker[r["ticker"]].append(r)

        results = []
        for ticker, strikes in by_ticker.items():
            num_strikes  = len(strikes)
            total_prem   = sum(s["prem"] for s in strikes)
            max_vol_oi   = max(s["vol_oi"] for s in strikes)
            avg_iv       = sum(s["iv"] or 0 for s in strikes) / num_strikes
            best         = max(strikes, key=lambda s: s["vol_oi"])
            price        = best["price"]

            # How late was the latest detection? Closer to 4 PM ET = higher bonus
            # last_seen is stored as UTC; 4 PM ET = 20:00 UTC
            latest_ts = max(s["last_seen"] for s in strikes)
            if hasattr(latest_ts, "hour"):
                hour_utc = latest_ts.hour + latest_ts.minute / 60.0
            else:
                try:
                    from dateutil import parser as _dp
                    _dt_obj = _dp.parse(str(latest_ts))
                    hour_utc = _dt_obj.hour + _dt_obj.minute / 60.0
                except Exception:
                    hour_utc = 19.5
            # Minutes before 4 PM ET close (20:00 UTC); earlier = more time to close
            minutes_to_close = max(0, (20.0 - hour_utc) * 60)
            # Late bonus: detected within 30 min of close gets 2.0x, 60 min = 1.5x, 90 min = 1.0x
            late_bonus = 2.0 if minutes_to_close <= 30 else 1.5 if minutes_to_close <= 60 else 1.0

            # Scoring
            vol_oi_factor = _math.log1p(max_vol_oi) / _math.log1p(5)
            prem_factor   = _math.log1p(total_prem / 1_000_000) / _math.log1p(1)
            iv_bonus      = 1.8 if avg_iv >= 90 else 1.5 if avg_iv >= 70 else 1.2 if avg_iv >= 50 else 1.0
            sweep_mult    = 1.0 + 0.4 * (num_strikes - 1)
            score         = round(vol_oi_factor * prem_factor * iv_bonus * sweep_mult * late_bonus, 1)

            grade = "EXTREME" if score >= 12 else "HIGH" if score >= 7 else "ELEVATED" if score >= 4 else "MODERATE"

            urgency_rank = {"EXPIRING": 3, "SHORT": 2, "NEAR": 1}.get(best["urgency"], 1)

            results.append({
                "ticker":          ticker,
                "price":           round(price, 2),
                "score":           score,
                "grade":           grade,
                "num_strikes":     num_strikes,
                "total_prem_m":    round(total_prem / 1_000_000, 2),
                "max_vol_oi":      round(max_vol_oi, 1),
                "avg_iv":          round(avg_iv, 1),
                "latest_at":       str(latest_ts),
                "minutes_to_close": round(minutes_to_close, 0),
                "urgency":         best["urgency"],
                "strikes": [
                    {
                        "ticker":           ticker,
                        "price":            round(s["price"], 2),
                        "strike":           float(s["strike"]),
                        "expiry":           str(s["expiry"]),
                        "days_out":         s["days_out"],
                        "vol_oi":           round(s["vol_oi"], 1),
                        "prem":             s["prem"],
                        "otm_pct":          round(s["otm_pct"], 1),
                        "iv":               round(s["iv"] or 0, 1),
                        "urgency":          s["urgency"],
                        "detected_at":      str(s["last_seen"]),
                        "minutes_to_close": round(minutes_to_close, 0),
                    }
                    for s in sorted(strikes, key=lambda x: x["vol_oi"], reverse=True)
                ],
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        for i, r in enumerate(results):
            r["rank"] = i + 1

        out = {"signals": results, "generated_at": _dt.now().isoformat(), "total": len(results)}
        app._eod_sweeps_cache    = out
        app._eod_sweeps_cache_ts = _dt.now()
        return jsonify(out)

    except Exception as e:
        import traceback
        print(f"[eod_sweeps] error: {e}\n{traceback.format_exc()}", file=__import__("sys").stderr)
        return jsonify({"error": str(e), "signals": []}), 500


@app.route("/stock-api/admin/run-eod-scan", methods=["POST"])
def admin_run_eod_scan():
    """
    Admin endpoint: manually trigger an EOD unusual-calls scan to populate
    unusual_calls_log for the current trading day.  Runs in a background thread
    and returns immediately so the HTTP request doesn't time out.
    """
    import threading as _thr
    import traceback as _tb

    def _bg():
        try:
            _run_unusual_calls_scan("manual-trigger")
            # Bust the EOD sweeps cache so the next request re-queries fresh data
            if hasattr(app, "_eod_sweeps_cache"):
                app._eod_sweeps_cache    = None
                app._eod_sweeps_cache_ts = None
            print("[admin_run_eod_scan] cache busted — fresh data ready")
        except Exception as exc:
            print(f"[admin_run_eod_scan] error: {exc}\n{_tb.format_exc()}")

    _thr.Thread(target=_bg, daemon=True).start()
    return jsonify({
        "status":  "started",
        "message": "EOD unusual-calls scan running in background (~2-3 min). "
                   "Call /stock-api/eod-sweeps?bust=1 afterwards to see fresh data.",
        "tickers": len(DEFAULT_LEADERBOARD),
    })


@app.route("/stock-api/conviction-calls", methods=["GET"])
def conviction_calls():
    """
    High-conviction call screener: stocks where calls DRAMATICALLY outpace puts.
    Criteria: multiple strikes lighting up simultaneously, Vol/OI ≥5x, ≤30d expiry,
    premium ≥$500K. Groups by ticker and scores by institutional sweep pattern.
    """
    import math as _math
    from datetime import datetime as _dt

    _cache = getattr(app, "_conv_calls_cache", None)
    _ts    = getattr(app, "_conv_calls_cache_ts", None)
    force  = request.args.get("force") == "1"
    if not force and _cache and _ts and (_dt.now() - _ts).total_seconds() < 900:  # 15-min cache
        return jsonify(_cache)

    try:
        _base_sql = """
                SELECT ticker, price::float, strike::float, expiry, days_out,
                       vol_oi::float, prem::bigint, otm_pct::float, iv::float,
                       urgency, last_seen
                FROM unusual_calls_log
                WHERE last_seen >= {interval}
                  AND days_out BETWEEN 1 AND 30
                  AND vol_oi  >= 5
                  AND prem    >= 500000
                  AND otm_pct BETWEEN -2 AND 30
                  AND strike  >= price * 0.97
                ORDER BY last_seen DESC, vol_oi DESC
            """
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            # Try today first, fall back to 24 hours if no results
            cur.execute(_base_sql.format(interval="CURRENT_DATE"))
            rows_today = cur.fetchall()
            if rows_today:
                rows_raw = rows_today
                window_label = "today"
            else:
                cur.execute(_base_sql.format(interval="NOW() - INTERVAL '1 day'"))
                rows_raw = cur.fetchall()
                window_label = "24h"

        cols = ["ticker","price","strike","expiry","days_out","vol_oi","prem","otm_pct","iv","urgency","last_seen"]
        rows = [dict(zip(cols, r)) for r in rows_raw]

        if not rows:
            return jsonify({"signals": [], "generated_at": _dt.now().isoformat(), "note": "No high-conviction calls found. Run a scan in 🚨 Unusual Calls first."})

        # Group by ticker — multi-strike sweep = strongest institutional signal
        from collections import defaultdict as _dd
        by_ticker = _dd(list)
        for r in rows:
            by_ticker[r["ticker"]].append(r)

        results = []
        for ticker, strikes in by_ticker.items():
            # Aggregate metrics
            num_strikes    = len(strikes)
            total_prem     = sum(s["prem"] for s in strikes)
            max_vol_oi     = max(s["vol_oi"] for s in strikes)
            avg_iv         = sum(s["iv"] or 0 for s in strikes) / num_strikes
            best_strike    = max(strikes, key=lambda s: s["vol_oi"])
            most_recent    = max(strikes, key=lambda s: s["last_seen"])
            price          = most_recent["price"]   # always use freshest price

            # Urgency: EXPIRING > SHORT > NEAR
            urgency_rank   = {"EXPIRING": 3, "SHORT": 2, "NEAR": 1}.get(best_strike["urgency"], 1)

            # IV conviction bonus (screaming options → screaming conviction)
            iv_bonus = 1.8 if avg_iv >= 90 else 1.5 if avg_iv >= 70 else 1.2 if avg_iv >= 50 else 1.0

            # Multi-strike sweep multiplier — every extra strike adds more conviction
            sweep_mult = 1.0 + 0.4 * (num_strikes - 1)

            # Compound conviction score
            prem_factor   = _math.log(total_prem / 1_000_000 + 1) + 1
            vol_oi_factor = _math.log(max_vol_oi + 1)
            score = round(vol_oi_factor * prem_factor * iv_bonus * sweep_mult * urgency_rank, 2)

            # Conviction label
            if score >= 12:   conviction = "EXTREME"
            elif score >= 7:  conviction = "HIGH"
            elif score >= 4:  conviction = "ELEVATED"
            else:             conviction = "MODERATE"

            # Urgency label for display
            days_out_min = min(s["days_out"] for s in strikes if s["days_out"])
            urgency_label = "EXPIRING" if days_out_min <= 5 else f"{days_out_min}D"

            results.append({
                "ticker":         ticker,
                "price":          price,
                "score":          score,
                "conviction":     conviction,
                "num_strikes":    num_strikes,
                "total_prem_m":   round(total_prem / 1_000_000, 2),
                "max_vol_oi":     round(max_vol_oi, 1),
                "avg_iv":         round(avg_iv, 1),
                "urgency":        urgency_label,
                "strikes":        sorted(strikes, key=lambda s: s["vol_oi"], reverse=True)[:8],
            })

        # Sort by conviction score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:15]
        for i, r in enumerate(results):
            r["rank"] = i + 1
            for s in r["strikes"]:
                if s.get("last_seen"):
                    s["last_seen"] = s["last_seen"].isoformat()

        out = {
            "signals":      results,
            "generated_at": _dt.now().isoformat(),
            "total":        len(results),
        }
        app._conv_calls_cache    = out
        app._conv_calls_cache_ts = _dt.now()
        return jsonify(out)
    except Exception as e:
        import traceback
        print(f"[conviction_calls] error: {e}\n{traceback.format_exc()}", file=__import__("sys").stderr)
        return jsonify({"error": str(e), "signals": []}), 500


@app.route("/stock-api/ai-short-calls", methods=["GET"])
def ai_short_calls():
    """5 AI-picked short-term call plays (≤30d expiry) drawn from the Unusual Calls scanner."""
    import sys
    from datetime import datetime as _dt
    from openai import OpenAI

    _cache = getattr(app, "_aisc_cache", None)
    _ts    = getattr(app, "_aisc_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 3600:
        return jsonify(_cache)

    # 1. Try in-memory live cache first
    uc   = getattr(app, "_unusual_calls_cache", None)
    hits = (uc.get("hits") or []) if uc else []

    # 2. Fall back to DB if live cache is empty (e.g. weekend / after restart)
    if not hits:
        try:
            with _psycopg2.connect(_DB_URL) as conn_fb, conn_fb.cursor() as cur_fb:
                cur_fb.execute("""
                    SELECT ticker, strike, expiry, days_out, vol_oi, prem, otm_pct, iv, urgency, price
                    FROM unusual_calls_log
                    WHERE last_seen >= NOW() - INTERVAL '5 days'
                      AND days_out BETWEEN 1 AND 30
                      AND prem >= 500000
                      AND otm_pct BETWEEN -2 AND 30
                      AND strike >= price * 0.97
                    ORDER BY last_seen DESC, vol_oi DESC
                    LIMIT 25
                """)
                rows = cur_fb.fetchall()
            hits = [
                {"ticker": r[0], "strike": r[1], "expiry": str(r[2]), "days_out": r[3],
                 "vol_oi": float(r[4]), "prem": int(r[5]), "otm_pct": float(r[6]),
                 "iv": float(r[7]) if r[7] else 0.0, "urgency": r[8], "price": float(r[9])}
                for r in rows
            ]
        except Exception as _dbe:
            print(f"[ai_short_calls] DB fallback error: {_dbe}", file=sys.stderr)

    if not hits:
        return jsonify({
            "error": "No unusual calls data available. Run a scan in the 🚨 Unusual Calls tab first, then come back.",
            "picks": [], "generated_at": None
        })

    hits = hits[:10]

    try:
        oai = OpenAI(
            base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL"),
            api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
            timeout=90.0,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    signals_text = "\n".join([
        f"{i+1}. {h['ticker']} | ${h['strike']} call | exp {h['expiry']} ({h['days_out']}d) | "
        f"Vol/OI={h['vol_oi']}x | prem=${h['prem']:,} | {'+' if h['otm_pct']>0 else ''}{h['otm_pct']}% OTM | "
        f"IV={h.get('iv',0)}% | urgency={h['urgency']} | stock_price=${h['price']:.2f}"
        for i, h in enumerate(hits)
    ])

    user_msg = f"""These are today's unusual call signals (Vol/OI ≥3x, ≤30 day expiry, OTM/near-ATM calls only):

{signals_text}

Select the 5 BEST short-term call trade opportunities from this list. Rank by conviction.
Criteria: highest Vol/OI (fresh institutional buying), reasonable OTM% (not too far), premium size (commitment), days_out (urgency), urgency tier.

For each pick output a JSON object with ALL these fields:
- ticker (string)
- strike (number — use the strike from the signal)
- expiry (string YYYY-MM-DD — use from signal)
- days_out (integer — from signal)
- vol_oi (number — from signal)
- prem (integer — from signal)
- stock_price (number — from signal)
- otm_pct (number — from signal)
- breakeven (number — strike + estimated option premium per share; estimate option price as prem / (vol * 100) if vol known, else use a reasonable estimate)
- conviction ("HIGH" | "MEDIUM")
- urgency (string — from signal)
- thesis (string — 2 sentences MAX: why this signal is high conviction, what the move scenario is)
- why_it_stands_out (string — 1 sentence: the single most compelling data point)

Return a JSON array of exactly 5 objects. Sort by conviction (HIGH first). JSON only, no markdown."""

    system_msg = "You are a quantitative options analyst. You identify the highest-conviction short-term call trades from unusual options activity. Output valid JSON only."

    def _stream_ai():
        chunks = []
        finish = "unknown"
        stream = oai.chat.completions.create(
            model="gpt-4o-mini",
            max_completion_tokens=1500,
            stream=True,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                chunks.append(delta)
            if chunk.choices and chunk.choices[0].finish_reason:
                finish = chunk.choices[0].finish_reason
        return "".join(chunks).strip(), finish

    def _extract_json(raw):
        if "```" in raw:
            for part in raw.split("```"):
                stripped = part.lstrip("json").strip()
                if stripped.startswith("["):
                    return stripped
        if not raw.startswith("["):
            s = raw.find("["); e2 = raw.rfind("]") + 1
            if s >= 0 and e2 > s:
                return raw[s:e2]
        return raw

    try:
        import time as _time
        raw, finish = _stream_ai()
        raw = _extract_json(raw)

        if not raw:
            print("[ai_short_calls] empty on first attempt — retrying in 6s", file=sys.stderr, flush=True)
            _time.sleep(6)
            raw, finish = _stream_ai()
            raw = _extract_json(raw)

        if not raw:
            return jsonify({"error": f"AI returned no content (finish={finish}). Hit Regenerate to try again.", "picks": []}), 500

        try:
            picks = _json.loads(raw)
        except Exception:
            from json_repair import repair_json as _rj
            picks = _json.loads(_rj(raw))
        out = {
            "picks": picks,
            "generated_at": _dt.now().isoformat(),
            "signals_evaluated": len(hits),
        }
        app._aisc_cache    = out
        app._aisc_cache_ts = _dt.now()
        # Persist to daily log (skips if already saved today)
        try:
            import threading as _scl_thr
            _today_str = _dt.now().strftime("%Y-%m-%d")
            _scl_thr.Thread(
                target=_save_ai_short_calls_to_log,
                args=(picks, _today_str),
                daemon=True,
            ).start()
        except Exception as _sle:
            print(f"[ai_short_calls] log save error: {_sle}", file=sys.stderr)
        return jsonify(out)
    except Exception as e:
        import traceback
        print(f"[ai_short_calls] error: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        return jsonify({"error": str(e), "picks": []}), 500


@app.route("/stock-api/ai-short-calls-log", methods=["GET"])
def ai_short_calls_log():
    """Return full AI short-calls history with daily win rates (breakeven-based)."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, trade_date, rank, ticker, strike, expiry, days_out,
                       vol_oi, prem, stock_price, otm_pct, breakeven,
                       conviction, urgency, thesis, why_it_stands_out, outcome,
                       t1_price, t3_price, t5_price,
                       t1_pct, t3_pct, t5_pct,
                       t1_win, t3_win, t5_win,
                       expiry_price, expiry_pct, expiry_win,
                       created_at
                FROM ai_short_calls_log
                ORDER BY trade_date DESC, rank ASC
            """)
            rows = cur.fetchall()
            cols = ["id","trade_date","rank","ticker","strike","expiry","days_out",
                    "vol_oi","prem","stock_price","otm_pct","breakeven",
                    "conviction","urgency","thesis","why_it_stands_out","outcome",
                    "t1_price","t3_price","t5_price",
                    "t1_pct","t3_pct","t5_pct",
                    "t1_win","t3_win","t5_win",
                    "expiry_price","expiry_pct","expiry_win",
                    "created_at"]
            picks = []
            for row in rows:
                d = dict(zip(cols, row))
                d["trade_date"] = str(d["trade_date"]) if d["trade_date"] else None
                d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
                picks.append(d)

            def _wr(key):
                vals = [p[key] for p in picks if p[key] is not None]
                if not vals: return None
                return round(sum(1 for v in vals if v) / len(vals) * 100, 1)

            win_rates = {
                "expiry": _wr("expiry_win"),
                "t1": _wr("t1_win"),
                "t3": _wr("t3_win"),
                "t5": _wr("t5_win"),
            }

            # Per-day stats
            from collections import defaultdict as _dd
            by_date = _dd(lambda: {"total": 0, "wins": 0, "losses": 0, "open": 0})
            for p in picks:
                d = p["trade_date"]
                by_date[d]["total"] += 1
                if p["outcome"] == "WIN":   by_date[d]["wins"]   += 1
                elif p["outcome"] == "LOSS": by_date[d]["losses"] += 1
                else:                        by_date[d]["open"]   += 1
            day_stats = {d: dict(v) for d, v in sorted(by_date.items(), reverse=True)}

            return jsonify({
                "picks": picks,
                "count": len(picks),
                "win_rates": win_rates,
                "by_date": day_stats,
            })
    except Exception as e:
        return jsonify({"error": str(e), "picks": [], "count": 0,
                        "win_rates": {"expiry": None, "t1": None, "t3": None, "t5": None},
                        "by_date": {}}), 500


@app.route("/stock-api/multi-signal", methods=["GET"])
def multi_signal_convergence():
    """Multi-signal convergence scanner — 25 signal conditions including cross-referenced caches."""
    import yfinance as yf
    from datetime import datetime as _ms_dt

    _cache = getattr(app, "_ms_cache", None)
    _ts    = getattr(app, "_ms_cache_ts", None)
    if _cache and _ts and (_ms_dt.now() - _ts).total_seconds() < 600:
        return jsonify(_cache)

    SIGNAL_DEFS = [
        # ── Quant / price-action signals (computed live) ──────────────────
        ("VOLUME_SURGE",       "🔥 Volume Surge",        "Relative volume ≥ 3× average"),
        ("MORNING_RUNNER",     "🌅 Morning Runner",      "Gap up + rel vol ≥ 1.8× on the day"),
        ("NEAR_52WK_HIGH",     "📈 Near 52wk High",      "Within 3% of 52-week high"),
        ("ABOVE_52WK_HIGH",    "🚀 New 52wk High",       "Price currently above 52-week high"),
        ("MOMENTUM",           "⚡ Momentum",            "Day change ≥ 3%"),
        ("BIG_MOVE",           "💥 Big Move",            "Day change ≥ 5%"),
        ("MICRO_SQUEEZE",      "💎 Micro Squeeze",       "Market cap < $300M + rel vol ≥ 2×"),
        ("SECTOR_STRENGTH",    "💪 Sector Strength",     "Up ≥ 2% with rel vol ≥ 1.5×"),
        # ── Cross-referenced from real signal caches ──────────────────────
        ("DARK_POOL_HIT",      "🌑 Dark Pool",           "Appeared in dark pool scanner today"),
        ("UNUSUAL_CALLS",      "🎯 Unusual Calls",       "Flagged in unusual options call activity"),
        ("SQUEEZE_SETUP",      "💥 Squeeze Setup",       "Flagged by squeeze / low-float scanner"),
        ("MORNING_SCAN",       "🌅 Morning Scan Hit",    "Appeared in morning runners scanner"),
        ("BULL_FLOW",          "📈 Bull Flow",           "In the bull flow top signals today"),
        ("WHALE_ACTIVITY",     "🐋 Whale Activity",      "Whale block trade detected"),
        ("AI_TRADE_SIGNAL",    "🤖 AI Trade Signal",     "AI Trade tab selected this ticker today"),
        ("CHEAP_OPTIONS",      "💰 Cheap Options",       "IV rank < 20 — options priced below historical avg"),
        ("HIGH_QUANT_SCORE",   "🏆 High Quant Score",   "Composite quant score in top 20 of universe"),
        ("GAMMA_WALL",         "🧲 Gamma Wall",          "Near/above gamma wall — dealer buying amplifies move"),
        ("VOL_CRUSH_SETUP",    "📉 Vol Crush Setup",     "Inflated IV ahead of catalyst — market pricing in big move"),
        ("MAX_PAIN_PULL",      "⚡ Max Pain Pull",       "Price below max pain — MM pressure targets upside"),
        ("CALL_INTENT_HIGH",   "🎯 Call Intent",         "High call OI / unusual call intent detected"),
        # ── High-conviction quant filters ─────────────────────────────────────
        ("MARKET_REGIME",      "🌍 Market Regime",       "SPY above 50-day MA and VIX < 25 — risk-on macro environment"),
        ("RELATIVE_STRENGTH",  "🏆 Relative Strength",   "Outperforming S&P 500 by 15%+ over last 3 months (RS top tier)"),
        ("SHORT_SQUEEZE_FUEL", "💣 Short Squeeze Fuel",  "Short interest > 10% — trapped shorts amplify any upside move"),
        ("EPS_REVISION_UP",    "📊 EPS Revision Up",     "Earnings growth > 10% — analysts revising estimates higher"),
        # ── Technical / momentum signals ──────────────────────────────────────
        ("RSI_SETUP",          "📐 RSI Setup",           "RSI 14-day between 30–62 — not overbought, has room to run"),
        ("MACD_BULLISH",       "📈 MACD Cross",          "MACD crossed above signal line — momentum turning bullish"),
        ("BB_SQUEEZE",         "🗜️ BB Squeeze",           "Bollinger Bands at 6-month tightest — major breakout imminent"),
        ("GOLDEN_CROSS",       "⭐ Golden Cross",         "50-day MA above 200-day MA — institutional trend confirmation"),
        ("MOMENTUM_12_1",      "🚀 12-1 Momentum",       "Up 15%+ over prior 11 months (Jegadeesh-Titman quant factor)"),
        ("OBV_DIVERGE",        "🔊 OBV Accumulation",    "On-balance volume rising while price flat — quiet institutional buying"),
        # ── Fundamental quality signals ───────────────────────────────────────
        ("FLOAT_ROTATION",     "🔄 Float Rotation",      "Daily volume > 30% of float — high conviction from active participants"),
        ("PRICE_TARGET_UP",    "🎯 Analyst Target",      "Analyst consensus target >15% above current price — institutional conviction"),
        ("HIGH_QUALITY",       "💎 High Quality",        "ROE > 15% with manageable debt — quality factor used by quant funds"),
        ("ANALYST_UPGRADE",    "⬆️ Analyst Upgrade",     "Buy/Outperform upgrade in last 21 days — institutional re-rating signal"),
        ("EARNINGS_BEAT",      "✅ Earnings Beater",      "Beat EPS estimates in 75%+ of recent quarters — systematic under-model"),
        ("REVENUE_ACCEL",      "📈 Revenue Accel",       "QoQ revenue growth rate accelerating — institutional re-rating trigger"),
        ("MARGIN_EXPAND",      "📊 Margin Expansion",    "Gross margin expanding QoQ — pricing power and operating leverage"),
        # ── Macro health filters ──────────────────────────────────────────────
        ("VIX_CONTANGO",       "📉 VIX Contango",        "VIX spot below 3-month VIX — term structure healthy, no fear event priced in"),
        ("HYG_HEALTHY",        "🔋 Credit Healthy",      "High-yield bonds not diverging from equities — no credit stress signal"),
    ]

    # ── Build ticker sets from existing caches (read-only, safe) ─────────
    def _get_tickers(cache_attr, *keys):
        data = getattr(app, cache_attr, None) or {}
        for k in keys:
            items = data.get(k)
            if items:
                return set(r.get("ticker", "") for r in items if r.get("ticker"))
        return set()

    dp_tickers       = _get_tickers("_dp_cache",             "results")
    uc_tickers       = _get_tickers("_unusual_calls_cache",  "hits", "results")
    mr_tickers       = _get_tickers("_mr_cache",             "runners", "results")
    sq_tickers       = _get_tickers("_sq_cache",             "results", "hits")
    bf_tickers       = _get_tickers("_bf_cache",             "results", "top10")
    whale_tickers    = _get_tickers("_whale_cache",          "blocks", "results")
    ait_tickers      = _get_tickers("_ait_cache",            "picks", "results")
    cheap_iv_tickers = set(
        r.get("ticker", "") for r in (getattr(app, "_ivs_cache", None) or {}).get("rows", [])
        if r.get("setup") == "CHEAP_OPTIONS" and r.get("ticker")
    )
    cs_rows          = sorted(
        (getattr(app, "_cs_cache", None) or {}).get("results", []),
        key=lambda x: x.get("score", 0), reverse=True
    )
    high_quant_tickers = set(r.get("ticker", "") for r in cs_rows[:25] if r.get("ticker"))
    gw_tickers       = _get_tickers("_gw_cache",             "results")
    vc_tickers       = _get_tickers("_vc_cache",             "results")
    oi_tickers       = _get_tickers("_oi_cache",             "results")
    mp_map           = {r["ticker"]: r["max_pain"] for r in (getattr(app, "_mp_cache", None) or {}).get("results", []) if r.get("ticker") and r.get("max_pain")}

    # ── Get sector rotation context ───────────────────────────────────────
    sr_data = getattr(app, "_sr_cache", None) or {}
    sectors = sr_data.get("sectors", [])
    top_sector    = sectors[0]  if sectors else None
    bottom_sector = sectors[-1] if len(sectors) > 1 else None
    sector_context = {
        "top":    {"ticker": top_sector["ticker"],    "name": top_sector["name"],    "day_chg": top_sector["day_chg"],    "flow": top_sector["flow"]}    if top_sector    else None,
        "bottom": {"ticker": bottom_sector["ticker"], "name": bottom_sector["name"], "day_chg": bottom_sector["day_chg"], "flow": bottom_sector["flow"]} if bottom_sector else None,
    }

    # ── Global macro signals (computed once for all tickers) ─────────────────
    market_regime_on   = False
    spy_return_3mo_ref = 0.0
    vix_contango       = False
    hyg_healthy        = True
    try:
        _spy = yf.Ticker("SPY").history(period="70d")["Close"]
        _vix = yf.Ticker("^VIX").history(period="5d")["Close"]
        _spy_now  = float(_spy.iloc[-1])
        _spy_50ma = float(_spy.rolling(50).mean().iloc[-1])
        _vix_now  = float(_vix.iloc[-1])
        market_regime_on   = (_spy_now > _spy_50ma) and (_vix_now < 25)
        _idx = max(0, len(_spy) - 63)
        spy_return_3mo_ref = (_spy_now - float(_spy.iloc[_idx])) / float(_spy.iloc[_idx]) if float(_spy.iloc[_idx]) > 0 else 0
    except Exception:
        pass
    try:
        _vix3m_h = yf.Ticker("^VIX3M").history(period="5d")["Close"]
        _vixs_h  = yf.Ticker("^VIX").history(period="5d")["Close"]
        vix_contango = float(_vixs_h.iloc[-1]) < float(_vix3m_h.iloc[-1])
    except Exception:
        pass
    try:
        _hyg20 = yf.Ticker("HYG").history(period="20d")["Close"]
        _spy20 = yf.Ticker("SPY").history(period="20d")["Close"]
        _h_ret = (float(_hyg20.iloc[-1]) - float(_hyg20.iloc[0])) / (float(_hyg20.iloc[0]) or 1)
        _s_ret = (float(_spy20.iloc[-1]) - float(_spy20.iloc[0])) / (float(_spy20.iloc[0]) or 1)
        hyg_healthy = _h_ret > _s_ret - 0.02
    except Exception:
        pass

    results = []

    def _check(ticker):
        try:
            fi       = yf.Ticker(ticker).fast_info
            price    = float(getattr(fi, "last_price",                 0) or 0)
            prev_cl  = float(getattr(fi, "previous_close",             0) or 0)
            avg_vol  = float(getattr(fi, "three_month_average_volume",  1) or 1)
            today_vol= float(getattr(fi, "last_volume",                0) or 0)
            high52   = float(getattr(fi, "year_high",                  0) or 0)
            mkt_cap  = float(getattr(fi, "market_cap",                 0) or 0)

            if price <= 0 or prev_cl <= 0:
                return None

            day_chg  = round((price - prev_cl) / prev_cl * 100, 2)
            rel_vol  = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
            pct_from_high = round((price - high52) / high52 * 100, 2) if high52 > 0 else -100

            fired = []
            # ── Live quant checks ─────────────────────────────────────────
            if rel_vol >= 3.0:
                fired.append("VOLUME_SURGE")
            if rel_vol >= 1.8 and 1.0 <= day_chg <= 25:
                fired.append("MORNING_RUNNER")
            if pct_from_high >= -3.0:
                fired.append("NEAR_52WK_HIGH")
            if pct_from_high >= 0:
                fired.append("ABOVE_52WK_HIGH")
            if day_chg >= 3.0:
                fired.append("MOMENTUM")
            if day_chg >= 5.0:
                fired.append("BIG_MOVE")
            if mkt_cap > 0 and mkt_cap < 300_000_000 and rel_vol >= 2.0:
                fired.append("MICRO_SQUEEZE")
            if day_chg >= 2.0 and rel_vol >= 1.5:
                fired.append("SECTOR_STRENGTH")
            # ── Cross-reference existing signal caches ────────────────────
            if ticker in dp_tickers:           fired.append("DARK_POOL_HIT")
            if ticker in uc_tickers:           fired.append("UNUSUAL_CALLS")
            if ticker in sq_tickers:           fired.append("SQUEEZE_SETUP")
            if ticker in mr_tickers:           fired.append("MORNING_SCAN")
            if ticker in bf_tickers:           fired.append("BULL_FLOW")
            if ticker in whale_tickers:        fired.append("WHALE_ACTIVITY")
            if ticker in ait_tickers:          fired.append("AI_TRADE_SIGNAL")
            if ticker in cheap_iv_tickers:     fired.append("CHEAP_OPTIONS")
            if ticker in high_quant_tickers:   fired.append("HIGH_QUANT_SCORE")
            if ticker in gw_tickers:           fired.append("GAMMA_WALL")
            if ticker in vc_tickers:           fired.append("VOL_CRUSH_SETUP")
            if ticker in oi_tickers:           fired.append("CALL_INTENT_HIGH")
            mp_level = mp_map.get(ticker)
            if mp_level and price > 0 and price < mp_level:
                fired.append("MAX_PAIN_PULL")

            # ── Global macro (fire for all tickers when conditions met) ──────────
            if market_regime_on: fired.append("MARKET_REGIME")
            if vix_contango:     fired.append("VIX_CONTANGO")
            if hyg_healthy:      fired.append("HYG_HEALTHY")

            _tk2 = yf.Ticker(ticker)

            # ── 1-year OHLCV → all technical signals ──────────────────────────
            try:
                _df = _tk2.history(period="1y")
                _cl = _df["Close"] if len(_df) > 0 else None
                _vl = _df["Volume"] if len(_df) > 0 else None
                if _cl is not None and len(_cl) >= 30:
                    _n = float(_cl.iloc[-1])
                    # Relative Strength vs SPY (3-month)
                    _i3 = max(0, len(_cl) - 63)
                    if (float(_cl.iloc[_i3]) or 0) > 0:
                        if (_n - float(_cl.iloc[_i3])) / float(_cl.iloc[_i3]) > spy_return_3mo_ref + 0.15:
                            fired.append("RELATIVE_STRENGTH")
                    # 12-1 Momentum Factor (11-month return skipping last month)
                    if len(_cl) >= 60:
                        _i12 = max(0, len(_cl) - 252)
                        _i1  = max(0, len(_cl) - 21)
                        _base = float(_cl.iloc[_i12])
                        _top  = float(_cl.iloc[_i1]) if len(_cl) > 21 else _n
                        if _base > 0 and (_top - _base) / _base > 0.15:
                            fired.append("MOMENTUM_12_1")
                    # RSI 14-day (30–62 = not overbought, room to run)
                    if len(_cl) >= 15:
                        _d = _cl.diff()
                        _g = _d.clip(lower=0).rolling(14).mean()
                        _l = (-_d.clip(upper=0)).rolling(14).mean()
                        _rsi_val = float((100 - 100 / (1 + _g / _l.replace(0, 1e-9))).iloc[-1])
                        if 30 <= _rsi_val <= 62:
                            fired.append("RSI_SETUP")
                    # MACD bullish crossover (crossed up in last 5 sessions)
                    if len(_cl) >= 35:
                        _mc = _cl.ewm(span=12, adjust=False).mean() - _cl.ewm(span=26, adjust=False).mean()
                        _ms = _mc.ewm(span=9, adjust=False).mean()
                        _cx = (_mc > _ms).tolist()
                        if len(_cx) >= 6 and _cx[-1] and not _cx[-6]:
                            fired.append("MACD_BULLISH")
                    # Bollinger Band Squeeze (width in bottom 20% of 6-month range)
                    if len(_cl) >= 126:
                        _bw = (4 * _cl.rolling(20).std()) / _cl.rolling(20).mean().replace(0, 1e-9)
                        _lo = _bw.rolling(126).min(); _hi = _bw.rolling(126).max()
                        if (_bw.iloc[-1] - _lo.iloc[-1]) / (_hi.iloc[-1] - _lo.iloc[-1] + 1e-9) <= 0.20:
                            fired.append("BB_SQUEEZE")
                    # Golden Cross (50MA > 200MA)
                    if len(_cl) >= 200:
                        if float(_cl.rolling(50).mean().iloc[-1]) > float(_cl.rolling(200).mean().iloc[-1]):
                            fired.append("GOLDEN_CROSS")
                    # OBV Accumulation (OBV rising, price flat/up)
                    if _vl is not None and len(_vl) >= 22:
                        _sgn = (_cl.diff() > 0).astype(float) - (_cl.diff() < 0).astype(float)
                        _obv = (_vl * _sgn).cumsum()
                        _pc  = float(_cl.iloc[-22]) or 1
                        if float(_obv.iloc[-1]) > float(_obv.iloc[-22]) and (_n - _pc) / _pc >= -0.03:
                            fired.append("OBV_DIVERGE")
            except Exception:
                pass

            # ── Info-based signals ────────────────────────────────────────────
            try:
                _info = _tk2.info
                if (_info.get("shortPercentOfFloat") or 0) > 0.10:
                    fired.append("SHORT_SQUEEZE_FUEL")
                if (_info.get("earningsGrowth") or 0) > 0.10 or (_info.get("revenueGrowth") or 0) > 0.15:
                    fired.append("EPS_REVISION_UP")
                _flt = _info.get("floatShares") or 0
                if _flt > 0 and today_vol > 0 and today_vol / _flt > 0.30:
                    fired.append("FLOAT_ROTATION")
                _tgt = _info.get("targetMeanPrice") or 0
                if _tgt > 0 and price > 0 and (_tgt - price) / price > 0.15:
                    fired.append("PRICE_TARGET_UP")
                if (_info.get("returnOnEquity") or 0) > 0.15 and (_info.get("debtToEquity") or 999) < 150:
                    fired.append("HIGH_QUALITY")
            except Exception:
                pass

            # ── Analyst upgrades (last 15 recommendations) ───────────────────
            try:
                _recs = _tk2.recommendations
                if _recs is not None and len(_recs) > 0:
                    _buys = {"buy", "strong buy", "outperform", "overweight", "positive", "add"}
                    if _recs.tail(15)["To Grade"].str.lower().str.strip().isin(_buys).any():
                        fired.append("ANALYST_UPGRADE")
            except Exception:
                pass

            # ── Earnings beat rate (≥75% of recent quarters) ─────────────────
            try:
                _eh = _tk2.earnings_history
                if _eh is not None and len(_eh) >= 3:
                    _sc = next((c for c in _eh.columns if "surprise" in c.lower()), None)
                    if _sc and (_eh[_sc] > 0).mean() >= 0.75:
                        fired.append("EARNINGS_BEAT")
            except Exception:
                pass

            # ── Revenue acceleration + Gross margin expansion ─────────────────
            try:
                _qis = _tk2.quarterly_income_stmt
                if _qis is not None and len(_qis.columns) >= 4:
                    if "Total Revenue" in _qis.index:
                        _rv = _qis.loc["Total Revenue"].iloc[:4].astype(float).values
                        if abs(_rv[1]) > 0 and abs(_rv[2]) > 0:
                            if (_rv[0] - _rv[1]) / abs(_rv[1]) > (_rv[1] - _rv[2]) / abs(_rv[2]) + 0.02:
                                fired.append("REVENUE_ACCEL")
                    if "Gross Profit" in _qis.index and "Total Revenue" in _qis.index:
                        _gp = _qis.loc["Gross Profit"].iloc[:4].astype(float).values
                        _rv = _qis.loc["Total Revenue"].iloc[:4].astype(float).values
                        if _rv[0] > 0 and _rv[2] > 0 and _gp[0]/_rv[0] > _gp[2]/_rv[2] + 0.01:
                            fired.append("MARGIN_EXPAND")
            except Exception:
                pass

            if len(fired) < 2:
                return None

            return {
                "ticker":        ticker,
                "price":         round(price, 2),
                "day_chg":       day_chg,
                "rel_vol":       rel_vol,
                "pct_from_high": pct_from_high,
                "mkt_cap_b":     round(mkt_cap / 1e9, 2) if mkt_cap else None,
                "signals":       fired,
                "score":         len(fired),
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=25) as ex:
        futures = {ex.submit(_check, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: (x["score"], x["rel_vol"]), reverse=True)
    signal_defs_map = {s[0]: {"id": s[0], "label": s[1], "desc": s[2]} for s in SIGNAL_DEFS}
    out = {
        "hits":           results[:40],
        "total":          len(results),
        "scanned":        len(DEFAULT_LEADERBOARD),
        "signal_defs":    signal_defs_map,
        "max_signals":    len(SIGNAL_DEFS),
        "sector_context": sector_context,
        "cache_status": {
            "dark_pool":       len(dp_tickers),
            "unusual_calls":   len(uc_tickers),
            "morning_runners": len(mr_tickers),
            "squeeze":         len(sq_tickers),
            "bull_flow":       len(bf_tickers),
            "whale":           len(whale_tickers),
            "ai_trades":       len(ait_tickers),
            "cheap_iv":        len(cheap_iv_tickers),
            "quant_score":     len(high_quant_tickers),
            "gamma_wall":      len(gw_tickers),
            "vol_crush":       len(vc_tickers),
            "call_intent":     len(oi_tickers),
            "max_pain":        len(mp_map),
            "market_regime":   1 if market_regime_on else 0,
            "vix_contango":    1 if vix_contango else 0,
            "hyg_healthy":     1 if hyg_healthy else 0,
        },
        "market_regime_on":  market_regime_on,
        "vix_contango":       vix_contango,
        "hyg_healthy":        hyg_healthy,
    }
    app._ms_cache    = out
    app._ms_cache_ts = _ms_dt.now()
    return jsonify(out)


@app.route("/stock-api/multi-signal/ai-thesis", methods=["POST"])
def multi_signal_ai_thesis():
    """Generate an AI trade thesis for a ticker based on all its convergent signals."""
    try:
        body    = request.get_json(force=True) or {}
        ticker  = (body.get("ticker") or "").upper().strip()
        signals = body.get("signals") or []
        price   = body.get("price", 0)
        day_chg = body.get("day_chg", 0)
        rel_vol = body.get("rel_vol", 0)
        pct_from_high = body.get("pct_from_high", 0)
        mkt_cap_b     = body.get("mkt_cap_b")

        if not ticker or not signals:
            return jsonify({"error": "ticker and signals required"}), 400

        cap_str = f"${mkt_cap_b:.1f}B" if mkt_cap_b and mkt_cap_b >= 1 else (f"${mkt_cap_b*1000:.0f}M" if mkt_cap_b else "unknown")

        # ── Earnings context (non-blocking, best-effort) ──────────────────────
        earnings_ctx = ""
        try:
            earn = _check_earnings(ticker)
            if earn:
                imp = earn.get("implied_move_pct")
                eps = earn.get("eps_estimate")
                imp_str = f"±{imp}% implied move (ATM straddle)" if imp else "implied move unavailable"
                eps_str = f"EPS estimate: ${eps:+.2f}" if eps is not None else "EPS estimate: N/A"
                earnings_ctx = f"""
⚠️  EARNINGS EVENT: {ticker} reports in {earn['days_until']} day(s) on {earn['earnings_date']}
    Options market pricing {imp_str}
    {eps_str}
"""
        except Exception:
            pass

        prompt = f"""You are a professional quantitative trader analyzing a convergence signal alert.

Ticker: {ticker}
Current Price: ${price}
Day Change: {day_chg:+.2f}%
Relative Volume: {rel_vol:.2f}x average
Distance from 52-week high: {pct_from_high:+.2f}%
Market Cap: {cap_str}
{earnings_ctx}
Signals firing simultaneously ({len(signals)}/8):
{chr(10).join(f"  ✅ {s}" for s in signals)}

Write a concise, actionable trade thesis with:
1. SETUP SUMMARY (2-3 sentences on why {len(signals)} converging signals matters)
2. BULL CASE (price target, catalyst, timeframe){" — factor in the earnings event and implied move" if earnings_ctx else ""}
3. BEAR CASE / RISK (what could go wrong){" — include earnings binary risk" if earnings_ctx else ""}
4. CONVICTION: CRITICAL / HIGH / WATCH / NOISE
5. SUGGESTED ACTION (buy calls, watch for entry, avoid) — naked long calls only, no spreads

Be direct, specific, and professional. No disclaimers."""

        client = OpenAI(
            api_key=os.environ["AI_INTEGRATIONS_OPENAI_API_KEY"],
            base_url=os.environ["AI_INTEGRATIONS_OPENAI_BASE_URL"],
        )
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=500,
        )
        return jsonify({"ticker": ticker, "thesis": resp.choices[0].message.content.strip()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/iv-rank", methods=["GET"])
def iv_rank():
    """IV rank (volatility rank) for a single ticker using options + historical price data."""
    import yfinance as yf, numpy as np
    ticker = (request.args.get("ticker") or "AAPL").upper().strip()
    try:
        tkr  = yf.Ticker(ticker)
        hist = tkr.history(period="1y", interval="1d")
        if len(hist) < 20:
            return jsonify({"error": "Not enough price history"}), 400

        # Calculate historical volatility (annualized) at multiple windows
        log_ret = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
        hv30  = float(log_ret[-30:].std()  * np.sqrt(252) * 100) if len(log_ret) >= 30  else None
        hv60  = float(log_ret[-60:].std()  * np.sqrt(252) * 100) if len(log_ret) >= 60  else None
        hv90  = float(log_ret[-90:].std()  * np.sqrt(252) * 100) if len(log_ret) >= 90  else None

        # Rolling 30-day HV for each day in the past year → used for HV rank
        rolling_hv30 = (
            log_ret.rolling(30).std() * np.sqrt(252) * 100
        ).dropna()
        hv_min  = float(rolling_hv30.min())
        hv_max  = float(rolling_hv30.max())
        hv_rank = round((hv30 - hv_min) / (hv_max - hv_min) * 100, 1) if hv30 and hv_max > hv_min else None

        # Get current IV from options chain (ATM, nearest 30-45d expiry)
        iv30 = None
        expiries = tkr.options
        from datetime import datetime, timedelta
        now = datetime.now()
        target = now + timedelta(days=30)
        best_exp = None
        best_diff = 9999
        for exp in (expiries or []):
            try:
                exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                diff = abs((exp_dt - target).days)
                if diff < best_diff and (exp_dt - now).days >= 7:
                    best_diff = diff
                    best_exp = exp
            except Exception:
                pass

        if best_exp:
            try:
                chain = tkr.option_chain(best_exp)
                fi    = tkr.fast_info
                spot  = float(getattr(fi, "last_price", 0) or 0)
                calls = chain.calls
                puts  = chain.puts
                if not calls.empty and spot > 0:
                    calls["dist"] = (calls["strike"] - spot).abs()
                    atm_call = calls.nsmallest(1, "dist")
                    iv30_call = float(atm_call["impliedVolatility"].iloc[0]) * 100 if not atm_call.empty else None
                    puts_f = puts.copy()
                    puts_f["dist"] = (puts_f["strike"] - spot).abs()
                    atm_put = puts_f.nsmallest(1, "dist")
                    iv30_put = float(atm_put["impliedVolatility"].iloc[0]) * 100 if not atm_put.empty else None
                    if iv30_call and iv30_put:
                        iv30 = round((iv30_call + iv30_put) / 2, 1)
                    elif iv30_call:
                        iv30 = round(iv30_call, 1)
            except Exception:
                pass

        iv_hv_ratio = round(iv30 / hv30, 2) if iv30 and hv30 else None

        fi = tkr.fast_info
        price = float(getattr(fi, "last_price", 0) or 0)
        prev  = float(getattr(fi, "previous_close", 0) or 0)
        day_chg = round((price - prev) / prev * 100, 2) if prev > 0 else 0

        # IV rank proxy: use IV vs rolling HV range as a volatility premium signal
        iv_rank_val = None
        if iv30 and hv_max > hv_min:
            iv_rank_val = round((iv30 - hv_min) / (hv_max - hv_min) * 100, 1)
            iv_rank_val = min(max(iv_rank_val, 0), 100)

        return jsonify({
            "ticker":      ticker,
            "price":       round(price, 2),
            "day_chg":     day_chg,
            "hv30":        round(hv30, 1)  if hv30  else None,
            "hv60":        round(hv60, 1)  if hv60  else None,
            "hv90":        round(hv90, 1)  if hv90  else None,
            "hv_min":      round(hv_min, 1),
            "hv_max":      round(hv_max, 1),
            "hv_rank":     hv_rank,
            "iv30":        iv30,
            "iv_rank":     iv_rank_val,
            "iv_hv_ratio": iv_hv_ratio,
            "expiry_used": best_exp,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


IV_SCAN_TICKERS = [
    "AAPL","MSFT","NVDA","TSLA","AMZN","GOOGL","META","AMD","SMCI","PLTR",
    "ARM","COIN","MSTR","HOOD","SOFI","RIVN","LCID","NKLA","JOBY","ACHR",
    "GME","AMC","BBBY","SPCE","CLOV","SNDL","MVIS","WKHS","RIDE","GOEV",
    "UPST","AFRM","OPEN","LMND","PSFE","PAYA","LFMD","HIMS","GENI","BODY",
    "RBLX","U","DKNG","PENN","SKLZ","FTIV","SPNV","APPH","MVST","NKLA",
]

@app.route("/stock-api/iv-rank/scan", methods=["GET"])
def iv_rank_scan():
    """Scan a curated set of liquid/volatile tickers for interesting IV setups."""
    import yfinance as yf, numpy as np
    from datetime import datetime as _ivs_dt

    _cache = getattr(app, "_ivs_cache", None)
    _ts    = getattr(app, "_ivs_cache_ts", None)
    if _cache and _ts and (_ivs_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    from datetime import datetime, timedelta

    results = []

    def _scan_iv(ticker):
        try:
            tkr  = yf.Ticker(ticker)
            hist = tkr.history(period="1y", interval="1d")
            if len(hist) < 30:
                return None

            log_ret = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
            hv30 = float(log_ret[-30:].std() * np.sqrt(252) * 100)
            rolling_hv30 = (log_ret.rolling(30).std() * np.sqrt(252) * 100).dropna()
            hv_min = float(rolling_hv30.min())
            hv_max = float(rolling_hv30.max())
            hv_rank = round((hv30 - hv_min) / (hv_max - hv_min) * 100, 1) if hv_max > hv_min else 50

            # Try to get ATM IV
            iv30 = None
            expiries = tkr.options
            now_dt = datetime.now()
            target = now_dt + timedelta(days=30)
            best_exp = None
            best_diff = 9999
            for exp in (expiries or []):
                try:
                    exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                    diff = abs((exp_dt - target).days)
                    if diff < best_diff and (exp_dt - now_dt).days >= 7:
                        best_diff = diff
                        best_exp = exp
                except Exception:
                    pass

            if best_exp:
                try:
                    chain = tkr.option_chain(best_exp)
                    fi = tkr.fast_info
                    spot = float(getattr(fi, "last_price", 0) or 0)
                    if not chain.calls.empty and spot > 0:
                        calls = chain.calls.copy()
                        calls["dist"] = (calls["strike"] - spot).abs()
                        atm = calls.nsmallest(1, "dist")
                        if not atm.empty:
                            iv30 = round(float(atm["impliedVolatility"].iloc[0]) * 100, 1)
                except Exception:
                    pass

            fi    = tkr.fast_info
            price = float(getattr(fi, "last_price", 0) or 0)
            prev  = float(getattr(fi, "previous_close", 0) or 0)
            day_chg = round((price - prev) / prev * 100, 2) if prev > 0 else 0

            iv_hv_ratio = round(iv30 / hv30, 2) if iv30 and hv30 else None
            iv_rank_val = round((iv30 - hv_min) / (hv_max - hv_min) * 100, 1) if iv30 and hv_max > hv_min else hv_rank

            # Setup classification
            setup = "NEUTRAL"
            if iv30 and iv_hv_ratio:
                if iv_rank_val < 20:
                    setup = "CHEAP_OPTIONS"
                elif iv_rank_val > 80:
                    setup = "EXPENSIVE_OPTIONS"
                if iv_hv_ratio > 1.5:
                    setup = "IV_PREMIUM"
                elif iv_hv_ratio < 0.8 and iv_rank_val < 30:
                    setup = "CHEAP_OPTIONS"

            return {
                "ticker":      ticker,
                "price":       round(price, 2),
                "day_chg":     day_chg,
                "hv30":        round(hv30, 1),
                "hv_rank":     hv_rank,
                "iv30":        iv30,
                "iv_rank":     round(iv_rank_val, 1),
                "iv_hv_ratio": iv_hv_ratio,
                "setup":       setup,
            }
        except Exception:
            return None

    tickers_dedup = list(dict.fromkeys(IV_SCAN_TICKERS))
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_scan_iv, t): t for t in tickers_dedup}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: (x.get("iv_rank") or 50), reverse=True)
    out = {"rows": results, "scanned": len(tickers_dedup)}
    app._ivs_cache    = out
    app._ivs_cache_ts = _ivs_dt.now()
    return jsonify(out)


@app.route("/stock-api/52week-breakout", methods=["GET"])
def breakout_52week():
    """52-week high breakout scanner — price near/above 52wk high + volume confirmation."""
    import yfinance as yf
    from datetime import datetime as _bk_dt

    _cache = getattr(app, "_bk_cache", None)
    _ts    = getattr(app, "_bk_cache_ts", None)
    if _cache and _ts and (_bk_dt.now() - _ts).total_seconds() < 900:
        return jsonify(_cache)

    results = []

    def _scan_bk(ticker):
        try:
            fi    = yf.Ticker(ticker).fast_info
            price = float(getattr(fi, "last_price",                0) or 0)
            high52 = float(getattr(fi, "year_high",                0) or 0)
            low52  = float(getattr(fi, "year_low",                 0) or 0)
            avg_vol= float(getattr(fi, "three_month_average_volume",1) or 1)
            today_vol = float(getattr(fi, "last_volume",           0) or 0)
            mkt_cap   = float(getattr(fi, "market_cap",            0) or 0)
            prev_close= float(getattr(fi, "previous_close",        0) or 0)

            if price <= 0 or high52 <= 0 or low52 <= 0:
                return None

            pct_from_high = round((price - high52) / high52 * 100, 2)
            # Only stocks within 3% below or above their 52-week high
            if pct_from_high < -3.0:
                return None

            rel_vol   = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
            if rel_vol < 1.3:
                return None

            range_52   = high52 - low52
            range_pos  = round((price - low52) / range_52 * 100, 1) if range_52 > 0 else 100
            day_chg_pct= round((price - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
            mkt_cap_b  = round(mkt_cap / 1e9, 2) if mkt_cap else None

            # Score: higher rel_vol * closer to/above high
            above_bonus = max(0, pct_from_high)        # bonus if actually above 52wk high
            score = round(rel_vol * (1 + above_bonus / 10), 2)

            return {
                "ticker":        ticker,
                "price":         round(price, 2),
                "high_52":       round(high52, 2),
                "low_52":        round(low52, 2),
                "pct_from_high": pct_from_high,
                "range_pos":     range_pos,
                "rel_vol":       rel_vol,
                "day_chg_pct":   day_chg_pct,
                "mkt_cap_b":     mkt_cap_b,
                "score":         score,
                "breakout":      pct_from_high >= 0,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(_scan_bk, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    out = {"hits": results[:40], "total": len(results), "scanned": len(DEFAULT_LEADERBOARD)}
    app._bk_cache    = out
    app._bk_cache_ts = _bk_dt.now()
    return jsonify(out)


@app.route("/stock-api/sector-rotation", methods=["GET"])
def sector_rotation():
    """Sector rotation heatmap — 11 SPDR sector ETFs with flow direction signals."""
    import yfinance as yf
    from datetime import datetime as _sr_dt

    _cache = getattr(app, "_sr_cache", None)
    _ts    = getattr(app, "_sr_cache_ts", None)
    if _cache and _ts and (_sr_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    SECTORS = [
        ("XLK",  "Technology"),
        ("XLF",  "Financials"),
        ("XLV",  "Healthcare"),
        ("XLE",  "Energy"),
        ("XLI",  "Industrials"),
        ("XLY",  "Cons. Discretionary"),
        ("XLP",  "Cons. Staples"),
        ("XLU",  "Utilities"),
        ("XLB",  "Materials"),
        ("XLRE", "Real Estate"),
        ("XLC",  "Communication"),
    ]

    results = []
    ticker_names = {t: n for t, n in SECTORS}
    tickers_list = [t for t, n in SECTORS]

    # Batch download 1-year daily data (1 API call for all 11 ETFs — avoids rate limiting)
    try:
        batch = yf.download(
            tickers_list, period="1y", interval="1d",
            auto_adjust=True, progress=False, threads=True
        )
        # Multi-ticker download returns multi-level columns: (field, ticker)
        for ticker in tickers_list:
            try:
                name = ticker_names[ticker]
                if len(tickers_list) > 1:
                    close  = batch["Close"][ticker].dropna()
                    volume = batch["Volume"][ticker].dropna()
                    high_s = batch["High"][ticker].dropna()
                    low_s  = batch["Low"][ticker].dropna()
                else:
                    close  = batch["Close"].dropna()
                    volume = batch["Volume"].dropna()
                    high_s = batch["High"].dropna()
                    low_s  = batch["Low"].dropna()

                if len(close) < 2:
                    continue

                price      = float(close.iloc[-1])
                prev_close = float(close.iloc[-2])
                if price <= 0 or prev_close <= 0:
                    continue

                high52 = float(high_s.max())
                low52  = float(low_s.min())
                today_vol = float(volume.iloc[-1]) if len(volume) > 0 else 0
                # 3-month average volume (~63 trading days)
                avg_vol = float(volume.iloc[-63:].mean()) if len(volume) >= 10 else float(volume.mean())
                avg_vol = max(avg_vol, 1)

                day_chg   = round((price - prev_close) / prev_close * 100, 2)
                rel_vol   = round(today_vol / avg_vol, 2)
                range_pos = round((price - low52) / (high52 - low52) * 100, 1) if high52 > low52 else 50

                wk1_chg = round((price - float(close.iloc[-5])) / float(close.iloc[-5]) * 100, 2) if len(close) >= 5 else None
                mo1_chg = round((price - float(close.iloc[-21])) / float(close.iloc[-21]) * 100, 2) if len(close) >= 21 else None

                if day_chg > 0 and rel_vol >= 1.1:
                    flow = "INFLOW"
                elif day_chg < 0 and rel_vol >= 1.1:
                    flow = "OUTFLOW"
                elif day_chg > 0:
                    flow = "RISING"
                elif day_chg < 0:
                    flow = "FALLING"
                else:
                    flow = "NEUTRAL"

                results.append({
                    "ticker":    ticker,
                    "name":      name,
                    "price":     round(price, 2),
                    "day_chg":   day_chg,
                    "wk1_chg":   wk1_chg,
                    "mo1_chg":   mo1_chg,
                    "rel_vol":   rel_vol,
                    "range_pos": range_pos,
                    "flow":      flow,
                })
            except Exception:
                continue
    except Exception:
        pass

    results.sort(key=lambda x: x["day_chg"], reverse=True)
    out = {"sectors": results, "scanned": len(SECTORS)}
    app._sr_cache    = out
    app._sr_cache_ts = _sr_dt.now()
    return jsonify(out)


@app.route("/stock-api/squeeze-setup", methods=["GET"])
def squeeze_setup():
    """High-conviction short squeeze + low-float breakout scanner."""
    import yfinance as yf
    from datetime import datetime as _sq_dt

    _cache = getattr(app, "_sq_cache", None)
    _ts    = getattr(app, "_sq_cache_ts", None)
    if _cache and _ts and (_sq_dt.now() - _ts).total_seconds() < 900:
        return jsonify(_cache)

    results = []

    def _scan_sq(ticker):
        try:
            tkr  = yf.Ticker(ticker)
            fi   = tkr.fast_info
            info = tkr.info

            price = float(getattr(fi, "last_price", 0) or 0)
            if price <= 0:
                return None

            sfp     = float(info.get("shortPercentOfFloat", 0) or 0) * 100
            dtc     = float(info.get("shortRatio",          0) or 0)
            float_sh = float(info.get("floatShares",        0) or 0)
            mkt_cap  = float(info.get("marketCap",          0) or 0) or float(getattr(fi, "market_cap", 0) or 0)
            mkt_cap_b = round(mkt_cap / 1e9, 2) if mkt_cap else None

            avg_vol   = float(getattr(fi, "three_month_average_volume", 1) or 1)
            today_vol = float(getattr(fi, "last_volume", 0) or 0)
            rel_vol   = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0

            vol_pct_float = round(today_vol / float_sh * 100, 2) if float_sh > 0 else None
            float_m       = round(float_sh / 1e6, 2) if float_sh > 0 else None

            is_squeeze   = sfp >= 15 and dtc >= 5
            is_low_float = float_m is not None and float_m <= 20 and (vol_pct_float or 0) >= 8

            if not is_squeeze and not is_low_float:
                return None

            signal_type = "BOTH" if (is_squeeze and is_low_float) else ("SQUEEZE" if is_squeeze else "LOW_FLOAT")
            sq_comp  = min(sfp * dtc, 200)      if is_squeeze   else 0
            lf_comp  = min((vol_pct_float or 0) * rel_vol * 5, 200) if is_low_float else 0
            score    = round(sq_comp + lf_comp, 1)

            return {
                "ticker":          ticker,
                "price":           round(price, 2),
                "signal_type":     signal_type,
                "short_float_pct": round(sfp, 1),
                "days_to_cover":   round(dtc, 1),
                "float_m":         float_m,
                "vol_pct_float":   vol_pct_float,
                "rel_vol":         rel_vol,
                "mkt_cap_b":       mkt_cap_b,
                "score":           score,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_scan_sq, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    out = {"setups": results[:40], "total": len(results), "scanned": len(DEFAULT_LEADERBOARD)}
    app._sq_cache    = out
    app._sq_cache_ts = _sq_dt.now()
    return jsonify(out)


@app.route("/stock-api/squeeze-setup/ai-signal", methods=["POST"])
def squeeze_ai_signal():
    """AI conviction analysis for squeeze + low-float setups with optional Twilio SMS."""
    import os, json, re, sys
    from openai import OpenAI

    body = request.get_json(silent=True) or {}
    rows = body.get("rows", [])
    if not rows:
        return jsonify({"error": "No setup data provided"}), 400

    rows = sorted(rows, key=lambda r: -r.get("score", 0))[:20]

    lines = []
    for r in rows:
        parts = [
            f"ticker={r['ticker']}",
            f"signal={r['signal_type']}",
            f"short_float={r.get('short_float_pct', 0)}%",
            f"days_to_cover={r.get('days_to_cover', 0)}d",
        ]
        if r.get("float_m") is not None:
            parts.append(f"float={r['float_m']}M_shares")
        if r.get("vol_pct_float") is not None:
            parts.append(f"vol_pct_float={r['vol_pct_float']}%")
        parts.append(f"rel_vol={r.get('rel_vol', 0)}x")
        if r.get("mkt_cap_b") is not None:
            parts.append(f"mktcap=${r['mkt_cap_b']}B")
        parts.append(f"score={r.get('score', 0)}")
        lines.append("  " + " | ".join(parts))

    prompt = f"""You are a short squeeze and low-float breakout specialist.

Signal types:
- SQUEEZE: high short float (>=15%) + high days-to-cover (>=5d) — forced buying avalanche on any catalyst
- LOW_FLOAT: tiny float (<=20M shares) + heavy volume (>=8% of float today) — tiny supply, explosive on demand
- BOTH: both conditions simultaneously — the most dangerous setup possible

Key metrics:
- short_float: what % of float is sold short. >25% = extreme. >35% = explosive powder keg
- days_to_cover: how many trading days shorts need to fully exit at normal volume. >8d = violent squeeze potential
- float_m: total float in millions. <5M = micro float, any buying pressure moves it 10%+
- vol_pct_float: today's vol as % of total float. >15% means the float is rotating rapidly — something is happening now
- rel_vol: today's vol vs 3-month avg. >5x = 5 times normal activity — unusual accumulation

Setups to analyze:
{chr(10).join(lines)}

Conviction levels:
- CRITICAL: BOTH signal, OR SQUEEZE with short_float>25% + days_to_cover>8 + rel_vol>3 — near-certain violent move on any catalyst
- HIGH: SQUEEZE with short_float>18% + days_to_cover>5, OR LOW_FLOAT with vol_pct_float>12% + rel_vol>4 — strong setup
- WATCH: setup present but one or more metrics are borderline — monitor for volume confirmation
- NOISE: metrics look okay on one dimension but the composite picture doesn't confirm explosiveness

Return ONLY valid JSON, no markdown:
{{"signals":[{{"ticker":"XXXX","signal":"CRITICAL","thesis":"2-3 punchy sentences: what makes this explosive, what the catalyst trigger would look like, and the key risk.","confidence":92}}]}}"""

    try:
        client = OpenAI(
            api_key=os.environ["AI_INTEGRATIONS_OPENAI_API_KEY"],
            base_url=os.environ["AI_INTEGRATIONS_OPENAI_BASE_URL"],
        )
        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=3000,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw).strip()
        raw = re.sub(r'\s*```$', '', raw).strip()
        parsed  = json.loads(raw)
        signals = parsed.get("signals", [])

        sms_sent = []
        t_sid   = os.getenv("TWILIO_ACCOUNT_SID")
        t_token = os.getenv("TWILIO_AUTH_TOKEN")
        t_from  = os.getenv("TWILIO_FROM_NUMBER")
        t_to    = os.getenv("TWILIO_TO_NUMBER")

        if all([t_sid, t_token, t_from, t_to]):
            try:
                from twilio.rest import Client as TwilioClient
                tw       = TwilioClient(t_sid, t_token)
                critical = [s for s in signals if s.get("signal") in ("CRITICAL", "HIGH")][:3]
                for sig in critical:
                    row = next((r for r in rows if r["ticker"] == sig["ticker"]), {})
                    msg = (
                        f"🔥 StockScanner AI — {sig['signal']} SETUP\n"
                        f"{sig['ticker']} ${row.get('price','?')} | {row.get('signal_type','')} signal\n"
                        f"Short: {row.get('short_float_pct',0):.1f}% | {row.get('days_to_cover',0):.1f}d to cover\n"
                        f"Float: {row.get('float_m','?')}M shares | {row.get('rel_vol',0):.1f}x vol\n"
                        f"{sig['thesis'][:160]}"
                    )
                    tw.messages.create(body=msg, from_=t_from, to=t_to)
                    sms_sent.append(sig["ticker"])
            except Exception as sms_err:
                print(f"[squeeze_ai] SMS error: {sms_err}", file=sys.stderr, flush=True)

        return jsonify({"signals": signals, "sms_sent": sms_sent})

    except Exception as e:
        import traceback
        print(f"[squeeze_ai] error: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        return jsonify({"error": str(e), "signals": []}), 500


@app.route("/stock-api/morning-runners", methods=["GET"])
def morning_runners():
    """Morning runners — scans all tickers for pre-market volume spikes + gap moves."""
    import yfinance as yf
    from datetime import datetime as _mr_dt

    _cache = getattr(app, "_mr_cache", None)
    _ts    = getattr(app, "_mr_cache_ts", None)
    if _cache and _ts and (_mr_dt.now() - _ts).total_seconds() < 600:
        return jsonify(_cache)

    results = []

    def _scan_mr(ticker):
        try:
            fi         = yf.Ticker(ticker).fast_info
            price      = float(getattr(fi, "last_price",                0) or 0)
            prev_close = float(getattr(fi, "previous_close",            0) or 0)
            if price <= 0 or prev_close <= 0:
                return None
            gap_pct    = round((price - prev_close) / prev_close * 100, 2)
            avg_vol    = float(getattr(fi, "three_month_average_volume", 1) or 1)
            today_vol  = float(getattr(fi, "last_volume",               0) or 0)
            rel_vol    = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
            mkt_cap    = float(getattr(fi, "market_cap",                0) or 0)
            mkt_cap_b  = round(mkt_cap / 1e9, 2) if mkt_cap else None

            if rel_vol < 1.5 and abs(gap_pct) < 4.0:
                return None

            # score = relative volume * (|gap%| + 1) — big vol + big gap = top of list
            score   = round(rel_vol * (abs(gap_pct) + 1), 2)
            squeeze = bool(mkt_cap_b is not None and mkt_cap_b < 2.0 and rel_vol >= 3.0)

            return {
                "ticker":     ticker,
                "price":      round(price, 2),
                "prev_close": round(prev_close, 2),
                "gap_pct":    gap_pct,
                "rel_vol":    rel_vol,
                "avg_vol":    int(avg_vol),
                "today_vol":  int(today_vol),
                "mkt_cap_b":  mkt_cap_b,
                "score":      score,
                "squeeze":    squeeze,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(_scan_mr, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    out = {
        "runners": results[:40],
        "total":   len(results),
        "scanned": len(DEFAULT_LEADERBOARD),
    }
    app._mr_cache    = out
    app._mr_cache_ts = _mr_dt.now()
    return jsonify(out)


import xml.etree.ElementTree as _ET_xml
import re as _re_mp

# ── MARKET PRESS ──────────────────────────────────────────────────────────────
_mp_cache    = None
_mp_cache_ts = None
_mp_lock     = threading.Lock()
_MP_TTL      = 120   # 2-minute cache

_MP_FEEDS = [
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US", "category": "MARKETS",     "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^DJI&region=US&lang=en-US",  "category": "MARKETS",     "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=QQQ&region=US&lang=en-US",   "category": "TECH",        "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GLD&region=US&lang=en-US",   "category": "COMMODITIES", "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TLT&region=US&lang=en-US",   "category": "RATES",       "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",   "category": "MARKETS",     "source": "Yahoo Finance"},
]

def _fetch_rss_feed(feed_info):
    import urllib.request as _ureq
    import datetime as _dt_mp2
    items = []
    try:
        req = _ureq.Request(feed_info["url"], headers={"User-Agent": "Mozilla/5.0 StockScannerBot/1.0"})
        resp = _ureq.urlopen(req, timeout=8)
        root = _ET_xml.fromstring(resp.read())
        for item in root.findall(".//item"):
            title    = (item.findtext("title")      or "").strip()
            link     = (item.findtext("link")       or "").strip()
            pub_date = (item.findtext("pubDate")    or "").strip()
            desc     = _re_mp.sub(r"<[^>]+>", "", item.findtext("description") or "")[:220].strip()
            if not title:
                continue
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub_date)
                ts = dt.isoformat()
                now_utc = _dt_mp2.datetime.now(_dt_mp2.timezone.utc)
                diff_m = int((now_utc - dt).total_seconds() / 60)
                age = (f"{diff_m}m ago" if diff_m < 60
                       else f"{diff_m//60}h ago" if diff_m < 1440
                       else f"{diff_m//1440}d ago")
            except Exception:
                ts, age = pub_date, ""
            items.append({
                "title":        title,
                "url":          link,
                "source":       feed_info["source"],
                "category":     feed_info["category"],
                "published_at": ts,
                "age":          age,
                "summary":      desc,
            })
    except Exception:
        pass
    return items

@app.route("/stock-api/market-press", methods=["GET"])
def market_press():
    global _mp_cache, _mp_cache_ts
    import datetime as _dt_mp3
    with _mp_lock:
        if _mp_cache and _mp_cache_ts and (_dt_mp3.datetime.now() - _mp_cache_ts).total_seconds() < _MP_TTL:
            return jsonify(_mp_cache)
    raw = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for rows in ex.map(_fetch_rss_feed, _MP_FEEDS):
            raw.extend(rows)
    raw.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    seen, deduped = set(), []
    for a in raw:
        if a["title"] not in seen:
            seen.add(a["title"])
            deduped.append(a)
    out = {"articles": deduped[:60], "count": len(deduped),
           "fetched_at": _dt_mp3.datetime.now(_dt_mp3.timezone.utc).isoformat()}
    with _mp_lock:
        _mp_cache, _mp_cache_ts = out, _dt_mp3.datetime.now()
    return jsonify(out)


# ── EARNINGS CALENDAR + IMPLIED MOVE ─────────────────────────────────────────
_ec_cache    = None
_ec_cache_ts = None
_ec_lock     = threading.Lock()
_EC_TTL      = 600   # 10-minute cache

_EC_WATCHLIST = [
    "AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","AMD","INTC","NFLX",
    "CRM","ORCL","IBM","ADBE","QCOM","MU","AVGO","TXN","AMAT","MRVL",
    "JPM","GS","BAC","WFC","MS","BLK","SCHW","C","V","MA",
    "JNJ","PFE","MRNA","ABBV","BMY","LLY","UNH","CVS","AMGN","GILD",
    "XOM","CVX","COP","SLB","EOG",
    "DIS","CMCSA","T","VZ","NFLX",
    "WMT","TGT","COST","HD","LOW",
    "BA","CAT","MMM","GE","HON","RTX","LMT",
    "COIN","HOOD","PLTR","SNOW","UBER","LYFT","ABNB","DASH",
    "SHOP","SQ","PYPL","AFRM","RIVN","LCID",
    "ZM","DOCU","NOW","DDOG","NET","CRWD","PANW",
    "F","GM","RIVN",
]

def _check_earnings(ticker):
    import datetime as _dt_ec
    import yfinance as yf
    today = _dt_ec.date.today()
    cutoff = today + _dt_ec.timedelta(days=30)
    try:
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        if cal is None:
            return None
        earn_date = None
        try:
            if hasattr(cal, "empty") and not cal.empty:
                if "Earnings Date" in cal.index:
                    earn_date = cal.loc["Earnings Date"].iloc[0]
                elif "Earnings Date" in cal.columns:
                    earn_date = cal.iloc[0]["Earnings Date"]
            elif isinstance(cal, dict):
                ed_list = cal.get("Earnings Date", [])
                earn_date = ed_list[0] if ed_list else None
        except Exception:
            return None
        if earn_date is None:
            return None
        if hasattr(earn_date, "date"):
            earn_dt = earn_date.date()
        else:
            earn_dt = _dt_ec.datetime.strptime(str(earn_date)[:10], "%Y-%m-%d").date()
        if earn_dt < today or earn_dt > cutoff:
            return None

        info  = tk.fast_info
        price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        if not price or price <= 0:
            return None

        # Implied move via ATM straddle
        implied_move_pct = None
        try:
            exps = tk.options
            target_exp = next(
                (e for e in (exps or [])
                 if _dt_ec.datetime.strptime(e, "%Y-%m-%d").date() >= earn_dt),
                None
            )
            if target_exp:
                chain  = tk.option_chain(target_exp)
                calls, puts = chain.calls, chain.puts
                if not calls.empty and not puts.empty:
                    strikes = calls["strike"].values
                    atm = strikes[abs(strikes - price).argmin()]
                    c_row = calls[calls["strike"] == atm]
                    p_row = puts[puts["strike"]  == atm]
                    if not c_row.empty and not p_row.empty:
                        def _mid(row, col):
                            b, a = row.iloc[0].get("bid", 0), row.iloc[0].get("ask", 0)
                            m = (b + a) / 2
                            return m if m > 0 else row.iloc[0].get("lastPrice", 0)
                        straddle = _mid(c_row, "call") + _mid(p_row, "put")
                        implied_move_pct = round(straddle / price * 100, 1)
        except Exception:
            pass

        # EPS estimate
        eps_est = None
        try:
            if isinstance(cal, dict):
                ea = cal.get("Earnings Average", [])
                eps_est = ea[0] if ea else None
            elif "Earnings Average" in cal.index:
                eps_est = cal.loc["Earnings Average"].iloc[0]
        except Exception:
            pass

        # Short name + market cap
        mkt_cap_b, short_name = None, ticker
        try:
            mc = getattr(info, "market_cap", None)
            if mc:
                mkt_cap_b = round(mc / 1e9, 1)
        except Exception:
            pass
        try:
            short_name = (tk.info.get("shortName") or ticker)[:30]
        except Exception:
            pass

        return {
            "ticker":           ticker,
            "name":             short_name,
            "earnings_date":    earn_dt.isoformat(),
            "days_until":       (earn_dt - today).days,
            "price":            round(price, 2),
            "eps_estimate":     round(float(eps_est), 2) if eps_est is not None else None,
            "implied_move_pct": implied_move_pct,
            "mkt_cap_b":        mkt_cap_b,
        }
    except Exception:
        return None

@app.route("/stock-api/earnings-calendar", methods=["GET"])
def earnings_calendar():
    global _ec_cache, _ec_cache_ts
    import datetime as _dt_ec2
    with _ec_lock:
        if _ec_cache and _ec_cache_ts and (_dt_ec2.datetime.now() - _ec_cache_ts).total_seconds() < _EC_TTL:
            return jsonify(_ec_cache)
    results = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        for r in ex.map(_check_earnings, _EC_WATCHLIST):
            if r:
                results.append(r)
    results.sort(key=lambda x: (x["earnings_date"], -(x["mkt_cap_b"] or 0)))
    out = {"earnings": results, "count": len(results),
           "as_of": _dt_ec2.date.today().isoformat(), "window_days": 30}
    with _ec_lock:
        _ec_cache, _ec_cache_ts = out, _dt_ec2.datetime.now()
    return jsonify(out)


@app.route("/stock-api/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
