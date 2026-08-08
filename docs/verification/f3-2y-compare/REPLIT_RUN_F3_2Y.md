# Replit — F3 SPY 0DTE 2-year backtest (live website rules)

Cursor Cloud is Polygon rate-limited. Please run on stock-api where `POLYGON_API_KEY` is set.

**Do not restart stock-api if another long BT is mid-flight** unless this replaces it.

```bash
cd artifacts/stock-scanner-api
# ensure this file exists (from PR branch cursor/f3-2y-compare-fc0f or after merge to dev)
export PYTHONUNBUFFERED=1
export F3_BAG_RATE_SLEEP=0.35
nohup python3 -u f3_bag_backtest.py --days 730 --stop 0.65 \
  > /tmp/f3_2y_backtest.log 2>&1 &
echo PID=$!
tail -f /tmp/f3_2y_backtest.log
```

Rules must match the website F3 card: $200 notional, −65% premium stop, else 16:00, PM+ORB breakout, real Polygon option bars only.

When finished, paste the summary table (trades / WR / P&L stop vs no-stop) and save `docs/verification/f3-bag-backtest/LATEST.json`.
