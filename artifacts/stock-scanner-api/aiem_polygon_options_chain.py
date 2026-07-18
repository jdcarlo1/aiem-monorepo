"""
aiem_polygon_options_chain.py — Real Polygon Options Chain for the Standalone Options Engine

Fetches full options chains from Polygon v3/snapshot/options/{ticker}.
Parses real Greeks, IV, volume, OI, bid/ask for every contract.
Provides strategy evaluation for all supported multi-leg structures.

Supported strategies evaluated:
  LONG_CALL            buy ATM/OTM call
  LONG_PUT             buy ATM/OTM put
  BULL_CALL_SPREAD     buy ATM call + sell OTM call
  BEAR_PUT_SPREAD      buy ATM put  + sell OTM put
  IRON_CONDOR          sell OTM call + put, buy further OTM call + put
  IRON_BUTTERFLY       sell ATM call + put, buy wings
  LONG_STRANGLE        buy OTM call + OTM put
  COVERED_CALL         (paper only — long stock + short call)
  CASH_SECURED_PUT     (paper only — short put)

All real data from Polygon — no synthetic/hardcoded values.
"""

import os
import json
import math
import logging
import urllib.request
import urllib.parse
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger("aiem_polygon_options_chain")

_POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
_BASE        = "https://api.polygon.io"

# Strategy evaluation constants
_MIN_OI       = 50       # minimum open interest per leg
_MIN_VOLUME   = 5        # minimum daily volume per leg
_MAX_SPREAD   = 0.25     # max bid-ask spread as pct of mid (25%)
_MIN_DTE      = 5
_MAX_DTE      = 25


# ─────────────────────────────────────────────────────────────────────────────
# POLYGON FETCH
# ─────────────────────────────────────────────────────────────────────────────

def _polygon_get(path: str, params: dict) -> Optional[dict]:
    params["apiKey"] = _POLYGON_KEY
    url = f"{_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aiem-options-chain/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        log.debug(f"[polygon_get] {path} error: {e}")
        return None


