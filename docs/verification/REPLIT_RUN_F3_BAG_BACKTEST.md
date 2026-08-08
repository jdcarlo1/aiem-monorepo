# Replit / AIEM handoff — F3 bag/back test

Cursor Cloud often lacks `POLYGON_API_KEY`. Run on the stock-api host (Replit)
where the key is already set.

```bash
cd artifacts/stock-scanner-api
export PYTHONUNBUFFERED=1
# optional: F3_BAG_RATE_SLEEP=0.25
python3 -u f3_bag_backtest.py --days 63 --stop 0.65
```

Live rule match: PM → ORB → breakout with PM → ATM long, $200 notional, **−65% premium stop**, else 16:00 exit.

Results land in `docs/verification/f3-bag-backtest/` (`LATEST.json` + timestamped run).

After Gamma Blast was rejected as a bad pattern, this is the next Pattern Lab strategy to re-check with **real** Polygon option bars.
