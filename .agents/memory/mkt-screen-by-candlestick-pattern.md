---
name: Market-wide candlestick pattern screener
description: AIEM tool that scans the whole market for a candlestick pattern at once, vs the old per-ticker-only tool
---

`_mkt_screen_by_candlestick_pattern(pattern="any", min_price, min_volume, end_date, top_n, lookback)` in `artifacts/stock-scanner-api/main.py` scans all tickers in one call and returns matches sorted by dollar volume, reusing `candlestick_patterns.py`'s `detect_patterns()` per ticker (same batch-fetch-by-500 architecture as `_mkt_screen_by_indicator`).

**Why:** AIEM previously could only check candlestick patterns one ticker at a time (`mkt_candlestick_patterns`), so it couldn't answer "what stocks right now have a bullish engulfing" or find other tickers sharing a mover's pre-move candlestick signature — a real capability gap surfaced while investigating SNDK.

**How to apply:** Registered in `_build_aiem_tool_map()`, has an OpenAI function schema (enum pattern incl. "any"), and is listed in `aiem_registry.py` Phase 5 `PHASE_TOOLS`. All three must stay in sync per the existing aiem-tool-map convention. Verified live: scanned 5,247 tickers, found 10 bullish_engulfing matches in ~14s.

Gotcha: an f-string containing a nested f-string with a backslash-escaped quote (`f"...{f\"...\"}..."`) is a SyntaxError on Python 3.11 — pull the nested expression into its own variable first instead of nesting f-strings inline.
