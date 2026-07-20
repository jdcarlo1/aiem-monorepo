"""
check_clean_tree.py — Strict working-tree cleanliness checker for DPL sealed runs.

Replaces the broad `grep -v '^??'` exclusion with an explicit allowlist.
Called by verified_run.sh before executing the verifier.

Usage:
    git status --porcelain=v1 -z > /tmp/git_status.bin
    python3 tools/check_clean_tree.py \\
        --status-file /tmp/git_status.bin \\
        --allow-exact "tools/verified_run_seq" \\
        --allow-exact "dpl/engine_integrity_refs.json"

Exit 0  = tree is acceptably clean (only allowlisted modifications present)
Exit 1  = unacceptable modifications or untracked code/config/executable files found

Design:
  - Parses NUL-delimited git status (porcelain v1 -z) safely — no shell word splitting.
  - Tracked modifications (M/A/D/R/C/U and any XY combination):
      PASS only if the path is in the explicit allowlist (exact match, no wildcards/prefixes).
  - Untracked files (??):
      FAIL if the file has a code/config/executable extension or is executable (mode 0o111).
      PASS if the file is a .log in the designated evidence directory.
      PASS otherwise (e.g., .txt notes, .md workspace docs).
  - Renamed/copied files (R/C XY): ALWAYS FAIL (allowlist cannot cover rename targets).
  - Symlinks, directories: ALWAYS FAIL.
  - Path traversal (.. in path): ALWAYS FAIL.
  - Produces SHA-256 of the raw NUL-delimited status input for chain binding.

Allowlist format: exact relative paths from repo root (e.g. "tools/verified_run_seq").
No wildcards, no prefix matches, no directory matches.
"""

import argparse
import hashlib
import os
import stat
import sys
from pathlib import PurePosixPath

# ── Extension classification ───────────────────────────────────────────────────
# Files with these extensions are "code / config / secrets" — untracked presence fails.
_CODE_EXTS = frozenset({
    ".py", ".pyc", ".pyo",
    ".sh", ".bash", ".zsh",
    ".sql", ".psql",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".envrc",
    ".js", ".ts", ".jsx", ".tsx",
    ".rb", ".go", ".rs", ".c", ".cpp", ".h",
    ".dockerfile", ".containerfile",
    ".key", ".pem", ".crt", ".cer",
    ".secret",
})

# Dedicated generated-evidence directory: untracked .log files here are allowed.
_EVIDENCE_LOG_DIR = "tools/logs"
_EVIDENCE_LOG_SUFFIX = ".log"


def _is_evidence_log(path: str) -> bool:
    """True if path is a .log file under the designated evidence directory."""
    try:
        pp = PurePosixPath(path)
        return (
            pp.suffix == _EVIDENCE_LOG_SUFFIX
            and str(pp.parent) == _EVIDENCE_LOG_DIR
        )
    except Exception:
        return False


def _has_traversal(path: str) -> bool:
    """True if the path contains .. or starts with /."""
    parts = PurePosixPath(path).parts
    return ".." in parts or (len(parts) > 0 and parts[0] == "/")


def _is_executable(abs_path: str) -> bool:
    """True if the file has any execute bit set."""
    try:
        return bool(os.stat(abs_path).st_mode & 0o111)
    except OSError:
        return False


def _is_symlink(abs_path: str) -> bool:
    try:
        return os.path.islink(abs_path)
    except OSError:
        return False


def _is_dir(abs_path: str) -> bool:
    try:
        return os.path.isdir(abs_path)
    except OSError:
        return False


def parse_nul_status(raw: bytes) -> list[tuple[str, str, str | None]]:
    """
    Parse git status --porcelain=v1 -z output.

    Each entry is one of:
      XY SP path NUL              (normal, delete, add, untracked)
      XY SP orig NUL new NUL      (rename R, copy C)

    Returns list of (xy, path, orig_path_or_None).
    xy  = 2-char status code e.g. " M", "M ", "??", "R ", "C "
    path = primary path (new path for renames/copies)
    orig = original path for R/C, else None
    """
    entries = []
    records = raw.split(b"\x00")
    i = 0
    while i < len(records):
        rec = records[i]
        if not rec:
            i += 1
            continue
        if len(rec) < 4:
            i += 1
            continue
        xy = rec[:2].decode("utf-8", errors="replace")
        # Records are "XY path" (note the space at offset 2)
        if rec[2:3] != b" ":
            i += 1
            continue
        path = rec[3:].decode("utf-8", errors="replace")
        xy_stripped = xy.strip()
        if xy_stripped in ("R", "C"):
            # Next record is the new path (dest)
            if i + 1 < len(records) and records[i + 1]:
                new_path = records[i + 1].decode("utf-8", errors="replace")
                entries.append((xy, new_path, path))
                i += 2
            else:
                entries.append((xy, path, None))
                i += 1
        else:
            entries.append((xy, path, None))
            i += 1
    return entries


