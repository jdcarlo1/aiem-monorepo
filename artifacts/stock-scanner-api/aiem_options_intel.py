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
                  AND scan_date >= CURRENT_DATE - INTERVAL '2 days'
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

        if iv_rank < 20:
            verdict = "CHEAP — favor BUYING options (calls or puts); low cost to be long vega"
        elif iv_rank > 80:
            verdict = "EXPENSIVE — favor SELLING premium (credit spreads, covered calls, iron condors)"
        else:
            verdict = "FAIR — directional options plays acceptable; standard sizing"

        return {
            "ticker":      ticker.upper(),
            "spot":        round(spot, 2),
            "current_iv":  round(current_iv, 4),
            "iv_rank":     round(iv_rank, 1),
            "iv_low_52w":  round(iv_low, 4),
            "iv_high_52w": round(iv_high, 4),
            "skew_tag":    skew_tag,
            "verdict":     verdict,
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
            if expiry:
                cur.execute("""
                    SELECT strike, option_type, SUM(oi) AS total_oi
                    FROM oi_daily_snapshot
                    WHERE ticker = %s AND expiry = %s
                      AND snapshot_date = (
                          SELECT MAX(snapshot_date) FROM oi_daily_snapshot WHERE ticker = %s
                      )
                    GROUP BY strike, option_type
                    ORDER BY total_oi DESC LIMIT 20
                """, (ticker.upper(), expiry, ticker.upper()))
            else:
                cur.execute("""
                    SELECT strike, option_type, SUM(oi) AS total_oi
                    FROM oi_daily_snapshot
                    WHERE ticker = %s
                      AND snapshot_date = (
                          SELECT MAX(snapshot_date) FROM oi_daily_snapshot WHERE ticker = %s
                      )
                    GROUP BY strike, option_type
                    ORDER BY total_oi DESC LIMIT 20
                """, (ticker.upper(), ticker.upper()))
            rows = cur.fetchall()

        if not rows:
            return {"error": f"No OI snapshot data for {ticker}"}

        results      = []
        total_call   = 0
        total_put    = 0
        top_call_oi  = 0
        top_put_oi   = 0
        top_call_str = None
        top_put_str  = None

        for strike, opt_type, oi in rows:
            s   = float(strike)
            oi_ = int(oi)
            results.append({"strike": s, "type": opt_type, "oi": oi_})
            if opt_type == "call":
                total_call += oi_
                if oi_ > top_call_oi:
                    top_call_oi, top_call_str = oi_, s
            else:
                total_put += oi_
                if oi_ > top_put_oi:
                    top_put_oi, top_put_str = oi_, s

        pc_ratio = round(total_put / total_call, 2) if total_call > 0 else None

        interp = (
            f"Largest call wall: ${top_call_str} ({top_call_oi:,} OI) = overhead resistance. "
            f"Largest put wall: ${top_put_str} ({top_put_oi:,} OI) = floor support. "
            f"Put/Call OI ratio: {pc_ratio or 'N/A'}"
        )
        if pc_ratio and pc_ratio > 1.5:
            interp += " — PUT-HEAVY (>1.5): significant downside hedging / bearish positioning."
        elif pc_ratio and pc_ratio < 0.7:
            interp += " — CALL-HEAVY (<0.7): bullish speculative positioning."

        return {
            "ticker":            ticker.upper(),
            "expiry_filter":     expiry,
            "top_oi_strikes":    results,
            "total_call_oi":     total_call,
            "total_put_oi":      total_put,
            "put_call_oi_ratio": pc_ratio,
            "biggest_call_wall": top_call_str,
            "biggest_put_wall":  top_put_str,
            "interpretation":    interp,
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
                "scan_date >= CURRENT_DATE - INTERVAL '2 days'",
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

    Confirms every required input from the final decision requirements has been
    collected for both the call and the put. Returns ready_for_decision=True only
    when ALL required fields are populated AND no hard rejection gate fires.

    Required per-contract fields (call_data and put_data must each contain):
      delta, gamma, theta, vega, iv, volume, open_interest,
      bid, ask, bid_ask_spread_pct, breakeven, premium_at_risk,
      expected_move, probability_estimate, expected_return, dte, slippage_pct

    Required stock-level fields (in call_data — shared context):
      stock_direction, market_regime, iv_rank, iv_crush_risk,
      vwap_position, sector_strength, market_breadth

    Hard rejection gates (fail → that direction is ineligible):
      ✗ dte < 5                        (expiration too close)
      ✗ open_interest < 500            (insufficient liquidity)
      ✗ volume < 100                   (insufficient liquidity)
      ✗ bid_ask_spread_pct > 0.20      (excessive spread)
      ✗ slippage_pct > 0.15            (excessive slippage)
      ✗ delta < 0.20                   (lottery strike)
      ✗ probability_estimate < 0.35   (below minimum PoP)
    """
    PER_CONTRACT_FIELDS = [
        "delta", "gamma", "theta", "vega", "iv",
        "volume", "open_interest",
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

    for f in STOCK_FIELDS:
        if call_data.get(f) is None:
            missing.append(f"stock:{f}")

    for label, data in [("call", call_data), ("put", put_data)]:
        for f in PER_CONTRACT_FIELDS:
            if data.get(f) is None:
                missing.append(f"{label}:{f}")

    def _check_gates(label: str, data: dict) -> list:
        fails = []
        checks = [
            ("dte",                  lambda v: float(v) < 5,    "DTE < 5 — expiration too close"),
            ("open_interest",        lambda v: float(v) < 500,  "OI < 500 — insufficient liquidity"),
            ("volume",               lambda v: float(v) < 100,  "volume < 100 — insufficient liquidity"),
            ("bid_ask_spread_pct",   lambda v: float(v) > 0.20, "bid/ask spread > 20% of mid"),
            ("slippage_pct",         lambda v: float(v) > 0.15, "slippage > 15%"),
            ("delta",                lambda v: abs(float(v)) < 0.20, "delta < 0.20 — lottery strike"),
            ("probability_estimate", lambda v: float(v) < 0.35, "PoP < 35% — below minimum threshold"),
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

    call_gate_fails = _check_gates("call", call_data)
    put_gate_fails  = _check_gates("put",  put_data)
    all_gate_fails  = call_gate_fails + put_gate_fails

    call_eligible = len(missing) == 0 and len(call_gate_fails) == 0
    put_eligible  = len(missing) == 0 and len(put_gate_fails)  == 0
    ready         = len(missing) == 0 and (call_eligible or put_eligible)

    if missing:
        verdict = (
            f"NOT READY — {len(missing)} required field(s) missing. "
            "Collect all missing inputs before calling this function again."
        )
    elif not call_eligible and not put_eligible:
        verdict = (
            "BOTH DIRECTIONS REJECTED by hard gates. "
            "Return NO TRADE — neither the call nor the put meets minimum quality standards."
        )
    elif not call_eligible:
        verdict = (
            "CALL rejected by hard gates. Only the PUT is eligible. "
            "Proceed to final scoring — still requires overall score >= 55 to send alert."
        )
    elif not put_eligible:
        verdict = (
            "PUT rejected by hard gates. Only the CALL is eligible. "
            "Proceed to final scoring — still requires overall score >= 55 to send alert."
        )
    else:
        verdict = (
            "BOTH directions passed all gates and have all required inputs. "
            "Proceed to final scoring — highest score wins; "
            "winning direction must score >= 55 AND >= 10 points above the other to send an alert."
        )

    return {
        "ticker":             ticker.upper(),
        "ready_for_decision": ready,
        "missing_fields":     missing,
        "gate_failures":      all_gate_fails,
        "call_eligible":      call_eligible,
        "put_eligible":       put_eligible,
        "verdict":            verdict,
    }
