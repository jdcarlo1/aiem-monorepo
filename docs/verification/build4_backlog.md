# Build 4 Backlog — Missing Required Screens

**Created:** 2026-07-23  
**Source:** Phase 11 close-out — NOT_IMPLEMENTED List B  
**Reference:** `docs/verification/phase11_ops_dashboard_close_out.md`  
**Build phase:** Build 4 (Specialist Council + Indicator Lab + Performance Analytics)

---

## Purpose

This file is the durable tracking record for the four Phase B Terminal screens that are not built. They were identified as scope gaps (not cosmetic gaps) during Phase 11 verification. Each item has a confirmed backend engine. The frontend pages do not exist.

This file must be updated when each item is built and verified. It is the authoritative source for Build 4 frontend scope.

---

## Backlog Items

### BUILD4-001 — Performance Analytics Page

**Phase B item:** PAGE-007  
**Frontend route:** `/performance`  
**Component to build:** `artifacts/aiem-dashboard/src/pages/Performance.tsx`  
**Status:** BACKLOG — backend exists, frontend not built

**Backend state (confirmed 2026-07-23):**
- `GET /stock-api/paper-performance` — main.py line 48416; returns paper trade performance analytics
- `_aiem_tool_analyze_independent_performance(days_back=60)` — AIEM tool (main.py line 25145)
- `_aiem_tool_query_own_prediction_performance(days_back=45)` — AIEM tool (main.py line 25225)
- Phase 8 Performance Analytics engine sealed: `docs/verification/phase8-perf-FINAL.md` (SEQ=93, PASS=37/FAIL=0)

**Minimum frontend scope:**
- Win rate by signal source (bar chart)
- Average return by holding period (T+1/T+3/T+5)
- Cumulative P&L curve (line chart)
- Performance breakdown by regime (BULL_STRONG / BULL_MODERATE / NEUTRAL / BEAR)
- 60-day rolling window; poll interval 300s

---

### BUILD4-002 — Probability Engine Page

**Phase B item:** PAGE-008  
**Frontend route:** `/probability`  
**Component to build:** `artifacts/aiem-dashboard/src/pages/ProbabilityEngine.tsx`  
**Status:** BACKLOG — backend exists, frontend not built

**Backend state (confirmed 2026-07-23):**
- `GET /stock-api/aiem-probability-engine/daily-picks` — main.py line 48658
- `GET /stock-api/aiem-probability-engine/track-record` — main.py line 48718
- `POST /stock-api/aiem-probability-engine/force-run` — main.py line 48875
- `POST /stock-api/aiem-probability-engine/live-query` — main.py line 48904
- `GET /stock-api/aiem-probability-engine/live-query/verify/<row_id>` — main.py line 48962
- `GET /stock-api/quant/options-probability` — main.py line 1656
- Full probability engine module: `artifacts/stock-scanner-api/aiem_probability_engine/`
  - daily_picks.py, live_query.py, reports.py, walk_forward.py, model_registry.py
- Phase 7 sealed: `docs/verification/phase7-probability-calibration-FINAL.md`

**Minimum frontend scope:**
- Daily picks table (ticker / direction / probability / confidence band)
- Track-record summary (historical accuracy by confidence decile)
- Live-query panel (ticker input → probability output with verify link)
- Poll interval 60s for daily-picks; track-record loads once

---

### BUILD4-003 — Calibration Page

**Phase B item:** PAGE-009  
**Frontend route:** `/calibration` OR folded as a tab inside `/probability`  
**Component to build:** `artifacts/aiem-dashboard/src/pages/Calibration.tsx` (or tab in ProbabilityEngine.tsx)  
**Status:** BACKLOG — partial backend (bundled with probability engine), frontend not built

**Backend state (confirmed 2026-07-23):**
- `calibration.py` exists inside `artifacts/stock-scanner-api/aiem_probability_engine/calibration.py`
- No standalone `/calibration` API route exists — calibration data surfaces through the track-record endpoint
- Confidence calibration check in main.py: `calib = "INSUFFICIENT DATA..."` / `"calibration": calib` (line ~26607)
- Phase 7 covers probability + calibration jointly

**Build decision required (before BUILD4-003 starts):**
- Option A: standalone `/calibration` route + Calibration.tsx page
- Option B: "Calibration" tab inside ProbabilityEngine.tsx, sourcing from `/track-record`

**Minimum frontend scope (either option):**
- Reliability diagram (predicted probability vs. actual win rate by decile)
- Brier score over time
- Confidence bin table (bins: 0-40 / 40-55 / 55-70 / 70-85 / 85-100)

---

### BUILD4-004 — Indicator Laboratory Page

**Phase B item:** PAGE-010  
**Frontend route:** `/indicator-lab`  
**Component to build:** `artifacts/aiem-dashboard/src/pages/IndicatorLab.tsx`  
**Status:** BACKLOG — partial backend (discovery data + layer9 scores), frontend partially covered by Signals.tsx

**Backend state (confirmed 2026-07-23):**
- `GET /stock-api/admin/signal-discoveries` — main.py line 69710; returns `aiem_signal_discoveries` rows
- `layer9_scores` table initialized at main.py line 57731; 2-hour background scanner
- `aiem_stat_research_runner.py` — standalone workflow (50-cell batches, 20h freshness gate)
- Existing `Signals.tsx` at `/signals` surfaces discovery rows — this is **not** a full interactive lab

**Gap between Signals.tsx and a full Indicator Lab:**
- Signals.tsx: flat table of confirmed discoveries; no filter by signal type; no test vector inspection; no per-discovery detail view; no link to walk-forward results
- Full lab requires: parameterized signal browser, test-result detail drill-down (Fisher p-value, BH-FDR, n, WR, OOS edge), filter by status (CONFIRMED/RETIRED/PENDING), and ability to inspect the raw evidence for any given discovery

**Minimum frontend scope:**
- Discovery browser: filter by signal_name / status / date range
- Per-discovery detail panel: Fisher p, BH-FDR q, n, WR, oos_edge, regime breakdown
- Layer9 score chart: hurst_raw / vpin_raw / amihud_score / vrp_score for a selected ticker
- Research queue: stat_research_runner batch status (cells run today, pending, freshness)
- Poll interval 120s

---

## Acceptance Gate

Each item is complete when:
1. The frontend page is built and registered in `App.tsx`
2. All `useApi` calls return real data from the confirmed backend routes
3. Loading / error / empty states are implemented
4. The item passes Phase 12 (or Build 4 verification) under `verified_run.sh`

---

## Build 4 Scope Context

Build 4 was originally defined as: Specialist Council + Indicator Lab + Performance Analytics.

**Council.tsx** (`/council`) is already built and PASS in Phase 11 (PAGE-038 verified under cross-page consistency; council data live).

Build 4 remaining scope as of 2026-07-23:
- BUILD4-001: Performance Analytics (backend full — quickest build)
- BUILD4-002: Probability Engine (backend full — high value)
- BUILD4-003: Calibration (backend partial — fold into Probability page to minimize new route work)
- BUILD4-004: Indicator Lab (backend partial — most complex, needs design decision on scope boundary with Signals.tsx)
