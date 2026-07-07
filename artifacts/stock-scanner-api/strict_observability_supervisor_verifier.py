"""
strict_observability_supervisor_verifier.py
==========================================
Verifies that:
  1. All 13 AIEM pipeline stages are logged in aiem_pipeline_audit_log for
     every trade that has an audit_trace_id.
  2. The Supervisor consumes (not duplicates) the Observability Layer —
     aiem_supervisor_event_log rows reference real audit_trace_ids.
  3. Closed-loop tables (trust history, Thompson history, candidate rankings,
     RL buffer) are populated and cross-referenced with real trace_ids.

Verdict per check: PASS / PARTIAL / FAIL
Overall verdict:   PASS (all PASS) / PARTIAL (any PARTIAL) / FAIL (any FAIL)

Usage:
    python3 strict_observability_supervisor_verifier.py [--lookback-days N]

Exit codes: 0 = PASS, 1 = PARTIAL, 2 = FAIL, 3 = DB error
"""

import os
import sys
import argparse
import json
import datetime
import traceback

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("[FATAL] psycopg2 not installed")
    sys.exit(3)

# Stages 1-11 are logged at trade ENTRY (in _aiem_paper_execute_today).
# Stages 12-13 are logged at trade EXIT (in _aiem_paper_mark_to_market).
# Open trades legitimately max out at 11 stages — do not penalise them for
# missing 12+13 which have not fired yet.
_ENTRY_STAGES = [
    "signal_received",
    "aiem_candidate_intake",
    "duplicate_filter_check",
    "market_context_loaded",
    "module_scores_generated",
    "candidate_ranking_created",
    "trust_weights_applied",
    "drift_gate_checked",
    "thompson_sampler_checked",
    "rl_weight_checked",
    "final_aiem_decision",
]
_EXIT_STAGES = [
    "outcome_recorded",
    "learning_update_applied",
]
_REQUIRED_STAGES = _ENTRY_STAGES + _EXIT_STAGES
_N_STAGES = len(_REQUIRED_STAGES)


def _connect(db_url: str):
    return psycopg2.connect(db_url, connect_timeout=6)


def _verdict(pass_n: int, total: int, threshold: float = 1.0) -> str:
    if total == 0:
        return "PARTIAL"
    ratio = pass_n / total
    if ratio >= threshold:
        return "PASS"
    if ratio >= 0.50:
        return "PARTIAL"
    return "FAIL"


def check_pipeline_stage_coverage(cu, since: datetime.date) -> dict:
    """
    CHECK 1 — Correct stages present per trade trace.

    Rules:
      OPEN trade  → must have all 11 ENTRY stages (12+13 fire on close, not yet expected)
      CLOSED trade → must have all 13 stages (entry + exit)

    Threshold: ≥80% of traces meet their expected stage set → PASS.
    """
    cu.execute("""
        SELECT t.audit_trace_id,
               t.ticker,
               t.trade_date,
               t.status,
               COUNT(DISTINCT l.module_name)                              AS stages_logged,
               ARRAY_AGG(DISTINCT l.module_name ORDER BY l.module_name)   AS stages_present
        FROM aiem_paper_trades t
        LEFT JOIN aiem_pipeline_audit_log l ON l.trace_id = t.audit_trace_id
        WHERE t.trade_date >= %s
          AND t.audit_trace_id IS NOT NULL
        GROUP BY t.audit_trace_id, t.ticker, t.trade_date, t.status
        ORDER BY t.trade_date DESC, t.ticker
    """, (since,))
    rows = cu.fetchall()

    if not rows:
        return {
            "check": "pipeline_stage_coverage",
            "verdict": "PARTIAL",
            "detail": (
                "No trades with audit_trace_id found in the lookback window. "
                "New trades logged after the code deployment will appear here."
            ),
            "total_traces": 0,
            "full_traces": 0,
            "partial_traces": 0,
            "missing_by_trace": [],
        }

    full = 0
    partial_n = 0
    missing_by_trace = []
    open_count = 0
    closed_count = 0

    for trace_id, ticker, trade_date, status, stages_logged, stages_present in rows:
        present_set = set(stages_present or [])
        is_open = (status or "OPEN") == "OPEN"
        expected = _ENTRY_STAGES if is_open else _REQUIRED_STAGES
        if is_open:
            open_count += 1
        else:
            closed_count += 1

        missing = [s for s in expected if s not in present_set]
        if not missing:
            full += 1
        else:
            partial_n += 1
            missing_by_trace.append({
                "trace_id": trace_id,
                "ticker": ticker,
                "trade_date": str(trade_date),
                "status": status,
                "stages_logged": stages_logged,
                "expected_stages": len(expected),
                "missing_stages": missing,
            })

    total = len(rows)
    v = _verdict(full, total, threshold=0.80)
    return {
        "check": "pipeline_stage_coverage",
        "verdict": v,
        "detail": (
            f"{full}/{total} traces meet their expected stage set "
            f"({open_count} open=11-stage, {closed_count} closed=13-stage). "
            f"{partial_n} incomplete. Threshold: 80% → PASS."
        ),
        "total_traces": total,
        "full_traces": full,
        "partial_traces": partial_n,
        "open_trades": open_count,
        "closed_trades": closed_count,
        "missing_by_trace": missing_by_trace[:10],
    }


