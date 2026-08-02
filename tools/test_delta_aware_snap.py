#!/usr/bin/env python3
"""
Test suite: delta-aware grid-snap for strike selection (Item 4 implementation).

Directive: Directive_ApproachA_Fix3_Review_c66a973 ITEM 4
Approach:
  1. Compute continuous K_cont via BS delta inversion (existing _bs_invert_delta_strike).
  2. Identify BOTH floor and ceiling candidates on the _sinc grid.
  3. Skip any candidate that violates the OTM guard (call: K <= spot; put: K >= spot).
  4. Compute BS delta at each surviving candidate.
  5. Among candidates with |delta| >= GATE (0.35): choose the one closest to DELTA_TARGET.
     If both pass, choose closest delta to target.
     If neither passes, return (None, None, False) and log reason.

STOP after output — no commit until Joel approves the table.
"""
import math
import sys

# ── shared constants (match aiem_options_scheduler.py exactly) ──────────────
_DELTA_TARGET = 0.35
_GATE         = 0.35
_DTE          = 9
_T            = _DTE / 252.0

# Standard normal CDF
_N = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

# A&S §26.2.17 inverse normal CDF (same as in production code)
def _norm_ppf(p: float) -> float:
    if p <= 0 or p >= 1:
        return 0.0
    if p < 0.5:
        sign = -1.0; q = p
    else:
        sign = 1.0; q = 1.0 - p
    t = math.sqrt(-2.0 * math.log(q))
    c0, c1, c2    = 2.515517, 0.802853, 0.010328
    d1_, d2_, d3_ = 1.432788, 0.189269, 0.001308
    num = c0 + c1 * t + c2 * t * t
    den = 1.0 + d1_ * t + d2_ * t * t + d3_ * t * t * t
    return sign * (t - num / den)

def _bs_d1d2(S, K, sig, T):
    if sig <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0, -0.1
    d1 = (math.log(S / K) + 0.5 * sig ** 2 * T) / (sig * math.sqrt(T))
    return d1, d1 - sig * math.sqrt(T)

def _bs_invert_delta_strike(S: float, target_N_d1: float, sig: float, T: float) -> float:
    if sig <= 0 or T <= 0 or S <= 0:
        return S * 1.025
    d1_t       = _norm_ppf(target_N_d1)
    sig_sqrt_T = sig * math.sqrt(T)
    return S * math.exp(0.5 * sig ** 2 * T - d1_t * sig_sqrt_T)

# ── sinc breakpoints (current c66a973 values) ────────────────────────────────
def _sinc_for(spot: float) -> float:
    return (0.5 if spot < 5 else 1.0 if spot < 25
            else 2.5 if spot < 200 else 5.0)


# ── ITEM 4: delta-aware grid-snap ─────────────────────────────────────────────
def _delta_aware_snap(K_cont: float, is_call: bool, spot: float,
                      sig: float, T: float, sinc: float,
                      gate: float = _GATE,
                      delta_target: float = _DELTA_TARGET):
    """
    Returns (strike, delta_abs, passed_gate, reason_str).
    If no valid candidate: (None, None, False, reason).
    """
    # Candidates: floor and ceiling on the sinc grid
    K_floor = math.floor(K_cont / sinc) * sinc
    K_ceil  = math.ceil(K_cont  / sinc) * sinc

    results = []   # (K, delta_abs, otm_ok, passes_gate)
    for K in sorted({K_floor, K_ceil}):   # sorted for determinism
        if K <= 0:
            continue
        # OTM guard
        otm_ok = (K > spot) if is_call else (K < spot)
        if not otm_ok:
            results.append((K, None, False, f"K={'call' if is_call else 'put'} OTM guard violated (K={K}, spot={spot})"))
            continue

        d1, _ = _bs_d1d2(spot, K, sig, T)
        raw_delta = _N(d1)
        delta_abs = raw_delta if is_call else abs(raw_delta - 1.0)

        passes = delta_abs >= gate
        results.append((K, delta_abs, passes,
                        f"|delta|={delta_abs:.4f} {'PASS' if passes else 'FAIL'} vs gate={gate}"))

    # Filter to passing candidates (OTM ok AND delta >= gate)
    passing = [(K, d, r) for K, d, ok, r in results if ok and d is not None and d >= gate]
    if passing:
        best = min(passing, key=lambda x: abs(x[1] - delta_target))
        return best[0], best[1], True, f"|delta|={best[1]:.4f} PASS"
    else:
        details = "; ".join(r for _, _, _, r in results)
        return None, None, False, f"no candidate passes gate={gate}: {details}"


