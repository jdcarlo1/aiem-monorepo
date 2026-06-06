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
    _scheduler.start()
    print("[scheduler] APScheduler started — scans at 9:00 AM, 9:45 AM, 3:30 PM, & 4:15 PM ET, Mon–Fri")
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
            hist = t.history(period="2d")
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

    return jsonify({
        "sectors": sectors,
        "indices": indices,
        "advance_decline": {"up": ad_up, "down": ad_down, "unchanged": ad_unch},
        "as_of": date.today().isoformat(),
    })


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
    return jsonify({"gainers": gainers, "losers": losers, "scanned": len(tickers)})


@app.route("/stock-api/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
