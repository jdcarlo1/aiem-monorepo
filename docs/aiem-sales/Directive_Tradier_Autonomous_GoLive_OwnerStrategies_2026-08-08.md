# Directive_Tradier_Autonomous_GoLive_OwnerStrategies_2026-08-08

**Status:** AUTHORITATIVE for go-live planning  
**Supersedes:** any F3-first Tradier readiness sketch from the same session  
**Doctrine:** `docs/aiem-sales/autonomous-desk-doctrine.md`  
**Live locks:** `docs/aiem-sales/live-path-policy.md`

---

## Owner correction (read first)

The owner is **not** using F3 SPY 0DTE as a production pattern.
F3 did not hold up in their backtesting. Do **not** center go-live, demos,
or “first strategy on Tradier” work on F3.

The product was designed for what the owner wants:

> Fully autonomous find → decide → execute → grade across **their** strategies  
> on paper now, and the **same** autonomy on Tradier later — **without**  
> per-trade Approve/Reject.

That includes the OE catalog strategies they care about (condors, butterflies,
verticals, risk reversals, etc.) and Pattern Lab equity patterns (Gap Fill, ORB)
where those are part of their book. **All of those are in scope for Tradier.**

Earlier “F3 is the safest first bet” language was a **liquidity/cost triage**,
not a product redesign and not a ban on the rest of the catalog. Triage does
not override owner strategy selection.

---

## Two different statements (do not conflate)

| Statement | Meaning |
|---|---|
| **Designed for all your strategies on Tradier** | YES. Broker adapter + OE catalog + autonomous execute path are built so the engine can run the strategies you enable — multi-leg included — without you babysitting each trade. |
| **Every backtested P&L survives realistic fills** | NOT automatically. Honest ask/bid + fees + slippage can shrink or flip some published numbers. That is a **tradeability gate**, not “you can’t use that strategy on Tradier.” |

**Tradier capability ≠ edge survival.**  
Capability: multi-leg orders, quotes, accounts — wire and use it.  
Edge survival: prove under Version B fills (ask in / bid out / fees / dual slip)
before sizing up. Rejecting a *thin quote today* is not disabling the strategy
forever.

---

## In-scope strategy set (owner book)

### A. OE / options catalog (primary owner intent)
Any strategy the owner enables from the OE registry / their saved set, including
but not limited to:

- `LONG_CALL_CONDOR`, `LONG_PUT_CONDOR`
- `LONG_CALL_BUTTERFLY`, `LONG_PUT_BUTTERFLY`, `IRON_BUTTERFLY`
- `IRON_CONDOR`
- Verticals: `BULL_CALL_SPREAD`, `BEAR_PUT_SPREAD`, `BEAR_CALL_SPREAD`, `BULL_PUT_SPREAD`
- `RISK_REVERSAL` / reverse
- Other catalog ids the owner enables (`JADE_LIZARD`, calendars, diagonals, etc.)

Names from owner language that map or need mapping:

| Owner name | Catalog / action |
|---|---|
| long call/put condor | `LONG_CALL_CONDOR` / `LONG_PUT_CONDOR` |
| call/put butterfly | `LONG_CALL_BUTTERFLY` / `LONG_PUT_BUTTERFLY` |
| bullish risk reversal | `RISK_REVERSAL` |
| put_ladder / narrow_wing_butterfly | **not in catalog today** — add as first-class strategies if still in the owner book, do not silently drop |

### B. Pattern Lab equity (if still in owner book)
- `GAP_FILL`
- `ORB`

### C. Explicitly out of owner go-live path
- **`F3_SPY_0DTE`** — do not use as proving ground, default live card, or Tradier pilot unless the owner reinstates it.

---

## Design target on Tradier (unchanged)

```
signal / chain select → decide (gates) → place_order(Tradier)
  → fill / reconcile → grade → learn
```

Human role after unlock: **arm/disarm, caps, kill switch, post-hoc review** —  
not pick the daily trade.

Same autonomy as paper. No HITL Approve/Reject queue.

---

## Go-live readiness checklist (owner strategies)

Work top-down. Do not arm live cash until the row is green.

### Phase 0 — Strategy inventory (owner-true)
- [ ] List every strategy the owner wants live on Tradier (options + equity)
- [ ] Map each to catalog id or create missing ids (`put_ladder`, `narrow_wing_butterfly`, …)
- [ ] Mark each: legs, debit/credit, defined risk Y/N, max loss formula
- [ ] Confirm F3 is **disabled** for this go-live track

### Phase 1 — Honest paper on **those** strategies (not F3)
For each enabled strategy, autonomous paper must:

