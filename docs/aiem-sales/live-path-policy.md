# AIEM Live Path Policy

## Default
**PAPER_ONLY.** AIEM is sold as a research / paper terminal.

## Hard locks (`simulation_lock.py`)
Live trading requires **both**:
1. `LIVE_TRADING_ENABLED=true`
2. Matching confirmation phrase env vars

Even then, functions that call `assert_simulation_mode()` refuse live execution until explicitly redesigned.

## Broker adapter status
- Tradier: **market data** only today
- IBKR / Alpaca: **stubs only** (not connected)
- Order routing: **blocked**

## Commercial rule
Do not pitch AIEM as a live trading desk until:
1. Dedicated broker adapter reviewed
2. Fill/fee/corp-action P&L audit path exists
3. Separate “AIEM Live” SKU or entitlement is defined  
   (so research buyers are not forced onto live risk)
