#!/bin/bash
# check_model_swap_wiring.sh
#
# Run this from your project root in the Replit Shell tab:
#   bash check_model_swap_wiring.sh
#
# It checks two things needed to decide how to wire model_swap_patches.py in:
#   1. Does the model_versions DB table already exist anywhere (SQL/migrations/code)?
#   2. Where does your retrain pipeline currently load/save the active model?

echo "===================================================="
echo "1) Searching for 'model_versions' table references"
echo "===================================================="
grep -rn "model_versions" *.py 2>/dev/null
echo ""
echo "(no output above = table is not yet referenced/created anywhere)"
echo ""

echo "===================================================="
echo "2) Searching for model load/save functions in retrain files"
echo "===================================================="
grep -n "load_model\|save_model\|active_model\|current_model" retrain_pipeline.py model_training.py 2>/dev/null
echo ""
echo "(no output above = those exact terms weren't found - the retrain/load"
echo " logic may use different function/variable names worth grepping for)"
echo ""

echo "===================================================="
echo "3) Bonus: confirm model_swap_patches.py functions are still unused"
echo "===================================================="
grep -rn "deploy_new_version\|rollback_to_previous\|get_active_version" *.py 2>/dev/null
echo ""
echo "(only hits inside model_swap_patches.py itself = still fully unwired)"
