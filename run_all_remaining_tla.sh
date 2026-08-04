#!/usr/bin/env bash
set -e

echo "############################################"
echo "STEP 0: current state"
echo "############################################"
git status
git diff --cached --stat
echo

echo "############################################"
echo "STEP 1: unstage main.py so aiem_options_scheduler.py stands alone"
echo "############################################"
git restore --staged artifacts/stock-scanner-api/main.py || true

echo "--- diff --stat after unstaging main.py (must show ONLY aiem_options_scheduler.py) ---"
STAT1=$(git diff --cached --stat)
echo "$STAT1"
if echo "$STAT1" | grep -q "main.py"; then
echo "!! main.py still appears in staged diff. Aborting before issuing a bad TLA."
exit 1
fi
if ! echo "$STAT1" | grep -q "aiem_options_scheduler.py"; then
echo "!! aiem_options_scheduler.py is not staged at all. Aborting."
exit 1
fi
echo "OK: only aiem_options_scheduler.py is staged."
echo

echo "############################################"
echo "STEP 2: issue clean single-file TLA for aiem_options_scheduler.py"
echo "############################################"
python3 tools/issue_tla.py \
--approved-by "Joel" \
--note "Tier C re-gate: aiem_options_scheduler.py - supersedes 9 fabricated retroactive TLA records. Directive_TieredRemediation_Execute_2026-08-03."
echo

echo "############################################"
echo "STEP 3: re-stage main.py alone (leave aiem_options_scheduler.py's approval as-is)"
echo "############################################"
git add artifacts/stock-scanner-api/main.py

echo "--- diff --stat after re-staging main.py (must show ONLY main.py) ---"
STAT2=$(git diff --cached --stat)
echo "$STAT2"
if echo "$STAT2" | grep -q "aiem_options_scheduler.py"; then
echo "!! aiem_options_scheduler.py still staged alongside main.py. Aborting."
exit 1
fi
echo "OK: only main.py is staged. (This should match the original approval 535c1c53 - not re-issuing it.)"
echo

echo "############################################"
echo "STEP 4: unstage main.py again so aiem_strat_scheduler.py stands alone"
echo "############################################"
git restore --staged artifacts/stock-scanner-api/main.py

echo "# Remediation: Directive_TieredRemediation_Execute_2026-08-03" >> artifacts/stock-scanner-api/aiem_strat_scheduler.py
git add artifacts/stock-scanner-api/aiem_strat_scheduler.py

echo "--- diff --stat right before TLA (must show ONLY aiem_strat_scheduler.py) ---"
STAT3=$(git diff --cached --stat)
echo "$STAT3"
if echo "$STAT3" | grep -q "main.py"; then
echo "!! main.py still appears in staged diff. Aborting before issuing a bad TLA."
exit 1
fi
if ! echo "$STAT3" | grep -q "aiem_strat_scheduler.py"; then
echo "!! aiem_strat_scheduler.py is not staged at all. Aborting."
exit 1
fi
echo "OK: only aiem_strat_scheduler.py is staged."

python3 tools/issue_tla.py \
--approved-by "Joel" \
--note "Tier C re-gate: aiem_strat_scheduler.py - supersedes 1 fabricated retroactive TLA record. Directive_TieredRemediation_Execute_2026-08-03."
echo

echo "############################################"
echo "STEP 5: re-stage main.py one last time so it's ready alongside the others"
echo "############################################"
git add artifacts/stock-scanner-api/main.py
echo

echo "############################################"
echo "STEP 6: FINAL STATE"
echo "############################################"
git status
git diff --cached --stat
echo
echo "DONE. Paste everything above back to Claude. Nothing has been committed."
