---
name: signal-discoveries audit findings and hardening
description: 2026-07-02 audit of aiem_signal_discoveries write paths, gate bypass proof, blast radius, and what constitutes valid OOS evidence
---

## The structural bypass proof
`oos_edge = NULL` in a `status='validated'` row is physically impossible via the current
`_mkt_tool_save_discovery` function — Gate 1 returns immediately without INSERT when
`oos_edge is None`. Finding NULL in the DB column is therefore hard proof that the row was
written outside that function entirely (raw SQL, one-off script, or the aiem_process.py path).

**Why:** Useful as a future audit heuristic: any time a validated discovery row shows NULL
oos_edge, assume write-path bypass, not a code bug in the gate itself.

## Two write paths exist (as of 2026-07-02)
1. **`_mkt_tool_save_discovery` (main.py ~L20828)** — 4-gate checked: oos_edge required+>0,
   win_rate≥54%, n≥200, Bonferroni p-value. Status = 'validated' at insert.
2. **`aiem_write_signal_discoveries` (aiem_process.py)** — dormant (not run by any workflow).
   After hardening: in_misses≥5, miss_rate≥60%, gap≥0.25; stores signal_win_rate on 0-100
   scale. Status = 'hypothesis' at insert; promotion to 'validated' in `nightly_learn`
   (rolling_win_rate≥0.55, n_outcomes≥10).

**How to apply:** Before adding any new automated pattern-detector that writes to this table,
confirm it also uses 0-100 scale for signal_win_rate and has explicit gates matching path 1.

## Scale convention: 0-100, not 0-1
`signal_win_rate` and `baseline_win_rate` use **percentage scale (0-100)**, matching Gate 2's
`float(signal_win_rate) < 54.0` comparison. The aiem_process.py path originally stored raw
fractions (0-1); fixed 2026-07-02.

## What counts as valid OOS evidence (learned the hard way)
**NOT valid:**
- Parsing EV figures from the `notes` text column, even if those notes predate the current
  session. Notes are agent-written qualitative text with no structured, timestamped backing.
  `aiem_discovery_outcomes` confirmed `retestable=False` for ids 2 and 3 — no structured data
  to verify the claimed EV.
- A holdout window that falls inside the original training window. id 9 (washout ignition):
  holdout was Apr-Jun 2026, inside the 2yr Jul 2024–Jun 2026 training window. Outcome checker
  confirmed skip_reason="no fresh forward window yet."
- A holdout with n<200 or p>0.05. id 4's structured OOS result: n=9, p=0.3936, avg_ret=-0.44%.

**IS valid:**
- A structurally independent holdout period (data the signal was NOT trained on).
- n≥200, statistically significant p-value in that holdout.
- Results in `aiem_discovery_outcomes` with a real `realized_win_rate`, not NULL.

**How to apply:** Before setting `oos_edge` on any row, check `aiem_discovery_outcomes` for
that `discovery_id`. If `retestable=False` or `realized_win_rate IS NULL`, there is no valid
OOS evidence regardless of what the `notes` column says.

## DB-level constraint (corrected form)
```sql
ALTER TABLE aiem_signal_discoveries ADD CONSTRAINT oos_edge_required
CHECK (status != 'validated' OR (oos_edge IS NOT NULL AND oos_edge > 0));
```
`hypothesis` and `retired` rows may have NULL oos_edge (correct — they haven't earned OOS
validation). Only `validated` rows are blocked. An earlier incorrect form that exempted only
`retired` was replaced with this form after ids 2,3,4,9 were reverted to `hypothesis`.

## Final state after 2026-07-02 audit
- **validated (clean):** id 6 (oos_edge=16.37, gate1+2 PASS)
- **validated (pre-existing Gate 2 miss):** id 1 (signal_win_rate=52.35 < 54%; known issue)
- **hypothesis (awaiting real forward OOS):** ids 2, 3, 4, 9 — underlying evidence in notes
  is qualitative/agent-authored; need forward OOS accumulation before promotion
- **retired:** ids 5, 7, 8 — compound gate failures; no remediation path without full rebuild

## Blast radius from original bypass
7 of 9 rows had oos_edge=NULL. Retired 5,7,8 (compound failures). ids 2,3,4,9 demoted to
hypothesis — NOT validated, because no independent OOS evidence exists for any of them.
The earlier "re-annotation" pass (populating oos_edge from notes text) was reverted: notes-
derived numbers are not structured OOS evidence.

## id 9 (washout ignition) metric note
`signal_win_rate=0.15` stores fraction of fires achieving ≥20% gain in 20d (n=261, p=0.0006)
— a valid but differently-defined metric than Gate 2 expects. The conventional win rate cannot
be computed without a proper forward OOS window. Do not overwrite with an in-sample holdout
figure. Both metric and status stay at original values until forward OOS data accumulates
(~2.5 fires/week market-wide → meaningful n accrues in ~80 weeks).
