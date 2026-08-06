# Conviction Track Record — stale June-only outcomes

Date: 2026-08-06

## Problem

Tab read only `conviction_calls_outcomes`, which stopped at **2026-06-16**, while `conviction_calls_snapshot` continued through **2026-08-05**. Graded lookback was 14 days with a short Tradier window, so the UI looked “over a month old.”

## Fix

- Widen outcome fill to 90–120d; history from each ticker’s earliest pending snap.
- API joins **snapshot LEFT JOIN outcomes** so the log shows current dates even before all horizons settle.
- Live Neon backfill: **697** graded rows through **2026-08-04**, T+1 win rate ~48.9%.

Single-environment check: prod Neon only.
