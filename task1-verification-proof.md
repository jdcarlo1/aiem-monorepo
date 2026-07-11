# Task 1 — Verification Proof

Generated: 2026-07-11

---

## 1. Raw Test Execution Proof

### Test runner type

These are NOT pytest or unittest tests. There is no test framework. The test script
is a standalone Python script (`task1-verification-fixes/test_decisions.py`) that:
- Inserts and deletes rows in the live dev database directly
- Calls the patched function inline (the function body is copied verbatim into the
  script to avoid importing the full 40,000-line module)
- Asserts expected RuntimeError codes are raised or expected return values are returned
- Prints PASS/FAIL for each case and exits with code 1 on any failure

### Exact command executed

```
cd /home/runner/workspace/artifacts/stock-scanner-api
python3 task1-verification-fixes/test_decisions.py 2>&1
```

Working directory: `/home/runner/workspace/artifacts/stock-scanner-api`
Script path: `task1-verification-fixes/test_decisions.py`
Python binary: system `python3`
stdout and stderr merged via `2>&1`

### Complete unedited terminal output

```
======================================================================
DECISION 1 — multi_signal: future scan_date in scan_result_cache
======================================================================

[setup] Inserting scan_result_cache row with scan_date=2026-07-12 ...
[setup] DB confirms MAX(scan_date) = 2026-07-12 (today=2026-07-11, tomorrow=2026-07-12)

[test D1-A] Calling stage3 with source='multi_signal', ticker='AAPL' ...
[test D1-A] RuntimeError raised: LOOKAHEAD BIAS DETECTED [multi_signal]: scan_result_cache.scan_date=2026-07-12 > today=2026-07-11. ERROR_CODE=MULTI_SIGNAL_CACHE_FUTURE_DATE. All multi_signal picks from this cache record are contaminated.
[test D1-A] ERROR_CODE=MULTI_SIGNAL_CACHE_FUTURE_DATE confirmed in message

[cleanup] Deleting scan_result_cache row for endpoint='multi-signal', scan_date=2026-07-12 ...
[cleanup] Remaining rows for endpoint=multi-signal: 0

[test D1-B] Calling stage3 with source='multi_signal', NO cache row — expect MULTI_SIGNAL_CACHE_MISSING ...
[test D1-B] RuntimeError raised: LOOKAHEAD BIAS DETECTED [multi_signal]: No row in scan_result_cache for endpoint='multi-signal'. ERROR_CODE=MULTI_SIGNAL_CACHE_MISSING. Pick provenance unknown — cannot verify scan was not future-dated. Fail-closed: same rationale as CONVICTION_PROVENANCE_UNKNOWN_EMPTY_TABLE.
[test D1-B] ERROR_CODE=MULTI_SIGNAL_CACHE_MISSING confirmed — fail-closed on missing cache

======================================================================
DECISION 2 — conviction_stack: empty table = CONVICTION_PROVENANCE_UNKNOWN_EMPTY_TABLE
======================================================================

[setup] conviction_stack_watchlist row count = 0 (must be 0 for this test)

[test D2-A] Calling stage3 with source='conviction_stack', ticker='MSFT' (no rows in table) ...
[test D2-A] RuntimeError raised: LOOKAHEAD BIAS DETECTED [conviction_stack]: No rows in conviction_stack_watchlist for ticker=MSFT. ERROR_CODE=CONVICTION_PROVENANCE_UNKNOWN_EMPTY_TABLE. Provenance unknown — pick rejected. Fires in ALL environments (dev empty table and production outage are indistinguishable by design).
[test D2-A] ERROR_CODE=CONVICTION_PROVENANCE_UNKNOWN_EMPTY_TABLE confirmed in message

[setup D2-B] Inserting conviction_stack_watchlist row with snap_date=2026-07-12 for ticker='TSLA' ...
[setup D2-B] DB confirms snap_date = 2026-07-12

[test D2-B] Calling stage3 with source='conviction_stack', ticker='TSLA', snap_date=2026-07-12 ...
[test D2-B] RuntimeError raised: LOOKAHEAD BIAS DETECTED [conviction_stack]: conviction_stack_watchlist.snap_date=2026-07-12 > today=2026-07-11. ERROR_CODE=CONVICTION_FUTURE_SNAP_DATE.
[test D2-B] ERROR_CODE=CONVICTION_FUTURE_SNAP_DATE confirmed in message

[cleanup D2-B] Deleting test row for TSLA ...
[cleanup D2-B] TSLA rows remaining: 0

[setup D2-C] Inserting conviction_stack_watchlist row with snap_date=2026-07-11 for ticker='GOOG' ...

[test D2-C] Calling stage3 with source='conviction_stack', ticker='GOOG', snap_date=2026-07-11 (should pass) ...
[test D2-C] Returned: {'check': 'lookahead_bias', 'ticker': 'GOOG', 'source': 'conviction_stack', 'raw_score': 12.0, 'bias_detected': False, 'passed': True, 'note': 'non-polygon source; pipeline architecture guarantees prior-session data only'}

======================================================================
DECISION 3 — data_snapshot post-loop: contamination-rate gate
======================================================================

[test D3-A] 100 picks, 10 violations (10%) — expect RuntimeError ...
  [data_snapshot] contamination_rate=10.0% (10/100 picks triggered leakage guard)
[test D3-A] RuntimeError raised: DATA_SNAPSHOT_CONTAMINATION_RATE_EXCEEDED: 10/100 picks (10.0%) triggered LookaheadViolation. Threshold: 5%. Dataset build aborted — training on contaminated data is not allowed.

[test D3-B] 100 picks, 4 violations (4%) — expect PASS (below 5%) ...
  [data_snapshot] contamination_rate=4.0% (4/100 picks triggered leakage guard)
[test D3-B] Returned 96 rows (expected 96)

[test D3-C] 100 picks, 5 violations (exactly 5%) — expect PASS (threshold is strictly >5%) ...
  [data_snapshot] contamination_rate=5.0% (5/100 picks triggered leakage guard)
[test D3-C] Returned 95 rows (expected 95)

[test D3-D] 0 violations — gate is skipped entirely (expect PASS) ...
[test D3-D] Returned 50 rows

======================================================================
TEST SUMMARY
======================================================================
  [✓] PASS  D1-A multi_signal future date raises RuntimeError
  [✓] PASS  D1-B multi_signal missing cache raises MULTI_SIGNAL_CACHE_MISSING (fail-closed)
  [✓] PASS  D2-A conviction_stack empty table raises CONVICTION_PROVENANCE_UNKNOWN_EMPTY_TABLE
  [✓] PASS  D2-B conviction_stack future snap_date raises CONVICTION_FUTURE_SNAP_DATE
  [✓] PASS  D2-C conviction_stack today snap_date returns passed=True
  [✓] PASS  D3-A 10% contamination rate raises DATA_SNAPSHOT_CONTAMINATION_RATE_EXCEEDED
  [✓] PASS  D3-B 4% contamination rate passes (below threshold)
  [✓] PASS  D3-C 5% contamination exactly at threshold passes (strictly >5% triggers)
  [✓] PASS  D3-D 0 violations — gate is not entered

ALL 9 TESTS PASSED
EXIT_CODE: 0
```