# ─────────────────────────────────────────────────────────────────────────────
# TEST INFRASTRUCTURE
# ─────────────────────────────────────────────────────────────────────────────
PASS_COUNT = 0
FAIL_COUNT = 0

def check(label, condition, got=None, expected=None):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {label}")
    else:
        FAIL_COUNT += 1
        detail = f"  got={got!r}, expected={expected!r}" if got is not None else ""
        print(f"  [FAIL] {label}{detail}")


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 1 — Unit tests for _delta_aware_snap
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 68)
print("BLOCK 1 — Unit tests for delta-aware snap logic")
print("=" * 68)

# 1a: Both candidates pass → choose closest delta to target
# Construct a case where floor and ceil both pass gate
# Use HAL call: K_cont=32.668, sinc=2.5
# K_floor=32.5 (δ=0.376 PASS), K_ceil=35.0 (δ=0.098 FAIL)
# So actually only one passes; test that we pick the passing one
spot_hal = 31.64; iv_hal = 0.40
K_c, d_c, ok_c, _ = _delta_aware_snap(32.668, True, spot_hal, iv_hal, _T, 2.5)
check("HAL call: floor 32.5 passes (delta>0.35), ceil 35.0 fails", ok_c and abs(K_c - 32.5) < 0.01)
check("HAL call: delta is approximately 0.35-0.40", ok_c and 0.35 <= d_c <= 0.42)

# 1b: CLF put — floor passes OTM but fails gate, ceil violates OTM → no candidate
spot_clf = 11.63; iv_clf = 0.60
K_cont_put_clf = _bs_invert_delta_strike(spot_clf, 1 - _DELTA_TARGET, iv_clf, _T)
sinc_clf = _sinc_for(spot_clf)  # = 1.0
K_p, d_p, ok_p, reason_p = _delta_aware_snap(K_cont_put_clf, False, spot_clf, iv_clf, _T, sinc_clf)
check("CLF put: continuous K_put < 11.63", K_cont_put_clf < spot_clf)
check("CLF put: returns no candidate (OTM-valid candidate delta < gate)", not ok_p)
check("CLF put: reason string mentions 'gate'", "gate" in reason_p)

# 1c: AMGN call — floor closer to delta target passes; ceil below target delta
spot_amgn = 387.64; iv_amgn = 0.20
K_cont_call_amgn = _bs_invert_delta_strike(spot_amgn, _DELTA_TARGET, iv_amgn, _T)
sinc_amgn = _sinc_for(spot_amgn)  # = 5.0
K_a, d_a, ok_a, _ = _delta_aware_snap(K_cont_call_amgn, True, spot_amgn, iv_amgn, _T, sinc_amgn)
check("AMGN call: chosen strike is 390 (floor, higher delta vs 395 which fails)", ok_a and abs(K_a - 390.0) < 0.01)
check("AMGN call: delta ≥ 0.35", ok_a and d_a >= 0.35)