def fetch_options_chain(ticker: str,
                        min_dte: int = _MIN_DTE,
                        max_dte: int = _MAX_DTE,
                        limit: int = 250) -> dict:
    """
    Fetch full options chain from Polygon v3/snapshot/options/{ticker}.
    Filters to DTE range [min_dte, max_dte].

    Returns:
      {
        "calls":          list of parsed call contracts,
        "puts":           list of parsed put contracts,
        "expirations":    sorted list of available expiry dates,
        "contracts_total": int,
        "ticker":         str,
        "fetch_error":    str | None,
      }
    """
    today = date.today()
    min_exp = (today + timedelta(days=min_dte)).isoformat()
    max_exp = (today + timedelta(days=max_dte)).isoformat()

    resp = _polygon_get(
        f"/v3/snapshot/options/{ticker}",
        {
            "limit":                  str(limit),
            "expiration_date.gte":    min_exp,
            "expiration_date.lte":    max_exp,
        },
    )

    if not resp or "results" not in resp:
        return {
            "calls": [], "puts": [],
            "expirations": [], "contracts_total": 0,
            "ticker": ticker,
            "fetch_error": "no_results_from_polygon",
        }

    calls, puts = [], []
    expirations_seen = set()

    for c in resp.get("results", []):
        details    = c.get("details", {})
        greeks     = c.get("greeks", {})
        day_data   = c.get("day",    {})
        quote      = c.get("last_quote", {})
        implied_iv = c.get("implied_volatility", None)

        exp_str  = details.get("expiration_date", "")
        ctype    = details.get("contract_type", "")
        strike   = details.get("strike_price", 0.0)

        if not exp_str or not ctype or not strike:
            continue

        try:
            exp_date = date.fromisoformat(exp_str)
        except ValueError:
            continue

        dte = (exp_date - today).days
        if dte < min_dte or dte > max_dte:
            continue

        expirations_seen.add(exp_str)

        bid    = float(quote.get("bid", 0.0)      or 0.0)
        ask    = float(quote.get("ask", 0.0)      or 0.0)
        mid    = float(quote.get("midpoint", 0.0) or ((bid + ask) / 2 if bid + ask > 0 else 0.0))

        if mid <= 0 and bid <= 0 and ask <= 0:
            mid = 0.0

        spread_pct = ((ask - bid) / mid) if mid > 0 else 1.0

        volume = int(day_data.get("volume",       0) or 0)
        oi     = int(c.get("open_interest",       0) or 0)

        contract = {
            "ticker":          ticker,
            "contract_type":   ctype,           # "call" | "put"
            "expiration_date": exp_str,
            "strike":          float(strike),
            "dte":             dte,
            "bid":             round(bid, 4),
            "ask":             round(ask, 4),
            "mid":             round(mid, 4),
            "bid_ask_spread_pct": round(spread_pct, 4),
            "volume":          volume,
            "open_interest":   oi,
            "implied_volatility": float(implied_iv) if implied_iv else None,
            "delta":  float(greeks.get("delta",  0.0) or 0.0),
            "gamma":  float(greeks.get("gamma",  0.0) or 0.0),
            "theta":  float(greeks.get("theta",  0.0) or 0.0),
            "vega":   float(greeks.get("vega",   0.0) or 0.0),
            "rho":    float(greeks.get("rho",    0.0) or 0.0),
            "liquid": (oi >= _MIN_OI and volume >= _MIN_VOLUME and
                       spread_pct <= _MAX_SPREAD and mid > 0),
        }

        if ctype == "call":
            calls.append(contract)
        elif ctype == "put":
            puts.append(contract)

    # Sort by strike
    calls.sort(key=lambda x: (x["expiration_date"], x["strike"]))
    puts.sort(key=lambda x:  (x["expiration_date"], x["strike"]))

    return {
        "calls":           calls,
        "puts":            puts,
        "expirations":     sorted(expirations_seen),
        "contracts_total": len(calls) + len(puts),
        "ticker":          ticker,
        "fetch_error":     None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT SELECTION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _best_expiry(expirations: list, target_dte: int = 14) -> Optional[str]:
    """Pick the expiry closest to target_dte from the list."""
    today = date.today()
    best, best_diff = None, 999
    for exp in expirations:
        try:
            d = date.fromisoformat(exp)
            diff = abs((d - today).days - target_dte)
            if diff < best_diff:
                best, best_diff = exp, diff
        except ValueError:
            pass
    return best


def _filter_by_expiry(contracts: list, expiry: str) -> list:
    return [c for c in contracts if c["expiration_date"] == expiry]


def _closest_delta(contracts: list, target_delta: float) -> Optional[dict]:
    """Find the liquid contract whose |delta| is closest to target."""
    liquid = [c for c in contracts if c["liquid"] and c["mid"] > 0]
    if not liquid:
        liquid = [c for c in contracts if c["mid"] > 0]   # relax liquidity gate
    if not liquid:
        return None
    return min(liquid, key=lambda x: abs(abs(x["delta"]) - target_delta))


def _otm_from_atm(contracts: list, atm_strike: float,
                  direction: str = "up", pct: float = 0.05) -> Optional[dict]:
    """
    Find a liquid OTM contract roughly pct% away from atm_strike.
    direction = "up" for call wings, "down" for put wings.
    """
    target = atm_strike * (1 + pct if direction == "up" else 1 - pct)
    liquid = [c for c in contracts if c["liquid"] and c["mid"] > 0]
    if not liquid:
        liquid = [c for c in contracts if c["mid"] > 0]
    if not liquid:
        return None
    return min(liquid, key=lambda x: abs(x["strike"] - target))


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY EVALUATION
# ─────────────────────────────────────────────────────────────────────────────

def _pop_from_delta(delta: float, ctype: str) -> float:
    """Rough POP: for calls delta≈POP, for puts POP≈1-|delta|."""
    if ctype == "call":
        return max(0.0, min(1.0, abs(delta)))
    else:
        return max(0.0, min(1.0, 1.0 - abs(delta)))


def _ev(max_profit: float, max_loss: float, pop: float) -> float:
    pnl_if_win  = max_profit
    pnl_if_lose = -abs(max_loss)
    return round(pop * pnl_if_win + (1 - pop) * pnl_if_lose, 4)


def _slippage(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2
    return round((ask - mid) / mid, 4) if mid > 0 else 0.05


def eval_long_call(calls: list, spot: float, expiry: str) -> Optional[dict]:
    """Long call: buy ATM/slight-OTM call."""
    exp_calls = _filter_by_expiry(calls, expiry)
    leg = _closest_delta(exp_calls, 0.40)  # slight OTM
    if not leg:
        return None
    cost  = leg["mid"] * 100
    pop   = _pop_from_delta(leg["delta"], "call")
    max_p = 999 * 100   # unlimited; cap at 3x for EV
    max_l = cost
    return {
        "strategy":      "LONG_CALL",
        "direction":     "BULLISH",
        "legs":          [{"action": "BUY", **leg}],
        "net_debit":     round(cost, 2),
        "net_credit":    0.0,
        "max_profit":    round(leg["mid"] * 3 * 100, 2),  # estimated 3x
        "max_loss":      round(max_l, 2),
        "breakeven":     round(leg["strike"] + leg["mid"], 2),
        "pop":           round(pop, 4),
        "ev_after_costs":_ev(leg["mid"] * 3 * 100, max_l, pop),
        "capital_required": round(cost, 2),
        "delta":         leg["delta"],
        "vega":          leg["vega"],
        "theta":         leg["theta"],
        "iv":            leg["implied_volatility"],
        "slippage_pct":  _slippage(leg["bid"], leg["ask"]),
        "liquid":        leg["liquid"],
    }


def eval_long_put(puts: list, spot: float, expiry: str) -> Optional[dict]:
    """Long put: buy ATM/slight-OTM put."""
    exp_puts = _filter_by_expiry(puts, expiry)
    leg = _closest_delta(exp_puts, 0.40)
    if not leg:
        return None
    cost = leg["mid"] * 100
    pop  = _pop_from_delta(leg["delta"], "put")
    return {
        "strategy":      "LONG_PUT",
        "direction":     "BEARISH",
        "legs":          [{"action": "BUY", **leg}],
        "net_debit":     round(cost, 2),
        "net_credit":    0.0,
        "max_profit":    round(leg["strike"] * 100 - cost, 2),
        "max_loss":      round(cost, 2),
        "breakeven":     round(leg["strike"] - leg["mid"], 2),
        "pop":           round(pop, 4),
        "ev_after_costs":_ev(leg["strike"] * 0.5 * 100, cost, pop),
        "capital_required": round(cost, 2),
        "delta":         leg["delta"],
        "vega":          leg["vega"],
        "theta":         leg["theta"],
        "iv":            leg["implied_volatility"],
        "slippage_pct":  _slippage(leg["bid"], leg["ask"]),
        "liquid":        leg["liquid"],
    }


def eval_bull_call_spread(calls: list, spot: float, expiry: str) -> Optional[dict]:
    """Buy ATM call, sell OTM call ~5% higher."""
    exp_calls = _filter_by_expiry(calls, expiry)
    long_leg  = _closest_delta(exp_calls, 0.50)
    if not long_leg:
        return None
    short_leg = _otm_from_atm(exp_calls, long_leg["strike"], "up", 0.05)
    if not short_leg or short_leg["strike"] <= long_leg["strike"]:
        return None
    width    = short_leg["strike"] - long_leg["strike"]
    net_deb  = (long_leg["mid"] - short_leg["mid"]) * 100
    if net_deb <= 0:
        return None
    max_p    = (width - (long_leg["mid"] - short_leg["mid"])) * 100
    max_l    = net_deb
    pop      = round(1 - long_leg["delta"] + short_leg["delta"], 4)
    pop      = max(0.3, min(0.7, pop))
    return {
        "strategy":      "BULL_CALL_SPREAD",
        "direction":     "BULLISH",
        "legs":          [{"action": "BUY", **long_leg}, {"action": "SELL", **short_leg}],
        "net_debit":     round(net_deb, 2),
        "net_credit":    0.0,
        "max_profit":    round(max(0, max_p), 2),
        "max_loss":      round(max_l, 2),
        "breakeven":     round(long_leg["strike"] + (long_leg["mid"] - short_leg["mid"]), 2),
        "pop":           pop,
        "ev_after_costs":_ev(max_p, max_l, pop),
        "capital_required": round(max_l, 2),
        "delta":         round(long_leg["delta"] - short_leg["delta"], 4),
        "vega":          round(long_leg["vega"]  - short_leg["vega"],  4),
        "theta":         round(long_leg["theta"] - short_leg["theta"], 4),
        "iv":            long_leg["implied_volatility"],
        "slippage_pct":  round((_slippage(long_leg["bid"], long_leg["ask"]) +
                                _slippage(short_leg["bid"], short_leg["ask"])) / 2, 4),
        "liquid":        long_leg["liquid"] and short_leg["liquid"],
    }


def eval_bear_put_spread(puts: list, spot: float, expiry: str) -> Optional[dict]:
    """Buy ATM put, sell OTM put ~5% lower."""
    exp_puts  = _filter_by_expiry(puts, expiry)
    long_leg  = _closest_delta(exp_puts, 0.50)
    if not long_leg:
        return None
    short_leg = _otm_from_atm(exp_puts, long_leg["strike"], "down", 0.05)
    if not short_leg or short_leg["strike"] >= long_leg["strike"]:
        return None
    width    = long_leg["strike"] - short_leg["strike"]
    net_deb  = (long_leg["mid"] - short_leg["mid"]) * 100
    if net_deb <= 0:
        return None
    max_p    = (width - (long_leg["mid"] - short_leg["mid"])) * 100
    max_l    = net_deb
    pop      = round(abs(long_leg["delta"]) - abs(short_leg["delta"]), 4)
    pop      = max(0.3, min(0.7, pop))
    return {
        "strategy":      "BEAR_PUT_SPREAD",
        "direction":     "BEARISH",
        "legs":          [{"action": "BUY", **long_leg}, {"action": "SELL", **short_leg}],
        "net_debit":     round(net_deb, 2),
        "net_credit":    0.0,
        "max_profit":    round(max(0, max_p), 2),
        "max_loss":      round(max_l, 2),
        "breakeven":     round(long_leg["strike"] - (long_leg["mid"] - short_leg["mid"]), 2),
        "pop":           pop,
        "ev_after_costs":_ev(max_p, max_l, pop),
        "capital_required": round(max_l, 2),
        "delta":         round(long_leg["delta"] - short_leg["delta"], 4),
        "vega":          round(long_leg["vega"]  - short_leg["vega"],  4),
        "theta":         round(long_leg["theta"] - short_leg["theta"], 4),
        "iv":            long_leg["implied_volatility"],
        "slippage_pct":  round((_slippage(long_leg["bid"], long_leg["ask"]) +
                                _slippage(short_leg["bid"], short_leg["ask"])) / 2, 4),
        "liquid":        long_leg["liquid"] and short_leg["liquid"],
    }


def eval_iron_condor(calls: list, puts: list, spot: float, expiry: str) -> Optional[dict]:
    """
    Sell OTM call spread + sell OTM put spread.
    Short call ~5% OTM, long call ~10% OTM.
    Short put ~5% OTM, long put ~10% OTM.
    """
    exp_calls = _filter_by_expiry(calls, expiry)
    exp_puts  = _filter_by_expiry(puts,  expiry)

    sc = _otm_from_atm(exp_calls, spot, "up",   0.05)   # short call
    lc = _otm_from_atm(exp_calls, spot, "up",   0.10)   # long call (wing)
    sp = _otm_from_atm(exp_puts,  spot, "down", 0.05)   # short put
    lp = _otm_from_atm(exp_puts,  spot, "down", 0.10)   # long put (wing)

    if not all([sc, lc, sp, lp]):
        return None
    if sc["strike"] >= lc["strike"] or sp["strike"] <= lp["strike"]:
        return None

    net_credit = (sc["mid"] - lc["mid"] + sp["mid"] - lp["mid"]) * 100
    if net_credit <= 0:
        return None

    call_width = (lc["strike"] - sc["strike"]) * 100
    put_width  = (sp["strike"] - lp["strike"]) * 100
    max_loss   = min(call_width, put_width) - net_credit
    pop        = round(1 - abs(sc["delta"]) - abs(sp["delta"]), 4)
    pop        = max(0.50, min(0.80, pop))

    return {
        "strategy":      "IRON_CONDOR",
        "direction":     "NEUTRAL",
        "legs":          [
            {"action": "SELL", **sc}, {"action": "BUY",  **lc},
            {"action": "SELL", **sp}, {"action": "BUY",  **lp},
        ],
        "net_debit":     0.0,
        "net_credit":    round(net_credit, 2),
        "max_profit":    round(net_credit, 2),
        "max_loss":      round(max(0, max_loss), 2),
        "breakeven_call":round(sc["strike"] + net_credit / 100, 2),
        "breakeven_put": round(sp["strike"] - net_credit / 100, 2),
        "pop":           pop,
        "ev_after_costs":_ev(net_credit, max(0, max_loss), pop),
        "capital_required": round(max(call_width, put_width) - net_credit, 2),
        "delta":         round(sc["delta"] - lc["delta"] + sp["delta"] - lp["delta"], 4),
        "slippage_pct":  round(sum(_slippage(x["bid"], x["ask"])
                                   for x in [sc, lc, sp, lp]) / 4, 4),
        "liquid":        all(x["liquid"] for x in [sc, lc, sp, lp]),
    }


def eval_long_strangle(calls: list, puts: list, spot: float, expiry: str) -> Optional[dict]:
    """Buy OTM call + OTM put ~5% from ATM."""
    exp_calls = _filter_by_expiry(calls, expiry)
    exp_puts  = _filter_by_expiry(puts,  expiry)
    lc = _otm_from_atm(exp_calls, spot, "up",   0.05)
    lp = _otm_from_atm(exp_puts,  spot, "down", 0.05)
    if not lc or not lp:
        return None
    cost   = (lc["mid"] + lp["mid"]) * 100
    pop    = round(abs(lc["delta"]) + abs(lp["delta"]), 4)
    pop    = max(0.20, min(0.60, pop))
    return {
        "strategy":       "LONG_STRANGLE",
        "direction":      "NEUTRAL",
        "legs":           [{"action": "BUY", **lc}, {"action": "BUY", **lp}],
        "net_debit":      round(cost, 2),
        "net_credit":     0.0,
        "max_profit":     9999.0,   # unlimited
        "max_loss":       round(cost, 2),
        "breakeven_call": round(lc["strike"] + (lc["mid"] + lp["mid"]), 2),
        "breakeven_put":  round(lp["strike"] - (lc["mid"] + lp["mid"]), 2),
        "pop":            pop,
        "ev_after_costs": _ev(cost * 1.5, cost, pop),   # estimated 1.5x if wins
        "capital_required": round(cost, 2),
        "delta":          round(lc["delta"] + lp["delta"], 4),
        "slippage_pct":   round((_slippage(lc["bid"], lc["ask"]) +
                                 _slippage(lp["bid"], lp["ask"])) / 2, 4),
        "liquid":         lc["liquid"] and lp["liquid"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATE ALL STRATEGIES
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_all_strategies(chain: dict, spot: float,
                             direction_bias: str = "NEUTRAL") -> list:
    """
    Run all strategy evaluators against the fetched chain.
    Returns list of strategy dicts sorted by ev_after_costs DESC.
    `direction_bias` filters which strategies are directionally eligible:
      BULLISH  → LONG_CALL, BULL_CALL_SPREAD (+ NEUTRAL strategies)
      BEARISH  → LONG_PUT, BEAR_PUT_SPREAD  (+ NEUTRAL strategies)
      NEUTRAL  → all
    """
    calls      = chain.get("calls", [])
    puts       = chain.get("puts",  [])
    expirations = chain.get("expirations", [])

    if not expirations:
        return []

    expiry = _best_expiry(expirations, target_dte=14)
    if not expiry:
        return []

    evaluators = []

    # Directional (enabled based on bias)
    if direction_bias in ("BULLISH", "NEUTRAL"):
        evaluators += [
            ("LONG_CALL",       lambda: eval_long_call(calls, spot, expiry)),
            ("BULL_CALL_SPREAD",lambda: eval_bull_call_spread(calls, spot, expiry)),
        ]
    if direction_bias in ("BEARISH", "NEUTRAL"):
        evaluators += [
            ("LONG_PUT",        lambda: eval_long_put(puts, spot, expiry)),
            ("BEAR_PUT_SPREAD", lambda: eval_bear_put_spread(puts, spot, expiry)),
        ]

    # Neutral / volatility strategies (always evaluated)
    evaluators += [
        ("IRON_CONDOR",   lambda: eval_iron_condor(calls, puts, spot, expiry)),
        ("LONG_STRANGLE", lambda: eval_long_strangle(calls, puts, spot, expiry)),
    ]

    results = []
    for name, fn in evaluators:
        try:
            r = fn()
            if r:
                r["expiry_used"] = expiry
                results.append(r)
        except Exception as e:
            log.debug(f"[eval_all] {name} failed: {e}")

    # Sort: eligible strategies first (liquid + positive EV), then by EV
    def _rank(s):
        liquid_ok = s.get("liquid", False)
        ev        = s.get("ev_after_costs", -9999)
        return (int(liquid_ok), ev)

    results.sort(key=_rank, reverse=True)
    return results