---

## 2. Deferred Ticket

### Full contents of `.local/tasks/backtest-integrity-gap.md`

```
# Backtest Integrity Gap — LookaheadViolation in context.py / predict.py

**Status:** Open — explicitly out of scope for Task 1 per user directive dated 2026-07-11.

## What the gap is

`context.py:272/277/282` and `predict.py:323/328/333` each catch `LookaheadViolation`
and swallow it — setting degraded overlay values (regime/liquidity/layer9) and continuing
to produce a prediction score. The pick still gets scored, just with partial overlays.

This does NOT affect live paper trades: confirmed via grep that `predict.py` is
not called anywhere inside `_aiem_paper_pick_candidates()` or
`_aiem_paper_execute_today()` (both returned empty — see 2026-07-11 audit output).

## Who is affected

Only the probability engine backtest pipeline:
- Historical scoring in `context.py` (used during model training)
- Live scoring in `predict.py` (used during AI Short Calls scoring, not paper picks)

## What needs to be done

1. Add a `_lookahead_fired: bool` flag to the return dict of `predict.py`'s scoring function
   when any overlay catch fires.
2. Find every caller of that function and check whether a `_lookahead_fired=True` result
   causes the pick to be excluded from the scored set, or whether it still proceeds with
   a degraded score.
3. For `context.py`: same pattern — flag contaminated context rows so the training loop
   in `build_dataset()` can exclude them (or count them toward the contamination-rate gate
   already added in Task 1).

## Why this was deferred

The fix requires tracing every caller of `predict.py`'s scoring function to find where
`_lookahead_fired` would need to be checked. That is a separate scope from the live
paper-trade protection work in Task 1. Fixing these catches without knowing the callers
could add complexity to a path that doesn't affect live trades.

## Evidence

- Grep run 2026-07-11: zero matches for `predict\|probability_engine` in
  `_aiem_paper_pick_candidates` (lines 42185-42850) and `_aiem_paper_execute_today`
  (lines 42851-43500).
- 8 catch sites total: 7 use bare `except LookaheadViolation`, 1 uses
  `except _pit_guard.LookaheadViolation` (main.py:22479, AIEM tool, not paper path).
- 6 of the 8 sites are in the probability engine (backtest/scoring path only).
```

### What integrity gap it describes

`context.py` and `predict.py` both catch `LookaheadViolation` when computing feature
overlays (regime, liquidity, layer9) and silently continue with degraded values instead
of aborting. A pick that triggered a lookahead violation during overlay computation is
still scored and returned, just with some overlay features zeroed/defaulted. The
violation is logged but the pick is not excluded.

This means: if the probability engine's training pipeline (`context.py` + `build_dataset()`)
processes a row where a lookahead violation fired in an overlay, that row is silently
included in the training set with partially-contaminated features. The contamination-rate
gate added in Task 1 (`data_snapshot.py`) counts only violations that fire in
`_pit_features()`, not in the overlay computation layer — so overlay-sourced contamination
is not caught by the gate.

### Why it is deferred and whether it affects the "ALL 9 TESTS PASSED" claim

**Why deferred:** The fix is caller-tracing work. To add a `_lookahead_fired` propagation
flag, every caller of `predict.py`'s scoring function must be identified and updated to
check the flag and decide whether to exclude the pick. Without that trace, adding the flag
without hooking callers accomplishes nothing. This is a distinct scope from Task 1's
mandate, which was limited to `data_snapshot.py`'s post-loop gate (per the approved scope
ruling).

**Does deferring it affect the "ALL 9 TESTS PASSED" claim? No.** The 9 tests cover
exactly the three things that were in scope for Task 1:

- D1-A/D1-B: `stage3_lookahead_bias_check` with `source='multi_signal'`
- D2-A/D2-B/D2-C: `stage3_lookahead_bias_check` with `source='conviction_stack'`
- D3-A/D3-B/D3-C/D3-D: `data_snapshot.py` post-loop contamination-rate gate

None of the 9 tests touch `context.py` or `predict.py`. The deferred gap is in a
separate code path (probability engine overlay computation) that was explicitly excluded
from Task 1 scope. The 9 PASS results are accurate for what they claim to test. They
make no claim about `context.py` or `predict.py` overlay catch behavior.