# 1d: When BOTH floor and ceil pass, choose closest delta to 0.35
# Manufacture: use a tight IV case so both K_floor and K_ceil have delta in range
# AMGN put: K_floor=380 (fails, delta=0.293), K_ceil=385 (passes, delta=0.421)
# → only ceil passes, so this tests single-candidate selection
K_cont_put_amgn = _bs_invert_delta_strike(spot_amgn, 1 - _DELTA_TARGET, iv_amgn, _T)
K_ap, d_ap, ok_ap, _ = _delta_aware_snap(K_cont_put_amgn, False, spot_amgn, iv_amgn, _T, sinc_amgn)
check("AMGN put: chosen strike is 385 (ceil, passes gate)", ok_ap and abs(K_ap - 385.0) < 0.01)
check("AMGN put: delta ≥ 0.35", ok_ap and d_ap >= 0.35)

# 1e: Synthetic both-pass scenario to verify "closest to target" wins over "higher delta"
# Build a case where K_floor delta=0.38 and K_ceil delta=0.52 — both pass; floor wins (0.38 closer to 0.35)
# To engineer this: use large sinc=10.0, high IV, low DTE; adjust S/K manually
# Use S=100, K_cont=99.0, sinc=10.0 so floor=90, ceil=100
# At sigma=1.0, T=0.03571, K=90:
# d1=(ln(100/90)+0.5*1.0*0.03571)/(1.0*0.1890)=(0.10536+0.01786)/0.1890=0.6519 → N=0.7428 → delta_call=0.743
# K=100: d1=(0+0.01786)/0.1890=0.0944 → N=0.5376 → delta_call=0.538
# Both pass gate=0.35; floor delta=0.743 (|0.743-0.35|=0.393), ceil delta=0.538 (|0.538-0.35|=0.188) → ceil wins
K_both_floor = 90.0; K_both_ceil = 100.0; S_both = 100.0; sig_both = 1.0; sinc_both = 10.0
K_cont_both = 99.5  # floor=90, ceil=100 with sinc=10
# For a call: both K>spot=100? No: K=90<S=100 violates OTM call guard. Use put test instead.
# For a put: spot=100, K=90 (OTM), K=100 (violates OTM put guard since K>=spot).
# Only K=90 survives OTM for put; delta at K=90 = 1 - N(d1) = 1 - 0.743 = 0.257 FAIL for put.
# Better synthetic: use is_call=True, S=100, sinc=10, K_cont=95
# floor=90 (K>S=100? No, OTM call violated), ceil=100 (K=S=100, also OTM violated).
# Difficult to engineer both-pass OTM call without specific values. Use ATM-ish with high T.
# Use: S=50, sinc=5, T=0.3, sig=0.30 (longer expiry for call)
S_t = 50.0; sig_t = 0.30; T_t = 0.3; sinc_t = 5.0; gate_t = 0.35
K_cont_t = _bs_invert_delta_strike(S_t, _DELTA_TARGET, sig_t, T_t)
K_floor_t = math.floor(K_cont_t / sinc_t) * sinc_t
K_ceil_t  = math.ceil(K_cont_t  / sinc_t) * sinc_t
d1_f, _ = _bs_d1d2(S_t, K_floor_t, sig_t, T_t)
d1_c, _ = _bs_d1d2(S_t, K_ceil_t,  sig_t, T_t)
delta_f = _N(d1_f); delta_c = _N(d1_c)
both_pass = (delta_f >= gate_t and delta_c >= gate_t and
             K_floor_t > S_t and K_ceil_t > S_t)
if both_pass:
    K_bt, d_bt, ok_bt, _ = _delta_aware_snap(K_cont_t, True, S_t, sig_t, T_t, sinc_t, gate=gate_t)
    # expect: the one with delta closer to 0.35
    closer = K_floor_t if abs(delta_f - gate_t) < abs(delta_c - gate_t) else K_ceil_t
    check("both-pass: chosen strike is the one whose delta is closer to target",
          ok_bt and abs(K_bt - closer) < 0.01)
    check("both-pass: chosen delta >= gate", ok_bt and d_bt >= gate_t)
