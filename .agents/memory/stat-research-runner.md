---
name: Standalone stat research runner
description: Why the main.py continuous research loop never auto-starts, and the standalone runner that fixes it.
---

## The Problem
`_MKT_CONTINUOUS_LOOP_STARTED = False` (line 30754) and `def _mkt_start_continuous_loop` (line 30818) are both inside the Flask dead zone (lines ~29315–41826). When the module loads, a silent exception fires in that range and skips module-level code, so the flag is never assigned and the function is never defined. The deferred init lambda at line 1939 (`lambda: _mkt_start_continuous_loop()`) raises NameError, caught silently, and the loop never starts.

**Why:**  The dead zone kills `@app.route` decorators AND module-level assignments inside the problematic try/except block. The continuous loop's flag + start function are both victims.

## The Fix
`aiem_stat_research_runner.py` — standalone Python script (no Flask, no main.py imports):
- Uses psycopg2 + scipy.stats.ttest_ind directly
- Writes to `aiem_grid_test_state` (EOD) and `aiem_intraday_grid_state` (intraday)
- Workflow: `artifacts/stock-scanner: stat-research`
- Command: `python3 /home/runner/workspace/artifacts/stock-scanner-api/aiem_stat_research_runner.py`
- Loop: batch_size=50 cells per pass, 20h freshness gate, 2h sleep weekdays / 30min weekends

**How to apply:** If the continuous loop in main.py ever needs to be wired again, note that any module-level code inside a try/except between lines 29315-41826 may be silently skipped. Put flags and start calls BEFORE line 29315 or use the standalone runner pattern.

## Performance
- Each SQL query (LEAD window function on polygon_market_daily): ~14-20 seconds
- 184-cell full pass: ~42 minutes
- Confirmed working: first cell `ind_rsi_14_lt_30|next_day` written 2026-07-13 08:02:16
- First FINDING: RSI oversold (<30) → avg return +0.39% (p=0.0000, n=100k)

## Isolation
- Zero OpenAI tokens — pure scipy.stats
- aiem_isolation_guard NOT used (standalone, outside main.py's scope)
- `aiem_process.py` handles same-day first-candle capture at 9:36 AM ET
- Intraday battery stays idle until `aiem_first_candle_data` accumulates rows (starts Monday 9:36 AM)
