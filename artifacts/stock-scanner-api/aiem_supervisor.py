"""
AIEM_SUPERVISOR_META_REASONING_LAYER
=====================================
Connected directly into the real AIEM closed-loop pipeline.

6 inline hooks (not a standalone report):
  1. supervisor_on_scanner_alert       — before AIEM intake
  2. supervisor_on_candidate_ranking   — after candidate ranking stored
  3. supervisor_on_final_decision      — after AIEM final decision
  4. supervisor_on_paper_trade_opened  — after paper trade INSERT
  5. supervisor_on_trade_closed        — after MTM exit + pnl known
  6. supervisor_on_learning_update     — after trust/EMA update

AIEM = decision_authority  |  Supervisor = meta_authority
Mode: MONITOR_ONLY — may log/flag/grade, may NOT block or override.
"""

import os
import json
import datetime
import psycopg2

_DB_URL = os.environ.get("DATABASE_URL", "")

# ── Mode config ────────────────────────────────────────────────────────────
AIEM_SUPERVISOR_MODE = "MONITOR_ONLY"   # MONITOR_ONLY | SOFT_OVERRIDE | HARD_OVERRIDE

# ── Thresholds ─────────────────────────────────────────────────────────────
_MIN_SAMPLE_BAD_LEARNING  = 10
_MAX_WEIGHT_DELTA_ALLOWED = 0.10
_FREEZE_CONSEC_LOSSES     = 5
_MIN_WR_RETIRE            = 0.35


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ══════════════════════════════════════════════════════════════════════════════

def init_schema():
    """Create / migrate all supervisor tables. Idempotent."""
    sql = """
    -- Event log: one row per hook fire
    CREATE TABLE IF NOT EXISTS aiem_supervisor_event_log (
        id               BIGSERIAL PRIMARY KEY,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        audit_trace_id   TEXT,
        trade_id         BIGINT,
        ticker           TEXT,
        event_type       TEXT NOT NULL,
        source_table     TEXT,
        source_row_id    BIGINT,
        supervisor_mode  TEXT NOT NULL DEFAULT 'MONITOR_ONLY',
        supervisor_verdict TEXT,
        notes_json       JSONB NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_sup_evlog_trace ON aiem_supervisor_event_log(audit_trace_id);
    CREATE INDEX IF NOT EXISTS idx_sup_evlog_created ON aiem_supervisor_event_log(created_at DESC);

    -- Loop audit: one row per audit_trace_id, updated at each step
    CREATE TABLE IF NOT EXISTS aiem_supervisor_loop_audit (
        id                     BIGSERIAL PRIMARY KEY,
        created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        audit_trace_id         TEXT UNIQUE NOT NULL,
        trade_id               BIGINT,
        ticker                 TEXT,
        scanner_alert_seen     BOOLEAN NOT NULL DEFAULT FALSE,
        aiem_intake_seen       BOOLEAN NOT NULL DEFAULT FALSE,
        candidate_ranking_seen BOOLEAN NOT NULL DEFAULT FALSE,
        final_decision_seen    BOOLEAN NOT NULL DEFAULT FALSE,
        paper_trade_seen       BOOLEAN NOT NULL DEFAULT FALSE,
        outcome_seen           BOOLEAN NOT NULL DEFAULT FALSE,
        learning_update_seen   BOOLEAN NOT NULL DEFAULT FALSE,
        loop_complete          BOOLEAN NOT NULL DEFAULT FALSE,
        missing_steps_json     JSONB   NOT NULL DEFAULT '[]',
        verdict                TEXT    NOT NULL DEFAULT 'INCOMPLETE'
    );

    -- Learning review: one row per learning update, with verdict
    CREATE TABLE IF NOT EXISTS aiem_supervisor_learning_review (
        id               BIGSERIAL PRIMARY KEY,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        audit_trace_id   TEXT,
        trade_id         BIGINT,
        ticker           TEXT,
        signal_source    TEXT,
        old_trust_score  NUMERIC,
        new_trust_score  NUMERIC,
        delta            NUMERIC,
        sample_size      INTEGER,
        pnl_pct          NUMERIC,
        review_verdict   TEXT NOT NULL,
        risk_of_bad_learning TEXT,
        reason           TEXT,
        recommended_action TEXT
    );

    -- Risk review: one row per final-decision risk check
    CREATE TABLE IF NOT EXISTS aiem_supervisor_risk_review (
        id               BIGSERIAL PRIMARY KEY,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        audit_trace_id   TEXT,
        trade_id         BIGINT,
        ticker           TEXT,
        aiem_decision    TEXT,
        aiem_confidence  NUMERIC,
        risk_score       NUMERIC NOT NULL DEFAULT 0,
        risk_flags_json  JSONB   NOT NULL DEFAULT '[]',
        supervisor_verdict TEXT  NOT NULL,
        recommended_action TEXT
    );

    -- Daily report
    CREATE TABLE IF NOT EXISTS aiem_supervisor_daily_report (
        id                    BIGSERIAL PRIMARY KEY,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        report_date           DATE UNIQUE NOT NULL,
        alerts_seen           INTEGER NOT NULL DEFAULT 0,
        aiem_decisions_seen   INTEGER NOT NULL DEFAULT 0,
        paper_trades_seen     INTEGER NOT NULL DEFAULT 0,
        closed_trades_seen    INTEGER NOT NULL DEFAULT 0,
        learning_updates_seen INTEGER NOT NULL DEFAULT 0,
        complete_loops        INTEGER NOT NULL DEFAULT 0,
        incomplete_loops      INTEGER NOT NULL DEFAULT 0,
        bad_learning_flags    INTEGER NOT NULL DEFAULT 0,
        risk_flags            INTEGER NOT NULL DEFAULT 0,
        overall_grade         TEXT,
        report_json           JSONB NOT NULL DEFAULT '{}'
    );

    -- Migrate existing loop_audit table (add new columns if missing)
    ALTER TABLE aiem_supervisor_loop_audit
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
    ALTER TABLE aiem_supervisor_loop_audit
        ADD COLUMN IF NOT EXISTS scanner_alert_seen BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE aiem_supervisor_loop_audit
        ADD COLUMN IF NOT EXISTS aiem_intake_seen BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE aiem_supervisor_loop_audit
        ADD COLUMN IF NOT EXISTS candidate_ranking_seen BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE aiem_supervisor_loop_audit
        ADD COLUMN IF NOT EXISTS final_decision_seen BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE aiem_supervisor_loop_audit
        ADD COLUMN IF NOT EXISTS paper_trade_seen BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE aiem_supervisor_loop_audit
        ADD COLUMN IF NOT EXISTS outcome_seen BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE aiem_supervisor_loop_audit
        ADD COLUMN IF NOT EXISTS learning_update_seen BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE aiem_supervisor_loop_audit
        ADD COLUMN IF NOT EXISTS verdict TEXT;

    -- Idempotent UNIQUE index on loop_audit.audit_trace_id.
    -- Older instances were created with TEXT UNIQUE in the DDL but the
    -- constraint name differed, so ON CONFLICT (audit_trace_id) would fail.
    -- CREATE UNIQUE INDEX IF NOT EXISTS is safe to run repeatedly.
    CREATE UNIQUE INDEX IF NOT EXISTS idx_sup_loop_trace_uniq
        ON aiem_supervisor_loop_audit(audit_trace_id);
    """
    try:
        with psycopg2.connect(_DB_URL) as c, c.cursor() as cu:
            cu.execute(sql)
            c.commit()
        print("[supervisor] schema ready — 5 tables initialised")
    except Exception as e:
        print(f"[supervisor] schema init error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _event(event_type, audit_trace_id, trade_id, ticker,
           source_table=None, source_row_id=None,
           verdict="ALLOW_MONITOR_ONLY", notes=None):
    """Write one row to aiem_supervisor_event_log."""
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_event_log
                    (audit_trace_id, trade_id, ticker, event_type,
                     source_table, source_row_id, supervisor_mode,
                     supervisor_verdict, notes_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (
                audit_trace_id, trade_id, ticker, event_type,
                source_table, source_row_id, AIEM_SUPERVISOR_MODE,
                verdict, json.dumps(notes or {}),
            ))
            row_id = cu.fetchone()[0]
            c.commit()
        return row_id
    except Exception as e:
        print(f"[supervisor] event_log write error: {e}")
        return None


