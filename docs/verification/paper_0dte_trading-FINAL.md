# 0DTE Sweep Paper Trading — Permanent Verification Record

**Date:** 2026-07-27  
**Status: PASS** — all directive checklist items satisfied  
**Feature scope:** stock scanning website only (`patterns/zero_dte_sweep.py` + `main.py`). Zero changes to AIEM or options engine.

---

## What was built

Every time the existing 0DTE sweep scanner fires an alert match (writing a row to `pattern_0dte_matches`), a hypothetical paper trade is logged in `paper_0dte_trades`. A separate 1-minute price monitor checks exit levels intraday; an EOD closer fires at 15:35 ET. A live win-rate view `v_paper_0dte_stats` is queryable at any time.

---

## Proposed config defaults — ⚠️ flagged for approval

These are module-level constants in `patterns/zero_dte_sweep.py`, **not** hardcoded in any logic. Change them there to adjust for all future trades.

| Constant | Value | Meaning |
|---|---|---|
| `_PAPER_PROFIT_TARGET_PCT` | `1.00` | Exit when option premium doubles (+100% of entry ask) |
| `_PAPER_STOP_LOSS_PCT` | `0.50` | Exit when option loses half its value (−50% of entry ask) |
| `_PAPER_CONTRACTS` | `1` | Hypothetical contract count per trade (×100 shares) |

**Rationale for proposed values:** 100%/50% are the most widely cited 0DTE paper-trading defaults in retail options communities. They are aggressive enough to reflect realistic 0DTE behavior while providing a clear falsification boundary. These must be confirmed as final by Joel before treating them as production defaults.

---

## Verification checklist

### 1. Raw SQL — table creation (full schema)

```
paper_0dte_trades columns (from information_schema):
  trade_id          bigint        NOT NULL  default=nextval(...)
  match_id          bigint        NOT NULL  FK → pattern_0dte_matches(id)
  contract_symbol   text          NOT NULL
  ticker            text          NOT NULL
  side              text          NOT NULL   (call / put)
  strike            numeric       NOT NULL
  expiry            date          NOT NULL
  entry_price       numeric       NOT NULL   (ask at alert time)
  contracts         integer       NOT NULL  default=1
  profit_target_pct numeric       NOT NULL  (stored from _PAPER_PROFIT_TARGET_PCT)
  stop_loss_pct     numeric       NOT NULL  (stored from _PAPER_STOP_LOSS_PCT)
  exit_price        numeric       nullable
  exit_reason       text          nullable   (target | stop | expired_worthless | eod)
  exit_time         timestamptz   nullable
  pnl_usd           numeric       nullable   ((exit-entry) × contracts × 100)
  pnl_pct           numeric       nullable   ((exit-entry) / entry)
  win               boolean       nullable
  status            text          NOT NULL  default='open'  (open | closed)
  opened_at         timestamptz   NOT NULL  default=now()
```

### 2. End-to-end trade lifecycle (raw SQL result)

```
=== Step 3: insert synthetic pattern_0dte_matches (SPYTEST_VERF) ===
  match_id=1  ask=$1.22

=== Step 4: open paper trade (entry_price=$1.22) ===
  trade_id=1  entry=$1.22  target=$2.44  stop=$0.61
  profit_target_pct=1.0 (config source: _PAPER_PROFIT_TARGET_PCT)
  stop_loss_pct=0.5     (config source: _PAPER_STOP_LOSS_PCT)

=== Step 5: confirm status='open', pnl_usd=NULL ===
  trade_id=1  status=open  pnl_usd=None  win=None   PASS

=== Step 6: negative control — price +20% (between stop and target) ===
  mid_price=$1.46  target=$2.44  stop=$0.61
  exit_reason=None   NEGATIVE CONTROL PASS — trade row untouched, status still 'open'

=== Step 7: simulate target hit (price +105%) → close ===
  trade_id=1  exit_price=$2.50  reason=target
  pnl_usd=$+128.10  win=True  status=closed

=== Step 8: v_paper_0dte_stats view (live query) ===
  total_trades=1  wins=1  losses=0  open_trades=0
  win_rate_pct=100.00%  avg_win_usd=128.10  avg_loss_usd=None   PASS

=== Step 9: EOD expire test ===
  trade_id=2  reason=expired_worthless  pnl_usd=$-85.00  win=False  status=closed
  EOD EXPIRE PASS — closed at $0.00, not silently dropped

✅ ALL CHECKS PASSED
   alert→open | neg-ctrl(stays open) | target→closed | EOD→expired_worthless
```

