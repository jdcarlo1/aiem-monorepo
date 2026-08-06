"""
AIEM Sales Readiness aggregator.

Surfaces buyer-facing proof for:
  1) Reliability (morning loop / OE / paper marks)
  2) Honest P&L (mark quality + performance honesty)
  3) Live path status (hard-blocked by simulation_lock)
  4) Commercial layer (roles model, API surface, docs, demo, diligence)

This does NOT enable live brokerage. Live remains fail-closed.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Optional


RELIABILITY_JOBS = {
    "morning_loop": ["aiem_morning_scan", "aiem_morning_scan_watchdog"],
    "paper_marks": ["aiem_paper_refresh_marks", "aiem_paper_execute"],
    "options_engine": [
        "options_pipeline_scheduler",
        "gex_options_alert",
        "gex_options_alert_0935",
        "gex_options_alert_0950",
    ],
}

API_SURFACE = [
    {"method": "GET", "path": "/stock-api/aiem-predictions", "sku": "AIEM", "auth": "public/read"},
    {"method": "GET", "path": "/stock-api/aiem-paper-portfolio", "sku": "AIEM", "auth": "public/read"},
    {"method": "GET", "path": "/stock-api/paper-performance", "sku": "AIEM", "auth": "token"},
    {"method": "GET", "path": "/stock-api/morning-brief", "sku": "AIEM", "auth": "public/read"},
    {"method": "GET", "path": "/stock-api/aiem-sales-readiness", "sku": "AIEM", "auth": "token"},
    {"method": "GET", "path": "/stock-api/aiem-broker/status", "sku": "AIEM", "auth": "token"},
    {"method": "POST", "path": "/stock-api/aiem-broker/paper-order", "sku": "AIEM", "auth": "admin",
     "note": "Paper adapter smoke test only"},
    {"method": "GET", "path": "/stock-api/admin/job-heartbeats", "sku": "AIEM", "auth": "admin"},
    {"method": "GET", "path": "/stock-api/admin/trade-records", "sku": "OE", "auth": "admin",
     "note": "OE SKU — not bundled into AIEM-only sale"},
]

ROLES_MODEL = [
    {"role": "Viewer", "permissions": ["read dashboards", "read paper book", "read reliability"],
     "status": "documented", "enforced": False},
    {"role": "Trader", "permissions": ["Viewer+", "force paper execute/MTM (admin-gated today)"],
     "status": "documented", "enforced": False},
    {"role": "Auditor", "permissions": ["Viewer+", "evidence chain", "diligence export"],
     "status": "documented", "enforced": False},
    {"role": "Admin", "permissions": ["full token access", "scheduler", "kill switches"],
     "status": "partial", "enforced": True, "note": "single ADMIN_TOKEN today"},
]


def _iso(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _connect(db_url: str):
    import psycopg2
    return psycopg2.connect(db_url, connect_timeout=5)


def _job_map(db_url: str) -> dict:
    out = {}
    try:
        with _connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT job_name, last_success, last_attempt, consecutive_failures, last_error
                FROM job_heartbeats
            """)
            for name, ok, attempt, fails, err in cur.fetchall():
                out[str(name)] = {
                    "job_name": str(name),
                    "last_success": _iso(ok),
                    "last_attempt": _iso(attempt),
                    "consecutive_failures": int(fails or 0),
                    "last_error": (err or "")[:400] if err else None,
                    "green": int(fails or 0) == 0 and ok is not None,
                }
    except Exception as e:
        return {"_error": str(e)}
    return out


