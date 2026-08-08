# AIEM / OE Live Path Policy

## Product doctrine
**Fully autonomous.** The engine finds strategies and executes them without
per-trade human approval — that is why it was built.

See: `docs/aiem-sales/autonomous-desk-doctrine.md`

## Default today
**PAPER_ONLY (autonomous paper).**  
OE/AIEM auto-find and auto paper-fill. You do not approve each trade.
Live brokerage orders remain locked until deliberately armed.

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

When those locks are armed later, execution stays **autonomous** (engine → broker).
There is no plan to insert Approve/Reject for each live order.

## How to hook up later (still autonomous)
1. Keep `AIEM_BROKER_PROVIDER=paper` until ready  
2. Prove autonomous paper completes trades with honest P&L  
3. Implement `place_order()` in the chosen stub (broker **paper** account first)  
4. Wire risk: daily loss, kill-switch flatten, position reconcile  
5. Set broker API credentials  
6. Arm `LIVE_TRADING_*` dual locks  
7. Set `AIEM_ALLOW_LIVE_ORDERS=1` after code review  
8. Flip `AIEM_BROKER_PROVIDER` to `tradier` / `alpaca` / `ibkr`  

Human role after unlock: monitor kill switch / caps — **not** pick daily trades.

## Owner go-live track (2026-08-08)
See `Directive_Tradier_Autonomous_GoLive_OwnerStrategies_2026-08-08.md`.

- Product target = **all owner-enabled strategies** on Tradier (multi-leg included), autonomous.
- **F3 is not** the owner’s go-live proving pattern (did not hold up in their backtests).
- Do not conflate “Tradier can run the catalog” with “every optimistic backtest survives ask/bid costs.”
- Tradeability skips (wide spreads) keep multi-leg strategies enabled without forcing bad fills.

## Commercial rule
Do not pitch as a live autonomous brokerage desk until the chosen adapter is
implemented and reviewed. Research buyers stay on autonomous paper by default.