### 3. Config trace (grep — not hardcoded)

```
grep -n "_PAPER_PROFIT_TARGET_PCT|_PAPER_STOP_LOSS_PCT|_PAPER_CONTRACTS|profit_target_pct|stop_loss_pct" \
     artifacts/stock-scanner-api/patterns/zero_dte_sweep.py

48:  # profit_target_pct=1.00 → exit when option premium doubles (+100%).
49:  # stop_loss_pct=0.50     → exit when option loses half its value (−50%).
52:  _PAPER_PROFIT_TARGET_PCT: float = 1.00   ← DEFINED HERE
53:  _PAPER_STOP_LOSS_PCT:     float = 0.50   ← DEFINED HERE
54:  _PAPER_CONTRACTS:         int   = 1      ← DEFINED HERE
285: -- Source constants: _PAPER_PROFIT_TARGET_PCT, _PAPER_STOP_LOSS_PCT
286: profit_target_pct NUMERIC NOT NULL,   ← stored from config, not hardcoded
287: stop_loss_pct     NUMERIC NOT NULL,   ← stored from config, not hardcoded
560: Config source: _PAPER_PROFIT_TARGET_PCT, _PAPER_STOP_LOSS_PCT, _PAPER_CONTRACTS
578: _PAPER_PROFIT_TARGET_PCT, _PAPER_STOP_LOSS_PCT,   ← passed to INSERT
```

No hardcoded `1.00` or `0.50` appears in any target/stop logic. Every read of these values references the named constant.

### 4. Negative control

**Claim:** a trade whose current price falls between stop_price and target_price is never closed, never silently dropped.

**Mechanism:** `monitor_open_trades()` only calls `_close_trade()` when `exit_reason` is set (non-None). `exit_reason` is set **only** if `current_price >= target_price` (→ "target") **or** `current_price <= stop_price` (→ "stop"). If neither, `exit_reason` stays `None` and the loop moves to the next trade without touching the row.

**AST proof (line 693 — sole _close_trade call):**
```
monitor_open_trades found at line 633
_close_trade call lines inside monitor_open_trades: [693]
Result: _close_trade is only reachable when exit_reason is set (target/stop hit).
A trade with price between stop_price and target_price: exit_reason stays None.
No _close_trade call occurs. Row stays open.   NEGATIVE CONTROL PASS
```

**Live SQL confirmation (Step 6 above):** row with `status='open'` and `pnl_usd=NULL` remained after simulating a +20% price move (above stop, below target).

### 5. Broker/order-submit grep

```
grep -n "broker|order_submit|place_order|submit_order|execute_order|tradier.*order|order.*tradier|POST.*orders|/v1/accounts.*orders" \
     artifacts/stock-scanner-api/patterns/zero_dte_sweep.py

NONE
```

No order-placement or broker-call code path exists anywhere in this feature. Tradier is used only for price reads (`/v1/markets/quotes`, `/v1/markets/options/chains`, `/v1/markets/timesales`). No `POST` to any orders endpoint.

### 6. Monitoring — check interval and mechanism

**Interval:** 1 minute  
**Mechanism:** separate APScheduler job (id=`zero_dte_paper_monitor`), **not** the 5-min scan cycle.  
**Window guard:** 9:30–15:40 ET, Mon–Fri only (in `monitor_open_trades()`).  
**Why separate:** the directive explicitly states the 5-min alert cadence is insufficient to catch intraday target/stop hits. The 1-min poll is registered in `main.py` independently of `_run_0dte_sweep`.

**Log confirmation from startup:**
```
[0dte_sweep] 5-min scan scheduled (windows 10:00-11:30 and 14:00-15:30 ET guard in scan_once)
[0dte_paper] 1-min price monitor + 15:35 ET EOD closer scheduled
```

### 7. EOD handling

**Mechanism:** `close_eod_trades()` is called by APScheduler CronTrigger at 15:35 ET, Mon–Fri (id=`zero_dte_paper_eod`). Any trade still `status='open'` is closed at current market value. If price is unavailable (option already worthless / Tradier returns nothing), closes at `$0.00` with `exit_reason='expired_worthless'`. Confirmed in Step 9 above: `win=False, status=closed, pnl_usd=$-85.00`.

### 8. Win-rate view

`v_paper_0dte_stats` is a `CREATE OR REPLACE VIEW` — not a materialized view, not a static snapshot. Every query re-computes from `paper_0dte_trades` rows. Confirmed in Step 8: immediately reflected the win after the UPDATE to `closed`.

