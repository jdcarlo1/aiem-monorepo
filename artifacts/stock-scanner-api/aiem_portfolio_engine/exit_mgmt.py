"""
aiem_portfolio_engine/exit_mgmt.py — S11: Portfolio Exit Management.

Evaluates existing open positions for exit, reduce, hedge, roll, or adjust actions.
Called independently of the entry gate; fires on existing positions, not candidates.

Actions:
  HOLD   — position is within normal parameters; no action required
  CLOSE  — exit the full position (max-loss approach, near expiry deep ITM, etc.)
  REDUCE — close a portion of the position (oversized, partial-profit)
  HEDGE  — add an offsetting leg to reduce net delta / directional exposure
  ROLL   — close expiring legs and reopen at later expiry
  ADJUST — modify the structure (buy back short wing, sell replacement, etc.)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from .snapshot import PortfolioSnapshot, PortfolioPosition

EXIT_HOLD   = "HOLD"
EXIT_CLOSE  = "CLOSE"
EXIT_REDUCE = "REDUCE"
EXIT_HEDGE  = "HEDGE"
EXIT_ROLL   = "ROLL"
EXIT_ADJUST = "ADJUST"

EXIT_ACTIONS = (EXIT_HOLD, EXIT_CLOSE, EXIT_REDUCE, EXIT_HEDGE, EXIT_ROLL, EXIT_ADJUST)


@dataclass
class ExitRecommendation:
    position_id:     str
    ticker:          str
    action:          str              # one of EXIT_ACTIONS
    urgency:         str              # HIGH / MEDIUM / LOW
    reasons:         List[str]        = field(default_factory=list)
    target_size:     Optional[int]    = None   # contracts to keep (REDUCE/ROLL) or 0 (CLOSE)
    roll_to_expiry:  Optional[str]    = None   # for ROLL
    hedge_ticker:    Optional[str]    = None   # for HEDGE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id":   self.position_id,
            "ticker":        self.ticker,
            "action":        self.action,
            "urgency":       self.urgency,
            "reasons":       self.reasons,
            "target_size":   self.target_size,
            "roll_to_expiry": self.roll_to_expiry,
            "hedge_ticker":  self.hedge_ticker,
        }


def evaluate_exit(
    position:    PortfolioPosition,
    current_pnl: float = 0.0,
    max_loss:    Optional[float] = None,
) -> ExitRecommendation:
    """
    Evaluate a single open position and return an exit recommendation.

    current_pnl : unrealized P&L in dollars (negative = loss).
    max_loss    : override for maximum acceptable loss; defaults to position.maximum_loss.
    """
    reasons: List[str] = []
    loss_limit = max_loss if max_loss is not None else position.maximum_loss

    # Minimum DTE across all option legs
    min_dte: Optional[int] = None
    for lg in position.legs:
        if lg.asset_type in ("CALL", "PUT") and lg.dte_at_entry is not None:
            if min_dte is None or lg.dte_at_entry < min_dte:
                min_dte = lg.dte_at_entry

    # ── Check 1: Max-loss threshold reached → CLOSE (HIGH) ────────────────────
    if loss_limit > 0 and current_pnl <= -(loss_limit * 0.90):
        reasons.append(
            f"P&L {current_pnl:.2f} at or beyond 90% of max_loss {loss_limit:.2f}"
        )
        return ExitRecommendation(
            position_id = position.paper_trade_id,
            ticker      = position.ticker,
            action      = EXIT_CLOSE,
            urgency     = "HIGH",
            reasons     = reasons,
            target_size = 0,
        )

    # ── Check 2: DTE ≤ 3 → CLOSE (assignment/pin risk, HIGH) ─────────────────
    if min_dte is not None and min_dte <= 3:
        reasons.append(
            f"Min DTE={min_dte} ≤ 3 — imminent assignment/pin risk; close immediately"
        )
        return ExitRecommendation(
            position_id = position.paper_trade_id,
            ticker      = position.ticker,
            action      = EXIT_CLOSE,
            urgency     = "HIGH",
            reasons     = reasons,
            target_size = 0,
        )

    # ── Check 3: DTE ≤ 7 → ROLL (HIGH) ───────────────────────────────────────
    if min_dte is not None and min_dte <= 7:
        reasons.append(
            f"Min DTE={min_dte} ≤ 7 — roll before assignment / theta cliff"
        )
        return ExitRecommendation(
            position_id    = position.paper_trade_id,
            ticker         = position.ticker,
            action         = EXIT_ROLL,
            urgency        = "HIGH",
            reasons        = reasons,
            roll_to_expiry = "next_monthly",
            target_size    = position.n_contracts,
        )

    # ── Check 4: DTE ≤ 21 and profitable → ADJUST (partial profit, MEDIUM) ───
    if min_dte is not None and min_dte <= 21 and loss_limit > 0 and current_pnl >= loss_limit * 0.50:
        reasons.append(
            f"DTE={min_dte}, P&L={current_pnl:.2f} ≥ 50% of max_loss={loss_limit:.2f}; "
            f"adjust to lock in partial profit"
        )
        return ExitRecommendation(
            position_id = position.paper_trade_id,
            ticker      = position.ticker,
            action      = EXIT_ADJUST,
            urgency     = "MEDIUM",
            reasons     = reasons,
        )

    # ── Check 5: Oversized position (> 3 contracts) → REDUCE (LOW) ───────────
    if position.n_contracts > 3:
        target = max(1, position.n_contracts // 2)
        reasons.append(
            f"n_contracts={position.n_contracts} > 3; reduce to {target} for position sizing"
        )
        return ExitRecommendation(
            position_id = position.paper_trade_id,
            ticker      = position.ticker,
            action      = EXIT_REDUCE,
            urgency     = "LOW",
            reasons     = reasons,
            target_size = target,
        )

    # ── Default: HOLD ─────────────────────────────────────────────────────────
    reasons.append("Position within normal parameters — no exit action required")
    return ExitRecommendation(
        position_id = position.paper_trade_id,
        ticker      = position.ticker,
        action      = EXIT_HOLD,
        urgency     = "LOW",
        reasons     = reasons,
    )


def recommend_portfolio_exits(
    snapshot:      PortfolioSnapshot,
    pnl_map:       Optional[Dict[str, float]] = None,
) -> List[ExitRecommendation]:
    """
    Evaluate all open positions and return exit recommendations.

    pnl_map : optional dict mapping paper_trade_id → current unrealized P&L.
    """
    pnl = pnl_map or {}
    return [
        evaluate_exit(pos, current_pnl=pnl.get(pos.paper_trade_id, 0.0))
        for pos in snapshot.positions
    ]
