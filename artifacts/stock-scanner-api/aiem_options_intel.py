"""
aiem_options_intel.py  —  Direction-agnostic options intelligence tools for AIEM

Four AIEM-callable tools that let AIEM evaluate both bullish AND bearish
options setups on equal footing, based purely on risk/reward:

  mkt_expected_move   — stock price × ATM_IV × sqrt(dte/252)
  mkt_iv_rank_live    — current IV vs 52-week HV range (cheap vs expensive)
  mkt_oi_by_strike    — OI distribution across strikes (pinning/walls)
  mkt_bearish_signals — aggregate: FEAR_PREMIUM + LONG_GAMMA + INVERTED term

All functions pull from DB tables already populated by existing scans
(options_structure_scan, polygon_market_daily, oi_daily_snapshot).
No additional API calls required.
"""

import os
import math
import psycopg2

_DB_URL = os.environ.get("DATABASE_URL", "")


def compute_expected_move(ticker: str, dte_days: int = 5) -> dict:
    """
    Expected Move = stock_price × ATM_IV × sqrt(dte_days / 252)

    This is the ±1 standard-deviation price range the options market is
    pricing for the given holding period (68% probability price stays inside).

    Uses front_iv from options_structure_scan (computed nightly from Tradier chains).
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT spot, front_iv, back_iv, pc_skew_tag
                FROM options_structure_scan
                WHERE ticker = %s
                  AND scan_date >= CURRENT_DATE - INTERVAL '5 days'
                  AND front_iv IS NOT NULL AND spot IS NOT NULL AND spot > 0
                ORDER BY scan_date DESC
                LIMIT 1
            """, (ticker.upper(),))
            row = cur.fetchone()

        if not row:
            return {"error": f"No options data for {ticker} in options_structure_scan"}

        spot, front_iv, back_iv, skew_tag = (
            float(row[0]), float(row[1]),
            float(row[2]) if row[2] else None,
            row[3],
        )
        # front_iv/back_iv stored as percentage (e.g. 39.78 = 39.78%) — normalise to decimal
        if front_iv > 1.0:
            front_iv = front_iv / 100.0
        if back_iv is not None and back_iv > 1.0:
            back_iv = back_iv / 100.0
        em     = spot * front_iv * math.sqrt(dte_days / 252)
        em_pct = front_iv * math.sqrt(dte_days / 252) * 100

        interp = (
            f"68% probability price stays within ±${em:.2f} "
            f"(±{em_pct:.1f}%) over {dte_days} trading days. "
            f"Call strikes above ${spot + em:.2f} = OTM calls. "
            f"Put strikes below ${spot - em:.2f} = OTM puts."
        )
        if skew_tag == "FEAR_PREMIUM":
            interp += " Put IV > Call IV (FEAR_PREMIUM) — options market expects downside risk."
        elif skew_tag == "CALL_SKEW":
            interp += " Call IV > Put IV (CALL_SKEW) — options market expects upside."

        return {
            "ticker":            ticker.upper(),
            "spot":              round(spot, 2),
            "front_iv":          round(front_iv, 4),
            "back_iv":           round(back_iv, 4) if back_iv else None,
            "skew_tag":          skew_tag,
            "dte_days":          dte_days,
            "expected_move":     round(em, 2),
            "expected_move_pct": round(em_pct, 2),
            "interpretation":    interp,
        }
    except Exception as e:
        return {"error": str(e)}


