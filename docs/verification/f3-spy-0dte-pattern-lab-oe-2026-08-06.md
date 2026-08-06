# F3 SPY 0DTE — AIM Pattern Lab + Options Engine terminal

Wired 2026-08-06 per user directive (real option pricing rules).

## Terminals
- **AIM / AIEM dashboard** — Pattern Lab (`/pattern-lab`): third card **F3 SPY 0DTE**
- **Options Engine terminal** (`oe-dashboard`) — new **Strategies** nav (`/strategies`)

## Live paper
- `artifacts/stock-scanner-api/aim_f3_spy_0dte.py`
- Fed by existing Pattern Lab SPY 1-min capture via `AIMPaperTradingEngine.evaluate_market_bars`
- Snapshot field: `f3` on `GET /stock-api/pattern-lab/snapshot` (no main.py route change)

## Rules
Premarket direction → ORB 9:30–9:44 → breakout with PM → ATM long call/put @ $200 notional → **−65% premium stop** (auto sell), else exit 16:00. No profit target. Real Tradier premiums when available; breakout without premium stays `WAITING_PREMIUM` (no synthetic entry).
