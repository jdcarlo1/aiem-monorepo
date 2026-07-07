# ============================================================
# AEIM STRICT PERFORMANCE AUDITOR — PART 1 OF 2
# File: aiem_performance_auditor.py
# ============================================================

import os
import json
import hashlib
import datetime
import psycopg2
from psycopg2.extras import RealDictCursor


_AEIM_MKT_REQUIRED_ORDER = [
    "mkt_load_discoveries",
    "mkt_explore_dimensions",
    "mkt_generate_hypotheses",
    "mkt_factor_correlations",
    "mkt_test_signal",
    "mkt_test_inverse",
    "mkt_analyze_top_movers",
    "mkt_analyze_false_signals",
    "mkt_volume_patterns",
    "mkt_price_patterns",
    "mkt_compute_momentum",
    "mkt_find_thresholds",
    "mkt_discover_interactions",
    "mkt_regime_filter",
    "mkt_compare_signals",
    "mkt_invent_indicator",
    "mkt_validate_oos",
    "mkt_save_discovery",
    "mkt_signal_drift",
    "mkt_build_composite",
]


_AEIM_CORE_REQUIRED_ORDER = [
    "evaluate_previous_model",
    "rollback_to_previous_model",
    "register_hypotheses",
    "query_pick_outcomes",
    "query_missed_movers",
    "analyze_signal_correlation",
    "multivariate_regression",
    "discover_numeric_patterns",
    "compare_picks_vs_misses",
    "query_market_regime",
    "query_cross_signal_overlap",
    "query_temporal_patterns",
    "query_rank_effectiveness",
    "query_exit_timing",
    "search_past_findings",
    "run_statistical_significance",
    "test_scoring_hypothesis",
    "save_research_model",
    "query_own_prediction_performance",
    "list_signal_dimensions",
    "test_new_signal",
]


def _aeim_db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _safe_json(obj):
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return json.dumps({"unserializable": str(obj)[:2000]})