def _upsert_loop_audit(audit_trace_id, ticker=None, trade_id=None, **step_flags):
    """
    Upsert the loop_audit row for this trace_id.
    step_flags keys match column names: scanner_alert_seen, aiem_intake_seen, etc.
    After update, re-checks if loop is now complete.
    """
    if not audit_trace_id:
        return
    all_steps = [
        "scanner_alert_seen", "aiem_intake_seen", "candidate_ranking_seen",
        "final_decision_seen", "paper_trade_seen", "outcome_seen",
        "learning_update_seen",
    ]
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            # Upsert row
            cu.execute("""
                INSERT INTO aiem_supervisor_loop_audit
                    (audit_trace_id, ticker, trade_id, verdict)
                VALUES (%s,%s,%s,'INCOMPLETE')
                ON CONFLICT (audit_trace_id) DO UPDATE
                    SET updated_at = NOW(),
                        ticker   = COALESCE(EXCLUDED.ticker, aiem_supervisor_loop_audit.ticker),
                        trade_id = COALESCE(EXCLUDED.trade_id, aiem_supervisor_loop_audit.trade_id)
            """, (audit_trace_id, ticker, trade_id))
            c.commit()

            # Update step flags
            if step_flags:
                set_parts = ", ".join(
                    f"{k} = TRUE" for k in step_flags if k in all_steps and step_flags[k]
                )
                if set_parts:
                    cu.execute(
                        f"UPDATE aiem_supervisor_loop_audit SET {set_parts}, updated_at=NOW() "
                        f"WHERE audit_trace_id=%s",
                        (audit_trace_id,)
                    )
                    c.commit()

            # Re-read and check completeness
            cu.execute(
                f"SELECT {', '.join(all_steps)} FROM aiem_supervisor_loop_audit "
                f"WHERE audit_trace_id=%s",
                (audit_trace_id,)
            )
            row = cu.fetchone()
            if row:
                missing = [s for s, v in zip(all_steps, row) if not v]
                complete = len(missing) == 0
                verdict = "COMPLETE" if complete else "INCOMPLETE"
                cu.execute("""
                    UPDATE aiem_supervisor_loop_audit
                    SET loop_complete=%s, missing_steps_json=%s, verdict=%s, updated_at=NOW()
                    WHERE audit_trace_id=%s
                """, (complete, json.dumps(missing), verdict, audit_trace_id))
                c.commit()
    except Exception as e:
        print(f"[supervisor] loop_audit upsert error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# HOOK 1: SCANNER ALERT → before AIEM intake
# ══════════════════════════════════════════════════════════════════════════════

def supervisor_on_scanner_alert(audit_trace_id, ticker, signal_source,
                                scanner_score=None, scanner_reason=None,
                                alert_timestamp=None):
    """
    Fires when the stock scanner sends a candidate to AIEM.
    Logs the alert. AIEM intake begins after this.
    """
    try:
        _event(
            "SCANNER_ALERT", audit_trace_id, None, ticker,
            source_table="aiem_paper_trades",
            verdict="ALLOW_MONITOR_ONLY",
            notes={
                "signal_source": signal_source,
                "scanner_score": scanner_score,
                "scanner_reason": scanner_reason,
                "alert_timestamp": str(alert_timestamp or datetime.datetime.utcnow()),
                "mode": AIEM_SUPERVISOR_MODE,
            },
        )
        _upsert_loop_audit(
            audit_trace_id, ticker=ticker,
            scanner_alert_seen=True, aiem_intake_seen=True,
        )
        return {"verdict": "ALLOW_MONITOR_ONLY", "mode": AIEM_SUPERVISOR_MODE}
    except Exception as e:
        print(f"[supervisor] on_scanner_alert error: {e}")
        return {"verdict": "ALLOW_MONITOR_ONLY", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# HOOK 2: CANDIDATE RANKING — after aiem_candidate_rankings stored
# ══════════════════════════════════════════════════════════════════════════════

def supervisor_on_candidate_ranking(audit_trace_id, run_id=None, candidates=None):
    """
    Fires after AIEM ranks candidates and stores them in aiem_candidate_rankings.
    Reads the table and verifies required scoring columns exist.
    """
    required_cols = [
        "raw_score", "trust_multiplier", "drift_multiplier",
        "rl_weight", "final_adjusted_score",
    ]
    try:
        ranking_issues = []
        row_count = 0
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("SELECT COUNT(*) FROM aiem_candidate_rankings")
            row_count = cu.fetchone()[0] or 0

            # Verify columns exist on the table
            cu.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'aiem_candidate_rankings'
            """)
            existing_cols = {r[0] for r in cu.fetchall()}
            for col in required_cols:
                if col not in existing_cols:
                    ranking_issues.append(f"MISSING_COLUMN:{col}")

        verdict = "ALLOW_MONITOR_ONLY" if not ranking_issues else "FLAG_MONITOR_ONLY"
        ticker = (candidates or [{}])[0].get("ticker") if candidates else None

        _event(
            "CANDIDATE_RANKING", audit_trace_id, None, ticker,
            source_table="aiem_candidate_rankings",
            verdict=verdict,
            notes={
                "run_id": run_id,
                "ranking_row_count": row_count,
                "ranking_issues": ranking_issues,
                "candidate_count": len(candidates or []),
                "mode": AIEM_SUPERVISOR_MODE,
            },
        )

        for cand in (candidates or []):
            _upsert_loop_audit(
                audit_trace_id, ticker=cand.get("ticker"),
                candidate_ranking_seen=True,
            )
            break  # one loop-audit row per trace

        return {
            "verdict": verdict,
            "ranking_row_count": row_count,
            "ranking_issues": ranking_issues,
        }
    except Exception as e:
        print(f"[supervisor] on_candidate_ranking error: {e}")
        return {"verdict": "ALLOW_MONITOR_ONLY", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# HOOK 3: FINAL DECISION — after AIEM decides, before trade insert
# ══════════════════════════════════════════════════════════════════════════════

def supervisor_on_final_decision(audit_trace_id, ticker, trade_id=None,
                                 decision="EXECUTE", confidence_score=None,
                                 decision_reason=None):
    """
    Fires after AIEM makes its final decision.
    Writes supervisor_checked=true, meta_authority=AIEM_SUPERVISOR.
    """
    try:
        risk = supervisor_review_risk(
            audit_trace_id=audit_trace_id, ticker=ticker, trade_id=trade_id,
            aiem_decision=decision, aiem_confidence=confidence_score,
        )

        verdict = "ALLOW_MONITOR_ONLY"

        _event(
            "FINAL_DECISION", audit_trace_id, trade_id, ticker,
            source_table="aiem_pipeline_audit_log",
            verdict=verdict,
            notes={
                "decision": decision,
                "confidence_score": confidence_score,
                "decision_reason": decision_reason,
                "decision_authority": "AIEM",
                "supervisor_checked": True,
                "meta_authority": "AIEM_SUPERVISOR",
                "supervisor_verdict": verdict,
                "mode": AIEM_SUPERVISOR_MODE,
                "risk_score": risk.get("risk_score", 0),
            },
        )

        _upsert_loop_audit(audit_trace_id, ticker=ticker, trade_id=trade_id,
                           final_decision_seen=True)

        # Wire aiem_decision_log write
        try:
            import datetime as _dt_dec
            with psycopg2.connect(_DB_URL, connect_timeout=3) as _dc, _dc.cursor() as _dcu:
                _dcu.execute("""
                    INSERT INTO aiem_decision_log
                        (ticker, trade_date, decision_type, decision_rationale,
                         signal_source, confidence_score, final_decision,
                         audit_trace_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    ticker,
                    _dt_dec.date.today(),
                    "EXECUTE" if decision == "EXECUTE" else "SKIP",
                    str(decision_reason or "")[:500],
                    None,
                    confidence_score,
                    decision,
                    audit_trace_id,
                ))
        except Exception as _dec_e:
            print(f"[supervisor] decision_log write skipped: {_dec_e}")

        return {
            "verdict": verdict,
            "decision_authority": "AIEM",
            "meta_authority": "AIEM_SUPERVISOR",
            "risk": risk,
        }
    except Exception as e:
        print(f"[supervisor] on_final_decision error: {e}")
        return {"verdict": "ALLOW_MONITOR_ONLY", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# HOOK 4: PAPER TRADE OPENED
# ══════════════════════════════════════════════════════════════════════════════

def supervisor_on_paper_trade_opened(audit_trace_id, trade_id, ticker,
                                     signal_source, entry_price, entry_time=None):
    """
    Fires when AIEM opens a paper trade.
    Verifies: audit_trace_id linked, signal_source preserved, trade exists.
    """
    issues = []
    try:
        row = None
        for _attempt in range(3):
            with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
                cu.execute("""
                    SELECT audit_trace_id, signal_source, entry_price, status
                    FROM aiem_paper_trades WHERE id=%s
                """, (trade_id,))
                row = cu.fetchone()
            if row:
                break
            import time as _time_sup
            _time_sup.sleep(0.6)
        if not row:
            issues.append("TRADE_NOT_FOUND")
        else:
            if not row[0]:
                issues.append("MISSING_AUDIT_TRACE_ID")
            elif row[0] != audit_trace_id:
                issues.append(f"TRACE_MISMATCH:expected={audit_trace_id},got={row[0]}")
            if not row[1]:
                issues.append("MISSING_SIGNAL_SOURCE")
            if row[3] != "OPEN":
                issues.append(f"UNEXPECTED_STATUS:{row[3]}")

        verdict = "ALLOW_MONITOR_ONLY" if not issues else "FLAG_MONITOR_ONLY"

        _event(
            "PAPER_TRADE_OPENED", audit_trace_id, trade_id, ticker,
            source_table="aiem_paper_trades", source_row_id=trade_id,
            verdict=verdict,
            notes={
                "signal_source": signal_source,
                "entry_price": entry_price,
                "entry_time": str(entry_time or datetime.datetime.utcnow()),
                "issues": issues,
                "mode": AIEM_SUPERVISOR_MODE,
            },
        )

        _upsert_loop_audit(audit_trace_id, ticker=ticker, trade_id=trade_id,
                           paper_trade_seen=True)

        return {"verdict": verdict, "issues": issues}
    except Exception as e:
        print(f"[supervisor] on_paper_trade_opened error: {e}")
        return {"verdict": "ALLOW_MONITOR_ONLY", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# HOOK 5: TRADE CLOSED — after MTM exit + pnl known
# ══════════════════════════════════════════════════════════════════════════════

def supervisor_on_trade_closed(audit_trace_id, trade_id, ticker,
                               exit_price=None, exit_time=None,
                               pnl=None, pnl_pct=None):
    """
    Fires when trade closes and pnl is known.
    Verifies: exit exists, pnl exists, outcome classification possible.
    """
    issues = []
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                SELECT status, exit_price, pnl, pnl_pct, exit_reason
                FROM aiem_paper_trades WHERE id=%s
            """, (trade_id,))
            row = cu.fetchone()
            if not row:
                issues.append("TRADE_NOT_FOUND")
            else:
                if row[0] == "OPEN":
                    issues.append("TRADE_STILL_OPEN")
                if row[1] is None:
                    issues.append("MISSING_EXIT_PRICE")
                if row[2] is None:
                    issues.append("MISSING_PNL")
                if row[3] is None:
                    issues.append("MISSING_PNL_PCT")
                if not row[4]:
                    issues.append("MISSING_EXIT_REASON")

        outcome_class = "WIN" if (pnl_pct or 0) > 0 else ("LOSS" if (pnl_pct or 0) < 0 else "FLAT")
        verdict = "ALLOW_MONITOR_ONLY" if not issues else "FLAG_MONITOR_ONLY"

        _event(
            "TRADE_CLOSED", audit_trace_id, trade_id, ticker,
            source_table="aiem_paper_trades", source_row_id=trade_id,
            verdict=verdict,
            notes={
                "exit_price": exit_price,
                "exit_time": str(exit_time or datetime.datetime.utcnow()),
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "outcome_class": outcome_class,
                "issues": issues,
                "learning_update_expected": True,
                "mode": AIEM_SUPERVISOR_MODE,
            },
        )

        _upsert_loop_audit(audit_trace_id, ticker=ticker, trade_id=trade_id,
                           outcome_seen=True)

        return {"verdict": verdict, "outcome_class": outcome_class, "issues": issues}
    except Exception as e:
        print(f"[supervisor] on_trade_closed error: {e}")
        return {"verdict": "ALLOW_MONITOR_ONLY", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# HOOK 6: LEARNING UPDATE — after trust/EMA update applied
# ══════════════════════════════════════════════════════════════════════════════

def supervisor_on_learning_update(audit_trace_id, trade_id, ticker,
                                  signal_source, old_trust_score,
                                  new_trust_score, delta, reason=None):
    """
    Fires after AIEM applies the trust/EMA update.
    Checks for bad learning. MONITOR_ONLY — flags but does not block.
    """
    try:
        review = supervisor_review_learning_update(
            audit_trace_id=audit_trace_id, trade_id=trade_id, ticker=ticker,
            signal_source=signal_source, old_trust_score=old_trust_score,
            new_trust_score=new_trust_score, delta=delta, reason=reason,
        )

        verdict = (
            "FLAG_BAD_LEARNING_MONITOR_ONLY"
            if review.get("risk_of_bad_learning") in ("HIGH", "MEDIUM")
            else ("REQUIRE_MORE_DATA_MONITOR_ONLY"
                  if review.get("recommended_action") == "REQUIRE_MORE_DATA"
                  else "ALLOW_UPDATE_MONITOR_ONLY")
        )

        _event(
            "LEARNING_UPDATE", audit_trace_id, trade_id, ticker,
            source_table="signal_trust_history",
            verdict=verdict,
            notes={
                "signal_source": signal_source,
                "old_trust": old_trust_score,
                "new_trust": new_trust_score,
                "delta": delta,
                "reason": reason,
                "review_verdict": review.get("review_verdict"),
                "risk_of_bad_learning": review.get("risk_of_bad_learning"),
                "mode": AIEM_SUPERVISOR_MODE,
            },
        )

        _upsert_loop_audit(audit_trace_id, ticker=ticker, trade_id=trade_id,
                           learning_update_seen=True)

        return {"verdict": verdict, "review": review}
    except Exception as e:
        print(f"[supervisor] on_learning_update error: {e}")
        return {"verdict": "ALLOW_UPDATE_MONITOR_ONLY", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# REVIEW FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def supervisor_review_learning_update(audit_trace_id, trade_id, ticker,
                                      signal_source, old_trust_score,
                                      new_trust_score, delta, reason=None,
                                      pnl_pct=None):
    """
    Checks if the learning update is aggressive, premature, or contradicts history.
    Returns verdict + risk assessment. Never blocks — monitor only.
    """
    risks = []
    review_verdict = "PASS"
    risk_level = "LOW"
    recommended_action = "ALLOW_UPDATE"
    sample_size = 0

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), AVG(pnl_pct)
                FROM aiem_paper_trades
                WHERE signal_source=%s AND status!='OPEN'
            """, (signal_source,))
            row = cu.fetchone()
            sample_size = row[0] or 0
            wins = row[1] or 0
            avg_ret = float(row[2] or 0)

            cu.execute("""
                SELECT pnl FROM aiem_paper_trades
                WHERE signal_source=%s AND status!='OPEN'
                ORDER BY created_at DESC LIMIT 5
            """, (signal_source,))
            recent = [float(r[0]) for r in cu.fetchall()]

        long_run_wr = (wins / sample_size) if sample_size else 0
        abs_delta = abs(float(delta or 0))

        # Check 1: sample too small
        if sample_size < _MIN_SAMPLE_BAD_LEARNING:
            risks.append(f"small_sample(n={sample_size}<{_MIN_SAMPLE_BAD_LEARNING})")
            recommended_action = "REQUIRE_MORE_DATA"
            risk_level = "MEDIUM"

        # Check 2: update too aggressive
        if abs_delta > _MAX_WEIGHT_DELTA_ALLOWED:
            risks.append(
                f"large_delta({abs_delta:.4f}>{_MAX_WEIGHT_DELTA_ALLOWED})")
            risk_level = "HIGH" if risk_level != "HIGH" else risk_level

        # Check 3: consecutive losses but trust still rising
        if (len(recent) >= 3 and all(p < 0 for p in recent[:3])
                and float(new_trust_score or 0) > float(old_trust_score or 0)):
            risks.append("trust_rising_despite_3_recent_losses")
            risk_level = "HIGH"

        # Check 4: winning on a consistently losing signal
        if (float(pnl_pct or 0) > 0 and long_run_wr == 0.0
                and sample_size >= _MIN_SAMPLE_BAD_LEARNING):
            risks.append(f"lucky_win_on_0pct_WR_signal({signal_source})")
            risk_level = "HIGH"

        # Check 5: signal WR ≤ retirement threshold
        if long_run_wr <= _MIN_WR_RETIRE and sample_size >= _MIN_SAMPLE_BAD_LEARNING:
            risks.append(f"signal_below_retirement_WR({long_run_wr:.1%})")
            risk_level = "HIGH" if risk_level != "HIGH" else risk_level

        if risks:
            review_verdict = "FLAGGED"
        else:
            review_verdict = "PASS"
            risk_level = "LOW"
            recommended_action = "ALLOW_UPDATE"

        reason_str = "; ".join(risks) if risks else "No bad learning detected"

        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_learning_review
                    (audit_trace_id, trade_id, ticker, signal_source,
                     old_trust_score, new_trust_score, delta, sample_size,
                     pnl_pct, review_verdict, risk_of_bad_learning,
                     reason, recommended_action)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                audit_trace_id, trade_id, ticker, signal_source,
                old_trust_score, new_trust_score, delta, sample_size,
                pnl_pct, review_verdict, risk_level,
                reason_str, recommended_action,
            ))
            c.commit()

        return {
            "review_verdict": review_verdict,
            "risk_of_bad_learning": risk_level,
            "reason": reason_str,
            "recommended_action": recommended_action,
            "sample_size": sample_size,
            "long_run_wr": long_run_wr,
        }

    except Exception as e:
        print(f"[supervisor] review_learning_update error: {e}")
        return {"review_verdict": "ERROR", "risk_of_bad_learning": "UNKNOWN",
                "error": str(e)}


