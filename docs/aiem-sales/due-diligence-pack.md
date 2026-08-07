# AIEM Terminal — Due Diligence Pack

**SKU:** AIEM Terminal (equity / autonomous research desk)  
**Not included by default:** Options Engine Terminal (sold separately)

## 1. What the buyer is buying

An institutional-style **paper research terminal** with:

- Autonomous morning Loop B predictions (`aiem_predictions`)
- Equity paper book + MTM / performance analytics
- Command Center, signals, council, pattern lab, probability, risk, audit
- Explicit simulation lock (no accidental live brokerage)

## 2. What the buyer is NOT buying (unless separately contracted)

- Live brokerage order routing
- Options Engine Terminal / OE trade book
- Multi-tenant SaaS, SSO, MFA
- Guaranteed forward returns

## 3. Reliability proof

Track in AIEM → **Sales Readiness**:

- Morning prediction streak (sale bar: **5 consecutive green days** after Publish)
- Paper marks green (no null `last_price` / `pnl` on OPEN)
- Job heartbeats for `aiem_morning_scan` / paper jobs

**Critical:** Git fixes do not count until production **Publish/redeploy**.

## 4. Honest P&L policy

- STOCK/ETF: underlying quote MTM
- CALL/PUT: live option mid when `option_entry_mid` captured; otherwise **labeled** synthetic 2× proxy
- Buyer report: Analytics → Performance + Sales Readiness → Honest P&L

## 5. Live path policy

See `live-path-policy.md`. Default = paper-only. Dual env locks required before any live adapter work.

## 6. Commercial controls

| Control | Status |
|---|---|
| Auth | Admin token / session login |
| Roles (Viewer/Trader/Auditor/Admin) | Documented; Admin enforced today |
| API surface | See `api-surface.md` |
| Demo script | See `demo-script.md` |
| OE separation | Separate SKU — not bundled |

## 7. Suggested diligence questions

1. Show 5 consecutive green morning prediction days after last Publish.  
2. Show OPEN book with non-null marks and option honesty %.  
3. Confirm `LIVE_TRADING_ENABLED` is not armed.  
4. Confirm OE is not required for AIEM-only operation.  