def _aeim_auditor_init_tables():
    """
    Creates all strict verification tables.
    These tables prove what AEIM did, in what order, and whether it passed.
    """
    with _aeim_db() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_research_audit_sessions (
                id SERIAL PRIMARY KEY,
                session_id TEXT UNIQUE NOT NULL,
                session_type TEXT NOT NULL,
                started_at TIMESTAMP DEFAULT NOW(),
                ended_at TIMESTAMP,
                total_tool_calls INTEGER DEFAULT 0,
                model_saved BOOLEAN DEFAULT FALSE,
                discovery_saved BOOLEAN DEFAULT FALSE,
                strict_pass BOOLEAN DEFAULT FALSE,
                verdict TEXT,
                violations JSONB DEFAULT '[]'::jsonb
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_research_tool_audit (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json JSONB DEFAULT '{}'::jsonb,
                result_json JSONB DEFAULT '{}'::jsonb,
                result_status TEXT,
                llm_loop_used BOOLEAN DEFAULT TRUE,
                aeim_tool_executed BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_discovery_gate_audit (
                id SERIAL PRIMARY KEY,
                session_id TEXT,
                discovery_id INTEGER,
                hypothesis_text TEXT,
                conditions_json JSONB,
                p_value FLOAT,
                signal_n INTEGER,
                signal_win_rate FLOAT,
                baseline_win_rate FLOAT,
                edge_broad FLOAT,
                edge_tight FLOAT,
                oos_edge FLOAT,
                inverse_test_seen BOOLEAN DEFAULT FALSE,
                oos_test_seen BOOLEAN DEFAULT FALSE,
                save_seen BOOLEAN DEFAULT FALSE,
                verified BOOLEAN DEFAULT FALSE,
                verdict TEXT,
                violations JSONB DEFAULT '[]'::jsonb,
                audit_hash TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_daily_performance_audit (
                id SERIAL PRIMARY KEY,
                audit_date DATE DEFAULT CURRENT_DATE,
                total_discoveries INTEGER DEFAULT 0,
                verified_discoveries INTEGER DEFAULT 0,
                rejected_discoveries INTEGER DEFAULT 0,
                total_predictions INTEGER DEFAULT 0,
                graded_predictions INTEGER DEFAULT 0,
                t3_wins INTEGER DEFAULT 0,
                t3_losses INTEGER DEFAULT 0,
                t3_win_rate FLOAT,
                verdict TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS aiem_trade_outcome_audit (
                id SERIAL PRIMARY KEY,
                ticker TEXT,
                signal_date DATE,
                discovery_id INTEGER,
                entry_price FLOAT,
                exit_price FLOAT,
                return_pct FLOAT,
                win BOOLEAN,
                aeim_verified BOOLEAN DEFAULT FALSE,
                reason_json JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

    print("[AEIM AUDITOR] strict verification tables initialized")


def _aeim_start_audit_session(session_type="research"):
    """
    Starts a new auditable AEIM session.
    Call this at the beginning of _run_aiem_research_agent().
    """
    _aeim_auditor_init_tables()

    session_id = "AEIM-{}-{}".format(
        session_type.upper(),
        datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    )

    with _aeim_db() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO aiem_research_audit_sessions
            (session_id, session_type)
            VALUES (%s, %s)
        """, [session_id, session_type])

    print("[AEIM AUDITOR] session started:", session_id)
    return session_id


def _aiem_log_tool_call(session_id, sequence_number, tool_name, arguments, result):
    """
    Logs every AEIM tool call.
    This proves AEIM actually executed the tool instead of pretending.
    """
    status = None

    if isinstance(result, dict):
        status = result.get("status") or result.get("verdict") or result.get("error")

    with _aeim_db() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO aiem_research_tool_audit
            (session_id, sequence_number, tool_name, arguments_json, result_json, result_status)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, [
            session_id,
            sequence_number,
            tool_name,
            _safe_json(arguments or {}),
            _safe_json(result or {}),
            str(status)[:250] if status else None,
        ])

    return True


def _aiem_verify_tool_order(session_id):
    """
    Strictly verifies AEIM followed the required workflow order.
    """
    violations = []

    with _aeim_db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT sequence_number, tool_name
            FROM aiem_research_tool_audit
            WHERE session_id = %s
            ORDER BY sequence_number ASC, id ASC
        """, [session_id])
        rows = cur.fetchall()

    tools_seen = [r["tool_name"] for r in rows]

    mkt_seen = [t for t in tools_seen if t in _AEIM_MKT_REQUIRED_ORDER]
    core_seen = [t for t in tools_seen if t in _AEIM_CORE_REQUIRED_ORDER]

    def check_order(label, seen, required):
        local = []
        positions = {name: i for i, name in enumerate(required)}
        seq = [positions[t] for t in seen if t in positions]

        if seq != sorted(seq):
            local.append("{} workflow order violation".format(label))

        return local

    violations.extend(check_order("MKT", mkt_seen, _AEIM_MKT_REQUIRED_ORDER))
    violations.extend(check_order("CORE", core_seen, _AEIM_CORE_REQUIRED_ORDER))

    if "mkt_save_discovery" in tools_seen:
        if "mkt_test_signal" not in tools_seen:
            violations.append("Discovery save blocked: mkt_test_signal was not run.")
        if "mkt_test_inverse" not in tools_seen:
            violations.append("Discovery save blocked: mkt_test_inverse was not run.")
        if "mkt_validate_oos" not in tools_seen:
            violations.append("Discovery save blocked: mkt_validate_oos was not run.")

    if "save_research_model" in tools_seen:
        if "run_statistical_significance" not in tools_seen:
            violations.append("Model save blocked: run_statistical_significance was not run.")
        if "test_scoring_hypothesis" not in tools_seen:
            violations.append("Model save blocked: test_scoring_hypothesis was not run.")

    return {
        "session_id": session_id,
        "tools_seen": tools_seen,
        "strict_order_pass": len(violations) == 0,
        "violations": violations,
    }


def _aiem_verify_saved_discoveries(session_id=None):
    """
    Strict discovery gate.

    A discovery FAILS if:
    - status is not validated
    - p_value is missing or >= 0.05
    - signal_n is too small
    - oos_edge is missing
    - inverse test was not seen
    - OOS validation was not seen
    - save discovery was not seen
    """
    violations_total = []
    verified_count = 0
    rejected_count = 0

    with _aeim_db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, hypothesis_text, conditions_json, p_value, signal_n,
                   signal_win_rate, baseline_win_rate, edge_broad, edge_tight,
                   oos_edge, status
            FROM aiem_signal_discoveries
            ORDER BY id DESC
            LIMIT 100
        """)
        discoveries = cur.fetchall()

    tools_seen = []

    if session_id:
        with _aeim_db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT tool_name
                FROM aiem_research_tool_audit
                WHERE session_id = %s
            """, [session_id])
            tools_seen = [r["tool_name"] for r in cur.fetchall()]

    inverse_seen = "mkt_test_inverse" in tools_seen if session_id else True
    oos_seen = "mkt_validate_oos" in tools_seen if session_id else True
    save_seen = "mkt_save_discovery" in tools_seen if session_id else True

    for d in discoveries:
        v = []

        p_value = d.get("p_value")
        signal_n = d.get("signal_n")
        oos_edge = d.get("oos_edge")
        status = d.get("status")

        if status != "validated":
            v.append("status is not validated")

        if p_value is None or float(p_value) >= 0.05:
            v.append("p_value missing or >= 0.05")

        if signal_n is None or int(signal_n) < 50:
            v.append("signal_n missing or too small")

        if oos_edge is None:
            v.append("oos_edge missing")

        if not inverse_seen:
            v.append("inverse test not seen in session")

        if not oos_seen:
            v.append("OOS validation not seen in session")

        if not save_seen:
            v.append("save discovery tool not seen in session")

        verified = len(v) == 0

        if verified:
            verified_count += 1
            verdict = "PASSED — discovery is AEIM verified"
        else:
            rejected_count += 1
            verdict = "FAILED — discovery not deployment safe"
            violations_total.extend(v)

        raw_hash = hashlib.sha256(_safe_json({
            "discovery_id": d["id"],
            "session_id": session_id,
            "verified": verified,
            "violations": v,
            "checked_at": str(datetime.datetime.utcnow())
        }).encode()).hexdigest()

        with _aeim_db() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_discovery_gate_audit
                (session_id, discovery_id, hypothesis_text, conditions_json,
                 p_value, signal_n, signal_win_rate, baseline_win_rate,
                 edge_broad, edge_tight, oos_edge, inverse_test_seen,
                 oos_test_seen, save_seen, verified, verdict, violations, audit_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, [
                session_id,
                d["id"],
                d.get("hypothesis_text"),
                _safe_json(d.get("conditions_json") or {}),
                p_value,
                signal_n,
                d.get("signal_win_rate"),
                d.get("baseline_win_rate"),
                d.get("edge_broad"),
                d.get("edge_tight"),
                oos_edge,
                inverse_seen,
                oos_seen,
                save_seen,
                verified,
                verdict,
                _safe_json(v),
                raw_hash,
            ])

    return {
        "checked": len(discoveries),
        "verified": verified_count,
        "rejected": rejected_count,
        "violations": sorted(list(set(violations_total))),
    }


def _aiem_close_audit_session(session_id, model_saved=False, discovery_saved=False):
    """
    Closes the AIEM audit session and produces final strict PASS / FAIL.
    """
    order_check = _aiem_verify_tool_order(session_id)
    discovery_check = _aiem_verify_saved_discoveries(session_id)

    violations = []
    violations.extend(order_check["violations"])
    violations.extend(discovery_check["violations"])

    strict_pass = len(violations) == 0

    verdict = (
        "STRICT PASS — AIEM research session followed required verification."
        if strict_pass else
        "STRICT FAIL — AIEM research session has verification violations."
    )

    with _aeim_db() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE aiem_research_audit_sessions
            SET ended_at = NOW(),
                total_tool_calls = (
                    SELECT COUNT(*)
                    FROM aiem_research_tool_audit
                    WHERE session_id = %s
                ),
                model_saved = %s,
                discovery_saved = %s,
                strict_pass = %s,
                verdict = %s,
                violations = %s
            WHERE session_id = %s
        """, [
            session_id,
            model_saved,
            discovery_saved,
            strict_pass,
            verdict,
            _safe_json(violations),
            session_id,
        ])

    print("[AIEM AUDITOR]", verdict)

    if violations:
        print("[AIEM AUDITOR] violations:", violations)

    return {
        "session_id": session_id,
        "strict_pass": strict_pass,
        "verdict": verdict,
        "order_check": order_check,
        "discovery_check": discovery_check,
        "violations": violations,
    }


def _aiem_daily_performance_audit():
    """
    Daily performance auditor.

    It checks your existing ai_early_movers_log table if present.
    This gives AIEM proof of real-world prediction performance.
    """
    total_predictions = 0
    graded_predictions = 0
    t3_wins = 0
    t3_losses = 0
    win_rate = None

    try:
        with _aeim_db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT COUNT(*) AS n
                FROM ai_early_movers_log
            """)
            total_predictions = cur.fetchone()["n"]

            cur.execute("""
                SELECT
                    COUNT(*) AS graded,
                    COUNT(*) FILTER (WHERE t3_win = TRUE) AS wins,
                    COUNT(*) FILTER (WHERE t3_win = FALSE) AS losses
                FROM ai_early_movers_log
                WHERE t3_win IS NOT NULL
            """)
            row = cur.fetchone()

            graded_predictions = row["graded"] or 0
            t3_wins = row["wins"] or 0
            t3_losses = row["losses"] or 0

            if graded_predictions:
                win_rate = round((t3_wins / graded_predictions) * 100, 2)

    except Exception as e:
        print("[AIEM AUDITOR] performance audit warning:", e)

    with _aeim_db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM aiem_signal_discoveries")
        total_discoveries = cur.fetchone()["n"]

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE verified = TRUE) AS verified,
                COUNT(*) FILTER (WHERE verified = FALSE) AS rejected
            FROM aiem_discovery_gate_audit
        """)
        row = cur.fetchone()

        verified_discoveries = row["verified"] or 0
        rejected_discoveries = row["rejected"] or 0

    verdict = "OK — AIEM daily performance audit completed"

    if rejected_discoveries > verified_discoveries:
        verdict = "WARNING — more rejected discoveries than verified discoveries"

    if graded_predictions >= 20 and win_rate is not None and win_rate < 50:
        verdict = "WARNING — AIEM recent graded win rate below 50%"

    with _aeim_db() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO aiem_daily_performance_audit
            (total_discoveries, verified_discoveries, rejected_discoveries,
             total_predictions, graded_predictions, t3_wins, t3_losses,
             t3_win_rate, verdict)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, [
            total_discoveries,
            verified_discoveries,
            rejected_discoveries,
            total_predictions,
            graded_predictions,
            t3_wins,
            t3_losses,
            win_rate,
            verdict,
        ])

    return {
        "total_discoveries": total_discoveries,
        "verified_discoveries": verified_discoveries,
        "rejected_discoveries": rejected_discoveries,
        "total_predictions": total_predictions,
        "graded_predictions": graded_predictions,
        "t3_wins": t3_wins,
        "t3_losses": t3_losses,
        "t3_win_rate": win_rate,
        "verdict": verdict,
    }


def _aiem_record_trade_outcome(
    ticker,
    signal_date,
    discovery_id,
    entry_price,
    exit_price,
    reason_json=None,
):
    """
    Records actual trade outcome.

    This is how AIEM learns from wins/losses instead of only saving theories.
    """
    return_pct = None
    win = None
    aiem_verified = False

    if entry_price and exit_price:
        return_pct = round(((exit_price / entry_price) - 1) * 100, 4)
        win = return_pct > 0

    verification = _aiem_verify_saved_discoveries()
    aiem_verified = verification.get("verified", 0) > 0

    with _aeim_db() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO aiem_trade_outcome_audit
            (ticker, signal_date, discovery_id, entry_price, exit_price,
             return_pct, win, aeim_verified, reason_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, [
            ticker,
            signal_date,
            discovery_id,
            entry_price,
            exit_price,
            return_pct,
            win,
            aiem_verified,
            _safe_json(reason_json or {}),
        ])

        audit_id = cur.fetchone()[0]

    return {
        "audit_id": audit_id,
        "ticker": ticker,
        "signal_date": str(signal_date),
        "discovery_id": discovery_id,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "return_pct": return_pct,
        "win": win,
        "aiem_verified": aiem_verified,
    }


def _aiem_generate_final_audit_report(limit=10):
    """
    Produces final strict verification report.
    Use this in admin dashboard or API.
    """
    with _aeim_db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT *
            FROM aiem_research_audit_sessions
            ORDER BY id DESC
            LIMIT %s
        """, [limit])
        sessions = cur.fetchall()

        cur.execute("""
            SELECT *
            FROM aiem_discovery_gate_audit
            ORDER BY id DESC
            LIMIT %s
        """, [limit * 3])
        discoveries = cur.fetchall()

        cur.execute("""
            SELECT *
            FROM aiem_daily_performance_audit
            ORDER BY id DESC
            LIMIT %s
        """, [limit])
        performance = cur.fetchall()

        cur.execute("""
            SELECT *
            FROM aiem_trade_outcome_audit
            ORDER BY id DESC
            LIMIT %s
        """, [limit * 3])
        trades = cur.fetchall()

    return {
        "status": "ok",
        "message": "AIEM strict verification report generated",
        "latest_sessions": [dict(x) for x in sessions],
        "latest_discovery_audits": [dict(x) for x in discoveries],
        "latest_daily_performance": [dict(x) for x in performance],
        "latest_trade_outcomes": [dict(x) for x in trades],
    }


