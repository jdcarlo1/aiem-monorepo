"""
risk_classifier.py — Structural risk classification for the Advanced
Strategy Engine.

Assigns each strategy one of three execution modes:
  AUTONOMOUS    — defined-risk, within capital gates → eligible for paper trading
  ANALYSIS_ONLY — can be modeled/priced, but not executed (unknown max loss,
                  excessive risk, or missing data)
  REJECTED      — structure is too dangerous to evaluate at all

Structures that produce REJECTED:
  Naked Call, Naked Put, Naked Straddle, Naked Strangle,
  Naked Ratio (net uncovered short), Unlimited-Loss Synthetic,
  Missing Buying Power (key data absent)

Structures that produce ANALYSIS_ONLY:
  Unknown Max Loss (payoff engine returned None),
  Excessive Risk (exceeds capital caps)

All others with finite, positive max_loss → AUTONOMOUS.
"""
from __future__ import annotations
from typing import List, Optional, Tuple, Dict, Any

from .legs import (
    Leg, ASSET_CALL, ASSET_PUT, ASSET_STOCK,
    SIDE_LONG, SIDE_SHORT,
    RISK_DEFINED, RISK_LIMITED, RISK_UNDEFINED,
    MODE_AUTONOMOUS, MODE_ANALYSIS_ONLY,
)
from .config import MAX_CAPITAL_PER_TRADE, MAX_CAPITAL_AT_RISK_PCT, PORTFOLIO_CAPITAL

# ── Execution mode constants ──────────────────────────────────────────────────
MODE_REJECTED = "REJECTED"

# ── Risk flag names ───────────────────────────────────────────────────────────
FLAG_NAKED_CALL               = "NAKED_CALL"
FLAG_NAKED_PUT                = "NAKED_PUT"
FLAG_NAKED_STRADDLE           = "NAKED_STRADDLE"
FLAG_NAKED_STRANGLE           = "NAKED_STRANGLE"
FLAG_NAKED_RATIO              = "NAKED_RATIO"
FLAG_UNLIMITED_LOSS_SYNTHETIC = "UNLIMITED_LOSS_SYNTHETIC"
FLAG_MISSING_BUYING_POWER     = "MISSING_BUYING_POWER"
FLAG_UNKNOWN_MAX_LOSS         = "UNKNOWN_MAX_LOSS"
FLAG_EXCESSIVE_RISK           = "EXCESSIVE_RISK"

# Flags that alone force REJECTED
_REJECTED_FLAGS = {
    FLAG_NAKED_CALL, FLAG_NAKED_PUT, FLAG_NAKED_STRADDLE,
    FLAG_NAKED_STRANGLE, FLAG_NAKED_RATIO,
    FLAG_UNLIMITED_LOSS_SYNTHETIC, FLAG_MISSING_BUYING_POWER,
}

# Flags that produce ANALYSIS_ONLY (when no REJECTED flag is present)
_ANALYSIS_FLAGS = {FLAG_UNKNOWN_MAX_LOSS, FLAG_EXCESSIVE_RISK}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _short_calls(legs: List[Leg]) -> List[Leg]:
    return [lg for lg in legs if lg.asset_type == ASSET_CALL and lg.side == SIDE_SHORT]

def _short_puts(legs: List[Leg]) -> List[Leg]:
    return [lg for lg in legs if lg.asset_type == ASSET_PUT  and lg.side == SIDE_SHORT]

def _long_calls(legs: List[Leg]) -> List[Leg]:
    return [lg for lg in legs if lg.asset_type == ASSET_CALL and lg.side == SIDE_LONG]

def _long_puts(legs: List[Leg]) -> List[Leg]:
    return [lg for lg in legs if lg.asset_type == ASSET_PUT  and lg.side == SIDE_LONG]

def _long_stock(legs: List[Leg]) -> List[Leg]:
    return [lg for lg in legs if lg.asset_type == ASSET_STOCK and lg.side == SIDE_LONG]

def _short_stock(legs: List[Leg]) -> List[Leg]:
    return [lg for lg in legs if lg.asset_type == ASSET_STOCK and lg.side == SIDE_SHORT]


def _call_is_covered(sc: Leg, legs: List[Leg]) -> bool:
    """
    A short call is covered when:
    - A long call exists at a lower or equal strike (same or earlier expiry), OR
    - Long stock is present (covered call).
    Expiration comparison: earlier expiry = shorter-dated = less restrictive coverage.
    """
    sc_k   = sc.strike or 0.0
    sc_exp = sc.expiration or ""
    for lc in _long_calls(legs):
        lc_k   = lc.strike or 0.0
        lc_exp = lc.expiration or ""
        if lc_k <= sc_k and (not lc_exp or not sc_exp or lc_exp >= sc_exp):
            return True
    if _long_stock(legs):
        return True
    return False


