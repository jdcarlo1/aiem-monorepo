#!/usr/bin/env bash
# git-autosync.sh — Automatically pushes tracked-file changes to the 'dev' branch every 30 min.
# Only stages files already tracked by git (git add -u), so new untracked runtime
# artifacts are never accidentally committed.
# New files are only committed when explicitly added during a session.

set -euo pipefail

REPO_DIR="/home/runner/workspace"
REMOTE_BRANCH="dev"
INTERVAL=1800  # 30 minutes

echo "[autosync] started — pushing tracked changes to origin/${REMOTE_BRANCH} every ${INTERVAL}s"

while true; do
    cd "$REPO_DIR"

    # Stage only already-tracked files (no new untracked files)
    git add -u

    if ! git diff --cached --quiet; then
        TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        git commit -m "auto-sync: ${TIMESTAMP}"
        git push origin "HEAD:${REMOTE_BRANCH}"
        echo "[autosync] ${TIMESTAMP} — pushed to origin/${REMOTE_BRANCH}"
    else
        echo "[autosync] $(date -u +"%H:%MZ") — no tracked-file changes, skipping"
    fi

    sleep "$INTERVAL"
done
