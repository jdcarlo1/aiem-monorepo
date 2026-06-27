---
name: Tradier greeks=false IV bug
description: Root cause and fix for iv=0 stored in unusual_calls_log; admin backfill endpoint
---

**Rule:** `_tradier_fetch_calls` must always use `"greeks": "true"` and extract `greeks.mid_iv` as primary IV source.

**Why:** With `greeks: "false"`, Tradier's response omits the greeks object entirely and `implied_volatility` returns None → stored as iv=0 in `unusual_calls_log` → conviction scoring applies minimum 1.0× IV bonus instead of 1.5–1.8× for high-IV names → signals misclassified as ELEVATED instead of HIGH/EXTREME.

**Fix applied:**
- Line ~1646: `"greeks": "false"` → `"greeks": "true"`
- Line ~1667: `float(((_o.get("greeks") or {}).get("mid_iv")) or _o.get("implied_volatility") or 0)`

**Backfill endpoint:** `POST /stock-api/admin/backfill-iv?days=7` (X-Admin-Token header required)
- Queries all iv=0 rows, fetches Tradier chains with greeks=true, updates in-place
- Clears unusual-calls, conviction-calls, eod-sweeps in-memory caches when done
- Idempotent, runs in background daemon thread
- **Only effective during market hours** — Tradier returns mid_iv=0 on weekends (markets closed)

**Production fix path:** Publish → Monday 9:36 AM auto-scan saves real IV → call backfill-iv once Monday morning to fix any remaining Friday records.

**How to apply:** Any time `_tradier_fetch_calls` is modified, verify `greeks: "true"` is still set. If iv=0 appears in the DB again, run the backfill endpoint during market hours.