def _put_is_covered(sp: Leg, legs: List[Leg]) -> bool:
    """
    A short put is covered when:
    - A long put exists at a higher or equal strike (same or earlier expiry).
    Note: cash collateral is NOT structural coverage — only long put hedges count.
    Short stock at the same or higher strike also covers a short put.
    """
    sp_k   = sp.strike or 0.0
    sp_exp = sp.expiration or ""
    for lp in _long_puts(legs):
        lp_k   = lp.strike or 0.0
        lp_exp = lp.expiration or ""
        if lp_k >= sp_k and (not lp_exp or not sp_exp or lp_exp >= sp_exp):
            return True
    for ss in _short_stock(legs):
        return True
    return False


# ── Detection functions ───────────────────────────────────────────────────────

def is_naked_call(legs: List[Leg]) -> Tuple[bool, str]:
    """Any uncovered short call → naked call (unlimited loss)."""
    for sc in _short_calls(legs):
        if not _call_is_covered(sc, legs):
            return True, f"Uncovered short call at strike={sc.strike}"
    return False, ""


def is_naked_put(legs: List[Leg]) -> Tuple[bool, str]:
    """Any uncovered short put → naked put (loss up to strike × 100)."""
    for sp in _short_puts(legs):
        if not _put_is_covered(sp, legs):
            return True, f"Uncovered short put at strike={sp.strike}"
    return False, ""


def is_naked_straddle(legs: List[Leg]) -> Tuple[bool, str]:
    """
    Naked straddle: short call + short put at the same strike, both uncovered.
    Detected before naked_call/put so the specific flag is applied.
    """
    for sc in _short_calls(legs):
        for sp in _short_puts(legs):
            same = abs((sc.strike or 0.0) - (sp.strike or 0.0)) < 0.01
            if same and not _call_is_covered(sc, legs) and not _put_is_covered(sp, legs):
                return True, f"Naked straddle at strike={sc.strike}"
    return False, ""


def is_naked_strangle(legs: List[Leg]) -> Tuple[bool, str]:
    """
    Naked strangle: short call + short put at different strikes, both uncovered.
    """
    for sc in _short_calls(legs):
        for sp in _short_puts(legs):
            diff = abs((sc.strike or 0.0) - (sp.strike or 0.0)) >= 0.01
            if diff and not _call_is_covered(sc, legs) and not _put_is_covered(sp, legs):
                return True, (
                    f"Naked strangle: short call@{sc.strike}, short put@{sp.strike}"
                )
    return False, ""


def is_naked_ratio(legs: List[Leg]) -> Tuple[bool, str]:
    """
    Naked ratio: net uncovered short position after all hedges.

    Counts weighted ratios:
      net_short_calls = Σ ratio(short calls) − Σ ratio(long calls) − long_stock_lots
      net_short_puts  = Σ ratio(short puts)  − Σ ratio(long puts)  − short_stock_lots

    Any positive net → uncovered short exposure.
    """
    sc_weight  = sum(lg.ratio for lg in _short_calls(legs))
    lc_weight  = sum(lg.ratio for lg in _long_calls(legs))
    sp_weight  = sum(lg.ratio for lg in _short_puts(legs))
    lp_weight  = sum(lg.ratio for lg in _long_puts(legs))
    long_stk   = len(_long_stock(legs))
    short_stk  = len(_short_stock(legs))

    net_sc = sc_weight - lc_weight - long_stk
    net_sp = sp_weight - lp_weight - short_stk

    if net_sc > 0:
        return True, f"Naked ratio: net {net_sc} uncovered short call(s)"
    if net_sp > 0:
        return True, f"Naked ratio: net {net_sp} uncovered short put(s)"
    return False, ""


def is_unlimited_loss_synthetic(legs: List[Leg]) -> Tuple[bool, str]:
    """
    Detect synthetics with unlimited loss potential.

    Cases:
    1. Short stock without a long call hedge → unlimited upside loss.
    2. Synthetic short (short call + long put at same strike, same expiry)
       without long stock hedge → equivalent to short stock → unlimited upside loss.
    """
    # Case 1: actual short stock without call hedge
    if _short_stock(legs) and not _long_calls(legs):
        return True, "Short stock without call hedge — unlimited upside loss"

    # Case 2: synthetic short (short call + long put same strike/expiry)
    for sc in _short_calls(legs):
        for lp in _long_puts(legs):
            same_k   = abs((sc.strike or 0.0) - (lp.strike or 0.0)) < 0.01
            same_exp = (sc.expiration or "") == (lp.expiration or "")
            if same_k and same_exp and not _long_stock(legs):
                return True, (
                    f"Synthetic short (short call@{sc.strike} + long put@{lp.strike}) "
                    f"without long stock hedge — unlimited upside loss"
                )
    return False, ""


def has_missing_buying_power(legs: List[Leg]) -> Tuple[bool, str]:
    """
    Buying power cannot be computed when strike or mid price is absent
    for any option leg.
    """
    missing = []
    for lg in legs:
        if lg.asset_type not in (ASSET_CALL, ASSET_PUT):
            continue
        if lg.strike is None:
            missing.append(f"{lg.side} {lg.asset_type}: missing strike")
        if lg.mid is None:
            missing.append(
                f"{lg.side} {lg.asset_type}@{lg.strike}: missing mid price"
            )
    if missing:
        return True, "Missing data prevents buying-power calculation: " + "; ".join(missing)
    return False, ""


