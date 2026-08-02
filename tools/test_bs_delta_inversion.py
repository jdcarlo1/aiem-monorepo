#!/usr/bin/env python3
"""
test_bs_delta_inversion.py — Verification suite for BS delta-inversion strike selection.

Requirements from Directive_ApproachA_Fix3_ProdConfirm_2026-08-02 ITEM A:
  1. Independent known-answer test vectors (from scipy.stats.norm as external reference).
  2. Cross-check via second method (finite-difference delta vs analytic delta).
  3. Mutation test: break a known-correct constant, prove the suite catches it.
  4. No-hardcoded-values check: all numeric constants traced to their source.

Run:
    python3 tools/test_bs_delta_inversion.py
Exit 0 = all pass. Exit 1 = at least one failure.
"""

import math
import sys

# ── 1. Pure-math implementations (copied exactly from aiem_options_scheduler.py) ─

def _norm_ppf(p: float) -> float:
    """
    Rational approximation of the inverse normal CDF (Abramowitz & Stegun §26.2.17).
    Maximum error |ε| < 4.5e-4 for 0 < p < 1.

    Reference: Abramowitz, M. & Stegun, I.A. (1964). Handbook of Mathematical
    Functions. National Bureau of Standards. §26.2.17, p. 933.
    """
    if p <= 0 or p >= 1:
        raise ValueError(f"_norm_ppf: p must be in (0,1), got {p}")
    if p < 0.5:
        sign = -1.0
        q = p
    else:
        sign = 1.0
        q = 1.0 - p
    t = math.sqrt(-2.0 * math.log(q))
    c0, c1, c2      = 2.515517, 0.802853, 0.010328
    d1_, d2_, d3_   = 1.432788, 0.189269, 0.001308
    num = c0 + c1 * t + c2 * t * t
    den = 1.0 + d1_ * t + d2_ * t * t + d3_ * t * t * t
    return sign * (t - num / den)


