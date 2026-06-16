---
name: Finviz data source
description: Finviz replaced Barchart for small/micro cap mover discovery; correct ticker regex pattern and known rate-limit interactions
---

## Rule
Barchart's `proxies/core-api` endpoint is IP-blocked on this Replit server — returns `{"count":0,"total":0,"data":[]}` for ALL lists regardless of XSRF auth. Do not attempt to re-add Barchart.

Finviz is the permanent replacement in both `_get_microcap_tickers()` and `morning_inflows()`.

## Correct Finviz ticker regex
The screener link format is `stock?t=TICKER&ty=...` — NOT the old `screener.ashx?v=1&...ticker=TICKER` format.

**Working pattern:** `r'stock\?t=([A-Z]{1,5})&'`

The old pattern `r'screener\.ashx\?v=1&[^"]*ticker=([A-Z]{1,6})'` returns 0 matches.

## Yahoo Finance rate limiting
When the OI snapshot (311 tickers × options chains) runs concurrently with morning_inflows scoring, Yahoo blocks all requests from this server for hours. `fast_info`, `download()`, and the v7/v8 quote APIs all return rate-limit errors simultaneously.

**Why:** The OI snapshot at startup + scheduled times hammers Yahoo. Any manual trigger during debugging compounds the block.

**How to apply:** Never trigger OI snapshots manually during active debugging sessions. Scheduled jobs run sequentially and stay within rate limits. Manual triggers stack on top of scheduled calls and blow the limit.

## Finviz screener filter strings that work
- `cap_micro,sh_opt_option,ta_change_u5` — micro-cap + options + up 5%+
- `cap_small,sh_opt_option,ta_change_u5` — small-cap + options + up 5%+
- `cap_micro,sh_opt_option` — micro-cap + options (any move)
- `cap_small,sh_opt_option` — small-cap + options (any move)
- `cap_micro,ta_change_u10` — micro-cap up 10%+
- `cap_small,ta_change_u10` — small-cap up 10%+
