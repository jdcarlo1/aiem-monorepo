"""
dpl/integrity_gate.py — Engine Integrity Gate

Extracted from aiem_options_scheduler.py for independent testability (F6, R11).
Encapsulates hash-match, allowlist-approval, and commit-attribution checks that
must all pass before any production execution is permitted.

F2 (R11): uses allowlist (APPROVED_IDENTITIES) instead of blocklist.
F3 (R11): rechecks refs.commit_sha against live git HEAD at runtime.
F4 (R11): no environment bypass — missing refs blocks in every environment.

Called by:
  - aiem_options_scheduler.py  : run_options_pipeline() gate stage
  - dpl/verify_dpl_phase3.py   : C36 executed negative-control tests
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Callable, Dict, Optional

log = logging.getLogger(__name__)

# ── Operator-managed allowlist (A5 / F2) ──────────────────────────────────────
# Empty set  →  blocks all production execution.
# Operator populates with their exact identity string (approved_by field value)
# before enabling Stage 8.  C28_approved_by_in_allowlist_and_engine_hash_match
# reads from this same constant.
APPROVED_IDENTITIES: set = set()


class IntegrityGateError(ValueError):
    """Raised when the engine integrity gate blocks execution.

    Message always begins '[ENGINE_INTEGRITY_GATE] BLOCKED:'.
    Callers that previously caught ValueError may catch this subclass directly.
    """


def run_integrity_gate(
    refs_path: str,
    *,
    block_fn: Optional[Callable[..., None]] = None,
    log_fn:   Optional[Callable[[str], None]] = None,
) -> Dict:
    """Run all engine integrity checks in order.

    Raises IntegrityGateError on any block condition; returns the
    engine_manifest verify result dict on success.

    Args:
        refs_path : Absolute path to engine_integrity_refs.json.
        block_fn  : Optional callback invoked *before* raising on each block::

                        block_fn(reason, exc_cls='', exc_detail='',
                                 live_hash='', expected_hash='')

                    Use this to log to oe_gate_events with caller-side context
                    (ticker, trace_id) that this module cannot see.
        log_fn    : Optional info-level logging callable (single str arg).
    """

    def _block(
        reason:       str,
        exc_cls:      str = '',
        exc_detail:   str = '',
        live_hash:    str = '',
        expected_hash:str = '',
    ) -> None:
        if block_fn is not None:
            try:
                block_fn(
                    reason,
                    exc_cls=exc_cls,
                    exc_detail=exc_detail,
                    live_hash=live_hash,
                    expected_hash=expected_hash,
                )
            except Exception:
                pass
        msg = f"[ENGINE_INTEGRITY_GATE] BLOCKED: {reason}"
        if exc_cls or exc_detail:
            msg += f" ({exc_cls}: {exc_detail})"
        if live_hash:
            msg += f" live={live_hash[:32]}"
        if expected_hash:
            msg += f" != approved={expected_hash[:32]}"
        raise IntegrityGateError(msg)

    # ── Step 1: refs file must exist (F4: no bypass in any environment) ───────
    if not os.path.exists(refs_path):
        _block(
            'REFS_FILE_MISSING',
            exc_detail=f"required at {refs_path}; no bypass in any environment (F4)",
        )

    # ── Step 2: Engine root-hash verification via engine_manifest ─────────────
    result: Dict = {}
    try:
        dpl_dir = os.path.dirname(os.path.abspath(refs_path))
        if dpl_dir not in sys.path:
            sys.path.insert(0, dpl_dir)
        from engine_manifest import verify_against_refs as _vfn  # type: ignore[import]
        result = _vfn(refs_path)
    except ImportError as e:
        _block('IMPORT_FAILURE', type(e).__name__, str(e))
    except PermissionError as e:
        _block('FILE_PERMISSION_FAILURE', type(e).__name__, str(e))
    except (OSError, IOError) as e:
        _block('IO_FAILURE', type(e).__name__, str(e))
    except (ValueError, TypeError, KeyError) as e:
        _block('INVALID_REFS_FILE', type(e).__name__, str(e))
    except Exception as e:
        _block('UNKNOWN_VERIFICATION_EXCEPTION', type(e).__name__, str(e))

    if not result.get('ok'):
        _block(
            'HASH_MISMATCH',
            live_hash=result.get('live_root_hash', ''),
            expected_hash=result.get('approved_root_hash', ''),
        )

    # ── Step 3: Load refs data ────────────────────────────────────────────────
    try:
        refs_data = json.load(open(refs_path))
    except Exception as e:
        _block('INVALID_REFS_FILE', type(e).__name__, str(e))
        refs_data = {}  # unreachable; satisfies type checker

    # ── Step 4: dpl_production_certification must start with 'APPROVED' ───────
    cert = refs_data.get('dpl_production_certification', '')
    if not str(cert).upper().startswith('APPROVED'):
        _block(
            'NOT_APPROVED',
            exc_detail=f"dpl_production_certification={str(cert)[:80]!r}",
        )

    # ── Step 5: approved_at must be present ───────────────────────────────────
    appr_at = refs_data.get('approved_at')
    if not appr_at:
        _block('APPROVED_AT_NULL', exc_detail='approved_at is null or empty')

    # ── Step 6: approved_by must be in APPROVED_IDENTITIES allowlist (A5/F2) ──
    appr_by = refs_data.get('approved_by')
    if appr_by not in APPROVED_IDENTITIES:
        _block(
            'NOT_IN_APPROVED_IDENTITIES',
            exc_detail=(
                f"approved_by={appr_by!r} not in APPROVED_IDENTITIES "
                f"(size={len(APPROVED_IDENTITIES)}; empty allowlist blocks)"
            ),
        )

    # ── Step 7: refs.commit_sha must equal live git HEAD (F3) ────────────────
    refs_commit = refs_data.get('commit_sha', '')
    try:
        git_head = subprocess.run(
            ['git', '--no-optional-locks', 'rev-parse', 'HEAD'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(refs_path)),
        ).stdout.strip()
    except Exception as e:
        _block('GIT_HEAD_CHECK_FAILED', type(e).__name__, str(e))
        git_head = ''  # unreachable

    if refs_commit != git_head:
        _block(
            'COMMIT_SHA_MISMATCH',
            exc_detail=(
                f"refs.commit_sha={refs_commit[:16]!r} != HEAD={git_head[:16]!r}; "
                "update refs.commit_sha to current HEAD before production execution"
            ),
        )

    if log_fn is not None:
        log_fn(
            f"[integrity_gate] PASS "
            f"engine_root_hash={result.get('live_root_hash', '')[:24]}... "
            f"approved_by={appr_by!r} commit={refs_commit[:16]}"
        )
    return result
