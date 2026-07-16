"""
aiem_strat_scheduler.py — Standalone Advanced Options Strategy Engine Scheduler

Runs as its own Replit workflow (completely separate from all existing processes).
Uses the proven job-queue/heartbeat/health pattern from aiem_options_scheduler.py
but targets ONLY the ase_* tables — never touches any existing AIEM tables.

Architecture
────────────
  DB table: ase_engine_jobs  (UNIQUE ticker+scan_date+thesis = idempotency)
  State machine: PENDING → CLAIMED → EXECUTING → DONE | FAILED
  Stale recovery:
    CLAIMED  > 5 min  → reset to PENDING
    EXECUTING > 10 min → reset to PENDING
  Max 3 recovery attempts before FAILED.
  Heartbeat: writes to job_heartbeats every 5 min.
  Health endpoint: GET /health → JSON (port 5054).

Schedule (ET, Mon-Fri)
  09:40 — seed daily candidates from polygon_rvol_scan (top 25 stocks)
  09:55 — run full strategy evaluation + paper trading
  16:00 — daily position monitoring pass + close expired positions
  16:15 — generate daily performance report
  18:00 — weekly report (Fridays only)
  22:00 — end-of-month report (last trading day of month)
  00:05 — purge ase_engine_jobs > 30 days old

IMPORTANT: NEVER modifies any table outside ase_* prefix.
"""
import os
import sys
import json
import time
import uuid
import hashlib
import logging
import threading
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

import psycopg2
import psycopg2.extras
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import pytz

# ── Bootstrap — ensure package is importable ─────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from aiem_strat_engine.db import create_schema, get_conn, list_tables
from aiem_strat_engine import config as ase_cfg

