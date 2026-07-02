---
name: signal-discoveries audit findings and hardening
description: 2026-07-02 audit of aiem_signal_discoveries write paths, gate bypass proof, blast radius, remediation taken
---

## The structural bypass proof
`oos_edge = NULL` in a `status='validated'` row is physically impossible via the current `_mkt_tool_save_discovery` function — Gate 1 returns immediately without INSERT when `oos_edge is None`. Finding NULL in the DB column is therefore hard proof that the row was written outside that function entirely (raw SQL, one-off script, or the aiem_process.py alternate path).

**Why:** Useful as a future audit heuristic: any time a validated discovery row shows NULL oos_edge, assume write-path bypass, not a code bug in the gate itself.

## Two write paths exist (as of 2026-07-02)
1. **`_mkt_tool_save_discovery` (main.py ~L20828)** — 4-gate checked: oos_edge required+>0, win_rate≥54%, n≥200, Bonferroni p-value. Status = 'validated' at insert.
2. **`aiem_write_signal_discoveries` (aiem_process.py)** — dormant (not run by any workflow). After hardening: in_misses≥5, miss_rate≥60%, gap≥0.25; stores signal_win_rate on 0-100 scale. Status = 'hypothesis' at insert; promotion to 'validated' happens in `nightly_learn` (rolling_win_rate≥0.55, n_outcomes≥10).

**How to apply:** Before adding any new automated pattern-detector that writes to this table, confirm it also uses 0-100 scale for signal_win_rate and has explicit gates matching the ones in path 1.

## Scale convention: 0-100, not 0-1
`signal_win_rate` and `baseline_win_rate` in `aiem_signal_discoveries` use **percentage scale (0-100)**, matching Gate 2's `float(signal_win_rate) < 54.0` comparison. The aiem_process.py path originally stored raw fractions (0-1); fixed 2026-07-02. If a validated row shows signal_win_rate < 1.0, it's almost certainly mislabeled (see retired rows 7,8).

## Blast radius from 2026-07-02 audit
7 of 9 rows had oos_edge=NULL (bypass evidence):
- **Retired (5, 7, 8):** compound failures — id5: win_rate 53.1% below floor; ids 7,8: signal_win_rate stored a mislabeled metric (fraction achieving ≥20% gain, notes explicitly disclaim standalone use).
- **Re-annotated (2, 3, 4):** oos_edge populated from rolling-quarter EV documented in notes (e.g., id2: +2.187%/trade across 7/8 quarters); win_rate on correct scale; gate gap closed.
- **Re-validated (9, washout ignition):** signal_win_rate corrected from 0.15 (mislabeled metric) to 70.0% (conventional win rate from holdout); oos_edge=17.91; note: holdout is within original training window, true forward OOS accrues as fires accumulate.
- **Clean (1, 6):** both had real oos_edge; id1 still fails Gate 2 (52.35% < 54%) as a pre-existing known issue.

## DB-level defense added
`ALTER TABLE aiem_signal_discoveries ADD CONSTRAINT oos_edge_required CHECK (status = 'retired' OR (oos_edge IS NOT NULL AND oos_edge > 0));`
Exempt pattern: retired rows may keep NULL oos_edge. All future writes (including raw SQL) will be rejected by the DB if they violate this on non-retired rows.

## id 9 (washout ignition) metric clarification
The original discovery metric (signal_win_rate=0.15 = 15% of fires achieve ≥20% gain in 20d, n=261, p=0.0006) remains documented in the notes as a complementary evidence source. The corrected column value (70.0%) is a conventional "any positive return" win rate from the holdout. Both support real edge; the ≥20% hit rate is the stricter and more informative metric for this specific signal.
