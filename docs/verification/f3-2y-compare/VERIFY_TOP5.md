# Verification — ranking strategies #1–#5

Checked 2026-08-07 against archived trade ledgers + live `aim_asym_paper_strategies.py`.

## Method
For each ranked package: reopen the TP-grid ledger JSON → re-sum every trade `pnl` → recompute WR and avg → confirm TP% equals the live website ledger config → spot-check first-trade OCC legs vs live builders.

## Results — all PASS

| # | Strategy | Ledger | Trades | Sum(pnl)=reported | WR match | Avg match | Live TP% |
|---:|---|---|---:|:---:|:---:|:---:|---:|
| 1 | Narrow-Wing Call Butterfly | `narrow_wing_butterfly__tp200.json` | 94 | PASS ($260,940) | PASS 86.17% | PASS | 200 |
| 2 | Bullish Risk Reversal | `bullish_risk_reversal__tp75.json` | 94 | PASS ($202,238) | PASS 91.49% | PASS | 75 |
| 3 | Long Put Butterfly | `07_long_put_butterfly__tp200.json` | 91 | PASS ($104,676) | PASS 63.74% | PASS | 200 |
| 4 | Long Call Butterfly | `06_long_call_butterfly__tp100.json` | 92 | PASS ($76,186) | PASS 84.78% | PASS | 100 |
| 5 | Put Ladder Defined | `23_put_ladder_defined_risk__tp150.json` | 92 | PASS ($69,455) | PASS 52.17% | PASS | 150 |

## Legs match live website builders (sample trade 2024-08-12, SPY≈533)
- Narrow-Wing: `+1 531C / −2 533C / +1 535C` (= ATM±2)
- Bullish RR: `+6 538C / −6 528P` (= call k+5 / put k−5, packaged)
- Put fly: `+1 538P / −2 533P / +1 528P` (= ATM±5 puts)
- Call fly: `+9 528C / −18 533C / +9 538C` (= ATM±5 calls, packaged)
- Put ladder: `+3 533P / −3 528P / −3 523P / +3 518P` (= k, k−5, k−10, k−15)

All sample trades have `sl_pct=0` (no stop), matching live paper (TP only).

## Caveat (honest)
Math and TP wiring check out. These BTs use **Polygon daily** option marks and Monday entries — same family as live paper, not identical to live Tradier mid fills tick-by-tick.