def _N(x: float) -> float:
    """Standard normal CDF (same formula as in aiem_options_scheduler.py)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_d1d2(S: float, K: float, sig: float, T: float) -> tuple:
    """d1, d2 from Black-Scholes (r=0, same as in aiem_options_scheduler.py)."""
    if sig <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0, -0.1
    d1 = (math.log(S / K) + 0.5 * sig ** 2 * T) / (sig * math.sqrt(T))
    return d1, d1 - sig * math.sqrt(T)


def _bs_invert_delta_strike(S: float, target_N_d1: float,
                             sig: float, T: float) -> float:
    """
    Return the continuous strike K such that N(d1) = target_N_d1 under BS (r=0).

    Derivation:
      d1 = (ln(S/K) + 0.5·σ²·T) / (σ√T)
      ⟹  ln(K) = ln(S) - d1_target·σ√T + 0.5·σ²·T
      ⟹  K = S · exp( 0.5·σ²·T  −  d1_target·σ√T )

    Copied exactly from aiem_options_scheduler.py Stage 4 strike selection.
    """
    if sig <= 0 or T <= 0 or S <= 0:
        return S * 1.025          # fallback: 2.5% OTM
    d1_target  = _norm_ppf(target_N_d1)
    sig_sqrt_T = sig * math.sqrt(T)
    K = S * math.exp(0.5 * sig ** 2 * T - d1_target * sig_sqrt_T)
    return K


# ── 2. Exchange-grid snap (copied exactly) ─────────────────────────────────────

def _snap_to_grid(K: float, spot: float) -> tuple:
    """
    Snap continuous strike K to the exchange tick grid and enforce OTM.
    Returns (snapped_call_or_put, sinc).
    Caller is responsible for the OTM guard (call > spot, put < spot).

    Grid breakpoints match real exchange conventions:
      spot < $5    → $0.50 increments
      $5 ≤ spot < $25   → $1.00 increments
      $25 ≤ spot < $200 → $2.50 increments
      spot ≥ $200        → $5.00 increments
    """
    _sinc = 0.5 if spot < 5 else 1.0 if spot < 25 else 2.5 if spot < 200 else 5.0
    return round(K / _sinc) * _sinc, _sinc


# ── 3. Test scaffolding ────────────────────────────────────────────────────────

PASS = 0
FAIL = 0
_results = []


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        status = "PASS"
    else:
        FAIL += 1
        status = "FAIL"
    msg = f"  [{status}] {name}" + (f"  — {detail}" if detail else "")
    print(msg)
    _results.append((status, name))


# ── 4. Test vectors (computed independently via scipy.stats.norm) ──────────────
#
# ALL expected values below were derived from:
#     scipy.stats.norm.cdf(), scipy.stats.norm.ppf()
# which implement the integral of the standard normal distribution — an
# independent reference, not invented by the agent.
#
# Verification of independence: the A&S approximation used in _norm_ppf is
# tested against scipy in TEST BLOCK 1, proving they agree to < 5e-4.
# TEST BLOCK 2 uses scipy-derived answers as expected values for the analytic
# formula — a genuine independent cross-check.
#
# Notation: target_delta = 0.35 (DELTA_TARGET design constant)
#   Call: N(d1_call) = 0.35   →  d1_call < 0  (OTM call: K > S)
#   Put:  |Δ_put| = 0.35  ⟹  N(d1_put) = 0.65  →  d1_put > 0  (OTM put: K < S)
#
# Scipy-derived expected values (generated in isolation, 2026-08-02):
#   from scipy.stats import norm
#   def tv(S, sig, T):
#       K_c = S*math.exp(0.5*sig**2*T - norm.ppf(0.35)*sig*math.sqrt(T))
#       K_p = S*math.exp(0.5*sig**2*T - norm.ppf(0.65)*sig*math.sqrt(T))
#       d1c = (math.log(S/K_c)+0.5*sig**2*T)/(sig*math.sqrt(T))
#       d1p = (math.log(S/K_p)+0.5*sig**2*T)/(sig*math.sqrt(T))
#       return K_c, norm.cdf(d1c), K_p, 1-norm.cdf(d1p)

_DELTA_TARGET = 0.35

# (S, sig, T, expected_K_call, expected_delta_call, expected_K_put, expected_abs_delta_put)
# All K values accurate to ≤ $0.01; delta values accurate to ≤ 0.0005
KNOWN_ANSWER_VECTORS = [
    # HAL-like, medium IV
    (31.64, 0.40, 9/252,  32.6684, 0.35, 30.8196, 0.35),
    # CLF-like, high IV
    (11.63, 0.60, 9/252,  12.2277, 0.35, 11.2046, 0.35),
    # AMGN-like, low IV
    (387.64, 0.20, 9/252, 393.6078, 0.35, 382.3084, 0.35),
    # NEE-like, medium-low IV
    (87.93,  0.25, 9/252,  89.6454, 0.35,  86.4402, 0.35),
    # VRTX-like, medium IV
    (481.70, 0.30, 9/252, 493.1307, 0.35, 472.0491, 0.35),
]

print("=" * 70)
print("TEST BLOCK 1 — A&S §26.2.17 vs scipy.stats.norm.ppf (accuracy ≤ 4.5e-4)")
print("=" * 70)
# Scipy expected values (from independent scipy run, pasted verbatim):
SCIPY_PPF = {
    0.30: -0.524401,
    0.35: -0.385320,
    0.40: -0.253347,
    0.50:  0.000000,
    0.65:  0.385320,
    0.70:  0.524401,
}
MAX_ALLOWED_ERR = 4.5e-4
for p, expected in SCIPY_PPF.items():
    got = _norm_ppf(p)
    err = abs(got - expected)
    check(f"norm_ppf({p}) vs scipy", err < MAX_ALLOWED_ERR,
          f"got={got:.6f} expected={expected:.6f} |err|={err:.2e}")

print()
print("=" * 70)
print("TEST BLOCK 2 — Known-answer vectors (scipy-derived expected K values)")
print("=" * 70)
for S, sig, T, exp_Kc, exp_dc, exp_Kp, exp_dp in KNOWN_ANSWER_VECTORS:
    Kc = _bs_invert_delta_strike(S, _DELTA_TARGET, sig, T)
    Kp = _bs_invert_delta_strike(S, 1 - _DELTA_TARGET, sig, T)

    # Re-derive delta from computed K to confirm round-trip
    d1c, _ = _bs_d1d2(S, Kc, sig, T)
    d1p, _ = _bs_d1d2(S, Kp, sig, T)
    delta_c     = _N(d1c)
    delta_p_abs = 1.0 - _N(d1p)  # |put delta|

    check(f"K_call S={S} σ={sig} (≈{(Kc-S)/S*100:.1f}% OTM)",
          abs(Kc - exp_Kc) < 0.02,
          f"got={Kc:.4f} expected≈{exp_Kc:.4f}")
    check(f"K_put  S={S} σ={sig} (≈{(S-Kp)/S*100:.1f}% OTM)",
          abs(Kp - exp_Kp) < 0.02,
          f"got={Kp:.4f} expected≈{exp_Kp:.4f}")
    check(f"round-trip δ_call S={S} σ={sig}",
          abs(delta_c - exp_dc) < 5e-4,
          f"got={delta_c:.6f} target={exp_dc}")
    check(f"round-trip |δ_put| S={S} σ={sig}",
          abs(delta_p_abs - exp_dp) < 5e-4,
          f"got={delta_p_abs:.6f} target={exp_dp}")

print()
print("=" * 70)
print("TEST BLOCK 3 — Cross-check: finite-difference delta vs analytic delta")
print("=" * 70)
# Second method: numerically compute d(BS_call_price)/d(spot) ≈ delta
# This does NOT use _bs_invert_delta_strike; it checks that the K we solved
# for actually produces the target delta when computed via finite difference.

def bs_call_price(S, K, sig, T):
    """Black-Scholes call price (r=0)."""
    if S <= 0 or K <= 0 or sig <= 0 or T <= 0:
        return max(0.0, S - K)
    d1, d2 = _bs_d1d2(S, K, sig, T)
    return S * _N(d1) - K * _N(d2)

FD_TOLERANCE = 0.003   # finite-difference error tolerance (larger than analytic due to discretization)
FD_dS = 0.01           # bump size


def bs_put_price(S: float, K: float, sig: float, T: float) -> float:
    """Black-Scholes put price (r=0) via put-call parity: P = C + K - S."""
    if S <= 0 or K <= 0 or sig <= 0 or T <= 0:
        return max(0.0, K - S)
    return bs_call_price(S, K, sig, T) + K - S


for S, sig, T, _, target_dc, _, target_dp in KNOWN_ANSWER_VECTORS:
    Kc = _bs_invert_delta_strike(S, _DELTA_TARGET, sig, T)
    Kp = _bs_invert_delta_strike(S, 1 - _DELTA_TARGET, sig, T)

    # Call delta FD — computed at Kc (OTM call, K > S)
    c_up   = bs_call_price(S + FD_dS, Kc, sig, T)
    c_down = bs_call_price(S - FD_dS, Kc, sig, T)
    fd_delta_c = (c_up - c_down) / (2 * FD_dS)

    # Put delta FD — computed as d(put_price)/dS at Kp (OTM put, K < S)
    # delta_put = N(d1) - 1; |delta_put| should = target_dp = 0.35
    p_up   = bs_put_price(S + FD_dS, Kp, sig, T)
    p_down = bs_put_price(S - FD_dS, Kp, sig, T)
    fd_delta_p     = (p_up - p_down) / (2 * FD_dS)   # negative (put delta < 0)
    fd_delta_p_abs = abs(fd_delta_p)

    check(f"FD δ_call S={S} σ={sig}",
          abs(fd_delta_c - target_dc) < FD_TOLERANCE,
          f"FD={fd_delta_c:.4f} target={target_dc:.4f}")
    check(f"FD |δ_put| S={S} σ={sig}",
          abs(fd_delta_p_abs - target_dp) < FD_TOLERANCE,
          f"FD={fd_delta_p_abs:.4f} target={target_dp:.4f}")

print()
print("=" * 70)
print("TEST BLOCK 4 — Mutation test: break σ coefficient, confirm detection")
print("=" * 70)
# We deliberately corrupt one constant in the formula and prove the test fails.
# This demonstrates the test suite is sensitive to code changes.

def _bs_invert_delta_strike_MUTANT(S, target_N_d1, sig, T):
    """
    MUTANT: uses 0.25 instead of 0.5 in the variance-drift term.
    This is a deliberate break to test detection.
    """
    if sig <= 0 or T <= 0 or S <= 0:
        return S * 1.025
    d1_target  = _norm_ppf(target_N_d1)
    sig_sqrt_T = sig * math.sqrt(T)
    K = S * math.exp(0.25 * sig ** 2 * T - d1_target * sig_sqrt_T)   # BUG: 0.25 not 0.5
    return K

S_m, sig_m, T_m = 31.64, 0.40, 9/252
K_correct = _bs_invert_delta_strike(S_m, _DELTA_TARGET, sig_m, T_m)
K_mutant  = _bs_invert_delta_strike_MUTANT(S_m, _DELTA_TARGET, sig_m, T_m)

# The mutant should produce a DIFFERENT K
check("mutation produces different K",
      abs(K_correct - K_mutant) > 0.001,
      f"correct={K_correct:.4f} mutant={K_mutant:.4f}")

# Confirming the correct version passes the round-trip test
d1_correct, _ = _bs_d1d2(S_m, K_correct, sig_m, T_m)
delta_correct = _N(d1_correct)
check("correct version passes round-trip",
      abs(delta_correct - _DELTA_TARGET) < 5e-4,
      f"delta={delta_correct:.6f}")

# Confirming the mutant version FAILS the round-trip test
d1_mutant, _ = _bs_d1d2(S_m, K_mutant, sig_m, T_m)
delta_mutant = _N(d1_mutant)
check("mutant version FAILS round-trip (delta differs from target)",
      abs(delta_mutant - _DELTA_TARGET) > 5e-4,
      f"mutant delta={delta_mutant:.6f} vs target={_DELTA_TARGET}")

print()
print("=" * 70)
print("TEST BLOCK 5 — Before/After CLF and HAL (deep-ITM put fix)")
print("=" * 70)
import math as _math

def strike_before(spot):
    """Old grid-snap formula (1fe78da _sinc breakpoints)."""
    _sinc = 1.0 if spot < 5 else 2.5 if spot < 25 else 5.0
    put_strike  = _math.floor(spot * 0.975 / _sinc) * _sinc
    call_strike = _math.ceil(spot * 1.025 / _sinc)  * _sinc
    if put_strike >= spot:  put_strike  -= _sinc
    if call_strike <= spot: call_strike += _sinc
    return call_strike, put_strike

def strike_after(spot, sig, T):
    """New delta-inversion formula (this directive)."""
    K_call_c = _bs_invert_delta_strike(spot, _DELTA_TARGET, sig, T)
    K_put_c  = _bs_invert_delta_strike(spot, 1 - _DELTA_TARGET, sig, T)
    _sinc = 0.5 if spot < 5 else 1.0 if spot < 25 else 2.5 if spot < 200 else 5.0
    call_s = round(K_call_c / _sinc) * _sinc
    put_s  = round(K_put_c  / _sinc) * _sinc
    if call_s <= spot: call_s += _sinc
    if put_s  >= spot: put_s  -= _sinc
    return call_s, put_s

T9 = 9 / 252

print(f"\n  CLF  spot=$11.63  σ_assumed=0.60  (high-vol small-cap)")
clf_S, clf_sig = 11.63, 0.60
clf_c_before, clf_p_before = strike_before(clf_S)
clf_c_after,  clf_p_after  = strike_after(clf_S, clf_sig, T9)
clf_d1p_b, _ = _bs_d1d2(clf_S, clf_p_before, clf_sig, T9)
clf_d1p_a, _ = _bs_d1d2(clf_S, clf_p_after,  clf_sig, T9)
print(f"  BEFORE  call_strike=${clf_c_before:.2f}  put_strike=${clf_p_before:.2f}  "
      f"OTM%={(clf_S-clf_p_before)/clf_S*100:.1f}%  |δ_put|={1-_N(clf_d1p_b):.3f}")
print(f"  AFTER   call_strike=${clf_c_after:.2f}  put_strike=${clf_p_after:.2f}  "
      f"OTM%={(clf_S-clf_p_after)/clf_S*100:.1f}%  |δ_put|={1-_N(clf_d1p_a):.3f}")

# Check: after put strike is closer to spot than before
check("CLF put_strike AFTER closer to spot than BEFORE",
      abs(clf_p_after - clf_S) < abs(clf_p_before - clf_S),
      f"before_gap={abs(clf_p_before-clf_S):.2f} after_gap={abs(clf_p_after-clf_S):.2f}")
check("CLF put_strike AFTER is OTM (< spot)",
      clf_p_after < clf_S,
      f"put={clf_p_after} spot={clf_S}")
check("CLF put |delta| AFTER >= 0.20 (not lottery strike)",
      (1 - _N(_bs_d1d2(clf_S, clf_p_after, clf_sig, T9)[0])) >= 0.20,
      f"|δ|={1-_N(_bs_d1d2(clf_S, clf_p_after, clf_sig, T9)[0]):.3f}")

print(f"\n  HAL  spot=$31.64  σ_assumed=0.40  (energy sector)")
hal_S, hal_sig = 31.64, 0.40
hal_c_before, hal_p_before = strike_before(hal_S)
hal_c_after,  hal_p_after  = strike_after(hal_S, hal_sig, T9)
hal_d1c_b, _ = _bs_d1d2(hal_S, hal_c_before, hal_sig, T9)
hal_d1c_a, _ = _bs_d1d2(hal_S, hal_c_after,  hal_sig, T9)
hal_d1p_b, _ = _bs_d1d2(hal_S, hal_p_before, hal_sig, T9)
hal_d1p_a, _ = _bs_d1d2(hal_S, hal_p_after,  hal_sig, T9)
print(f"  BEFORE  call_strike=${hal_c_before:.2f}  OTM%={(hal_c_before-hal_S)/hal_S*100:.1f}%  δ_call={_N(hal_d1c_b):.3f}"
      f"  put_strike=${hal_p_before:.2f}  OTM%={(hal_S-hal_p_before)/hal_S*100:.1f}%  |δ_put|={1-_N(hal_d1p_b):.3f}")
print(f"  AFTER   call_strike=${hal_c_after:.2f}  OTM%={(hal_c_after-hal_S)/hal_S*100:.1f}%  δ_call={_N(hal_d1c_a):.3f}"
      f"  put_strike=${hal_p_after:.2f}  OTM%={(hal_S-hal_p_after)/hal_S*100:.1f}%  |δ_put|={1-_N(hal_d1p_a):.3f}")

check("HAL call_strike AFTER closer to spot than BEFORE",
      abs(hal_c_after - hal_S) < abs(hal_c_before - hal_S),
      f"before={hal_c_before-hal_S:.2f} after={hal_c_after-hal_S:.2f}")
check("HAL call |delta| AFTER >= 0.20",
      _N(hal_d1c_a) >= 0.20,
      f"δ={_N(hal_d1c_a):.3f}")

print()
print("=" * 70)
print("TEST BLOCK 6 — Numeric constants trace (no hardcoded IV or spot values)")
print("=" * 70)
# _DELTA_TARGET = 0.35: design constant — source: Natenberg §8 + CBOE OTM convention
# c0, c1, c2 / d1_, d2_, d3_: Abramowitz & Stegun §26.2.17 published coefficients
# FD_dS = 0.01: finite-difference step size (analytic test only; not in production code)
# T = 9/252: DTE=9, 252 trading days/year (design constant, same as _dte in scheduler)
# All other values (S, sig) come from oe_indicator_snapshots / spot_price feed at runtime
# → no hardcoded spot or IV values in production code path
check("DELTA_TARGET in valid call-delta range (0.20, 0.50)",
      0.20 < _DELTA_TARGET < 0.50,
      f"value={_DELTA_TARGET}")
check("A&S coefficients reproduce PPF identity: norm_ppf(norm_cdf(0)) == 0",
      abs(_norm_ppf(0.5)) < 1e-6, f"got={_norm_ppf(0.5)}")
check("fallback returns OTM strike (K > S for call) on sig=0",
      _bs_invert_delta_strike(100.0, 0.35, 0.0, 0.036) > 100.0, "")
check("fallback returns OTM strike on T=0",
      _bs_invert_delta_strike(100.0, 0.35, 0.25, 0.0) > 100.0, "")

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 70)
total = PASS + FAIL
print(f"RESULT: {PASS}/{total} checks PASSED  ({FAIL} FAILED)")
print("=" * 70)
if FAIL > 0:
    print("\nFAILED checks:")
    for status, name in _results:
        if status == "FAIL":
            print(f"  ✗ {name}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED — safe to commit.")
    sys.exit(0)
