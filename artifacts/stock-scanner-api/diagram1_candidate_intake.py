"""
Diagram1CandidateIntake  —  Directive 11 Phase 3

Polls aiem_candidate_pipeline for PENDING_FULL_ANALYSIS rows, atomically
claims them (single CTE with SELECT … FOR UPDATE SKIP LOCKED), and drives
each candidate through the full run_full_cycle() lifecycle.

Guarantees
──────────
  Atomic claim   — single UPDATE-from-CTE; two concurrent polls can never
                   claim the same row (SKIP LOCKED means the second caller
                   sees an empty CTE and updates 0 rows).

  Idempotency    — every claim assigns a fresh attempt_id (UUID4). The
                   CLAIMED → FULL_ANALYSIS_RUNNING transition re-checks that
                   attempt_id still matches before run_full_cycle() is called.
                   A duplicate poll on an in-flight row finds 0 PENDING rows,
                   so run_full_cycle() is called exactly once per attempt.

  Stale recovery — rows stuck in CLAIMED_BY_DIAGRAM1 or FULL_ANALYSIS_RUNNING
                   for > STALE_CLAIM_TIMEOUT_MINUTES are moved to RETRY_PENDING
                   before the next poll cycle (requires the two extra transitions
                   added to the trigger in Phase 3).

  Retry/quarantine — up to MAX_RETRIES attempts, then QUARANTINED.

Scheduling
──────────
  intake_pending_candidates() is called by a BackgroundScheduler job added in
  main.py (IntervalTrigger, every 2 minutes).  It must NEVER be called from
  _aiem_paper_pick_candidates().
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

# ── Logging ────────────────────────────────────────────────────────────────
log = logging.getLogger("diagram1_candidate_intake")
log.setLevel(logging.INFO)
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    ))
    log.addHandler(_h)

# ── Config ─────────────────────────────────────────────────────────────────
_DB_URL: str = os.environ.get("DATABASE_URL", "")

STALE_CLAIM_TIMEOUT_MINUTES: int = 30
# Rationale: run_full_cycle() includes LLM and multi-stage DB calls;
# 30 min is conservative enough to cover any legitimate slow run while
# still catching genuine crashes / restarts that left a row stuck.

MAX_RETRIES: int = 3
# After 3 failed attempts the candidate is QUARANTINED, not retried.

MAX_BATCH_PER_POLL: int = 5
# Max candidates claimed per single intake_pending_candidates() call.

RETRY_BACKOFF_SECONDS: List[int] = [60, 300, 900]
# Attempt 1 → 1 min, attempt 2 → 5 min, attempt 3 → 15 min.


# ── Schema SQL (applied by ensure_schema at startup) ──────────────────────
_SCHEMA_SQL = """
ALTER TABLE aiem_candidate_pipeline
    ADD COLUMN IF NOT EXISTS attempt_number INTEGER     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS attempt_id     TEXT,
    ADD COLUMN IF NOT EXISTS retry_after    TIMESTAMPTZ;
