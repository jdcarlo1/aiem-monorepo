---
name: Washout-Complete quality gates
description: 3 research-validated filters that raise washout-complete WR from 55% to 73%, discovered via 97K-signal AIEM backtest
---

## All 6 Quality Gates (wired into _check_momentum_washout_complete)

| Gate | Constant | Value | Why |
|---|---|---|---|
| Bad months | `_BAD_MONTHS` | Skip Jan(1), Feb(2), Mar(3), Nov(11) | Feb=20% WR, Mar=27%, Nov=36%, Jan=49% |
| Price floor | `_MIN_PRICE` | ≥ $5 | Penny stocks show no edge after washout |
| Prior 10d trend | `_TREND_MAX10D` | ≤ −5% | Rising stocks into signal = 48% WR; falling = 63% |
| Volatility CV | `_CV_MAX` | ≤ 15% | Losers avg 32% CV vs winners avg 10% — 3× higher |
| Entry depth | `_ENTRY_DISC_MIN` | ≥ −15% | >15% below coil = breakdown not washout |
| 20d free-fall | `_TREND_MAX20D` | ≥ −20% | Free-fall stocks (−17.4% avg 20d) have 74% loser rate |

## Month Filter — CONFIRMED with all other gates in place

Tested with all 5 other gates locked. Month effect is REAL, not an artifact:
- Bad months combined: **34.3% WR** vs Good months: **69.2% WR** (35pp gap)

**Per-month breakdown (all 5 other gates applied):**
| Month | WR | Notes |
|---|---|---|
| Feb | 19.6% | Catastrophic — 54% lose >10% |
| Mar | 27.3% | Very bad |
| Nov | 35.9% | **BAD — was missed in original filter; added** |
| Jan | 49.2% | Coin flip |
| Oct | 53.1% | **Was in bad list but actually OK — removed** |
| May | 57.6% | Good |
| Jun | 61.3% | Good |
| Sep | 62.1% | Good |
| Dec | 61.0% | Good |
| Aug | 72.8% | Very good |
| Apr | 86.6% | Best month — +10.3% avg return, 4% lose >10% |

**Oct removed, Nov added** — corrected from original {1,2,3,10} → now {1,2,3,11}

## Final 6-Gate Backtest Results (4,879 signals)

- WR at 21d: **68.0%** | WR at 45d: **68.8%** (peak) | WR at 63d: 65.0%
- Avg return at 45d: +5.6% | Avg peak gain (45d window): +13.1%
- Hit +10% within 45d: 49.5% | Hit +15%: 28.7% | Hit +20%: 17.4%
- Stop loss (−10%) triggers: 13.4% of trades; 76% of those are real losers
- True loser rate: 10.4% | Lose >10% at 45d: 9.9% | Lose >20%: 2.8%
- Avg max drawdown first 10 days: −2.1% (very shallow — don't panic early)

## Trade Rules from Data
- **Hold**: 30–45 days (WR peaks at 45d, decays after 63d)
- **Take profit**: +15% target (avg winner peaks +13.1%)
- **Stop loss**: −10% (keep it — 76% of triggers are real losers, not noise)
- **Never average down** — drawdown cohort averages −25% at worst

**Why:** Counter-intuitive: stocks that were RISING before signal = 48% WR. Real washout needs prior downtrend. If stock fell >15% from coil OR has 3× normal volatility, it's a breakdown, not a washout.

**How to apply:** Constants in `_check_momentum_washout_complete()`. `prior_ret10d` stored in `momentum_washout_complete` table. lag20d + price_std20 computed in SQL window but not stored separately.