# ── Config ───────────────────────────────────────────────────────────────────
_DB_URL          = os.environ["DATABASE_URL"]
_ET              = pytz.timezone("America/New_York")
_HEALTH_PORT     = int(os.environ.get("STRAT_SCHEDULER_PORT", "5054"))
_STALE_CLAIM_SEC = 300     # 5 min
_STALE_EXEC_SEC  = 600     # 10 min
_MAX_RETRIES     = 3
_HEARTBEAT_NAME  = "aiem_strat_scheduler"
_MAX_CANDIDATES  = 25      # top N stocks to evaluate daily

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s %(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
sys.stdout.reconfigure(line_buffering=True)
log = logging.getLogger("aiem_strat_scheduler")

# ── Telegram helper ──────────────────────────────────────────────────────────
def _tg(text: str) -> bool:
    token   = "".join(os.environ.get("TELEGRAM_BOT_TOKEN", "").split())
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        body = json.dumps({"chat_id": chat_id, "text": text[:4096]}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status == 200
    except Exception:
        return False


# ── DB helpers ───────────────────────────────────────────────────────────────
def _db_conn():
    return psycopg2.connect(_DB_URL)


def _is_market_day() -> bool:
    today = datetime.now(_ET).date()
    if today.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return True


def _heartbeat(status: str = "alive", info: dict = None):
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO job_heartbeats (job_name, status, info, created_at)
                VALUES (%s,%s,%s,NOW())
                ON CONFLICT (job_name)
                DO UPDATE SET status=EXCLUDED.status, info=EXCLUDED.info, created_at=NOW()
            """, (_HEARTBEAT_NAME, status, json.dumps(info or {})))
            conn.commit()
    except Exception as exc:
        log.debug(f"Heartbeat error: {exc}")


def _recover_stale_jobs():
    """Reset CLAIMED/EXECUTING jobs that are stuck."""
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            # Stale CLAIMED
            cur.execute("""
                UPDATE ase_engine_jobs
                SET status='PENDING', claimed_at=NULL, attempts=attempts+1
                WHERE status='CLAIMED'
                  AND claimed_at < NOW() - INTERVAL '%s seconds'
                  AND attempts < %s
            """ % (_STALE_CLAIM_SEC, _MAX_RETRIES))
            # Stale EXECUTING
            cur.execute("""
                UPDATE ase_engine_jobs
                SET status='PENDING', started_at=NULL, claimed_at=NULL, attempts=attempts+1
                WHERE status='EXECUTING'
                  AND started_at < NOW() - INTERVAL '%s seconds'
                  AND attempts < %s
            """ % (_STALE_EXEC_SEC, _MAX_RETRIES))
            # Give up on too many retries
            cur.execute("""
                UPDATE ase_engine_jobs SET status='FAILED', error_msg='max retries exceeded'
                WHERE status='PENDING' AND attempts >= %s
            """, (_MAX_RETRIES,))
            conn.commit()
    except Exception as exc:
        log.error(f"recover_stale: {exc}")


# ── Daily candidate seeding ──────────────────────────────────────────────────
def _seed_candidates():
    """
    Seed today's ase_engine_jobs from polygon_rvol_scan (top N by RVOL).
    Only runs on market days.
    """
    if not _is_market_day():
        log.info("seed_candidates: not a market day — skipping")
        return

    today = date.today()
    log.info(f"seed_candidates: seeding for {today}")

    try:
        with _db_conn() as conn, conn.cursor() as cur:
            # Pull top-25 stocks from polygon_rvol_scan (most recent scan date)
            cur.execute("""
                SELECT DISTINCT ON (ticker) ticker, close_strength, rvol_ratio, gap_pct
                FROM polygon_rvol_scan
                WHERE scan_date >= CURRENT_DATE - INTERVAL '3 days'
                  AND rvol_ratio >= 1.5
                  AND close_price >= 5.0
                  AND close_price <= 500.0
                ORDER BY ticker, scan_date DESC, rvol_ratio DESC
                LIMIT %s
            """, (_MAX_CANDIDATES,))
            rows = cur.fetchall()

            if not rows:
                log.warning("seed_candidates: no rows from polygon_rvol_scan")
                _tg("⚠️ ASE Scheduler: no polygon_rvol_scan rows for seeding")
                return

            seeded = 0
            for ticker, close_str, rvol, gap_pct in rows:
                # Determine thesis from close_strength + gap
                if (close_str or 0) >= 0.60 and (gap_pct or 0) >= 0:
                    thesis = "BULLISH"
                elif (close_str or 0) <= 0.40 and (gap_pct or 0) <= 0:
                    thesis = "BEARISH"
                else:
                    thesis = "NEUTRAL"

                cur.execute("""
                    INSERT INTO ase_engine_jobs (ticker, thesis, scan_date, status, priority)
                    VALUES (%s, %s, %s, 'PENDING', 5)
                    ON CONFLICT (ticker, scan_date, thesis) DO NOTHING
                """, (ticker, thesis, today))
                seeded += cur.rowcount

            conn.commit()
            log.info(f"seed_candidates: seeded {seeded} new jobs")
            _tg(f"📊 ASE Scheduler: seeded {seeded} jobs for {today}")

    except Exception as exc:
        log.error(f"seed_candidates: {exc}")
        _tg(f"❌ ASE Scheduler seed error: {exc}")


# ── Strategy evaluation pipeline ─────────────────────────────────────────────
def _run_one_job(job_id: int, ticker: str, thesis: str, scan_date: date) -> bool:
    """Run the full strategy engine for one ticker+thesis."""
    # Import here to keep startup fast
    from aiem_strat_engine.builder import build_all_for_ticker
    from aiem_strat_engine.chain_data import get_spot, get_atm_iv, get_chain, get_expirations, get_skew
    from aiem_strat_engine.payoff import compute_payoff, _price_grid
    from aiem_strat_engine.probability import calibrated_pop
    from aiem_strat_engine.pricing import mid_price, conservative_fill, slippage_estimate, commission, liquidity_score as liq_sc
    from aiem_strat_engine.eligibility import check_strategy_eligible, assignment_risk_label, pin_risk_label
    from aiem_strat_engine.scoring import compute_capital_compounding_score
    from aiem_strat_engine.selector import EvaluationResult, select, evaluation_summary
    from aiem_strat_engine.greeks import aggregate as agg_greeks
    from aiem_strat_engine.legs import strategy_fingerprint, net_debit_credit
    from aiem_strat_engine.paper_trader import insert_paper_trade, save_decision_run, _new_run_id
    from aiem_strat_engine.config import config_sha256, PORTFOLIO_CAPITAL

    run_id = _new_run_id(ticker, thesis)
    log.info(f"[{run_id}] START {ticker} {thesis}")

    spot = get_spot(ticker)
    if not spot:
        log.warning(f"[{run_id}] no spot price for {ticker}")
        return False

    expirations = get_expirations(ticker)
    if not expirations:
        log.warning(f"[{run_id}] no expirations for {ticker}")
        return False

    # Get ATM IV from front-month chain
    front_chain = get_chain(ticker, expirations[0]) if expirations else []
    iv_rank = None
    atm_iv  = get_atm_iv(front_chain, spot) or 0.30
    skew    = get_skew(front_chain) or 0.0
    expected_move = spot * atm_iv * (21/365)**0.5  # 21-day expected move approx

    # Determine market regime from DB (best-effort)
    market_regime = "NEUTRAL"
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT close_strength, rvol_ratio FROM polygon_rvol_scan
                WHERE ticker=%s ORDER BY scan_date DESC LIMIT 1
            """, (ticker,))
            row = cur.fetchone()
            if row:
                cs, rv = row
                if (cs or 0) > 0.70 and (rv or 0) > 2.0: market_regime = "BULL_TREND"
                elif (cs or 0) < 0.30 and (rv or 0) > 2.0: market_regime = "BEAR_TREND"
    except Exception:
        pass

    vol_regime = "HIGH_IV" if (atm_iv or 0) > 0.40 else "LOW_IV"

    # Build all eligible strategies
    strategy_builds = build_all_for_ticker(ticker, thesis, market_regime, vol_regime)
    log.info(f"[{run_id}] built {len(strategy_builds)} strategies")

    evaluations = []
    for spec, legs in strategy_builds:
        try:
            # Payoff
            dte_min = min((lg.dte or 30) for lg in legs if lg.dte)
            payoff_info = compute_payoff(legs, spec.name, spot)

            # Probability
            prices = _price_grid(spot)
            payoffs_list = [sum(
                (1 if lg.side == "LONG" else -1) * max(0, (spot_p - (lg.strike or spot_p)) if lg.asset_type == "CALL" else max(0, (lg.strike or spot_p) - spot_p)) * lg.ratio
                for lg in legs if lg.asset_type != "STOCK"
            ) - (payoff_info.get("net_cost") or 0) for spot_p in prices]

            prob_info = calibrated_pop(
                payoffs_list, prices, spot, atm_iv, dte_min, skew, expected_move
            )
            pop = prob_info.get("pop")

            # Pricing
            mid     = mid_price(legs)
            cfill   = conservative_fill(legs)
            slip    = slippage_estimate(legs, atm_iv)
            comm    = commission(legs)
            liq     = liq_sc(legs)
            max_loss= payoff_info.get("max_loss")
            cap_risk= (max_loss or 0) * 100

            ev_raw = None
            ev_net = None
            ror    = None
            if max_loss and max_loss > 0 and pop is not None:
                max_p = payoff_info.get("max_profit") or 0
                ev_raw = pop * max_p * 100 - (1 - pop) * max_loss * 100
                ev_net = ev_raw - slip - comm
                ror    = ev_net / max(cap_risk, 1.0)

            pricing_info = {
                "mid": mid, "conservative_fill": cfill, "slippage": slip,
                "commission": comm, "liquidity_score": liq, "ev_before_costs": ev_raw,
                "ev_after_costs": ev_net, "capital_at_risk": cap_risk,
                "buying_power": cap_risk, "return_on_risk": ror,
                "reward_risk": (payoff_info.get("max_profit") or 0) / max(max_loss or 1, 0.001),
            }

            # Greeks
            greeks_info = agg_greeks(legs)

            # Eligibility
            elig, reject_reasons = check_strategy_eligible(
                legs, spec.execution_mode, max_loss, pop, ror
            )

            # Score
            sc = compute_capital_compounding_score(
                pop=pop, ev_after_costs=ror, max_loss=max_loss,
                max_profit=payoff_info.get("max_profit"), risk_class=spec.risk_class,
                execution_mode=spec.execution_mode, liquidity=liq,
                strategy_direction=spec.direction, strategy_vol_thesis=spec.vol_thesis,
                strategy_family=spec.family, thesis=thesis, market_regime=market_regime,
                vol_regime=vol_regime, iv_rank=iv_rank, return_on_risk=ror,
                assignment_risk=assignment_risk_label(legs),
                pop_fat_tail=prob_info.get("pop_fat_tail"),
                pop_lognormal=prob_info.get("pop_lognormal"),
                slippage=slip, capital_at_risk=cap_risk, n_legs=len(legs),
                portfolio_capital=PORTFOLIO_CAPITAL,
            )

            fp = strategy_fingerprint(legs)
            evaluations.append(EvaluationResult(
                strategy_name=spec.name, strategy_family=spec.family,
                strategy_fingerprint=fp, risk_class=spec.risk_class,
                execution_mode=spec.execution_mode, eligible=elig,
                rejection_reasons=reject_reasons, legs=legs,
                payoff_info=payoff_info, probability_info=prob_info,
                pricing_info=pricing_info, greeks_info=greeks_info,
                score_components=sc, capital_compounding_score=sc["capital_compounding_score"],
                iv_rank=iv_rank,
            ))
        except Exception as exc:
            log.debug(f"[{run_id}] eval error {spec.name}: {exc}")

    # Select best
    from aiem_strat_engine.selector import select
    selection = select(evaluations, thesis, market_regime, iv_rank)
    log.info(f"[{run_id}] decision={selection.decision} reason={selection.reason[:80]}")

    # Persist decision run
    save_decision_run(
        run_id=run_id, ticker=ticker, spot=spot, thesis=thesis,
        market_regime=market_regime, volatility_regime=vol_regime, event_context=None,
        iv_rank=iv_rank, iv_percentile=None, expected_move=expected_move,
        n_evaluated=len(evaluations),
        n_rejected=sum(1 for e in evaluations if not e.eligible),
        selection=selection, config_sha=config_sha256(),
    )

    # Paper trade if selected
    if selection.decision == "TRADE" and selection.selected:
        pt_id = insert_paper_trade(
            evaluation=selection.selected, selection=selection,
            ticker=ticker, thesis=thesis, market_regime=market_regime,
            volatility_regime=vol_regime, event_context=None,
            run_id=run_id, underlying_price=spot,
        )
        if pt_id:
            log.info(f"[{run_id}] Paper trade inserted: {pt_id}")
            _tg(
                f"📈 ASE: {ticker} {thesis}\n"
                f"Strategy: {selection.selected.strategy_name}\n"
                f"Score: {selection.selected.capital_compounding_score:.3f}\n"
                f"ID: {pt_id}"
            )

    return True


def _run_all_pending():
    """Claim and run all PENDING ase_engine_jobs."""
    if not _is_market_day():
        return

    _recover_stale_jobs()
    today = date.today()

    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, thesis, scan_date FROM ase_engine_jobs
                WHERE status='PENDING' AND scan_date=%s
                ORDER BY priority DESC, created_at ASC LIMIT 30
            """, (today,))
            jobs = cur.fetchall()
    except Exception as exc:
        log.error(f"run_all_pending: {exc}")
        return

    log.info(f"run_all_pending: {len(jobs)} jobs to process")
    for job_id, ticker, thesis, scan_date in jobs:
        # Atomic claim
        try:
            with _db_conn() as conn, conn.cursor() as cur:
                cur.execute("""
                    UPDATE ase_engine_jobs
                    SET status='CLAIMED', claimed_at=NOW()
                    WHERE id=%s AND status='PENDING'
                    RETURNING id
                """, (job_id,))
                if not cur.fetchone():
                    continue  # already claimed by another worker
                conn.commit()
        except Exception:
            continue

        # Execute
        try:
            with _db_conn() as conn, conn.cursor() as cur:
                cur.execute("UPDATE ase_engine_jobs SET status='EXECUTING', started_at=NOW() WHERE id=%s", (job_id,))
                conn.commit()

            success = _run_one_job(job_id, ticker, thesis, scan_date)

            with _db_conn() as conn, conn.cursor() as cur:
                new_status = "DONE" if success else "FAILED"
                cur.execute("UPDATE ase_engine_jobs SET status=%s, finished_at=NOW() WHERE id=%s", (new_status, job_id))
                conn.commit()
        except Exception as exc:
            log.error(f"job {job_id} ({ticker}): {exc}")
            try:
                with _db_conn() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE ase_engine_jobs SET status='FAILED', error_msg=%s WHERE id=%s", (str(exc)[:500], job_id))
                    conn.commit()
            except Exception:
                pass


# ── Position management ───────────────────────────────────────────────────────
def _monitor_positions():
    """Afternoon position monitoring + exit decisions."""
    if not _is_market_day():
        return
    from aiem_strat_engine.position_manager import monitor_all_positions
    log.info("monitor_positions: starting")
    summary = monitor_all_positions()
    log.info(f"monitor_positions: closed={len(summary.get('closed', []))}, held={len(summary.get('held', []))}")
    if summary.get("closed"):
        for c in summary["closed"]:
            _tg(f"🔔 ASE Closed: {c.get('ticker')} — {c.get('reason')} PnL=${c.get('pnl', 0):.2f}")


# ── Reporting ─────────────────────────────────────────────────────────────────
def _daily_report():
    from aiem_strat_engine.reporting import generate_daily_report
    rpt = generate_daily_report()
    if rpt:
        log.info(f"daily_report: {rpt.get('trades_closed')} closed, PnL=${rpt.get('net_pnl_paper', 0):.2f}")
        _tg(
            f"📋 ASE Daily Report {date.today()}\n"
            f"Scans: {rpt.get('scans_run', 0)} | Trades: {rpt.get('trades_opened', 0)} opened, {rpt.get('trades_closed', 0)} closed\n"
            f"PnL (paper): ${rpt.get('net_pnl_paper', 0):.2f}\n"
            f"Win rate: {(rpt.get('win_rate') or 0):.1%}"
        )


def _weekly_report():
    today = datetime.now(_ET).date()
    if today.weekday() != 4:  # only Fridays
        return
    from aiem_strat_engine.reporting import generate_weekly_report
    rpt = generate_weekly_report()
    if rpt:
        log.info(f"weekly_report done: {rpt.get('report_id')}")


def _monthly_report():
    today = datetime.now(_ET).date()
    # Last calendar day of month
    tomorrow = today + timedelta(days=1)
    if tomorrow.month == today.month:
        return
    from aiem_strat_engine.reporting import generate_monthly_report
    rpt = generate_monthly_report()
    if rpt:
        log.info(f"monthly_report done: {rpt.get('report_id')}")


def _cleanup_old_jobs():
    """Purge ase_engine_jobs older than 30 days."""
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                DELETE FROM ase_engine_jobs
                WHERE created_at < NOW() - INTERVAL '30 days'
                  AND status IN ('DONE','FAILED')
            """)
            deleted = cur.rowcount
            conn.commit()
        if deleted:
            log.info(f"cleanup: deleted {deleted} old jobs")
    except Exception as exc:
        log.error(f"cleanup: {exc}")


