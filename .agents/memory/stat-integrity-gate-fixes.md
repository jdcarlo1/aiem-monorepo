---
name: Statistical integrity gate fixes
description: Bonferroni gate escape hatches closed; embargo gap added to walk-forward splitter; hypothesis pre-reg visibility added
---

## Rules fixed (July 2026)

### aiem_test_ledger Bonferroni gate (_mkt_tool_save_discovery)
`p_value=None` → hard block (was: silently skipped gate)
DB failure on ledger query → hard block (was: `except: pass`, gate failed open)
Both escape hatches are now closed. Gate 4 is the only fully-enforced Bonferroni path.

**Why:** A gate that fails open is worse than a failed save. Statistical integrity gates must be fail-closed.

### Hypothesis pre-registration visibility (Option B)
`hypothesis_id=None` → `[NO-PRE-REG]` flag appended to `notes` column in `aiem_signal_discoveries`
`hypothesis_id=N` → verified against hypothesis_registry; `[PRE-REG: hypothesis_id=N]` in notes
Option B chosen (not enforcement) because hypothesis_registry and aiem_test_ledger are parallel systems; forcing pre-registration on every save would be too rigid for AIEM's iterative research loop.

### Embargo gap in date_safe_walk_forward_splits() (date_utils.py)
`embargo_days=2` default — 2 trading dates excluded from BOTH train and val (dropped, not reassigned)
`embargo_days=0` restores back-to-back behavior
Different from `_train_embargoed()` in pit_correction.py (PIT correctness, not inter-window gap).

### walk_forward.py is a developer tool — NOT scheduled
Zero external callers confirmed. Module docstring now says STATUS: DEVELOPER TOOL — NOT SCHEDULED.
Any future mention of "automatic walk-forward validation" is unsupported by this file.

## Three-tier Bonferroni map (current state)
| Layer | Mechanism | Liveness |
|---|---|---|
| hypothesis_registry | bonferroni_adjusted_alpha() = 0.05/n_registered | AIEM-discretion — only active if AIEM calls register_hypothesis() |
| aiem_test_ledger gate | 0.05 / n_tests in last 30 days | LIVE, auto-logged, fail-CLOSED (both escape hatches sealed) |
| BH-FDR Module 5/6 | Benjamini-Hochberg step-up | LIVE, hardwired, no opt-out |
