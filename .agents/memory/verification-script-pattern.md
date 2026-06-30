---
name: User-requested verification scripts
description: How to respond when the user asks for a "verification code/script" to independently confirm a feature actually ran in production
---

When the user asks for a "verification code" or "verification script" for a module
just built/deployed, they mean a standalone shell script they run themselves later
(not now) to falsify-test the claim — not a literal OTP/2FA code. Look for an
existing template first: `artifacts/stock-scanner-api/scripts/verify_*.sh`.

**Why:** this user has been burned before by self-reported "it works" claims that
weren't independently checkable; these scripts are deliberately built so a
fabricated answer is hard to fake.

**How to apply:** model new verification scripts on the existing ones. Required
sections: (1) real-time anchors — shell date, ET date, `SELECT NOW()` from the DB,
so staleness is obvious; (2) a market-hours/weekday sanity gate; (3) direct SQL
against the actual tables the feature writes (job_log for scheduler firing,
feature-specific tables for output), grouped/counted, not just "row exists"; (4) a
cross-check that output values are genuinely distinct per row (not one templated
string repeated); (5) a grep against the live process log file under
`/tmp/logs/` for corroborating lines; (6) an explicit "FINAL VERDICT CRITERIA"
checklist so the user (or a future agent) can judge pass/fail objectively instead
of trusting prose. Save the script under
`artifacts/stock-scanner-api/scripts/verify_<feature>.sh` and `chmod +x` it.
