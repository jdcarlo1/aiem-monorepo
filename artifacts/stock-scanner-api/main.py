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
from email_alerts import (
    init_db, subscribe, unsubscribe, get_active_subscribers,
    send_daily_digest, smtp_configured, subscriber_count,
)
from historical_performance import init_score_history_table, save_scan_scores
import execution
import pnl

app = Flask(__name__)
CORS(app)

# ── init DB & scheduler ──────────────────────────────────────────────────────
init_db()
init_score_history_table()

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

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:50]]

    def _get_row(ticker):
        try:
            from datetime import date
            today_str = date.today().isoformat()

            opts = fetch_options_data(ticker)
            if not opts or opts.get("top_prem_value", 0) <= 0:
                return None

            # Skip 0DTE — expiry must be after today
            expiry = opts.get("top_prem_expiry")
            if not expiry or expiry <= today_str:
                return None

            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0:
                hist = tkr.history(period="1d")
                price = float(hist["Close"].iloc[-1]) if not hist.empty else 0
            prem_k = float(opts.get("top_prem_value", 0))   # in $K
            return {
                "ticker":         ticker,
                "price":          round(price, 2),
                "strike":         opts.get("top_prem_strike"),
                "expiry":         expiry,
                "premium_m":      round(prem_k / 1000, 2),   # convert to $M
                "premium_k":      round(prem_k, 1),
                "call_put_ratio": round(float(opts.get("call_put_ratio", 0)), 2),
                "call_vol_oi":    round(float(opts.get("call_vol_oi", 0)), 2),
                "total_call_vol": int(opts.get("total_call_vol", 0)),
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
    top10 = rows[:20]
    for i, r in enumerate(top10):
        r["rank"] = i + 1

    return jsonify({"results": top10, "scanned": len(tickers), "returned": len(top10)})


@app.route("/stock-api/", methods=["GET"])
@app.route("/stock-api", methods=["GET"])
@app.route("/stock-api/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
