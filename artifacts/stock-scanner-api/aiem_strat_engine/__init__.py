"""
aiem_strat_engine — Standalone Advanced Options Strategy Engine
===============================================================
Scope : evaluate, rank, and paper-trade multi-leg options structures.
Isolation: reads chain data from Tradier directly; writes to ase_* DB tables
           only; NEVER imports from or modifies main.py / aiem_options_pipeline.py
           / aiem_options_structure.py or any Diagram-1/2/3 module.
Paper-only until separately approved and verified.
"""
__version__ = "1.0.0"
__all__ = [
    "config", "db", "legs", "catalog", "builder",
    "chain_data", "pricing", "probability", "eligibility",
    "payoff", "greeks", "scoring", "selector",
    "paper_trader", "position_manager", "reporting",
]
