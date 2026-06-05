import pandas as pd
import numpy as np
from indicators import compute_indicators, build_history
from scoring import compute_score


def backtest_strategy(df: pd.DataFrame, buy_threshold: float = 6.5, sell_threshold: float = 4.5, initial_cash: float = 10000.0) -> dict:
    if df is None or len(df) < 60:
        return {"error": "Insufficient data for backtest"}

    results = []
    cash = initial_cash
    shares = 0.0
    entry_price = 0.0
    trades = []
    equity_curve = []

    window = 60

    for i in range(window, len(df)):
        slice_df = df.iloc[:i].copy()
        close = float(df["Close"].squeeze().iloc[i])
        date = str(df.index[i].date()) if hasattr(df.index[i], "date") else str(df.index[i])

        try:
            ind = compute_indicators(slice_df)
            if not ind:
                equity_curve.append({"date": date, "value": cash + shares * close})
                continue
            ind.pop("history", None)
            score_data = compute_score(ind)
            score = score_data["score"]
        except Exception:
            equity_curve.append({"date": date, "value": cash + shares * close})
            continue

        if shares == 0 and score >= buy_threshold and cash > 0:
            shares = cash / close
            entry_price = close
            cash = 0.0
            trades.append({
                "type": "BUY",
                "date": date,
                "price": round(close, 2),
                "shares": round(shares, 4),
                "score": round(score, 1),
            })

        elif shares > 0 and score <= sell_threshold:
            proceeds = shares * close
            pnl = proceeds - (shares * entry_price)
            pnl_pct = (close - entry_price) / entry_price * 100
            cash = proceeds
            trades.append({
                "type": "SELL",
                "date": date,
                "price": round(close, 2),
                "shares": round(shares, 4),
                "score": round(score, 1),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
            })
            shares = 0.0
            entry_price = 0.0

        portfolio_value = cash + shares * close
        equity_curve.append({
            "date": date,
            "value": round(portfolio_value, 2),
            "close": round(close, 2),
            "in_position": shares > 0,
        })

    if shares > 0:
        final_price = float(df["Close"].squeeze().iloc[-1])
        final_proceeds = shares * final_price
        cash = final_proceeds
        shares = 0.0

    final_value = cash
    total_return = (final_value - initial_cash) / initial_cash * 100
    n_trades = len([t for t in trades if t["type"] == "BUY"])
    winning_trades = [t for t in trades if t.get("type") == "SELL" and t.get("pnl", 0) > 0]
    win_rate = len(winning_trades) / max(len([t for t in trades if t["type"] == "SELL"]), 1) * 100

    buy_hold_return = 0.0
    try:
        start_price = float(df["Close"].squeeze().iloc[window])
        end_price = float(df["Close"].squeeze().iloc[-1])
        buy_hold_return = (end_price - start_price) / start_price * 100
    except Exception:
        pass

    max_drawdown = 0.0
    if equity_curve:
        values = [e["value"] for e in equity_curve]
        peak = values[0]
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_drawdown:
                max_drawdown = dd

    sparse_curve = equity_curve[::5] if len(equity_curve) > 200 else equity_curve

    return {
        "initial_cash": initial_cash,
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return, 2),
        "buy_hold_return_pct": round(buy_hold_return, 2),
        "alpha": round(total_return - buy_hold_return, 2),
        "n_trades": n_trades,
        "win_rate": round(win_rate, 1),
        "max_drawdown_pct": round(max_drawdown, 2),
        "trades": trades,
        "equity_curve": sparse_curve,
        "buy_threshold": buy_threshold,
        "sell_threshold": sell_threshold,
    }
