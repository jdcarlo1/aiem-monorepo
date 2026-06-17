---
name: Net Flow tab display rule
description: Why the MICRO NET FLOW tier sections must never render empty when positive-inflow rows exist
---

# Net Flow by Cap Size — display rule

The MICRO NET FLOW tab (MicroNetFlow component in `artifacts/stock-scanner/src/pages/Dashboard.tsx`)
renders three tier sections (Nano / Micro / Small). Each section has per-tier dollar
threshold buttons and filters rows by `net_m >= minVal`.

**Rule:** A tier section must never show an empty/zero state when the scan actually
returned positive-inflow rows for that tier. Thresholds are *display filters*, not data
gates — when nothing clears the chosen threshold, fall back to rendering the largest few
positive rows with a small "below your $X+ filter — showing the largest" note. Defaults
sit at the **lowest** threshold so the broadest set shows first.

**Why:** Owner Joel is non-technical and anxious. The header counts positives as
`nano+micro+small` (raw), but sections were defaulting to the *middle* threshold and
hard-hiding everything below it. On thin mid-day Autoscale scans every positive got
filtered out, so all three sections looked blank → Joel repeatedly reported the tab as
"broken / nothing shows up." An empty section reads as a bug to him, even when data exists.

**How to apply:** Don't raise the tier defaults back to mid-tier, and don't remove the
fallback-to-top-rows behavior. If reworking this tab, preserve the invariant: data found ⇒
something visible. Note the deeper reliability cause (sparse scans) is the Autoscale→Reserved
VM migration, tracked in `stockscanner-deployment.md`; this display rule is the safety net.
