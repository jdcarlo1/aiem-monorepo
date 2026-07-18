"""
aiem_portfolio_engine — Phase 2 Portfolio Optimization & Portfolio Risk.

Public entry point: run_portfolio_gate()
"""
from .gate import run_portfolio_gate, PortfolioDecision
from .exit_mgmt import (
    EXIT_HOLD, EXIT_CLOSE, EXIT_REDUCE, EXIT_HEDGE, EXIT_ROLL, EXIT_ADJUST,
    EXIT_ACTIONS, ExitRecommendation, evaluate_exit, recommend_portfolio_exits,
)
from .config import GATE_STEPS

__all__ = [
    "run_portfolio_gate", "PortfolioDecision",
    "EXIT_HOLD", "EXIT_CLOSE", "EXIT_REDUCE", "EXIT_HEDGE", "EXIT_ROLL", "EXIT_ADJUST",
    "EXIT_ACTIONS", "ExitRecommendation", "evaluate_exit", "recommend_portfolio_exits",
    "GATE_STEPS",
]