def _prediction_streak(db_url: str, lookback_days: int = 10) -> dict:
    """Count recent trading days with non-empty aiem_predictions."""
    try:
        with _connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT prediction_date::text, COUNT(*)
                FROM aiem_predictions
                WHERE prediction_date >= CURRENT_DATE - (%s * INTERVAL '1 day')
                GROUP BY prediction_date
                ORDER BY prediction_date DESC
            """, (lookback_days,))
            rows = cur.fetchall() or []
        days = [{"date": r[0], "count": int(r[1]), "green": int(r[1]) > 0} for r in rows]
        streak = 0
        for d in days:
            if d["green"]:
                streak += 1
            else:
                break
        today_n = next((d["count"] for d in days if d["date"] == dt.date.today().isoformat()), 0)
        return {
            "lookback_days": lookback_days,
            "days_with_predictions": days,
            "consecutive_green_from_latest": streak,
            "today_count": today_n,
            "today_green": today_n > 0,
            "target_consecutive_days": 5,
            "meets_sale_bar": streak >= 5,
        }
    except Exception as e:
        return {"error": str(e), "meets_sale_bar": False, "consecutive_green_from_latest": 0}


def _paper_honesty(db_url: str) -> dict:
    try:
        with _connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                  COUNT(*) FILTER (WHERE status='OPEN') AS open_n,
                  COUNT(*) FILTER (WHERE status='OPEN' AND last_price IS NULL) AS open_null_marks,
                  COUNT(*) FILTER (WHERE status='OPEN' AND pnl IS NULL) AS open_null_pnl,
                  COUNT(*) FILTER (WHERE status='OPEN' AND trade_type IN ('CALL_OPTION','PUT_OPTION')) AS open_options,
                  COUNT(*) FILTER (
                    WHERE status='OPEN' AND trade_type IN ('CALL_OPTION','PUT_OPTION')
                      AND option_entry_mid IS NOT NULL
                  ) AS open_options_with_entry_mid,
                  COUNT(*) FILTER (WHERE status!='OPEN') AS closed_n,
                  COALESCE(SUM(pnl) FILTER (WHERE status!='OPEN'),0) AS closed_pnl,
                  COUNT(*) FILTER (
                    WHERE status!='OPEN' AND trade_type IN ('CALL_OPTION','PUT_OPTION')
                  ) AS closed_options
            """)
            r = cur.fetchone()
            open_n = int(r[0] or 0)
            null_marks = int(r[1] or 0)
            null_pnl = int(r[2] or 0)
            open_opts = int(r[3] or 0)
            opt_entry = int(r[4] or 0)
            closed_n = int(r[5] or 0)
            closed_pnl = float(r[6] or 0)
            closed_opts = int(r[7] or 0)
        marks_ok = open_n == 0 or (null_marks == 0 and null_pnl == 0)
        option_honesty = {
            "open_options": open_opts,
            "with_option_entry_mid": opt_entry,
            "real_option_mtm_ready_pct": round(100.0 * opt_entry / open_opts, 1) if open_opts else None,
            "note": (
                "CALL/PUT P&L uses live option mid when option_entry_mid exists; "
                "otherwise labeled synthetic 2x underlying proxy."
            ),
        }
        return {
            "open_positions": open_n,
            "open_null_marks": null_marks,
            "open_null_pnl": null_pnl,
            "marks_green": marks_ok,
            "closed_trades": closed_n,
            "closed_realized_pnl": closed_pnl,
            "closed_option_trades": closed_opts,
            "option_honesty": option_honesty,
            "buyer_trust_ready": marks_ok and (open_opts == 0 or opt_entry >= max(1, open_opts // 2)),
        }
    except Exception as e:
        return {"error": str(e), "marks_green": False, "buyer_trust_ready": False}


def _live_path_status() -> dict:
    live_enabled = os.environ.get("LIVE_TRADING_ENABLED", "") == "true"
    phrase_set = bool(os.environ.get("LIVE_TRADING_CONFIRMATION_PHRASE"))
    try:
        import simulation_lock as sl
        armed = bool(sl.is_live_trading_enabled())
        mode = "LIVE_ARMED" if armed else "PAPER_ONLY"
    except Exception:
        armed = False
        mode = "PAPER_ONLY"

    broker = {}
    try:
        from aiem_broker import broker_readiness_report
        broker = broker_readiness_report()
    except Exception as e:
        broker = {"error": str(e)}

    return {
        "mode": mode,
        "live_trading_enabled_env": live_enabled,
        "confirmation_phrase_set": phrase_set,
        "dual_lock_armed": armed,
        "broker_adapter": {
            "status": (broker.get("active_status") or {}).get("connected") and "connected" or "hookup_ready_stubs",
            "active_provider": broker.get("active_provider"),
            "active_status": broker.get("active_status"),
            "providers": broker.get("providers"),
            "providers_supported_as_stubs": ["tradier", "alpaca", "ibkr"],
            "order_routing": "paper_simulator_default_stubs_not_wired",
            "how_to_hookup_later": broker.get("how_to_hookup_later"),
            "live_gate": broker.get("live_gate"),
            "note": (
                "Broker adapter layer is ready to hook up later. "
                "Default provider=paper. Stubs never send live orders."
            ),
        },
        "can_place_live_orders": False,
        "sale_positioning": "research_paper_terminal",
    }


def _commercial_layer() -> dict:
    return {
        "roles": ROLES_MODEL,
        "auth_today": {
            "mechanism": "ADMIN_TOKEN / session login",
            "mfa": False,
            "sso": False,
            "rbac_enforced": False,
            "note": "Roles are documented for commercial packaging; enforcement is Admin-token only today.",
        },
        "api_surface": API_SURFACE,
        "docs": [
            {"name": "Due Diligence Pack", "path": "docs/aiem-sales/due-diligence-pack.md"},
            {"name": "Demo Script", "path": "docs/aiem-sales/demo-script.md"},
            {"name": "API Surface", "path": "docs/aiem-sales/api-surface.md"},
            {"name": "Roles Model", "path": "docs/aiem-sales/roles-model.md"},
            {"name": "Live Path Policy", "path": "docs/aiem-sales/live-path-policy.md"},
        ],
        "sku_separation": {
            "aiem_terminal": "equity / autonomous research desk",
            "oe_terminal": "sold separately — not bundled by default",
            "bundle": "optional premium later",
            "auth": "same password/token for both terminals (Phase 0); books separated in UI",
            "phase0": "cross-book UI bleed removed — see docs/aiem-sales/phase0-product-honesty.md",
        },
    }


def _score_reliability(jobs: dict, preds: dict, paper: dict) -> dict:
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "green": bool(ok), "detail": detail})

    ms = jobs.get("aiem_morning_scan") or {}
    add("morning_loop_heartbeat", ms.get("green"), ms.get("last_error") or ms.get("last_success") or "missing heartbeat")
    add("morning_predictions_today", preds.get("today_green"), f"today_count={preds.get('today_count', 0)}")
    add("morning_streak", preds.get("consecutive_green_from_latest", 0) >= 3,
        f"streak={preds.get('consecutive_green_from_latest', 0)} (sale bar=5)")
    add("paper_marks", paper.get("marks_green"),
        f"null_marks={paper.get('open_null_marks')} null_pnl={paper.get('open_null_pnl')}")

    oe_any = False
    oe_green = False
    for jn in RELIABILITY_JOBS["options_engine"]:
        j = jobs.get(jn)
        if j:
            oe_any = True
            if j.get("green"):
                oe_green = True
                break
    add("options_engine_heartbeat", oe_green if oe_any else False,
        "OE sold separately — shown for platform health only" if oe_any else "no OE heartbeats in this deploy")

    green_n = sum(1 for c in checks if c["green"])
    return {
        "checks": checks,
        "green_count": green_n,
        "total_checks": len(checks),
        "overall_green": green_n == len(checks),
        "sale_ready_reliability": bool(preds.get("meets_sale_bar")) and bool(paper.get("marks_green")),
    }


