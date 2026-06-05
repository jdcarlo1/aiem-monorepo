from ml_engine import predict_direction
from factors import momentum_factor, volatility_factor, volume_factor, trend_factor
from regime import detect_regime


def prop_signal(df, include_ml: bool = False) -> dict | None:
    """
    Compute prop-desk signal for a stock.
    include_ml=False (default) skips Random Forest training — fast enough for scans.
    include_ml=True runs the full ML model — use only for single-stock deep analysis.
    """
    if df is None or len(df) < 60:
        return None

    try:
        regime = detect_regime(df)

        mom   = momentum_factor(df)
        vol   = volatility_factor(df)
        volu  = volume_factor(df)
        trend = trend_factor(df)

        if include_ml:
            ml_data = predict_direction(df)
            ml_prob = ml_data.get("probability_up", 50.0) / 100.0
        else:
            ml_prob = 0.5

        score = 0.0
        if regime == "TRENDING":
            score += trend * 40
            score += mom   * 30
        elif regime == "HIGH_VOL":
            score += ml_prob * 50
            score += volu    * 20
        else:
            score += ml_prob * 30
            score += trend   * 20

        score += volu * 10

        score_10 = max(1, min(10, round(score / 8)))

        return {
            "score": score_10,
            "regime": regime,
            "ml_probability": round(ml_prob * 100, 1) if include_ml else None,
            "momentum": round(mom,   3),
            "volatility": round(vol,  4),
            "volume": round(volu,  2),
            "trend": round(trend,  3),
        }
    except Exception:
        return None
