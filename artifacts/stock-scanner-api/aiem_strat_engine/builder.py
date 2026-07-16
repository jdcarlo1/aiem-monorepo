"""
builder.py — Generic multi-leg strategy constructor.

Resolves StrategySpec leg templates to concrete Leg objects using live chain data.
Handles dedup (fingerprint matching), named-strategy classification,
CUSTOM_MULTI_LEG fallback, and combinatorial explosion prevention.
"""
from __future__ import annotations
import hashlib, json
from typing import List, Optional, Dict, Any, Tuple
from datetime import date, datetime

from .legs import Leg, strategy_fingerprint, canonical_sort, SIDE_LONG, SIDE_SHORT
from .catalog import CATALOG, CATALOG_BY_NAME, StrategySpec, FAMILY_CONDOR
from .chain_data import (
    get_chain, get_expirations, get_spot, get_dte,
    find_option_by_delta, get_strikes_near_atm,
    select_expirations_for_dte_slots, get_atm_iv, get_skew,
)
from .config import MAX_EVALUATIONS_PER_RUN, DTE_SLOTS, DELTA_ANCHORS


class BuildError(Exception):
    """Raised when a leg template cannot be resolved to a concrete leg."""


def _resolve_expiry(
    expiry_map: Dict[str, Optional[str]],
    slot: str,
) -> Optional[str]:
    """Map DTE slot name to actual expiry date string."""
    mapping = {
        "FRONT":     expiry_map.get("WEEKLY") or expiry_map.get("BIWEEKLY") or expiry_map.get("MONTHLY"),
        "BACK":      expiry_map.get("BIMONTHLY") or expiry_map.get("QUARTERLY"),
        "MONTHLY":   expiry_map.get("MONTHLY") or expiry_map.get("BIWEEKLY"),
        "QUARTERLY": expiry_map.get("QUARTERLY") or expiry_map.get("BIMONTHLY"),
        "LEAPS":     expiry_map.get("LEAPS"),
        "BIWEEKLY":  expiry_map.get("BIWEEKLY"),
        "WEEKLY":    expiry_map.get("WEEKLY"),
        "ZERO_DTE":  expiry_map.get("ZERO_DTE"),
    }
    return mapping.get(slot, expiry_map.get(slot))


def _build_leg_from_template(
    template: dict,
    chain_by_expiry: Dict[str, List[dict]],
    expiry_map: Dict[str, Optional[str]],
    spot: float,
    strike_width: Optional[float] = None,
) -> Leg:
    """
    Resolve one LegTemplate dict to a concrete Leg with live market data.

    strike_width: dollar width between adjacent strikes in the chain.
    """
    asset_type  = template["asset_type"]
    side        = template["side"]
    ratio       = template.get("ratio", 1)
    delta_target= template.get("delta_target", 0.50)
    dte_slot    = template.get("dte_slot", "FRONT")
    offset      = template.get("strike_offset", 0)

    if asset_type == "STOCK":
        return Leg(
            asset_type="STOCK",
            side=side,
            ratio=ratio,
            strike=None,
            expiration=None,
            dte=None,
            mid=spot,
            bid=spot * 0.9999,
            ask=spot * 1.0001,
            delta=1.0 if side == SIDE_LONG else -1.0,
            gamma=0.0,
            theta=0.0,
            vega=0.0,
        )

    expiry = _resolve_expiry(expiry_map, dte_slot)
    if not expiry:
        raise BuildError(f"No expiry available for DTE slot '{dte_slot}'")

    chain = chain_by_expiry.get(expiry, [])
    if not chain:
        raise BuildError(f"Empty chain for {expiry}")

    call_or_put = "C" if asset_type == "CALL" else "P"
    base_opt = find_option_by_delta(chain, call_or_put, delta_target)
    if not base_opt:
        raise BuildError(f"No {asset_type} near delta={delta_target} for {expiry}")

    # Apply strike offset if specified
    if offset != 0 and strike_width:
        target_strike = base_opt["strike"] + offset * strike_width
        # Find nearest in chain
        candidates = [o for o in chain if o.get("call_or_put") == call_or_put]
        if candidates:
            base_opt = min(candidates, key=lambda o: abs((o.get("strike") or 0) - target_strike))

    dte = get_dte(expiry)
    return Leg(
        asset_type   = asset_type,
        side         = side,
        ratio        = ratio,
        strike       = base_opt.get("strike"),
        expiration   = expiry,
        dte          = dte,
        option_symbol= base_opt.get("option_symbol"),
        bid          = base_opt.get("bid"),
        ask          = base_opt.get("ask"),
        mid          = base_opt.get("mid"),
        iv           = base_opt.get("iv"),
        delta        = base_opt.get("delta"),
        gamma        = base_opt.get("gamma"),
        theta        = base_opt.get("theta"),
        vega         = base_opt.get("vega"),
        rho          = base_opt.get("rho"),
        volume       = base_opt.get("volume"),
        open_interest= base_opt.get("open_interest"),
        quote_timestamp=base_opt.get("quote_timestamp"),
    )


