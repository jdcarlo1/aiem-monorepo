"""
staleness_guard.py — Fix #11 (restart-on-commit gap), expanded

Problem: a fix can be correct on disk while the running process keeps serving
old bytecode indefinitely (this happened once during F8 verification and was
only caught by a manual process-start-time vs. file-mtime comparison).

This module is an isolated watchdog with three independent staleness signals
and a graceful-drain restart:

  1. Per-module mtime watching, discovered dynamically. Every check cycle we
     scan sys.modules for any module whose __file__ lives under this same
     directory (main.py, or anything main.py imports, transitively, at any
     depth — including modules imported lazily at request time) that we are
     not yet watching, and start watching it with its mtime AT THE MOMENT WE
     NOTICE IT as the baseline (i.e. the moment it demonstrably got loaded
     into this process's memory). This is derived from the real import
     graph, not a hand-maintained list or a directory glob, so it can never
     pick up a file that isn't actually running in this process, and it
     naturally covers transitive imports. Third-party packages (under
     .pythonlibs / site-packages) are out of scope by design — a dependency
     version bump is a different failure mode than "this repo's own file
     changed under a running process."
  2. Git HEAD-SHA drift, re-checked every cycle (not just recorded once at
     boot) — catches a committed change even in the rare case a file's mtime
     comparison alone would miss it.
  3. (existing) main.py itself, watched from the very first cycle after
     boot, before any of main.py's later imports have even executed.

On detecting staleness via any signal:
  - Logs a loud, repeated CRITICAL banner (fail loud, never fail silent).
  - Begins a GRACEFUL DRAIN: stops admitting new requests (before_request
    hook starts returning 503) and waits for the live in-flight request
    counter to reach zero, polling every 0.5s, bounded by a 25s timeout.
    If the timeout is hit with requests still in flight, it force-restarts
    anyway and says so loudly (fail-closed: indefinite stale-serving is
    worse than dropping one hung request).
  - Restarts in place via os.execv (same PID, fresh interpreter re-reads
    every file from disk). If the exec attempt itself raises, draining is
    reversed (new requests resume) and the process stays flagged
    `is_stale=True`, re-logging every cycle until resolved.
  - Exposes get_process_info() for a read-only health endpoint, and
    mark_request_start()/mark_request_end() for the Flask request-lifecycle
    hooks that main.py wires in (before_request / teardown_request).

Nothing in this module touches route logic. main.py wiring is limited to:
import + start_watchdog() + one health route + one before_request/
teardown_request pair that only ever does something (reject with 503)
during the rare draining window.
"""
import os
import sys
import time
import datetime
import threading
import subprocess

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_STOCK_API_DIR = os.path.dirname(os.path.abspath(__file__))
_MAIN_FILE = os.path.abspath(os.path.join(_STOCK_API_DIR, "main.py"))

_CHECK_INTERVAL_SEC = 15
_DRAIN_TIMEOUT_SEC = 25
_DRAIN_POLL_SEC = 0.5

_PROCESS_START_TIME = time.time()

_watch_lock = threading.Lock()
_WATCHED_MTIMES = {_MAIN_FILE: (os.path.getmtime(_MAIN_FILE) if os.path.exists(_MAIN_FILE) else None)}

_state_lock = threading.Lock()
_inflight_count = 0
_draining = False

_is_stale = False
_stale_detected_at = None
_stale_reason = None
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


# ── in-flight request tracking (wired via Flask before_request/teardown_request) ──

def mark_request_start():
    """Call at the start of every request. Returns False (and the caller
    should short-circuit with a 503) if a drain is in progress."""
    global _inflight_count
    with _state_lock:
        if _draining:
            return False
        _inflight_count += 1
        return True


def mark_request_end():
    """Call unconditionally in teardown; only actually decrements if this
    request was counted (guarded by the caller checking mark_request_start's
    return value first, but safe to call regardless)."""
    global _inflight_count
    with _state_lock:
        _inflight_count = max(0, _inflight_count - 1)


def is_draining():
    with _state_lock:
        return _draining


def get_inflight_count():
    with _state_lock:
        return _inflight_count


# ── dynamic local-module discovery ──

def _discover_and_register_new_local_modules():
    """Scan sys.modules for local modules (under this directory) not yet
    watched, and start watching each with its current mtime as baseline."""
    newly_added = []
    for mod in list(sys.modules.values()):
        try:
            f = getattr(mod, "__file__", None)
            if not f:
                continue
            f = os.path.abspath(f)
            if not f.endswith(".py"):
                continue
            if os.path.commonpath([f, _STOCK_API_DIR]) != _STOCK_API_DIR:
                continue
        except Exception:
            continue
        with _watch_lock:
            if f not in _WATCHED_MTIMES:
                try:
                    _WATCHED_MTIMES[f] = os.path.getmtime(f)
                    newly_added.append(f)
                except OSError:
                    pass
    return newly_added


