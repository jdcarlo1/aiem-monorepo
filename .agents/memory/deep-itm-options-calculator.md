---
name: Deep ITM options-probability calculator spec resolution
description: Which of 3 pasted spec files defines "deep ITM" for the merged options-probability calculator, and how the conflict was resolved
---

Three pasted source files described "deep ITM" differently for the merged `_compute_options_probability_matrix` calculator in `artifacts/stock-scanner-api/main.py`:
1. `AEMIQuantEngine` (earliest/superseded draft) — hardcoded flat 10% ITM strike (`spot*0.90`), simplified holding-BEP approximation (`strike + premium*1.02`), broadcasts ALL results (no ≥80% filter) to a fixed 20-ticker tech/momentum watchlist.
2. `InstitutionalAEMIProcessor` (integrated) — delta-targeted deep ITM via `target_delta=0.80` (real option delta, not a flat % depth).
3. `CatalystProbabilityEngine` (integrated) — earnings/event modifiers only, no strike selection.

Resolution: kept the flat 5/10/15% depth rows (pre-existing, matches file #1's 10% target) AND added a new delta-targeted row (real `greeks.delta` from Tradier chain, nearest to 0.80) so both specs are satisfied without removing existing rows. Kept the rigorous Black-Scholes-solved holding BEP (not file #1's crude approximation) and the filtered ≥80%+liquidity-PASS Telegram alert style (not file #1's unfiltered digest-to-fixed-watchlist style), per the architect-approved design that predates file #1's re-pasting.

**Why:** user re-pasted file #1 later in the same feature session to double-check "deep ITM" was implemented; the three files are NOT one coherent spec — they're sequential drafts/directions, so literal fidelity to the oldest draft (file #1) was deliberately not chosen where it conflicted with the two later, more rigorous files.

**How to apply:** if the user again asks about deep-ITM/watchlist/broadcast-style differences here, the open question is whether they want the Telegram job's watchlist narrowed to file #1's fixed 20-ticker list (NVDA/TSLA/AAPL/AMD/MSFT/AMZN/META/GOOGL/NFLX/PLTR/SMCI/INTC/AVGO/QCOM/COIN/MARA/BABA/MU/ARM/PANW) instead of the app's current broader `WATCHLIST_DEFAULT`.
