---
name: Nano-cap morning two-stage conviction system
description: Design, sizing rule, universe, scoring, scheduler/auth conventions for the daily nano-cap morning BUY workflow in stock-scanner-api/main.py
---

# Nano-cap morning two-stage conviction system

A daily morning workflow that surfaces low-float nano-cap names showing multi-day
stealth accumulation, then confirms them intraday before issuing a BUY list. Lives
in `artifacts/stock-scanner-api/main.py` (EDIT A block ~line 3336+).

## The two-stage workflow (deliberate)
- **9:35 AM — "get ready" watchlist** (Stage A + watch email): top 20 by multi-day
  stealth accumulation. This is NOT a buy — it's a heads-up so the owner is ready.
- **9:45 AM — confirmed BUY list** (Stage B intraday confirm + buy email): re-checks
  the first 15 min of volume / VWAP / anti-pump, then issues the actual buys.
- Stages run as separate scheduler slots; do not collapse them into one.

## Sizing rule — the part that's easy to get wrong
- Size each buy at **$500 WORTH per name**: `shares = int(500 / entry)`. NOT 500 shares.
  `_NANO_DOLLARS_PER_BUY = 500`. There is intentionally NO `_NANO_SHARES_PER_BUY`.
- **Why:** user explicitly chose **20 names @ $500** over 10 @ $1000. Breadth wins for
  fat-tailed nano payoffs — more shots at a rare multi-bagger beats bigger single bets.
- Entry = the 9:45 price; stop = `entry * 0.95` set immediately (`_NANO_STOP_PCT = 0.05`).
  Winners ride (no profit target). Stage D grades forward from `pick_date + 1` (day 0 excluded).

## Universe
- Must include LOW FLOAT names. Filter:
  `cap_nano, sh_float_u20, sh_price_o0.5, sh_avgvol_o20` (float < 20M shares).
- Sourced via Finviz (Barchart is permanently IP-blocked — see finviz-data-source.md).

## Scoring (Stage A conviction, 0-100)
- accumulation 40 + steadiness 25 + volume 20 + momentum 15.
- **Accumulation is magnitude-weighted, not just sign.** It uses `flow_ratio =
  net_flow / dollar_vol` (signed flow as a share of total dollar volume, -1..1) as the
  dominant driver (22 of 40), with up-close consistency secondary (18). **Why:** steady
  drift on no real net flow must NOT score like genuine stealth accumulation — an early
  version only checked `net_flow > 0` + up-day ratio and ignored intensity.

## Guards / conventions (do not regress)
- **Never let a data outage wipe a good list.** `_run_nano_morning_ranking` only
  DELETE+replaces today's candidates if `len(results) >= max(25, ucount//10)`. `_score`
  returns a row for every name with usable history regardless of conviction, so a thin
  count means yfinance/Finviz failed, not "few qualified." Below the floor → keep prior rows, return.
- **Side-effect POST routes are fail-closed gated.** `/stock-api/nano-morning/{run-ranking,
  send-watch,send-buy,grade}` require `ADMIN_TOKEN` env set AND matched via `X-Admin-Token`
  header or `?token=` (`_nano_admin_ok()`). **Why:** they send the owner real buy/watch
  emails; the live site is public. The GET routes (candidates, picks) stay open (power the UI).
  The scheduler calls the underlying functions DIRECTLY (not over HTTP), so it is unaffected
  whether or not ADMIN_TOKEN is set — manual HTTP triggering is the only thing that needs the token.
- Stage A uses a non-blocking `app._nano_morning_lock` so overlapping triggers don't double-run.