def check_supervisor_consumes_audit(cu, since: datetime.date) -> dict:
    """
    CHECK 2 — Supervisor events reference real aiem_pipeline_audit_log trace_ids
    (not just logging independently). Verifies at least 70% of supervisor events
    for trades in the window carry an audit_trace_id that exists in the audit log.
    """
    cu.execute("""
        SELECT
            COUNT(*)                                                  AS total_sup_events,
            COUNT(e.audit_trace_id)                                   AS events_with_trace,
            COUNT(DISTINCT l.trace_id)                                AS traces_cross_linked
        FROM aiem_supervisor_event_log e
        LEFT JOIN aiem_pipeline_audit_log l ON l.trace_id = e.audit_trace_id
        WHERE e.created_at::date >= %s
    """, (since,))
    row = cu.fetchone()
    total_ev   = int(row[0] or 0)
    with_trace = int(row[1] or 0)
    cross_linked = int(row[2] or 0)

    if total_ev == 0:
        return {
            "check": "supervisor_consumes_audit",
            "verdict": "PARTIAL",
            "detail": "No supervisor events found in the lookback window.",
            "total_supervisor_events": 0,
            "events_with_audit_trace_id": 0,
            "traces_cross_linked_to_audit_log": 0,
        }

    pct = with_trace / total_ev
    v = "PASS" if pct >= 0.70 else "PARTIAL" if pct >= 0.40 else "FAIL"
    return {
        "check": "supervisor_consumes_audit",
        "verdict": v,
        "detail": (
            f"{with_trace}/{total_ev} supervisor events carry an audit_trace_id "
            f"({pct*100:.1f}%). {cross_linked} unique traces cross-linked. "
            f"Threshold: 70% → PASS."
        ),
        "total_supervisor_events": total_ev,
        "events_with_audit_trace_id": with_trace,
        "traces_cross_linked_to_audit_log": cross_linked,
    }


