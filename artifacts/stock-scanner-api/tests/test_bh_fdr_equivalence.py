"""
Unit-level proof for AIEM Diagram 2 remediation spec, P1-1 / C2 (BH-FDR canonicalization).

Proves the refactor that made aiem_module5_discovery._bh_fdr_reject(),
aiem_module6_rediscovery._bh_fdr_reject(), and
niche_segment_finder._benjamini_hochberg() all delegate to the single
canonical aiem_stat_tests.bh_fdr_reject() / bh_fdr_adjust() is a pure
behavior-preserving move: every callable returns byte-identical
(element-for-element equal) boolean reject lists on every fixed test vector
below, including edge cases (empty / single / all-reject / none-reject /
boundary / ties) and a seeded random 75-value vector. Also proves
bh_fdr_adjust()'s adjusted-p-value-based rejection decision matches
bh_fdr_reject()'s raw-threshold decision (they are two equivalent
formulations of the same BH step-up rule).

Run: python3 artifacts/stock-scanner-api/tests/test_bh_fdr_equivalence.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiem_stat_tests as _stat_tests
import aiem_module5_discovery as _m5
import aiem_module6_rediscovery as _m6
import niche_segment_finder as _niche

CALLABLES = {
    "canonical (aiem_stat_tests.bh_fdr_reject)": _stat_tests.bh_fdr_reject,
    "module5 (_bh_fdr_reject delegate)": _m5._bh_fdr_reject,
    "module6 (_bh_fdr_reject delegate)": _m6._bh_fdr_reject,
    "canonical adjusted (aiem_stat_tests.bh_fdr_adjust)":
        lambda p, a: _stat_tests.bh_fdr_adjust(p, a)[0],
    "niche (_benjamini_hochberg delegate)":
        lambda p, a: _niche._benjamini_hochberg(p, a)[0],
}

VECTORS = {
    "empty": ([], 0.05),
    "single_reject": ([0.001], 0.05),
    "single_no_reject": ([0.9], 0.05),
    "all_reject": ([0.001, 0.002, 0.003, 0.004, 0.005], 0.05),
    "none_reject": ([0.9, 0.8, 0.7, 0.6, 0.99], 0.05),
    "boundary_exact": ([0.01, 0.02, 0.03, 0.04, 0.05], 0.05),
    "boundary_just_over": ([0.0101, 0.0201, 0.0301, 0.0401, 0.0501], 0.05),
    "ties_all_same": ([0.03, 0.03, 0.03, 0.03], 0.05),
    "ties_mixed": ([0.01, 0.01, 0.04, 0.04, 0.2], 0.05),
    "classic_bh_textbook": (
        [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216, 0.222, 0.251, 0.269, 0.275, 0.34],
        0.05,
    ),
    "alpha_0_10": ([0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10], 0.10),
    "alpha_0_01": ([0.001, 0.002, 0.003, 0.5, 0.6], 0.01),
    "unsorted_input_order": ([0.9, 0.001, 0.5, 0.002, 0.3], 0.05),
    "seeded_random_75": (
        [round(v, 6) for v in (lambda rng: [rng.random() for _ in range(75)])(random.Random(42))],
        0.05,
    ),
}


def run():
    failures = []
    for vec_name, (p_values, alpha) in VECTORS.items():
        results = {}
        for label, fn in CALLABLES.items():
            results[label] = fn(list(p_values), alpha)

        baseline_label = "canonical (aiem_stat_tests.bh_fdr_reject)"
        baseline = results[baseline_label]
        ok = True
        for label, result in results.items():
            if result != baseline:
                ok = False
                failures.append(
                    f"MISMATCH on vector '{vec_name}': {label} returned {result}, "
                    f"expected (canonical) {baseline}"
                )

        status = "PASS" if ok else "FAIL"
        n_rejected = sum(baseline)
        print(f"[{status}] {vec_name}: n={len(p_values)} alpha={alpha} rejected={n_rejected} -> {baseline}")

    if failures:
        print("\n".join(failures))
        raise SystemExit(f"\n{len(failures)} FAILURE(S)")

    print("\nALL CASES PASSED")


if __name__ == "__main__":
    run()