"""


class Diagram1CandidateIntake:
    """
    Diagram-1-owned intake component.

    Lifecycle
    ─────────
    intake_pending_candidates()
      ├── _recover_stale_claims()   CLAIMED/RUNNING > 30 min → RETRY_PENDING
      ├── _requeue_retries()        RETRY_PENDING + backoff passed → PENDING
      ├── _quarantine_exhausted()   RETRY_PENDING + attempts ≥ MAX_RETRIES → QUARANTINED
      └── for each PENDING (up to MAX_BATCH_PER_POLL):
            _atomic_claim()         PENDING → CLAIMED  (attempt_id set here)
            _transition_to_running() CLAIMED → RUNNING (attempt_id re-verified)
            run_full_cycle()        (only if transition confirmed)
            _mark_completed()       RUNNING → FULL_ANALYSIS_COMPLETED
            _mark_failed_retry()    RUNNING → FAILED → RETRY_PENDING  (on error)
    """

    def __init__(self, db_url: Optional[str] = None):
        self._db_url = db_url or _DB_URL

    # ── Schema ────────────────────────────────────────────────────────────
    def ensure_schema(self) -> None:
        """
        Idempotent startup hook.  Adds attempt_number, attempt_id, retry_after
        columns to aiem_candidate_pipeline if they do not exist yet.
        The trigger additions for CLAIMED/RUNNING → RETRY_PENDING are applied
        via approved_run.sh during Phase 3 deployment (not here, to preserve
        the evidence chain requirement).
        """
        if not self._db_url:
            log.warning("[intake] DATABASE_URL not set — schema init skipped")
            return
        try:
            with psycopg2.connect(self._db_url, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute(_SCHEMA_SQL)
                conn.commit()
            log.info("[intake] schema ensured (attempt_number, attempt_id, retry_after)")
        except Exception as exc:
            log.error("[intake] ensure_schema error: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────
    def _conn(self) -> "psycopg2.extensions.connection":
        return psycopg2.connect(self._db_url, connect_timeout=5)

    # ── Stale claim recovery ──────────────────────────────────────────────
    def _recover_stale_claims(self, conn) -> int:
        """
        Move rows stuck > STALE_CLAIM_TIMEOUT_MINUTES in CLAIMED_BY_DIAGRAM1
        or FULL_ANALYSIS_RUNNING to RETRY_PENDING.

        Requires two trigger transitions added in Phase 3:
          CLAIMED_BY_DIAGRAM1   → RETRY_PENDING
          FULL_ANALYSIS_RUNNING → RETRY_PENDING
        """
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_candidate_pipeline
                SET    status    = 'RETRY_PENDING',
                       error_msg = 'stale-claim recovery: stuck in ' || status
                                   || ' > %(mins)s min (possible crash/restart)'
                WHERE  status IN ('CLAIMED_BY_DIAGRAM1', 'FULL_ANALYSIS_RUNNING')
                  AND  claimed_at < NOW() - (%(mins)s * INTERVAL '1 minute')
                  AND  NOT is_test_record
                RETURNING id, ticker, status
            """, {"mins": STALE_CLAIM_TIMEOUT_MINUTES})
            recovered: List[Tuple] = cur.fetchall()
        conn.commit()
        for row_id, ticker, prev in recovered:
            log.warning(
                "[intake] stale-claim recovery id=%s ticker=%s %s → RETRY_PENDING",
                row_id, ticker, prev,
            )
        return len(recovered)

    # ── Re-queue eligible RETRY_PENDING ───────────────────────────────────
    def _requeue_retries(self, conn) -> int:
        """
        RETRY_PENDING rows whose backoff window has passed and whose
        attempt_number < MAX_RETRIES are moved back to PENDING_FULL_ANALYSIS
        so the main claim loop can pick them up again.
        """
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_candidate_pipeline
                SET    status      = 'PENDING_FULL_ANALYSIS',
                       retry_after = NULL
                WHERE  status        = 'RETRY_PENDING'
                  AND  attempt_number < %(max_r)s
                  AND  (retry_after IS NULL OR retry_after <= NOW())
                  AND  NOT is_test_record
                RETURNING id, ticker, attempt_number
            """, {"max_r": MAX_RETRIES})
            requeued: List[Tuple] = cur.fetchall()
        conn.commit()
        for row_id, ticker, n in requeued:
            log.info("[intake] requeued id=%s ticker=%s (attempt %s)", row_id, ticker, n)
        return len(requeued)

    # ── Quarantine exhausted retries ──────────────────────────────────────
    def _quarantine_exhausted(self, conn) -> int:
        """
        RETRY_PENDING rows at or above MAX_RETRIES are transitioned to
        QUARANTINED.  The requeue step above skips these (attempt_number ≥
        MAX_RETRIES), so they land here.
        """
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_candidate_pipeline
                SET    status    = 'QUARANTINED',
                       error_msg = 'max retries (' || %(mr)s::text || ') exhausted'
                WHERE  status        = 'RETRY_PENDING'
                  AND  attempt_number >= %(mr)s
                  AND  NOT is_test_record
                RETURNING id, ticker, attempt_number
            """, {"mr": MAX_RETRIES})
            quarantined: List[Tuple] = cur.fetchall()
        conn.commit()
        for row_id, ticker, n in quarantined:
            log.warning("[intake] quarantined id=%s ticker=%s (attempts=%s)", row_id, ticker, n)
        return len(quarantined)

    # ── Atomic claim ─────────────────────────────────────────────────────
    def _atomic_claim(
        self, conn, attempt_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Single atomic CTE: SELECT … FOR UPDATE SKIP LOCKED + UPDATE in one
        round-trip.

        Concurrency proof: two callers racing on the same PENDING row:
          - Caller A: CTE locks the row (SKIP LOCKED), UPDATE succeeds → 1 row
          - Caller B: CTE finds the row locked, skips it (SKIP LOCKED returns
            empty set) → UPDATE affects 0 rows → returns None

        Returns the claimed row as a dict, or None if nothing was available.
        """
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                WITH claimed AS (
                    SELECT id
                    FROM   aiem_candidate_pipeline
                    WHERE  status         = 'PENDING_FULL_ANALYSIS'
                      AND  candidate_date = CURRENT_DATE
                      AND  (retry_after IS NULL OR retry_after <= NOW())
                      AND  NOT is_test_record
                    ORDER  BY score DESC NULLS LAST, inserted_at ASC
                    LIMIT  1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE aiem_candidate_pipeline p
                SET    status         = 'CLAIMED_BY_DIAGRAM1',
                       claimed_at     = NOW(),
                       attempt_number = COALESCE(attempt_number, 0) + 1,
                       attempt_id     = %(attempt_id)s,
                       error_msg      = NULL
                FROM   claimed
                WHERE  p.id = claimed.id
                RETURNING
                    p.id, p.ticker, p.score, p.raw_score, p.trade_type,
                    p.source, p.detail, p.attempt_number, p.attempt_id,
                    p.candidate_date
            """, {"attempt_id": attempt_id})
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None

    # ── Idempotency gate: CLAIMED → RUNNING ───────────────────────────────
    def _transition_to_running(
        self, conn, row_id: int, attempt_id: str
    ) -> bool:
        """
        Atomically transitions CLAIMED_BY_DIAGRAM1 → FULL_ANALYSIS_RUNNING,
        gated on attempt_id matching what THIS invocation set.

        If another intake invocation somehow holds a different attempt_id (not
        possible with SKIP LOCKED, but belt-and-suspenders), this returns False
        and run_full_cycle() is skipped — idempotency preserved.
        """
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_candidate_pipeline
                SET    status = 'FULL_ANALYSIS_RUNNING'
                WHERE  id         = %(id)s
                  AND  status     = 'CLAIMED_BY_DIAGRAM1'
                  AND  attempt_id = %(attempt_id)s
                RETURNING id
            """, {"id": row_id, "attempt_id": attempt_id})
            updated = cur.fetchone()
        conn.commit()
        return updated is not None

    # ── Completion ────────────────────────────────────────────────────────
    def _mark_completed(
        self,
        conn,
        row_id: int,
        attempt_id: str,
        paper_trade_id: Optional[int] = None,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_candidate_pipeline
                SET    status         = 'FULL_ANALYSIS_COMPLETED',
                       completed_at   = NOW(),
                       paper_trade_id = %(pt)s
                WHERE  id         = %(id)s
                  AND  attempt_id = %(aid)s
            """, {"id": row_id, "aid": attempt_id, "pt": paper_trade_id})
        conn.commit()

    # ── Failure → RETRY_PENDING (or QUARANTINED on exhaust) ───────────────
    def _mark_failed_retry(
        self,
        conn,
        row_id: int,
        attempt_id: str,
        attempt_number: int,
        error: str,
    ) -> str:
        """
        Transitions FULL_ANALYSIS_RUNNING → FAILED → RETRY_PENDING.
        The _quarantine_exhausted() call in the NEXT poll will promote
        exhausted RETRY_PENDING rows to QUARANTINED (attempt_number ≥
        MAX_RETRIES).

        Uses two separate commits to satisfy the trigger's legal-transition
        graph (RUNNING → FAILED is one allowed arc; FAILED → RETRY_PENDING
        is a separate arc).
        """
        error_short = error[:500]
        backoff_s = RETRY_BACKOFF_SECONDS[
            min(attempt_number - 1, len(RETRY_BACKOFF_SECONDS) - 1)
        ]
        # Step 1: FULL_ANALYSIS_RUNNING → FAILED
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_candidate_pipeline
                SET    status    = 'FAILED',
                       error_msg = %(err)s
                WHERE  id        = %(id)s
                  AND  attempt_id = %(aid)s
            """, {"id": row_id, "aid": attempt_id, "err": error_short})
        conn.commit()
        # Step 2: FAILED → RETRY_PENDING (with exponential backoff)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE aiem_candidate_pipeline
                SET    status      = 'RETRY_PENDING',
                       retry_after = NOW() + (%(bs)s * INTERVAL '1 second')
                WHERE  id = %(id)s
            """, {"id": row_id, "bs": backoff_s})
        conn.commit()
        log.info(
            "[intake] id=%s attempt=%s → RETRY_PENDING (backoff=%ss): %s",
            row_id, attempt_number, backoff_s, error[:120],
        )
        return "RETRY_PENDING"

    # ── Main entry point ──────────────────────────────────────────────────
    def intake_pending_candidates(self) -> Dict[str, int]:
        """
        Main polling entry point — called every 2 min by the BackgroundScheduler
        job registered in main.py.

        MUST NOT be called from _aiem_paper_pick_candidates().
        _aiem_paper_pick_candidates() only writes PENDING rows; this method
        claims and processes them.

        Steps
        ─────
        1. Recover stale claims (CLAIMED/RUNNING > 30 min → RETRY_PENDING)
        2. Re-queue eligible RETRY_PENDING (backoff passed, attempt < MAX)
        3. Quarantine exhausted RETRY_PENDING (attempt ≥ MAX_RETRIES)
        4. Claim + process up to MAX_BATCH_PER_POLL candidates

        Returns a summary dict suitable for logging.
        """
        summary: Dict[str, int] = {
            "stale_recovered": 0,
            "requeued":        0,
            "quarantined":     0,
            "claimed":         0,
            "completed":       0,
            "failed":          0,
        }
        if not self._db_url:
            log.warning("[intake] DATABASE_URL not set — poll skipped")
            return summary

        try:
            conn = psycopg2.connect(self._db_url, connect_timeout=5)
        except Exception as exc:
            log.error("[intake] DB connect failed: %s", exc)
            return summary

        try:
            summary["stale_recovered"] = self._recover_stale_claims(conn)
            summary["requeued"]        = self._requeue_retries(conn)
            summary["quarantined"]     = self._quarantine_exhausted(conn)

            for _ in range(MAX_BATCH_PER_POLL):
                attempt_id = str(uuid.uuid4())
                row = self._atomic_claim(conn, attempt_id)
                if row is None:
                    break  # no more PENDING rows today

                summary["claimed"] += 1
                row_id       = row["id"]
                ticker       = row["ticker"]
                attempt_num  = row["attempt_number"]

                log.info(
                    "[intake] claimed id=%s ticker=%s attempt=%s attempt_id=%s",
                    row_id, ticker, attempt_num, attempt_id,
                )

                # ── Idempotency gate ────────────────────────────────────
                # Verify our attempt_id still matches before invoking
                # run_full_cycle().  If another concurrent caller somehow
                # holds a different attempt_id, we skip rather than double-run.
                if not self._transition_to_running(conn, row_id, attempt_id):
                    log.error(
                        "[intake] idempotency guard: attempt_id mismatch for "
                        "id=%s ticker=%s — skipping run_full_cycle()",
                        row_id, ticker,
                    )
                    summary["failed"] += 1
                    continue

                # ── Invoke run_full_cycle() ─────────────────────────────
                paper_trade_id: Optional[int] = None
                try:
                    import aiem_master_orchestrator as _amo  # noqa: PLC0415
                    orch   = _amo.get_orchestrator()
                    packet = orch.run_full_cycle(
                        ticker         = ticker,
                        source         = row.get("source") or "candidate_intake",
                        scanner_signal = {
                            "score":      row.get("score"),
                            "raw_score":  row.get("raw_score"),
                            "trade_type": row.get("trade_type"),
                            "detail":     row.get("detail"),
                        },
                        # execution_plan_id ties this packet to exactly this attempt.
                        # A duplicate call with the same attempt_id is structurally
                        # blocked by the idempotency gate above.
                        execution_plan_id = attempt_id,
                    )
                    if hasattr(packet, "paper_trade_id"):
                        paper_trade_id = getattr(packet, "paper_trade_id", None)

                    self._mark_completed(conn, row_id, attempt_id, paper_trade_id)
                    summary["completed"] += 1
                    log.info(
                        "[intake] completed id=%s ticker=%s attempt=%s "
                        "paper_trade_id=%s",
                        row_id, ticker, attempt_num, paper_trade_id,
                    )

                except Exception as run_exc:
                    err_str = f"{type(run_exc).__name__}: {run_exc}"
                    log.error(
                        "[intake] run_full_cycle failed id=%s ticker=%s: %s",
                        row_id, ticker, err_str,
                    )
                    self._mark_failed_retry(
                        conn, row_id, attempt_id, attempt_num, err_str
                    )
                    summary["failed"] += 1

        except Exception as outer_exc:
            log.error("[intake] outer error: %s", outer_exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if any(v > 0 for v in summary.values()):
            log.info("[intake] poll complete: %s", summary)
        return summary


# ── Module-level singleton ─────────────────────────────────────────────────
_intake_singleton: Optional[Diagram1CandidateIntake] = None


def get_intake() -> Diagram1CandidateIntake:
    """Return the process-wide Diagram1CandidateIntake singleton."""
    global _intake_singleton
    if _intake_singleton is None:
        _intake_singleton = Diagram1CandidateIntake()
    return _intake_singleton
