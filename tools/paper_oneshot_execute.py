#!/usr/bin/env python3
"""One-shot paper execute for live proof (no APScheduler).

  AIEM_PAPER_ONESHOT=1 DATABASE_URL=... python3 tools/paper_oneshot_execute.py
"""
from __future__ import annotations

import os
import sys

os.environ["AIEM_PAPER_ONESHOT"] = "1"
# Avoid accidental production side-effects from other optional services
os.environ.setdefault("SKIP_TELEGRAM", "1")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
API = os.path.join(ROOT, "artifacts", "stock-scanner-api")
sys.path.insert(0, API)

def main():
    if not os.environ.get("DATABASE_URL"):
        print("FAIL: DATABASE_URL required")
        sys.exit(2)
    print("[oneshot] importing main (scheduler suppressed)…", flush=True)
    import main as m  # noqa: E402
    print("[oneshot] calling _aiem_paper_execute_today(trigger_source=admin_oneshot)…", flush=True)
    m._aiem_paper_execute_today(trigger_source="admin_oneshot")
    print("[oneshot] execute returned", flush=True)

    import psycopg2
    with psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=15) as c, c.cursor() as cur:
        cur.execute(
            "SELECT signal_source, COUNT(*) FROM aiem_paper_trades "
            "WHERE trade_date = CURRENT_DATE GROUP BY signal_source ORDER BY 2 DESC"
        )
        print("### PAPER_TODAY_BY_SOURCE")
        rows = cur.fetchall()
        if not rows:
            print("ROWS: (empty)")
        for r in rows:
            print(r)
        cur.execute(
            "SELECT business_date, status, trigger_source, picks_count, error_text, "
            "recovery_attempts, execution_id "
            "FROM paper_trade_job_ledger WHERE business_date = CURRENT_DATE"
        )
        print("### LEDGER_TODAY")
        for r in cur.fetchall():
            print(r)
        cur.execute(
            "SELECT id, status, trades_inserted, error_msg, trigger_source "
            "FROM aiem_paper_execution_log ORDER BY id DESC LIMIT 5"
        )
        print("### EXEC_LOG_TAIL")
        for r in cur.fetchall():
            print(r)


if __name__ == "__main__":
    main()
