# Real AIEM Morning Loop — Aug 6 failure + Friday fix plan

## What failed (2026-08-06)

| Piece | Status that day | Root cause |
|---|---|---|
| `aiem_morning_scan` → `today_predictions: 0` | Failed | Prod still on old binary: `column "score" does not exist` |
| Morning brief | Empty ("Pre-market data is loading") | Flask queried wrong `call_sweep_log` columns (`prem`/`price`/`last_seen`) and **cached** the placeholder |
| Nano / SC morning picks | Soft / separate | Not caused by Loop B `score` bug; own universe/grade gates |
| `aiem_v3_discovery` new equity | None | Premarket 8:00 job likely missed or orchestrator produced no BUY; Jul 27 opens may consume capacity |
| Options Engine completed trades | 0 | Liquidity / score gates (pre-`OE_GATE_PROFILE=balanced`) |

## Code status (this PR)

| Fix | Status |
|---|---|
| Loop B SQL uses `total_pts` (not `score`) | ✅ in git (PR #19 + this branch) |
| Per-source try/except + Polygon fallback | ✅ |
| Catchup window 09:07–16:00 ET | ✅ |
| Catchup **waits** for module load (no silent skip) | ✅ **added** |
| 9:45 ET Loop B watchdog if predictions empty | ✅ **added** |
| Morning brief → `unusual_calls_log` + Loop B + RVOL | ✅ **added** |
| Do not cache "loading" placeholder | ✅ **added** |
| api-server morning-brief proxies Flask | ✅ **added** |
| v3 discovery startup catchup | ✅ **added** |
| OE `balanced` gate profile | ✅ |

## Required ops action

1. **Merge PR #38**
2. **Publish / redeploy stock-api before Friday 9:07 AM ET**
3. Verify:
   - `GET /stock-api/aiem-predictions` → `today_predictions` non-empty after 9:07 (or by 9:45 watchdog)
   - `GET /stock-api/morning-brief` → real brief text, not loading stub
   - OE: `OE_GATE_PROFILE` unset or `balanced`
   - Paper opens preferably `signal_source=aiem_loop_b` when Loop B succeeded

**Without Publish, Friday fails the same way as Aug 6.**