def get_process_info():
    """Read-only snapshot for the /stock-api/process-info health endpoint."""
    with _watch_lock:
        watched_snapshot = dict(_WATCHED_MTIMES)
    stale_files = []
    for f, start_mtime in watched_snapshot.items():
        if os.path.exists(f) and start_mtime is not None:
            cur = os.path.getmtime(f)
            if cur > start_mtime:
                stale_files.append(f)
    sha_now = _git_commit_sha()
    sha_drift = bool(_START_GIT_SHA and sha_now and sha_now != _START_GIT_SHA)
    return {
        "pid": os.getpid(),
        "process_start_time": _iso(_PROCESS_START_TIME),
        "process_start_git_sha": _START_GIT_SHA,
        "current_git_sha": sha_now,
        "git_sha_drift": sha_drift,
        "watched_file_count": len(watched_snapshot),
        "watched_files": sorted(watched_snapshot.keys()),
        "stale_files": stale_files,
        "is_stale": bool(stale_files) or sha_drift or _is_stale,
        "stale_reason": _stale_reason,
        "auto_restart_attempted": _restart_attempted,
        "restart_error": _restart_error,
        "draining": is_draining(),
        "inflight_request_count": get_inflight_count(),
        "check_interval_sec": _CHECK_INTERVAL_SEC,
        "drain_timeout_sec": _DRAIN_TIMEOUT_SEC,
    }


def _drain_and_restart():
    global _draining, _restart_error
    with _state_lock:
        _draining = True
    print(
        f"[STALENESS-GUARD] draining in-flight requests before restart "
        f"(max {_DRAIN_TIMEOUT_SEC}s) — new requests will get HTTP 503 starting now.",
        flush=True,
    )
    drain_start = time.time()
    while True:
        n = get_inflight_count()
        if n <= 0:
            print(
                f"[STALENESS-GUARD] drain complete in {time.time() - drain_start:.1f}s "
                f"— 0 in-flight requests, proceeding with restart.",
                flush=True,
            )
            break
        if time.time() - drain_start >= _DRAIN_TIMEOUT_SEC:
            print(
                f"[STALENESS-GUARD] WARNING: drain timeout ({_DRAIN_TIMEOUT_SEC}s) reached with "
                f"{n} request(s) still in-flight. Forcing restart anyway — fail-closed: serving "
                f"stale code indefinitely is worse than dropping a slow request.",
                flush=True,
            )
            break
        time.sleep(_DRAIN_POLL_SEC)
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
        # On success, this process image is replaced — nothing below this
        # line executes in the old process.
    except Exception as _exec_e:
        _restart_error = str(_exec_e)
        with _state_lock:
            _draining = False  # resume normal request handling — restart failed
        print(
            f"[STALENESS-GUARD] CRITICAL: os.execv restart FAILED: {_exec_e}. Resumed normal "
            f"request handling. Process remains flagged is_stale=True and will keep re-logging "
            f"every {_CHECK_INTERVAL_SEC}s until manually restarted. This is intentional "
            f"fail-closed behavior — staleness is never silently dropped.",
            flush=True,
        )


def _watchdog_loop():
    global _is_stale, _stale_detected_at, _stale_reason, _restart_attempted
    while True:
        time.sleep(_CHECK_INTERVAL_SEC)
        try:
            newly_added = _discover_and_register_new_local_modules()
            if newly_added:
                print(
                    f"[STALENESS-GUARD] now also watching {len(newly_added)} "
                    f"newly-imported local module(s): {newly_added}",
                    flush=True,
                )

            with _watch_lock:
                items = list(_WATCHED_MTIMES.items())
            stale_hit = None
            for f, start_mtime in items:
                if not os.path.exists(f) or start_mtime is None:
                    continue
                cur_mtime = os.path.getmtime(f)
                if cur_mtime > start_mtime:
                    stale_hit = (f, start_mtime, cur_mtime)
                    break

            sha_now = _git_commit_sha()
            sha_stale = bool(_START_GIT_SHA and sha_now and sha_now != _START_GIT_SHA)

            if stale_hit or sha_stale:
                _is_stale = True
                _stale_detected_at = time.time()
                if stale_hit:
                    f, start_mtime, cur_mtime = stale_hit
                    _stale_reason = f"mtime: {f} (loaded={start_mtime}, current={cur_mtime})"
                else:
                    _stale_reason = f"git_sha_drift: boot={_START_GIT_SHA} current={sha_now}"
                print("=" * 78, flush=True)
                print(
                    f"[STALENESS-GUARD] CRITICAL: {_stale_reason}. This process "
                    f"(pid={os.getpid()}, started {_iso(_PROCESS_START_TIME)}, "
                    f"git_sha_at_boot={_START_GIT_SHA}) is serving STALE code. "
                    f"Beginning graceful drain then automatic self-restart.",
                    flush=True,
                )
                print("=" * 78, flush=True)
                _restart_attempted = True
                _drain_and_restart()
        except Exception as e:
            print(f"[STALENESS-GUARD] watchdog loop error (will retry next cycle): {e}", flush=True)


def start_watchdog():
    t = threading.Thread(target=_watchdog_loop, daemon=True, name="staleness-guard")
    t.start()
    print(
        f"[STALENESS-GUARD] started; watching main.py + dynamically-discovered local "
        f"imports (re-scanned every cycle), check_interval={_CHECK_INTERVAL_SEC}s, "
        f"drain_timeout={_DRAIN_TIMEOUT_SEC}s, pid={os.getpid()}, git_sha_at_boot={_START_GIT_SHA}",
        flush=True,
    )