# ── Health server ─────────────────────────────────────────────────────────────
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps({
            "service": "aiem_strat_scheduler",
            "status":  "ok",
            "ts":      datetime.utcnow().isoformat() + "Z",
            "tables":  list_tables(),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args): pass   # suppress access logs

def _start_health_server():
    try:
        srv = HTTPServer(("0.0.0.0", _HEALTH_PORT), _HealthHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        log.info(f"Health server started on port {_HEALTH_PORT}")
    except Exception as exc:
        log.warning(f"Health server failed: {exc}")


# ── Startup ───────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("aiem_strat_scheduler starting")
    log.info(f"Health port: {_HEALTH_PORT}")
    log.info("=" * 60)

    # 1. Create schema (idempotent)
    log.info("Creating ase_* schema...")
    ok = create_schema()
    log.info(f"Schema: {'OK' if ok else 'FAILED'}")
    tables = list_tables()
    log.info(f"Tables: {tables}")

    # 2. Start health server
    _start_health_server()

    # 3. Build scheduler
    sched = BackgroundScheduler(timezone=_ET)

    # 09:40 ET — seed daily candidates
    sched.add_job(_seed_candidates, CronTrigger(day_of_week="mon-fri", hour=9, minute=40), id="seed")
    # 09:55 ET — run all pending jobs
    sched.add_job(_run_all_pending,  CronTrigger(day_of_week="mon-fri", hour=9, minute=55), id="eval")
    # 16:00 ET — monitor/close positions
    sched.add_job(_monitor_positions, CronTrigger(day_of_week="mon-fri", hour=16, minute=0), id="monitor")
    # 16:15 ET — daily report
    sched.add_job(_daily_report,      CronTrigger(day_of_week="mon-fri", hour=16, minute=15), id="daily_rpt")
    # 18:00 ET — weekly report (Fridays)
    sched.add_job(_weekly_report,     CronTrigger(day_of_week="mon-fri", hour=18, minute=0), id="weekly_rpt")
    # 22:00 ET — monthly report (last day of month)
    sched.add_job(_monthly_report,    CronTrigger(day_of_week="mon-fri", hour=22, minute=0), id="monthly_rpt")
    # 00:05 ET — cleanup old jobs
    sched.add_job(_cleanup_old_jobs,  CronTrigger(hour=0, minute=5),                         id="cleanup")
    # Heartbeat every 5 min
    sched.add_job(lambda: _heartbeat("alive", {"tables": len(tables)}),
                  "interval", minutes=5, id="heartbeat")

    sched.start()
    log.info("Scheduler started")

    # 4. Startup kick if today is a market day and it's past 9:40 ET
    now_et = datetime.now(_ET)
    if _is_market_day() and now_et.hour >= 9 and now_et.minute >= 45:
        log.info("Startup kick: running missed evaluation pass")
        threading.Thread(target=_seed_candidates, daemon=True).start()
        threading.Thread(target=_run_all_pending, daemon=True).start()

    _tg(f"🚀 ASE Scheduler started — port {_HEALTH_PORT}, schema {'OK' if ok else 'FAILED'}")
    log.info("Running — waiting for scheduled jobs")

    try:
        while True:
            time.sleep(30)
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down scheduler")
        sched.shutdown(wait=False)


if __name__ == "__main__":
    main()
