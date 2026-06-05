from flask import Flask, request, jsonify
from flask_cors import CORS
import os

from scanner import analyze_ticker, scan_tickers, WATCHLIST_DEFAULT, fetch_stock_data
from portfolio import get_portfolio, add_position, remove_position, get_portfolio_value
from backtest import backtest_strategy
from alerts import get_alerts, add_alert, delete_alert
from analytics import run_historical_analytics

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


@app.route("/stock-api/", methods=["GET"])
@app.route("/stock-api", methods=["GET"])
@app.route("/stock-api/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