def check(
    status_file: str,
    allow_exact: list[str],
    repo_root: str | None = None,
) -> int:
    """
    Run the tree cleanliness check.
    Returns 0 (clean) or 1 (dirty/violation).
    Prints a line for every path with its classification.
    """
    with open(status_file, "rb") as f:
        raw = f.read()

    status_sha256 = hashlib.sha256(raw).hexdigest()
    allowlist_set = frozenset(allow_exact)

    print(f"[check_clean_tree] status_input_sha256={status_sha256}")
    print(f"[check_clean_tree] allowlist={sorted(allowlist_set)}")
    print(f"[check_clean_tree] allowlist_count={len(allowlist_set)}")

    entries = parse_nul_status(raw)
    print(f"[check_clean_tree] entries_parsed={len(entries)}")

    violations = []
    allowed_items = []
    skipped_items = []

    for xy, path, orig in entries:
        xy2 = xy.strip()
        is_untracked = xy2 == "??"
        is_rename    = xy2.startswith("R") or xy2.startswith("C")

        # Path traversal check — always fail
        if _has_traversal(path):
            msg = f"FAIL:PATH_TRAVERSAL  [{xy}] {path!r}"
            print(f"  {msg}")
            violations.append(msg)
            continue

        abs_path = os.path.join(repo_root, path) if repo_root else path

        # Symlink check — always fail
        if _is_symlink(abs_path):
            msg = f"FAIL:SYMLINK  [{xy}] {path!r}"
            print(f"  {msg}")
            violations.append(msg)
            continue

        # Directory check — always fail
        if _is_dir(abs_path):
            msg = f"FAIL:DIRECTORY  [{xy}] {path!r}"
            print(f"  {msg}")
            violations.append(msg)
            continue

        if is_untracked:
            # Untracked files: fail if code/config/executable; allow .log evidence
            ext = os.path.splitext(path)[1].lower()
            if _is_evidence_log(path):
                msg = f"ALLOW:EVIDENCE_LOG  [{xy}] {path}"
                print(f"  {msg}")
                allowed_items.append(msg)
                continue
            if ext in _CODE_EXTS:
                msg = f"FAIL:UNTRACKED_CODE  [{xy}] {path!r}  ext={ext!r}"
                print(f"  {msg}")
                violations.append(msg)
                continue
            if _is_executable(abs_path):
                msg = f"FAIL:UNTRACKED_EXECUTABLE  [{xy}] {path!r}"
                print(f"  {msg}")
                violations.append(msg)
                continue
            msg = f"ALLOW:UNTRACKED_NONCRITICAL  [{xy}] {path}"
            print(f"  {msg}")
            allowed_items.append(msg)
            continue

        if is_rename:
            # Renames always fail — allowlist covers exact paths, not rename targets
            msg = f"FAIL:RENAME_OR_COPY  [{xy}] {orig!r} → {path!r}"
            print(f"  {msg}")
            violations.append(msg)
            continue

        # Tracked modification (M/A/D and any XY combination)
        if path in allowlist_set:
            msg = f"ALLOW:ALLOWLISTED  [{xy}] {path}"
            print(f"  {msg}")
            allowed_items.append(msg)
        else:
            msg = f"FAIL:TRACKED_MODIFICATION  [{xy}] {path!r}  (not in allowlist)"
            print(f"  {msg}")
            violations.append(msg)

    print(f"[check_clean_tree] violations={len(violations)}")
    print(f"[check_clean_tree] allowed={len(allowed_items)}")
    print(f"[check_clean_tree] status_sha256={status_sha256}")

    if violations:
        print("[check_clean_tree] RESULT=DIRTY")
        return 1

    print("[check_clean_tree] RESULT=CLEAN")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strict NUL-delimited git status tree cleanliness checker."
    )
    parser.add_argument(
        "--status-file", required=True,
        help="Path to file containing git status --porcelain=v1 -z output (binary).",
    )
    parser.add_argument(
        "--allow-exact", action="append", default=[],
        dest="allow_exact",
        help="Exact repo-relative path to permit as tracked-modified. Repeatable.",
    )
    parser.add_argument(
        "--repo-root", default=None,
        help="Absolute path to repository root (for symlink/executable checks).",
    )
    args = parser.parse_args()

    rc = check(
        status_file=args.status_file,
        allow_exact=args.allow_exact,
        repo_root=args.repo_root,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
