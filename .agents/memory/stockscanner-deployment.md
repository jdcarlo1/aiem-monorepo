---
name: StockScanner production deployment must be Reserved VM
description: Why stock-scanner prod needs an always-on VM (not Autoscale), how to change it, and how dev/prod databases differ
---

# StockScanner production deployment

**Rule:** The production deployment must be a **Reserved VM (always-on)**, not Autoscale.

**Why:** The stock-api relies on (1) APScheduler for timed scans during market hours and (2) background daemon-thread scans that take ~60s to finish then save. Autoscale sleeps the server between visitors and recycles instances on demand, so:
- scheduled scans don't fire (server asleep at scan time),
- triggered scans get killed mid-run before saving → tab shows perpetual "Scanning…" and missing names,
- the health check (`/stock-api/`, `/api`) times out → deployment reports status 500 and crash-loops (repeated "APScheduler started" in deployment logs).
Evidence: same scanner code captured 19 names in always-on dev vs only 4 in Autoscale prod on the same day (June 16 2026).

**How to change it:** The deployment type CANNOT be changed programmatically — `.replit` is locked from direct edits and there is no deployConfig callback. The **user must select Reserved VM in the Publish/Deployments pane** and re-publish. (Confirmed in deployment-failure-debugging.md.)

**Symptom → diagnosis:** If prod shows empty tabs + healthcheck 500 floods + repeated scheduler restarts in deployment logs, the deployment is on Autoscale — tell the user to switch to Reserved VM.

**The wake-up email catch-up CANNOT substitute for an always-on VM.** The `@app.before_request` catch-up only fires when a visitor hits the site. With no traffic at scan time (e.g. 9:35 AM ET), an Autoscale instance is asleep, the scheduler never runs, and the catch-up never triggers → the real-time alert is permanently missed (user only gets a late, collapsed email when they next open the app). Confirmed June 17 2026: zero deployment logs 8:40–10:00 AM ET (server asleep) → a +275% SNBR morning alert never sent. Only Reserved VM fixes timed sends.

**Dev vs prod databases are SEPARATE.** Cleaning/curating the dev DB does NOT change the live app. The production DB is read-only from the agent side (`executeSql` with `environment:"production"` allows SELECT only), so prod data cannot be manually populated — it only fills from the deployed app's own scans. The fix for empty prod data is to make the deployment reliable (VM) so its scans run, not manual inserts.
