---
name: SMS alert threshold evolution
description: Why the SMS threshold is 1%+volume-tiered, not 10% — based on real movers observed June 12 2026
---

## The Core Insight
Volume is the primary signal. Price % confirms direction but comes too late.
A stock up 1% on 5× volume is early accumulation. A stock up 10% on 1.5× volume is already done.

## Threshold Evolution (June 12 2026)
- Started at 10% flat
- Lowered to 7.5% (NAT +7.96% would have been missed at 10%)
- Lowered to 3% (AIP, NAT, ANDG all opened below 3%)
- Lowered to 1% with tiered volume (8 of 10 movers opened below 3%)

## Final Tiered Logic (sms_alerts.py)
| Move size | Min rel_vol | Rationale |
|-----------|------------|-----------|
| +1–3%     | 5×         | Tiny move needs huge vol proof |
| +3–7%     | 4×         | Moderate move, confirm with vol |
| +7–10%    | 3×         | Strong move, standard vol bar |
| +10–20%   | 2×         | Big move, vol confirms |
| +20%+     | 1.5×       | Massive move, any vol works |

## June 12 2026 — 10 Missed Movers (all opened below old thresholds)
| Ticker | Open % | Close % | Pattern |
|--------|--------|---------|---------|
| NAT    | ~+1%   | +7.96%  | NYSE slow grind, no catalyst |
| ANDG   | ~+2.8% | +7.91%  | NYSE slow grind, no catalyst |
| AIP    | ~+0.5% | +9.89%  | Flat open, afternoon run (was in EOD picks) |
| VECO   | ~+2%   | +8.29%  | Insider selling yet still ran — volume |
| SKE    | ~+3.6% | +7.87%  | Dip-and-recover accumulation |
| ZBIO   | ~+4%   | +11.49% | Conference presentation catalyst |
| ALMS   | ~+5%   | +15.55% | Biotech grind |
| ELVN   | ~+6.5% | +14.30% | Clinical data catalyst spike |
| ARM    | ~+4.6% | +11.27% | Semi sector sweep (China export restrictions) |
| AMKR   | ~+2.4% | +8.71%  | Semi sector sweep |
| AXTI   | ~+2.4% | +10.01% | Semi sector (indium phosphide) — IN WATCHLIST |

**Why:** 8 of 10 opened below 3%. The volume at open IS the signal.

## Two Move Types Identified
1. **Slow grinders** (NAT, ANDG, AIP, VECO): flat/tiny open, accumulate all day. Volume at open is the only early signal.
2. **Catalyst spikes** (ELVN, ZBIO, ARM): gap up at open, spike, sometimes pullback. Pre-market scanner would catch these before the bell.

## Sector Sweeps
When one semiconductor stock moves on macro news (China export controls), check the whole sector.
ARM → AMKR → AXTI → KLA → Teradyne all moved same day on same catalyst.

## What the Scanner Was Already Catching
- AXTI was in morning watchlist — scanner checked it every scan, just no SMS notification
- AIP was in June 11 EOD accumulation picks (ranked #15/15) — still ran +9.89% next day
- The scanner finds these. The missing piece is real-time SMS notification (Twilio pending).

**Why this matters:** Don't over-tune the scanner — it's finding the right stocks. The problem is notification timing, not discovery.

## ETF Gate: VWAP Beats Open-of-Day (backtested Jun 1–13 2026)
**Backtest script:** `artifacts/stock-scanner-api/backtest_etf_gate.py`

### Morning Burst (9:35–9:45 AM) — clear winner
| Gate | n | Win Rate | EV/trade |
|---|---|---|---|
| No gate | 40 | 62% | +0.23% |
| Open gate (old) | 35 | 60% | +0.20% |
| VWAP gate (live) | 30 | 67% | +0.83% |

VWAP gate blocked Jun 9 semi blowups (AMAT/LRCX/AMKR/ONTO −4 to −6%) where SMH had fallen below VWAP at 9:35 AM despite being above open. Open gate missed this.

### Grinder (10:30 AM) — marginal improvement
| Gate | n | Win Rate | EV/trade |
|---|---|---|---|
| No gate | 28 | 50% | −0.08% |
| Open gate (old) | 25 | 44% | −0.25% |
| VWAP gate (live) | 27 | 48% | −0.12% |

VWAP gate +4pp WR over open gate. Note: open gate was blocking winners (CAT Jun 2, AMD Jun 3) that had ETFs above VWAP despite being below open — those correctly pass under VWAP gate.

**Key insight:** At 9:35 AM, VWAP ≠ open — ETFs can gap up then immediately reverse below VWAP while still above open. VWAP captures intraday weakness that open gate is blind to.

**Decision (June 14 2026):** Switched both scanners to VWAP gate in `sms_alerts.py`.
