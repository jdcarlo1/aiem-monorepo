# AIEM Live Path Policy

## Default
**PAPER_ONLY.** AIEM is sold as a research / paper terminal.

## Broker adapter layer

Package: `artifacts/stock-scanner-api/aiem_broker/`

| Provider | Status | Env |
|---|---|---|
| `tradier_paper` | **Preferred** — paper fills at live Tradier bid/ask | `TRADIER_API_TOKEN` / `TRADIER_API_TOKEN_2`, `TRADIER_ACCOUNT_ID` |
| `paper` | Simulated fills without live quotes | `AIEM_BROKER_PROVIDER=paper` |
| `tradier` | Stub — **live** orders not wired | keep blocked |
| `alpaca` / `ibkr` | Stubs — not wired | — |

Auto-select: if `AIEM_BROKER_PROVIDER` unset and a Tradier token is present → `tradier_paper`.

API:
- `GET /stock-api/aiem-broker/status` — readiness (public reduced view; admin full report)
- `POST /stock-api/aiem-broker/paper-order` — smoke-test paper adapter (`tradier_paper`/`paper` only)
- Paper Money portfolio payload includes `broker` summary

## What tradier_paper does
- Pulls live NBBO / option chains from Tradier
- Simulates fills (buy→ask, sell→bid) with optional commission
- Maintains a local paper cash/positions ledger
- Tags `aiem_paper_trades` with `broker_provider`, `broker_order_id`, `fill_source`
- **Never** POSTs to `/v1/accounts/{id}/orders`

## Hard locks before any live order
1. `simulation_lock` dual flags (`LIVE_TRADING_ENABLED` + confirmation phrase)
2. `AIEM_ALLOW_LIVE_ORDERS=1` (third deliberate switch)
3. Real `place_order()` implementation replacing the stub’s `NOT_IMPLEMENTED`

Stubs **never** send HTTP order requests — even if locks are armed.
`tradier_paper` cannot place live orders by construction.

## How to hook up live later
1. Keep `AIEM_BROKER_PROVIDER=tradier_paper` for brokerage-like paper  
2. Implement live `place_order()` in the `tradier` stub  
3. Set broker API credentials  
4. Arm `LIVE_TRADING_*` dual locks  
5. Set `AIEM_ALLOW_LIVE_ORDERS=1` after code review  
6. Flip `AIEM_BROKER_PROVIDER` to `tradier` only then  

## Commercial rule
Do not pitch AIEM as a live trading desk until the chosen live adapter is implemented and reviewed.
Research buyers stay on paper by default — Tradier paper mode is the “almost real” path.
