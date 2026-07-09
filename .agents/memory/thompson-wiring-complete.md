---
name: Thompson sampler wiring — COMPLETE
description: Thompson sampled_score is now wired into _aiem_paper_pick_candidates() as LEARNING GATE 3 (third score multiplier). Status moved from PARTIAL to PASS.
---

## Rule
Thompson sampled_score from aiem_paper_thompson is consumed in `_aiem_paper_pick_candidates()` in main.py as LEARNING GATE 3 — a third score multiplier after drift gate and trust gate.

## How it works
- Inserted between LEARNING GATE 2 (trust) block and `_final = sorted(...)` (~L39534-39565)
- Reads `SELECT signal_source, sampled_score FROM aiem_paper_thompson WHERE sampled_score IS NOT NULL` — one query loads all sources into `_th_map`
- For each candidate: `_th_lbl = 0.5 + sampled_score` maps [0,1] Beta sample to [0.5x, 1.5x] band
- No-data sources default to `_th_lbl=1.0` (neutral — not penalized)
- Stores `thompson_sampled_score`, `thompson_multiplier`, `thompson_signal_source` on candidate dict
- Stage 14 Diagram 2 lambda extended to emit all 3 fields in payload_json

## Multiplier chain (in-place, not a single line)
There is NO single `final_score = raw * dm * tw` line in _aiem_paper_pick_candidates. The chain is sequential in-place mutations on `_candidates[t]["score"]`:
1. `_add()` helper: `_eff = score * drift_mult`
2. LEARNING GATE 2: `score = round(score * trust_mult, 4)` (if trust_mult ≠ 1.0)
3. LEARNING GATE 3: `score = round(score * _th_lbl, 4)` (if _th_lbl ≠ 1.0)
4. `_final = sorted(...)` locks in the order

**Why:** Thompson was computed and stored but never read by the decision path. This wiring closes the loop.

## Do NOT
- Touch `update_paper_thompson()` — write path is verified correct
- Merge Thompson with signal_trust_weights/_tw_lbl — separate systems
- Use a single-line final_score pattern that doesn't exist here
