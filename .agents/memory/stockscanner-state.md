---
name: StockScanner AI product state
description: Current state of StockScanner AI (nclexai.org/stock-scanner/) — modules, endpoints, key files, scheduler, deployment notes
---

## Product URL
https://nclexai.org/stock-scanner/

## Key files
- artifacts/stock-scanner-api/main.py — 45,127 lines; Flask on port 5050
- artifacts/stock-scanner-api/ — all modules live here

## Completed hardening modules (all 5/5 PASS as of June 28 2026)
- reddit_sentiment.py, candlestick_patterns.py, economic_calendar.py
- regime_macro_patch.py, model_swap_patches.py
- regime_detector.py — bridge from regime_macro_patch → market_regime_overlay.combine_regime_votes()
- model_versions table: 14 columns (original ML pipeline cols + version_label, deployed_at, is_active, rolled_back_at)

## Scheduler config (lines 1748-1751)
max_workers=4, coalesce=True, max_instances=1, misfire_grace_time=600 — CORRECT, do not change

## Circuit breaker pattern
_yf_breaker_open() = global breaker (open/closed/half-open), fast-fail at endpoint entry
_yahoo_breaker = token bucket rate limiter, used inside per-ticker loops
Both already present in all TIMEOUT endpoints as of June 28 2026

## Smoke test baseline (June 28 2026, Sunday, market closed)
33 endpoints tested → 33 OK / 0 FAIL / 0 TIMEOUT
"call-intent, vol-crush, max-pain, options-intent" = NOT Flask routes; UI labels in composite-score response

## Stripe setup
- Live subscriptions enabled (via Stripe integration)
- Email-only alerts (no Twilio)

## Scheduler times
See owner-email-scheduler.md and market-brief-email.md for email scheduling details
