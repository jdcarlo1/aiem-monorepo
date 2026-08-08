---
name: Paper fill realism gaps (16) — weekend vs live-only
description: Permanent record of 16 pre-live paper-fill gaps; honest CLOSED vs PARTIAL vs OPEN (no blanket weekend True)
---

# Paper Fill Realism — 16-gap permanent record

**Date:** 2026-08-08 (reconciled)  
**Branch:** `cursor/tradier-paper-broker`  
**Evidence:** `.local/Directive_PaperFillRealism_Weekend_RAW_2026-08-08.txt`  
**Harness:** `artifacts/stock-scanner-api/verify_paper_fill_realism_weekend.py`

## Honest scope banner (replaces `ALL_WEEKEND_ITEMS_OK=True`)

```
WEEKEND_SCOPE — NOT a blanket pass
FULLY_CLOSED (code + evidence, no sandbox token required):
  #10 Margin/BP checks
  #11 Multi-leg package AON pricing
  #12 Regulatory fee schedule (OCC/ORF/TAF/exchange)
  #13 Halt gate (historical fixture + forced block)
PARTIAL (code exists; proof incomplete or books unfinished):
  #2  Sandbox order adapter — needs #1 for real order/status/positions loop
  #3  Fill realism all books — OE mid / MARKET_ON_EXPIRY still open
  #8  Order rejects — handler proven; real sandbox reject body blocked by #1 (401)
  #9  Partial fills — fixture/P&L proven; real sandbox partial blocked by #1
OPEN (not done this pass / human / ops):
  #1  Real Tradier sandbox account+token — JOEL / EXTERNAL (only credential blocker)
  #4  HTTP 429 backoff — not implemented
  #5  ≥5 trading days sandbox + heartbeats — needs #1 + DATABASE_URL
  #6  Freeze rule config — needs Joel sign-off
LIVE-ONLY (explicitly open; do not fake):
  #7  Live brokerage connection
  #14 Assignment / early exercise
  #15 Queue position / execution latency
  #16 Real slippage under live stress

HARNESS_NOTE: verify_paper_fill_realism_weekend.py exercises the six
directive weekend checks (#8–#13 mapping). A green harness means those
unit paths ran — it does NOT mean every WEEKEND-bucket gap is closed.
```

## Source of the "16" list

No single prior file was titled “16 gaps.” The prior enumerations that together make **16** are:

**A. Pre-live gap inventory** (`.local/Directive_TradierSandbox_PreLiveVerification_2026-08-08_FAIL.md` §True work remaining) — **7 items, verbatim:**

1. Obtain a real Tradier sandbox account + sandbox token that authorizes `sandbox.tradier.com` profile/orders (current token’s sandbox profile/orders are 401).
2. Implement sandbox order adapter (`POST /v1/accounts/{id}/orders` + order status + positions) behind `AIEM_BROKER_PROVIDER=tradier_sandbox`, separate from prod.
3. Fix fill realism in all books that will go live (OE phase2, F3, asym, AIEM): Entry = ask (buys) / bid (sells to open credits); Exit = bid (longs) / ask (covers); Fees = `n_legs × contracts × 2 × $0.65` (or Tradier’s actual schedule), never flat 0.65; Remove `MARKET_ON_EXPIRY` as default fill_quality unless truly MOC/expiry.
4. Add Tradier HTTP 429 handling with logged backoff (not silent `{}`).
5. Run ≥5 trading days on sandbox with `job_heartbeats` + candidate cross-check + orphan audit SQL; archive raw outputs.
6. Freeze rule config (delta/OTM/TP/size/fees) with Joel sign-off; diff against runtime.
7. Only then consider live brokerage connection (still behind live_gate).

**B. Fill-realism edge gaps** (directive list) — **9 items, verbatim:**

8. Order rejects — submit real orders through Tradier sandbox with conditions that should reject (bad price, insufficient BP). Paste raw reject response, confirm code handles it (no silent fill assumption).
9. Partial fills — submit sandbox order sized to partially fill if possible; else simulate via sandbox order-status polling logic. Paste raw status showing partial, confirm P&L/position code handles partial correctly.
10. Margin/buying-power checks — pure math, no live dependency. Implement real BP check against account rules. Paste test showing a trade blocked when BP insufficient.
11. Multi-leg package pricing — price the whole package as one fill (not leg-by-leg independent), matching how a real spread order fills. Paste before/after on one condor showing package-level pricing.
12. Regulatory fees beyond flat $0.65 — add real fee schedule (OCC, exchange, TAF, etc.) as a rate table. Paste fee breakdown on one trade, old vs new total.
13. Halts — check Polygon/Tradier halt flag before fill; block or delay fill if halted. Paste a forced-halt test (real historical halted ticker/date) proving the block fires.
14. Assignment (early exercise on short legs) — sandbox doesn't simulate this.
15. Queue position / execution latency — sandbox fills are near-instant, not representative.
16. Real slippage under live market stress — sandbox liquidity/depth differs from live.

## Classification (reconciled)