def supervisor_review_risk(audit_trace_id, ticker, trade_id=None,
                           aiem_decision="EXECUTE", aiem_confidence=None):
    """
    Pre-trade risk gate in MONITOR_ONLY mode. Never blocks.
    Returns risk_score and flags for logging.
    """
    flags = []
    risk_score = 0
    recommended = "ALLOW"

    try:
        import datetime as _rdt
        today = _rdt.date.today()
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("SELECT COUNT(*) FROM aiem_paper_trades WHERE trade_date=%s", (today,))
            trades_today = cu.fetchone()[0] or 0
            if trades_today >= 5:
                flags.append(f"HIGH_TRADE_COUNT({trades_today}_today)")
                risk_score += 20

            cu.execute("SELECT COUNT(*) FROM aiem_paper_trades WHERE ticker=%s AND status='OPEN'",
                       (ticker,))
            already_open = cu.fetchone()[0] or 0
            if already_open:
                flags.append("TICKER_ALREADY_OPEN")
                risk_score += 40
                recommended = "REDUCE_CONFIDENCE"

        verdict = "ALLOW_MONITOR_ONLY"
        if flags:
            verdict = "FLAG_MONITOR_ONLY"

        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_risk_review
                    (audit_trace_id, trade_id, ticker, aiem_decision,
                     aiem_confidence, risk_score, risk_flags_json,
                     supervisor_verdict, recommended_action)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                audit_trace_id, trade_id, ticker, aiem_decision,
                aiem_confidence, risk_score, json.dumps(flags),
                verdict, recommended,
            ))
            c.commit()

        return {"risk_score": risk_score, "flags": flags, "verdict": verdict,
                "recommended": recommended}

    except Exception as e:
        print(f"[supervisor] review_risk error: {e}")
        return {"risk_score": 0, "flags": [], "verdict": "ALLOW_MONITOR_ONLY"}


