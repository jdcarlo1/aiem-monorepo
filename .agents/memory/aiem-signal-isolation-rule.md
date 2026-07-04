---
name: AIEM signal isolation rule
description: User-stated architectural constraint — AIEM experimental signals feed only the paper trades tab, never any other website tab
---

## Rule
AIEM-sourced signals (GEX, Put/Call Skew, Term Structure, CTA Triggers, and any future AIEM-only signals) connect **only** to the paper trades tab. No other website tab receives AIEM signal input.

**Why:** User explicitly stated this on 2026-07-04: "AIEM doesn't influence any of my tabs. They only influence the tab with the paper trades." AIEM runs independently in the background and its signals are promoted to public-facing tabs only after sufficient validated data.

**How to apply:**
- `_run_five_layer_conviction` (→ Conviction Stack tab) must never read from `options_structure_scan`, `cta_trigger_scan`, or any AIEM-only signal table.
- New AIEM signals go into AIEM tool maps + paper trade pipeline only (`_aiem_paper_pick_candidates`).
- Promotion path to website tabs: collect data → validate quality → explicit user approval → only then inject into tab-facing scoring functions.
- If a build spec asks to inject AIEM signals into L1-L8 or any tab endpoint, confirm with user before proceeding — Section 0 of any such spec must establish blast radius first.
