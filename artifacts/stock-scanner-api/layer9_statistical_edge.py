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

Cross-sectional inputs (pca_factor1_var, absorption_ratio_val):
  - Computed upstream in _run_layer9_bg_scan BEFORE batch_layer9_scores
    is called, so they are available at score time.
  - absorption_ratio uses _absorption_ratio_fn from advanced_quant_indicators.

Stat-arb input (stat_arb_coint_pvalue):
  - Sourced from stat_arb_pairs DB table (Engle-Granger test_cointegration
    result stored by register_pair()). Queried once per bg_scan cycle and
    passed in via stat_arb_coint_map.
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
        absorption_ratio as _absorption_ratio_fn,
    )
    _INDICATORS_AVAILABLE = True
except ImportError:
    _INDICATORS_AVAILABLE = False


# ──────────────────────────────────────────────────────────────────────
# Weights — must sum to 1.0. Tuned for the existing 8-layer universe.
# illiquidity_penalty is INVERTED before merging (high illiquidity = bad).
# Conditional adjustments (rnd, garch, stat_arb, pca, absorption_ratio)
# do NOT carry fixed weights — they apply bounded ±point adjustments on
# top of the weighted composite so sparse/cross-sectional signals cannot
# distort the base score when absent.
# ──────────────────────────────────────────────────────────────────────
_WEIGHTS = {
    "hurst_regime":        0.18,   # tradeable regime (trend OR mean-rev)
    "vpin_toxicity":       0.18,   # informed-flow pressure
    "jump_risk":           0.09,   # discontinuous gap/shock flag
    "tail_risk":           0.14,   # realized skew/kurtosis (crash risk)
    "entropy_clarity":     0.14,   # low entropy = clean pattern
    "illiquidity_penalty": 0.18,   # Amihud + Roll (INVERTED: thin = bad)
    "vrp_proxy":           0.09,   # variance risk premium via rolling vol differential
    # skew_velocity is conditional (requires options_structure_scan history) — weight added inline
    # rnd_component is conditional (requires options chain_df) — ±5 adjustment, not weighted
    # garch_persistence is conditional (requires arch package) — ±5 adjustment, not weighted
    # stat_arb_cointegration is conditional (from stat_arb_pairs) — ±4 adjustment, not weighted
    # pca_factor1 is cross-sectional (passed in pre-computed) — ±4 adjustment, not weighted
    # absorption_ratio is cross-sectional (passed in pre-computed) — ±5 adjustment, not weighted
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
                          chain_df: "pd.DataFrame | None" = None,
                          db_url: "str | None" = None,
                          stat_arb_coint_pvalue: "float | None" = None,
                          pca_factor1_var: "float | None" = None,
                          absorption_ratio_val: "float | None" = None,
                          xmom_zscore: "float | None" = None) -> dict:
    """
    Compute the Layer 9 Statistical Edge sub-score (0-100) for one ticker.

    Args:
        ticker:     Ticker symbol string (for logging only).
        history_df: DataFrame from _td_history(); must have columns
                    Close, Volume, High, Low (case-sensitive).
                    Minimum ~60 rows recommended; returns safe default
                    with fewer than 30 rows.
        lookback:   Window for rolling indicator calcs (default 60 bars).
        stat_arb_coint_pvalue: Engle-Granger p-value from stat_arb_pairs
                    for this ticker's best active pair (None if not in any pair).
                    <0.05 → +4pt, <0.15 → +2pt, else 0.
        pca_factor1_var: Cross-sectional PC1 variance fraction computed by
                    pca_factor_decomposition() across the full batch BEFORE
                    this call. Same value for all tickers in the batch.
                    >=0.60 → -4pt (systemic), <0.35 → +4pt (idiosyncratic).
        absorption_ratio_val: Kritzman absorption ratio from
                    advanced_quant_indicators.absorption_ratio() computed
                    alongside PCA before this call. Same value for all tickers.
                    >0.75 → -5pt (fragile), <0.35 → +5pt (stock-picker's market).

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

        # ── 7. Variance Risk Premium (true IV when available, else proxy) ──
        # Preferred: ATM front_iv from options_structure_scan (percent → decimal)
        # vs 20-day realized vol. Fallback: 21d trailing vol as "implied" proxy
        # vs 5d realized (legacy rolling differential).
        vrp_note = "rolling_vol_proxy_no_options_chain"
        try:
            realized_vol_fast = returns.tail(lk).rolling(5,  min_periods=3).std() * math.sqrt(252)
            implied_vol_proxy = returns.tail(lk).rolling(21, min_periods=10).std() * math.sqrt(252)
            vrp_series = variance_risk_premium(realized_vol_fast, implied_vol_proxy)
            vrp_latest = _safe_float(vrp_series.dropna().iloc[-1] if not vrp_series.dropna().empty else None, 0.0)

            if db_url:
                try:
                    import psycopg2 as _vrp_pg
                    with _vrp_pg.connect(db_url, connect_timeout=3) as _vrpc, _vrpc.cursor() as _vrpcu:
                        _vrpcu.execute("""
                            SELECT front_iv FROM options_structure_scan
                            WHERE ticker = %s AND front_iv IS NOT NULL AND front_iv > 0
                            ORDER BY scan_date DESC LIMIT 1
                        """, (ticker,))
                        _iv_row = _vrpcu.fetchone()
                    if _iv_row and _iv_row[0] is not None:
                        _front_iv = float(_iv_row[0])
                        # Stored as percent (e.g. 25.5); convert if clearly percent-scale.
                        _iv_dec = _front_iv / 100.0 if _front_iv > 2.0 else _front_iv
                        _rv = float(returns.tail(20).std() * math.sqrt(252)) if len(returns) >= 5 else 0.0
                        if _iv_dec > 0 and _rv > 0:
                            vrp_latest = float((_iv_dec ** 2) - (_rv ** 2))
                            vrp_note = "true_iv_from_options_structure_scan.front_iv"
                except Exception:
                    pass
        except Exception:
            vrp_latest = 0.0
        # Centre at 50; typical range −0.1 to +0.15 in variance units
        vrp_score = min(100.0, max(0.0, 50.0 + vrp_latest * 400.0))
        components["vrp_proxy"] = {
            "raw": round(vrp_latest, 6),
            "score": round(vrp_score, 1),
            "note": vrp_note,
        }

        # ── 8. GARCH(1,1) persistence (optional — requires arch) ─────────
        # Persistence = alpha1 + beta1. High (>0.95): vol is very sticky —
        # choppy after spikes, cuts directional edge. Low (<0.70): vol
        # decays fast → regime transitioning, explosive move potential.
        # Implemented as a ±adjustment (like RND) to avoid weight-sum
        # distortion when GARCH fitting fails on thin history.
        garch_adjustment = 0.0
        try:
            from volatility_clustering import (
                fit_garch_model as _garch_fit_fn,
                get_persistence as _garch_pers_fn,
            )
            _gf = _garch_fit_fn(returns)
            if _gf is not None:
                _gpers = _safe_float(_garch_pers_fn(_gf), 0.0)
                flags["garch_persistence"] = round(_gpers, 4)
                components["garch_persistence"] = {
                    "raw": round(_gpers, 4),
                    "score": round(max(0.0, min(100.0, (1.0 - _gpers) * 100.0)), 1),
                }
                if _gpers > 0.95:
                    garch_adjustment = -5.0   # high clustering → choppy, cut edge
                elif _gpers < 0.70:
                    garch_adjustment = 3.0    # vol decaying → explosive potential
        except Exception:
            pass

        # ── 9. Risk-Neutral Density (conditional on options chain) ────
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

        # ── 10. Skew velocity (options_structure_scan pc_skew_pp history) ──
        # Rate-of-change of the put/call skew over trailing trading days.
        # Rising skew = growing put demand = bearish. Falling = unwind = bullish.
        # Follows vrp_proxy / jump_risk pattern: raw value → score → weighted.
        # BACKFILL LAG: first usable after ≥5 days of daily pc_skew_pp rows for
        # this ticker exist in options_structure_scan. Data accumulates from the
        # nightly options structure scan. Column set = options_structure_scan.pc_skew_pp.
        if db_url:
            try:
                import psycopg2 as _sv_pg
                from advanced_quant_indicators import skew_velocity as _sv_fn
                with _sv_pg.connect(db_url, connect_timeout=3) as _svc, _svc.cursor() as _svcu:
                    _svcu.execute("""
                        SELECT pc_skew_pp FROM options_structure_scan
                        WHERE ticker = %s AND pc_skew_pp IS NOT NULL
                        ORDER BY scan_date DESC LIMIT 30
                    """, (ticker,))
                    _sv_rows = [float(r[0]) for r in _svcu.fetchall()]
                if len(_sv_rows) >= 5:
                    _sv_series = pd.Series(list(reversed(_sv_rows)))  # oldest→newest
                    _sv_val = float(_sv_fn(_sv_series, window=5).iloc[-1])
                    flags["skew_velocity"] = round(_sv_val, 5)
                    _sv_score = min(100.0, max(0.0, 50.0 - _sv_val * 2000.0))
                    components["skew_velocity"] = {
                        "raw": round(_sv_val, 5),
                        "score": round(_sv_score, 1),
                    }
                    weights["skew_velocity"] = 0.09  # conditional — absent = weight not wasted
            except Exception:
                pass

        # ── 11. Stat Arb cointegration p-value (from stat_arb_pairs table) ──
        # Source: stat_arb_engine.test_cointegration() → register_pair() →
        # stat_arb_pairs.coint_pvalue. Queried once per bg_scan cycle for all
        # active pairs; best (lowest) p-value per ticker passed in here.
        # Named weight: bounded ±4-point adjustment.
        #   <0.05 → +4  (strong cointegration: high mean-reversion predictability)
        #   <0.15 → +2  (moderate cointegration)
        #   else  →  0  (no cointegration signal available for this ticker)
        stat_arb_adjustment = 0.0
        if stat_arb_coint_pvalue is not None:
            _sap = _safe_float(stat_arb_coint_pvalue, 1.0)
            if _sap < 0.05:
                stat_arb_adjustment = 4.0
            elif _sap < 0.15:
                stat_arb_adjustment = 2.0
            components["stat_arb_cointegration"] = {
                "raw_coint_pvalue": round(_sap, 4),
                "adjustment":       stat_arb_adjustment,
                "note": "Engle-Granger p-value from stat_arb_pairs; <0.05→+4, <0.15→+2, else 0",
            }

        # ── 12. PCA factor1 variance (cross-sectional, passed in pre-computed) ──
        # Source: advanced_quant_indicators.pca_factor_decomposition(returns_matrix)
        # called in _run_layer9_bg_scan BEFORE batch_layer9_scores so the value is
        # available at score time (not post-hoc). Cross-sectional: same value for
        # all tickers in the batch.
        # pca_factor1_var ∈ [0,1]: fraction of cross-sectional return variance
        # explained by the first principal component.
        # Named weight: bounded ±4-point adjustment.
        #   >=0.60 → -4  (one factor dominates: systemic risk, low idiosyncratic edge)
        #   <0.35  → +4  (low PC1 share: stock-picker's market)
        #   else   →  0
        pca_adjustment = 0.0
        if pca_factor1_var is not None:
            _pca = _safe_float(pca_factor1_var, 0.5)
            if _pca >= 0.60:
                pca_adjustment = -4.0
            elif _pca < 0.35:
                pca_adjustment = 4.0
            components["pca_factor1"] = {
                "raw":        round(_pca, 4),
                "adjustment": pca_adjustment,
                "note": "cross-sectional PC1 variance share; >=0.60→-4, <0.35→+4, else 0",
            }

        # ── 13. Absorption Ratio (Kritzman et al., via _absorption_ratio_fn) ──
        # Source: advanced_quant_indicators.absorption_ratio(returns_matrix) called
        # in _run_layer9_bg_scan alongside PCA BEFORE batch_layer9_scores. The
        # import _absorption_ratio_fn is declared at module level above so this
        # module owns the dependency even though the value is passed pre-computed.
        # Fraction of total cross-sectional variance in top N principal components.
        # Named weight: bounded ±5-point adjustment (same scale as RND).
        #   >0.75 → -5  (highly correlated/fragile: idiosyncratic edges overrun)
        #   <0.35 → +5  (idiosyncratic market: stock-picking environment)
        #   else  →  0
        absorption_adjustment = 0.0
        if absorption_ratio_val is not None:
            _ar = _safe_float(absorption_ratio_val, 0.5)
            if _ar > 0.75:
                absorption_adjustment = -5.0
            elif _ar < 0.35:
                absorption_adjustment = 5.0
            components["absorption_ratio"] = {
                "raw":        round(_ar, 4),
                "adjustment": absorption_adjustment,
                "note": "Kritzman absorption ratio; >0.75→-5, <0.35→+5, else 0",
            }

        # ── 14. Cross-sectional momentum z-score (passed in from batch) ──
        # Source: advanced_quant_indicators.cross_sectional_momentum_zscore
        # computed once per bg_scan over the aligned returns matrix.
        #   z >  1.0 → +4 (relative strength vs peers)
        #   z < -1.0 → -4 (relative weakness)
        #   else     →  0
        xmom_adjustment = 0.0
        if xmom_zscore is not None:
            _xz = _safe_float(xmom_zscore, 0.0)
            if _xz > 1.0:
                xmom_adjustment = 4.0
            elif _xz < -1.0:
                xmom_adjustment = -4.0
            components["cross_sectional_momentum"] = {
                "raw":        round(_xz, 4),
                "adjustment": xmom_adjustment,
                "note": "cross_sectional_momentum_zscore; >1→+4, <-1→-4, else 0",
            }

        # ── Compute weighted final score ─────────────────────────────
        weight_sum  = sum(weights.values())
        norm_w      = {k: v / weight_sum for k, v in weights.items()}
        final_score = sum(
            components[k]["score"] * norm_w[k]
            for k in norm_w
            if k in components
        )
        final_score = round(float(np.clip(
            final_score + rnd_adjustment + garch_adjustment
            + stat_arb_adjustment + pca_adjustment + absorption_adjustment
            + xmom_adjustment,
            0.0, 100.0,
        )), 2)

        return {
            "ticker":            ticker,
            "statistical_score": final_score,
            "components":        components,
            "flags":             flags,
            "regime":            regime,
            # Durable field for layer9_scores.xmom_zscore (Layer9 write path).
            "xmom_zscore":       (
                round(_safe_float(xmom_zscore, 0.0), 6)
                if xmom_zscore is not None else None
            ),
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }

    except Exception as exc:
        import traceback as _dbg_tb
        print(f"[layer9_debug] {ticker} raised: {exc}\n{_dbg_tb.format_exc()}")
        return {**_SAFE_DEFAULT, "ticker": ticker, "error": str(exc)}


def batch_layer9_scores(tickers_histories: dict, timeout_per: float = 3.0,
                        chain_df_map: dict = None,
                        db_url: "str | None" = None,
                        stat_arb_coint_map: "dict | None" = None,
                        pca_factor1_var: "float | None" = None,
                        absorption_ratio_val: "float | None" = None,
                        xmom_zscore_map: "dict | None" = None) -> dict:
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
        stat_arb_coint_map: optional {ticker: coint_pvalue} from stat_arb_pairs.
                      Built once per bg_scan cycle by querying active pairs.
        pca_factor1_var: cross-sectional PC1 variance fraction (same value for
                      all tickers in the batch). Computed BEFORE this call in
                      _run_layer9_bg_scan step 3c.
        absorption_ratio_val: Kritzman absorption ratio (same for all tickers).
                      Computed alongside PCA in _run_layer9_bg_scan step 3c.
        xmom_zscore_map: optional {ticker: latest cross-sectional momentum z}.

    Returns:
        {ticker: result_dict} mapping.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _TE

    _chain_map    = chain_df_map or {}
    _stat_arb_map = stat_arb_coint_map or {}
    _xmom_map     = xmom_zscore_map or {}
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(
                compute_layer9_score, t, df,
                chain_df=_chain_map.get(t),
                db_url=db_url,
                stat_arb_coint_pvalue=_stat_arb_map.get(t),
                pca_factor1_var=pca_factor1_var,
                absorption_ratio_val=absorption_ratio_val,
                xmom_zscore=_xmom_map.get(t),
            ): t
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
