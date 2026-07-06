---
name: AIEM Process module — gap sweet spot indicator
description: aiem_process.py autonomous scanner, its isolation architecture, and the S1b gap sweet spot (15-25%) selected from a 4-tier backtest as the highest-accuracy indicator.
---

# AIEM Process — Gap Sweet Spot Indicator (S1b)

## What the module is
`artifacts/stock-scanner-api/aiem_process.py` — completely isolated autonomous scanner.
- Hunts low-float nano-cap gappers: $1–$20 price, float <20M shares, gap >2%, premarket vol >50K
- Own daily cycle: warmup 6:55 AM → premarket scan 7:00–9:15 AM (every 15 min) → open watcher 9:30–10:30 AM (every 5 min) → grade outcomes 4:30 PM → nightly learn 5:00–6:00 PM
- Writes to its own table: `aiem_process_predictions` — zero overlap with main scanner tables
- Alerts go directly to Telegram

## Backtest result that prompted the change
At 9:30 open: **81.7% WR, +18.9% avg return** on high-confidence picks (score ≥72).
By mid-morning (VWAP entry): drops to 54.2% WR — edge is almost entirely in the opening print.

## The 4-tier gap backtest
The previous session backtested all 4 gap tiers against `aiem_process_predictions` outcomes:
- gap_small (2–5%): lower WR — not selected
- gap_moderate (5–10%): lower WR — not selected
- gap_large (10–15%): lower WR — not selected
- **gap_sweet_spot (15–25%): 85% WR, 864W/153L, 13 months, avg win +18.1%, median +15.4% — SELECTED**

## What was implemented
Signal **S1b** added to `aiem_score_ticker()` in `aiem_process.py`:
```python
# S1b — Gap sweet spot bonus (15–25% = 85% WR, highest validated tier)
_add("gap_sweet_spot", 5, 15 <= gap_pct < 25,
     f"Sweet spot gap {gap_pct:.1f}% (85% WR zone, +18% avg)")
```
+5 bonus points when premarket gap is in the 15–25% range. Gaps >25% excluded — tend to be pump-and-dump setups that reverse at open.

**Why:** 15–25% is the validated sweet spot for this specific universe (low-float nano-caps). Very large gaps >25% are noise/pump; small gaps <15% don't have enough momentum.

## How to refer to this in future sessions
Say: "AIEM Process module" or "aiem_process.py" or "nano-cap gap sweet spot" or "S1b indicator"
