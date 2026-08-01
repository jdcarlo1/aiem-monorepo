#!/usr/bin/env bash
# tools/install_hooks.sh
#
# Installs git hooks from tools/ into .git/hooks/.
# Run after every fresh clone or Replit environment reset.
#
# Usage:
#   bash tools/install_hooks.sh
#
# What it installs:
#   tools/pre-push  →  .git/hooks/pre-push  (TLA pre-push gate)
#
# The pre-push hook fires on every `git push` (manual terminal, agent, daemon)
# and calls tools/check_protected_push.py to enforce TLA compliance.
#
# KNOWN LIMITATION: `git push --no-verify` still bypasses hooks by git design.
# Document any --no-verify use in tools/trading_logic_approvals.jsonl.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="${REPO_ROOT}/.git/hooks"
TOOLS_DIR="${REPO_ROOT}/tools"

install_hook() {
    local src="${TOOLS_DIR}/$1"
    local dst="${HOOKS_DIR}/$1"
    if [ ! -f "${src}" ]; then
        echo "ERROR: source hook not found: ${src}" >&2
        exit 1
    fi
    cp "${src}" "${dst}"
    chmod +x "${dst}"
    echo "INSTALLED: ${src} -> ${dst} (executable)"
}

echo "Installing git hooks from tools/ into .git/hooks/ ..."
install_hook "pre-push"
echo "Done."
echo ""
echo "Installed hooks:"
ls -la "${HOOKS_DIR}/pre-push"
