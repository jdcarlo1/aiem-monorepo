# AIEM Institutional Terminal — Phase B Handoff
**Date:** July 21, 2026  
**Scope:** Full dashboard build (Phase B) on top of the Phase A API inventory

---

## What Was Built

A Bloomberg-style dark institutional terminal at `/aiem/` that visualizes AIEM's live intelligence — paper portfolio, decision audit trail, evidence chain, specialist council votes, signal discoveries, scheduler status, and more — all sourced from the existing stock-api Flask backend with no duplicate business logic.

---

## Artifact Details

| Property | Value |
|---|---|
| Artifact ID | `artifacts/aiem-dashboard` |
| Kind | React + Vite web app |
| Preview path | `/aiem/` |
| Port | 26003 |
| Workflow | `artifacts/aiem-dashboard: web` |
| Framework | React 18 + Vite 7 + Wouter + TanStack Query |

---

## Backend Changes (main.py)

Five new admin-gated API routes were inserted into `artifacts/stock-scanner-api/main.py` at lines 69038–69340 (before `/stock-api/unusual-puts`). All require `X-Admin-Token` header matching `ADMIN_TOKEN` secret.

| Route | Line | Source Table | Returns |
|---|---|---|---|
| `GET /stock-api/admin/decision-audit` | 69038 | `oe_decision_audit` | `{ count, rows }` (limit param, default 50) |
| `GET /stock-api/admin/gate-events` | 69110 | `oe_gate_events` | `{ count, rows }` (limit param, default 50) |
| `GET /stock-api/admin/council-runs` | 69166 | `oe_council_runs` | `{ count, rows }` (limit param, default 50) |
| `GET /stock-api/admin/position-sizing-log` | 69231 | `oe_position_sizing_log` | `{ count, rows }` (limit param, default 50) |
| `GET /stock-api/admin/evidence-chain/status` | 69297 | `evidence_chain.log` | `{ seq, last_command, last_exit_code, last_timestamp_utc, last_entry_hash, total_entries }` |

Each route uses `psycopg2` directly (not the shared pool) with unique import aliases (`_pg_da2`, `_pg_ge2`, etc.) to avoid name collisions with surrounding code.

---

## Frontend File Structure

```
artifacts/aiem-dashboard/
├── index.html                          ← Space Grotesk + Space Mono fonts via <link>
├── package.json
├── vite.config.ts
└── src/
    ├── App.tsx                         ← WouterRouter base=BASE_URL, 14 routes
    ├── index.css                       ← Dark terminal theme (black bg, orange primary, cyan secondary)
    ├── main.tsx
    ├── hooks/
    │   └── use-api.ts                  ← Custom polling hook with auth + staleness indicator
    ├── lib/
    │   ├── auth.ts                     ← getToken() / setToken() / clearToken() on sessionStorage
    │   └── utils.ts
    ├── components/
    │   └── layout/
    │       ├── AppLayout.tsx           ← Auth guard + sidebar layout
    │       └── Sidebar.tsx             ← Left nav linking all 14 sections
    └── pages/
        ├── login.tsx                   ← 79 lines
        ├── CommandCenter.tsx           ← 107 lines
        ├── Opportunities.tsx           ← 134 lines
        ├── PaperTrades.tsx             ← 116 lines
        ├── Decisions.tsx               ← 127 lines
        ├── Proof.tsx                   ← 165 lines
        ├── Risk.tsx                    ← 142 lines
        ├── Council.tsx                 ← 80 lines
        ├── Signals.tsx                 ← 116 lines
        ├── Regime.tsx                  ← 114 lines
        ├── Scheduler.tsx               ← 127 lines
        ├── Options.tsx                 ← 91 lines
        ├── Learning.tsx                ← 112 lines
        ├── Alerts.tsx                  ← 108 lines
        └── not-found.tsx               ← 21 lines
```

**Total frontend source:** ~2,024 lines across 22 files

---

## Authentication

- Login screen at `/aiem/` — plain text input + PASTE button
- Token stored in `sessionStorage` as `aiem_admin_token`
- `AppLayout` redirects unauthenticated users to `/` on every route
- `useApi` hook automatically attaches `X-Admin-Token` header to any URL containing `/admin/`
- On 401 or 403: token cleared, user redirected to login

---

## The `useApi` Hook

```typescript
useApi<T>(url: string, options?: RequestInit, pollIntervalMs?: number)
// Returns: { data, loading, error, isStale, refetch }
```

- Injects auth header automatically based on URL path
- Polls via `setInterval` when `pollIntervalMs` is provided
- `isStale = true` when last successful fetch is older than `2 × pollIntervalMs`
- Clears token and redirects home on 401/403

---

## Pages and API Endpoints

