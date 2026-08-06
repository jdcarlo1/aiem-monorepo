# Directive_F3_RealOptionsPricing_2026-08-06

## Full-scope check
- `f3_strategy.py` / `spy_stoploss_sweep.py`: **not present** in monorepo (ls → No such file).
- Repo-wide `atm_est` / `leverage = clamp` / `0.50*spy` synthetic formula: **zero matches** in strategy code.
- Only mentions of "synthetic leverage" are negation comments in the real-pricing paths.

## Fix landed
- `tools/f3_strategy.py` — Polygon `/v2/aggs/ticker/{O:...}/range/1/minute` for entry/exit premiums; skip trade if no bars; no synthetic fallback.
- sha256: see commit / `sha256sum tools/f3_strategy.py`

## Backtest re-run
**Blocked:** `POLYGON_API_KEY` in this environment returns HTTP 401 Unknown API Key (stocks + options). Valid key with options entitlement requested via environment setup.

## UI wiring
Pattern Lab / OE Strategies poll live `GET /pattern-lab/snapshot` → in-memory `AIMPaperTradingEngine.dashboard_snapshot()` → `aim_f3_spy_0dte.F3OptionsLedger`. They do **not** read `tools/f3_strategy.py` output or any cached backtest CSV.
