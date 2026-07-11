"""
walk_forward.py — expanding-window walk-forward validation per horizon.

STATUS: DEVELOPER TOOL. run_walk_forward() had zero external callers and was
deleted per the Group A wiring audit (2026-07-11). aiem_discovery_engine.py's
own docstring states "no auto-deployment" for the discovery pipeline; walk-forward
validation was never a required gate.

To re-activate:
  - Re-implement run_walk_forward() here and call it from
    aiem_discovery_engine.DiscoveryEngine.promote_candidate() before
    status is set to 'approved'.
  - Use date_safe_walk_forward_splits() from date_utils.py (embargo_days=2
    default is already correct for the time-series leakage guard).
"""
