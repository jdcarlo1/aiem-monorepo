---
name: lognormal_cdf survival function
description: _lognormal_cdf in aiem_strat_engine/probability.py returns P(X>S), not P(X<S) despite its docstring. All production callers use it correctly as a survival function.
---

# _lognormal_cdf is a survival function

**Rule:** `_lognormal_cdf(S, spot, sigma, T)` in `aiem_strat_engine/probability.py` returns
`N(-z)` where `z = (ln(S/spot) + 0.5σ²T) / (σ√T)`.

`N(-z)` equals **P(X > S)** under the risk-neutral lognormal, i.e. the complementary CDF
(survival function). The docstring incorrectly claims it returns `P(X < S)`.

**Why:** Confirmed by FIN-015 verification (Phase 5): for S=102, spot=100, σ=0.20, T=30/365,
`_lognormal_cdf` returns ≈0.354 = P(X > 102), not P(X < 102) ≈ 0.646.

**How to apply:**
- When calling `_lognormal_cdf(S, ...)` to get the probability that price EXCEEDS S: call
  it directly — `prob = _lognormal_cdf(S, spot, sigma, T)`.
- When you need P(X < S): use `1 - _lognormal_cdf(S, spot, sigma, T)`.
- Do NOT write `1 - _lognormal_cdf(...)` to get P(X > S) — that would double-complement and
  return P(X < S).
- All existing callers in `probability_of_profit`, `probability_of_touch`,
  `probability_of_max_profit` are correct — they already treat the return value as P(X > S).
- The docstring is wrong; the code is correct and internally consistent.
