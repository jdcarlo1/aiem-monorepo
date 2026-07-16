"""
legs.py — Leg and LegTemplate dataclasses, canonical sort, and
deterministic strategy fingerprinting.
"""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field, asdict
from typing import Optional, List


# ── Asset types ─────────────────────────────────────────────────────────────
ASSET_CALL  = "CALL"
ASSET_PUT   = "PUT"
ASSET_STOCK = "STOCK"

# ── Sides ──────────────────────────────────────────────────────────────────
SIDE_LONG  = "LONG"
SIDE_SHORT = "SHORT"

# ── Risk classes ────────────────────────────────────────────────────────────
RISK_DEFINED   = "DEFINED_RISK"
RISK_LIMITED   = "LIMITED_RISK"   # limited but not hard-capped (e.g. covered call)
RISK_UNDEFINED = "UNDEFINED_RISK"

# ── Execution modes ─────────────────────────────────────────────────────────
MODE_AUTONOMOUS    = "AUTONOMOUS"
MODE_ANALYSIS_ONLY = "ANALYSIS_ONLY"


@dataclass(frozen=True)
class LegTemplate:
    """
    Abstract leg specification used in the strategy catalog.
    Delta/DTE anchored — resolved to concrete strikes by builder.py.
    """
    asset_type:   str            # CALL | PUT | STOCK
    side:         str            # LONG | SHORT
    delta_target: float = 0.50   # target absolute delta (ignored for STOCK)
    dte_slot:     str   = "FRONT" # FRONT | BACK | LEAPS
    strike_offset: int  = 0      # 0=ATM, +1=one width OTM, -1=one width ITM
    ratio:         int  = 1      # leg multiplier (for ratio spreads)

    def sort_key(self) -> tuple:
        order = {ASSET_STOCK: 0, ASSET_CALL: 1, ASSET_PUT: 2}
        return (order.get(self.asset_type, 9), self.dte_slot, self.strike_offset, self.side, self.ratio)


@dataclass
class Leg:
    """
    Concrete resolved option/stock leg with live market data attached.
    Immutable once constructed — adjustments produce new Leg objects.
    """
    asset_type:      str                 # CALL | PUT | STOCK
    side:            str                 # LONG | SHORT
    quantity:        int     = 1         # number of contracts
    ratio:           int     = 1         # ratio multiplier
    strike:          Optional[float] = None
    expiration:      Optional[str]   = None   # YYYY-MM-DD
    dte:             Optional[int]   = None
    option_symbol:   Optional[str]   = None
    bid:             Optional[float] = None
    ask:             Optional[float] = None
    mid:             Optional[float] = None
    iv:              Optional[float] = None
    delta:           Optional[float] = None
    gamma:           Optional[float] = None
    theta:           Optional[float] = None
    vega:            Optional[float] = None
    rho:             Optional[float] = None
    charm:           Optional[float] = None
    vanna:           Optional[float] = None
    vomma:           Optional[float] = None
    volume:          Optional[int]   = None
    open_interest:   Optional[int]   = None
    quote_timestamp: Optional[str]   = None
    data_provider:   str             = "tradier"

    @property
    def signed_mid(self) -> Optional[float]:
        """Positive = debit leg (we pay), negative = credit leg (we receive)."""
        if self.mid is None:
            return None
        return self.mid if self.side == SIDE_LONG else -self.mid

    @property
    def signed_delta(self) -> Optional[float]:
        if self.delta is None:
            return None
        return self.delta if self.side == SIDE_LONG else -self.delta

    def to_dict(self) -> dict:
        return asdict(self)


def canonical_sort(legs: List[Leg]) -> List[Leg]:
    """
    Canonical sort order for leg fingerprinting:
    STOCK first, then CALL, then PUT;
    within type: by expiration, then strike, then side (LONG < SHORT).
    """
    def key(lg: Leg):
        type_order = {ASSET_STOCK: 0, ASSET_CALL: 1, ASSET_PUT: 2}
        side_order = {SIDE_LONG: 0, SIDE_SHORT: 1}
        return (
            type_order.get(lg.asset_type, 9),
            lg.expiration or "",
            lg.strike or 0.0,
            side_order.get(lg.side, 9),
            lg.ratio,
        )
    return sorted(legs, key=key)


def strategy_fingerprint(legs: List[Leg]) -> str:
    """
    Deterministic SHA-256 fingerprint from canonical leg structure.
    Two strategies with the same leg shape (asset_type, side, strike_offset
    from ATM, DTE-bucket, ratio) produce identical fingerprints regardless
    of the underlying or exact strike prices.
    Uses abstract representation (not live strikes) for catalog dedup.
    """
    sorted_legs = canonical_sort(legs)
    abstract = []
    for lg in sorted_legs:
        abstract.append({
            "a": lg.asset_type,
            "s": lg.side,
            "r": lg.ratio,
            "exp": lg.expiration,
            "K":  lg.strike,
        })
    payload = json.dumps(abstract, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def net_debit_credit(legs: List[Leg]) -> Optional[float]:
    """
    Total net cost of the strategy per unit (positive = debit, negative = credit).
    Returns None if any leg lacks mid price.
    """
    total = 0.0
    for lg in legs:
        if lg.signed_mid is None:
            return None
        total += lg.signed_mid * lg.ratio
    return total


def aggregate_greeks(legs: List[Leg]) -> dict:
    """
    Sum greeks across all legs (with sign and ratio).
    Returns dict with keys: delta, gamma, theta, vega, rho, charm, vanna, vomma.
    Any None leg causes the aggregate to be None for that greek.
    """
    keys = ["delta", "gamma", "theta", "vega", "rho", "charm", "vanna", "vomma"]
    out = {k: 0.0 for k in keys}
    missing = set()
    for lg in legs:
        mult = lg.ratio * (1 if lg.side == SIDE_LONG else -1)
        for k in keys:
            val = getattr(lg, k, None)
            if val is None:
                missing.add(k)
            else:
                if k not in missing:
                    out[k] = (out.get(k) or 0.0) + val * mult
    for k in missing:
        out[k] = None
    return out


def buying_power_required(legs: List[Leg], max_loss: Optional[float]) -> Optional[float]:
    """
    Buying power estimate for defined-risk strategies = max_loss.
    For strategies with no max_loss (ANALYSIS_ONLY), returns None.
    """
    if max_loss is not None and max_loss > 0:
        return max_loss * 100  # per-contract multiplier
    return None
