def compute_score(indicators: dict) -> dict:
    score = 0
    max_score = 0
    breakdown = []

    rsi = indicators.get("rsi")
    if rsi is not None:
        max_score += 2
        if 40 <= rsi <= 60:
            pts = 2
            label = "RSI neutral (healthy)"
        elif 30 <= rsi < 40 or 60 < rsi <= 70:
            pts = 1
            label = "RSI mildly extended"
        elif rsi < 30:
            pts = 1
            label = "RSI oversold (potential reversal)"
        else:
            pts = 0
            label = "RSI overbought (caution)"
        score += pts
        breakdown.append({"factor": "RSI", "points": pts, "max": 2, "label": label, "value": round(rsi, 1)})

    macd = indicators.get("macd")
    macd_signal = indicators.get("macd_signal")
    macd_hist = indicators.get("macd_hist")
    if macd is not None and macd_signal is not None:
        max_score += 2
        if macd > macd_signal and macd_hist and macd_hist > 0:
            pts = 2
            label = "MACD bullish crossover"
        elif macd > macd_signal:
            pts = 1
            label = "MACD above signal"
        else:
            pts = 0
            label = "MACD bearish"
        score += pts
        breakdown.append({"factor": "MACD", "points": pts, "max": 2, "label": label, "value": round(macd, 3)})

    price = indicators.get("price")
    sma50 = indicators.get("sma50")
    sma200 = indicators.get("sma200")
    if price and sma50 and sma200:
        max_score += 2
        if price > sma50 > sma200:
            pts = 2
            label = "Price > SMA50 > SMA200 (strong uptrend)"
        elif price > sma50 or price > sma200:
            pts = 1
            label = "Price above one moving average"
        else:
            pts = 0
            label = "Price below both MAs (downtrend)"
        score += pts
        breakdown.append({"factor": "Trend (SMA)", "points": pts, "max": 2, "label": label, "value": round(price, 2)})

    volume_ratio = indicators.get("volume_ratio")
    if volume_ratio is not None:
        max_score += 2
        if volume_ratio >= 2.0:
            pts = 2
            label = f"Volume surge ({volume_ratio:.1f}x avg)"
        elif volume_ratio >= 1.3:
            pts = 1
            label = f"Above avg volume ({volume_ratio:.1f}x)"
        else:
            pts = 0
            label = f"Low volume ({volume_ratio:.1f}x avg)"
        score += pts
        breakdown.append({"factor": "Volume", "points": pts, "max": 2, "label": label, "value": round(volume_ratio, 2)})

    bb_upper = indicators.get("bb_upper")
    bb_lower = indicators.get("bb_lower")
    bb_mid = indicators.get("bb_mid")
    if price and bb_upper and bb_lower and bb_mid:
        max_score += 2
        if price > bb_mid and price < bb_upper:
            pts = 2
            label = "Price in upper BB band (bullish)"
        elif abs(price - bb_lower) < abs(price - bb_upper):
            pts = 1
            label = "Price near BB lower (possible bounce)"
        elif price > bb_upper:
            pts = 1
            label = "Price above BB (strong but stretched)"
        else:
            pts = 0
            label = "Price below BB midline"
        score += pts
        breakdown.append({"factor": "Bollinger Bands", "points": pts, "max": 2, "label": label, "value": round(price, 2)})

    if max_score == 0:
        normalized = 5
    else:
        normalized = round((score / max_score) * 10, 1)
        normalized = max(1.0, min(10.0, normalized))

    rating = "Strong Buy" if normalized >= 8 else "Buy" if normalized >= 6.5 else "Hold" if normalized >= 5 else "Weak" if normalized >= 3 else "Avoid"

    return {
        "score": normalized,
        "raw": score,
        "max_raw": max_score,
        "rating": rating,
        "breakdown": breakdown,
    }
