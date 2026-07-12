---
name: Flask route registration dead zone
description: Silent module-level exception in main.py between lines ~29315 and ~41826 prevents @app.route decorators from registering; admin endpoints must be placed before ~line 29315 to be reachable via HTTP.
---

## Rule
Any new `@app.route(...)` endpoint added to main.py at a line number >= ~29315 will silently 404 forever — Flask's URL map is never updated past the point where a bare `try:` block swallows an exception.

## Evidence
- Line 18881 (s7c-force-run) → HTTP 200 ✓
- Line 20011 (test-telegram) → HTTP 401 (registered, auth rejected) ✓
- Line 29315 (backtest-candlestick-confluence) → times out (registered, slow handler) ✓
- Line 41826 (regime-overlay-check) → HTTP 404 ✗
- Line 45942 (supervisor-summary) → HTTP 404 ✗
- Line 63074 (run-council-now) → HTTP 404 ✗

## Why
There is a bare `try:` block somewhere between line 29315 and 41826 in main.py that executes inline (module-level) code. An exception thrown inside that block is caught silently (`except Exception as ...: pass` or similar). All `@app.route(...)` decorators inside that block after the exception point never execute, and neither do any outside it that come later, because the Flask URL map was mid-update.

## How to apply
- Before adding any new endpoint to main.py: check its line number. If > ~29000, insert it before line ~18500 instead (in the s7c area, after line 18881, or between lines 20026 and 29315 which is a safe insertion zone).
- The admin_test_block_halt endpoint added at line 63186 for Directive 4 is in the dead zone and unreachable via HTTP. Use the standalone test script pattern or move the endpoint to before line ~29000.
