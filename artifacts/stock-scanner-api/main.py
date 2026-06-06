from flask import Flask, request, jsonify
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import math

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
    _scheduler.start()
    print("[scheduler] APScheduler started — scans at 9:00 AM, 9:45 AM, 3:30 PM, & 4:15 PM ET + outcomes at 4:30 PM, Mon–Fri")
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
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS expiry_price FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS expiry_pct   FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS expiry_win   BOOL",
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


def _save_ai_trades_to_log(trades: list, trade_date: str):
    """Persist today's AI trade picks. Skips if already logged for this date."""
    if not trades:
        return
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for t in trades:
                cur.execute("""
                    INSERT INTO ai_trade_log
                        (trade_date, ticker, direction, setup_type, conviction,
                         price_at_signal, entry_strike, expiry, target_price, stop_loss,
                         signals_aligned, thesis, risk_level)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (trade_date, ticker, direction) DO NOTHING
                """, (
                    trade_date,
                    t.get("ticker"),
                    t.get("direction"),
                    t.get("setup_type"),
                    t.get("conviction"),
                    t.get("price"),
                    t.get("entry_strike"),
                    t.get("expiry"),
                    t.get("target_price"),
                    t.get("stop_loss"),
                    _json.dumps(t.get("signals_aligned", [])),
                    t.get("thesis"),
                    t.get("risk_level"),
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
                       t1_price, t3_price, t5_price, t10_price
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

            def _win(pct, direction):
                if pct is None:
                    return None
                if direction == "BULLISH":
                    return pct >= 1.0
                elif direction == "BEARISH":
                    return pct <= -1.0
                else:
                    return abs(pct) < 3.0

            for row in rows:
                id_, ticker, trade_date, p0, direction, target, stoploss, expiry_str, exp_p, t1p, t3p, t5p, t10p = row
                updates = {}

                # ── Expiry date outcome (primary) ────────────────────────────
                expiry_date = _parse_expiry_date(expiry_str, trade_date)
                if expiry_date and expiry_date <= today and exp_p is None:
                    close = _fetch_close(ticker, expiry_date)
                    if close is not None and p0:
                        pct = round((close - p0) / p0 * 100, 2)
                        updates["expiry_price"] = close
                        updates["expiry_pct"]   = pct
                        updates["expiry_win"]   = _win(pct, direction)

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
                        updates[col_win] = _win(pct, direction)

                if updates:
                    # Primary outcome = expiry result if available, else T+5
                    exp_win_val = updates.get("expiry_win") if "expiry_win" in updates else None
                    t5_pct_val  = updates.get("t5_pct")
                    outcome = "OPEN"
                    if exp_win_val is not None:
                        outcome = "WIN" if exp_win_val else "LOSS"
                    elif t5_pct_val is not None:
                        outcome = "WIN" if _win(t5_pct_val, direction) else "LOSS"
                    updates["outcome"] = outcome
                    set_sql = ", ".join(f"{k} = %s" for k in updates)
                    cur.execute(f"UPDATE ai_trade_log SET {set_sql} WHERE id = %s",
                                list(updates.values()) + [id_])
            conn.commit()
        print(f"[ai_trade_log] outcomes updated for {len(rows)} trades")
    except Exception as e:
        print(f"[ai_trade_log] update_outcomes error: {e}")


_init_ai_trade_log_table()


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
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:50]]
    result = scan_smart_money(tickers)
    return jsonify(_safe(result))


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

    # Force a fresh Yahoo Finance crumb/session before bulk fetching
    try:
        yf.utils.get_crumb(reuse_session=False)
    except Exception:
        pass

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:50]]

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

    return jsonify({"results": top40, "scanned": len(tickers), "returned": len(top40)})


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
    for i, r in enumerate(results[:15]):
        r["rank"] = i + 1

    out = {"results": results[:15], "date": date_used, "total_in_db": len(raw)}
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
            net_upgrades_7d = None
            try:
                info = tkr.info
                ed = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
                if ed:
                    ed_dt = _dt.fromtimestamp(int(ed))
                    earnings_date = ed_dt.strftime("%Y-%m-%d")
                    diff_days = (_dt.now() - ed_dt).days
                    if 0 <= diff_days <= 10:
                        days_since_earnings = diff_days
                sfp = info.get("shortPercentOfFloat")
                if sfp and sfp > 0:
                    short_float_pct = round(float(sfp) * 100, 1)
                sr = info.get("shortRatio")
                if sr and sr > 0:
                    short_ratio = round(float(sr), 1)
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

            verdict = ("HIGH FEAR" if iv_rank >= 80 else "ELEVATED" if iv_rank >= 60 else "NORMAL" if iv_rank >= 30 else "LOW IV")
            return {
                "ticker": ticker, "price": round(price, 2),
                "current_iv": round(current_iv * 100, 1),
                "hv_30": round(hv30 * 100, 1), "iv_hv_ratio": iv_hv, "iv_rank": iv_rank,
                "verdict": verdict,
                "earnings_date": earnings_date, "days_since_earnings": days_since_earnings,
                "short_float_pct": short_float_pct, "short_ratio": short_ratio,
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
            }
        except Exception: return None

    # Pre-fetch SPY returns once (shared across all tickers for beta calculation)
    _spy_rets_arr = None
    try:
        _spy_raw = yf.download("SPY", period="60d", interval="1d", progress=False, auto_adjust=True)["Close"]
        # Handle both Series (single ticker) and DataFrame (multi-ticker) shapes
        _spy_hist = _spy_raw.iloc[:, 0] if hasattr(_spy_raw, "columns") else _spy_raw
        _spy_rets_arr = _spy_hist.dropna().pct_change().dropna().values
    except Exception:
        pass

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


