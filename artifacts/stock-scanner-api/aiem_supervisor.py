"""
AIEM_SUPERVISOR_META_REASONING_LAYER
=====================================
Sits above AIEM. AIEM is the trader. The Supervisor is the risk manager,
teacher, auditor, and meta-reasoning controller.

Fires:
  - After every paper trade closes      (run_post_trade_supervisor)
  - After every pick_candidates run     (run_post_pick_supervisor)
  - Daily after market close            (generate_daily_supervisor_report)
  - Weekly on Sunday                    (generate_weekly_supervisor_report)

AIEM remains decision_authority. Supervisor is meta_authority.
Every supervisor action is stored in the DB. Never silently overrides AIEM.
"""

import os
import json
import datetime
import psycopg2

_DB_URL = os.environ.get("DATABASE_URL", "")

# ── Thresholds ─────────────────────────────────────────────────────────────
_MIN_SAMPLE             = 10     # trades needed before trust updates "count"
_MAX_WEIGHT_CHANGE      = 0.08   # max EMA change per single trade (8%)
_MIN_WR_PROMOTION       = 0.55   # win rate required for PROMOTED status
_MAX_WR_RETIREMENT      = 0.35   # win rate at or below this → consider RETIRED
_NOISE_EDGE_THRESHOLD   = 0.02   # <2% avg return = noise, not signal
_MAX_TRADES_PER_DAY     = 5
_MAX_SAME_SOURCE_TODAY  = 3
_OVERFIT_SAMPLE_MIN     = 30     # need ≥30 trades before overfit score matters
_FREEZE_CONSECUTIVE_LOSSES = 5   # flag FREEZE after N consecutive losses


def _conn():
    return psycopg2.connect(_DB_URL)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA INIT
# ══════════════════════════════════════════════════════════════════════════════

