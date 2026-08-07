# AIEM Terminal — API Surface (SKU-scoped)

Base: `/stock-api`

## Core AIEM (include in AIEM-only sale)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/aiem-sales-readiness` | Admin token | Buyer proof dashboard data |
| GET | `/aiem-broker/status` | Admin token | Broker adapter readiness (paper + stubs) |
| POST | `/aiem-broker/paper-order` | Admin token | Paper adapter smoke test only (never live) |
| GET | `/aiem-predictions` | Public/read | Loop B morning predictions |
| GET | `/aiem-paper-portfolio` | Public/read | Equity paper book |
| GET | `/paper-performance` | Token | Quant performance report |
| GET | `/morning-brief` | Public/read | Morning narrative |
| GET | `/admin/job-heartbeats` | Admin | Reliability heartbeats |
| POST | `/aiem-paper-portfolio/force-mtm` | Admin | Refresh marks + MTM |
| POST | `/aiem-paper-portfolio/force-execute` | Admin | Paper execute (simulation locked) |

## Explicitly OE (do **not** bundle into AIEM-only)

| Method | Path | Note |
|---|---|---|
| GET | `/admin/trade-records` | OE book (`oe_trade_records`) |
| OE scheduler routes | options pipeline | Separate SKU deploy |

## Auth model today

- `X-Admin-Token: $ADMIN_TOKEN` or session login
- Roles (Viewer/Trader/Auditor/Admin) are **documented** in Sales Readiness; enforcement is Admin-token based until RBAC ships
