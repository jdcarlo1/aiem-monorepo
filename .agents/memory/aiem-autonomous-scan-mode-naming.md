---
name: AIEM Autonomous Scan Mode (future roadmap term)
description: User-coined name for the future fully-independent Polygon scanning mode of the Probability Engine; use this exact term when the user references it again.
---

## What the term means

"AIEM Autonomous Scan Mode" is the user's name for a **future** evolution of the
AIEM Probability Engine (`artifacts/stock-scanner-api/aiem_probability_engine/`).

As of 2026-07-01, the Probability Engine is explicitly NOT autonomous: it only
re-ranks the ~20 candidates that `main.py`'s existing options scanner already
wrote to `ai_short_calls_log` each day (10:15 AM ET job). It never looks at the
other ~11,000 stocks Polygon covers, so it can't find a pick the upstream
scanner's own rules didn't already surface.

"AIEM Autonomous Scan Mode" = the Probability Engine pulling the full market
directly from Polygon itself (same style as the existing full-market RVOL
scanner sweep), building its OWN candidate shortlist from scratch daily, and
grading its own picks — completely independent of `ai_short_calls_log` /
the existing scanner's rules.

**Why:** the user explicitly wants this because a ranker that only sees a
pre-filtered shortlist "is not really acting autonomously" and is capped by
whatever the upstream scanner's heuristics chose to surface.

**How to apply:** if the user says "build AIEM Autonomous Scan Mode" (or asks
"what did we call that thing" / similar) in a future session, this is the
feature: a second, fully independent full-market scanning + candidate
generation pipeline for the Probability Engine, decoupled from
`ai_short_calls_log`. Not yet built as of 2026-07-01 — was deliberately
deferred until after the current re-ranking version proves itself over a
multi-week live track record (started 2026-07-01, 10:30 AM ET daily job).
