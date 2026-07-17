"""
eligibility.py — Hard gate eligibility checks for strategy evaluation.
All checks return (passed: bool, reasons: list[str]).
Final check returns (eligible: bool, rejection_reasons: list[str]).
"""
from __future__ import annotations
from typing import List, Optional, Tuple
from .legs import Leg, SIDE_SHORT, ASSET_CALL, ASSET_PUT, ASSET_STOCK, MODE_ANALYSIS_ONLY
from .config import (
    MIN_DTE, MAX_BID_ASK_WIDTH, MIN_OPEN_INTEREST, MIN_VOLUME,
    MIN_IV, MAX_IV, MIN_PoP, MAX_SPREAD_PER_FILL,
    MAX_CAPITAL_PER_TRADE, MAX_CAPITAL_AT_RISK_PCT, PORTFOLIO_CAPITAL,
)

_OPTION_TYPES = {"CALL", "PUT"}


def _option_legs(legs: List[Leg]) -> List[Leg]:
    return [lg for lg in legs if lg.asset_type in _OPTION_TYPES]


def check_dte(legs: List[Leg], min_dte: int = MIN_DTE) -> Tuple[bool, List[str]]:
    """All option legs must have DTE >= min_dte."""
    reasons = []
    for lg in _option_legs(legs):
        if lg.dte is not None and lg.dte < min_dte:
            reasons.append(f"DTE too low: {lg.dte} < {min_dte} for {lg.option_symbol or lg.strike}")
    return len(reasons) == 0, reasons


def check_quotes_present(legs: List[Leg]) -> Tuple[bool, List[str]]:
    """All option legs must have non-zero bid, ask, and mid."""
    reasons = []
    for lg in _option_legs(legs):
        if lg.bid is None or lg.ask is None or lg.mid is None:
            reasons.append(f"Missing quote for {lg.option_symbol or lg.strike}")
        elif lg.bid >= lg.ask:
            reasons.append(f"Crossed quote: bid={lg.bid} >= ask={lg.ask} for {lg.option_symbol or lg.strike}")
        elif lg.mid <= 0:
            reasons.append(f"Zero mid price for {lg.option_symbol or lg.strike}")
    return len(reasons) == 0, reasons


def check_bid_ask_width(legs: List[Leg], max_frac: float = MAX_BID_ASK_WIDTH) -> Tuple[bool, List[str]]:
    """Per-leg bid-ask spread must be <= max_frac of mid."""
    reasons = []
    for lg in _option_legs(legs):
        if lg.bid is None or lg.ask is None or lg.mid is None or lg.mid <= 0:
            continue
        frac = (lg.ask - lg.bid) / lg.mid
        if frac > max_frac:
            reasons.append(
                f"Bid-ask too wide: {frac:.1%} > {max_frac:.1%} for "
                f"{lg.option_symbol or lg.strike}"
            )
    return len(reasons) == 0, reasons


def check_open_interest(legs: List[Leg], min_oi: int = MIN_OPEN_INTEREST) -> Tuple[bool, List[str]]:
    """All option legs must have open_interest >= min_oi."""
    reasons = []
    for lg in _option_legs(legs):
        oi = lg.open_interest or 0
        if oi < min_oi:
            reasons.append(
                f"Low OI: {oi} < {min_oi} for {lg.option_symbol or lg.strike}"
            )
    return len(reasons) == 0, reasons


def check_volume(legs: List[Leg], min_vol: int = MIN_VOLUME) -> Tuple[bool, List[str]]:
    """All option legs must have today's volume >= min_vol."""
    reasons = []
    for lg in _option_legs(legs):
        v = lg.volume or 0
        if v < min_vol:
            reasons.append(
                f"Low volume: {v} < {min_vol} for {lg.option_symbol or lg.strike}"
            )
    return len(reasons) == 0, reasons


def check_iv_range(legs: List[Leg], min_iv: float = MIN_IV, max_iv: float = MAX_IV) -> Tuple[bool, List[str]]:
    """IV must be within reliable range for each option leg."""
    reasons = []
    for lg in _option_legs(legs):
        iv = lg.iv
        if iv is None:
            reasons.append(f"No IV for {lg.option_symbol or lg.strike}")
        elif iv < min_iv:
            reasons.append(f"IV too low: {iv:.1%} for {lg.option_symbol or lg.strike}")
        elif iv > max_iv:
            reasons.append(f"IV too high: {iv:.1%} (likely data error) for {lg.option_symbol or lg.strike}")
    return len(reasons) == 0, reasons


