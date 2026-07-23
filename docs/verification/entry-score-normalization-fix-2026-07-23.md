# entry_score Normalization Fix — Close Record (2026-07-23)

**Commit:** a6d7d36eb85db9e0e87902545dd7939bd53ab93c  
**Status:** CLOSED — both fixes verified against live data and negative-control tests  
**Data Immutability:** no rows deleted or mutated in aiem_paper_trades or oe_trade_records

---

## Chain Script sha256 (unchanged throughout)

```
ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f  verify_chain.sh
58534be51d9445e13c1838532a7d94c2773d6e152d435e6f620ddba64a9f3bf5  tools/verified_run.sh
```

Both MATCH canonical from Phase 8 seal (SEQ=93).

---

## Pre/Post sha256 of Changed Files

| File | Pre-edit | Post-edit |
|------|----------|-----------|
| `paper_performance.py` | `b139e04557c7406bc3fbeb196fd2ac336106d990e253b00695d0d721f2615a5c` | `a275ef6b013f5a388325ad3f453506070d424db228b2ec77afe76724134373cd` |
| `aiem_options_phase3.py` | `2c9908aebe8cd70948da3dbe3bf11f7f8a2be98327e09723057b78b6ab917d2f` | `6fd7ac0de9a7d56986c54a0d3891947161dced23247744df0e73a331fb48a77e` |

---

## Fix 1 — PERF-038 Banding (paper_performance.py:324–367)

### Root cause
Fixed thresholds `[0,20,40,60,80,100]` applied to raw `entry_score` values ranging
52.58–8836.3. Result: 8/9 trades collapsed into the `>= 80` catch-all; bands 0–20,
20–40, 60–80 returned zero rows. Banding was uninformative.

### Fix applied
Replaced fixed thresholds with quintile percentiles (P0/P20/P40/P60/P80/P100)
computed from the actual score distribution at query time using `np.percentile`.
Labels include the real computed threshold values. Rationale: fixed thresholds are
arbitrary at an unknown raw scale; percentile bands always produce non-empty buckets
proportional to the actual distribution and self-document the real scale.

### diff (paper_performance.py)
```diff
-        # ── PERF-038 by confidence band (entry_score) ─────────────────────────
+        # ── PERF-038 by confidence band (entry_score, percentile-based) ──────
+        # entry_score is RAW (not 0-100 normalized). Fixed 0/20/40/60/80/100
+        # thresholds removed 2026-07-23 — they collapsed 8/9 trades into ">=80".
         scored = [(float(c['entry_score']), float(c['pnl']))
                   for c in closed if c['entry_score'] is not None]
         by_confidence = {}
-        if scored:
-            bands = [
-                ('0–20', 0, 20), ('20–40', 20, 40), ('40–60', 40, 60),
-                ('60–80', 60, 80), ('80–100', 80, 100),
-            ]
-            for label, lo, hi in bands:
-                subset = [p for s, p in scored if lo <= s < hi]
-                if lo == 80:
-                    subset = [p for s, p in scored if s >= 80]
-                if subset:
-                    by_confidence[label] = {
-                        'n': len(subset),
-                        'net_pnl': round(sum(subset), 4),
-                        'win_rate': round(sum(1 for p in subset if p > 0)/len(subset)*100, 1),
-                    }
+        by_confidence_note = (...)
+        if len(scored) >= 2:
+            _pct_cuts = [float(np.percentile(_scores_only, p)) for p in (0, 20, 40, 60, 80, 100)]
+            # ... quintile band logic with _last=True for open-ended upper band
```

### PERF-038 live rerun — all 9 trades (raw output)
```
=== PERF-038 BANDING RERUN — LIVE DATA (n=9 trades) ===
note: Bands are percentile-based (P0-P20/P20-P40/P40-P60/P60-P80/P80-P100)
      computed from the actual entry_score distribution at query time.
      entry_score is raw (not 0-100 normalized); fixed thresholds removed 2026-07-23.

Band distribution (5 non-empty bands):
  P0–P20 (52.6–184.9)
    n=2, net_pnl=16.88, win_rate=50.0%
    threshold_lo=52.5845, threshold_hi=184.8563
  P20–P40 (184.9–532.9)
    n=2, net_pnl=-959.92, win_rate=0.0%
    threshold_lo=184.8563, threshold_hi=532.8549
  P40–P60 (532.9–696.8)
    n=1, net_pnl=-466.33, win_rate=0.0%
    threshold_lo=532.8549, threshold_hi=696.8298
  P60–P80 (696.8–1400.8)
    n=2, net_pnl=-300.58, win_rate=0.0%
    threshold_lo=696.8298, threshold_hi=1400.8426
  P80–P100 (1400.8–8836.4)
    n=2, net_pnl=-604.48, win_rate=50.0%
    threshold_lo=1400.8426, threshold_hi=8836.3952

Total trades across all bands: 9 — PASS
```