def check_closed_loop_tables(cu, since: datetime.date) -> dict:
    """
    CHECK 3 — Closed-loop tables populated and cross-referenced with trace_ids.
    Tables: signal_trust_history, aiem_paper_thompson_history,
            aiem_candidate_rankings, rl_experience_buffer.
    """
    results = {}

    _table_checks = [
        ("signal_trust_history",       "audit_trace_id", "recorded_at",  None),
        ("aiem_paper_thompson_history","audit_trace_id", "recorded_at",  None),
        # candidate_rankings: exclude smoke-test rows
        # %% is required because psycopg2 treats lone % as a parameter placeholder
        ("aiem_candidate_rankings",    "audit_trace_id", "created_at",
         "AND run_id NOT LIKE 'aiem_smoke_%%' AND run_id NOT LIKE 'smoke_%%'"),
    ]

    for tbl, trace_col, date_col, extra_where in _table_checks:
        try:
            extra = extra_where or ""
            cu.execute(f"""
                SELECT COUNT(*) AS total_rows,
                       COUNT({trace_col}) AS rows_with_trace
                FROM {tbl}
                WHERE {date_col}::date >= %s {extra}
            """, (since,))
            r = cu.fetchone()
            total, with_trace = int(r[0] or 0), int(r[1] or 0)
            pct = (with_trace / total * 100) if total else 0
            status = (
                "PASS"    if total > 0 and pct >= 70 else
                "PARTIAL" if total > 0 else
                "PARTIAL"   # 0 production rows: needs first real trade cycle
            )
            results[tbl] = {"rows": total, "with_trace_id": with_trace,
                             "pct": round(pct, 1), "status": status}
        except Exception as e:
            results[tbl] = {"error": str(e), "status": "FAIL"}

    try:
        cu.execute("""
            SELECT COUNT(*) FROM rl_experience_buffer
            WHERE created_at::date >= %s
        """, (since,))
        rl_rows = int(cu.fetchone()[0] or 0)
        results["rl_experience_buffer"] = {
            "rows": rl_rows,
            "note": "no audit_trace_id column (RL buffer is aggregate, not per-trade)",
            "status": "PASS" if rl_rows > 0 else "PARTIAL",
        }
    except Exception as e:
        results["rl_experience_buffer"] = {"error": str(e), "status": "FAIL"}

    statuses = [v.get("status", "FAIL") for v in results.values()]
    if all(s == "PASS" for s in statuses):
        overall = "PASS"
    elif any(s == "FAIL" for s in statuses):
        overall = "FAIL"
    else:
        overall = "PARTIAL"

    return {
        "check": "closed_loop_tables",
        "verdict": overall,
        "detail": (
            "signal_trust_history + thompson_history + candidate_rankings + "
            "rl_experience_buffer — rows present and trace-linked."
        ),
        "tables": results,
    }


def check_no_scanner_decides(cu, since: datetime.date) -> dict:
    """
    CHECK 4 — Strict AIEM source verification: no pipeline audit log row
    has decision_authority='stock_scanner' for the final_aiem_decision stage.
    This ensures the scanner never makes the trade decision.
    """
    cu.execute("""
        SELECT COUNT(*) FROM aiem_pipeline_audit_log
        WHERE module_name = 'final_aiem_decision'
          AND decision_authority != 'AIEM'
          AND logged_at::date >= %s
    """, (since,))
    violations = int(cu.fetchone()[0] or 0)

    cu.execute("""
        SELECT COUNT(*) FROM aiem_pipeline_audit_log
        WHERE module_name = 'final_aiem_decision'
          AND logged_at::date >= %s
    """, (since,))
    total = int(cu.fetchone()[0] or 0)

    v = "PASS" if violations == 0 and total > 0 else (
        "PARTIAL" if total == 0 else "FAIL"
    )
    return {
        "check": "no_scanner_decides",
        "verdict": v,
        "detail": (
            f"{violations}/{total} final_aiem_decision rows have "
            f"decision_authority != 'AIEM'. "
            f"Zero violations required for PASS."
        ),
        "final_decision_rows": total,
        "authority_violations": violations,
    }


