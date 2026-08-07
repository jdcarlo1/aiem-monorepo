#!/usr/bin/env python3
"""
Dry-run: call persist_asym_paper_open/close for each of the 3 asym strategies.
Proves INSERT/UPDATE execute (RETURNING id + SELECT after).

Usage:
  DATABASE_URL=postgresql://... python3 dry_run_asym_paper_persist.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Ensure stock-scanner-api import path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dry_run_asym")


def main() -> int:
    dsn = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("NEON_DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or ""
    )
    print("===== DRY_RUN_ENV =====")
    print(f"DATABASE_URL_PRESENT={bool(dsn)}")
    if not dsn:
        print("BLOCKED: no DATABASE_URL — cannot fire real INSERT")
        return 2

    import psycopg2
    from aim_asym_paper_strategies import (
        persist_asym_paper_open,
        persist_asym_paper_close,
    )

    strategies = ("put_butterfly", "call_butterfly", "put_ladder")
    sample_legs = [
        {"qty": 1, "right": "put", "strike": 505.0, "premium": 1.25, "symbol": "O:SPYDRY"},
        {"qty": -2, "right": "put", "strike": 500.0, "premium": 0.80, "symbol": "O:SPYDRY2"},
        {"qty": 1, "right": "put", "strike": 495.0, "premium": 0.45, "symbol": "O:SPYDRY3"},
    ]

    print("===== DRY_RUN_OPEN_CLOSE =====")
    ids = {}
    for i, strat in enumerate(strategies):
        tp = {0: 200.0, 1: 100.0, 2: 150.0}[i]
        entry_debit = 120.0 + 10.0 * i
        print(f"--- OPEN {strat} ---")
        oid = persist_asym_paper_open(
            strategy=strat,
            underlying="SPY",
            entry_debit_usd=entry_debit,
            packages=1,
            expiration="2026-08-28",
            legs=sample_legs,
            entry_premium_ps=entry_debit / 100.0,
            take_profit_pct=tp,
        )
        print(f"RETURNING_ID strategy={strat} id={oid!r}")
        ids[strat] = oid
        if oid is None:
            print(f"FAIL: persist_asym_paper_open returned None for {strat}")
            continue

        exit_val = entry_debit * (1.0 + tp / 100.0)  # synthetic TP hit
        pnl = exit_val - entry_debit
        print(f"--- CLOSE {strat} id={oid} pnl={pnl:.2f} ---")
        persist_asym_paper_close(
            paper_trade_id=oid,
            strategy=strat,
            exit_value_usd=exit_val,
            pnl_usd=pnl,
            reason=f"DRY_RUN_TP_{int(tp)}PCT",
        )
        print(f"CLOSE_CALLED strategy={strat} id={oid}")

    print("===== SELECT AFTER DRY RUN =====")
    with psycopg2.connect(dsn, connect_timeout=8) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, strategy, ticker, status, trade_type, notional,
                   entry_price, exit_price, pnl, exit_reason, created_at, updated_at
            FROM aiem_paper_trades
            WHERE strategy IN ('put_butterfly','call_butterfly','put_ladder')
            ORDER BY strategy, id
            """
        )
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print("COLUMNS", cols)
        print(f"ROW_COUNT={len(rows)}")
        for r in rows:
            print("ROW", dict(zip(cols, r)))

        cur.execute(
            """
            SELECT strategy, COUNT(*), MIN(created_at), MAX(created_at)
            FROM aiem_paper_trades
            WHERE strategy IN ('put_butterfly','call_butterfly','put_ladder')
            GROUP BY strategy
            ORDER BY strategy
            """
        )
        print("===== GROUP BY strategy =====")
        for r in cur.fetchall():
            print("GROUP", r)

    missing = [s for s in strategies if ids.get(s) is None]
    if missing:
        print("FAIL_MISSING_IDS", missing)
        return 1
    if len(rows) < 3:
        print("FAIL_ROW_COUNT", len(rows))
        return 1
    print("DRY_RUN_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
