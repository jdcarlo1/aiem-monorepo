#!/usr/bin/env bash
# tools/hooks/install.sh — one-time setup for tracked git hooks.
#
# Run once after cloning:
#   bash tools/hooks/install.sh
#
# What it does: copies tracked hook files from tools/hooks/ into .git/hooks/
# and makes them executable.  Idempotent — safe to run multiple times.

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
HOOKS_SRC="${REPO_ROOT}/tools/hooks"
HOOKS_DST="${REPO_ROOT}/.git/hooks"

for src_file in "${HOOKS_SRC}"/*; do
    fname="$(basename "$src_file")"
    [[ "$fname" == "install.sh" ]] && continue   # skip this installer

    dst_file="${HOOKS_DST}/${fname}"
    cp -f "$src_file" "$dst_file"
    chmod +x "$dst_file"
    echo "Installed: tools/hooks/${fname} -> .git/hooks/${fname}"
done

echo ""
echo "Git hooks installed.  Run 'bash tools/hooks/install.sh' again after pulling updates."
