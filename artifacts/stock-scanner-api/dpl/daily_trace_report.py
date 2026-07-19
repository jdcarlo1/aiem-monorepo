#!/usr/bin/env python3
"""
dpl/daily_trace_report.py — DPL Daily Trace Report Generator (Item 10)

Generates a daily evidence report containing:
  - scheduler trigger records
  - trace IDs and decision IDs
  - strategy selected / rejected alternatives
  - risk gate outcomes
  - execution records
  - reconciliation results
  - chain head
  - recoveries and failures

Usage:
    python3 dpl/daily_trace_report.py [--date YYYY-MM-DD] [--output report.json]

Scheduled via APScheduler (4:45 PM ET daily) in aiem_options_scheduler.py.
Can also be run standalone for any past date.
"""

import os
import sys
import json
import hashlib
import logging
import argparse
import psycopg2
import psycopg2.extras
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger("daily_trace_report")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

_DB_URL = os.environ.get("DATABASE_URL", "")
_ET = timezone(timedelta(hours=-4))  # EDT; adjust to -5 for EST if needed

_REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "tools", "logs", "daily_reports")


def _conn():
    return psycopg2.connect(_DB_URL, connect_timeout=8,
                             cursor_factory=psycopg2.extras.RealDictCursor)


def _chain_head() -> dict:
    chain_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "tools", "verified_run_chain.jsonl")
    chain_file = os.path.normpath(chain_file)
    if not os.path.exists(chain_file):
        return {"error": "chain file not found", "path": chain_file}
    entries = []
    with open(chain_file) as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line.strip()))
    if not entries:
        return {"error": "chain file empty"}
    last = entries[-1]
    return {
        "seq":        last.get("seq"),
        "ts_end":     last.get("ts_end"),
        "entry_hash": last.get("entry_hash"),
        "exit_code":  last.get("exit_code"),
        "total_entries": len(entries),
    }