def init_schema():
    """Create all 7 supervisor tables. Idempotent."""
    sql_path = os.path.join(os.path.dirname(__file__), "aiem_supervisor_migration.sql")
    try:
        with open(sql_path) as f:
            ddl = f.read()
        with _conn() as c, c.cursor() as cu:
            cu.execute(ddl)
            c.commit()
        print("[supervisor] schema ready — 7 tables + signal_health view")
    except Exception as e:
        print(f"[supervisor] schema init error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 7: OVERRIDE LOG (used by all other modules)
# ══════════════════════════════════════════════════════════════════════════════

def _log_override(audit_trace_id, ticker, trade_id, aiem_decision,
                  aiem_confidence, supervisor_decision, supervisor_confidence,
                  override_type, reason, evidence=None):
    try:
        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_overrides
                    (audit_trace_id, ticker, trade_id, aiem_original_decision,
                     aiem_original_confidence, supervisor_final_decision,
                     supervisor_adjusted_confidence, override_type, reason, evidence_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                audit_trace_id, ticker, trade_id,
                aiem_decision, aiem_confidence,
                supervisor_decision, supervisor_confidence,
                override_type, reason,
                json.dumps(evidence or {}),
            ))
            c.commit()
    except Exception as e:
        print(f"[supervisor] override log error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 1: LEARNING LOOP MONITOR
# ══════════════════════════════════════════════════════════════════════════════

def run_loop_audit(trade_id, audit_trace_id, ticker, signal_source):
    """
    Verify every alert follows the full closed-loop chain:
      scanner → intake → ranking → decision → trade → outcome → learning update
    Returns dict with loop_complete bool and list of missing steps.
    """
    missing = []
    try:
        with _conn() as c, c.cursor() as cu:
            # Step 1: pipeline audit trace exists
            cu.execute("""
                SELECT COUNT(*), array_agg(DISTINCT module_name)
                FROM aiem_pipeline_audit_log
                WHERE trace_id = %s
            """, (audit_trace_id,))
            row = cu.fetchone()
            count, modules = (row[0] or 0), (row[1] or [])
            if count == 0:
                missing.append("pipeline_audit_trace")
            else:
                for step in ["signal_received", "aiem_candidate_intake", "final_aiem_decision"]:
                    if step not in modules:
                        missing.append(step)

            # Step 2: candidate ranking stored
            cu.execute("SELECT COUNT(*) FROM aiem_candidate_rankings WHERE ticker=%s LIMIT 1",
                       (ticker,))
            if (cu.fetchone()[0] or 0) == 0:
                missing.append("candidate_ranking")

            # Step 3: trade exists
            if trade_id:
                cu.execute("SELECT status, pnl, signal_source FROM aiem_paper_trades WHERE id=%s",
                           (trade_id,))
                tr = cu.fetchone()
                if not tr:
                    missing.append("trade_record")
                elif tr[0] == 'OPEN':
                    missing.append("trade_outcome_not_settled")
            else:
                missing.append("trade_record")

            # Step 4: RL experience recorded
            if trade_id:
                cu.execute("SELECT COUNT(*) FROM rl_experience_buffer WHERE trade_id=%s",
                           (trade_id,))
                if (cu.fetchone()[0] or 0) == 0:
                    missing.append("rl_experience_buffer_row")

            # Step 5: trust update recorded
            cu.execute("SELECT COUNT(*) FROM signal_trust_history WHERE trade_id=%s",
                       (str(trade_id),))
            if (cu.fetchone()[0] or 0) == 0:
                missing.append("signal_trust_history_row")

            # Step 6: learning_update_applied audit step
            cu.execute("""
                SELECT COUNT(*) FROM aiem_pipeline_audit_log
                WHERE trace_id=%s AND module_name='learning_update_applied'
            """, (audit_trace_id,))
            if (cu.fetchone()[0] or 0) == 0:
                missing.append("learning_update_applied")

        loop_complete = len(missing) == 0
        verdict = "COMPLETE" if loop_complete else "INCOMPLETE"
        notes = (f"Missing: {', '.join(missing)}" if missing
                 else "All 6 loop steps verified in DB")

        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_loop_audit
                    (audit_trace_id, ticker, trade_id, signal_source,
                     loop_complete, missing_steps_json, supervisor_verdict, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                audit_trace_id, ticker, trade_id, signal_source,
                loop_complete, json.dumps(missing), verdict, notes,
            ))
            c.commit()

        return {"loop_complete": loop_complete, "missing": missing, "verdict": verdict}

    except Exception as e:
        print(f"[supervisor] loop_audit error: {e}")
        return {"loop_complete": False, "missing": ["ERROR"], "verdict": "ERROR", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 2: BAD LEARNING DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

def run_bad_learning_check(signal_source, old_trust, new_trust, sample_size,
                           trade_id, ticker, audit_trace_id, win, pnl_pct):
    """
    Detects when AIEM may be learning the wrong lesson.
    Returns {action, flag_type, reason}.
    """
    flag_type = None
    action = "ALLOW_UPDATE"
    reason_parts = []
    delta = abs(float(new_trust or 0) - float(old_trust or 0))

    try:
        # Check 1: sample size too small for a real update
        if (sample_size or 0) < _MIN_SAMPLE:
            flag_type = "INSUFFICIENT_SAMPLE"
            action = "REQUIRE_MORE_DATA"
            reason_parts.append(
                f"Only {sample_size} trades — need {_MIN_SAMPLE} before trust updates matter")

        # Check 2: weight change too large per single trade
        if delta > _MAX_WEIGHT_CHANGE:
            flag_type = flag_type or "EXCESSIVE_WEIGHT_CHANGE"
            action = "LIMIT_UPDATE" if action == "ALLOW_UPDATE" else action
            reason_parts.append(
                f"Trust change {delta:.4f} exceeds max {_MAX_WEIGHT_CHANGE:.4f} per trade")

        # Check 3: punishing a signal that still has positive long-run WR
        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), AVG(pnl_pct)
                FROM aiem_paper_trades
                WHERE signal_source=%s AND status != 'OPEN'
            """, (signal_source,))
            row = cu.fetchone()
            total_n, total_wins, avg_ret = (row[0] or 0), (row[1] or 0), (row[2] or 0)

        long_run_wr = (total_wins / total_n) if total_n > 0 else 0
        avg_ret_f = float(avg_ret or 0)

        # Check 4: punishing signal with positive long-run expectancy after a single loss
        if not win and old_trust > new_trust and long_run_wr > _MIN_WR_PROMOTION:
            flag_type = flag_type or "PUNISHING_POSITIVE_EXPECTANCY"
            action = "LIMIT_UPDATE" if action == "ALLOW_UPDATE" else action
            reason_parts.append(
                f"Single loss punishing {signal_source} which has "
                f"{long_run_wr:.1%} WR over {total_n} trades")

        # Check 5: signal with 0% WR getting any positive trust update
        if win and old_trust < new_trust and long_run_wr == 0 and total_n >= _MIN_SAMPLE:
            flag_type = flag_type or "LUCKY_WIN_OVER_CONSISTENT_LOSER"
            action = "LIMIT_UPDATE" if action == "ALLOW_UPDATE" else action
            reason_parts.append(
                f"Single win boosting {signal_source} which has 0% WR over {total_n} trades")

        # Check 6: consecutive losses — potential FREEZE
        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                SELECT pnl FROM aiem_paper_trades
                WHERE signal_source=%s AND status != 'OPEN'
                ORDER BY created_at DESC LIMIT %s
            """, (signal_source, _FREEZE_CONSECUTIVE_LOSSES))
            recent = [r[0] for r in cu.fetchall()]

        if (len(recent) >= _FREEZE_CONSECUTIVE_LOSSES
                and all(p <= 0 for p in recent)):
            flag_type = "CONSECUTIVE_LOSSES"
            action = "FREEZE_SIGNAL"
            reason_parts.append(
                f"{_FREEZE_CONSECUTIVE_LOSSES} consecutive losses on {signal_source}")

        # Check 7: noise — average return too small to be a real signal
        if abs(avg_ret_f) < _NOISE_EDGE_THRESHOLD and total_n >= _MIN_SAMPLE:
            flag_type = flag_type or "NOISE_LEVEL_RETURNS"
            action = "FLAG_FOR_REVIEW" if action == "ALLOW_UPDATE" else action
            reason_parts.append(
                f"avg_pnl_pct {avg_ret_f:.3%} below noise threshold {_NOISE_EDGE_THRESHOLD:.1%}")

        if not flag_type:
            flag_type = "NONE"

        reason = "; ".join(reason_parts) if reason_parts else "No bad learning detected"
        allowed_change = _MAX_WEIGHT_CHANGE

        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_bad_learning_flags
                    (audit_trace_id, trade_id, ticker, signal_source,
                     flag_type, old_value, new_value,
                     expected_allowed_change, sample_size, reason, supervisor_action)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                audit_trace_id, trade_id, ticker, signal_source,
                flag_type, float(old_trust or 0), float(new_trust or 0),
                allowed_change, sample_size, reason, action,
            ))
            c.commit()

        if action != "ALLOW_UPDATE":
            _log_override(
                audit_trace_id, ticker, trade_id,
                "AIEM_EMA_TRUST_UPDATE", float(new_trust or 0),
                f"SUPERVISOR_{action}", float(old_trust or 0),
                f"BAD_LEARNING_{flag_type}", reason,
                {"long_run_wr": long_run_wr, "n": total_n, "delta": delta},
            )

        return {"action": action, "flag_type": flag_type, "reason": reason}

    except Exception as e:
        print(f"[supervisor] bad_learning_check error: {e}")
        return {"action": "ALLOW_UPDATE", "flag_type": "ERROR", "reason": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 3: RISK CONTROL SUPERVISOR
# ══════════════════════════════════════════════════════════════════════════════

def run_risk_check(ticker, signal_source, trade_id=None,
                   audit_trace_id=None, aiem_approved=True):
    """
    Pre-trade risk gate. Returns {approved, action, risk_score, flags}.
    AIEM remains decision_authority — supervisor logs and recommends only.
    """
    flags = []
    risk_score = 0
    action = "ALLOW"

    try:
        import datetime as _rdt
        today = _rdt.date.today()

        with _conn() as c, c.cursor() as cu:
            # Risk 1: max trades per day
            cu.execute("""
                SELECT COUNT(*) FROM aiem_paper_trades
                WHERE trade_date=%s AND status != 'CLOSED_STALE'
            """, (today,))
            trades_today = cu.fetchone()[0] or 0
            if trades_today >= _MAX_TRADES_PER_DAY:
                flags.append(f"MAX_TRADES_PER_DAY ({trades_today}/{_MAX_TRADES_PER_DAY})")
                risk_score += 30
                action = "REDUCE_CONFIDENCE"

            # Risk 2: max from same signal source today
            cu.execute("""
                SELECT COUNT(*) FROM aiem_paper_trades
                WHERE trade_date=%s AND signal_source=%s
            """, (today, signal_source))
            source_today = cu.fetchone()[0] or 0
            if source_today >= _MAX_SAME_SOURCE_TODAY:
                flags.append(
                    f"SAME_SOURCE_CONCENTRATION {signal_source} ({source_today} today)")
                risk_score += 25
                action = "REDUCE_CONFIDENCE" if action == "ALLOW" else action

            # Risk 3: same ticker already open
            cu.execute("""
                SELECT COUNT(*) FROM aiem_paper_trades
                WHERE ticker=%s AND status='OPEN'
            """, (ticker,))
            ticker_open = cu.fetchone()[0] or 0
            if ticker_open > 0:
                flags.append(f"TICKER_ALREADY_OPEN ({ticker})")
                risk_score += 40
                action = "BLOCK_TRADE" if risk_score >= 40 else action

            # Risk 4: repeated losses from same signal family (last 5)
            cu.execute("""
                SELECT COUNT(*) FROM aiem_paper_trades
                WHERE signal_source=%s AND status != 'OPEN'
                  AND pnl < 0
                  AND created_at > NOW() - INTERVAL '5 days'
            """, (signal_source,))
            recent_losses = cu.fetchone()[0] or 0
            if recent_losses >= 3:
                flags.append(
                    f"REPEATED_LOSSES {signal_source} ({recent_losses} losses in 5d)")
                risk_score += 20
                action = "REDUCE_CONFIDENCE" if action == "ALLOW" else action

            # Risk 5: daily PnL check — is today already deep red?
            cu.execute("""
                SELECT COALESCE(SUM(pnl), 0) FROM aiem_paper_trades
                WHERE trade_date=%s AND status != 'OPEN'
            """, (today,))
            daily_pnl = float(cu.fetchone()[0] or 0)
            if daily_pnl < -500:
                flags.append(f"DAILY_PNL_NEGATIVE (${daily_pnl:.0f} today)")
                risk_score += 15
                action = "REDUCE_CONFIDENCE" if action == "ALLOW" else action

            # Risk 6: signal lifecycle — frozen or retired?
            cu.execute("""
                SELECT new_status FROM aiem_supervisor_signal_lifecycle
                WHERE signal_source=%s ORDER BY created_at DESC LIMIT 1
            """, (signal_source,))
            lc_row = cu.fetchone()
            lifecycle = lc_row[0] if lc_row else "ACTIVE"
            if lifecycle in ("FROZEN", "RETIRED"):
                flags.append(f"SIGNAL_LIFECYCLE_{lifecycle} ({signal_source})")
                risk_score += 50
                action = "BLOCK_TRADE"

        approved_by_supervisor = action not in ("BLOCK_TRADE", "PAUSE_TRADING_DAY",
                                                 "PAUSE_SIGNAL_FAMILY")

        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_risk_checks
                    (audit_trace_id, ticker, trade_id, risk_score,
                     risk_flags_json, approved_by_aiem,
                     approved_by_supervisor, supervisor_action, reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                audit_trace_id, ticker, trade_id,
                risk_score, json.dumps(flags),
                aiem_approved, approved_by_supervisor,
                action,
                "; ".join(flags) if flags else "All risk checks passed",
            ))
            c.commit()

        if not approved_by_supervisor:
            _log_override(
                audit_trace_id, ticker, trade_id,
                "AIEM_APPROVE", None,
                f"SUPERVISOR_{action}", None,
                "RISK_BLOCK",
                f"risk_score={risk_score}: {'; '.join(flags)}",
                {"flags": flags, "risk_score": risk_score},
            )

        return {
            "approved": approved_by_supervisor,
            "action": action,
            "risk_score": risk_score,
            "flags": flags,
        }

    except Exception as e:
        print(f"[supervisor] risk_check error: {e}")
        return {"approved": True, "action": "ALLOW", "risk_score": 0,
                "flags": [], "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 4: AIEM PERFORMANCE GRADER
# ══════════════════════════════════════════════════════════════════════════════

def generate_performance_report(period_type="daily",
                                period_start=None, period_end=None):
    """
    Grade AIEM over time. period_type: daily / weekly / monthly.
    Returns report dict and stores in aiem_supervisor_performance_reports.
    """
    import datetime as _pdt
    today = _pdt.date.today()

    if period_type == "daily":
        p_start = period_start or today
        p_end   = period_end   or today
    elif period_type == "weekly":
        p_end   = period_end   or today
        p_start = period_start or (p_end - _pdt.timedelta(days=6))
    else:
        p_end   = period_end   or today
        p_start = period_start or (p_end - _pdt.timedelta(days=29))

    try:
        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                SELECT
                    COUNT(*)                                          AS total,
                    SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)           AS wins,
                    AVG(pnl_pct)                                      AS avg_pnl_pct,
                    MIN(pnl)                                          AS worst_trade,
                    MAX(pnl)                                          AS best_trade,
                    COUNT(DISTINCT signal_source)                     AS signal_sources
                FROM aiem_paper_trades
                WHERE trade_date BETWEEN %s AND %s
                  AND status != 'OPEN'
            """, (p_start, p_end))
            stats = cu.fetchone()
            total, wins, avg_pnl, worst, best, sources = (
                stats[0] or 0, stats[1] or 0,
                float(stats[2] or 0), float(stats[3] or 0),
                float(stats[4] or 0), stats[5] or 0,
            )

            # By-source breakdown
            cu.execute("""
                SELECT signal_source,
                       COUNT(*) as n,
                       SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as w,
                       AVG(pnl_pct) as avg_ret
                FROM aiem_paper_trades
                WHERE trade_date BETWEEN %s AND %s AND status != 'OPEN'
                GROUP BY signal_source
                ORDER BY COUNT(*) DESC
            """, (p_start, p_end))
            by_source = [
                {"source": r[0], "n": r[1],
                 "wins": r[2], "wr": round((r[2]/r[1])*100, 1) if r[1] else 0,
                 "avg_ret_pct": round(float(r[3] or 0), 4)}
                for r in cu.fetchall()
            ]

            # Bad learning flags in period
            cu.execute("""
                SELECT COUNT(*), supervisor_action
                FROM aiem_supervisor_bad_learning_flags
                WHERE created_at::date BETWEEN %s AND %s
                GROUP BY supervisor_action
            """, (p_start, p_end))
            bad_flags = {r[1]: r[0] for r in cu.fetchall()}

            # Loop audit completeness
            cu.execute("""
                SELECT COUNT(*), SUM(CASE WHEN loop_complete THEN 1 ELSE 0 END)
                FROM aiem_supervisor_loop_audit
                WHERE created_at::date BETWEEN %s AND %s
            """, (p_start, p_end))
            la = cu.fetchone()
            la_total, la_complete = (la[0] or 0), (la[1] or 0)

            # Risk blocks
            cu.execute("""
                SELECT COUNT(*) FROM aiem_supervisor_risk_checks
                WHERE created_at::date BETWEEN %s AND %s
                  AND approved_by_supervisor = FALSE
            """, (p_start, p_end))
            risk_blocks = cu.fetchone()[0] or 0

        win_rate = (wins / total) if total else 0
        max_drawdown = worst

        # ── Grading logic ──────────────────────────────────────────────
        # learning_quality_score: penalises bad flags, rewards loop completeness
        bad_flag_count = sum(bad_flags.values())
        loop_quality = (la_complete / la_total) if la_total else 0.5
        learning_quality = max(0, min(1,
            loop_quality - (bad_flag_count * 0.05)))

        # risk_discipline_score: penalises risk blocks and single-day concentration
        risk_discipline = max(0, min(1,
            1.0 - (risk_blocks * 0.1)
            - (1 if total > _MAX_TRADES_PER_DAY * 3 else 0) * 0.2))

        # confidence_calibration: are high-confidence picks winning more?
        # (proxy: sources with highest WR should have been picked most often)
        top_source_wr = max((s["wr"] for s in by_source), default=0) / 100
        bottom_source_wr = min((s["wr"] for s in by_source), default=0) / 100
        calibration = min(1.0, top_source_wr + 0.1) if by_source else 0.5

        # Overall grade
        composite = (win_rate * 0.4 + learning_quality * 0.3
                     + risk_discipline * 0.2 + calibration * 0.1)
        if composite >= 0.65:
            grade = "A"
        elif composite >= 0.55:
            grade = "B"
        elif composite >= 0.45:
            grade = "C"
        elif composite >= 0.30:
            grade = "D"
        else:
            grade = "F"

        report = {
            "period_type": period_type,
            "period_start": str(p_start),
            "period_end": str(p_end),
            "total_trades": total,
            "wins": wins,
            "win_rate": round(win_rate, 4),
            "avg_pnl_pct": round(avg_pnl, 4),
            "max_drawdown": round(max_drawdown, 2),
            "best_trade": round(best, 2),
            "by_source": by_source,
            "bad_learning_flags": bad_flags,
            "loop_audit": {"total": la_total, "complete": la_complete,
                           "quality": round(loop_quality, 3)},
            "risk_blocks": risk_blocks,
            "confidence_calibration_score": round(calibration, 3),
            "learning_quality_score": round(learning_quality, 3),
            "risk_discipline_score": round(risk_discipline, 3),
            "composite": round(composite, 3),
            "overall_supervisor_grade": grade,
        }

        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_performance_reports
                    (period_type, period_start, period_end, total_alerts,
                     total_trades, win_rate, avg_pnl_pct, max_drawdown,
                     confidence_calibration_score, learning_quality_score,
                     risk_discipline_score, overall_supervisor_grade, report_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (period_type, period_start) DO UPDATE
                    SET total_trades=EXCLUDED.total_trades,
                        win_rate=EXCLUDED.win_rate,
                        avg_pnl_pct=EXCLUDED.avg_pnl_pct,
                        max_drawdown=EXCLUDED.max_drawdown,
                        overall_supervisor_grade=EXCLUDED.overall_supervisor_grade,
                        report_json=EXCLUDED.report_json,
                        created_at=NOW()
            """, (
                period_type, p_start, p_end, total,
                total, win_rate, avg_pnl, max_drawdown,
                calibration, learning_quality, risk_discipline,
                grade, json.dumps(report),
            ))
            c.commit()

        print(f"[supervisor] performance_report {period_type} {p_start}→{p_end}: "
              f"WR={win_rate:.1%} grade={grade}")
        return report

    except Exception as e:
        print(f"[supervisor] performance_report error: {e}")
        return {"error": str(e), "grade": "F"}


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 5: SIGNAL RETIREMENT AND PROMOTION CONTROLLER
# ══════════════════════════════════════════════════════════════════════════════

