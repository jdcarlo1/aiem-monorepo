---
name: StockScanner AI source export package
description: Pre-built zip of stock scanner code (no AIEM, no options engine) committed to the repo — ready to present without regeneration
---

**File:** `stockscanner-ai-source.zip` at project root  
**Asset metadata ID:** `BD15QEY42-RLmOcpsut7_` (in `.agents/agent_assets_metadata.toml`)  
**Built:** 2026-07-26, commit e5eb9f90  
**Size:** 1.8 MB, 228 files

## Contents
- `stock-scanner-api/` — 149 Python files (118,330 lines). Core Flask API, scanner, signals, ML, backtests, alerts, portfolio, regime, intraday modules.
- `stock-scanner-api/patterns/` — 2 files: `zero_dte_sweep.py` (522 lines, 0DTE scanner added 2026-07-26) + `__init__.py`
- `stock-scanner-web/` — 77 React/TS/CSS files (30,574 lines). Vite frontend.
- `TECHNICAL_DOSSIER.md` — 409-line review doc covering all 8 requested items.
- `docs/zero_dte_sweep-FINAL.md` — 0DTE verification record.

## What is EXCLUDED
- `aiem_*.py` (108 files, 76,933 lines) — AIEM institutional engine
- `ase_*.py`, `verify_*.py`, `strict_*.py`, `d2_*.py`, `d3_*.py` (44 files) — options strategy engine + verification toolchain
- `dpl/` and `tools/` subdirectories
- Old zip archives that were in `public/` (`stockscanner_source_20260711.zip`, `aiem_all_code.txt`, `aiem_source_python_only.zip`)

## How to present it next time
Just run: `present_asset(files=[{"file_path": "stockscanner-ai-source.zip", "title": "StockScanner AI — Source Code Package"}])`  
No regeneration needed unless new files have been added since 2026-07-26.

## Rebuild trigger
Rebuild if: new Python modules added to `artifacts/stock-scanner-api/`, significant frontend changes, or user asks for a fresher copy. Re-run the zip script in the session plan — takes ~5 seconds.

**Why:** `_IV_HISTORY_MIN=5` disclosure approved same day; 0DTE sweep added same day. User explicitly asked for this to be saved so they don't have to wait for regeneration next session.