def _scheduler_triggers(report_date: date) -> list:
    rows = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT run_id, run_date, trigger_type, trigger_time_et,
                       stocks_scanned, contracts_evaluated,
                       selected_ticker, selected_strategy, decision,
                       final_ccs, created_at
                FROM options_engine_runs
                WHERE run_date = %s
                  AND is_test_record = FALSE
                ORDER BY created_at
            """, (report_date,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
    except Exception as e:
        log.warning(f"scheduler_triggers query failed: {e}")
    return rows


def _decisions(report_date: date) -> list:
    rows = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT decision_id, ticker, scan_date, direction,
                       call_score, put_score, decision_type,
                       verification_status, chain_hash, input_hash,
                       created_at
                FROM oe_decision_audit
                WHERE DATE(created_at AT TIME ZONE 'America/New_York') = %s
                  AND is_test_record = FALSE
                ORDER BY created_at
            """, (report_date,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
    except Exception as e:
        log.warning(f"decisions query failed: {e}")
    return rows


def _gate_events(report_date: date) -> list:
    rows = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT event_id, decision_id, gate_name, action_taken,
                       gate_value, threshold, reason, created_at
                FROM oe_gate_events
                WHERE DATE(created_at AT TIME ZONE 'America/New_York') = %s
                  AND is_test_record = FALSE
                ORDER BY created_at
            """, (report_date,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
    except Exception as e:
        log.warning(f"gate_events query failed: {e}")
    return rows


def _strategy_candidates(report_date: date) -> list:
    rows = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT capture_id, decision_id, ticker, scan_date,
                       strategy_name, strategy_direction, score,
                       selected, rejection_reason, created_at
                FROM oe_strategy_candidates
                WHERE DATE(created_at AT TIME ZONE 'America/New_York') = %s
                  AND is_test_record = FALSE
                ORDER BY created_at, score DESC
            """, (report_date,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
    except Exception as e:
        log.warning(f"strategy_candidates query failed: {e}")
    return rows


def _pipeline_jobs(report_date: date) -> list:
    rows = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, scan_date, status, claimed_at,
                       completed_at, worker_pid, retry_count,
                       error_message
                FROM options_pipeline_jobs
                WHERE scan_date = %s
                ORDER BY id
            """, (report_date,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
    except Exception as e:
        log.warning(f"pipeline_jobs query failed: {e}")
    return rows


def _unreplayable(report_date: date) -> list:
    rows = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT decision_id, reason_code, recoverable, registered_at
                FROM oe_unreplayable_rows
                WHERE DATE(registered_at AT TIME ZONE 'America/New_York') = %s
                  AND is_test_record = FALSE
                ORDER BY registered_at
            """, (report_date,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
    except Exception as e:
        log.warning(f"unreplayable query failed: {e}")
    return rows


def _paper_trades(report_date: date) -> list:
    rows = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, trade_date, direction, signal_source,
                       entry_price, current_price, pnl_pct, status,
                       created_at
                FROM aiem_paper_trades
                WHERE trade_date = %s
                ORDER BY created_at
            """, (report_date,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
    except Exception as e:
        log.warning(f"paper_trades query failed: {e}")
    return rows


def _index_corrections(report_date: date) -> list:
    rows = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT correction_id, original_entry_id, field_corrected,
                       old_value, new_value, correction_reason,
                       correction_hash, created_at
                FROM oe_index_corrections
                WHERE DATE(created_at AT TIME ZONE 'America/New_York') = %s
                  AND is_test_record = FALSE
                ORDER BY created_at
            """, (report_date,))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
    except Exception as e:
        log.warning(f"index_corrections query failed: {e}")
    return rows


def build_report(report_date: date) -> dict:
    log.info(f"Building daily trace report for {report_date}")

    triggers    = _scheduler_triggers(report_date)
    decisions   = _decisions(report_date)
    gates       = _gate_events(report_date)
    candidates  = _strategy_candidates(report_date)
    jobs        = _pipeline_jobs(report_date)
    unreplayable = _unreplayable(report_date)
    paper       = _paper_trades(report_date)
    corrections = _index_corrections(report_date)
    chain_head  = _chain_head()

    # Execution summary
    total_decisions = len(decisions)
    trades     = [d for d in decisions if d.get("decision_type") == "trade"]
    no_trades  = [d for d in decisions if d.get("decision_type") == "no_trade"]
    verified   = [d for d in decisions if d.get("verification_status") == "VERIFIED"]
    drifted    = [d for d in decisions
                  if d.get("verification_status") in ("CODE_DRIFT", "WEIGHTS_DRIFT")]

    report = {
        "report_meta": {
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "report_date":   report_date.isoformat(),
            "report_type":   "DAILY_TRACE",
            "generator":     "dpl/daily_trace_report.py",
            "item_reference": "DPL Remediation Directive Item 10",
        },
        "summary": {
            "scheduler_triggers":   len(triggers),
            "total_decisions":      total_decisions,
            "trades":               len(trades),
            "no_trades":            len(no_trades),
            "verified_decisions":   len(verified),
            "drifted_decisions":    len(drifted),
            "unreplayable_rows":    len(unreplayable),
            "gate_events":          len(gates),
            "strategy_candidates":  len(candidates),
            "jobs_total":           len(jobs),
            "jobs_completed":       len([j for j in jobs if j.get("status") == "COMPLETED"]),
            "jobs_failed":          len([j for j in jobs if j.get("status") == "FAILED"]),
            "paper_trades":         len(paper),
            "index_corrections":    len(corrections),
            "chain_head_seq":       chain_head.get("seq"),
            "chain_head_hash":      chain_head.get("entry_hash"),
        },
        "chain_head": chain_head,
        "scheduler_triggers": triggers,
        "decisions": decisions,
        "gate_events": gates,
        "strategy_candidates": candidates,
        "pipeline_jobs": jobs,
        "unreplayable_rows": unreplayable,
        "paper_trades": paper,
        "index_corrections": corrections,
        "recoveries": [j for j in jobs if j.get("retry_count", 0) > 0],
        "failures": (
            [j for j in jobs if j.get("status") == "FAILED"] +
            [d for d in decisions if d.get("verification_status") == "REPLAY_ERROR"] +
            unreplayable
        ),
    }

    # Compute report SHA-256 for integrity
    payload = json.dumps(report, sort_keys=True, separators=(',', ':')).encode()
    report["report_sha256"] = hashlib.sha256(payload).hexdigest()

    return report


def save_report(report: dict, output_path: str = None) -> str:
    if output_path is None:
        os.makedirs(_REPORT_DIR, exist_ok=True)
        rdate = report["report_meta"]["report_date"]
        output_path = os.path.join(_REPORT_DIR, f"daily_trace_{rdate}.json")

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    log.info(f"Report saved to {output_path}")
    return output_path


def print_summary(report: dict):
    s = report["summary"]
    ch = report["chain_head"]
    print("\n" + "=" * 60)
    print(f"  AIEM OPTIONS ENGINE — DAILY TRACE REPORT")
    print(f"  Date: {report['report_meta']['report_date']}")
    print(f"  Generated: {report['report_meta']['generated_at']}")
    print("=" * 60)
    print(f"  Scheduler triggers:    {s['scheduler_triggers']}")
    print(f"  Total decisions:       {s['total_decisions']}  "
          f"(trades={s['trades']} no_trade={s['no_trades']})")
    print(f"  Verified:              {s['verified_decisions']}")
    print(f"  Drifted (CODE/WEIGHT): {s['drifted_decisions']}")
    print(f"  Unreplayable rows:     {s['unreplayable_rows']}")
    print(f"  Gate events:           {s['gate_events']}")
    print(f"  Pipeline jobs:         {s['jobs_total']}  "
          f"(ok={s['jobs_completed']} fail={s['jobs_failed']})")
    print(f"  Paper trades:          {s['paper_trades']}")
    print(f"  Index corrections:     {s['index_corrections']}")
    print(f"  Chain head SEQ:        {ch.get('seq')}")
    print(f"  Chain head hash:       {ch.get('entry_hash','?')}")
    print(f"  Report SHA-256:        {report.get('report_sha256','?')}")
    print("=" * 60)
    if s["unreplayable_rows"] > 0:
        print(f"  !! WARNING: {s['unreplayable_rows']} unreplayable row(s) today !!")
    if s["drifted_decisions"] > 0:
        print(f"  !! WARNING: {s['drifted_decisions']} drifted decision(s) today !!")
    if s["jobs_failed"] > 0:
        print(f"  !! WARNING: {s['jobs_failed']} failed job(s) today !!")
    print()


def main():
    parser = argparse.ArgumentParser(description="DPL Daily Trace Report Generator")
    parser.add_argument("--date", default=None,
                        help="Report date YYYY-MM-DD (default: today ET)")
    parser.add_argument("--output", default=None,
                        help="Output file path (default: tools/logs/daily_reports/daily_trace_YYYY-MM-DD.json)")
    parser.add_argument("--print-summary", action="store_true",
                        help="Print human-readable summary to stdout")
    args = parser.parse_args()

    if args.date:
        report_date = date.fromisoformat(args.date)
    else:
        report_date = datetime.now(_ET).date()

    report = build_report(report_date)
    path   = save_report(report, args.output)

    if args.print_summary:
        print_summary(report)
    else:
        print(json.dumps(report["summary"], indent=2))

    print(f"Report: {path}")
    print(f"SHA-256: {report['report_sha256']}")


if __name__ == "__main__":
    main()