def compute_iv_rank_live(ticker: str) -> dict:
    """
    IV Rank = (current_IV - 52wk_low) / (52wk_high - 52wk_low) × 100

    Uses front_iv from options_structure_scan as current IV.
    Uses rolling 20-day HV from polygon_market_daily as the 52-week range proxy.

    IV Rank < 20  →  IV CHEAP  →  favor BUYING options (calls or puts)
    IV Rank > 80  →  IV EXPENSIVE  →  favor SELLING premium
    20-80         →  FAIR  →  directional plays acceptable
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT front_iv, spot, pc_skew_tag
                FROM options_structure_scan
                WHERE ticker = %s AND front_iv IS NOT NULL AND front_iv > 0
                ORDER BY scan_date DESC LIMIT 1
            """, (ticker.upper(),))
            row = cur.fetchone()
            if not row:
                return {"error": f"No options_structure_scan data for {ticker}"}
            current_iv, spot, skew_tag = float(row[0]), float(row[1]), row[2]
            # front_iv stored as percentage — normalise to decimal for HV comparison
            if current_iv > 1.0:
                current_iv = current_iv / 100.0

            cur.execute("""
                SELECT close_price
                FROM polygon_market_daily
                WHERE ticker = %s
                  AND scan_date >= CURRENT_DATE - INTERVAL '400 days'
                  AND close_price IS NOT NULL AND close_price > 0
                ORDER BY scan_date ASC
            """, (ticker.upper(),))
            price_rows = cur.fetchall()

        if len(price_rows) < 30:
            return {
                "error": f"Insufficient price history for {ticker} "
                         f"(need 30+, have {len(price_rows)})"
            }

        prices = [float(r[0]) for r in price_rows]

        hvs = []
        for i in range(20, len(prices)):
            seg     = prices[i - 20:i]
            log_ret = [math.log(seg[j] / seg[j - 1]) for j in range(1, 20) if seg[j - 1] > 0]
            if len(log_ret) < 15:
                continue
            mean_lr = sum(log_ret) / len(log_ret)
            var     = sum((r - mean_lr) ** 2 for r in log_ret) / (len(log_ret) - 1)
            hvs.append(math.sqrt(var * 252))

        if not hvs:
            return {"error": "Could not compute HV — insufficient return data"}

        iv_low  = min(hvs)
        iv_high = max(hvs)
        if iv_high > iv_low:
            iv_rank = (current_iv - iv_low) / (iv_high - iv_low) * 100
            iv_rank = max(0.0, min(100.0, iv_rank))
        else:
            iv_rank = 50.0

        iv_percentile = round(
            sum(1 for hv in hvs if hv <= current_iv) / len(hvs) * 100, 1
        )

        if iv_rank < 20:
            verdict = "CHEAP — favor BUYING options (calls or puts); low cost to be long vega"
        elif iv_rank > 80:
            verdict = "EXPENSIVE — favor SELLING premium (credit spreads, covered calls, iron condors)"
        else:
            verdict = "FAIR — directional options plays acceptable; standard sizing"

        return {
            "ticker":        ticker.upper(),
            "spot":          round(spot, 2),
            "current_iv":    round(current_iv, 4),
            "iv_rank":       round(iv_rank, 1),
            "iv_percentile": iv_percentile,
            "iv_low_52w":    round(iv_low, 4),
            "iv_high_52w":   round(iv_high, 4),
            "skew_tag":      skew_tag,
            "verdict":       verdict,
        }
    except Exception as e:
        return {"error": str(e)}