def _estimate_strike_width(chain: List[dict]) -> float:
    """Estimate typical width between adjacent strikes in a chain."""
    strikes = sorted({o.get("strike") for o in chain if o.get("strike")})
    if len(strikes) < 2:
        return 5.0
    diffs = [strikes[i+1] - strikes[i] for i in range(min(5, len(strikes)-1))]
    return sum(diffs) / len(diffs)


def build_strategy(
    spec: StrategySpec,
    ticker: str,
    chain_by_expiry: Dict[str, List[dict]],
    expiry_map: Dict[str, Optional[str]],
    spot: float,
) -> Optional[List[Leg]]:
    """
    Attempt to build concrete legs from a StrategySpec.
    Returns list of Leg objects or None if construction fails.
    """
    # Estimate strike width from the front-month chain
    front_expiry = _resolve_expiry(expiry_map, "FRONT")
    front_chain  = chain_by_expiry.get(front_expiry or "", [])
    strike_width = _estimate_strike_width(front_chain) if front_chain else 5.0

    legs = []
    for tmpl in spec.leg_templates:
        try:
            leg = _build_leg_from_template(
                tmpl, chain_by_expiry, expiry_map, spot, strike_width
            )
            legs.append(leg)
        except BuildError:
            return None
        except Exception:
            return None

    return canonical_sort(legs) if legs else None


def match_to_catalog(legs: List[Leg]) -> Optional[StrategySpec]:
    """
    Try to match a concrete set of legs to a named StrategySpec.
    Uses structural heuristics: leg count, asset types, sides, DTE relationship.
    """
    n = len(legs)
    cp_sides  = [(lg.asset_type, lg.side) for lg in legs]
    has_stock = any(lg.asset_type == "STOCK" for lg in legs)
    exps      = list({lg.expiration for lg in legs if lg.expiration})
    n_exps    = len(exps)

    for spec in CATALOG:
        if spec.min_legs <= n <= spec.max_legs:
            if spec.has_stock == has_stock:
                # Calendar check: multiple expirations required
                is_cal = ("CALENDAR" in spec.family or "DIAGONAL" in spec.family)
                if is_cal and n_exps < 2:
                    continue
                # Rough match on template leg structure
                tmpl_cp_sides = [(t["asset_type"], t["side"]) for t in spec.leg_templates[:n]]
                if sorted(tmpl_cp_sides) == sorted(cp_sides[:n]):
                    return spec
    return None


def classify_legs(legs: List[Leg]) -> Tuple[str, str]:
    """
    Classify a set of concrete legs into (strategy_name, family).
    Returns ("CUSTOM_MULTI_LEG", "CUSTOM") if no match found.
    """
    matched = match_to_catalog(legs)
    if matched:
        return matched.name, matched.family
    return "CUSTOM_MULTI_LEG", "CUSTOM"


def build_all_for_ticker(
    ticker: str,
    thesis: str,
    market_regime: str = "NEUTRAL",
    vol_regime: str = "NEUTRAL",
    event_context: Optional[str] = None,
) -> List[Tuple[StrategySpec, List[Leg]]]:
    """
    Build all eligible strategies for a given ticker and thesis.
    Enforces MAX_EVALUATIONS_PER_RUN cap to prevent combinatorial explosion.

    Returns list of (StrategySpec, legs) tuples — only successfully built ones.
    """
    spot = get_spot(ticker)
    if not spot:
        return []

    expirations = get_expirations(ticker)
    if not expirations:
        return []

    # Select best expiry for each DTE slot
    expiry_map = select_expirations_for_dte_slots(expirations)

    # Fetch chains for needed expirations (lazy — only what's needed)
    needed_expiries = {e for e in expiry_map.values() if e}
    chain_by_expiry: Dict[str, List[dict]] = {}
    for exp in needed_expiries:
        chain_by_expiry[exp] = get_chain(ticker, exp)

    # Filter catalog by thesis direction + vol regime + dte range
    eligible_specs = _filter_specs_by_context(
        CATALOG, thesis, market_regime, vol_regime, event_context, expiry_map
    )

    results = []
    for spec in eligible_specs[:MAX_EVALUATIONS_PER_RUN]:
        legs = build_strategy(spec, ticker, chain_by_expiry, expiry_map, spot)
        if legs is not None:
            results.append((spec, legs))

    return results