- [ ] Enter buys at **ASK** (one-sided → `ONE_SIDED_ASK`)
- [ ] Exit sells at **BID** (one-sided → `ONE_SIDED_BID`; intrinsic only at true expiry settle)
- [ ] Charge `paper_round_trip_fees(n_legs, qty) = 0.65 * legs * qty * 2`
- [ ] Dual adverse half-spread slippage (engine: `quantity = qty * n_legs`)
- [ ] Archive per trade: `entry_bid/ask`, `exit_bid/ask`, `fill_quality`,
      `exit_fill_quality`, `entry_slippage_est`, `exit_slippage_est`, `fees_est`, `realized_pnl`
- [ ] Complete full lifecycle: open → manage/exit → grade (no stuck “selected” rows)
- [ ] Report Version A (old/as-published assumptions) vs Version B (realistic fills)
      on the **same** trade list when historical quotes exist; else label **CANNOT VERIFY**
      — never invent synthetic spreads

**Pass rule (per strategy):** after N completed paper trades (owner sets N; default suggest ≥ 30),
Version B expectancy and max DD are acceptable to the owner under their size caps.
Failing the pass rule means **keep on paper / tighten liquidity gate**, not “delete from Tradier roadmap.”

### Phase 2 — Liquidity / tradeability gate (keeps multi-leg alive)
Before sending an order (paper or live), the engine should be able to refuse a
*specific quote* without disabling the strategy:

- [ ] Require two-sided bid/ask on every leg (or package) at decision time
- [ ] Estimate round-trip cost = fees + entry half-spread$ + exit half-spread$
- [ ] Estimate max theoretical gain (defined-risk) or owner-configured target credit
- [ ] **Reject this opportunity** if `round_trip_cost > owner_threshold * max_gain`
      (owner sets threshold; starting suggestion 0.35–0.50 for multi-leg)
- [ ] Log rejection as `TRADEABILITY_SKIP` — distinct from “no edge” and from strategy off

This is how condors/butterflies stay in the system: trade them when the market
is liquid enough; skip when the spread taxes the structure.

### Phase 3 — Tradier adapter (broker paper first)
- [ ] Implement real `TradierBroker.place_order()` (replace stub `NOT_IMPLEMENTED`)
- [ ] Support **equity** orders (Gap Fill / ORB if enabled)
- [ ] Support **single-leg** option orders
- [ ] Support **multi-leg / spread** orders for catalog structures (required for owner book)
- [ ] Limit orders preferred for options (buy limits at/inside ask; sell at/inside bid)
- [ ] Order reconcile: broker id → local `oe_trade_records` / Pattern Lab ledger
- [ ] Position sync + flatten path for kill switch
- [ ] Run against **Tradier paper/sandbox account** before any cash account

Env (already documented): `TRADIER_API_TOKEN_2`, `TRADIER_ACCOUNT_ID`,
`AIEM_BROKER_PROVIDER=tradier`.

### Phase 4 — Risk locks (fail closed)
- [ ] Daily loss cap
- [ ] Per-strategy and portfolio size caps
- [ ] Kill switch flatten (all open broker positions the engine owns)
- [ ] `simulation_lock` dual flags + `AIEM_ALLOW_LIVE_ORDERS=1` only after review
- [ ] No silent fallback to mid fills in live mode

### Phase 5 — Arm live (still autonomous)
- [ ] Owner reviews paper Version B track record for **enabled** strategies
- [ ] Flip provider to `tradier` on broker **paper** → soak
- [ ] Then cash account only if soak is clean
- [ ] Human monitors caps / kill switch — does **not** approve each trade

---

## What “success on Tradier” means for this owner

1. **All enabled owner strategies** can be selected and ordered by the engine on Tradier.  
2. Autonomy is intact (no per-trade human queue).  
3. Thin/wide markets get **skipped by gate**, not forced mid fills.  
4. Graded P&L uses real fill economics — no return to optimistic mid fantasy.  
5. F3 is irrelevant unless the owner brings it back.

---

## Explicit non-goals

- Do not rebuild the product as F3-only.
- Do not insert Approve/Reject prop-desk UX.
- Do not silently drop catalog strategies because a proof condor was expensive on one thin quote.
- Do not claim live autonomous brokerage until Phase 3–4 are done.
- Do not re-tune strategy parameters under this directive (report / wire / gate only),
  unless a later directive says otherwise.

---

## Immediate next engineering steps (when implementation is approved)

1. Owner strategy enable-list (Phase 0) — freeze the set  
2. Paper path: force those strategies through honest fill archive (Phase 1)  
3. Tradeability skip gate (Phase 2)  
4. Tradier multi-leg `place_order` on broker paper (Phase 3)  
5. Risk + arming (Phase 4–5)

---

## One-line product truth

**Yes — this is designed so you can run your strategies on Tradier autonomously.**  
F3 is not the plan. Honest fills and liquidity gates are how the rest of the book
stays alive without lying about P&L.
