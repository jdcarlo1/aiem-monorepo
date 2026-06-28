---
name: Tradier circuit breaker
description: Tradier call-timeout protection — trip threshold, cooldown, and wiring points in main.py
---

## Rule
`_TD_BREAKER` trips after 3 consecutive Tradier timeouts in 30s → stays open 90s.
All `_td_history()` and `_td_intraday()` calls return empty DataFrame instantly when open.

**Why:** Without a breaker, 3 Tradier calls × 6s timeout = 18s hang per endpoint.
During the 9:30-9:45 market-open burst, multiple jobs compete for the 4-worker APScheduler
pool, and Tradier timeouts saturate it — Flask HTTP threads starve → every tab spins.

**How to apply:**
- `_TD_BREAKER` state dict declared near `_YF_BREAKER` (line ~269)
- `_td_breaker_open()` + `_td_note_timeout()` defined just after (line ~277)
- Both `_td_history` and `_td_intraday` have `if _td_breaker_open(): return pd.DataFrame()` as first line
- Exception handlers in both functions call `_td_note_timeout()` before printing the error

**Thresholds (tunable):**
- Window: 30s | Threshold: 3 hits | Cooldown: 90s
- Yahoo breaker: 300s cooldown (much longer — Yahoo throttle lasts minutes)
- Tradier: 90s (shorter — Tradier recovers faster from transient overload)

**lookahead_audit.py** is a standalone static scanner (`python lookahead_audit.py /path`).
Run it against the api dir to find yf.download() calls missing `auto_adjust=False`.
NEVER import it as a runtime module — it's a dev-time CLI tool only.
Fixed 33 backtest files (all auto_adjust=True → False) in one session. Re-run confirms 0 BACKTEST-RELEVANT findings.
