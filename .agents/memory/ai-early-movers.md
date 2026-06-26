---
name: AI Early Movers isolated system
description: Architecture and daily cycle for the experimental 🧠 AI EARLY MOVERS tab — fully separate from all other tabs.
---

# AI Early Movers — Isolated Experimental System

## What it is
Scans 8,000+ US stocks via Polygon grouped daily every morning. Finds stocks up 2.5%+ from open on day 1 or 2 of a new move. AI picks 5 best (BUY_CALL or BUY_STOCK). Completely isolated — own endpoint, own DB tables, own cache, own scheduler jobs, own miss-detection and feedback loop.

## Why isolated
User decision: this is an experiment running in parallel for 60+ days before any sizing decisions. Must NEVER interfere with any other tab. If it fails, no other tab is affected.

## Full automatic daily cycle (weekdays only)
1. **10:20 AM ET** — `ai_early_movers` scheduler job → `GET /stock-api/ai-early-movers?force=1` → `_bg_aiem()` runs in daemon thread → fetches Polygon grouped daily → finds movers → AI picks 5 → saves to `ai_early_movers_log`
2. **4:50 PM ET** — `aiem_miss_detection` scheduler job → `_detect_aiem_misses()` daemon thread → fetches today's Polygon grouped daily → cross-references against `ai_early_movers_log` → finds 5%+ movers AI skipped → saves top 50 to `ai_early_movers_misses`
3. **Next 10:20 AM** — `_get_aiem_feedback()` reads yesterday's `ai_early_movers_misses` → injects into AI prompt as "LEARNING FEEDBACK" section → AI learns from patterns it overlooked

## Tables
- `ai_early_movers_log` — today's picks (ticker, rec_type, strike, expiry, day_ret, confirmed_2d, etc.)
- `ai_early_movers_misses` — stocks that moved 5%+ that AI didn't pick (miss_date, ticker, day_ret, has_uc)

## Key separation rules
- **Why:** User explicitly wants no cross-contamination with existing tabs (unusual calls, high conviction, etc.)
- `_bg_aiem()` is the ONLY function that calls `_get_aiem_feedback()` — feedback never bleeds into _bg_aisc()
- `_detect_aiem_misses()` queries `ai_early_movers_log` — NOT `ai_short_calls_log`
- Cache: `app._aiem_cache` / `app._aiem_cache_ts` / `app._aiem_scanning` — all separate from `app._aisc_*`
- Frontend tab id: `aiearlymovers`, component: `AIEarlyMoversTab` — separate from `AIShortCallsTab`

## Polygon fetch pattern
Uses `urllib.request.urlopen` (NOT `requests`) to bypass the Yahoo circuit-breaker patch. URL: `https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true&include_otc=false&apiKey={key}`

## Signal thresholds
- Min day_ret from open: 2.5% (miss detection gate is 5%)
- Min volume: 80,000
- Price range: $3–$600
- "2-DAY CONFIRMED" = also up prev day ≥1% from open
- Top 80 movers fed to AI; AI picks 5

## AI Short Calls (unchanged)
_bg_aisc() is restored to original unusual-calls behavior: loads from UC cache/DB → momentum filter (Tradier + Polygon 5d) → enriches → original _enrich_line format → options analyst prompt → BUY_CALL only.
