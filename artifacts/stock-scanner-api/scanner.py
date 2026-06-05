import yfinance as yf
import pandas as pd
from indicators import compute_indicators
from scoring import compute_score
from ml_engine import predict_direction

WATCHLIST_DEFAULT = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "JPM", "BAC", "GS", "V", "MA", "PYPL",
    "JNJ", "PFE", "UNH", "ABBV",
    "XOM", "CVX",
    "SPY", "QQQ", "IWM",
]


def fetch_stock_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    try:
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if data.empty:
            return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        return data
    except Exception:
        return None


def analyze_ticker(ticker: str) -> dict:
    df = fetch_stock_data(ticker, period="2y")
    if df is None or df.empty:
        return {"ticker": ticker, "error": "Could not fetch data"}

    info = {}
    try:
        stock = yf.Ticker(ticker)
        raw_info = stock.info or {}
        info = {
            "name": raw_info.get("longName", ticker),
            "sector": raw_info.get("sector", ""),
            "industry": raw_info.get("industry", ""),
            "market_cap": raw_info.get("marketCap"),
            "pe_ratio": raw_info.get("trailingPE"),
            "forward_pe": raw_info.get("forwardPE"),
            "dividend_yield": raw_info.get("dividendYield"),
            "beta": raw_info.get("beta"),
            "description": raw_info.get("longBusinessSummary", "")[:300] if raw_info.get("longBusinessSummary") else "",
        }
    except Exception:
        info = {"name": ticker}

    indicators = compute_indicators(df)
    score_data = compute_score(indicators)
    ml_data = predict_direction(df)

    history = indicators.pop("history", [])

    return {
        "ticker": ticker.upper(),
        "info": info,
        "indicators": indicators,
        "score": score_data,
        "ml": ml_data,
        "history": history,
    }


def scan_tickers(tickers: list) -> list:
    results = []
    for ticker in tickers:
        try:
            data = analyze_ticker(ticker)
            if "error" not in data:
                results.append({
                    "ticker": data["ticker"],
                    "name": data["info"].get("name", ticker),
                    "sector": data["info"].get("sector", ""),
                    "price": data["indicators"].get("price"),
                    "price_change_pct": data["indicators"].get("price_change_pct"),
                    "rsi": data["indicators"].get("rsi"),
                    "volume_ratio": data["indicators"].get("volume_ratio"),
                    "score": data["score"].get("score"),
                    "rating": data["score"].get("rating"),
                    "direction": data["ml"].get("direction"),
                    "prob_up": data["ml"].get("probability_up"),
                })
        except Exception as e:
            results.append({"ticker": ticker, "error": str(e)})
    return results
