#!/usr/bin/env python3
"""F3 SPY 0DTE — real option pricing backtest (reference stub).

Full runner: artifacts/stock-scanner-api/f3_bag_backtest.py
Live paper ledger: artifacts/stock-scanner-api/aim_f3_spy_0dte.py
Handoff: docs/verification/REPLIT_RUN_F3_BAG_BACKTEST.md

Set POLYGON_API_KEY then:
  python artifacts/stock-scanner-api/f3_bag_backtest.py --days 63 --stop 0.65
"""
import os
import runpy
import sys
from pathlib import Path

def main():
    if not (os.getenv("POLYGON_API_KEY") or os.getenv("POLYGON_KEY")):
        print("Set POLYGON_API_KEY and run:")
        print("  python artifacts/stock-scanner-api/f3_bag_backtest.py --days 63 --stop 0.65")
        sys.exit(2)
    target = Path(__file__).resolve().parents[1] / "artifacts" / "stock-scanner-api" / "f3_bag_backtest.py"
    sys.argv = [str(target)] + sys.argv[1:]
    runpy.run_path(str(target), run_name="__main__")

if __name__ == "__main__":
    main()
