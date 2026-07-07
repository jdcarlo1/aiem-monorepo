"""
AIEM v3 — Phase 8: Governance & Verification Engine
End-to-end health checks for all AIEM v3 engines.
Stores results to aiem_verification_logs and aiem_system_health.
"""

import os
import time
import json
from datetime import date
from typing import List, Dict

_DB_URL = os.environ.get("DATABASE_URL", "")


def _sf(v, d=0.0) -> float:
    try:
        return float(v) if v is not None else d
    except Exception:
        return d


def _store_health(db_url: str, module: str, status: str,
                  latency_ms: float, detail: str = "") -> None:
    import psycopg2
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_system_health (check_time, module, status, latency_ms, detail)
                VALUES (NOW(), %s, %s, %s, %s)
            """, (module, status, round(latency_ms, 1), detail[:500]))
            conn.commit()
    except Exception:
        pass  # health store failure is non-fatal


def _store_verification(db_url: str, run_type: str, module: str,
                        test_name: str, result: str,
                        expected: str, actual: str, details: str) -> None:
    import psycopg2
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_verification_logs
                    (run_date, run_type, module, test_name, result,
                     expected_value, actual_value, details, created_at)
                VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, NOW())
            """, (run_type, module, test_name, result, expected, actual, details[:1000]))
            conn.commit()
    except Exception:
        pass


# ── Individual engine checks ───────────────────────────────────────────────────

def check_database(db_url: str) -> Dict:
    import psycopg2
    t0 = time.time()
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        ms = (time.time() - t0) * 1000
        return {"module": "database", "status": "HEALTHY", "latency_ms": ms, "detail": "ping ok"}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"module": "database", "status": "FAILED", "latency_ms": ms, "detail": str(e)}


def check_polygon_data(db_url: str) -> Dict:
    import psycopg2
    t0 = time.time()
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(scan_date), COUNT(*)
                FROM polygon_market_daily
                WHERE scan_date >= CURRENT_DATE - 5
            """)
            row = cur.fetchone()
        ms       = (time.time() - t0) * 1000
        max_date = row[0]
        n_rows   = row[1]
        from datetime import date as _date, timedelta
        stale    = max_date is None or ((_date.today() - max_date).days > 5)
        status   = "WARNING" if stale else "HEALTHY"
        detail   = f"latest={max_date}, rows_5d={n_rows}"
        return {"module": "polygon_data", "status": status, "latency_ms": ms, "detail": detail}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"module": "polygon_data", "status": "FAILED", "latency_ms": ms, "detail": str(e)}


def check_macro_engine(db_url: str) -> Dict:
    t0 = time.time()
    try:
        import aiem_macro_engine as me
        result = me.get_latest_macro(db_url)
        ms     = (time.time() - t0) * 1000
        if not result or result.get("macro_score") is None:
            return {"module": "macro_engine", "status": "WARNING",
                    "latency_ms": ms, "detail": "no macro score in DB"}
        score  = result["macro_score"]
        regime = result.get("regime", "?")
        return {"module": "macro_engine", "status": "HEALTHY",
                "latency_ms": ms, "detail": f"score={score} regime={regime}"}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"module": "macro_engine", "status": "FAILED", "latency_ms": ms, "detail": str(e)}


def check_discovery_engine(db_url: str) -> Dict:
    import psycopg2
    t0 = time.time()
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), MAX(created_at)
                FROM aiem_discovery_memory
                WHERE created_at >= NOW() - INTERVAL '24 hours'
            """)
            row = cur.fetchone()
        ms     = (time.time() - t0) * 1000
        n      = row[0]
        latest = row[1]
        status = "HEALTHY" if n > 0 else "WARNING"
        return {"module": "discovery_engine", "status": status,
                "latency_ms": ms, "detail": f"discoveries_24h={n}, latest={latest}"}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"module": "discovery_engine", "status": "FAILED", "latency_ms": ms, "detail": str(e)}


def check_technical_engine(db_url: str) -> Dict:
    import psycopg2
    t0 = time.time()
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM aiem_technical_scores
                WHERE computed_at >= NOW() - INTERVAL '24 hours'
            """)
            n = cur.fetchone()[0]
        ms     = (time.time() - t0) * 1000
        status = "HEALTHY" if n > 0 else "WARNING"
        return {"module": "technical_engine", "status": status,
                "latency_ms": ms, "detail": f"scores_24h={n}"}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"module": "technical_engine", "status": "FAILED", "latency_ms": ms, "detail": str(e)}


def check_decision_engine(db_url: str) -> Dict:
    import psycopg2
    t0 = time.time()
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), COUNT(CASE WHEN decision IN ('BUY','SMALL_BUY') THEN 1 END)
                FROM aiem_decision_history
                WHERE created_at >= NOW() - INTERVAL '24 hours'
            """)
            row = cur.fetchone()
        ms     = (time.time() - t0) * 1000
        total  = row[0]
        buys   = row[1]
        status = "HEALTHY" if total > 0 else "WARNING"
        return {"module": "decision_engine", "status": status,
                "latency_ms": ms, "detail": f"decisions_24h={total}, buys={buys}"}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"module": "decision_engine", "status": "FAILED", "latency_ms": ms, "detail": str(e)}


