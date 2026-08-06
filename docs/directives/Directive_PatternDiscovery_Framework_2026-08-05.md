# Directive_PatternDiscovery_Framework_2026-08-05
#
# STATUS: ASSIGNED TO AIEM — continuous 24/7 worker
# Handoff mechanism (no inbox): code + workflow restart
#
#   Workflow: artifacts/stock-scanner: pattern-discovery
#   Runner:   artifacts/stock-scanner-api/aiem_pattern_discovery_runner.py
#   Wrapper:  artifacts/stock-scanner-api/pattern_discovery_wrapper.sh
#   Health:   :5058/   Trigger: POST :5058/trigger/run
#   Evidence: docs/verification/pattern-discovery-FINAL.{md,json}
#
# The restart of `artifacts/stock-scanner: pattern-discovery` IS the message.
# AIEM wakes up executing Steps 1–5 of this directive in a loop (cycle sleep
# default 6h). Does NOT touch D1/D2/D3 or live Pattern Lab dashboard.
#
# Split (confirmed in runner stdout before any results):
#   Full window: 2025-08-05 → 2026-08-05 (1 year SPY 1-min Polygon)
#   In-sample:   2025-08-05 → 2026-04-05 (first 8 months)
#   Out-of-sample: 2026-04-05 → 2026-08-05 (last 4 months; never used in search)
#
# Owner instruction (Joel, 2026-08-05): start working on this 24 hours a day.