def check_greeks_present(legs: List[Leg]) -> Tuple[bool, List[str]]:
    """All option legs must have delta (minimum for risk calculation)."""
    reasons = []
    for lg in _option_legs(legs):
        if lg.delta is None:
            reasons.append(f"Missing delta for {lg.option_symbol or lg.strike}")
    return len(reasons) == 0, reasons


def check_max_loss_defined(
    max_loss: Optional[float],
    execution_mode: str,
) -> Tuple[bool, List[str]]:
    """
    For AUTONOMOUS strategies, max_loss must be finite and defined.
    For ANALYSIS_ONLY, undefined max_loss is allowed.
    """
    if execution_mode == MODE_ANALYSIS_ONLY:
        return True, []
    if max_loss is None:
        return False, ["Undefined max loss; strategy is ANALYSIS_ONLY only"]
    if max_loss <= 0:
        return False, ["Max loss is zero or negative — check payoff calculation"]
    return True, []


def check_capital_limits(
    max_loss: Optional[float],
    max_capital: float = MAX_CAPITAL_PER_TRADE,
) -> Tuple[bool, List[str]]:
    """Buying power per trade must not exceed the configured cap."""
    if max_loss is None:
        return True, []  # Handled by check_max_loss_defined
    bp = max_loss * 100  # per-contract dollar value
    if bp > max_capital:
        return False, [
            f"Buying power ${bp:,.0f} exceeds max ${max_capital:,.0f}"
        ]
    return True, []


def check_pop_threshold(pop: Optional[float]) -> Tuple[bool, List[str]]:
    """Probability of profit must meet minimum."""
    if pop is None:
        return False, ["PoP could not be calculated"]
    if pop < MIN_PoP:
        return False, [f"PoP {pop:.1%} < minimum {MIN_PoP:.1%}"]
    return True, []


def check_assignment_risk(legs: List[Leg]) -> Tuple[bool, List[str]]:
    """
    Flag short ITM options with DTE <= 3 as assignment risk.
    These are warnings, not hard blocks (returned as low-severity notes).
    """
    warnings = []
    for lg in _option_legs(legs):
        if lg.side == SIDE_SHORT and lg.delta is not None and lg.dte is not None:
            abs_delta = abs(lg.delta)
            if abs_delta > 0.70 and lg.dte <= 3:
                warnings.append(
                    f"Assignment risk: short ITM option delta={abs_delta:.2f} DTE={lg.dte}"
                )
    return True, warnings  # Warnings only — don't block


def check_strategy_eligible(
    legs: List[Leg],
    execution_mode: str,
    max_loss: Optional[float],
    pop: Optional[float],
    ev_after_costs: Optional[float],
) -> Tuple[bool, List[str]]:
    """
    Run all hard gate checks in sequence.
    Returns (eligible, [rejection_reasons]).
    """
    all_reasons: List[str] = []

    checks = [
        check_quotes_present(legs),
        check_dte(legs),
        check_bid_ask_width(legs),
        check_open_interest(legs),
        check_volume(legs),
        check_iv_range(legs),
        check_greeks_present(legs),
        check_max_loss_defined(max_loss, execution_mode),
        check_capital_limits(max_loss),
    ]

    # Only enforce PoP/EV for autonomous strategies
    if execution_mode != MODE_ANALYSIS_ONLY:
        if pop is not None:
            passed, reasons = check_pop_threshold(pop)
            if not passed:
                all_reasons.extend(reasons)
        if ev_after_costs is not None and ev_after_costs < -0.05:
            all_reasons.append(
                f"EV/risk {ev_after_costs:.4f} < -0.05 threshold"
            )

    for passed, reasons in checks:
        if not passed:
            all_reasons.extend(reasons)

    return len(all_reasons) == 0, all_reasons


def assignment_risk_label(legs: List[Leg]) -> str:
    _, warnings = check_assignment_risk(legs)
    return "HIGH" if warnings else "LOW"


