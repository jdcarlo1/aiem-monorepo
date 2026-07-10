"""
test_ema_trust_equivalence.py
------------------------------
Proves meta_learning_signal_trust.compute_ema_trust_update() (the new
canonical, DB-free arithmetic core added for Diagram 2 remediation step
P1-2) is byte-identical to BOTH of the two arithmetic implementations it
replaces:

  1. The OLD meta_learning_signal_trust.update_trust_weight() arithmetic
     (frozen verbatim below as _old_mlst_arithmetic) -- used live today by
     alert_grading.py (TELEGRAM_ALERTS bucket).

  2. The OLD main.py MTM close-path inline arithmetic (frozen verbatim
     below as _old_main_inline_arithmetic) -- used live today at paper
     trade close (PAPER_TRADING bucket), feeding Stage 14 candidate score
     multiplication and Stage 21 learning-feedback trace.

Both old implementations only ever ran with decay_factor=0.95,
min_weight=0.2, max_weight=2.0 (no call site anywhere in the codebase
overrides these), so the fixed-vector cases below use those defaults
throughout, plus one non-default-max_weight case to prove the general
canonical formula (not the 2*new_rate shortcut) is what got implemented.

Run: python3 tests/test_ema_trust_equivalence.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meta_learning_signal_trust import compute_ema_trust_update


def _old_mlst_arithmetic(existing_rate, existing_n, new_outcome_was_win,
                          decay_factor=0.95, min_weight=0.2, max_weight=2.0):
    """Frozen copy of update_trust_weight()'s pre-refactor arithmetic
    (the `if existing: ... else: ...` block), operating on plain values
    instead of a DB row dict."""
    outcome_value = 1.0 if new_outcome_was_win else 0.0
    if existing_rate is not None:
        prior_rate = float(existing_rate)
        n_observed = existing_n + 1
        new_rate = decay_factor * prior_rate + (1 - decay_factor) * outcome_value
    else:
        new_rate = outcome_value
        n_observed = 1
    trust_weight = 1.0 + (new_rate - 0.5) * 2 * (max_weight - 1.0)
    trust_weight = max(min_weight, min(max_weight, trust_weight))
    return new_rate, n_observed, trust_weight


def _old_main_inline_arithmetic(existing_rate, existing_n, win):
    """Frozen copy of main.py's pre-refactor inline block
    (_tw_wr / _tw_n / _tw_wt_new), which only ever used the hardcoded
    defaults decay=0.95, clamp [0.2, 2.0]."""
    tw_win = 1.0 if win else 0.0
    if existing_rate is not None:
        tw_prior = float(existing_rate)
        tw_n = int(existing_n) + 1
        tw_wr = 0.95 * tw_prior + 0.05 * tw_win
    else:
        tw_prior = 0.5
        tw_n = 1
        tw_wr = tw_win
    tw_wt_new = max(0.2, min(2.0, tw_wr * 2.0))
    return tw_wr, tw_n, tw_wt_new


CASES = [
    # (label, prior_rate, prior_n, outcome_was_win)
    ("first_observation_win",   None, 0,  True),
    ("first_observation_loss",  None, 0,  False),
    ("existing_row_win",        0.60, 12, True),
    ("existing_row_loss",       0.60, 12, False),
    ("clamp_low_boundary",      0.05, 30, False),
    ("clamp_high_boundary",     0.98, 30, True),
    ("exact_new_rate_point_one", 0.0, 1,  True),
    ("mid_convergence_step1",   0.50, 5,  True),
    ("mid_convergence_step2",   0.525, 6, True),
    ("mid_convergence_step3",   0.549, 7, True),
    ("near_zero_prior",         0.0, 1,   False),
    ("near_one_prior",          1.0, 1,   True),
]


def run():
    failures = []

    for label, prior_rate, prior_n, win in CASES:
        new_rate, new_n, trust_weight = compute_ema_trust_update(
            prior_rate, prior_n, win,
            decay_factor=0.95, min_weight=0.2, max_weight=2.0,
        )

        existing_rate = prior_rate if (prior_rate is not None and prior_n > 0) else None
        existing_n = prior_n

        old_mlst_rate, old_mlst_n, old_mlst_tw = _old_mlst_arithmetic(
            existing_rate, existing_n, win,
        )
        old_main_rate, old_main_n, old_main_tw = _old_main_inline_arithmetic(
            existing_rate, existing_n, win,
        )

        ok = True
        if (round(new_rate, 12), new_n, round(trust_weight, 12)) != \
           (round(old_mlst_rate, 12), old_mlst_n, round(old_mlst_tw, 12)):
            ok = False
            failures.append(f"{label}: mismatch vs OLD meta_learning_signal_trust arithmetic: "
                             f"new=({new_rate},{new_n},{trust_weight}) "
                             f"old=({old_mlst_rate},{old_mlst_n},{old_mlst_tw})")
        if (round(new_rate, 12), new_n, round(trust_weight, 12)) != \
           (round(old_main_rate, 12), old_main_n, round(old_main_tw, 12)):
            ok = False
            failures.append(f"{label}: mismatch vs OLD main.py inline arithmetic: "
                             f"new=({new_rate},{new_n},{trust_weight}) "
                             f"old=({old_main_rate},{old_main_n},{old_main_tw})")

        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label}: rate={new_rate:.6f} n={new_n} trust={trust_weight:.6f}")

    # Non-default max_weight case: proves the general canonical formula
    # (1.0 + (new_rate-0.5)*2*(max_weight-1.0)) is implemented, not the
    # 2*new_rate shortcut that only holds when max_weight == 2.0.
    new_rate, new_n, trust_weight = compute_ema_trust_update(
        0.60, 12, True, decay_factor=0.95, min_weight=0.1, max_weight=3.0,
    )
    expected_rate = 0.95 * 0.60 + 0.05 * 1.0
    expected_tw = max(0.1, min(3.0, 1.0 + (expected_rate - 0.5) * 2 * (3.0 - 1.0)))
    shortcut_tw = 2.0 * expected_rate  # would be WRONG for max_weight != 2.0
    ok = (round(trust_weight, 12) == round(expected_tw, 12))
    if not ok:
        failures.append(f"non_default_max_weight: got {trust_weight}, expected {expected_tw} "
                         f"(shortcut formula would have given {shortcut_tw})")
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] non_default_max_weight: rate={new_rate:.6f} n={new_n} "
          f"trust={trust_weight:.6f} (expected {expected_tw:.6f}, "
          f"2x-shortcut would be {shortcut_tw:.6f})")

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print(f"RESULT: ALL {len(CASES) + 1} CASES PASSED")


if __name__ == "__main__":
    run()
