# OE Gate Structure — Why Trades Were Blocked (2026-08-06)

**Question:** Were RR gates too strict? Should we loosen gates so early-morning
movers can come through even if they fade by EOD?

**Short answer:** Risk/reward was **not** the hard blocker today. The walls
were **liquidity / missing quotes** and **dual-leg field requirements**.
We still loosened the overall gate structure so morning opportunities can
enter under a tunable profile.

---

## What production did today

| Outcome | Count | Meaning |
|---|---|---|
| `NO_TRADE_GATES` / `NO_LIQUID_CONTRACTS` | 10 | Both call+put failed liquid-chain (bid=0/ask=0 or no predicate pass); often expiry `2026-08-14` |
| `FAILED` (missing Polygon/OSS) | 5 | APPS, CRCT, FTK, TBI, SWIM — no market data to score |
| Completed trades | 0 | Checkpoint `done: 0`, `oe_trade_records` empty |

Execute window: ~09:45–10:26 AM ET — still early enough that thin names often
show **ask-only / zero bid** quotes.

---

## Gate stack (what actually blocks)

```
seed (09:40) → execute (09:45)
  ├─ missing PMD/OSS              → FAILED
  ├─ _liquid_chain (bid/ask/IV/δ) → NO_LIQUID_CONTRACTS if BOTH legs empty
  ├─ verify_options_decision_inputs hard gates (OI/vol/spread/slip/δ/PoP/DTE)
  └─ soft decision: score + margin → NO_TRADE
```

| Layer | RR involved? | Role |
|---|---|---|
| `D5_risk_reward` | scoring only (10% weight) | Never hard-rejects |
| Liquid chain | no | Early-morning zero quotes die here |
| Hard verify gates | no | OI/vol/spread/PoP/δ/DTE |
| Soft score/margin | no | Was 55 / 10 |

---

## Structural problems (beyond “RR too strict”)

1. **Both bid>0 and ask>0 required** — morning movers frequently have ask>0, bid=0.
2. **Both call AND put field sets required** — a liquid CALL with no PUT quotes
   could not become ready even for a LONG_CALL thesis.
3. **volume/OI listed as required fields** — when Tradier is late/None, readiness
   failed even though gate code intended “None = skip OI/vol check”.
4. **Strict floors** (OI≥500, vol≥100, spread≤20%, PoP≥35%, score≥55, margin≥10)
   stacked on top of thin early liquidity.

---

## Change shipped in this PR

New module: `artifacts/stock-scanner-api/aiem_options_gate_profile.py`

| Knob | `strict` (legacy) | `balanced` (**default**) | `opportunity` |
|---|---|---|---|
| min OI | 500 | 250 | 100 |
| min volume | 100 | 50 | 25 |
| max spread | 20% | 28% | 35% |
| max slippage | 15% | 20% | 25% |
| min \|δ\| | 0.20 | 0.18 | 0.15 |
| min PoP | 35% | 30% | 25% |
| min DTE | 5 | 5 | 3 |
| score / margin | 55 / 10 | 50 / 8 | 45 / 5 |
| one-sided quotes | off | **on** | on |
| single-leg ready | off | **on** | on |

Env controls:

- `OE_GATE_PROFILE=strict|balanced|opportunity`
- Per-knob overrides: `OE_GATE_MIN_OI`, `OE_GATE_SCORE_MIN`, …
- One-sided quotes synthesize `bid = ask * OE_GATE_ONE_SIDED_BID_FRAC` (default 0.85)
  so mid/spread math works without pretending there was a free fill.

Also:

- Ineligible opposite leg score forced to **0** so single-leg CALL doesn’t lose
  on phantom PUT margin.
- `volume` / `open_interest` no longer required-presence fields (still gated when present).

Tests: `artifacts/stock-scanner-api/tests/test_options_gate_profile.py`

---

## Tradeoffs / how to tune after deploy

- **More fills ≠ better expectancy.** `balanced` is the starting point.
- If still starved after redeploy: try `OE_GATE_PROFILE=opportunity` for 1–2 sessions
  and review paper P&L + fill quality.
- If junk fills spike: `OE_GATE_PROFILE=strict` or raise `OE_GATE_MIN_OI` / tighten spread.
- RR remains a soft scorer — if you want RR as a hard floor later, add an explicit
  `OE_GATE_MIN_RR` hard check (not present today).

---

## Deploy note

Gate changes live in stock-api scheduler/intel. After merge: **Publish/redeploy**
the Reserved VM (same as morning_scan SQL fix). Confirm logs show:

`[exec] … gate profile: profile=balanced …`