def _install_aiem_auditor_routes(app):
    """
    Flask admin routes for the strict performance auditor.
    Call this once after app = Flask(__name__).
    """
    from flask import Response as _FlaskResponse

    def _json_resp(obj):
        return _FlaskResponse(_safe_json(obj), mimetype="application/json")

    @app.route("/stock-api/admin/aiem-auditor/init", methods=["POST", "GET"])
    def aiem_auditor_init_route():
        _aeim_auditor_init_tables()
        return _json_resp({"status": "ok", "message": "AIEM strict auditor tables initialized"})

    @app.route("/stock-api/admin/aiem-auditor/daily", methods=["POST", "GET"])
    def aiem_auditor_daily_route():
        return _json_resp(_aiem_daily_performance_audit())

    @app.route("/stock-api/admin/aiem-auditor/report", methods=["GET"])
    def aiem_auditor_report_route():
        return _json_resp(_aiem_generate_final_audit_report())

    @app.route("/stock-api/admin/aiem-auditor/latest", methods=["GET"])
    def aiem_auditor_latest_route():
        return _json_resp(_aiem_generate_final_audit_report())


def _aiem_auditor_startup_check():
    """
    Safe startup check — initializes tables and logs a brief summary.
    Called from _DEFERRED_INITS during app startup.
    """
    try:
        _aeim_auditor_init_tables()
        report = _aiem_generate_final_audit_report(limit=3)
        print("[AIEM AUDITOR] startup check complete")
        return report
    except Exception as e:
        print("[AIEM AUDITOR] startup check failed:", e)
        return {
            "status": "error",
            "error": str(e)
        }


# ============================================================
# END OF AIEM STRICT PERFORMANCE AUDITOR
# ============================================================
