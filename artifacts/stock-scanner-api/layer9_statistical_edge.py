"""
LAYER 9: STATISTICAL EDGE
===========================
Aggregates the advanced quant indicators into a single 0-100 sub-score
per ticker, matching the scale convention of the existing 8 layers.

Integration contract with main.py:
  - Call compute_layer9_score(ticker, history_df) where history_df is
    the DataFrame returned by _td_history(ticker, days=120).
  - Returns a dict with 'statistical_score' (0-100) and 'components'.
  - All exceptions are caught internally; returns a safe default on failure.
  - No DB writes, no HTTP calls — pure in-process computation.
"""

import math
import numpy as np
import pandas as pd
from datetime import datetime, timezone

try:
    from advanced_quant_indicators import (
        hurst_exponent,
        vpin,
        roll_spread_estimator,
        corwin_schultz_spread,
        amihud_illiquidity,
        realized_skew_kurtosis,
        jump_detection_bipower,
        shannon_entropy,
        variance_risk_premium,
        risk_neutral_density,
    )
    _INDICATORS_AVAILABLE = True
except ImportError:
    _INDICATORS_AVAILABLE = False


# ──────────────────────────────────────────────────────────────────────
# Weights — must sum to 1.0. Tuned for the existing 8-layer universe.
# illiquidity_penalty is INVERTED before merging (high illiquidity = bad).
# ──────────────────────────────────────────────────────────────────────
_WEIGHTS = {
    "hurst_regime":        0.18,   # tradeable regime (trend OR mean-rev)
    "vpin_toxicity":       0.18,   # informed-flow pressure
    "jump_risk":           0.09,   # discontinuous gap/shock flag
    "tail_risk":           0.14,   # realized skew/kurtosis (crash risk)
    "entropy_clarity":     0.14,   # low entropy = clean pattern
    "illiquidity_penalty": 0.18,   # Amihud + Roll (INVERTED: thin = bad)
    "vrp_proxy":           0.09,   # variance risk premium via rolling vol differential
    # rnd_component is conditional (requires options chain_df) and does not
    # carry a fixed weight — when present it adjusts the final score by ±5.
}

_SAFE_DEFAULT = {
    "statistical_score": 50.0,
    "components": {},
    "flags": {"jump_detected": False},
    "regime": "unknown",
    "timestamp": None,
    "error": "indicators_unavailable",
}


