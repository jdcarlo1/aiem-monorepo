# AIEM Live Path Policy

## Default
**PAPER_ONLY.** AIEM is sold as a research / paper terminal.

## Broker adapter layer (ready to hook up later)

Package: `artifacts/stock-scanner-api/aiem_broker/`

| Provider | Status | Env |
|---|---|---|
| `paper` | **Active default** — simulated fills | `AIEM_BROKER_PROVIDER=paper` |
| `tradier` | Stub — orders not wired (market data already used elsewhere) | `TRADIER_API_TOKEN_2`, `TRADIER_ACCOUNT_ID` |
| `alpaca` | Stub — orders not wired | `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_BASE_URL` |
| `ibkr` | Stub — orders not wired | `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID` |

API:
- `GET /stock-api/aiem-broker/status` — readiness report
- `POST /stock-api/aiem-broker/paper-order` — smoke-test paper adapter only

## Hard locks before any live order
1. `simulation_lock` dual flags (`LIVE_TRADING_ENABLED` + confirmation phrase)
2. `AIEM_ALLOW_LIVE_ORDERS=1` (third deliberate switch)
3. Real `place_order()` implementation replacing the stub’s `NOT_IMPLEMENTED`

Stubs **never** send HTTP order requests — even if locks are armed.

## How to hook up later
1. Keep `AIEM_BROKER_PROVIDER=paper` until ready  
2. Implement `place_order()` in the chosen stub  
3. Set broker API credentials  
4. Arm `LIVE_TRADING_*` dual locks  
5. Set `AIEM_ALLOW_LIVE_ORDERS=1` after code review  
6. Flip `AIEM_BROKER_PROVIDER` to `tradier` / `alpaca` / `ibkr`  

## Commercial rule
Do not pitch AIEM as a live trading desk until the chosen adapter is implemented and reviewed.
Research buyers stay on paper by default.
