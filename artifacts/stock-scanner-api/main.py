from flask import Flask, request, jsonify
from flask_cors import CORS
import os

from scanner import analyze_ticker, scan_tickers, WATCHLIST_DEFAULT, fetch_stock_data
from portfolio import get_portfolio, add_position, remove_position, get_portfolio_value
from backtest import backtest_strategy
from alerts import get_alerts, add_alert, delete_alert
from analytics import run_historical_analytics
from prop_signal import prop_signal
import execution
import pnl

app = Flask(__name__)
CORS(app)

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
            price = float(df["Close"].iloc[-1])
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

    return jsonify({
        "signals": signals,
        "positions": enriched_positions,
        "cash": round(execution.get_cash(), 2),
        "realized_pnl": pnl.total_pnl(),
        "trades": pnl.get_trades()[-20:],
    })


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


@app.route("/stock-api/", methods=["GET"])
@app.route("/stock-api", methods=["GET"])
@app.route("/stock-api/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