def _safe_float(v, default=0.0):
    """Return float, replacing NaN/Inf/None with default."""
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def compute_rnd_component(chain_df: "pd.DataFrame", r: float = 0.05,
                           t: float = 0.083) -> dict:
    """
    Compute Risk-Neutral Density component from an options chain DataFrame.

    Args:
        chain_df: DataFrame with columns 'strike' (sorted ascending, evenly
                  spaced) and 'call_price' (mid-price at each strike).
        r:        Risk-free rate, annualized (default 5% = 0.05).
        t:        Time to expiration in years (default ~30 days = 0.083).

    Returns:
        dict with 'rnd_array', 'rnd_peak_strike', 'rnd_skew', 'available'.
    """
    try:
        if chain_df is None or chain_df.empty:
            return {"available": False, "reason": "no_chain_data"}
        if "strike" not in chain_df.columns or "call_price" not in chain_df.columns:
            return {"available": False, "reason": "missing_required_columns"}
        chain_sorted = chain_df.sort_values("strike").dropna()
        if len(chain_sorted) < 5:
            return {"available": False, "reason": "insufficient_strikes"}
        strikes     = chain_sorted["strike"].values.astype(float)
        call_prices = chain_sorted["call_price"].values.astype(float)
        density = risk_neutral_density(strikes, call_prices, r=r, t=t)
        valid   = ~np.isnan(density)
        if not valid.any():
            return {"available": False, "reason": "density_all_nan"}
        peak_idx    = int(np.nanargmax(density))
        rnd_skew    = float(np.nanmean(density[density > 0] * strikes[: len(density)][density > 0])
                            - strikes[peak_idx])
        return {
            "available":       True,
            "rnd_array":       density.tolist(),
            "rnd_peak_strike": float(strikes[peak_idx]),
            "rnd_skew":        round(rnd_skew, 4),
            "n_strikes":       int(valid.sum()),
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def compute_layer9_score(ticker: str, history_df: "pd.DataFrame",
                          lookback: int = 60,
                          chain_df: "pd.DataFrame | None" = None) -> dict:
    """
    Compute the Layer 9 Statistical Edge sub-score (0-100) for one ticker.

    Args:
        ticker:     Ticker symbol string (for logging only).
        history_df: DataFrame from _td_history(); must have columns
                    Close, Volume, High, Low (case-sensitive).
                    Minimum ~60 rows recommended; returns safe default
                    with fewer than 30 rows.
        lookback:   Window for rolling indicator calcs (default 60 bars).

    Returns:
        dict:
          'ticker'            : str
          'statistical_score' : float 0-100 final sub-score
          'components'        : dict of per-component raw + normalized values
          'flags'             : dict of boolean risk flags
          'regime'            : str label ('trending'|'mean_reverting'|'random_walk')
          'timestamp'         : UTC ISO timestamp
    """
    if not _INDICATORS_AVAILABLE:
        return {**_SAFE_DEFAULT, "ticker": ticker}

    try:
        # ── Validate & extract columns ────────────────────────────────
        if history_df is None or history_df.empty or len(history_df) < 30:
            return {**_SAFE_DEFAULT, "ticker": ticker, "error": "insufficient_history"}

        close  = history_df["Close"].squeeze().astype(float)
        volume = history_df["Volume"].squeeze().astype(float)
        high   = history_df["High"].squeeze().astype(float) if "High" in history_df else None
        low    = history_df["Low"].squeeze().astype(float)  if "Low"  in history_df else None

        close  = close.dropna()
        volume = volume.dropna()
        if len(close) < 30:
            return {**_SAFE_DEFAULT, "ticker": ticker, "error": "insufficient_close"}

        returns = close.pct_change().dropna()
        lk = min(lookback, len(close) - 1)

        components = {}
        flags      = {}
        weights    = dict(_WEIGHTS)

        # ── 1. Hurst regime fit ───────────────────────────────────────
        try:
            h = hurst_exponent(close.tail(lk * 2))
            h = _safe_float(h, 0.5)
            # Distance from 0.5 in EITHER direction = tradeable regime
            hurst_score = min(100.0, abs(h - 0.5) * 200.0)
            if h > 0.55:
                regime = "trending"
            elif h < 0.45:
                regime = "mean_reverting"
            else:
                regime = "random_walk"
        except Exception:
            h, hurst_score, regime = 0.5, 50.0, "random_walk"
        components["hurst_regime"] = {"raw": round(h, 3), "score": round(hurst_score, 1)}

        # ── 2. VPIN toxicity ─────────────────────────────────────────
        try:
            vpin_series = vpin(volume.tail(lk * 5), close.tail(lk * 5))
            vpin_latest = _safe_float(vpin_series.dropna().iloc[-1] if not vpin_series.dropna().empty else None, 0.3)
        except Exception:
            vpin_latest = 0.3
        vpin_score = min(100.0, vpin_latest * 150.0)
        components["vpin_toxicity"] = {"raw": round(vpin_latest, 3), "score": round(vpin_score, 1)}

        # ── 3. Jump risk ─────────────────────────────────────────────
        # Pass ALL available returns (not just tail-lk) so the rolling-252
        # z-score normalization has enough history to calibrate.  The flag
        # itself is still evaluated on the most recent 3 bars.
        try:
            jump_flags   = jump_detection_bipower(returns)
            jump_detected = bool(jump_flags.tail(3).any()) if not jump_flags.empty else False
        except Exception:
            jump_detected = False
        flags["jump_detected"] = jump_detected
        jump_score = 80.0 if jump_detected else 25.0
        components["jump_risk"] = {"raw": jump_detected, "score": jump_score}

        # ── 4. Tail risk (skew/kurtosis) ─────────────────────────────
        try:
            sk = realized_skew_kurtosis(returns.tail(lk))
            latest_skew = _safe_float(sk["skew"].dropna().iloc[-1]     if not sk["skew"].dropna().empty     else None, 0.0)
            latest_kurt = _safe_float(sk["kurtosis"].dropna().iloc[-1] if not sk["kurtosis"].dropna().empty else None, 0.0)
            tail_score  = min(100.0, max(0.0, (-latest_skew * 30.0) + (latest_kurt * 10.0) + 50.0))
        except Exception:
            latest_skew, latest_kurt, tail_score = 0.0, 0.0, 50.0
        components["tail_risk"] = {
            "raw_skew": round(latest_skew, 3),
            "raw_kurtosis": round(latest_kurt, 3),
            "score": round(tail_score, 1),
        }

        # ── 5. Entropy clarity ───────────────────────────────────────
        try:
            ent_series  = shannon_entropy(returns.tail(lk))
            latest_ent  = _safe_float(ent_series.dropna().iloc[-1] if not ent_series.dropna().empty else None, math.log2(10))
            max_ent     = math.log2(10)
            entropy_score = min(100.0, max(0.0, (1.0 - latest_ent / max_ent) * 100.0))
        except Exception:
            latest_ent, entropy_score = math.log2(10), 50.0
        components["entropy_clarity"] = {"raw": round(latest_ent, 3), "score": round(entropy_score, 1)}

        # ── 6. Illiquidity penalty (INVERTED: higher = worse signal) ──
        try:
            dollar_vol  = volume * close
            amihud_s    = amihud_illiquidity(returns.tail(lk), dollar_vol.tail(lk))
            amihud_val  = _safe_float(amihud_s.dropna().iloc[-1] if not amihud_s.dropna().empty else None, 0.0)

            # Also compute Roll spread (requires only close prices)
            roll_spread = _safe_float(roll_spread_estimator(close.tail(lk)), 0.0)

            # Corwin-Schultz if high/low available
            if high is not None and low is not None:
                cs_series = corwin_schultz_spread(high.tail(lk), low.tail(lk))
                cs_val    = _safe_float(cs_series.dropna().iloc[-1] if not cs_series.dropna().empty else None, 0.0)
            else:
                cs_val = roll_spread

            # Normalize: Amihud 0→1e-8 maps to 0-100; cap at 100
            # (scale factor calibrated for mid/large cap universe with $1M+ avg dollar vol)
            illiq_raw   = amihud_val * 1e8 + cs_val * 50.0
            illiq_score = min(100.0, max(0.0, illiq_raw))

            # INVERT: high illiquidity lowers the contribution (penalty)
            illiq_score_inverted = 100.0 - illiq_score
        except Exception:
            illiq_score_inverted = 50.0
            cs_val = 0.0
            amihud_val = 0.0
        components["illiquidity_penalty"] = {
            "raw_amihud": float(amihud_val),   # kept as full-precision float (may be 1e-13 for AAPL)
            "raw_amihud_fmt": f"{amihud_val:.4e}",   # scientific notation for readability
            "raw_cs_spread": round(cs_val, 5),
            "score": round(illiq_score_inverted, 1),   # already inverted
        }

        # ── 7. Variance Risk Premium proxy (rolling vol differential) ──
        # Without a live options chain we proxy "implied" vol using the
        # 21-day trailing vol (market's medium-term expectation) and
        # "realized" vol using the 5-day trailing vol (what just happened).
        # VRP = slow_var − fast_var: positive = recent vol below expectation
        # (historically bullish for calls: options tend to overprice risk).
        try:
            realized_vol_fast = returns.tail(lk).rolling(5,  min_periods=3).std() * math.sqrt(252)
            implied_vol_proxy = returns.tail(lk).rolling(21, min_periods=10).std() * math.sqrt(252)
            vrp_series = variance_risk_premium(realized_vol_fast, implied_vol_proxy)
            vrp_latest = _safe_float(vrp_series.dropna().iloc[-1] if not vrp_series.dropna().empty else None, 0.0)
        except Exception:
            vrp_latest = 0.0
        # Centre at 50; typical range −0.1 to +0.15 in variance units
        vrp_score = min(100.0, max(0.0, 50.0 + vrp_latest * 400.0))
        components["vrp_proxy"] = {
            "raw": round(vrp_latest, 6),
            "score": round(vrp_score, 1),
            "note": "rolling_vol_proxy_no_options_chain",
        }

        # ── 8. Risk-Neutral Density (conditional on options chain) ────
        # Only computed when chain_df (with 'strike' + 'call_price' cols)
        # is passed in by the caller.  When absent the component is marked
        # unavailable and does NOT participate in weighted scoring.
        rnd_result = compute_rnd_component(chain_df)
        components["rnd_component"] = rnd_result
        # RND is an optional ±5 bonus/penalty on top of the weighted score:
        # if density peak is above spot → market prices upside → +5
        # if density peak is below spot → market prices downside → −5
        rnd_adjustment = 0.0
        if rnd_result.get("available"):
            rnd_skew = rnd_result.get("rnd_skew", 0.0)
            rnd_adjustment = 5.0 if rnd_skew > 0 else -5.0

        # ── Compute weighted final score ─────────────────────────────
        weight_sum  = sum(weights.values())
        norm_w      = {k: v / weight_sum for k, v in weights.items()}
        final_score = sum(
            components[k]["score"] * norm_w[k]
            for k in norm_w
            if k in components
        )
        final_score = round(float(np.clip(final_score + rnd_adjustment, 0.0, 100.0)), 2)

        return {
            "ticker":            ticker,
            "statistical_score": final_score,
            "components":        components,
            "flags":             flags,
            "regime":            regime,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:
        import traceback as _dbg_tb
        print(f"[layer9_debug] {ticker} raised: {exc}\n{_dbg_tb.format_exc()}")
        return {**_SAFE_DEFAULT, "ticker": ticker, "error": str(exc)}


def batch_layer9_scores(tickers_histories: dict, timeout_per: float = 3.0,
                        chain_df_map: dict = None) -> dict:
    """
    Compute Layer 9 scores for a batch of tickers in parallel.

    Args:
        tickers_histories: {ticker: history_df} mapping.
        timeout_per: per-ticker CPU timeout (threads only; does not kill
                     numpy — set to a generous value like 3.0s).
        chain_df_map: optional {ticker: chain_df} mapping. chain_df must
                      have 'strike' and 'call_price' columns (call mid-price).
                      Tickers missing from this map get chain_df=None (RND
                      component will be skipped for them — not an error).

    Returns:
        {ticker: result_dict} mapping.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _TE

    _chain_map = chain_df_map or {}
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(compute_layer9_score, t, df, chain_df=_chain_map.get(t)): t
            for t, df in tickers_histories.items()
            if df is not None and not df.empty
        }
        for fut in as_completed(futures, timeout=timeout_per * len(futures) + 5):
            t = futures[fut]
            try:
                results[t] = fut.result(timeout=timeout_per)
            except Exception:
                results[t] = {**_SAFE_DEFAULT, "ticker": t}
    return results


def format_layer9_signal(result: dict) -> str:
    """
    Format a Layer 9 result dict into a compact signal string for
    inclusion in the AI trades prompt. E.g.:
      'stat9=72 regime=trending vpin=0.41 jump=False entropy=high tail=low'
    """
    if not result or result.get("error"):
        return ""
    s  = result.get("statistical_score", 50)
    c  = result.get("components", {})
    r  = result.get("regime", "")
    fl = result.get("flags", {})

    vpin_raw  = c.get("vpin_toxicity",    {}).get("raw", 0)
    ent_score = c.get("entropy_clarity",  {}).get("score", 50)
    tail_s    = c.get("tail_risk",        {}).get("score", 50)
    jump      = fl.get("jump_detected",   False)

    ent_label  = "high" if ent_score > 65 else ("low" if ent_score < 35 else "mid")
    tail_label = "high" if tail_s   > 65 else ("low" if tail_s   < 35 else "mid")

    return (
        f"stat9={s:.0f} regime={r} vpin={vpin_raw:.2f} "
        f"jump={jump} entropy={ent_label} tail_risk={tail_label}"
    )
