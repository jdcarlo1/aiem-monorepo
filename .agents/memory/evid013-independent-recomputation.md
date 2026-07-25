---
name: EVID-013 / NEG-038 / NEG-039 / NEG-040 — Independent Recomputation
description: Independent recomputation of paper-trading performance metrics; reconciliation result; independence scanner pattern.
---

## Status
EVID-013: PASS (sealed 2026-07-25). 10/10 metrics MATCH, 0 mismatches.

## Artifact
`tools/independent_recomputation.py` (sha256=3f4e4a98...)
`docs/verification/evid013-neg038-039-040-FINAL.md` (sealed)

## Results
- Reconciliation: 10/10 MATCH vs. API; Δ=0 for all except calmar (Δ=0.000005 = round() precision)
- Known-answer test vectors: 6/6 PASS
- Mutation check: PASS
- NEG-038 API consistency: PASS (2 independent calls identical)
- NEG-039 SQL cross-check: embedded in reconciliation, PASS
- NEG-040: same artifact as EVID-013 (stated explicitly)

## paper_performance.py
sha256 BEFORE = a38b04ee... (unchanged — original was NOT modified)

## Key metric facts (at n=19, 2026-07-25)
- sharpe_per_trade: −0.727374
- sortino_per_trade: −0.619949
- calmar_ratio: 0.999995
- var_95_pct: 21.9477
- cvar_95_pct: 27.0062
- max_drawdown_pct: −19.1503
- net_profit: −3830.05

## Independence scanner pattern
**Why:** A scanner that uses `"pattern" in line` matches the pattern inside docstrings/print-string literals, producing false positives.

**How to apply:** For independence checks, always use `^\s*import forbidden_module` and `^\s*compute_forbidden_func\s*\(` (anchored to line start) via grep/subprocess, NOT substring search. This ignores occurrences inside strings, comments, and docstrings.

```python
subprocess.run(["grep", "-nP",
    r"^\s*(import\s+module|from\s+module\s+import)", file], ...)
subprocess.run(["grep", "-nP",
    r"^\s*forbidden_call\s*\(", file], ...)
```

## NEG-040 scope
Both EVID-013 and NEG-040 require the same artifact (independent script + raw data + reconcile). They share `tools/independent_recomputation.py`. This is stated explicitly in the script and the sealed doc.