def run_signal_lifecycle(signal_source):
    """
    Evaluate a signal source and decide its lifecycle status.
    Returns {current_status, new_status, reason, supervisor_decision}.
    """
    try:
        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),
                       AVG(pnl_pct),
                       MIN(created_at)
                FROM aiem_paper_trades
                WHERE signal_source=%s AND status != 'OPEN'
            """, (signal_source,))
            row = cu.fetchone()
            n = row[0] or 0
            wins = row[1] or 0
            avg_ret = float(row[2] or 0)

            # Recent 10 trades
            cu.execute("""
                SELECT pnl FROM aiem_paper_trades
                WHERE signal_source=%s AND status != 'OPEN'
                ORDER BY created_at DESC LIMIT 10
            """, (signal_source,))
            recent_pnls = [float(r[0]) for r in cu.fetchall()]
            recent_wr = (sum(1 for p in recent_pnls if p > 0)
                         / len(recent_pnls)) if recent_pnls else 0
            recent_avg_ret = (sum(recent_pnls) / len(recent_pnls)) if recent_pnls else 0

            # Current lifecycle status
            cu.execute("""
                SELECT new_status FROM aiem_supervisor_signal_lifecycle
                WHERE signal_source=%s ORDER BY created_at DESC LIMIT 1
            """, (signal_source,))
            lc = cu.fetchone()
            current_status = lc[0] if lc else "ACTIVE"

        win_rate = (wins / n) if n > 0 else 0

        # Determine new status
        reasons = []
        if n < _MIN_SAMPLE:
            new_status = "WATCHLIST"
            reasons.append(f"Only {n} trades — need {_MIN_SAMPLE} for lifecycle decision")
            decision = "ACCUMULATE_DATA"
        elif win_rate >= _MIN_WR_PROMOTION and avg_ret > 0.02 and n >= _OVERFIT_SAMPLE_MIN:
            new_status = "PROMOTED"
            reasons.append(f"WR={win_rate:.1%} avg_ret={avg_ret:.2%} n={n} — exceeds promotion bar")
            decision = "PROMOTE"
        elif win_rate <= _MAX_WR_RETIREMENT and n >= _OVERFIT_SAMPLE_MIN:
            if all(p < 0 for p in recent_pnls[:5]):
                new_status = "RETIRED"
                reasons.append(
                    f"WR={win_rate:.1%} ≤ {_MAX_WR_RETIREMENT:.0%} + 5 recent losses — retire")
                decision = "RETIRE"
            else:
                new_status = "DEMOTED"
                reasons.append(
                    f"WR={win_rate:.1%} ≤ {_MAX_WR_RETIREMENT:.0%} over {n} trades — demote")
                decision = "DEMOTE"
        elif recent_wr < 0.25 and len(recent_pnls) >= 5:
            new_status = "FROZEN"
            reasons.append(
                f"Recent 10-trade WR={recent_wr:.1%} — freeze pending review")
            decision = "FREEZE"
        else:
            new_status = "ACTIVE"
            decision = "MAINTAIN"

        reason = "; ".join(reasons) or "No status change"

        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_signal_lifecycle
                    (signal_source, signal_name, current_status, new_status,
                     reason, sample_size, win_rate, avg_return,
                     recent_return, supervisor_decision)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                signal_source, signal_source,
                current_status, new_status, reason,
                n, win_rate, avg_ret, recent_avg_ret, decision,
            ))
            c.commit()

        if new_status != current_status and new_status in ("RETIRED", "FROZEN", "DEMOTED"):
            _log_override(
                None, None, None,
                "AIEM_SIGNAL_ACTIVE", None,
                f"SUPERVISOR_{new_status}", None,
                f"LIFECYCLE_{decision}",
                reason,
                {"n": n, "wr": win_rate, "avg_ret": avg_ret},
            )

        print(f"[supervisor] signal_lifecycle {signal_source}: "
              f"{current_status}→{new_status} ({decision})")
        return {
            "signal_source": signal_source,
            "current_status": current_status,
            "new_status": new_status,
            "reason": reason,
            "supervisor_decision": decision,
            "n": n,
            "win_rate": win_rate,
        }

    except Exception as e:
        print(f"[supervisor] signal_lifecycle error: {e}")
        return {"new_status": "ACTIVE", "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 6: OVERFITTING PROTECTION MODULE
# ══════════════════════════════════════════════════════════════════════════════

def run_overfit_check(signal_source, audit_trace_id=None, signal_id=None):
    """
    Check for overfitting symptoms.
    Returns {verdict, overfit_score, action}.
    """
    try:
        with _conn() as c, c.cursor() as cu:
            # All trades
            cu.execute("""
                SELECT COUNT(*), AVG(pnl_pct),
                       SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)
                FROM aiem_paper_trades
                WHERE signal_source=%s AND status != 'OPEN'
            """, (signal_source,))
            all_row = cu.fetchone()
            n_all = all_row[0] or 0
            avg_all = float(all_row[1] or 0)
            wins_all = all_row[2] or 0

            # Recent half (last 30 days)
            cu.execute("""
                SELECT COUNT(*), AVG(pnl_pct),
                       SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)
                FROM aiem_paper_trades
                WHERE signal_source=%s AND status != 'OPEN'
                  AND created_at > NOW() - INTERVAL '30 days'
            """, (signal_source,))
            rec_row = cu.fetchone()
            n_rec = rec_row[0] or 0
            avg_rec = float(rec_row[1] or 0)
            wins_rec = rec_row[2] or 0

            # OOS from signal_discoveries if available
            cu.execute("""
                SELECT AVG(oos_edge) FROM aiem_signal_discoveries
                WHERE invented_indicator ILIKE %s OR hypothesis_text ILIKE %s
                LIMIT 1
            """, (f"%{signal_source}%", f"%{signal_source}%"))
            oos_row = cu.fetchone()
            oos_edge = float(oos_row[0]) if oos_row and oos_row[0] else None

        in_sample_wr = (wins_all / n_all) if n_all else 0
        recent_wr = (wins_rec / n_rec) if n_rec else 0
        degradation = in_sample_wr - recent_wr

        overfit_score = 0.0
        reasons = []

        # Small sample
        if n_all < _OVERFIT_SAMPLE_MIN:
            overfit_score += 30
            reasons.append(f"small_sample n={n_all} < {_OVERFIT_SAMPLE_MIN}")

        # Strong in-sample but weak recent
        if in_sample_wr > 0.55 and recent_wr < 0.40 and n_rec >= 5:
            overfit_score += 35
            reasons.append(
                f"IS_WR={in_sample_wr:.1%} but recent_WR={recent_wr:.1%} — "
                f"degradation={degradation:.1%}")

        # OOS edge weak
        if oos_edge is not None and oos_edge < 0.02 and in_sample_wr > 0.55:
            overfit_score += 25
            reasons.append(f"OOS edge {oos_edge:.3f} weak despite IS edge")

        # Outlier dependency: best trade >> avg trade
        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                SELECT MAX(pnl), AVG(pnl) FROM aiem_paper_trades
                WHERE signal_source=%s AND status!='OPEN' AND pnl>0
            """, (signal_source,))
            ol = cu.fetchone()
            max_pnl = float(ol[0] or 0)
            avg_pnl = float(ol[1] or 0)
        outlier_dep = (max_pnl / avg_pnl) if avg_pnl > 0 else 1.0
        if outlier_dep > 5:
            overfit_score += 15
            reasons.append(f"outlier_dependency={outlier_dep:.1f}x (best={max_pnl:.0f}, avg={avg_pnl:.0f})")

        # Verdict
        if overfit_score >= 70:
            verdict = "LIKELY_OVERFIT"
            action = "REJECT_SIGNAL"
        elif overfit_score >= 45:
            verdict = "POSSIBLE_OVERFIT"
            action = "REQUIRE_MORE_DATA"
        elif overfit_score >= 20:
            verdict = "POSSIBLE_OVERFIT"
            action = "FLAG_FOR_REVIEW"
        else:
            verdict = "NOT_OVERFIT"
            action = "ALLOW"

        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO aiem_supervisor_overfit_checks
                    (signal_id, signal_source, audit_trace_id, overfit_score,
                     sample_size, in_sample_edge, recent_edge,
                     regime_stability_score, outlier_dependency_score,
                     verdict, action)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                signal_id, signal_source, audit_trace_id,
                overfit_score, n_all,
                in_sample_wr, recent_wr,
                1.0 - min(1.0, degradation),
                min(1.0, outlier_dep / 10),
                verdict, action,
            ))
            c.commit()

        return {
            "signal_source": signal_source,
            "overfit_score": overfit_score,
            "verdict": verdict,
            "action": action,
            "reasons": reasons,
            "n": n_all,
            "in_sample_wr": in_sample_wr,
            "recent_wr": recent_wr,
        }

    except Exception as e:
        print(f"[supervisor] overfit_check error: {e}")
        return {"verdict": "NOT_OVERFIT", "overfit_score": 0, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINTS (called from main.py)
# ══════════════════════════════════════════════════════════════════════════════

def run_post_trade_supervisor(trade_id, ticker, signal_source,
                              pnl_pct, win, old_trust, new_trust,
                              audit_trace_id=None, sample_size=None):
    """
    Called from _aiem_paper_mark_to_market after every trade close.
    Runs: loop audit + bad learning check + signal lifecycle.
    Non-blocking — all errors are caught.
    """
    results = {}
    try:
        results["loop_audit"] = run_loop_audit(
            trade_id, audit_trace_id, ticker, signal_source)
    except Exception as e:
        results["loop_audit"] = {"error": str(e)}

    try:
        results["bad_learning"] = run_bad_learning_check(
            signal_source, old_trust, new_trust,
            sample_size or _MIN_SAMPLE,
            trade_id, ticker, audit_trace_id, win, pnl_pct)
    except Exception as e:
        results["bad_learning"] = {"error": str(e)}

    try:
        results["signal_lifecycle"] = run_signal_lifecycle(signal_source)
    except Exception as e:
        results["signal_lifecycle"] = {"error": str(e)}

    try:
        results["overfit"] = run_overfit_check(signal_source, audit_trace_id)
    except Exception as e:
        results["overfit"] = {"error": str(e)}

    print(f"[supervisor] post_trade {ticker} #{trade_id}: "
          f"loop={results.get('loop_audit',{}).get('verdict','?')} "
          f"bad_learning={results.get('bad_learning',{}).get('action','?')} "
          f"lifecycle={results.get('signal_lifecycle',{}).get('new_status','?')}")
    return results


def run_post_pick_supervisor(ticker, signal_source,
                             aiem_approved=True, trade_id=None,
                             audit_trace_id=None):
    """
    Called from _aiem_paper_pick_candidates per selected ticker.
    Runs risk check. Returns {approved, action, risk_score}.
    AIEM decision is NOT overridden — result is logged only.
    """
    try:
        return run_risk_check(
            ticker, signal_source, trade_id, audit_trace_id, aiem_approved)
    except Exception as e:
        print(f"[supervisor] post_pick error: {e}")
        return {"approved": True, "action": "ALLOW", "risk_score": 0, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# DAILY + WEEKLY REPORTS
# ══════════════════════════════════════════════════════════════════════════════

def generate_daily_supervisor_report():
    """
    Full daily report — all 7 modules' findings for today.
    Called at 4:45 PM ET. Stored in aiem_supervisor_performance_reports.
    Returns report dict for Telegram / admin endpoint.
    """
    import datetime as _drdt
    today = _drdt.date.today()

    report = generate_performance_report("daily", today, today)

    try:
        with _conn() as c, c.cursor() as cu:
            # Bad learning flags today
            cu.execute("""
                SELECT flag_type, supervisor_action, COUNT(*)
                FROM aiem_supervisor_bad_learning_flags
                WHERE created_at::date = %s
                GROUP BY flag_type, supervisor_action
            """, (today,))
            bad_flags = [{"flag": r[0], "action": r[1], "count": r[2]}
                         for r in cu.fetchall()]

            # Risk blocks today
            cu.execute("""
                SELECT supervisor_action, COUNT(*), array_agg(ticker)
                FROM aiem_supervisor_risk_checks
                WHERE created_at::date = %s AND approved_by_supervisor=FALSE
                GROUP BY supervisor_action
            """, (today,))
            risk_blocks = [{"action": r[0], "count": r[1], "tickers": r[2]}
                           for r in cu.fetchall()]

            # Lifecycle changes today
            cu.execute("""
                SELECT signal_source, current_status, new_status, supervisor_decision
                FROM aiem_supervisor_signal_lifecycle
                WHERE created_at::date = %s AND new_status != current_status
            """, (today,))
            lifecycle_changes = [
                {"source": r[0], "from": r[1], "to": r[2], "decision": r[3]}
                for r in cu.fetchall()
            ]

            # Loop audit completeness today
            cu.execute("""
                SELECT COUNT(*), SUM(CASE WHEN loop_complete THEN 1 ELSE 0 END)
                FROM aiem_supervisor_loop_audit
                WHERE created_at::date = %s
            """, (today,))
            la = cu.fetchone()

            # Overrides today
            cu.execute("""
                SELECT COUNT(*) FROM aiem_supervisor_overrides
                WHERE created_at::date = %s
            """, (today,))
            overrides = cu.fetchone()[0] or 0

    except Exception as e:
        bad_flags, risk_blocks, lifecycle_changes = [], [], []
        la = (0, 0)
        overrides = 0

    la_total, la_complete = (la[0] or 0), (la[1] or 0)

    daily = {
        "report_date": str(today),
        "report_type": "AIEM_SUPERVISOR_DAILY_REPORT",
        "summary": {
            "total_trades_reviewed": report.get("total_trades", 0),
            "win_rate": report.get("win_rate", 0),
            "avg_pnl_pct": report.get("avg_pnl_pct", 0),
            "overall_grade": report.get("overall_supervisor_grade", "F"),
        },
        "learning_loop": {
            "total_audited": la_total,
            "loop_complete": la_complete,
            "loop_incomplete": la_total - la_complete,
        },
        "bad_learning_flags": bad_flags,
        "risk_blocks": risk_blocks,
        "signal_lifecycle_changes": lifecycle_changes,
        "supervisor_overrides": overrides,
        "supervisor_verdict": _overall_verdict(report),
    }

    print(f"[supervisor] DAILY REPORT {today}: "
          f"trades={daily['summary']['total_trades_reviewed']} "
          f"grade={daily['summary']['overall_grade']} "
          f"verdict={daily['supervisor_verdict']}")
    return daily


def generate_weekly_supervisor_report():
    """
    Full weekly report — all 7 modules over last 7 days.
    Called Sunday 6 PM ET.
    """
    import datetime as _wrdt
    today = _wrdt.date.today()
    week_start = today - _wrdt.timedelta(days=6)

    report = generate_performance_report("weekly", week_start, today)

    try:
        with _conn() as c, c.cursor() as cu:
            # All lifecycle changes this week
            cu.execute("""
                SELECT signal_source, new_status, supervisor_decision, COUNT(*)
                FROM aiem_supervisor_signal_lifecycle
                WHERE created_at::date BETWEEN %s AND %s
                GROUP BY signal_source, new_status, supervisor_decision
            """, (week_start, today))
            lifecycle = [{"source": r[0], "status": r[1],
                          "decision": r[2], "times": r[3]}
                         for r in cu.fetchall()]

            # Overfit checks this week
            cu.execute("""
                SELECT signal_source, verdict, overfit_score
                FROM aiem_supervisor_overfit_checks
                WHERE created_at::date BETWEEN %s AND %s
                ORDER BY overfit_score DESC LIMIT 5
            """, (week_start, today))
            overfit = [{"source": r[0], "verdict": r[1], "score": r[2]}
                       for r in cu.fetchall()]

            # All bad learning flags this week
            cu.execute("""
                SELECT flag_type, supervisor_action, COUNT(*)
                FROM aiem_supervisor_bad_learning_flags
                WHERE created_at::date BETWEEN %s AND %s
                GROUP BY flag_type, supervisor_action
                ORDER BY COUNT(*) DESC
            """, (week_start, today))
            bad_flags = [{"flag": r[0], "action": r[1], "count": r[2]}
                         for r in cu.fetchall()]

            # Signal health view
            cu.execute("SELECT * FROM aiem_supervisor_signal_health")
            signal_health = [
                {"source": r[0], "n": r[1], "wins": r[2],
                 "wr_pct": float(r[3] or 0),
                 "avg_pnl_pct": float(r[4] or 0),
                 "lifecycle": r[5]}
                for r in cu.fetchall()
            ]
    except Exception as e:
        lifecycle, overfit, bad_flags, signal_health = [], [], [], []

    weekly = {
        "report_type": "AIEM_SUPERVISOR_WEEKLY_REPORT",
        "period": f"{week_start} to {today}",
        "summary": {
            "total_trades": report.get("total_trades", 0),
            "win_rate": report.get("win_rate", 0),
            "avg_pnl_pct": report.get("avg_pnl_pct", 0),
            "overall_grade": report.get("overall_supervisor_grade", "F"),
            "learning_quality": report.get("learning_quality_score", 0),
            "risk_discipline": report.get("risk_discipline_score", 0),
        },
        "signal_health": signal_health,
        "signal_lifecycle_changes": lifecycle,
        "overfit_concerns": overfit,
        "bad_learning_flags": bad_flags,
        "by_source": report.get("by_source", []),
        "supervisor_verdict": _overall_verdict(report),
    }

    print(f"[supervisor] WEEKLY REPORT {week_start}→{today}: "
          f"grade={weekly['summary']['overall_grade']}")
    return weekly


def _overall_verdict(report):
    grade = report.get("overall_supervisor_grade", "F")
    return {
        "A": "AIEM improving with controlled risk — continue",
        "B": "AIEM stable but not clearly improving — monitor",
        "C": "AIEM learning but noisy — review bad flags",
        "D": "AIEM learning wrong lessons — intervention needed",
        "F": "AIEM unsafe or unverified — pause and audit",
    }.get(grade, "UNKNOWN")


def get_supervisor_summary():
    """Lightweight summary for the admin endpoint. No blocking queries."""
    try:
        import datetime as _sdt
        today = _sdt.date.today()
        with _conn() as c, c.cursor() as cu:
            cu.execute("""
                SELECT overall_supervisor_grade, win_rate, avg_pnl_pct,
                       total_trades, created_at
                FROM aiem_supervisor_performance_reports
                ORDER BY created_at DESC LIMIT 1
            """)
            latest = cu.fetchone()

            cu.execute("SELECT COUNT(*) FROM aiem_supervisor_bad_learning_flags "
                       "WHERE created_at > NOW() - INTERVAL '7 days'")
            flags_7d = cu.fetchone()[0] or 0

            cu.execute("SELECT COUNT(*) FROM aiem_supervisor_risk_checks "
                       "WHERE approved_by_supervisor=FALSE "
                       "AND created_at > NOW() - INTERVAL '7 days'")
            blocks_7d = cu.fetchone()[0] or 0

            cu.execute("SELECT COUNT(*) FROM aiem_supervisor_overrides "
                       "WHERE created_at > NOW() - INTERVAL '7 days'")
            overrides_7d = cu.fetchone()[0] or 0

            cu.execute("SELECT * FROM aiem_supervisor_signal_health")
            health = [{"source": r[0], "n": r[1], "wr_pct": float(r[3] or 0),
                       "lifecycle": r[5]} for r in cu.fetchall()]

        return {
            "latest_grade": latest[0] if latest else None,
            "latest_win_rate": float(latest[1] or 0) if latest else None,
            "latest_avg_pnl_pct": float(latest[2] or 0) if latest else None,
            "latest_total_trades": latest[3] if latest else None,
            "last_report_at": str(latest[4]) if latest else None,
            "bad_learning_flags_7d": flags_7d,
            "risk_blocks_7d": blocks_7d,
            "overrides_7d": overrides_7d,
            "signal_health": health,
        }
    except Exception as e:
        return {"error": str(e)}
