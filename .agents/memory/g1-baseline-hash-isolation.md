---
name: G1 baseline hash isolation test behavior
description: _D3_BASELINE_HASH is None in any standalone Python process — must seed from DB before G1 ALLOW tests
---

## Rule
When testing `_evaluate_g1_decision` in isolation (outside the live main.py process), `_D3_BASELINE_HASH` is `None` because `d3_startup()` / `run_phase0_baseline_freeze()` was never called. This causes G1 to return `BLOCK` with `BASELINE_MISMATCH:IN_MEMORY_HASH_UNSET` even when the DB hash is correct — not a production bug.

**Fix for isolation tests:**
```python
import psycopg2, os
with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=4) as cn:
    with cn.cursor() as cur:
        cur.execute('SELECT baseline_hash FROM d3_architecture_baseline ORDER BY id LIMIT 1')
        row = cur.fetchone()
d3._D3_BASELINE_HASH = row[0] if row else None
```

**Why:** In the live process, `d3_startup()` calls `run_phase0_baseline_freeze()` which reads the DB and sets `_D3_BASELINE_HASH` in memory. Isolation test scripts never call this. The 6 real ALLOW decisions in SHADOW mode (July 13–14) confirm the live process wires it correctly.

**How to apply:** Any future test that needs G1 to ALLOW (baseline passes) must seed `d3._D3_BASELINE_HASH` from the DB first. Tests for BLOCK paths (mismatch, PAUSED, DB-error) can patch the evaluator functions directly without seeding.
