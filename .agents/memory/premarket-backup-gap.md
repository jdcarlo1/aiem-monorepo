---
name: Premarket backup GH Actions gap
description: The original GH Actions backup missed the premarket window; fix and architecture.
---

**The gap:** morning-backup.yml fires at 9:50/10:10 AM ET only. The nightly 3 AM os._exit(0) reset causes aiem-process to boot ~7:46 AM ET — after the 6:55 AM warmup window. Premarket scans (7:00–9:15 AM ET) are missed entirely, leaving D1 with 0 candidates.

**The fix:** `.github/workflows/premarket-backup.yml` — fires every 15 min, 11:00–13:15 UTC (7:00–9:15 AM ET) Mon-Fri.

**Endpoint chain:**
POST /stock-api/admin/aiem-process/run-scan (main.py line ~11485)
→ urllib POST localhost:5055/run-scan
→ aiem_process.py _run_manual_scan()
→ aiem_warmup() + aiem_premarket_scan()
→ D1 universe + D2 scoring in DB
→ open_watcher at 9:30 AM ET reads predictions → D3 governance → Telegram

**Why soft failure:** aiem-process may still be booting at 7:00 AM. Workflow logs warning and exits 0; next 15-min run retries. No secret changes needed (REPLIT_APP_URL + ADMIN_TOKEN already in repo from market-hours-watchdog.yml).

**Do NOT confuse with:**
- morning-backup.yml → calls emergency-run (post-open Workstream D picks)
- market-hours-watchdog.yml → checks pipeline-checkpoint every minute 9:55 AM–3 PM ET
- run-aiem-morning-scan → Workstream D scan only, NOT D1/D2/D3 premarket funnel