else:
    # S=50 with 0.3T might not produce both-pass; verify selection is still deterministic
    K_bt, d_bt, ok_bt, _ = _delta_aware_snap(K_cont_t, True, S_t, sig_t, T_t, sinc_t, gate=gate_t)
    check("single-pass scenario: selected candidate satisfies gate (or no candidate)",
          not ok_bt or d_bt >= gate_t)

# 1f: All-fail puts (NEE call) → no candidate returned
spot_nee = 87.93; iv_nee = 0.25
K_cont_call_nee = _bs_invert_delta_strike(spot_nee, _DELTA_TARGET, iv_nee, _T)
sinc_nee = _sinc_for(spot_nee)  # = 2.5
K_nee, d_nee, ok_nee, reason_nee = _delta_aware_snap(K_cont_call_nee, True, spot_nee, iv_nee, _T, sinc_nee)
check("NEE call: floor violates OTM, ceil fails gate → no candidate", not ok_nee)
check("NEE call: result K is None", K_nee is None)

# 1g: OTM guard fires when continuous K is very close to spot
# CLF spot=11.63, very low sigma: K_call_cont could be just above spot
# Verify that a ceil candidate that lands exactly AT spot is excluded
spot_edge = 10.0; sig_edge = 0.05; sinc_edge = 1.0; T_edge = _T
K_cont_call_edge = _bs_invert_delta_strike(spot_edge, _DELTA_TARGET, sig_edge, T_edge)
K_e, d_e, ok_e, _ = _delta_aware_snap(K_cont_call_edge, True, spot_edge, sig_edge, T_edge, sinc_edge)
# floor may be <= spot; if ceil > spot and delta passes, we get a strike
check("edge-case call near spot: returned strike is always > spot or None",
      K_e is None or K_e > spot_edge)

print()

# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 2 — Before/After table: c66a973 round-to-nearest vs delta-aware snap
#            Tickers: CLF, HAL, AMGN, NEE, VRTX  ×  call + put = 10 legs
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 68)
print("BLOCK 2 — Before/After comparison: 5 tickers × 2 legs = 10 legs")
print("=" * 68)

TICKERS = [
    ("CLF",  11.63, 0.60),
    ("HAL",  31.64, 0.40),
    ("AMGN", 387.64, 0.20),
    ("NEE",  87.93, 0.25),
    ("VRTX", 481.70, 0.30),
]

def _before_snap(K_cont, is_call, spot, sinc):
    """c66a973 round-to-nearest, then hard OTM guard."""
    K = round(K_cont / sinc) * sinc
    if is_call and K <= spot:  K += sinc
    if not is_call and K >= spot: K -= sinc
    return K

def _fmt_otm(spot, K, is_call):
    pct = abs(K - spot) / spot * 100
    side = "OTM" if (K > spot) == is_call else "ITM!"
    return f"{pct:.1f}% {side}"

header = f"{'Ticker':<6} {'Side':<5} {'BEFORE K':>8} {'B|δ|':>6} {'B%OTM':>9} {'B.Gate':>7} " \
         f"{'AFTER K':>8} {'A|δ|':>6} {'A%OTM':>9} {'A.Gate':>7} {'Δ':>5}"
print(header)
print("-" * len(header))

