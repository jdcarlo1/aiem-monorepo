"""
aiem_probability_engine

Standalone module family implementing the "AIEM Probability Engine" spec:
calibrated per-horizon (1d/2d/3d/4d) up-move probabilities built from the
9 conviction layers, with walk-forward validation, regime/macro/toxicity/
cost adjustments, and ensemble disagreement -> confidence.

ISOLATION CONTRACT (do not violate):
  - This package does NOT import from, or get imported by, main.py.
  - It does NOT modify any live scheduler, scan, or alert-sending logic.
  - It only READS from the existing Postgres DB (ai_short_calls_log,
    polygon_market_daily, conviction_stack_watchlist, oi_daily_snapshot)
    and WRITES to its own new tables (see config.py) plus files under
    aiem_probability_engine/models/ and aiem_probability_engine/reports/.
  - Nothing in this package is wired into the live website/scanner until
    it has been independently validated and the user explicitly approves
    wiring it in (see README.md "Status").
"""