| Page | Route | Key Endpoint(s) | Poll |
|---|---|---|---|
| Login | `/` | — | — |
| Command Center | `/command` | `/stock-api/health`, `/stock-api/admin/macro/latest`, `/stock-api/admin/job-heartbeats` | 30s |
| Opportunity Queue | `/opportunities` | `/stock-api/aiem-predictions`, `/stock-api/gap-volume-signal`, `/stock-api/washout-ignition-signal`, `/stock-api/pullback-reentry`, `/stock-api/momentum-exhaustion` | 60s / on-demand |
| Paper Trading | `/paper-trades` | `/stock-api/aiem-paper-portfolio`, `/stock-api/paper-trades`, `/stock-api/aiem-paper-portfolio/execution-log` | 30–60s |
| Live Decisions | `/decisions` | `/stock-api/admin/decision-audit`, `/stock-api/admin/gate-events` | 30s |
| Decision Proof | `/proof` | `/stock-api/admin/evidence-chain/status`, `POST /stock-api/admin/aiem-verify-proof` | 60s |
| Portfolio Risk | `/risk` | `/stock-api/admin/position-sizing-log`, `/stock-api/gamma-wall`, `/stock-api/charm-cascade` | 60s |
| Specialist Council | `/council` | `/stock-api/admin/council-runs` | 60s |
| Signal Discoveries | `/signals` | `/stock-api/admin/aiem-pipeline-audit` | 60s |
| Market Regime | `/regime` | `/stock-api/admin/macro/latest`, `/stock-api/market/overview` | 30s (Recharts sparkline) |
| Scheduler | `/scheduler` | `/stock-api/admin/scheduler-jobs` | 60s |
| Options Pipeline | `/options` | `/stock-api/admin/pipeline-checkpoint`, `/stock-api/admin/gate-events` | 30s |
| Learning Loop | `/learning` | `/stock-api/admin/closed-loop-summary` | on-demand (Recharts chart) |
| Alert Feed | `/alerts` | `/stock-api/admin/job-heartbeats` | 30s |

---

## Design System

| Token | Value |
|---|---|
| Background | `hsl(0 0% 0%)` — pure black |
| Foreground | `hsl(0 0% 90%)` — near-white |
| Primary (orange) | `hsl(30 100% 50%)` — Bloomberg orange |
| Secondary (cyan) | `hsl(180 100% 40%)` — terminal cyan |
| Profit green | `hsl(120 100% 40%)` |
| Loss red | `hsl(0 100% 50%)` |
| Font (headings/body) | Space Grotesk (300–700) |
| Font (mono/data) | Space Mono (400, 700) |
| Border | `hsl(0 0% 15%)` — dark grey |
| Card | `hsl(0 0% 4%)` — near-black |

---

## Dependencies Used (already installed)

- `recharts` — macro score sparkline (Regime page) + ML training chart (Learning page)
- `wouter` — client-side routing
- `lucide-react` — icons throughout
- `@tanstack/react-query` — QueryClientProvider wrapper (individual fetches use custom hook)
- `sonner` / `@radix-ui/*` — toast + Toaster component

---

## Notable Implementation Decisions

1. **No Orval codegen** — the backend is Python/Flask, not the TypeScript API server. All fetch calls use the custom `useApi` hook directly against `/stock-api/...` absolute paths, which the Replit proxy routes to Flask at port 5050.

2. **Stale pipeline rows** — the `daily_pipeline_runs` table has stuck `RUNNING` rows from 2026-07-17–19. The Options page flags any `status=RUNNING` row with a date before today as `STALE` rather than hiding it.

3. **Direction-aware P&L** — PaperTrades P&L rendering correctly inverts colors for PUT/SHORT positions (green on negative underlying move).

4. **Evidence chain exit code** — `last_exit_code=1` is treated as WARNING state (amber), not an error (red), per the actual evidence chain semantics.

5. **Scheduler grouping** — 274 jobs are grouped by category prefix for display rather than listed as a flat 274-row table.

---

## Post-Build Fixes Applied

Two issues found during the first screenshot after the design subagent completed:

| Issue | File | Root Cause | Fix |
|---|---|---|---|
| JSX compile error | `Proof.tsx:103` | Bare `>` in JSX text content (esbuild strict mode) | Replaced with template literal `` {`> ${val}`} `` |
| PostCSS `@import` order warning | `index.css:6` | Google Fonts `@import url()` after `@theme inline` block | Moved to `<link>` tag in `index.html`, removed from CSS |

---

## How to Access

The terminal is live in the Replit preview pane under the **"AIEM Institutional Terminal"** dropdown. Direct URL:

```
https://<repl-domain>/aiem/
```

Enter the `ADMIN_TOKEN` secret value to log in. All 14 sections are accessible from the left sidebar after authentication.

---

## What Is NOT in Phase B (out of scope)

- WebSocket / real-time push (polling only, per spec)
- Write actions — the terminal is read-only; it displays data, does not submit trades
- Mobile layout — not requested
- AIEM chat / Q&A session — already exists on the stock-scanner web artifact
- Any new business logic — all calculations remain in the Python backend
