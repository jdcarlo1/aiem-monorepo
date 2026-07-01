"""
staleness_guard.py — Fix #11 (restart-on-commit gap)

Problem: a fix can be correct on disk while the running process keeps serving
old bytecode indefinitely (this happened once during F8 verification and was
only caught by a manual process-start-time vs. file-mtime comparison).

This module is a fully isolated, additive watchdog:
  - Records this process's start time, the mtime of the watched source
    file(s) at the moment it loaded them, and the git commit SHA at boot.
  - A daemon background thread re-checks those file mtimes every
    _CHECK_INTERVAL_SEC seconds. If any watched file's on-disk mtime is
    newer than what this process loaded, the process is DEFINITELY serving
    stale code (the file changed after this interpreter read it).
  - On detecting staleness: logs a loud, repeated CRITICAL banner (fail
    loud, never fail silent), then attempts an automatic in-place restart
    via os.execv (same PID, fresh interpreter re-reads the file from disk).
    If the exec attempt itself raises, the process is left flagged
    `is_stale=True` and keeps re-logging every cycle until resolved
    (fail-closed: staleness is never silently dropped).
  - Exposes get_process_info() for a read-only health endpoint so staleness
    can also be checked externally at any time (git SHA, pid, mtimes).

Nothing in this module touches any other part of the app. It only reads
file mtimes / git metadata and, on staleness, re-execs the current process.
"""
import os
import sys
import time
import datetime
import threading
import subprocess

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# Only the primary monolithic file is watched. Keeping the watch-list narrow
# is deliberate: watching the whole repo would trigger restarts for edits to
# completely unrelated artifacts (nclex-prep, mockup-sandbox, etc).
_MAIN_FILE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"))
_WATCHED_FILES = [_MAIN_FILE]

_CHECK_INTERVAL_SEC = 15

_PROCESS_START_TIME = time.time()
_START_MTIMES = {f: (os.path.getmtime(f) if os.path.exists(f) else None) for f in _WATCHED_FILES}

_is_stale = False
_stale_detected_at = None
_restart_attempted = False
_restart_error = None


def _git_commit_sha():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, timeout=3, text=True,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


_START_GIT_SHA = _git_commit_sha()


def _iso(ts):
    return datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z" if ts else None


def get_process_info():
    """Read-only snapshot for the /stock-api/process-info health endpoint."""
    current_mtimes = {f: (os.path.getmtime(f) if os.path.exists(f) else None) for f in _WATCHED_FILES}
    stale_files = [
        f for f in _WATCHED_FILES
        if current_mtimes.get(f) and _START_MTIMES.get(f) and current_mtimes[f] > _START_MTIMES[f]
    ]
    return {
        "pid": os.getpid(),
        "process_start_time": _iso(_PROCESS_START_TIME),
        "process_start_git_sha": _START_GIT_SHA,
        "current_git_sha": _git_commit_sha(),
        "watched_files": _WATCHED_FILES,
        "loaded_mtimes": {f: _iso(t) for f, t in _START_MTIMES.items()},
        "current_mtimes": {f: _iso(t) for f, t in current_mtimes.items()},
        "stale_files": stale_files,
        "is_stale": bool(stale_files) or _is_stale,
        "auto_restart_attempted": _restart_attempted,
        "restart_error": _restart_error,
        "check_interval_sec": _CHECK_INTERVAL_SEC,
    }


def _watchdog_loop():
    global _is_stale, _stale_detected_at, _restart_attempted, _restart_error
    while True:
        time.sleep(_CHECK_INTERVAL_SEC)
        try:
            for f in _WATCHED_FILES:
                if not os.path.exists(f):
                    continue
                cur_mtime = os.path.getmtime(f)
                start_mtime = _START_MTIMES.get(f)
                if start_mtime is not None and cur_mtime > start_mtime:
                    _is_stale = True
                    _stale_detected_at = time.time()
                    print("=" * 78, flush=True)
                    print(
                        f"[STALENESS-GUARD] CRITICAL: {f} changed on disk "
                        f"(loaded_mtime={start_mtime}, current_mtime={cur_mtime}). "
                        f"This process (pid={os.getpid()}, started {_iso(_PROCESS_START_TIME)}, "
                        f"git_sha_at_boot={_START_GIT_SHA}) is serving STALE code. "
                        f"Attempting automatic self-restart via os.execv now.",
                        flush=True,
                    )
                    print("=" * 78, flush=True)
                    _restart_attempted = True
                    try:
                        os.execv(sys.executable, [sys.executable] + sys.argv)
                        # On success, this process image is replaced — nothing
                        # below this line executes in the old process.
                    except Exception as _exec_e:
                        _restart_error = str(_exec_e)
                        print(
                            f"[STALENESS-GUARD] CRITICAL: os.execv restart FAILED: "
                            f"{_exec_e}. Process remains flagged is_stale=True and "
                            f"will keep re-logging every {_CHECK_INTERVAL_SEC}s until "
                            f"manually restarted. This is intentional fail-closed "
                            f"behavior — staleness is never silently dropped.",
                            flush=True,
                        )
        except Exception as e:
            print(f"[STALENESS-GUARD] watchdog loop error (will retry next cycle): {e}", flush=True)


def start_watchdog():
    t = threading.Thread(target=_watchdog_loop, daemon=True, name="staleness-guard")
    t.start()
    print(
        f"[STALENESS-GUARD] started; watching {_WATCHED_FILES}, "
        f"check_interval={_CHECK_INTERVAL_SEC}s, pid={os.getpid()}, "
        f"git_sha_at_boot={_START_GIT_SHA}",
        flush=True,
    )