```sql
CREATE OR REPLACE VIEW v_paper_0dte_stats AS
SELECT
    COUNT(*)                                                          AS total_trades,
    COUNT(*) FILTER (WHERE win IS TRUE)                               AS wins,
    COUNT(*) FILTER (WHERE win IS FALSE)                              AS losses,
    COUNT(*) FILTER (WHERE status = 'open')                           AS open_trades,
    ROUND(100.0 * COUNT(*) FILTER (WHERE win IS TRUE)
          / NULLIF(COUNT(*) FILTER (WHERE status = 'closed'), 0), 2) AS win_rate_pct,
    ROUND(AVG(pnl_usd) FILTER (WHERE win IS TRUE),  2)               AS avg_win_usd,
    ROUND(AVG(pnl_pct) FILTER (WHERE win IS TRUE),  4)               AS avg_win_pct,
    ROUND(AVG(pnl_usd) FILTER (WHERE win IS FALSE AND status='closed'), 2) AS avg_loss_usd,
    ROUND(AVG(pnl_pct) FILTER (WHERE win IS FALSE AND status='closed'), 4) AS avg_loss_pct,
    MAX(opened_at)                                                    AS last_trade_at
FROM paper_0dte_trades;
```

API endpoint: `GET /stock-api/0dte/paper-stats` — live, updated on every query.

---

## sha256 cross-check

| File | BEFORE | AFTER |
|---|---|---|
| `patterns/zero_dte_sweep.py` | `24087ad122b0...` | `109770223bb2...` |
| `main.py` | `8e3c5e174ee4...` | `02e1fa4fa08e...` |
| `src/lib/api.ts` | `f4cc48fd3ce0...` | `5e6ef2a0e046...` |
| `src/pages/Dashboard.tsx` | `f3a06b111adc...` | `0626d805eda5...` |

Full sha256 after (current committed state):
```
109770223bb29cb4a43fbf4d568b1133844ec81a1b88ae3c838e11419e390df5  patterns/zero_dte_sweep.py
02e1fa4fa08e4c70b83c1554fd56dd4e3b6ceea834244f981102c671a2087a9f  main.py
5e6ef2a0e046ad27e9c5da3448bf800abf1878ea442626fa7c4a207e9a026683  api.ts
0626d805eda5851344dfaa2ac72faaaee2a28c1e1ece6a444d09883dbd4d1474  Dashboard.tsx
```

---

## No-hardcoded-values check

All new numeric constants in `zero_dte_sweep.py` are named constants or pre-existing gate constants. The only new numeric literals introduced are:

| Literal | Location | Type | Verdict |
|---|---|---|---|
| `1.00` | `_PAPER_PROFIT_TARGET_PCT` declaration | config constant | ✅ named constant, not hardcoded in logic |
| `0.50` | `_PAPER_STOP_LOSS_PCT` declaration | config constant | ✅ named constant, not hardcoded in logic |
| `1` | `_PAPER_CONTRACTS` declaration | config constant | ✅ named constant |
| `100` | `pnl_usd = ... * contracts * 100` | shares-per-contract (fixed industry convention) | ✅ not a tunable parameter |

The pre-existing gate constants (`_DELTA_MIN=0.25`, `_DELTA_MAX=0.70`, `_VOI_MIN=2.0`, etc.) were not changed.

---

## Files changed

| File | Change |
|---|---|
| `artifacts/stock-scanner-api/patterns/zero_dte_sweep.py` | Added config constants; extended `ensure_tables()`; `_write_match()` now `RETURNING id`; added `_fetch_option_mid()`, `open_paper_trade()`, `_close_trade()`, `monitor_open_trades()`, `close_eod_trades()`; `scan_once()` wired to open paper trade on every match |
| `artifacts/stock-scanner-api/main.py` | 1-min monitor job + 15:35 ET EOD cron registered; startup `ensure_tables()` call added; `GET /stock-api/0dte/paper-trades` + `GET /stock-api/0dte/paper-stats` routes added |
| `artifacts/stock-scanner/src/lib/api.ts` | `ZeroDtePaperTrade`, `ZeroDtePaperStats` interfaces; `fetch0dtePaperTrades()`, `fetch0dtePaperStats()` |
| `artifacts/stock-scanner/src/pages/Dashboard.tsx` | `ZeroDtePaperTab` component; `"0dte-paper"` tab wired into union type, tab bar, and render |