def build_sales_readiness(db_url: Optional[str] = None) -> dict:
    db_url = db_url or os.environ.get("DATABASE_URL") or ""
    jobs = _job_map(db_url) if db_url else {"_error": "DATABASE_URL missing"}
    preds = _prediction_streak(db_url) if db_url else {"error": "DATABASE_URL missing", "meets_sale_bar": False}
    paper = _paper_honesty(db_url) if db_url else {"error": "DATABASE_URL missing", "marks_green": False}
    live = _live_path_status()
    commercial = _commercial_layer()
    reliability = _score_reliability(jobs if "_error" not in jobs else {}, preds, paper)

    # Buyer checklist scores (0-100 per pillar)
    pillars = {
        "reliability_proof": 100 if reliability.get("sale_ready_reliability") else (
            60 if reliability.get("green_count", 0) >= 3 else 25
        ),
        "honest_pnl": 100 if paper.get("buyer_trust_ready") else (55 if paper.get("marks_green") else 20),
        "live_path": 65,  # adapter/stubs ready; live orders still hard-blocked (correct for research SKU)
        "commercial_layer": 70,  # docs+roles+API present; RBAC not enforced
    }
    overall = round(sum(pillars.values()) / len(pillars))

    return {
        "ok": True,
        "sku": "AIEM_TERMINAL",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "overall_score": overall,
        "pillars": pillars,
        "reliability": {
            **reliability,
            "jobs": {k: v for k, v in (jobs or {}).items() if not str(k).startswith("_")},
            "prediction_streak": preds,
            "jobs_error": jobs.get("_error") if isinstance(jobs, dict) else None,
        },
        "honest_pnl": paper,
        "live_path": live,
        "commercial": commercial,
        "buyer_summary": {
            "positioning": "Institutional-style paper research terminal with real quant engines",
            "not_included": [
                "Live brokerage order routing (optional future)",
                "Options Engine Terminal (sold separately)",
                "Multi-tenant SaaS / SSO (roadmap)",
            ],
            "must_publish_note": (
                "Reliability streak only advances after production Publish. "
                "Git fixes do not count until redeployed."
            ),
        },
    }