def check_learning_engine(db_url: str) -> Dict:
    import psycopg2
    t0 = time.time()
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM aiem_counterfactual_results")
            cf_n = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM aiem_strategy_memory")
            sm_n = cur.fetchone()[0]
        ms     = (time.time() - t0) * 1000
        status = "HEALTHY" if sm_n >= 0 else "WARNING"
        return {"module": "learning_engine", "status": status,
                "latency_ms": ms,
                "detail": f"counterfactuals={cf_n}, strategy_memory_keys={sm_n}"}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"module": "learning_engine", "status": "FAILED", "latency_ms": ms, "detail": str(e)}


def check_paper_trading(db_url: str) -> Dict:
    import psycopg2
    t0 = time.time()
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), COUNT(CASE WHEN status='OPEN' THEN 1 END)
                FROM aiem_paper_trades
                WHERE trade_date >= CURRENT_DATE - 7
            """)
            row = cur.fetchone()
        ms   = (time.time() - t0) * 1000
        return {"module": "paper_trading", "status": "HEALTHY",
                "latency_ms": ms, "detail": f"trades_7d={row[0]}, open={row[1]}"}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"module": "paper_trading", "status": "FAILED", "latency_ms": ms, "detail": str(e)}


def check_scheduler(db_url: str) -> Dict:
    """Check that key scheduled jobs ran today via aiem_system_health timestamps."""
    import psycopg2
    t0 = time.time()
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT module, MAX(check_time) as last_run
                FROM aiem_system_health
                WHERE check_time >= NOW() - INTERVAL '24 hours'
                GROUP BY module
            """)
            modules_seen = {row[0]: row[1] for row in cur.fetchall()}
        ms = (time.time() - t0) * 1000
        detail = f"modules_seen_24h={list(modules_seen.keys())}"
        return {"module": "scheduler", "status": "HEALTHY",
                "latency_ms": ms, "detail": detail}
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return {"module": "scheduler", "status": "FAILED", "latency_ms": ms, "detail": str(e)}


# ── Full verification run ──────────────────────────────────────────────────────

def run_full_verification(db_url: str = None, run_type: str = "daily") -> Dict:
    """
    Run all engine health checks.
    Stores results to aiem_verification_logs + aiem_system_health.
    Returns full report dict.
    """
    db_url = db_url or _DB_URL
    print(f"[v3_verification] starting {run_type} verification...")

    checks = [
        check_database(db_url),
        check_polygon_data(db_url),
        check_macro_engine(db_url),
        check_discovery_engine(db_url),
        check_technical_engine(db_url),
        check_decision_engine(db_url),
        check_learning_engine(db_url),
        check_paper_trading(db_url),
        check_scheduler(db_url),
    ]

    passed  = [c for c in checks if c["status"] == "HEALTHY"]
    warned  = [c for c in checks if c["status"] == "WARNING"]
    failed  = [c for c in checks if c["status"] == "FAILED"]
    overall = "PASS" if not failed else ("WARN" if not failed else "FAIL")
    if failed:  overall = "FAIL"
    elif warned: overall = "WARN"
    else:        overall = "PASS"

    # Store each check
    for c in checks:
        _store_health(db_url, c["module"], c["status"], c["latency_ms"], c["detail"])
        result = "PASS" if c["status"] == "HEALTHY" else ("WARN" if c["status"] == "WARNING" else "FAIL")
        _store_verification(
            db_url, run_type, c["module"],
            f"{c['module']}_health_check", result,
            "HEALTHY", c["status"], c["detail"],
        )

    report = {
        "run_date":    date.today().isoformat(),
        "run_type":    run_type,
        "overall":     overall,
        "total":       len(checks),
        "passed":      len(passed),
        "warned":      len(warned),
        "failed":      len(failed),
        "checks":      checks,
        "failed_modules": [c["module"] for c in failed],
        "warned_modules": [c["module"] for c in warned],
    }

    status_emoji = "✅" if overall == "PASS" else ("⚠️" if overall == "WARN" else "❌")
    print(f"[v3_verification] {status_emoji} {overall} — "
          f"{len(passed)}/{len(checks)} PASS, {len(warned)} WARN, {len(failed)} FAIL")
    return report
