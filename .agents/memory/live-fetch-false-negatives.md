---
name: Live-fetch false negatives in alerts/emails
description: Why "No liquid calls found" contradicted a $330K sweep, and the rule for any user-facing claim derived from a flaky live fetch
---

# Live-fetch false negatives in alerts/emails

The trading emails mix TWO data tiers with very different reliability:
- **Cheap, reliable stored DB signals** — far-OTM sweeps, EOD accumulation, OI
  build. These are read from Postgres and almost always present.
- **A LIVE per-pick yfinance option-chain lookup** — used to name a specific
  contract to buy (the inline scan in the Top Pick email, and `_scan_best_call`).
  This throttles constantly (`YFRateLimitError`).

**The bug the owner caught:** the Top Pick email showed "No liquid calls found.
Buy $FCEL stock instead" sitting RIGHT NEXT TO a "Far-OTM Sweep · $330K premium ·
17.6× vol/OI" layer line — a self-contradiction. Cause: the live option-chain
fetch got throttled, the exception was swallowed, the contract picker returned
None, and the code printed an ABSOLUTE "no liquid calls" — conflating "couldn't
check" with "checked, genuinely nothing there."

**Rule:** any user-facing claim derived from a flaky live fetch must distinguish
"fetch failed / couldn't check" from "fetched, genuinely empty." Count successful
reads (e.g. how many option chains actually loaded). When the live path fails but
a STORED signal already exists (the very sweep that scored Layer 7), surface that
stored signal instead of asserting absence — never recommend buying the far-OTM
lottery contract itself; recommend stock and cite the sweep as evidence.
**Why:** false absolutes destroy trust in a money-facing product and look broken
when they contradict another panel built from the reliable data tier.

**Timezone trap:** the stored sweep "last seen" timestamp serializes as **UTC**
(`+00:00`) even though the field is named `*_et`. Convert with `_ET_TZ`
(`astimezone`) before taking `.date()` for any "seen today / seen Jun X" label, or
late-evening-ET sweeps show the wrong day. See `timezone-blank-tabs.md`.

**Durable fix is the paid feed:** this patch only makes the *messaging* honest;
the underlying yfinance throttling that causes the false negative is the same
data-source ceiling — a paid full-market/options feed (Polygon) removes it. See
`scanner-data-source-ceiling.md` / `paid-data-feed-options.md`.
