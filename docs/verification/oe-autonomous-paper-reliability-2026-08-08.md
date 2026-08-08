# OE Autonomous Paper Reliability Hardening (2026-08-08)

**Goal:** Engine finds setups and **auto paper-fills without human approval**,
then **completes/grades** trades with honest fill math.

**Not in scope:** HITL approve/reject queue, live broker unlock.

---

## What was broken

1. **0 completed trades** — morning liquidity + Tradier dual-sided-only fallback
   starved `NO_LIQUID_CONTRACTS` even under `balanced` one-sided profile.
2. **Wrong expiry** — alerts used hardcoded `today+9` instead of selected contract.
3. **Optimistic fills** — entry at mid; grading used bid; slippage mixed % vs $.
4. **Worthless expiry counted as breakeven** in phase3 root-cause.
5. **Silent missing trade records** — TRADE could DONE without `oe_trade_records`.

---

## Fixes shipped

| Area | Change |
|---|---|
| Gates / liquidity | Tradier fallback accepts one-sided asks (same as Polygon `_liquid_chain`) |
| Gates / DTE | Chain fetch + Tradier window use `OE_GATE` `min_dte` |
| Enrichment | Tradier OI/vol fetched for **selected** `call_exp`/`put_exp`; missing → `None` (not `0`) |
| Expiry | Alert `expiry`/`dte` from selected contract |
| Paper fill | Long buys fill at **ask**; `fill_quality=ASK` / `ONE_SIDED_ASK` |
| Costs | `slippage_est` in **dollars** (half-spread×100); fees $0.65 |
| Exit P&L | `(exit-entry)×100×qty − fees − slip` via `aiem_options_paper_fill` |
| Grading | SELECT `alert_date`; `EXPIRED_WORTHLESS` → `EXPIRED_LOSS` in phase3 |
| Capture | Loud error if TRADE has no trade_record; exceptions no longer debug-swallowed |
| Build tag | `_OE_LIQ_BUILD=tradier-onesided-askfill-v3` |

Modules:
- `artifacts/stock-scanner-api/aiem_options_paper_fill.py` (new)
- `artifacts/stock-scanner-api/aiem_options_scheduler.py`
- `artifacts/stock-scanner-api/aiem_options_phase2.py`
- `artifacts/stock-scanner-api/aiem_options_pipeline.py`
- Tests: `tests/test_options_paper_fill.py`

---

## Deploy checklist

1. Publish/redeploy **stock-api** (scheduler + intel on Reserved VM).
2. Confirm exec logs: `gate profile: profile=balanced` and `oe_liq_build=tradier-onesided-askfill-v3`.
3. Next session: expect non-zero `oe_trade_records` when candidates pass gates.
4. After expiries: `grade_options_outcomes` should close OPEN alerts and set
   `realized_pnl` with ask-based entry.

## Still paper-only

Live orders remain locked (`simulation_lock` / broker stubs). This hardening is
for **autonomous paper reliability**, not live capital.
