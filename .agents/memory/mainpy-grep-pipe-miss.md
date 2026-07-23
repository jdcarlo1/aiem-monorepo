---
name: main.py grep pipe pattern misses routes
description: Two-stage grep (grep results piped through second grep for @app.route) misses routes where the decorator line does not contain the keyword being searched. The docstring line matches the keyword but is several lines below the @app.route.
---

## Rule
When verifying whether a route exists in main.py, never conclude "route missing"
based solely on a piped grep that filters for @app.route in the same line as the
keyword. Always confirm with `sed -n 'N,Mp'` around hit lines.

## Why
Observed: `grep -n "oe_decision_audit" main.py | grep "@app.route"` returned empty,
but oe_decision_audit IS served by `/stock-api/admin/decision-audit` at line 69251
— the @app.route decorator is 8 lines above the docstring containing "oe_decision_audit".

## How to apply
- Step 1: `grep -n "oe_decision_audit" main.py` → note line numbers of hits.
- Step 2: `sed -n 'N-15,N+5p' main.py` for each hit to see surrounding context.
- Do not use a second pipe to filter for @app.route; decorators appear on a different line.