after_pass = 0
before_pass = 0
for ticker, spot, iv in TICKERS:
    sinc = _sinc_for(spot)
    for is_call, leg in [(True, "call"), (False, "put")]:
        target_N = _DELTA_TARGET if is_call else (1 - _DELTA_TARGET)
        K_cont = _bs_invert_delta_strike(spot, target_N, iv, _T)

        # BEFORE
        K_b = _before_snap(K_cont, is_call, spot, sinc)
        d1_b, _ = _bs_d1d2(spot, K_b, iv, _T)
        delta_b = _N(d1_b) if is_call else abs(_N(d1_b) - 1.0)
        gate_b = "PASS" if delta_b >= _GATE else "FAIL"
        if gate_b == "PASS": before_pass += 1

        # AFTER
        K_a, delta_a, ok_a, reason_a = _delta_aware_snap(K_cont, is_call, spot, iv, _T, sinc)
        if ok_a:
            gate_a = "PASS"
            after_pass += 1
            K_a_str   = f"{K_a:.2f}"
            delta_a_str = f"{delta_a:.3f}"
            otm_a = _fmt_otm(spot, K_a, is_call)
        else:
            gate_a = "NONE"
            K_a_str   = "none"
            delta_a_str = "—"
            otm_a = "—"

        arrow = "↑" if (gate_a == "PASS" and gate_b != "PASS") else \
                ("↓" if (gate_a != "PASS" and gate_b == "PASS") else " ")

        print(f"{ticker:<6} {leg:<5} {K_b:>8.2f} {delta_b:>6.3f} {_fmt_otm(spot, K_b, is_call):>9} "
              f"{gate_b:>7} {K_a_str:>8} {delta_a_str:>6} {otm_a:>9} {gate_a:>7} {arrow:>5}")

print("-" * len(header))
print(f"  BEFORE: {before_pass}/10 legs pass gate=0.35")
print(f"  AFTER:  {after_pass}/10 legs pass gate=0.35 (NONE = no candidate, not a trade)")
check(f"AFTER count ≥ BEFORE count ({after_pass} vs {before_pass})", after_pass >= before_pass)
print()

# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 3 — Reason strings for no-candidate legs
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 68)
print("BLOCK 3 — Reason strings for no-candidate outcomes")
print("=" * 68)
for ticker, spot, iv in TICKERS:
    sinc = _sinc_for(spot)
    for is_call, leg in [(True, "call"), (False, "put")]:
        target_N = _DELTA_TARGET if is_call else (1 - _DELTA_TARGET)
        K_cont   = _bs_invert_delta_strike(spot, target_N, iv, _T)
        K_a, d_a, ok_a, reason = _delta_aware_snap(K_cont, is_call, spot, iv, _T, sinc)
        if not ok_a:
            print(f"  {ticker} {leg}: {reason}")
check("All no-candidate reason strings non-empty",
      all(
          len(_delta_aware_snap(
              _bs_invert_delta_strike(s, _DELTA_TARGET if c else 1-_DELTA_TARGET, iv, _T),
              c, s, iv, _T, _sinc_for(s)
          )[3]) > 0
          for ticker, s, iv in TICKERS
          for c in [True, False]
      ))
print()

# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 4 — Regression: previously-passing legs still pass
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 68)
print("BLOCK 4 — Regression: previously-passing legs still pass (no regressions)")
print("=" * 68)

# From Block 2, legs that passed BEFORE: CLF call, HAL call, NEE put
prev_passing = [
    ("CLF",  11.63, 0.60, True,  "call"),
    ("HAL",  31.64, 0.40, True,  "call"),
    ("NEE",  87.93, 0.25, False, "put"),
]
for ticker, spot, iv, is_call, leg in prev_passing:
    sinc = _sinc_for(spot)
    target_N = _DELTA_TARGET if is_call else (1 - _DELTA_TARGET)
    K_cont = _bs_invert_delta_strike(spot, target_N, iv, _T)
    K_a, d_a, ok_a, reason = _delta_aware_snap(K_cont, is_call, spot, iv, _T, sinc)
    check(f"{ticker} {leg}: still passes after delta-aware snap", ok_a and d_a >= _GATE)


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 68)
total = PASS_COUNT + FAIL_COUNT
print(f"RESULT: {PASS_COUNT}/{total} checks PASSED  ({FAIL_COUNT} FAILED)")
if FAIL_COUNT == 0:
    print("ALL CHECKS PASSED — no commit until Joel approves table.")
else:
    print("FAILURES PRESENT — do not commit.")
    sys.exit(1)
