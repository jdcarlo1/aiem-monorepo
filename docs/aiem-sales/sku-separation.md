# AIEM vs OE — SKU separation (shared reserved VM)

## What is shared
- Reserved VM / stock-api process
- Polygon market data (quotes, option daily closes)
- Tradier SPY 1-min bars → **one feed, two evaluate calls**
- Operator password / `ADMIN_TOKEN` (Phase 0)

## What is isolated
| Layer | AIEM | OE |
|---|---|---|
| Strategy engine | `_PATTERN_LAB_ENGINE` (`sku=aiem`) | `_OE_STRATEGIES_ENGINE` (`sku=oe`) |
| Snapshot | `/pattern-lab/snapshot` | `/oe/strategies/snapshot` |
| Equity paper book | `/aiem-paper-portfolio` (excludes `AIEM:`/`OE:` packages) | n/a |
| Strategy paper book | `/sku-paper-portfolio?sku=aiem` (`AIEM:SPY:*`) | `/oe-strategies-portfolio` (`OE:SPY:*`) |
| Broker hook (hard-blocked) | `/aiem-broker/status` · paper-order | `/oe-broker/status` · paper-order |
| Live arm flag | `AIEM_ALLOW_LIVE_ORDERS=1` | `OE_ALLOW_LIVE_ORDERS=1` |
| Paper adapter cash | separate instance | separate instance |

## Brokerage rule
Sharing Polygon **cannot** cross-submit orders. Adapters are SKU-tagged; live remains fail-closed until you arm the dual `LIVE_TRADING_*` locks **and** that SKU’s allow flag, then implement a real `place_order()`.

## Deploy note
Publish outside Mon–Fri 08:50–10:30 ET (morning deploy miss guard).