def supervisor_verify_loop_complete(audit_trace_id):
    """Check if all 6 steps are recorded for this trace."""
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                SELECT loop_complete, missing_steps_json, verdict,
                       scanner_alert_seen, aiem_intake_seen, candidate_ranking_seen,
                       final_decision_seen, paper_trade_seen, outcome_seen,
                       learning_update_seen
                FROM aiem_supervisor_loop_audit WHERE audit_trace_id=%s
            """, (audit_trace_id,))
            row = cu.fetchone()
            if not row:
                return {"found": False, "loop_complete": False}
            return {
                "found": True,
                "loop_complete": row[0],
                "missing": row[1],
                "verdict": row[2],
                "steps": {
                    "scanner_alert": row[3],
                    "aiem_intake": row[4],
                    "candidate_ranking": row[5],
                    "final_decision": row[6],
                    "paper_trade": row[7],
                    "outcome": row[8],
                    "learning_update": row[9],
                },
            }
    except Exception as e:
        return {"error": str(e), "loop_complete": False}


# ══════════════════════════════════════════════════════════════════════════════
# DAILY REPORT
# ══════════════════════════════════════════════════════════════════════════════

def supervisor_generate_daily_report(report_date=None):
    """
    Called 4:50 PM ET daily. Aggregates all supervisor events for the day.
    """
    import datetime as _ddt
    today = report_date or _ddt.date.today()

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as c, c.cursor() as cu:
            def _count(event_type):
                cu.execute("""
                    SELECT COUNT(*) FROM aiem_supervisor_event_log
                    WHERE created_at::date=%s AND event_type=%s
                """, (today, event_type))
                return cu.fetchone()[0] or 0

            alerts = _count("SCANNER_ALERT")
            decisions = _count("FINAL_DECISION")
            trades_opened = _count("PAPER_TRADE_OPENED")
            trades_closed = _count("TRADE_CLOSED")
            learning = _count("LEARNING_UPDATE")

            cu.execute("""
                SELECT
                    SUM(CASE WHEN loop_complete THEN 1 ELSE 0 END),
                    SUM(CASE WHEN NOT loop_complete THEN 1 ELSE 0 END)
                FROM aiem_supervisor_loop_audit
                WHERE created_at::date=%s
            """, (today,))
            la = cu.fetchone()
            complete_loops = la[0] or 0
            incomplete_loops = la[1] or 0

            cu.execute("""
                SELECT COUNT(*) FROM aiem_supervisor_learning_review
                WHERE created_at::date=%s AND risk_of_bad_learning IN ('HIGH','MEDIUM')
            """, (today,))
            bad_flags = cu.fetchone()[0] or 0

            cu.execute("""
                SELECT COUNT(*) FROM aiem_supervisor_risk_review
                WHERE created_at::date=%s AND risk_score > 0
            """, (today,))
            risk_flags = cu.fetchone()[0] or 0

            cu.execute("""
                SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), AVG(pnl_pct)
                FROM aiem_paper_trades WHERE trade_date=%s AND status!='OPEN'
            """, (today,))
            tr = cu.fetchone()
            closed_n = tr[0] or 0
            closed_wins = tr[1] or 0
            avg_pnl = float(tr[2] or 0)

            # ── Observability cross-reference: pipeline audit stage coverage ─
            audit_total_traces = 0
            audit_full_traces  = 0
            audit_avg_stages   = 0.0
            try:
                cu.execute("""
                    WITH stage_counts AS (
                        SELECT trace_id,
                               COUNT(DISTINCT module_name) AS stage_count
                        FROM aiem_pipeline_audit_log
                        WHERE logged_at::date=%s
                        GROUP BY trace_id
                    )
                    SELECT
                        COUNT(*)                                                   AS total_traces,
                        SUM(CASE WHEN stage_count = 13 THEN 1 ELSE 0 END)         AS full_traces,
                        ROUND(AVG(stage_count)::numeric, 1)                        AS avg_stages
                    FROM stage_counts
                """, (today,))
                _pal = cu.fetchone()
                if _pal:
                    audit_total_traces = int(_pal[0] or 0)
                    audit_full_traces  = int(_pal[1] or 0)
                    audit_avg_stages   = float(_pal[2] or 0)
            except Exception as _pal_e:
                print(f"[supervisor] pipeline_audit cross-ref skipped: {_pal_e}")

            # ── Supervisor event coverage per audit_trace_id ─────────────────
            sup_event_coverage_pct = 0.0
            try:
                cu.execute("""
                    SELECT
                        COUNT(DISTINCT t.audit_trace_id)                           AS trades_with_trace,
                        COUNT(DISTINCT e.audit_trace_id)                           AS traces_with_sup_events
                    FROM aiem_paper_trades t
                    LEFT JOIN aiem_supervisor_event_log e
                        ON e.audit_trace_id = t.audit_trace_id
                       AND e.created_at::date = %s
                    WHERE t.trade_date = %s
                      AND t.audit_trace_id IS NOT NULL
                """, (today, today))
                _sev = cu.fetchone()
                if _sev and _sev[0]:
                    sup_event_coverage_pct = round(
                        float(_sev[1] or 0) / float(_sev[0]) * 100, 1
                    )
            except Exception as _sev_e:
                print(f"[supervisor] sup_event_coverage skipped: {_sev_e}")

        wr = (closed_wins / closed_n) if closed_n else 0
        loop_quality = (complete_loops / (complete_loops + incomplete_loops)
                        if (complete_loops + incomplete_loops) else 0.5)
        # Pipeline completeness factor: reward full 13-stage traces
        _audit_factor = (audit_full_traces / audit_total_traces
                         if audit_total_traces else 0.5)
        composite = (wr * 0.45
                     + loop_quality * 0.25
                     + (1 - min(1, bad_flags * 0.1)) * 0.15
                     + _audit_factor * 0.15)
        grade = (
            "A" if composite >= 0.70 else
            "B" if composite >= 0.55 else
            "C" if composite >= 0.40 else
            "D" if composite >= 0.25 else "F"
        )

        report = {
            "report_date": str(today),
            "mode": AIEM_SUPERVISOR_MODE,
            "alerts_seen": alerts,
            "aiem_decisions_seen": decisions,
            "paper_trades_seen": trades_opened,
            "closed_trades_seen": trades_closed,
            "learning_updates_seen": learning,
            "complete_loops": complete_loops,
            "incomplete_loops": incomplete_loops,
            "bad_learning_flags": bad_flags,
            "risk_flags": risk_flags,
            "win_rate": round(wr, 4),
            "avg_pnl_pct": round(avg_pnl, 4),
            "loop_quality": round(loop_quality, 3),
            "pipeline_audit_traces_today": audit_total_traces,
            "pipeline_audit_full_13stage_traces": audit_full_traces,
            "pipeline_audit_avg_stages_per_trace": audit_avg_stages,
            "supervisor_event_coverage_pct": sup_event_coverage_pct,
            "overall_grade": grade,
        }

        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_daily_report
                    (report_date, alerts_seen, aiem_decisions_seen,
                     paper_trades_seen, closed_trades_seen, learning_updates_seen,
                     complete_loops, incomplete_loops, bad_learning_flags,
                     risk_flags, overall_grade, report_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (report_date) DO UPDATE SET
                    alerts_seen=EXCLUDED.alerts_seen,
                    aiem_decisions_seen=EXCLUDED.aiem_decisions_seen,
                    paper_trades_seen=EXCLUDED.paper_trades_seen,
                    closed_trades_seen=EXCLUDED.closed_trades_seen,
                    learning_updates_seen=EXCLUDED.learning_updates_seen,
                    complete_loops=EXCLUDED.complete_loops,
                    incomplete_loops=EXCLUDED.incomplete_loops,
                    bad_learning_flags=EXCLUDED.bad_learning_flags,
                    risk_flags=EXCLUDED.risk_flags,
                    overall_grade=EXCLUDED.overall_grade,
                    report_json=EXCLUDED.report_json,
                    created_at=NOW()
            """, (
                today, alerts, decisions, trades_opened, trades_closed, learning,
                complete_loops, incomplete_loops, bad_flags, risk_flags,
                grade, json.dumps(report),
            ))
            c.commit()

        print(f"[supervisor] daily report {today}: grade={grade} "
              f"loops={complete_loops}/{complete_loops+incomplete_loops} "
              f"bad_learning={bad_flags}")
        return report

    except Exception as e:
        print(f"[supervisor] daily_report error: {e}")
        return {"error": str(e)}