def _filter_specs_by_context(
    specs: List[StrategySpec],
    thesis: str,
    market_regime: str,
    vol_regime: str,
    event_context: Optional[str],
    expiry_map: Dict[str, Optional[str]],
) -> List[StrategySpec]:
    """
    Narrow the catalog to strategies relevant for the current context.
    Priority: exact direction match > ANY > opposite direction excluded.
    """
    thesis_upper = thesis.upper()
    is_event = bool(event_context)

    filtered = []
    for spec in specs:
        # Direction filter
        if spec.direction not in ("ANY", "NEUTRAL", thesis_upper):
            # Allow NEUTRAL strategies for any thesis
            if not (spec.direction == "NEUTRAL"):
                continue

        # Vol regime filter (soft — don't hard-exclude, just deprioritize)
        # (scoring will handle regime mismatch penalty)

        # Event: prefer event family if event context present
        if is_event and spec.family == "EVENT_EXPIRATION":
            filtered.insert(0, spec)  # prioritize
        else:
            filtered.append(spec)

        # DTE availability check: skip if required slot has no expiry
        has_leaps = any(t.get("dte_slot") == "LEAPS" for t in spec.leg_templates)
        if has_leaps and not expiry_map.get("LEAPS"):
            continue
        has_back = any(t.get("dte_slot") == "BACK" for t in spec.leg_templates)
        if has_back and not (expiry_map.get("BIMONTHLY") or expiry_map.get("QUARTERLY")):
            continue

    # Deduplicate (can happen if event spec inserted twice)
    seen = set()
    deduped = []
    for s in filtered:
        if s.name not in seen:
            seen.add(s.name)
            deduped.append(s)
    return deduped


def build_custom_multi_leg(
    ticker: str,
    leg_specs: List[Dict[str, Any]],
) -> Optional[List[Leg]]:
    """
    Build a custom (non-catalog) multi-leg strategy from explicit leg specs.

    leg_specs: list of dicts with keys:
        asset_type: CALL | PUT | STOCK
        side: LONG | SHORT
        strike: float (exact)
        expiration: YYYY-MM-DD
        ratio: int (default 1)

    Returns canonical-sorted Leg list or None if any leg cannot be priced.
    """
    if len(leg_specs) < 1 or len(leg_specs) > 8:
        return None

    spot = get_spot(ticker)
    if not spot:
        return None

    needed_expiries = {s["expiration"] for s in leg_specs if s.get("expiration") and s["asset_type"] != "STOCK"}
    chain_by_expiry: Dict[str, List[dict]] = {e: get_chain(ticker, e) for e in needed_expiries}

    legs = []
    for ls in leg_specs:
        if ls["asset_type"] == "STOCK":
            legs.append(Leg(
                asset_type="STOCK", side=ls["side"],
                ratio=ls.get("ratio", 1), mid=spot,
                bid=spot*0.9999, ask=spot*1.0001,
                delta=1.0 if ls["side"] == SIDE_LONG else -1.0,
            ))
            continue

        exp = ls.get("expiration")
        chain = chain_by_expiry.get(exp, [])
        cp = "C" if ls["asset_type"] == "CALL" else "P"
        target_k = float(ls.get("strike", spot))
        candidates = [o for o in chain if o.get("call_or_put") == cp]
        if not candidates:
            return None
        opt = min(candidates, key=lambda o: abs((o.get("strike") or 0) - target_k))
        dte = get_dte(exp) if exp else None
        legs.append(Leg(
            asset_type=ls["asset_type"],
            side=ls["side"],
            ratio=ls.get("ratio", 1),
            strike=opt.get("strike"),
            expiration=exp,
            dte=dte,
            option_symbol=opt.get("option_symbol"),
            bid=opt.get("bid"),
            ask=opt.get("ask"),
            mid=opt.get("mid"),
            iv=opt.get("iv"),
            delta=opt.get("delta"),
            gamma=opt.get("gamma"),
            theta=opt.get("theta"),
            vega=opt.get("vega"),
            volume=opt.get("volume"),
            open_interest=opt.get("open_interest"),
        ))

    if not legs:
        return None

    # Dedup check: find if this matches an existing catalog entry
    sorted_legs = canonical_sort(legs)
    return sorted_legs


def fingerprint_for_ticker(ticker: str, legs: List[Leg]) -> str:
    """Deterministic fingerprint combining ticker + leg structure."""
    leg_fp = strategy_fingerprint(legs)
    combined = f"{ticker.upper()}:{leg_fp}"
    return hashlib.sha256(combined.encode()).hexdigest()[:20]
