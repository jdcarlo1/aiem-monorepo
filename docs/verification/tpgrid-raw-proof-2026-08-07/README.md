# Directive_TPGridBacktest_RawProof_2026-08-07

Raw proof pack for ranking strategies #1–#5. Not a narrative PASS restatement.

## Files

| Item | Path |
|---|---|
| Full ledgers (CSV) | `01_narrow_wing_full_ledger.csv` … `05_put_ladder_full_ledger.csv` |
| Source JSON ledgers | `narrow_wing_butterfly__tp200.json`, `bullish_risk_reversal__tp75.json`, `07_long_put_butterfly__tp200.json`, `06_long_call_butterfly__tp100.json`, `23_put_ladder_defined_risk__tp150.json` |
| Terminal re-run (cmd+stdout) | `00_RAW_TERMINAL_RERUN.txt` |
| sha256 + endpoint greps | `00_hashes_and_pricing_grep.txt` |
| Head/tail + awk | `01_ledgers_head_tail_awk.txt` |
| jq resum | `02_jq_resum.txt` |
| Pricing / staleness | `03_pricing_and_staleness.txt` |
| Negative controls | `05_negative_controls.txt` |
| No look-ahead lines | `06_no_lookahead_code.txt` |

## Reported totals (must match awk/jq)

| # | Strategy | n | sum(pnl) |
|---:|---|---:|---:|
| 1 | Narrow-Wing Call Butterfly TP200 | 94 | 260940 |
| 2 | Bullish Risk Reversal TP75 | 94 | 202238 |
| 3 | Long Put Butterfly TP200 | 91 | 104676 |
| 4 | Long Call Butterfly TP100 | 92 | 76186 |
| 5 | Put Ladder Defined TP150 | 92 | 69455 |

## Explicit CANNOT PRODUCE

- Worst single-trade dollar staleness (daily close vs intraday fill): ledgers lack option OHLC / fill timestamps.