def run_signal_lifecycle(signal_source):
    """Compatibility stub — signal lifecycle view via DB."""
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), AVG(pnl_pct)
                FROM aiem_paper_trades
                WHERE signal_source=%s AND status != 'OPEN'
            """, (signal_source,))
            row = cu.fetchone()
            n = row[0] or 0
            wins = row[1] or 0
            avg_ret = float(row[2] or 0)
        return {
            "signal_source": signal_source,
            "n_closed": n,
            "win_rate": round(wins / n, 4) if n else 0,
            "avg_pnl_pct": round(avg_ret, 4),
            "mode": AIEM_SUPERVISOR_MODE,
        }
    except Exception as e:
        return {"signal_source": signal_source, "error": str(e)}


def run_overfit_check(signal_source):
    """Compatibility stub — basic overfit check via IS/OOS split."""
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                SELECT
                    SUM(CASE WHEN trade_date <= CURRENT_DATE - 90 AND pnl>0 THEN 1 ELSE 0 END)::float /
                    NULLIF(SUM(CASE WHEN trade_date <= CURRENT_DATE - 90 THEN 1 ELSE 0 END), 0) AS is_wr,
                    SUM(CASE WHEN trade_date > CURRENT_DATE - 90 AND pnl>0 THEN 1 ELSE 0 END)::float /
                    NULLIF(SUM(CASE WHEN trade_date > CURRENT_DATE - 90 THEN 1 ELSE 0 END), 0) AS oos_wr,
                    COUNT(*) FILTER (WHERE trade_date > CURRENT_DATE - 90) AS oos_n
                FROM aiem_paper_trades
                WHERE signal_source=%s AND status != 'OPEN'
            """, (signal_source,))
            row = cu.fetchone()
            is_wr  = float(row[0] or 0)
            oos_wr = float(row[1] or 0)
            oos_n  = row[2] or 0
            overfit = (is_wr - oos_wr) > 0.15
        return {
            "signal_source": signal_source,
            "is_win_rate": round(is_wr, 4),
            "oos_win_rate": round(oos_wr, 4),
            "oos_n": oos_n,
            "overfit_flag": overfit,
            "mode": AIEM_SUPERVISOR_MODE,
        }
    except Exception as e:
        return {"signal_source": signal_source, "error": str(e)}