def has_unknown_max_loss(max_loss: Optional[float]) -> Tuple[bool, str]:
    """
    Max loss is unknown when the payoff engine returns None or a non-positive value.
    """
    if max_loss is None:
        return True, "Max loss = None — payoff engine could not determine worst case"
    if max_loss <= 0:
        return True, f"Max loss = {max_loss} (non-positive) — likely a calculation error"
    return False, ""


def is_excessive_risk(
    max_loss: Optional[float],
    portfolio_capital: float = PORTFOLIO_CAPITAL,
    max_capital_per_trade: float = MAX_CAPITAL_PER_TRADE,
    max_pct: float = MAX_CAPITAL_AT_RISK_PCT,
) -> Tuple[bool, str]:
    """
    Detect strategies that breach capital risk limits.

    Gate 1: buying power (max_loss × 100) > max_capital_per_trade
    Gate 2: buying power > max_pct × portfolio_capital
    """
    if max_loss is None:
        return False, ""
    bp  = max_loss * 100
    pct = bp / portfolio_capital if portfolio_capital > 0 else 1.0
    if bp > max_capital_per_trade:
        return True, (
            f"Buying power ${bp:,.0f} exceeds per-trade cap ${max_capital_per_trade:,.0f}"
        )
    if pct > max_pct:
        return True, (
            f"Risk {pct:.1%} of portfolio exceeds {max_pct:.1%} limit"
        )
    return False, ""


# ── Primary classifier ────────────────────────────────────────────────────────

def classify_strategy_risk(
    legs: List[Leg],
    max_loss: Optional[float],
    max_profit: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Full structural risk classification.

    Evaluation order (most severe first):
      1. Naked straddle / strangle (caught before naked_call/put to name correctly)
      2. Naked call / naked put
      3. Naked ratio
      4. Unlimited-loss synthetic
      5. Missing buying power
      6. Unknown max loss           → ANALYSIS_ONLY
      7. Excessive risk             → ANALYSIS_ONLY

    Returns:
        risk_class        : DEFINED_RISK / LIMITED_RISK / UNDEFINED_RISK
        execution_mode    : AUTONOMOUS / ANALYSIS_ONLY / REJECTED
        risk_flags        : list of FLAG_* constants triggered
        rejection_reasons : human-readable list of reasons
        can_paper_trade   : True only when AUTONOMOUS
        max_loss          : passed-through
        max_profit        : passed-through
    """
    flags:   List[str] = []
    reasons: List[str] = []

    def _check(fn, *args, flag):
        triggered, msg = fn(*args)
        if triggered:
            flags.append(flag)
            reasons.append(msg)

    # Naked combinations (check straddle/strangle first for correct naming)
    _check(is_naked_straddle,           legs,          flag=FLAG_NAKED_STRADDLE)
    _check(is_naked_strangle,           legs,          flag=FLAG_NAKED_STRANGLE)
    _check(is_naked_call,               legs,          flag=FLAG_NAKED_CALL)
    _check(is_naked_put,                legs,          flag=FLAG_NAKED_PUT)
    # Only add naked_ratio if not already caught by naked_call or naked_put
    if FLAG_NAKED_CALL not in flags and FLAG_NAKED_PUT not in flags:
        _check(is_naked_ratio,          legs,          flag=FLAG_NAKED_RATIO)
    _check(is_unlimited_loss_synthetic, legs,          flag=FLAG_UNLIMITED_LOSS_SYNTHETIC)
    _check(has_missing_buying_power,    legs,          flag=FLAG_MISSING_BUYING_POWER)

    # Analysis-only checks
    _check(has_unknown_max_loss,        max_loss,      flag=FLAG_UNKNOWN_MAX_LOSS)
    _check(is_excessive_risk,           max_loss,      flag=FLAG_EXCESSIVE_RISK)

    # Determine execution mode
    if any(f in _REJECTED_FLAGS for f in flags):
        execution_mode = MODE_REJECTED
        risk_class     = RISK_UNDEFINED
    elif any(f in _ANALYSIS_FLAGS for f in flags):
        execution_mode = MODE_ANALYSIS_ONLY
        risk_class     = RISK_LIMITED
    elif max_loss is not None and max_loss > 0:
        execution_mode = MODE_AUTONOMOUS
        risk_class     = RISK_DEFINED
    else:
        execution_mode = MODE_ANALYSIS_ONLY
        risk_class     = RISK_LIMITED

    return {
        "risk_class":        risk_class,
        "execution_mode":    execution_mode,
        "risk_flags":        flags,
        "rejection_reasons": reasons,
        "can_paper_trade":   execution_mode == MODE_AUTONOMOUS,
        "max_loss":          max_loss,
        "max_profit":        max_profit,
    }
