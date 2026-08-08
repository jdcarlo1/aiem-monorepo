# AIEM / OE Live Path Policy

## Default
**PAPER_ONLY.** Both SKUs ship as research / paper terminals.

## Shared market data, isolated orders
- Polygon and Tradier SPY bars may be shared on the reserved VM.
- Broker adapters are **SKU-scoped** — AIEM and OE never share paper cash or live allow flags.
- Sharing quotes cannot place a cross-product order.

## Broker adapter layer

Package: `artifacts/stock-scanner-api/aiem_broker/`

| Provider | Status | Env |
|---|---|---|
| `paper` | **Active default** — simulated fills | `AIEM_BROKER_PROVIDER` / `OE_BROKER_PROVIDER` |
| `tradier` | Stub — orders not wired (market data already used elsewhere) | `TRADIER_API_TOKEN_2`, `TRADIER_ACCOUNT_ID` |
| `alpaca` | Stub — orders not wired | `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_BASE_URL` |
| `ibkr` | Stub — orders not wired | `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID` |

API:
- `GET /stock-api/aiem-broker/status` — AIEM readiness
- `POST /stock-api/aiem-broker/paper-order` — AIEM paper smoke test
- `GET /stock-api/oe-broker/status` — OE readiness
- `POST /stock-api/oe-broker/paper-order` — OE paper smoke test

## Hard locks before any live order
1. `simulation_lock` dual flags (`LIVE_TRADING_ENABLED` + confirmation phrase)
2. Per-SKU third switch: `AIEM_ALLOW_LIVE_ORDERS=1` **or** `OE_ALLOW_LIVE_ORDERS=1`
3. Real `place_order()` implementation replacing the stub’s `NOT_IMPLEMENTED`

Stubs **never** send HTTP order requests — even if locks are armed.

## How to hook up later
1. Keep `*_BROKER_PROVIDER=paper` until ready  
2. Implement `place_order()` in the chosen stub  
3. Set broker API credentials (prefer separate account IDs per SKU)  
4. Arm `LIVE_TRADING_*` dual locks  
5. Set the SKU allow flag after code review  
6. Flip that SKU’s provider env to `tradier` / `alpaca` / `ibkr`  
7. Leave Polygon shared if desired  

## Commercial rule
Do not pitch either product as a live trading desk until the chosen adapter is implemented and reviewed.
Research buyers stay on paper by default.
