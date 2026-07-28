---
name: Options pipeline capital_efficiency and mid-price status
description: OPT-031 capital_efficiency implemented; OPT-007 real bid/ask available from Polygon but not wired
---

## OPT-031 — capital_efficiency (IMPLEMENTED 2026-07-28)
- Column: `oe_trade_records.capital_efficiency NUMERIC(8,4)`
- Formula: `profit_target / premium_at_risk` (reward-to-risk on max capital at risk)
- Written in `capture_trade_record()` before INSERT
- 25 pre-existing rows backfilled; new rows get it automatically
- Verifier verdict: PASS=24 (was PARTIAL when NOT_IMPLEMENTED counted against it)

## OPT-007 — mid-price / bid-ask (still PARTIAL)
- `aiem_polygon_options_chain.py` parses real bid/ask per contract from Polygon
- `options_chain` dict (built at scheduler line ~1228) contains real bid/ask per strike
- The selected strike's `call_bid`/`put_bid` are computed synthetically (model × 0.88/0.93)
- Tradier chain call (line ~1517) only extracts delta/vol/OI — bid/ask discarded
- To fix OPT-007: match `call_strike`/`put_strike` against `options_chain.calls[]` entries
  and use their real bid/ask; fall back to synthetic when chain is empty or after-hours

**Why the gap persists:** Outside market hours Polygon returns bid=ask=0, making the synthetic
approximation the correct after-hours fallback regardless.
