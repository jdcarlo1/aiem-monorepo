---
name: Tradier paper broker (brokerage-like paper fills)
description: tradier_paper fills at live NBBO; tradier live adapter wired but live_gate locked; URLs via TRADIER_API_BASE
---

# Tradier paper / live switch prep (2026-08-08)

## Providers
- `AIEM_BROKER_PROVIDER=tradier_paper` (default when token set) — NBBO paper fills, never POSTs orders
- `AIEM_BROKER_PROVIDER=tradier` — real `TradierBrokerAdapter` (submit/status/cancel) in `aiem_broker/tradier_live.py`
- Live path is **blocked** unless simulation_lock dual flags AND `AIEM_ALLOW_LIVE_ORDERS=1`

## URL config
Single source: `aiem_broker/tradier_config.py` → `TRADIER_API_BASE` (default `https://api.tradier.com`).
No other `.py` files hardcode the prod URL.

## live_order_sent
Behavioral flag from `live_gate.live_order_sent(http_order_posted=...)`.
True only if gate permits AND HTTP order was posted. Paper paths pass `False`.

## Risk env (defaults = current paper numbers)
`ASYM_RISK_USD=500`, `F3_TRADE_NOTIONAL_USD=200`, `F3_STOP_LOSS_PCT=0.65`, etc.

## Safety
Do NOT set `AIEM_ALLOW_LIVE_ORDERS=1` or `LIVE_TRADING_ENABLED=true` until Joel explicitly arms live.
Account `6YB85617` is live brokerage token, $0 equity / $0 BP — read-only probe OK; orders stay gated.
