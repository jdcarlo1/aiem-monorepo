# Phase 0 — Product Honesty (AIEM vs OE)

**Goal:** Make AIEM and OE feel like separate products in the UI, while keeping **the same password/token**.

## Auth (unchanged on purpose)
- Both terminals continue to accept the same `ADMIN_TOKEN` / operator password.
- No token split in Phase 0 (per product owner: “I still want them to both have the same password”).

## UI separation shipped

| Change | Where |
|---|---|
| Removed OE `oe_trade_records` panel from AIEM Paper Trades | `aiem-dashboard/.../PaperTrades.tsx` |
| AIEM Paper Trades labeled equity-book-only + SKU note | same |
| Removed AIEM `/aiem-paper-portfolio` from OE Positions | `oe-dashboard/.../positions.tsx` |
| OE Positions shows open **OE** trades from `oe_trade_records` | same |
| OE Status filters heartbeats/scheduler to OE-scoped jobs | `oe-dashboard/.../status.tsx` |
| OE auth copy notes shared password, separate product UI | `oe-dashboard/.../auth.tsx` |
| OE Calibration notes shared PE platform analytics | `oe-dashboard/.../calibration.tsx` |

## SKU engine / paper / broker (started)
See `docs/aiem-sales/sku-separation.md` and `live-path-policy.md`.
- Dual engines on one VM; strategy tickers `AIEM:SPY:*` vs `OE:SPY:*`
- Equity portfolio excludes strategy packages
- Per-SKU broker status/paper-order routes (live hard-blocked)

## Not in Phase 0
- Separate secrets / RBAC per SKU  
- Separate DB / deploy images  
- Removing OE pages from AIEM nav entirely (Options/Decisions still exist; books no longer mixed)

## Next (Phase 1+)
API allowlists, OE-native calibration, deploy SKUs — when ready to sell with hard isolation.