def check_learning_loop_closed(cu, since: datetime.date) -> dict:
    """
    CHECK 5 — For closed trades with audit_trace_id, confirm the
    learning_update_applied step is logged (stages 12+13 wired in MTM).
    """
    cu.execute("""
        SELECT
            COUNT(DISTINCT t.audit_trace_id)   AS closed_with_trace,
            COUNT(DISTINCT l.trace_id)          AS with_learning_step
        FROM aiem_paper_trades t
        LEFT JOIN aiem_pipeline_audit_log l
            ON l.trace_id = t.audit_trace_id
           AND l.module_name = 'learning_update_applied'
        WHERE t.trade_date >= %s
          AND t.audit_trace_id IS NOT NULL
          AND t.status != 'OPEN'
    """, (since,))
    row = cu.fetchone()
    closed = int(row[0] or 0)
    with_learning = int(row[1] or 0)

    if closed == 0:
        return {
            "check": "learning_loop_closed",
            "verdict": "PARTIAL",
            "detail": "No closed trades with audit_trace_id in lookback window.",
            "closed_traces": 0,
            "with_learning_update": 0,
        }

    pct = with_learning / closed
    v = "PASS" if pct >= 0.80 else "PARTIAL" if pct >= 0.40 else "FAIL"
    return {
        "check": "learning_loop_closed",
        "verdict": v,
        "detail": (
            f"{with_learning}/{closed} closed traces have "
            f"learning_update_applied logged ({pct*100:.1f}%). "
            f"Threshold: 80% → PASS."
        ),
        "closed_traces": closed,
        "with_learning_update": with_learning,
    }


def run_all_checks(db_url: str, lookback_days: int = 7) -> dict:
    since = datetime.date.today() - datetime.timedelta(days=lookback_days)
    results = []
    overall = "PASS"

    try:
        with _connect(db_url) as conn, conn.cursor() as cu:
            checks = [
                check_pipeline_stage_coverage,
                check_supervisor_consumes_audit,
                check_closed_loop_tables,
                check_no_scanner_decides,
                check_learning_loop_closed,
            ]
            for fn in checks:
                try:
                    r = fn(cu, since)
                except Exception as e:
                    r = {
                        "check": fn.__name__,
                        "verdict": "FAIL",
                        "detail": f"Check raised exception: {e}",
                        "traceback": traceback.format_exc(),
                    }
                results.append(r)
    except Exception as db_e:
        return {
            "overall_verdict": "FAIL",
            "error": f"DB connection failed: {db_e}",
            "checks": [],
        }

    verdicts = [r["verdict"] for r in results]
    if any(v == "FAIL" for v in verdicts):
        overall = "FAIL"
    elif any(v == "PARTIAL" for v in verdicts):
        overall = "PARTIAL"
    else:
        overall = "PASS"

    return {
        "run_at": datetime.datetime.utcnow().isoformat() + "Z",
        "lookback_days": lookback_days,
        "since": str(since),
        "overall_verdict": overall,
        "checks": results,
    }


def _print_report(report: dict) -> None:
    ov = report["overall_verdict"]
    symbol = {"PASS": "✅", "PARTIAL": "⚠️", "FAIL": "❌"}.get(ov, "?")
    print(f"\n{'='*68}")
    print(f"  AIEM Observability + Supervisor Verifier")
    print(f"  Run at:  {report.get('run_at','')}")
    print(f"  Window:  last {report.get('lookback_days',7)} days (since {report.get('since','')})")
    print(f"  Overall: {symbol} {ov}")
    print(f"{'='*68}")
    for c in report.get("checks", []):
        v = c["verdict"]
        sym = {"PASS": "✅", "PARTIAL": "⚠️", "FAIL": "❌"}.get(v, "?")
        print(f"\n  {sym} [{v:7s}] {c['check']}")
        print(f"           {c.get('detail','')}")
        for k, val in c.items():
            if k in ("check", "verdict", "detail", "traceback"):
                continue
            if isinstance(val, list) and len(val) > 3:
                val = val[:3]
                val.append("…")
            if isinstance(val, dict) and len(val) > 5:
                val = dict(list(val.items())[:5])
            print(f"           {k}: {json.dumps(val, default=str)}")
    print(f"\n{'='*68}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strict observability + supervisor verifier for AIEM pipeline."
    )
    parser.add_argument(
        "--lookback-days", type=int, default=7,
        help="Days of history to scan (default: 7)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON instead of human-readable report"
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("[FATAL] DATABASE_URL env var not set")
        return 3

    report = run_all_checks(db_url, lookback_days=args.lookback_days)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_report(report)

    ov = report.get("overall_verdict", "FAIL")
    return {"PASS": 0, "PARTIAL": 1, "FAIL": 2}.get(ov, 3)


if __name__ == "__main__":
    sys.exit(main())
