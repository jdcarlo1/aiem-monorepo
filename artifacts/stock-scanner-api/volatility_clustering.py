"""
volatility_clustering.py
---------------------------
GARCH(1,1) volatility-clustering model, designed as a drop-in 7th indicator
for market_regime_overlay.py.

WHY THIS EXISTS
----------------
regime.py and market_regime_overlay.py's vix_indicator() both measure
volatility using realized (backward-looking) measures: rolling std of
returns, or the VIX level itself. Both are reactive — they tell you
volatility WAS high, after it already happened.

GARCH(1,1) instead models volatility as a process with memory: today's
variance depends on yesterday's variance (clustering/persistence) AND
yesterday's squared return (shock reaction). This lets it FORECAST next-
period volatility, not just report the last realized number. It also
exposes a single number — the persistence parameter (alpha + beta) — that
tells you whether the market is currently in a "long memory" volatility
regime (shocks decay slowly, danger lingers) or a "short memory" one
(shocks die out fast).

This is the same family of model used for VIX-futures pricing and most
sell-side vol desks' regime classification — it's a genuine quant-standard
tool, not a toy.

REQUIRES: pip install arch
  (the `arch` package is the standard Python GARCH implementation —
  statsmodels does not have a maintained GARCH module)

INTEGRATION
-----------
Add as a 7th vote in market_regime_overlay.py's combine function:
    from volatility_clustering import garch_regime_indicator
    votes.append(garch_regime_indicator(price_history))
Same {"vote": -1/0/1, "reason": str} contract as the other 6 indicators,
so it slots in without changing the overlay's aggregation logic.
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional


def fit_garch_model(returns: pd.Series, p: int = 1, q: int = 1):
    """
    Fits a GARCH(p, q) model to a return series (in PERCENT, not decimal —
    e.g. 1.5 for a 1.5% daily move, not 0.015 — this is the `arch` package's
    expected scale and avoids numerical convergence issues).

    Returns the fitted model result object (has .params, .conditional_volatility,
    .forecast(), etc.) or None if fitting fails (e.g. insufficient data).
    """
    try:
        from arch import arch_model
    except ImportError:
        raise ImportError(
            "GARCH modeling requires the 'arch' package. Install with: "
            "pip install arch"
        )

    returns_clean = returns.dropna()
    if len(returns_clean) < 60:   # 60 trading days (~3 months) is sufficient for GARCH(1,1)
        return None

    returns_pct = returns_clean * 100.0

    try:
        model = arch_model(returns_pct, vol="Garch", p=p, q=q, dist="t")
        result = model.fit(disp="off", show_warning=False)
        return result
    except Exception:
        return None


def forecast_volatility(fitted_result, horizon: int = 5) -> Optional[Dict[str, Any]]:
    """
    Forecasts conditional volatility forward `horizon` trading days.
    Returns annualized vol forecast (assuming 252 trading days) plus the
    raw daily forecast path, or None if the fit is unusable.
    """
    if fitted_result is None:
        return None

    try:
        forecast = fitted_result.forecast(horizon=horizon, reindex=False)
        daily_variance = forecast.variance.values[-1]
        daily_vol_pct = np.sqrt(daily_variance)
        annualized_vol_pct = daily_vol_pct * np.sqrt(252)

        return {
            "daily_vol_forecast_pct": daily_vol_pct.tolist(),
            "annualized_vol_forecast_pct": annualized_vol_pct.tolist(),
            "horizon_days": horizon,
        }
    except Exception:
        return None


def get_persistence(fitted_result) -> Optional[float]:
    """
    alpha + beta from the fitted GARCH(1,1) model. This is the model's
    'memory' parameter:
      - close to 1.0 (e.g. > 0.95): shocks decay very slowly — volatility
        clustering is strong, current conditions likely to persist for weeks
      - well below 1.0 (e.g. < 0.80): shocks decay fast — even a big move
        today doesn't tell you much about next week
    Values >= 1.0 indicate a non-stationary fit (rare, usually a data issue —
    treat as unreliable and fall back to realized-vol indicators).
    """
    if fitted_result is None:
        return None
    try:
        params = fitted_result.params
        alpha = params.get("alpha[1]", 0.0)
        beta = params.get("beta[1]", 0.0)
        return float(alpha + beta)
    except Exception:
        return None


def garch_regime_indicator(price_history: pd.DataFrame, lookback: int = 252) -> Dict[str, Any]:
    """
    Vote-style indicator matching market_regime_overlay.py's contract:
    {"vote": -1 | 0 | 1, "reason": str}

    -1 (risk-off): GARCH forecasts rising volatility AND current regime is
                   high-persistence (clustering strong — danger likely to
                   continue, not a one-day blip)
     0 (neutral):  forecast is flat, or persistence is low/unreliable
                   (can't trust the regime read)
     1 (risk-on):  GARCH forecasts falling/low volatility with normal
                   persistence — calm and likely to stay calm

    price_history: DataFrame with a 'Close' (or 'close') column, daily
    frequency, most recent `lookback` rows used for fitting.
    """
    # Diagram-2 C8 remediation (2026-07-10): market_regime_overlay.py's own
    # indicators (vix/trend_structure/drawdown) all build price_history with
    # a LOWERCASE 'close' column; this function originally required an exact
    # capitalized 'Close' match, which meant every live call (both AIEM tool
    # call sites) silently fell through to the neutral "no price history
    # provided" branch even though real price_history WAS being passed.
    # Accept either case rather than forcing every caller to rename a column
    # that every other indicator in this module already relies on as-is.
    if price_history is None:
        return {"vote": 0, "reason": "no price history provided for GARCH fit"}
    _close_col = "Close" if "Close" in price_history.columns else (
        "close" if "close" in price_history.columns else None)
    if _close_col is None:
        return {"vote": 0, "reason": "no price history provided for GARCH fit"}

    close = price_history[_close_col].squeeze().astype(float)
    if len(close) < lookback:
        lookback = len(close)
    returns = close.iloc[-lookback:].pct_change().dropna()

    try:
        fitted = fit_garch_model(returns)
    except ImportError as e:
        return {"vote": 0, "reason": str(e)}

    if fitted is None:
        return {"vote": 0, "reason": "GARCH fit failed or insufficient data — "
                                       "falling back to neutral"}

    persistence = get_persistence(fitted)
    forecast = forecast_volatility(fitted, horizon=5)

    if persistence is None or forecast is None:
        return {"vote": 0, "reason": "GARCH forecast unavailable"}

    if persistence >= 1.0:
        return {"vote": 0, "reason": f"GARCH fit non-stationary (persistence={persistence:.3f}) "
                                       "— unreliable, treating as neutral"}

    current_cond_vol = fitted.conditional_volatility.iloc[-1]
    forecast_avg_vol = float(np.mean(forecast["daily_vol_forecast_pct"]))
    vol_rising = forecast_avg_vol > current_cond_vol * 1.10
    vol_falling = forecast_avg_vol < current_cond_vol * 0.90
    high_persistence = persistence > 0.90

    if vol_rising and high_persistence:
        return {
            "vote": -1,
            "reason": (f"GARCH forecasts rising volatility ({current_cond_vol:.2f}% -> "
                       f"{forecast_avg_vol:.2f}% daily) with high persistence "
                       f"({persistence:.3f}) — clustering suggests this isn't a one-day spike"),
        }

    if vol_falling and not high_persistence:
        return {
            "vote": 1,
            "reason": (f"GARCH forecasts cooling volatility ({current_cond_vol:.2f}% -> "
                       f"{forecast_avg_vol:.2f}% daily), normal persistence "
                       f"({persistence:.3f}) — calm regime likely to hold"),
        }

    return {
        "vote": 0,
        "reason": (f"GARCH forecast flat/mixed (current {current_cond_vol:.2f}%, "
                   f"forecast {forecast_avg_vol:.2f}%, persistence {persistence:.3f})"),
    }


def persist_garch_result(
    fitted_result,
    ticker: str,
    db_url: str,
    regime_vote: int = 0,
    forecast_vol_1d: float = None,
) -> bool:
    """
    Persist a fitted GARCH(1,1) result to garch_regime_log table.
    Provides runtime audit evidence (Diagram 2 Criterion 12) that GARCH(1,1)
    was actually computed (not silently bypassed) and that the DB record
    captures convergence status, parameters, and model fit diagnostics.

    Returns True on success, False on failure.
    """
    if fitted_result is None:
        return False
    try:
        import psycopg2 as _pg
        import datetime as _dt

        _params     = fitted_result.params
        _omega      = float(_params.get("omega",    0.0))
        _alpha1     = float(_params.get("alpha[1]", 0.0))
        _beta1      = float(_params.get("beta[1]",  0.0))
        _persistence = _alpha1 + _beta1
        # Long-run variance = omega / (1 - alpha - beta), annualised
        _lr_var     = _omega / max(1 - _persistence, 1e-6)
        _lr_vol     = float(np.sqrt(_lr_var) * np.sqrt(252))

        # 1-day ahead conditional volatility forecast
        try:
            _fc_obj = fitted_result.forecast(horizon=1, reindex=False)
            _f1d    = float(np.sqrt(_fc_obj.variance.values[-1, 0]))
        except Exception:
            _f1d    = forecast_vol_1d or float(fitted_result.conditional_volatility.iloc[-1])

        _converged  = bool(fitted_result.convergence_flag == 0) if hasattr(fitted_result, "convergence_flag") else True
        _aic        = float(fitted_result.aic) if hasattr(fitted_result, "aic") else None
        _bic        = float(fitted_result.bic) if hasattr(fitted_result, "bic") else None
        _regime     = "HIGH_PERSIST" if _persistence > 0.90 else (
                      "LOW_PERSIST"  if _persistence < 0.70 else "NORMAL")

        with _pg.connect(db_url, connect_timeout=4) as _c, _c.cursor() as _cu:
            # Schema matches existing table: logged_at / log_date column names
            _cu.execute("""
                CREATE TABLE IF NOT EXISTS garch_regime_log (
                    id               BIGSERIAL PRIMARY KEY,
                    ticker           TEXT NOT NULL,
                    logged_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    log_date         DATE NOT NULL DEFAULT CURRENT_DATE,
                    omega            FLOAT,
                    alpha1           FLOAT,
                    beta1            FLOAT,
                    long_run_vol     FLOAT,
                    forecast_vol_1d  FLOAT,
                    regime           TEXT,
                    converged        BOOLEAN,
                    aic              FLOAT,
                    bic              FLOAT,
                    vote             INTEGER
                )
            """)
            # Add vote column to any pre-existing tables that lack it
            _cu.execute("ALTER TABLE garch_regime_log ADD COLUMN IF NOT EXISTS vote INTEGER")
            # Ensure the UNIQUE(log_date, ticker) constraint required by ON CONFLICT exists.
            # Pre-check for duplicates first — if any exist, stop and report rather than
            # silently failing or deduplicating (Data Immutability Rule).
            _cu.execute("""
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM garch_regime_log
                    GROUP BY log_date, ticker HAVING COUNT(*) > 1
                ) _dup_sub
            """)
            _dup_ct = _cu.fetchone()[0]
            if _dup_ct > 0:
                raise Exception(
                    f"UNIQUE(log_date, ticker) index blocked: {_dup_ct} duplicate "
                    f"(log_date, ticker) group(s) found — manual dedup required before retrying"
                )
            _cu.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS garch_regime_log_date_ticker_uidx
                    ON garch_regime_log (log_date, ticker)
            """)
            _cu.execute("""
                INSERT INTO garch_regime_log
                    (ticker, log_date, omega, alpha1, beta1,
                     long_run_vol, forecast_vol_1d, regime, converged, aic, bic, vote)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (log_date, ticker) DO UPDATE SET
                    logged_at       = NOW(),
                    omega           = EXCLUDED.omega,
                    alpha1          = EXCLUDED.alpha1,
                    beta1           = EXCLUDED.beta1,
                    long_run_vol    = EXCLUDED.long_run_vol,
                    forecast_vol_1d = EXCLUDED.forecast_vol_1d,
                    regime          = EXCLUDED.regime,
                    converged       = EXCLUDED.converged,
                    aic             = EXCLUDED.aic,
                    bic             = EXCLUDED.bic,
                    vote            = EXCLUDED.vote
            """, (
                ticker,
                _dt.date.today().isoformat(),
                _omega, _alpha1, _beta1,
                _lr_vol, _f1d, _regime, _converged, _aic, _bic, regime_vote,
            ))
            _c.commit()
        return True
    except Exception as _e:
        print(f"[garch_persist] failed for {ticker}: {_e}")
        return False
