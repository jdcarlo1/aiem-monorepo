#!/usr/bin/env python3
"""Liquidity census — Monday Item A re-run script.

Run during trading hours (09:30-16:00 ET) BEFORE any commit.

Usage:
    python3 artifacts/stock-scanner-api/tools/liquidity_census.py CLF HAL AMGN NEE VRTX
    python3 artifacts/stock-scanner-api/tools/liquidity_census.py CLF --min-dte 5
"""
import sys, os, argparse

# Resolve to stock-scanner-api root regardless of CWD
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)   # tools/ → stock-scanner-api/
sys.path.insert(0, _API_ROOT)

import aiem_polygon_options_chain as ch

_LIQ_IV_MAX = 3.0


def _is_liquid(c):
    """Directive liquidity predicate:
    bid>0, ask>0, 0<iv<=3.0, abs(delta)>0.
    No OI/volume/spread gates — those are downstream gate concerns.
    """
    b, a = c.get("bid"), c.get("ask")
    iv, d = c.get("implied_volatility"), c.get("delta")
    if b is None or a is None or b <= 0 or a <= 0:
        return False
    if iv is None or iv <= 0 or iv > _LIQ_IV_MAX:
        return False
    if d is None or abs(d) == 0.0:
        return False
    return True


def liquidity_census(ticker, min_dte=5):
    result = ch.fetch_options_chain(ticker, min_dte=min_dte, max_dte=21)
    if result.get("fetch_error"):
        print(f"{ticker} FETCH_ERROR: {result['fetch_error']}")
        return {"ok": False}

    expirations = result.get("expirations", [])
    if not expirations:
        print(f"{ticker} NO_EXPIRATIONS")
        return {"ok": False}

    nearest_exp = expirations[0]
    summary = {}

    for typ in ("call", "put"):
        contracts = [
            c for c in result.get(f"{typ}s", [])
            if c.get("expiration_date") == nearest_exp
        ]
        total = len(contracts)
        liquid = [c for c in contracts if _is_liquid(c)]
        otm_band = [c for c in liquid if 0.20 <= abs(c.get("delta") or 0) <= 0.50]

        if liquid:
            deltas = [abs(c.get("delta") or 0) for c in liquid]
            d_range = f"[{min(deltas):.4f}..{max(deltas):.4f}]"
            strikes_liquid = [c["strike"] for c in liquid]
        else:
            d_range = "N/A"
            strikes_liquid = []

        print(
            f"{ticker} {typ.upper()} expiry={nearest_exp} "
            f"total={total} liquid={len(liquid)} "
            f"delta_range={d_range} strikes_liquid={strikes_liquid}"
        )

        if otm_band:
            print(f"  => contracts with abs(delta) in [0.20,0.50]: {len(otm_band)}")
            for c in otm_band:
                print(
                    f"     strike={c['strike']} delta={c['delta']:.4f} "
                    f"bid={c['bid']} ask={c['ask']} "
                    f"iv={c['implied_volatility']:.4f} "
                    f"oi={c.get('open_interest')} vol={c.get('volume')}"
                )

        summary[(ticker, typ)] = {
            "total": total, "liquid": len(liquid),
            "otm_band": len(otm_band), "strikes_liquid": strikes_liquid,
        }
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Liquidity census — paste output into Item A before any commit."
    )
    parser.add_argument("tickers", nargs="+",
                        help="Space-separated ticker list, e.g. CLF HAL AMGN NEE VRTX")
    parser.add_argument("--min-dte", type=int, default=5,
                        help="Minimum DTE for nearest expiry (default: 5)")
    args = parser.parse_args()

    total_otm = 0
    for ticker in args.tickers:
        s = liquidity_census(ticker.upper(), min_dte=args.min_dte)
        if isinstance(s, dict):
            for v in s.values():
                if isinstance(v, dict):
                    total_otm += v.get("otm_band", 0)
        print()

    print(
        f"=== GATE CHECK: ticker/leg combinations with abs(delta) in [0.20,0.50]: "
        f"{total_otm} / {len(args.tickers)*2} ==="
    )
    if total_otm == 0:
        print("GATE: FAIL — 0 liquid contracts. DO NOT COMMIT. "
              "Check market hours (09:30-16:00 ET) and Polygon API status.")
    else:
        print("GATE: PASS — liquid contracts exist. Proceed to dry-run (Item K step 2).")


if __name__ == "__main__":
    main()
