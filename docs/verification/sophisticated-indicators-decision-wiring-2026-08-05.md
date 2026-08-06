# Sophisticated indicators → stock decisions wiring (2026-08-05)

## Goal

Connect Council / BH-FDR / PIT / TreeSHAP / M2–M6 / Layer 9 / GARCH / GP / VPIN into the same paths that rank and insert stock decisions (conviction + paper picks), and fix false Signal Intel proxies.

## Changes

| Item | Before | After |
|------|--------|-------|
| **Signal Intel GARCH card** | Proxied Layer 9 ticker count | Reads `garch_regime_log` (real fits, votes, last log) |
| **Signal Intel GP card** | Same Layer 9 proxy | Reads `gp_discovered_templates` (Module 1 weekly evolution) |
| **VPIN** | Fixed bar-count windows | Equal-**volume** buckets + rolling imbalance (API signature unchanged) |
| **Conviction `layer9_edge`** | Always 0 (shadow-learning key unused) | Prefetch `layer9_scores` → 0–2 pts from statistical_score / jump |
| **Paper rank — M2** | Exit-only | Entry `drift_mult` ≤0.50 when decay_verdict in `{decaying,failing}` |
| **Paper rank — BH-FDR retire** | Ledger only | Entry `drift_mult` ≤0.25 when discovery `retired`/`superseded` |
| **Council** | Score ±20% without Layer 9 context | Passes Hurst/VPIN/Amihud/stat9 into `signal_engine` seat |
| **Layer 9 soft mult** | Debate context only | After council: ×1.10 if stat≥65, ×0.85 if jump or stat\<40 |
| **Debate soft gate** | Audit-only | Skip insert on BEAR_WINS / AVOID / NO_TRADE (or CONFLICTED+HIGH risk) |
| **PE / TreeSHAP** | Stage 13 often SKIP; no size effect | Soft: real (non-SKIP) score \<0.45 → notional ×0.5 |
| **PIT (G6)** | Unchanged SHADOW fail-open | Left as-is (needs explicit ENFORCE approval to hard-block) |

## Files

- `artifacts/stock-scanner-api/advanced_quant_indicators.py` — VPIN
- `artifacts/stock-scanner-api/main.py` — Signal Intel, conviction, paper pick/execute
- `artifacts/stock-scanner/src/pages/Dashboard.tsx` — Signal Intel card copy

## Intentionally not changed

- No DROP/ALTER migrations
- G6 PIT remains SHADOW until Joel approves ENFORCE
- PE SKIP / polygon_fallback still does **not** hard-block (Option C)
- Council seats still exclude GARCH/bull-bear/social (double-count rule)
- `ml_infrastructure.gp_signal_search` remains tool-only (scheduled path is Module 1 GP evolution)

## Smoke

- `python3 -c` VPIN on 300 synthetic bars → ≥50 non-NaN values in [0,1]
- `compile(main.py)` syntax OK
