---
name: Yahoo circuit breaker cooldown tuning
description: Why the breaker cooldown is 300s and what happens if it's shorter
---

## Rule
`_YF_BREAKER_COOLDOWN` must be at least 300 seconds (5 min).

**Why:** At market open (9:30 AM ET), the scheduler fires several concurrent jobs.
Yahoo throttles → breaker trips. With 60s cooldown, the half-open probe fires after
1 min, gets throttled again (Yahoo is still busy), re-trips, and the cycle repeats
all morning: trip→60s→probe→trip→... endpoints that slip through the half-open window
hang 18s+ and the user sees spinners all day.

5 min gives Yahoo enough time to fully recover. In practice, the probe at T+5min
succeeds and the breaker closes cleanly.

**How to apply:** Do not reduce this below 300s. If you observe all-day breaker
flapping in logs, increase further (600s). The admin `reset-breaker` endpoint exists
for manual recovery when you know Yahoo is healthy.
