---
name: Diagram 2 indicator column names and wiring gotchas
description: Actual DB column names for layer9_scores sub-indicators, M6 retire requirement, CTA lookback fix
---

## layer9_scores actual column names
Audit revealed column names differ from what human-readable docs suggest:

| Indicator | Actual Column | Wrong assumption |
|-----------|--------------|-----------------|
| Hurst Exponent | `hurst_raw` | ~~hurst_exponent~~ |
| VPIN | `vpin_raw` | ~~vpin~~ |
| Amihud Illiquidity | `amihud_score` | ~~amihud_illiquidity~~ |
| Variance Risk Premium | `vrp_score` | ~~vrp~~ |
| Corwin-Schultz Spread | `cs_spread_raw` | ✓ |
| PCA Factor 1 Variance | `pca_factor1_var` | ✓ |
| Risk-Neutral Density | `rnd_skew` | ✓ (NULL when rnd_available=False) |
| Jump Detection | `jump_detected` | ✓ (BOOL) |

**Why:** Column names were set by layer9_statistical_edge.py's INSERT which uses abbreviated/score forms.

## M6 Rediscovery needs aiem_signal_actions retire record

M6's SQL query:
```sql
FROM aiem_signal_actions sa
JOIN aiem_signal_discoveries sd ON sd.id = sa.discovery_id
WHERE sa.action = 'retire'
  AND sa.approved_at > last_run_ts
  AND sd.status = 'retired'
```

Directly updating `status='retired'` in aiem_signal_discoveries is NOT enough.
Must also INSERT into aiem_signal_actions with `action='retire'` and `approved_at=NOW()`.

**How to apply:** Any code that retires a signal must write to BOTH tables.

## CTA Triggers: lookback_days=365 (not 280)

`_fetch_tradier_closes(lookback_days=280)` only returns ~191 trading days.
Need `lookback_days=365` to get 200+ trading days required for SMA200 computation.

**Why:** 280 calendar days × (252/365) ≈ 193 trading days < 200 minimum needed for SMA200.

## options_structure_scan actual columns

ticker, scan_date, spot, gex_m, gex_regime, gamma_flip_price, pc_skew_pp, pc_skew_tag, term_ratio, term_tag, front_iv, back_iv, calls_analyzed, puts_analyzed, updated_at (NO scan_time column).

## Section 13 trace generation

aiem_diagram2_trace_audit.record_stage() is the correct API for generating D2 trace records.
Stages 1-17 match exactly what _aiem_paper_execute_today() writes via _d2_run().
Stage 18 = paper_trade_insert. Stage 99 = terminal_rejection (reserved).

The force-execute endpoint rejects on non-trading days (weekdays only). Direct record_stage()
calls work anytime for audit/test purposes.

## aiem_signal_discoveries: signal_win_rate re-registration issue

Modules re-register signals on server startup with ON CONFLICT logic that can reset
signal_win_rate to None (from backtest). The oos_edge column is more stable for audit
purposes since it's only set by the BH-FDR harness, not the registration path.
