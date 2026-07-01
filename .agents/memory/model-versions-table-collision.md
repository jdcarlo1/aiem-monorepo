---
name: model_versions table name collision
description: two stock-scanner-api modules both used a table named model_versions with incompatible schemas; one was retired
---

`online_learning.py` (live, wired) and `model_swap_patches.py` (dead, zero importers) both defined `CREATE TABLE IF NOT EXISTS model_versions`, but with different columns:
- `online_learning.py`: `model_name, version, is_live, ...` — per-model versioning, actually used in production.
- `model_swap_patches.py`: `version_label, deployed_at, is_active, notes, rolled_back_at` — single-global versioning, never wired in.

**Why this matters:** if `model_swap_patches.py` had been "wired in" naively (the obvious fix for an apparently-orphaned file), its `CREATE TABLE IF NOT EXISTS` would have silently no-op'd against the table `online_learning.py` already owns, then its `INSERT`/`UPDATE` statements would have crashed (or worse, corrupted assumptions) because the live table has none of the columns it expects.

**How to apply:** before wiring in any "orphaned" module that owns its own DB table, grep for that exact table name across the whole codebase first — don't assume an unwired file's schema is the one in production. `model_swap_patches.py` was retired (renamed to `.py.DEPRECATED_use_online_learning_py` with a warning header) rather than wired in, since `online_learning.py` already provides equivalent functionality (`get_live_model`, `propose_update`, `rollback_to_version`, `version_history`) on the correct live schema.

**Open item:** `online_learning.py`'s `rollback_to_version()` itself has zero call sites anywhere (confirmed via `grep -rn "rollback_to_version(" --include="*.py" .` — only the `def` itself matched). The load/propose side (`get_live_model`, `propose_update`) is live; the rollback side is defined but not yet wired to anything.
