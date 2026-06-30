---
name: AIEM Master Part 1 architecture
description: Consolidated staleness filter + Wall Street brain module, stubs wired to real Polygon/DB data
---

## Rule
`aiem_master_part1.py` at project root is the canonical Layer A+B module.
It supersedes the split `staleness_filter.py` + `aiem_verification_and_trading_brain.py`.

## Why
Previous split modules had NotImplementedError stubs; master file has all stubs wired to
Polygon snapshot/news API + polygon_market_daily DB. Single import, one source of truth.

## How to apply
- `aiem_standalone_scanner.py` imports `evaluate_signal_with_data` + `apply_wall_street_pattern_with_data` from `aiem_master_part1` with legacy fallback
- Both functions accept pre-fetched `history` (list[dict]) + `news` (list[dict]) to avoid duplicate Polygon calls
- HMAC verification (Section 3) confirms AIEM loaded the file; 6 questions, CONVICTION_THRESHOLD=70
- `NEWS_SOURCE_DELTA` maps Polygon publisher names to conviction deltas (SEC_8K=+5, REDDIT=-15)
- Delisting/SPAC/reverse-split detected from news text keywords; PIPE_FADE from `signal['pipe_price']`