Before fix: 1 band populated (8/9 trades in ">=80"), 4 bands empty.  
After fix: 5 bands populated, 2+2+1+2+2 = 9 (all accounted for).

---

## Fix 2 — Brier Score Guard (aiem_options_phase3.py — two sites)

### Root cause
Both `_compute_ic_attribution()` (line 909) and `_compute_scorecard_metrics()` (line 1376)
divided `selected_score / 100.0` treating `oe_trade_records.entry_score` as 0-100 normalized,
then silently clamped with `min(1.0, max(0.0, ...))`. If `entry_score` is raw (>100), all
probabilities clamp to 1.0, producing a Brier score of `mean((1-outcome)²)` — meaningless
and silent. `oe_trade_records` has only 2 test rows so the bug was latent.

### Fix applied
Removed silent clamp. Before computing probabilities, check for any value `> 100.0` or
`< 0.0`. If found: print a loud `[BRIER_GUARD] WARN` to stdout with count, max, min, and
reason, set `bs = None` / `brier = None`, and skip computation. If all scores are in
`[0, 100]`: divide by 100 normally (no clamp needed — range is already valid).

### diff (aiem_options_phase3.py — Location 1, _compute_ic_attribution)
```diff
-        probs = [min(1.0, max(0.0, float(r["selected_score"]) / 100.0)) for r in prob_rows]
+        _bad_sc = [float(r["selected_score"]) for r in prob_rows
+                   if float(r["selected_score"]) > 100.0 or float(r["selected_score"]) < 0.0]
+        if _bad_sc:
+            print(f"[BRIER_GUARD] WARN: selected_score out of [0,100] — ...")
+            bs = None
+        else:
+            probs = [float(r["selected_score"]) / 100.0 for r in prob_rows]
```

### diff (aiem_options_phase3.py — Location 2, _compute_scorecard_metrics)
```diff
-        probs = [min(1.0, max(0.0, float(r["selected_score"]) / 100.0)) for r in score_rows]
+        _bad_sc2 = [float(r["selected_score"]) for r in score_rows
+                    if float(r["selected_score"]) > 100.0 or float(r["selected_score"]) < 0.0]
+        if _bad_sc2:
+            print(f"[BRIER_GUARD] WARN: selected_score out of [0,100] in scorecard — ...")
+        else:
+            probs = [float(r["selected_score"]) / 100.0 for r in score_rows]
```

### Negative-control test — raw output

```
=== BRIER GUARD NEGATIVE-CONTROL TEST ===

[Location 1] raw scores (max=8836.4) — expected: bs=None, WARN fired
  bs=None, calibration_error=None
  WARN output: [BRIER_GUARD] WARN: selected_score out of [0,100] — 7/10 values
    out of range (max=8836.40, min=241.43). oe_trade_records.entry_score may not
    be 0-100 normalized. Brier/calibration suppressed — would produce
    clamped-to-1.0 probabilities.
  PASS: True

[Location 2] raw scores (max=8836.4) — expected: brier=None, WARN fired
  brier=None
  WARN output: [BRIER_GUARD] WARN: selected_score out of [0,100] in scorecard —
    5/5 out of range (max=8836.40). oe_trade_records.entry_score may not be
    0-100 normalized. Brier score suppressed — would produce clamped-to-1.0
    probabilities.
  PASS: True

[Location 1] valid scores (0-100) — expected: bs=float, no WARN
  bs=0.23084000000000002, calibration_error=0.072
  no WARN: True
  PASS: True

[Location 2] valid scores (0-100) — expected: brier=float, no WARN
  brier=0.1707
  no WARN: True
  PASS: True
```

All 4 test cases PASS.

---

## git diff HEAD --stat

```
(no output) — all changes captured in commit a6d7d36eb85db9e0e87902545dd7939bd53ab93c
```

---

## Consumer audit disposition

| Consumer | File | Scale assumption | Status |
|----------|------|-----------------|--------|
| PERF-038 banding | `paper_performance.py:324–367` | Now percentile-based, raw-aware | **FIXED** |
| Brier score loc 1 | `aiem_options_phase3.py:908–934` | Guard added, fails loudly if raw | **FIXED** |
| Brier score loc 2 | `aiem_options_phase3.py:1374–1396` | Guard added, fails loudly if raw | **FIXED** |
| Niche segment | `main.py:7352` | Raw (continuous feature) — no fix needed | Confirmed correct |
| Verifier | `verify_phase8_perf.py:737–740` | Raw (print only) — no fix needed | Confirmed correct |

**Session closed. No items remain open.**