| # | Gap (short) | Bucket | Status |
|---|---|---|---|
| 1 | Real Tradier sandbox account + token | **JOEL / EXTERNAL** | **OPEN** — rechecked 2026-08-08T17:43Z: no `TRADIER_SANDBOX_*` env var; only `TRADIER_API_TOKEN`/`_2` (identical, len=28) present. Sandbox profile → Apigee `InvalidAPICallAsNoApiProductMatchFound`; sandbox orders → `Unauthorized Account: 6YB85617`. Token works on **prod** `api.tradier.com` only. |
| 2 | Sandbox order adapter | **WEEKEND** | **PARTIAL** — code posts/parses and never assumes fill; live order/status/positions loop needs #1. |
| 3 | Fill realism all books | **WEEKEND** | **PARTIAL** — asym/paper_fills NBBO+fees done; **OE mid / `MARKET_ON_EXPIRY` still open** (agent work, not blocked solely by #1). |
| 4 | HTTP 429 backoff | **WEEKEND** | **OPEN** — not implemented this pass (agent work; not blocked solely by #1). |
| 5 | ≥5 trading days sandbox + heartbeats | **OPS** | **OPEN** — needs #1 + `DATABASE_URL` + multi-day run. |
| 6 | Freeze rule config + Joel sign-off | **WEEKEND** | **OPEN** — Joel human sign-off. |
| 7 | Live brokerage connection | **LIVE-ONLY** | **OPEN** |
| 8 | Order rejects | **WEEKEND** | **PARTIAL** — see consistency note below. |
| 9 | Partial fills | **WEEKEND** | **PARTIAL** — see consistency note below. |
| 10 | Margin/BP checks | **WEEKEND** | **FULLY_CLOSED** |
| 11 | Multi-leg package pricing | **WEEKEND** | **FULLY_CLOSED** |
| 12 | Regulatory fees | **WEEKEND** | **FULLY_CLOSED** |
| 13 | Halts | **WEEKEND** | **FULLY_CLOSED** |
| 14 | Assignment / early exercise | **LIVE-ONLY** | **EXPLICITLY OPEN** |
| 15 | Queue / latency | **LIVE-ONLY** | **EXPLICITLY OPEN** |
| 16 | Live stress slippage | **LIVE-ONLY** | **EXPLICITLY OPEN** |

### Consistency note — why #8/#9 are PARTIAL (same as #2), not FIXED

Directive text for #8/#9 requires **real Tradier sandbox** reject/partial evidence (or, for #9, “else simulate via sandbox order-status polling”).

What we have:
- **#8:** Real sandbox POST returned **401 Unauthorized** (auth failure), plus fixture-shaped reject parsing with `assumed_fill=false`. That proves the handler does not silently fill on error — it does **not** prove a brokerage-valid reject (bad price / insufficient BP) from an authorized sandbox account.
- **#9:** Fixture order-status `partially_filled` + PartialPosition P&L math. Directive allows simulation of status polling, but end-to-end sandbox partial still needs #1.

**Same dependency as #2:** without #1, #8/#9 cannot be promoted to FULLY_CLOSED. Relabeled **PARTIAL** for consistency. Fixture/handler proof is valuable and remains on record; it is not equivalent to a real sandbox reject/partial body.

## Item #1 — Joel’s blocker; what unblocks

**Confirm:** #1 (real Tradier sandbox account + sandbox-authorized token) is **on Joel, not the agent**. It is the **only credential/external blocker**.

Once #1 is resolved (sandbox profile/orders authorize), these specifically unblock:

| Unblocks | Why |
|---|---|
| **#2** Sandbox order adapter | Can complete real POST + GET order status + positions against sandbox |
| **#8** Order rejects | Can submit bad-price / insufficient-BP orders and capture real reject JSON (not 401) |
| **#9** Partial fills | Can attempt real partial (or poll real sandbox order ids); still may use status-poll sim as supplement |
| **#5** ≥5 trading-day campaign | Sandbox execution path becomes usable (still also needs `DATABASE_URL` / heartbeats) |

**Does NOT auto-close when #1 lands** (still agent or Joel work):

| Still open after #1 | Owner |
|---|---|
| **#3** OE mid / `MARKET_ON_EXPIRY` fill realism | Agent code |
| **#4** HTTP 429 backoff | Agent code |
| **#6** Freeze rule config sign-off | Joel |
| **#7, #14–#16** Live-only | Live capital / later |

So: #1 is the **only true external blocker** for the sandbox-dependent PARTIALs (#2/#8/#9) and a prerequisite for #5 — **not** the only remaining work overall.

## Paper trading cannot validate (#14–#16)

**Paper trading cannot validate assignment, queue latency, or live stress slippage until live capital is connected.** No synthetic approximation is presented as coverage.

## Code added (weekend pass)

- `aiem_broker/order_lifecycle.py`
- `aiem_broker/tradier_sandbox.py`
- `aiem_broker/buying_power.py`
- `aiem_broker/package_pricing.py`
- `aiem_broker/fee_schedule.py`
- `aiem_broker/halt_check.py`
- `verify_paper_fill_realism_weekend.py`
- `paper_fills.py` — optional regulatory fees via `TRADIER_PAPER_REG_FEES=1`
