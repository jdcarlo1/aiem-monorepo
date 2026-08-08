---
name: Paper fill realism gaps (16) — weekend vs live-only
description: Permanent record of 16 pre-live paper-fill gaps; weekend 1-6 built 2026-08-08; live-only 7-9 explicitly open until live capital
---

# Paper Fill Realism — 16-gap permanent record

**Date:** 2026-08-08  
**Branch:** `cursor/tradier-paper-broker`  
**Evidence:** `.local/Directive_PaperFillRealism_Weekend_RAW_2026-08-08.txt`  
**Harness:** `artifacts/stock-scanner-api/verify_paper_fill_realism_weekend.py`

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

**B. Fill-realism edge gaps** (this directive’s numbered list) — **9 items, verbatim:**

8. Order rejects — submit real orders through Tradier sandbox with conditions that should reject (bad price, insufficient BP). Paste raw reject response, confirm code handles it (no silent fill assumption).
9. Partial fills — submit sandbox order sized to partially fill if possible; else simulate via sandbox order-status polling logic. Paste raw status showing partial, confirm P&L/position code handles partial correctly.
10. Margin/buying-power checks — pure math, no live dependency. Implement real BP check against account rules. Paste test showing a trade blocked when BP insufficient.
11. Multi-leg package pricing — price the whole package as one fill (not leg-by-leg independent), matching how a real spread order fills. Paste before/after on one condor showing package-level pricing.
12. Regulatory fees beyond flat $0.65 — add real fee schedule (OCC, exchange, TAF, etc.) as a rate table. Paste fee breakdown on one trade, old vs new total.
13. Halts — check Polygon/Tradier halt flag before fill; block or delay fill if halted. Paste a forced-halt test (real historical halted ticker/date) proving the block fires.
14. Assignment (early exercise on short legs) — sandbox doesn't simulate this.
15. Queue position / execution latency — sandbox fills are near-instant, not representative.
16. Real slippage under live market stress — sandbox liquidity/depth differs from live.

## Classification

| # | Gap (short) | Bucket | Status 2026-08-08 |
|---|---|---|---|
| 1 | Real Tradier sandbox account + token | **WEEKEND-BLOCKED / EXTERNAL** | OPEN — sandbox still 401 with brokerage token; cannot complete without Joel sandbox credentials. Does not fit pure WEEKEND-build or LIVE-ONLY: it is a **credential prerequisite**. |
| 2 | Sandbox order adapter | **WEEKEND** | PARTIAL — `tradier_sandbox.py` posts + parses; never assumes fill. Full status/positions loop still needs working sandbox token (#1). |
| 3 | Fix fill realism all books (ask/bid/fees/fill_quality) | **WEEKEND** | PARTIAL — asym/paper_fills NBBO+fees done in PR #61; OE `MARKET_ON_EXPIRY` / mid paths still outstanding in phase2. |
| 4 | HTTP 429 backoff | **WEEKEND** | OPEN — not implemented this pass. |
| 5 | ≥5 trading days sandbox + heartbeats | **LIVE-ONLY / OPS** | OPEN — requires sandbox (#1) + `DATABASE_URL` + multi-day run. Not faked. |
| 6 | Freeze rule config + Joel sign-off | **WEEKEND** | OPEN — needs Joel sign-off (human). |
| 7 | Live brokerage connection (behind live_gate) | **LIVE-ONLY** | OPEN — do not connect. |
| 8 | Order rejects (directive weekend #1) | **WEEKEND** | FIXED (handler) — raw sandbox POST **401** captured; fixture rejects parsed; `assumed_fill=false`. True sandbox reject body still blocked by #1. |
| 9 | Partial fills (directive weekend #2) | **WEEKEND** | FIXED (sim) — order-status poll fixture `partially_filled` exec_qty=4/10; P&L on filled qty only ($120). No real sandbox partial without #1. |
| 10 | Margin/BP checks (directive weekend #3) | **WEEKEND** | FIXED — `buying_power.py`; trade blocked at BP=$100 need=$453; live acct OBP=$0 also blocks. |
| 11 | Multi-leg package pricing (directive weekend #4) | **WEEKEND** | FIXED — `package_pricing.price_package_atomic` AON + single `fill_id`; condor before/after evidence. |
| 12 | Regulatory fees (directive weekend #5) | **WEEKEND** | FIXED — `fee_schedule.py` OCC/ORF/TAF/exchange; old $2.60 → new $2.914 on 4-lot. |
| 13 | Halts (directive weekend #6) | **WEEKEND** | FIXED — historical GME 2021-01-28 fixture + forced halt blocks package fill. |
| 14 | Assignment / early exercise | **LIVE-ONLY** | **EXPLICITLY OPEN** — paper/sandbox cannot validate. Do not build synthetic coverage. |
| 15 | Queue position / execution latency | **LIVE-ONLY** | **EXPLICITLY OPEN** — sandbox/paper fills are near-instant; not representative. |
| 16 | Real slippage under live stress | **LIVE-ONLY** | **EXPLICITLY OPEN** — sandbox liquidity/depth ≠ live. |

### Why some don't fit either WEEKEND or LIVE-ONLY cleanly

- **#1** is an external credential dependency (WEEKEND-BLOCKED).
- **#5** is multi-day ops requiring DB + sandbox (ops gate, not a code weekend unit).
- **#6** requires human Joel sign-off.

## Paper trading cannot validate (#14–#16)

State plainly: **paper trading cannot validate assignment, queue latency, or live stress slippage until live capital is connected.** No synthetic approximation is presented as coverage.

## Code added this pass

- `aiem_broker/order_lifecycle.py`
- `aiem_broker/tradier_sandbox.py`
- `aiem_broker/buying_power.py`
- `aiem_broker/package_pricing.py`
- `aiem_broker/fee_schedule.py`
- `aiem_broker/halt_check.py`
- `verify_paper_fill_realism_weekend.py`
- `paper_fills.py` — optional regulatory fees via `TRADIER_PAPER_REG_FEES=1`
