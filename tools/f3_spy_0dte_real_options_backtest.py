#!/usr/bin/env python3
"""F3 SPY 0DTE — real option pricing backtest (reference).

Live paper ledger: artifacts/stock-scanner-api/aim_f3_spy_0dte.py
Terminals: AIEM Pattern Lab + OE Strategies page.

Set POLYGON_API_KEY and TRADIER_API_TOKEN (or TRADIER_API_TOKEN_2) in the env.
This file is the offline backtest companion; paste/run the full real-pricing
script the user provided, or extend this runner.

Rules summary:
  PM direction → ORB 9:30-9:44 → breakout with PM → ATM long call/put
  $200 notional, exit 16:00, no stop/target. Real premiums only.
"""
import os
import sys

def main():
    if not os.getenv("POLYGON_API_KEY") or not (
        os.getenv("TRADIER_API_TOKEN") or os.getenv("TRADIER_API_TOKEN_2")
    ):
        print("Set POLYGON_API_KEY and TRADIER_API_TOKEN to run the full backtest.")
        print("Live paper path uses Tradier chain premiums via aim_f3_spy_0dte.py")
        sys.exit(2)
    print("Use the full F3 real-options script from the 2026-08-06 directive paste,")
    print("or import helpers from aim_f3_spy_0dte for live premium/PM direction.")
    sys.exit(0)

if __name__ == "__main__":
    main()