@app.route("/stock-api/ai-trades", methods=["GET"])
def ai_trades():
    """Generate 5 AI trade setups by reading ALL cached tab signals — no re-fetching."""
    from datetime import datetime as _dt
    from openai import OpenAI

    _cache = getattr(app, "_ait_cache", None)
    _ts    = getattr(app, "_ait_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 3600:
        return jsonify(_cache)

    try:
        oai = OpenAI(
            base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL"),
            api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
            timeout=45.0,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

    # 17. Macro cross-asset signals (yield curve, DXY, credit spreads, crude, gold)
    macro_cross_asset = ""
    try:
        import yfinance as _yf_macro
        _mcr = _yf_macro.download(
            ["^TNX", "^IRX", "UUP", "HYG", "LQD", "USO", "GLD"],
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
        if _parts17:
            macro_cross_asset = " | ".join(_parts17)
            active_sources.append("Macro Cross-Asset")
    except Exception:
        macro_cross_asset = ""

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
            finally:
                app._cache_warming = False
        import threading
        threading.Thread(target=_do_warm, daemon=True).start()

    # Need at least 2 signal sources AND some rich tickers to proceed
    if len(active_sources) < 2 or len(rich) < 3:
        already_warming = getattr(app, "_cache_warming", False)
        if not already_warming:
            _start_cache_warming()
        return jsonify({
            "warming": True,
            "error": "Collecting live signals from all tabs — first load takes ~35 seconds. Click ↻ Regenerate shortly.",
            "trades": [],
            "tickers_scanned": len(rich),
            "signal_sources": active_sources
        })

    # Sort by composite score descending, fall back to alphabetical
    sorted_tickers = sorted(rich.values(), key=lambda x: x.get("composite_score", 50), reverse=True)

    # Build compact signal block — top 15 tickers, one line each, key fields only
    sig_lines = []
    for v in sorted_tickers[:10]:
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
        if v.get("live_alerts"):
            parts.append(f"alerts=[{'; '.join(v['live_alerts'][:2])}]")
        sig_lines.append(" | ".join(parts))

    sig_text = "\n".join(sig_lines)

    macro_line = f"MACRO: {macro_context}" if macro_context else ""
    sector_line = f"SECTORS: {sector_context}" if sector_context else ""
    index_line = f"INDICES: {index_context}" if index_context else ""
    regime_line = f"MARKET_REGIME: {market_regime}" if market_regime and market_regime != "UNKNOWN" else ""
    winrate_line = f"YOUR_HISTORICAL_WIN_RATES: {win_rate_context}" if win_rate_context else ""
    macro_cross_line = f"MACRO_CROSS_ASSET: {macro_cross_asset}" if macro_cross_asset else ""
    context_block = "\n".join(x for x in [macro_line, sector_line, index_line, regime_line, winrate_line, macro_cross_line] if x)

    system_msg = (
        "You are an elite institutional options trader operating at hedge-fund quant level. "
        "You receive 35+ data points per ticker across 17 sources including vol surface, dealer gamma, factor scores, and macro cross-asset signals. "
        "CRITICAL RULES:\n"
        "1. NEVER recommend a setup where opt_spread>12% (ILLIQUID_AVOID) — wide spreads destroy edge.\n"
        "2. In HIGH_FEAR or CORRECTION regimes: avoid LONG CALL; prefer PUT spreads or IRON CONDORs on tickers with iv_rv=RICH_SELL_PREM.\n"
        "3. In BULL_TREND regime: prefer LONG CALL or BULL CALL SPREAD on high-beta (beta≥1.5) names with vol_trend surging and mom12_1>0.\n"
        "4. In RANGING/CHOP regime: prefer IRON CONDOR on IV_rank≥60 + iv_rv=RICH_SELL_PREM tickers; avoid directional plays.\n"
        "5. If YOUR_HISTORICAL_WIN_RATES provided: strongly prefer setup_types with high win rates from your own history.\n"
        "6. persist=3d+ is your highest-conviction filter — multi-day institutional building is rare and reliable.\n"
        "7. earn_beat=3/4 or 4/4 gives fundamental tailwind; earn_beat=0/4 is a headwind.\n"
        "8. GEX=LONG_GAMMA(suppressive) → mean-reversion setups; SHORT_GAMMA(amplifying) → directional/momentum setups.\n"
        "9. iv_skew=FEAR_PREMIUM (>8pp) → institutional crash hedging; use PUT spreads or add protection.\n"
        "10. iv_rv=RICH_SELL_PREM (>20%) → premium selling edge; CHEAP_BUY_VOL (<-10%) → long vol edge.\n"
        "11. MACRO_CROSS_ASSET: YieldCurve=INVERTED → rotate defensive; CreditSpread=WIDENING → reduce risk; Gold=FLIGHT_TO_SAFETY → avoid long equities.\n"
        "12. sector_corr=IDIOSYNCRATIC (<0.5) → ticker moves on its own; prefer over highly correlated names.\n"
        "13. news=BEARISH_NEWS with BULL_TREND → fade the news; news=BULLISH_NEWS with momentum = confirmation.\n"
        "14. EXPIRY RULE: Default to expiry 30–90 days from today. Never recommend weekly or 0DTE expirations. EXCEPTION: If a ticker shows a single block options trade with premium ≥$10M at an expiry 180–365 days out (LEAPS territory), you MAY recommend that longer expiry — this is whale/institutional positioning and is extremely bullish or bearish. In that case set setup_type to LONG CALL or LONG PUT (not a spread), set conviction to HIGH, and explicitly note the whale block in signals_aligned (e.g. '$20M LEAPS call block, 9mo out').\n"
        "Output ONLY a JSON array of exactly 3 setups. No markdown. No text outside the array."
    )

    user_msg = f"""SOURCES ({len(active_sources)}): {', '.join(active_sources)}
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
- MACRO_CROSS_ASSET: YieldCurve(10y-3m)=curve shape; DXY=dollar; CreditSpread5d=HYG vs LQD; Crude5d; Gold5d

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

Return a JSON array of exactly 3 objects sorted BULLISH → NEUTRAL → BEARISH. Each must have ALL fields:
ticker (string), price (number), setup_type (LONG CALL|LONG PUT|BULL CALL SPREAD|BEAR PUT SPREAD|IRON CONDOR|STRADDLE), direction ("BULLISH"|"BEARISH"|"NEUTRAL"), conviction ("HIGH"|"MEDIUM"), entry_strike (number), expiry (YYYY-MM-DD), target_price (number), stop_loss (number), signals_aligned (list of 4-5 short strings naming exact signals used), thesis (2 sentences max), risk_level ("LOW"|"MEDIUM"|"HIGH")

JSON array only. No markdown. Start immediately with ["""

    def _call_openai_streaming():
        """Stream the response so we capture content even if the proxy truncates."""
        import sys
        chunks = []
        finish = "unknown"
        stream = oai.chat.completions.create(
            model="gpt-5-mini",
            max_completion_tokens=6000,
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
            return jsonify({
                "error": f"OpenAI returned no content (finish={finish}). Please hit Regenerate.",
                "trades": [],
                "signal_sources": active_sources,
            }), 500

        trades = _json.loads(raw)
        out = {
            "trades": trades,
            "generated_at": _dt.now().isoformat(),
            "tickers_scanned": len(rich),
            "signal_sources": active_sources,
            "signal_source_count": len(active_sources),
        }
        app._ait_cache = out
        app._ait_cache_ts = _dt.now()
        # Persist to track record log (once per day; duplicates silently skipped)
        try:
            from datetime import date as _date_now
            _save_ai_trades_to_log(trades, str(_date_now.today()))
        except Exception as _se:
            print(f"[ai_trade_log] background save error: {_se}")
        return jsonify(out)
    except Exception as e:
        import sys
        print(f"[ai_trades] exception: {e}", file=sys.stderr, flush=True)
        return jsonify({"error": str(e), "raw": locals().get("raw", ""), "trades": []}), 500


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
                       outcome, created_at
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
                    "outcome","created_at"]
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

            # By direction breakdown
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

            return jsonify({
                "trades": trades,
                "count": len(trades),
                "win_rates": win_rates,
                "by_direction": by_dir,
            })
    except Exception as e:
        return jsonify({"error": str(e), "trades": [], "count": 0,
                        "win_rates": {"t1": None, "t3": None, "t5": None, "t10": None},
                        "by_direction": {}}), 500


@app.route("/stock-api/whale-activity", methods=["GET"])
def whale_activity():
    """Scan for large institutional options blocks ($5M+ single-strike) across 30-365 day expirations."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_whale_cache", None)
    _ts    = getattr(app, "_whale_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

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
                            prem_m = vol * last * 100 / 1e6
                            if prem_m >= MIN_PREM_M:
                                otm_pct  = round((strike - price) / price * 100, 1)
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
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_scan_whale, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futures):
            all_blocks.extend(fut.result() or [])

    all_blocks.sort(key=lambda x: x["prem_m"], reverse=True)
    out = {"blocks": all_blocks[:60], "total": len(all_blocks), "scanned": len(DEFAULT_LEADERBOARD)}
    app._whale_cache    = out
    app._whale_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
