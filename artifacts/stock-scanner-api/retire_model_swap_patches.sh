#!/bin/bash
# retire_model_swap_patches.sh
#
# Run from your project root (in the artifacts/stock-scanner-api/ folder
# if that's where model_swap_patches.py and online_learning.py live):
#
#   bash retire_model_swap_patches.sh
#
# What it does:
#   1. Confirms rollback_to_version() in online_learning.py is actually
#      called somewhere live (not just defined and unused).
#   2. Prepends a loud DEPRECATED warning header to model_swap_patches.py
#      so no future agent session wires it in and corrupts the live
#      model_versions table.
#   3. Renames the file so it's unmistakably inactive.

echo "===================================================="
echo "1) Confirm rollback_to_version() is actually called somewhere"
echo "===================================================="
grep -rn "rollback_to_version(" --include="*.py" . 2>/dev/null
echo ""
echo "(if the ONLY hit is the def itself inside online_learning.py,"
echo " rollback exists but is not wired to anything live yet)"
echo ""

echo "===================================================="
echo "2) Prepending DEPRECATED warning header to model_swap_patches.py"
echo "===================================================="

if [ -f "model_swap_patches.py" ]; then
    TARGET="model_swap_patches.py"
elif [ -f "artifacts/stock-scanner-api/model_swap_patches.py" ]; then
    TARGET="artifacts/stock-scanner-api/model_swap_patches.py"
else
    echo "model_swap_patches.py not found in root or artifacts/stock-scanner-api/."
    echo "Find it manually with: find . -iname 'model_swap_patches.py'"
    exit 1
fi

HEADER='"""
DEPRECATED -- DO NOT WIRE IN.

This file'"'"'s model_versions table schema (version_label / deployed_at /
is_active / notes / rolled_back_at) conflicts with the LIVE model_versions
table owned by online_learning.py (model_name / version / is_live).

online_learning.py already provides full equivalent functionality:
  get_live_model()        <- equivalent of get_active_version()
  propose_update()        <- equivalent of deploy_new_version()
  rollback_to_version()   <- equivalent of rollback_to_previous()
  version_history()

Wiring this file in as-is will silently no-op on CREATE TABLE (table
already exists under the other schema) and then crash or corrupt data
on INSERT (columns do not match). Superseded -- kept for reference only.
"""

'

TMP_FILE=$(mktemp)
echo "$HEADER" > "$TMP_FILE"
cat "$TARGET" >> "$TMP_FILE"
mv "$TMP_FILE" "$TARGET"

echo "Header added to: $TARGET"
echo ""

echo "===================================================="
echo "3) Renaming file to make it unmistakably inactive"
echo "===================================================="
NEW_NAME="${TARGET%.py}.py.DEPRECATED_use_online_learning_py"
mv "$TARGET" "$NEW_NAME"
echo "Renamed:"
echo "  $TARGET"
echo "  -> $NEW_NAME"
echo ""
echo "Done. model_swap_patches.py is now clearly marked deprecated and"
echo "renamed so it won't accidentally be imported (Python won't import"
echo "a file with a .py.DEPRECATED_use_online_learning_py extension)."