def pin_risk_label(legs: List[Leg], spot: float) -> str:
    """Estimate pin risk: short options near spot at expiry."""
    for lg in _option_legs(legs):
        if lg.side == SIDE_SHORT and lg.strike and lg.dte is not None:
            pct_from_spot = abs(lg.strike - spot) / spot
            if pct_from_spot < 0.01 and lg.dte <= 2:
                return "HIGH"
    return "LOW"


def check_quote_age(
    legs: List[Leg],
    max_age_seconds: int = 300,
) -> Tuple[bool, List[str]]:
    """
    Reject stale options chains.

    Parses quote_timestamp (ISO 8601 from chain_data._normalize_option) and
    compares to UTC now.  Rejects if older than max_age_seconds (default: 300s).

    Missing timestamps are treated as stale (fail-closed).
    """
    from datetime import datetime, timezone
    reasons: List[str] = []
    now = datetime.now(timezone.utc)
    for lg in _option_legs(legs):
        ts = lg.quote_timestamp
        label = lg.option_symbol or str(lg.strike)
        if ts is None:
            reasons.append(f"No quote timestamp for {label} — treating as stale")
            continue
        parsed = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(str(ts)[:19], fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if parsed is None:
            reasons.append(f"Unparseable timestamp '{ts}' for {label}")
            continue
        age = (now - parsed).total_seconds()
        if age > max_age_seconds:
            reasons.append(
                f"Stale chain: quote is {age:.0f}s old (limit {max_age_seconds}s) "
                f"for {label}"
            )
    return len(reasons) == 0, reasons


def check_impossible_pricing(
    legs: List[Leg],
    spot: float,
) -> Tuple[bool, List[str]]:
    """
    Detect impossible option prices.  Rejects on any of:

    1. Negative bid or ask           — option can never be worth < 0
    2. Crossed market (bid > ask)    — already in check_quotes_present; caught here too
    3. Call ask > spot × 1.05       — call can't cost more than underlying
    4. Put ask > strike × 1.05      — put can't cost more than its max payoff (=strike)
    5. Negative mid price            — impossible, catches malformed chain data
    6. Call mid < intrinsic floor   — call mid < max(0, spot - strike) - 0.02 buffer

    The 5% / $0.02 buffers absorb data latency and rounding; they are not
    an excuse to accept wildly wrong prices.
    """
    reasons: List[str] = []
    for lg in _option_legs(legs):
        bid    = lg.bid
        ask    = lg.ask
        mid    = lg.mid
        strike = lg.strike or 0.0
        label  = lg.option_symbol or str(strike)

        if bid is not None and bid < 0:
            reasons.append(f"Negative bid {bid:.4f} for {label}")
        if ask is not None and ask < 0:
            reasons.append(f"Negative ask {ask:.4f} for {label}")
        if mid is not None and mid < 0:
            reasons.append(f"Negative mid {mid:.4f} for {label}")

        if bid is not None and ask is not None and bid > ask:
            reasons.append(
                f"Crossed market: bid={bid:.4f} > ask={ask:.4f} for {label}"
            )

        if lg.asset_type == ASSET_CALL:
            if ask is not None and spot > 0 and ask > spot * 1.05:
                reasons.append(
                    f"Impossible call price: ask={ask:.2f} > spot×1.05={spot*1.05:.2f} "
                    f"for {label}"
                )
            # Intrinsic floor for calls: mid < max(0, spot - strike) - 0.02
            if mid is not None and spot > 0:
                intrinsic = max(0.0, spot - strike)
                if mid < intrinsic - 0.02:
                    reasons.append(
                        f"Call mid={mid:.4f} below intrinsic floor {intrinsic:.4f} "
                        f"for {label}"
                    )

        if lg.asset_type == ASSET_PUT:
            if ask is not None and strike > 0 and ask > strike * 1.05:
                reasons.append(
                    f"Impossible put price: ask={ask:.2f} > strike×1.05={strike*1.05:.2f} "
                    f"for {label}"
                )
            # Intrinsic floor for puts: mid < max(0, strike - spot) - 0.02
            if mid is not None and spot > 0:
                intrinsic = max(0.0, strike - spot)
                if mid < intrinsic - 0.02:
                    reasons.append(
                        f"Put mid={mid:.4f} below intrinsic floor {intrinsic:.4f} "
                        f"for {label}"
                    )

    return len(reasons) == 0, reasons