def get_supervisor_summary():
    """Lightweight summary for admin endpoints."""
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=3) as c, c.cursor() as cu:
            cu.execute("""
                SELECT report_date, overall_grade, alerts_seen, paper_trades_seen,
                       complete_loops, bad_learning_flags
                FROM aiem_supervisor_daily_report ORDER BY report_date DESC LIMIT 1
            """)
            latest = cu.fetchone()
            cu.execute("SELECT COUNT(*) FROM aiem_supervisor_event_log "
                       "WHERE created_at > NOW() - INTERVAL '7 days'")
            events_7d = cu.fetchone()[0] or 0
            cu.execute("""
                SELECT COUNT(*) FROM aiem_supervisor_loop_audit
                WHERE created_at > NOW() - INTERVAL '7 days' AND loop_complete
            """)
            complete_7d = cu.fetchone()[0] or 0
            cu.execute("""
                SELECT * FROM aiem_supervisor_signal_health
            """)
            health = [{"source": r[0], "n": r[1], "wr_pct": float(r[3] or 0)}
                      for r in cu.fetchall()]
        return {
            "mode": AIEM_SUPERVISOR_MODE,
            "latest_report": {
                "date": str(latest[0]) if latest else None,
                "grade": latest[1] if latest else None,
                "alerts": latest[2] if latest else None,
                "trades": latest[3] if latest else None,
                "complete_loops": latest[4] if latest else None,
                "bad_learning_flags": latest[5] if latest else None,
            },
            "events_7d": events_7d,
            "complete_loops_7d": complete_7d,
            "signal_health": health,
        }
    except Exception as e:
        return {"mode": AIEM_SUPERVISOR_MODE, "error": str(e)}
