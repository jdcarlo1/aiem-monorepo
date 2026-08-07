# AI pick tabs → scanner-signal ranking (2026-08-05)

## Problem

AI Trades, AI Short Calls, and AI Early Movers fed scanner data into **gpt-4o-mini**,
which then **chose the tickers**. That produced weak / invented picks disconnected
from the sophisticated Stock Scanner tabs.

## Fix

OpenAI ranking is **disabled** on all three tabs. Tickers are ranked in Python from
scanner / Layer 9 fields only. Thesis text is built from those same signals
(not an LLM inventing names).

| Tab | Ranking source |
|-----|----------------|
| **AI Trades** | Unusual calls + composite + Layer9 (VPIN/Hurst/regime) + dark pool + persistence |
| **AI Short Calls** | Existing `_score_hit` (VOI / prem / dark pool / OTM / DTE) — top 5 |
| **AI Early Movers** | Polygon movers + UC flow + conviction stack + OI buildup |

Helpers in `main.py`:
- `_deterministic_ai_trades_from_pool`
- `_deterministic_stock_buys_from_rich`
- `_deterministic_short_call_picks`
- `_deterministic_early_mover_picks`

Also expanded `_build_ai_stock_picks` with dark pool + Layer 9 signal weights.

Responses include `"ranking_mode": "scanner_signals"` and `"openai_ranked": false`.

## UI

AI Trades header now says **Scanner-ranked** (not GPT-4o / Powered by OpenAI).
