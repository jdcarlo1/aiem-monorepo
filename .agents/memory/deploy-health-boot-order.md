---
name: Deploy health-check boot order + VM sizing
description: Rules for service health-check timing and VM sizing for multi-service Reserved VM deployments
---

# Deploy health-check boot order + VM sizing

## Rule 1 — Early port bind for stock-api
`make_server()` (werkzeug) in `main.py` must execute **before** all heavy local imports (scanner, sms_alerts, multiday_runner, etc. at lines 68-142). Those imports pull in numpy/pandas/sklearn/xgboost and take 60-120 s on a cold container. Replit's promote-phase prober fires immediately; if the port is silent it times out.

**Why:** Placement after heavy imports = 60-120 s before port opens = promote timeout.

**How to apply:** The werkzeug `make_server()` + `Thread.start()` call must be at lines ~163-166, immediately after the `@app.route` health routes are registered at lines 150-153, before any further imports.

## Rule 2 — aiem-process: health server before slow imports
`_start_process_health_server()` must execute before `import aiem_optprob` / `import aiem_firstcandle` (scipy/numpy/sklearn). Those imports take 30-60 s on a cold container. A 100 s production-only sleep (`if os.environ.get("REPLIT_DEPLOYMENT")`) after the health server yields CPU to stock-api during cold start.

**Why:** aiem_optprob pulls in scipy/numpy; importing it simultaneously with stock-api's heavy imports saturates CPU on small VMs.

## Rule 3 — VM sizing is the primary lever (CONFIRMED FIX)
When adding new heavy services (aiem-process, additional Python processes with scipy/numpy/sklearn), the Reserved VM **must be upsized**. All services start simultaneously during the promote phase; if RAM or CPU is insufficient, one or more services fail their health checks and the deploy fails.

**Why:** The deployment failed across 8+ attempts on July 13, 2026. The user upgraded the Reserved VM tier, which immediately fixed the publish. The 100 s stagger is a secondary precaution.

**How to apply:** Before adding a new heavy Python service to `artifact.toml`, check current VM tier. If current services already use >60% of RAM at steady state, upgrade the VM before adding more services.

## Rule 4 — api-server health route
`GET /api/healthz` must be registered as the FIRST route in `app.ts`, before any middleware. The artifact.toml `[services.production.health.startup]` path must match exactly.