def compute_oi_by_strike(ticker: str, expiry: str | None = None) -> dict:
    """
    OI distribution across strikes from oi_daily_snapshot.

    Largest OI concentrations = max-pain / pinning zones near expiration.
    Put wall below price  = mechanical support (MMs buy stock to delta-hedge).
    Call wall above price = mechanical resistance (MMs sell stock to delta-hedge).

    Returns top 20 strikes by OI, plus put/call OI ratio.
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            # oi_daily_snapshot has no option_type column; use price (underlying at snapshot)
            # to classify: strike > price → above-spot (call-side resistance);
            #              strike <= price → at/below-spot (put-side floor)
            if expiry:
                cur.execute("""
                    SELECT strike, SUM(oi) AS total_oi, AVG(price) AS avg_price
                    FROM oi_daily_snapshot
                    WHERE ticker = %s AND expiry = %s
                      AND snapshot_date = (
                          SELECT MAX(snapshot_date) FROM oi_daily_snapshot WHERE ticker = %s
                      )
                    GROUP BY strike
                    ORDER BY total_oi DESC LIMIT 20
                """, (ticker.upper(), expiry, ticker.upper()))
            else:
                cur.execute("""
                    SELECT strike, SUM(oi) AS total_oi, AVG(price) AS avg_price
                    FROM oi_daily_snapshot
                    WHERE ticker = %s
                      AND snapshot_date = (
                          SELECT MAX(snapshot_date) FROM oi_daily_snapshot WHERE ticker = %s
                      )
                    GROUP BY strike
                    ORDER BY total_oi DESC LIMIT 20
                """, (ticker.upper(), ticker.upper()))
            rows = cur.fetchall()

        if not rows:
            return {"error": f"No OI snapshot data for {ticker}"}

        results      = []
        total_above  = 0   # call-side (above spot)
        total_below  = 0   # put-side (at/below spot)
        top_above_oi  = 0
        top_below_oi  = 0
        top_above_str = None
        top_below_str = None

        for strike, oi, avg_price in rows:
            s        = float(strike)
            oi_      = int(oi)
            spot_ref = float(avg_price) if avg_price else 0
            side     = "above_spot" if s > spot_ref else "at_or_below_spot"
            results.append({"strike": s, "oi": oi_, "side": side})
            if side == "above_spot":
                total_above += oi_
                if oi_ > top_above_oi:
                    top_above_oi, top_above_str = oi_, s
            else:
                total_below += oi_
                if oi_ > top_below_oi:
                    top_below_oi, top_below_str = oi_, s

        pc_ratio = round(total_below / total_above, 2) if total_above > 0 else None

        interp = (
            f"Largest above-spot OI wall: ${top_above_str} ({top_above_oi:,} OI) = overhead resistance. "
            f"Largest at/below-spot OI wall: ${top_below_str} ({top_below_oi:,} OI) = floor support. "
            f"Below/Above OI ratio: {pc_ratio or 'N/A'}."
        )
        if pc_ratio and pc_ratio > 1.5:
            interp += " PUT-HEAVY (>1.5): significant downside hedging / bearish positioning."
        elif pc_ratio and pc_ratio < 0.7:
            interp += " CALL-HEAVY (<0.7): bullish speculative positioning."

        return {
            "ticker":              ticker.upper(),
            "expiry_filter":       expiry,
            "top_oi_strikes":      results,
            "total_above_spot_oi": total_above,
            "total_below_spot_oi": total_below,
            "below_above_ratio":   pc_ratio,
            "biggest_above_wall":  top_above_str,
            "biggest_below_wall":  top_below_str,
            "interpretation":      interp,
        }
    except Exception as e:
        return {"error": str(e)}


def compute_bearish_signals(min_fear_pp: float = 8.0, min_gex_m: float = 0.0) -> dict:
    """
    Aggregate multi-factor bearish options signals.

    Scoring:
      +6  extreme fear premium (>=15pp)
      +4  fear premium (>=8pp)
      +4  LONG_GAMMA regime (dealer positioning suppresses price)
      +3  INVERTED term structure (near-term stress / catalyst expected)
      +2  high put/call OI ratio from options_structure_scan

    conviction >= 10 = strong multi-factor bearish setup → PUT_OPTION candidate
    conviction >= 7  = moderate bearish → WATCH_PUT
    conviction <  7  = weak single-factor → data only

    BEARISH WORKFLOW:
    mkt_bearish_signals → filter conviction >= 10
    → mkt_oi_by_strike(ticker) to find highest-OI put strike for target
    → mkt_expected_move(ticker) to size the move expectation
    → mkt_iv_rank_live(ticker) — if IV_CHEAP, buying puts is cheap
    """
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            clauses = [
                "scan_date >= CURRENT_DATE - INTERVAL '5 days'",
                f"pc_skew_pp >= {min_fear_pp}",
                "spot >= 5.0",
                "spot IS NOT NULL",
            ]
            if min_gex_m > 0:
                clauses.append(f"ABS(gex_m) >= {min_gex_m}")

            cur.execute(f"""
                SELECT ticker, spot, pc_skew_pp, pc_skew_tag,
                       gex_m, gex_regime, gamma_flip_price,
                       term_ratio, term_tag, front_iv, back_iv
                FROM options_structure_scan
                WHERE {' AND '.join(clauses)}
                  AND (gex_regime = 'LONG_GAMMA'
                       OR term_tag = 'INVERTED'
                       OR pc_skew_pp >= 12)
                ORDER BY pc_skew_pp DESC LIMIT 25
            """)
            cols = [
                "ticker", "spot", "pc_skew_pp", "pc_skew_tag",
                "gex_m", "gex_regime", "gamma_flip_price",
                "term_ratio", "term_tag", "front_iv", "back_iv",
            ]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        results = []
        for r in rows:
            score   = 0.0
            reasons = []
            skew    = float(r["pc_skew_pp"] or 0)

            if skew >= 15:
                score += 6
                reasons.append(f"extreme_fear_premium={skew:.0f}pp")
            elif skew >= 8:
                score += 4
                reasons.append(f"fear_premium={skew:.0f}pp")

            if r.get("gex_regime") == "LONG_GAMMA":
                score += 4
                reasons.append("LONG_GAMMA=price_suppressive")
            if r.get("term_tag") == "INVERTED":
                score += 3
                reasons.append(f"INVERTED_term_ratio={float(r.get('term_ratio') or 0):.2f}")

            gfp = r.get("gamma_flip_price")
            spot = float(r.get("spot") or 0)
            if gfp and spot > 0:
                gfp_f   = float(gfp)
                gfp_pct = (spot - gfp_f) / spot * 100
                r["gamma_flip_distance_pct"] = round(gfp_pct, 2)
                if gfp_pct < 2.0:
                    reasons.append(f"gamma_flip_nearby={gfp_pct:.1f}%_below")

            r["conviction_score"]  = round(score, 1)
            r["reasons"]           = reasons
            r["suggested_trade"]   = (
                "PUT_OPTION"  if score >= 10 else
                "WATCH_PUT"   if score >= 7  else
                "DATA_ONLY"
            )
            for k in ("spot", "pc_skew_pp", "gex_m", "front_iv",
                      "back_iv", "term_ratio", "gamma_flip_price"):
                if r.get(k) is not None:
                    try:
                        r[k] = round(float(r[k]), 4)
                    except Exception:
                        pass
            results.append(r)

        results.sort(key=lambda x: x["conviction_score"], reverse=True)

        return {
            "count":   len(results),
            "results": results,
            "interpretation": (
                "conviction >= 10 = strong multi-factor bearish setup → PUT_OPTION candidate. "
                "LONG_GAMMA + FEAR_PREMIUM = institutions hedging downside + price suppression by dealers. "
                "INVERTED term = catalyst expected within days. "
                "Next steps: mkt_oi_by_strike → find put strike target; "
                "mkt_iv_rank_live → confirm IV is not EXPENSIVE before buying puts."
            ),
        }
    except Exception as e:
        return {"error": str(e)}


def verify_options_decision_inputs(
    ticker: str,
    call_data: dict,
    put_data: dict,
) -> dict:
    """
    Runtime gate — MANDATORY before outputting LONG CALL / LONG PUT / NO TRADE.

    Thresholds come from ``aiem_options_gate_profile.resolve_gate_profile()``
    (OE_GATE_PROFILE=strict|balanced|opportunity, default balanced).

    Required stock-level fields (in call_data — shared context):
      stock_direction, market_regime, iv_rank, iv_crush_risk,
      vwap_position, sector_strength, market_breadth

    Per-contract fields are required only for *active* legs (bid+ask present).
    When allow_single_leg=True (balanced/opportunity), a missing opposite leg
    does NOT block ready_for_decision — morning CALL movers often have no
    liquid put quotes early.

    Hard rejection gates (active leg only; None skips that check):
      ✗ dte < min_dte
      ✗ open_interest < min_oi
      ✗ volume < min_volume
      ✗ bid_ask_spread_pct > max_spread_pct
      ✗ slippage_pct > max_slippage_pct
      ✗ |delta| < min_delta
      ✗ probability_estimate < min_pop

    NOTE: D5 risk/reward is NOT a hard gate here — it is a REQ6 score weight.
    """
    try:
        from aiem_options_gate_profile import resolve_gate_profile, describe_gate_profile
        _gate = resolve_gate_profile()
    except Exception:
        _gate = {
            "profile": "balanced",
            "min_oi": 250, "min_volume": 50,
            "max_spread_pct": 0.28, "max_slippage_pct": 0.20,
            "min_delta": 0.18, "min_pop": 0.30, "min_dte": 5,
            "score_min": 50.0, "margin_min": 8.0,
            "allow_single_leg": True,
        }
        def describe_gate_profile(c=None):  # noqa: E306
            return "profile=balanced (fallback)"

    # volume / open_interest are OPTIONAL presence-wise: None skips the
    # liquidity hard gates (Tradier often unavailable early). When populated,
    # min_oi / min_volume still apply in _check_gates.
    PER_CONTRACT_FIELDS = [
        "delta", "gamma", "theta", "vega", "iv",
        "bid", "ask", "bid_ask_spread_pct",
        "breakeven", "premium_at_risk",
        "expected_move", "probability_estimate",
        "expected_return", "dte", "slippage_pct",
    ]
    STOCK_FIELDS = [
        "stock_direction", "market_regime", "iv_rank", "iv_crush_risk",
        "vwap_position", "sector_strength", "market_breadth",
    ]

    call_data = call_data or {}
    put_data  = put_data  or {}
    missing   = []
    allow_single = bool(_gate.get("allow_single_leg", True))
    score_min = float(_gate.get("score_min", 50))
    margin_min = float(_gate.get("margin_min", 8))

    def _leg_active(data: dict) -> bool:
        return data.get("bid") is not None and data.get("ask") is not None

    call_active = _leg_active(call_data)
    put_active = _leg_active(put_data)

    # Stock context: prefer call_data, fall back to put_data (single-leg PUT).
    stock_src = call_data if any(call_data.get(f) is not None for f in STOCK_FIELDS) else put_data
    for f in STOCK_FIELDS:
        if stock_src.get(f) is None and call_data.get(f) is None and put_data.get(f) is None:
            missing.append(f"stock:{f}")

    legs_to_require = []
    if allow_single:
        if call_active:
            legs_to_require.append(("call", call_data))
        if put_active:
            legs_to_require.append(("put", put_data))
        if not legs_to_require:
            # Neither leg quoted — not ready (scheduler usually exits earlier).
            missing.append("call:bid")
            missing.append("put:bid")
    else:
        legs_to_require = [("call", call_data), ("put", put_data)]

    for label, data in legs_to_require:
        for f in PER_CONTRACT_FIELDS:
            if data.get(f) is None:
                missing.append(f"{label}:{f}")

    min_oi = float(_gate["min_oi"])
    min_vol = float(_gate["min_volume"])
    max_spread = float(_gate["max_spread_pct"])
    max_slip = float(_gate["max_slippage_pct"])
    min_delta = float(_gate["min_delta"])
    min_pop = float(_gate["min_pop"])
    min_dte = float(_gate["min_dte"])

    def _check_gates(label: str, data: dict) -> list:
        fails = []
        checks = [
            ("dte", lambda v: float(v) < min_dte,
             f"DTE < {min_dte:g} — expiration too close"),
            ("open_interest", lambda v: float(v) < min_oi,
             f"OI < {min_oi:g} — insufficient liquidity"),
            ("volume", lambda v: float(v) < min_vol,
             f"volume < {min_vol:g} — insufficient liquidity"),
            ("bid_ask_spread_pct", lambda v: float(v) > max_spread,
             f"bid/ask spread > {max_spread:.0%} of mid"),
            ("slippage_pct", lambda v: float(v) > max_slip,
             f"slippage > {max_slip:.0%}"),
            ("delta", lambda v: abs(float(v)) < min_delta,
             f"delta < {min_delta:g} — lottery strike"),
            ("probability_estimate", lambda v: float(v) < min_pop,
             f"PoP < {min_pop:.0%} — below minimum threshold"),
        ]
        for field, test, reason in checks:
            val = data.get(field)
            if val is not None:
                try:
                    if test(val):
                        fails.append(f"{label}: {reason} (value={val})")
                except Exception:
                    pass
        return fails

    # Inactive legs are ineligible (not gate-failed) when single-leg is allowed.
    call_gate_fails = _check_gates("call", call_data) if call_active else []
    put_gate_fails = _check_gates("put", put_data) if put_active else []
    all_gate_fails = call_gate_fails + put_gate_fails

    stock_ok = not any(m.startswith("stock:") for m in missing)
    call_fields_ok = not any(m.startswith("call:") for m in missing)
    put_fields_ok = not any(m.startswith("put:") for m in missing)

    if allow_single:
        call_eligible = (
            stock_ok and call_active and call_fields_ok and len(call_gate_fails) == 0
        )
        put_eligible = (
            stock_ok and put_active and put_fields_ok and len(put_gate_fails) == 0
        )
        # Missing fields on inactive opposite leg must not block readiness.
        blocking_missing = [m for m in missing if m.startswith("stock:")]
        if call_active:
            blocking_missing += [m for m in missing if m.startswith("call:")]
        if put_active:
            blocking_missing += [m for m in missing if m.startswith("put:")]
        if not call_active and not put_active:
            blocking_missing = list(missing)
        missing = blocking_missing
        ready = len(missing) == 0 and (call_eligible or put_eligible)
    else:
        call_eligible = len(missing) == 0 and len(call_gate_fails) == 0
        put_eligible = len(missing) == 0 and len(put_gate_fails) == 0
        ready = len(missing) == 0 and (call_eligible or put_eligible)

    profile_txt = describe_gate_profile(_gate)

    if missing and not ready:
        verdict = (
            f"NOT READY — {len(missing)} required field(s) missing "
            f"[{profile_txt}]. "
            "Collect all missing inputs before calling this function again."
        )
    elif not call_eligible and not put_eligible:
        verdict = (
            f"BOTH DIRECTIONS REJECTED by hard gates [{profile_txt}]. "
            "Return NO TRADE — neither the call nor the put meets minimum quality standards."
        )
    elif not call_eligible:
        verdict = (
            f"CALL rejected by hard gates [{profile_txt}]. Only the PUT is eligible. "
            f"Proceed to final scoring — still requires overall score >= {score_min:g} "
            f"and margin >= {margin_min:g} to send alert."
        )
    elif not put_eligible:
        verdict = (
            f"PUT rejected by hard gates [{profile_txt}]. Only the CALL is eligible. "
            f"Proceed to final scoring — still requires overall score >= {score_min:g} "
            f"and margin >= {margin_min:g} to send alert."
        )
    else:
        verdict = (
            f"BOTH directions passed all gates [{profile_txt}]. "
            "Proceed to final scoring — highest score wins; "
            f"winning direction must score >= {score_min:g} AND "
            f">= {margin_min:g} points above the other to send an alert."
        )

    return {
        "ticker":             ticker.upper(),
        "ready_for_decision": ready,
        "missing_fields":     missing,
        "gate_failures":      all_gate_fails,
        "call_eligible":      call_eligible,
        "put_eligible":       put_eligible,
        "gate_profile":       _gate.get("profile"),
        "gate_thresholds":    {
            "min_oi": min_oi, "min_volume": min_vol,
            "max_spread_pct": max_spread, "max_slippage_pct": max_slip,
            "min_delta": min_delta, "min_pop": min_pop, "min_dte": min_dte,
            "score_min": score_min, "margin_min": margin_min,
            "allow_single_leg": allow_single,
        },
        "verdict":            verdict,
    }
