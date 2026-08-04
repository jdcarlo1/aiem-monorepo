"""
aiem_options_trigger_engine.py  —  Phase 6: Entry Trigger Engine
=================================================================
Options Engine only. Paper trading only.

Strategy selection creates PENDING_EXECUTION_PLAN (not immediate execution).
A scheduler job evaluates pending plans against live market snapshots and,
when a trigger fires, runs 8-check pre-fill revalidation before paper fill.

Supported trigger types (11):
  VWAP_RECLAIM       price crosses above VWAP from below
  VWAP_REJECTION     price fails at VWAP and turns down
  SR_BREAK           price breaks above/below a key S/R level
  PM_HIGH_BREAK      price breaks above premarket high
  PM_LOW_BREAK       price breaks below premarket low
  ORB_BREAK          price breaks above/below 30-min opening range
  PULLBACK_CONFIRMED price retraces to key level and holds
  VOLUME_CONFIRM     bar volume exceeds ADV multiple threshold
  MOMENTUM_CONFIRM   RSI crosses a directional threshold
  SECTOR_ALIGN       sector ETF bias aligns with trade direction
  LIQUIDITY_CONFIRM  option spread < threshold and volume > minimum

Pre-fill revalidation (8 checks — all must pass):
  spot_freshness       quote_age_seconds < 300
  chain_freshness      chain_age_seconds < 300
  liquidity            option bid > 0, spread_pct < max_spread
  trigger_validity     price hasn't moved > 2 % past trigger reference level
  duplicate_protection no existing FILLED plan for same ticker + scan_date
  portfolio_limits     open FILLED plans < max_open_positions
  max_loss             plan max_risk_usd < max_total_risk_usd
  expected_value       expected_value >= 0 when provided by snapshot

Data immutability: no DELETE / TRUNCATE / UPDATE on existing rows.
All writes via INSERT ... ON CONFLICT DO NOTHING or INSERT new rows.
UPDATE is used ONLY to advance status on rows created by this module.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

log = logging.getLogger("trigger_engine")
if not log.handlers:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

_DB_URL      = os.environ.get("DATABASE_URL", "")
_BOOTSTRAPPED = False

# Default plan TTL: plans expire if trigger never fires within this window
_DEFAULT_TTL_SECONDS = 7_200   # 2 hours

# Trigger type registry — all 11 supported triggers
TRIGGER_TYPES = {
    "VWAP_RECLAIM",
    "VWAP_REJECTION",
    "SR_BREAK",
    "PM_HIGH_BREAK",
    "PM_LOW_BREAK",
    "ORB_BREAK",
    "PULLBACK_CONFIRMED",
    "VOLUME_CONFIRM",
    "MOMENTUM_CONFIRM",
    "SECTOR_ALIGN",
    "LIQUIDITY_CONFIRM",
}

# Revalidation check names (order matters for cancel_reason reporting)
_REVAL_CHECKS = [
    "spot_freshness",
    "chain_freshness",
    "liquidity",
    "trigger_validity",
    "duplicate_protection",
    "portfolio_limits",
    "max_loss",
    "expected_value",
]

# ─────────────────────────────────────────────────────────────────────────────
# DB BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_trigger_engine(db_url: str = "") -> bool:
    """
    Idempotent CREATE TABLE IF NOT EXISTS for oe_execution_plans.
    Returns True on success, False on error.
    Called from aiem_options_scheduler._bootstrap_db() and at module first-use.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return True
    url = db_url or _DB_URL
    if not url:
        log.warning("[trigger_engine] bootstrap skipped — no DATABASE_URL")
        return False
    try:
        with psycopg2.connect(url, connect_timeout=8) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_execution_plans (
                    plan_id              TEXT         PRIMARY KEY,
                    trace_id             TEXT,
                    ticker               VARCHAR(20)  NOT NULL,
                    scan_date            DATE         NOT NULL,
                    direction            VARCHAR(12)  NOT NULL
                        CHECK (direction IN ('LONG_CALL','LONG_PUT')),
                    selected_strategy    VARCHAR(64),
                    score_at_plan        NUMERIC(8,4),
                    trigger_type         VARCHAR(48)  NOT NULL
                        CHECK (trigger_type IN (
                            'VWAP_RECLAIM','VWAP_REJECTION','SR_BREAK',
                            'PM_HIGH_BREAK','PM_LOW_BREAK','ORB_BREAK',
                            'PULLBACK_CONFIRMED','VOLUME_CONFIRM',
                            'MOMENTUM_CONFIRM','SECTOR_ALIGN','LIQUIDITY_CONFIRM'
                        )),
                    trigger_condition    JSONB        NOT NULL DEFAULT '{}',
                    snapshot_at_creation JSONB        DEFAULT '{}',
                    status               TEXT         NOT NULL
                        DEFAULT 'PENDING_EXECUTION_PLAN'
                        CHECK (status IN (
                            'PENDING_EXECUTION_PLAN',
                            'TRIGGER_MET',
                            'TRIGGER_EXPIRED',
                            'CANCELLED',
                            'FILLED'
                        )),
                    plan_created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    plan_expires_at      TIMESTAMPTZ  NOT NULL,
                    trigger_checked_at   TIMESTAMPTZ,
                    trigger_met_at       TIMESTAMPTZ,
                    status_updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    revalidation_json    JSONB,
                    cancel_reason        TEXT,
                    fill_ts              TIMESTAMPTZ,
                    fill_alert_id        INTEGER,
                    is_test_record       BOOLEAN      NOT NULL DEFAULT FALSE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS ix_oep_status_scan
                    ON oe_execution_plans (status, scan_date)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS ix_oep_ticker_date
                    ON oe_execution_plans (ticker, scan_date)
            """)
            conn.commit()
        _BOOTSTRAPPED = True
        log.info("[trigger_engine] oe_execution_plans bootstrapped")
        return True
    except Exception as e:
        log.error(f"[trigger_engine] bootstrap failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# TRIGGER SELECTION  (chooses the most appropriate trigger given live context)
# ─────────────────────────────────────────────────────────────────────────────

def select_trigger(
    direction:  str,
    stock_data: dict,
    pm_intel:   dict,
) -> Tuple[str, dict]:
    """
    Given a scored direction and market context, return the most specific
    trigger type and its condition dict.  Falls back to VWAP_RECLAIM/REJECTION
    when more specific information is unavailable.

    Returns: (trigger_type: str, trigger_condition: dict)
    """
    price = float(stock_data.get("price") or stock_data.get("close") or 0)
    vwap  = float(stock_data.get("vwap") or 0)
    pm_high = float(pm_intel.get("premarket_high") or 0)
    pm_low  = float(pm_intel.get("premarket_low")  or 0)

    if direction == "LONG_CALL":
        # Prefer PM high break if premarket data exists
        if pm_high > 0 and price < pm_high * 1.005:
            return "PM_HIGH_BREAK", {
                "direction": "BULLISH",
                "pm_high": pm_high,
                "breakout_buffer_pct": 0.001,
            }
        # VWAP reclaim
        if vwap > 0:
            return "VWAP_RECLAIM", {
                "direction": "BULLISH",
                "vwap_ref": vwap,
                "confirm_close_above": True,
            }
        # Volume confirmation fallback
        return "VOLUME_CONFIRM", {
            "direction": "BULLISH",
            "volume_ratio_threshold": 1.5,
        }
    else:  # LONG_PUT
        # Prefer PM low break
        if pm_low > 0 and price > pm_low * 0.995:
            return "PM_LOW_BREAK", {
                "direction": "BEARISH",
                "pm_low": pm_low,
                "breakdown_buffer_pct": 0.001,
            }
        # VWAP rejection
        if vwap > 0:
            return "VWAP_REJECTION", {
                "direction": "BEARISH",
                "vwap_ref": vwap,
                "confirm_close_below": True,
            }
        return "VOLUME_CONFIRM", {
            "direction": "BEARISH",
            "volume_ratio_threshold": 1.5,
        }


# ─────────────────────────────────────────────────────────────────────────────
# CREATE EXECUTION PLAN
# ─────────────────────────────────────────────────────────────────────────────

def create_execution_plan(
    ticker:            str,
    direction:         str,
    strategy:          Optional[str],
    score:             float,
    trigger_type:      str,
    trigger_condition: dict,
    trace_id:          str,
    scan_date,         # date or str
    db_url:            str = "",
    is_test_record:    bool = False,
    plan_ttl_seconds:  int = _DEFAULT_TTL_SECONDS,
    snapshot:          Optional[dict] = None,
) -> dict:
    """
    Write one PENDING_EXECUTION_PLAN row.  Idempotent on (ticker, scan_date,
    direction) — returns existing plan_id if a PENDING plan already exists.

    Returns dict with keys: plan_id, status, created_at, expires_at, existing
    """
    if trigger_type not in TRIGGER_TYPES:
        return {"error": f"unknown trigger_type: {trigger_type!r}"}
    if direction not in ("LONG_CALL", "LONG_PUT"):
        return {"error": f"invalid direction: {direction!r}"}

    url = db_url or _DB_URL
    bootstrap_trigger_engine(url)

    now_utc   = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(seconds=plan_ttl_seconds)
    plan_id   = f"oep_{uuid.uuid4().hex[:20]}"

    try:
        with psycopg2.connect(url, connect_timeout=6) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Idempotency: return existing PENDING plan if one exists
                cur.execute("""
                    SELECT plan_id, status, plan_created_at, plan_expires_at
                    FROM oe_execution_plans
                    WHERE ticker=%s AND scan_date=%s AND direction=%s
                      AND status='PENDING_EXECUTION_PLAN'
                      AND is_test_record=%s
                    LIMIT 1
                """, (ticker, scan_date, direction, is_test_record))
                existing = cur.fetchone()
                if existing:
                    log.info(
                        f"[trigger_engine] existing PENDING plan "
                        f"plan_id={existing['plan_id']} for {ticker}/{scan_date}/{direction}"
                    )
                    return {
                        "plan_id":    existing["plan_id"],
                        "status":     existing["status"],
                        "created_at": existing["plan_created_at"].isoformat(),
                        "expires_at": existing["plan_expires_at"].isoformat(),
                        "existing":   True,
                    }

                cur.execute("""
                    INSERT INTO oe_execution_plans (
                        plan_id, trace_id, ticker, scan_date,
                        direction, selected_strategy, score_at_plan,
                        trigger_type, trigger_condition,
                        snapshot_at_creation,
                        status, plan_created_at, plan_expires_at,
                        status_updated_at, is_test_record
                    ) VALUES (
                        %s,%s,%s,%s,
                        %s,%s,%s,
                        %s,%s,
                        %s,
                        'PENDING_EXECUTION_PLAN',%s,%s,
                        %s,%s
                    )
                    ON CONFLICT (plan_id) DO NOTHING
                """, (
                    plan_id, trace_id, ticker, scan_date,
                    direction, strategy, score,
                    trigger_type, json.dumps(trigger_condition),
                    json.dumps(snapshot or {}),
                    now_utc, expires_at,
                    now_utc, is_test_record,
                ))
                conn.commit()

        log.info(
            f"[trigger_engine] PENDING_EXECUTION_PLAN created "
            f"plan_id={plan_id} ticker={ticker} direction={direction} "
            f"trigger={trigger_type} expires={expires_at.isoformat()} "
            f"test={is_test_record}"
        )
        return {
            "plan_id":    plan_id,
            "status":     "PENDING_EXECUTION_PLAN",
            "created_at": now_utc.isoformat(),
            "expires_at": expires_at.isoformat(),
            "existing":   False,
        }
    except Exception as e:
        log.error(f"[trigger_engine] create_execution_plan failed: {e}")
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# TRIGGER EVALUATION  (per-type condition check)
# ─────────────────────────────────────────────────────────────────────────────

def _check_trigger(
    trigger_type:      str,
    trigger_condition: dict,
    snapshot:          dict,
) -> Tuple[bool, str]:
    """
    Evaluate one trigger type against a live market snapshot.

    snapshot keys used (all optional — missing keys cause the check to pass
    conservatively or return a specific 'insufficient data' reason):
        price, vwap, volume, adv, rsi, sector_bias,
        option_bid, option_ask, option_volume,
        pm_high, pm_low, orb_high, orb_low

    Returns: (fired: bool, reason: str)
    """
    price = float(snapshot.get("price") or 0)

    # ── VWAP_RECLAIM ────────────────────────────────────────────────────────
    if trigger_type == "VWAP_RECLAIM":
        vwap = float(snapshot.get("vwap") or trigger_condition.get("vwap_ref") or 0)
        if vwap <= 0:
            return False, "vwap_unavailable"
        fired = price >= vwap
        return fired, f"price={price:.4f} vwap={vwap:.4f} reclaimed={fired}"

    # ── VWAP_REJECTION ──────────────────────────────────────────────────────
    if trigger_type == "VWAP_REJECTION":
        vwap = float(snapshot.get("vwap") or trigger_condition.get("vwap_ref") or 0)
        if vwap <= 0:
            return False, "vwap_unavailable"
        fired = price < vwap
        return fired, f"price={price:.4f} vwap={vwap:.4f} rejected={fired}"

    # ── SR_BREAK ────────────────────────────────────────────────────────────
    if trigger_type == "SR_BREAK":
        level     = float(trigger_condition.get("level") or 0)
        direction = trigger_condition.get("direction", "BULLISH")
        buf       = float(trigger_condition.get("buffer_pct") or 0.001)
        if level <= 0:
            return False, "sr_level_missing"
        if direction == "BULLISH":
            fired = price >= level * (1 + buf)
            return fired, f"price={price:.4f} sr_level={level:.4f} break_up={fired}"
        else:
            fired = price <= level * (1 - buf)
            return fired, f"price={price:.4f} sr_level={level:.4f} break_down={fired}"

    # ── PM_HIGH_BREAK ────────────────────────────────────────────────────────
    if trigger_type == "PM_HIGH_BREAK":
        pm_high = float(
            snapshot.get("pm_high") or trigger_condition.get("pm_high") or 0
        )
        buf = float(trigger_condition.get("breakout_buffer_pct") or 0.001)
        if pm_high <= 0:
            return False, "pm_high_unavailable"
        fired = price >= pm_high * (1 + buf)
        return fired, f"price={price:.4f} pm_high={pm_high:.4f} fired={fired}"

    # ── PM_LOW_BREAK ─────────────────────────────────────────────────────────
    if trigger_type == "PM_LOW_BREAK":
        pm_low = float(
            snapshot.get("pm_low") or trigger_condition.get("pm_low") or 0
        )
        buf = float(trigger_condition.get("breakdown_buffer_pct") or 0.001)
        if pm_low <= 0:
            return False, "pm_low_unavailable"
        fired = price <= pm_low * (1 - buf)
        return fired, f"price={price:.4f} pm_low={pm_low:.4f} fired={fired}"

    # ── ORB_BREAK ────────────────────────────────────────────────────────────
    if trigger_type == "ORB_BREAK":
        orb_high  = float(snapshot.get("orb_high") or trigger_condition.get("orb_high") or 0)
        orb_low   = float(snapshot.get("orb_low")  or trigger_condition.get("orb_low")  or 0)
        direction = trigger_condition.get("direction", "BULLISH")
        if orb_high <= 0 or orb_low <= 0:
            return False, "orb_unavailable"
        if direction == "BULLISH":
            fired = price > orb_high
            return fired, f"price={price:.4f} orb_high={orb_high:.4f} orb_break_up={fired}"
        else:
            fired = price < orb_low
            return fired, f"price={price:.4f} orb_low={orb_low:.4f} orb_break_down={fired}"

    # ── PULLBACK_CONFIRMED ───────────────────────────────────────────────────
    if trigger_type == "PULLBACK_CONFIRMED":
        level     = float(trigger_condition.get("pullback_level") or 0)
        direction = trigger_condition.get("direction", "BULLISH")
        tol       = float(trigger_condition.get("bounce_confirmed_pct") or 0.005)
        if level <= 0:
            return False, "pullback_level_missing"
        if direction == "BULLISH":
            # Price must be within tol% above the pullback level (bounced and held)
            fired = level <= price <= level * (1 + tol * 5)
            return fired, f"price={price:.4f} pullback_level={level:.4f} held={fired}"
        else:
            fired = level * (1 - tol * 5) <= price <= level
            return fired, f"price={price:.4f} pullback_level={level:.4f} held={fired}"

    # ── VOLUME_CONFIRM ───────────────────────────────────────────────────────
    if trigger_type == "VOLUME_CONFIRM":
        volume    = float(snapshot.get("volume") or 0)
        adv       = float(snapshot.get("adv") or 0)
        threshold = float(trigger_condition.get("volume_ratio_threshold") or 1.5)
        if adv <= 0:
            # No ADV: pass if volume is non-zero (best-effort)
            return volume > 0, f"volume={volume:.0f} adv=unavailable fired={volume>0}"
        ratio = volume / adv
        fired = ratio >= threshold
        return fired, f"volume={volume:.0f} adv={adv:.0f} ratio={ratio:.2f} threshold={threshold} fired={fired}"

    # ── MOMENTUM_CONFIRM ─────────────────────────────────────────────────────
    if trigger_type == "MOMENTUM_CONFIRM":
        rsi       = float(snapshot.get("rsi") or 50)
        direction = trigger_condition.get("direction", "BULLISH")
        threshold = float(trigger_condition.get("rsi_threshold") or (55 if direction == "BULLISH" else 45))
        if direction == "BULLISH":
            fired = rsi >= threshold
            return fired, f"rsi={rsi:.1f} threshold={threshold} bullish_confirmed={fired}"
        else:
            fired = rsi <= threshold
            return fired, f"rsi={rsi:.1f} threshold={threshold} bearish_confirmed={fired}"

    # ── SECTOR_ALIGN ────────────────────────────────────────────────────────
    if trigger_type == "SECTOR_ALIGN":
        sector_bias     = str(snapshot.get("sector_bias") or "NEUTRAL")
        required_bias   = trigger_condition.get("direction", "BULLISH")
        expected_bias   = "BULLISH" if required_bias == "BULLISH" else "BEARISH"
        fired = sector_bias.upper() == expected_bias
        return fired, f"sector_bias={sector_bias} expected={expected_bias} aligned={fired}"

    # ── LIQUIDITY_CONFIRM ────────────────────────────────────────────────────
    if trigger_type == "LIQUIDITY_CONFIRM":
        bid        = float(snapshot.get("option_bid") or 0)
        ask        = float(snapshot.get("option_ask") or 0)
        vol        = int(snapshot.get("option_volume") or 0)
        max_spread = float(trigger_condition.get("max_spread_pct") or 0.15)
        min_vol    = int(trigger_condition.get("min_volume") or 10)
        if ask <= 0:
            return False, "option_ask_unavailable"
        spread_pct = (ask - bid) / ask
        fired = bid > 0 and spread_pct <= max_spread and vol >= min_vol
        return fired, (
            f"bid={bid:.4f} ask={ask:.4f} spread={spread_pct:.3f} "
            f"vol={vol} fired={fired}"
        )

    return False, f"unknown_trigger_type:{trigger_type}"


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATE TRIGGER  (check one plan against a snapshot, advance status)
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_trigger(plan_id: str, snapshot: dict, db_url: str = "") -> dict:
    """
    Load plan from DB, evaluate its trigger condition against snapshot.

    Status transitions:
      PENDING_EXECUTION_PLAN → TRIGGER_MET   (condition satisfied)
      PENDING_EXECUTION_PLAN → TRIGGER_EXPIRED (now > plan_expires_at)
      PENDING_EXECUTION_PLAN stays pending    (condition not yet met)

    Returns dict with keys:
      plan_id, ticker, direction, trigger_type, fired, reason, status
    """
    url = db_url or _DB_URL
    now_utc = datetime.now(timezone.utc)

    try:
        with psycopg2.connect(url, connect_timeout=4) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT plan_id, ticker, scan_date, direction,
                           trigger_type, trigger_condition, status,
                           plan_created_at, plan_expires_at, is_test_record
                    FROM oe_execution_plans
                    WHERE plan_id = %s
                """, (plan_id,))
                plan = cur.fetchone()
                if not plan:
                    return {"error": f"plan_id {plan_id!r} not found"}

                if plan["status"] != "PENDING_EXECUTION_PLAN":
                    return {
                        "plan_id":      plan_id,
                        "ticker":       plan["ticker"],
                        "direction":    plan["direction"],
                        "trigger_type": plan["trigger_type"],
                        "status":       plan["status"],
                        "fired":        False,
                        "reason":       f"plan already in terminal state: {plan['status']}",
                    }

                # Check expiry first
                expires_at = plan["plan_expires_at"]
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if now_utc > expires_at:
                    cur.execute("""
                        UPDATE oe_execution_plans
                        SET status='TRIGGER_EXPIRED',
                            trigger_checked_at=%s,
                            status_updated_at=%s
                        WHERE plan_id=%s AND status='PENDING_EXECUTION_PLAN'
                    """, (now_utc, now_utc, plan_id))
                    conn.commit()
                    log.info(
                        f"[trigger_engine] TRIGGER_EXPIRED plan_id={plan_id} "
                        f"ticker={plan['ticker']} expired_at={expires_at.isoformat()}"
                    )
                    return {
                        "plan_id":      plan_id,
                        "ticker":       plan["ticker"],
                        "direction":    plan["direction"],
                        "trigger_type": plan["trigger_type"],
                        "status":       "TRIGGER_EXPIRED",
                        "fired":        False,
                        "reason":       f"expired at {expires_at.isoformat()}",
                    }

                # Evaluate trigger condition
                tc = plan["trigger_condition"]
                if isinstance(tc, str):
                    tc = json.loads(tc)

                fired, reason = _check_trigger(
                    plan["trigger_type"], tc, snapshot
                )

                # Record the check timestamp regardless of outcome
                new_status = "TRIGGER_MET" if fired else "PENDING_EXECUTION_PLAN"
                cur.execute("""
                    UPDATE oe_execution_plans
                    SET trigger_checked_at = %s,
                        status             = %s,
                        trigger_met_at     = CASE WHEN %s THEN %s ELSE trigger_met_at END,
                        status_updated_at  = CASE WHEN %s THEN %s ELSE status_updated_at END
                    WHERE plan_id = %s AND status = 'PENDING_EXECUTION_PLAN'
                """, (
                    now_utc,
                    new_status,
                    fired, now_utc,   # trigger_met_at
                    fired, now_utc,   # status_updated_at
                    plan_id,
                ))
                conn.commit()

        if fired:
            log.info(
                f"[trigger_engine] TRIGGER_MET plan_id={plan_id} "
                f"ticker={plan['ticker']} direction={plan['direction']} "
                f"trigger={plan['trigger_type']} reason={reason}"
            )
        else:
            log.debug(
                f"[trigger_engine] trigger not yet met plan_id={plan_id} reason={reason}"
            )

        return {
            "plan_id":      plan_id,
            "ticker":       plan["ticker"],
            "direction":    plan["direction"],
            "trigger_type": plan["trigger_type"],
            "fired":        fired,
            "reason":       reason,
            "status":       new_status,
            "checked_at":   now_utc.isoformat(),
        }

    except Exception as e:
        log.error(f"[trigger_engine] evaluate_trigger error plan_id={plan_id}: {e}")
        return {"error": str(e), "plan_id": plan_id}


# ─────────────────────────────────────────────────────────────────────────────
# PRE-FILL REVALIDATION  (8 checks before paper fill)
# ─────────────────────────────────────────────────────────────────────────────

def _run_prefill_revalidation(
    plan:           dict,
    snapshot:       dict,
    champion_config: dict,
    db_url:         str,
) -> Tuple[bool, dict]:
    """
    Run all 8 pre-fill revalidation checks.
    Returns (all_passed: bool, detail: dict).

    Caller must pass champion_config (from aiem_options_phase5.get_current_champion_config).
    If not available, defaults are used.
    """
    checks: Dict[str, Any] = {}
    plan_id      = plan.get("plan_id", "")
    ticker       = plan.get("ticker", "")
    scan_date    = plan.get("scan_date")
    is_test      = plan.get("is_test_record", False)
    direction    = plan.get("direction", "")
    tc           = plan.get("trigger_condition", {})
    if isinstance(tc, str):
        tc = json.loads(tc)

    # Champion config defaults
    max_spread_pct      = float(champion_config.get("max_spread_pct",      0.30))
    max_open_positions  = int(  champion_config.get("max_open_positions",   10))
    max_total_risk_usd  = float(champion_config.get("max_total_risk_usd",   50_000))

    # 1. Spot freshness
    quote_age = float(snapshot.get("quote_age_seconds") or 9_999)
    checks["spot_freshness"] = {
        "passed":            quote_age < 300,
        "quote_age_seconds": quote_age,
        "threshold_s":       300,
    }

    # 2. Chain freshness
    chain_age = float(snapshot.get("chain_age_seconds") or 9_999)
    checks["chain_freshness"] = {
        "passed":            chain_age < 300,
        "chain_age_seconds": chain_age,
        "threshold_s":       300,
    }

    # 3. Liquidity
    bid  = float(snapshot.get("option_bid")    or 0)
    ask  = float(snapshot.get("option_ask")    or 0)
    spread = (ask - bid) / ask if ask > 0 else 1.0
    checks["liquidity"] = {
        "passed":        bid > 0 and ask > 0 and spread < max_spread_pct,
        "bid":           bid,
        "ask":           ask,
        "spread_pct":    round(spread, 4),
        "max_spread_pct": max_spread_pct,
    }

    # 4. Trigger validity — price hasn't moved > 2% past the reference level
    price    = float(snapshot.get("price") or 0)
    ref_lvl  = (
        tc.get("vwap_ref") or tc.get("pm_high") or tc.get("pm_low") or
        tc.get("level")    or tc.get("pullback_level") or 0
    )
    ref_lvl = float(ref_lvl)
    if ref_lvl > 0 and price > 0:
        move_pct = abs(price - ref_lvl) / ref_lvl
        passed_tv = move_pct <= 0.02
    else:
        move_pct = 0.0
        passed_tv = True   # no reference level — pass conservatively
    checks["trigger_validity"] = {
        "passed":        passed_tv,
        "price":         price,
        "reference_lvl": ref_lvl,
        "move_pct":      round(move_pct, 4),
        "max_move_pct":  0.02,
    }

    # 5. Duplicate protection — no FILLED plan for same ticker + scan_date + direction
    dup_found = False
    try:
        with psycopg2.connect(db_url, connect_timeout=3) as c, c.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM oe_execution_plans
                WHERE ticker=%s AND scan_date=%s AND direction=%s
                  AND status='FILLED' AND is_test_record=%s
            """, (ticker, scan_date, direction, is_test))
            dup_found = cur.fetchone()[0] > 0
    except Exception as e:
        log.warning(f"[trigger_engine] dup_protection query failed (fail-open): {e}")
    checks["duplicate_protection"] = {
        "passed":          not dup_found,
        "duplicate_found": dup_found,
    }

    # 6. Portfolio limits — open FILLED plans today
    open_pos = 0
    try:
        with psycopg2.connect(db_url, connect_timeout=3) as c, c.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM oe_execution_plans
                WHERE scan_date=%s AND status='FILLED' AND is_test_record=%s
            """, (scan_date, is_test))
            open_pos = cur.fetchone()[0]
    except Exception as e:
        log.warning(f"[trigger_engine] portfolio_limits query failed (fail-open): {e}")
    checks["portfolio_limits"] = {
        "passed":              open_pos < max_open_positions,
        "open_positions":      open_pos,
        "max_open_positions":  max_open_positions,
    }

    # 7. Max loss
    max_risk = float(snapshot.get("max_risk_usd") or 0)
    checks["max_loss"] = {
        "passed":           max_risk <= max_total_risk_usd,
        "max_risk_usd":     max_risk,
        "max_total_risk_usd": max_total_risk_usd,
    }

    # 8. Expected value
    ev = snapshot.get("expected_value")
    if ev is not None:
        ev_ok = float(ev) >= 0
    else:
        ev_ok = True   # not provided → pass
    checks["expected_value"] = {
        "passed":         ev_ok,
        "expected_value": ev,
    }

    all_passed = all(v["passed"] for v in checks.values())
    failed     = [k for k, v in checks.items() if not v["passed"]]

    return all_passed, {
        "checks":     checks,
        "failed":     failed,
        "all_passed": all_passed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# REVALIDATE AND FILL  (advance TRIGGER_MET → FILLED or CANCELLED)
# ─────────────────────────────────────────────────────────────────────────────

def revalidate_and_fill(
    plan_id:         str,
    snapshot:        dict,
    db_url:          str = "",
    champion_config: Optional[dict] = None,
) -> dict:
    """
    Load a TRIGGER_MET plan, run 8-check revalidation, and either:
      - FILLED:    all checks pass → mark plan FILLED, record fill timestamp
      - CANCELLED: any check fails → mark plan CANCELLED, record cancel_reason

    No DELETE/TRUNCATE/UPDATE on external tables.
    The fill action here is a status advance on oe_execution_plans only
    (full integration with aiem_options_alerts is Phase 7 wiring).

    Returns dict: plan_id, ticker, direction, status, revalidation, cancel_reason
    """
    url = db_url or _DB_URL
    now_utc = datetime.now(timezone.utc)

    if champion_config is None:
        # Try to load from phase5; fall back to bare defaults
        try:
            import aiem_options_phase5 as _p5
            champion_config = _p5.get_current_champion_config(url)
        except Exception:
            champion_config = {}

    try:
        with psycopg2.connect(url, connect_timeout=4) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT plan_id, ticker, scan_date, direction,
                           selected_strategy, score_at_plan,
                           trigger_type, trigger_condition,
                           status, plan_created_at, trigger_met_at,
                           is_test_record
                    FROM oe_execution_plans
                    WHERE plan_id = %s
                """, (plan_id,))
                plan = cur.fetchone()
                if not plan:
                    return {"error": f"plan_id {plan_id!r} not found"}

                if plan["status"] != "TRIGGER_MET":
                    return {
                        "plan_id":  plan_id,
                        "ticker":   plan["ticker"],
                        "status":   plan["status"],
                        "error":    f"plan status is {plan['status']!r}, expected TRIGGER_MET",
                    }

                plan_dict = dict(plan)

                # Run revalidation
                all_passed, reval = _run_prefill_revalidation(
                    plan_dict, snapshot, champion_config, url
                )

                if all_passed:
                    new_status   = "FILLED"
                    cancel_reason = None
                    log.info(
                        f"[trigger_engine] FILLED plan_id={plan_id} "
                        f"ticker={plan['ticker']} direction={plan['direction']} "
                        f"all_checks=PASS"
                    )
                else:
                    new_status   = "CANCELLED"
                    cancel_reason = "prefill_revalidation_failed: " + ", ".join(reval["failed"])
                    log.info(
                        f"[trigger_engine] CANCELLED plan_id={plan_id} "
                        f"ticker={plan['ticker']} direction={plan['direction']} "
                        f"failed_checks={reval['failed']}"
                    )

                cur.execute("""
                    UPDATE oe_execution_plans
                    SET status            = %s,
                        revalidation_json = %s,
                        cancel_reason     = %s,
                        fill_ts           = CASE WHEN %s='FILLED' THEN %s ELSE NULL END,
                        status_updated_at = %s
                    WHERE plan_id = %s AND status = 'TRIGGER_MET'
                """, (
                    new_status,
                    json.dumps(reval),
                    cancel_reason,
                    new_status, now_utc,
                    now_utc,
                    plan_id,
                ))
                conn.commit()

        return {
            "plan_id":       plan_id,
            "ticker":        plan["ticker"],
            "direction":     plan["direction"],
            "status":        new_status,
            "revalidation":  reval,
            "cancel_reason": cancel_reason,
            "processed_at":  now_utc.isoformat(),
        }

    except Exception as e:
        log.error(f"[trigger_engine] revalidate_and_fill error plan_id={plan_id}: {e}")
        return {"error": str(e), "plan_id": plan_id}


# ─────────────────────────────────────────────────────────────────────────────
# EXPIRE STALE PLANS  (batch sweep — called by scheduler every 5 min)
# ─────────────────────────────────────────────────────────────────────────────

def expire_stale_plans(db_url: str = "") -> dict:
    """
    Mark all PENDING_EXECUTION_PLAN rows past their expiry as TRIGGER_EXPIRED.
    Returns: {expired_count: int}
    """
    url = db_url or _DB_URL
    now_utc = datetime.now(timezone.utc)
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE oe_execution_plans
                SET status           = 'TRIGGER_EXPIRED',
                    status_updated_at = %s
                WHERE status = 'PENDING_EXECUTION_PLAN'
                  AND plan_expires_at < %s
            """, (now_utc, now_utc))
            n = cur.rowcount
            conn.commit()
        if n:
            log.info(f"[trigger_engine] expire_stale_plans: expired {n} plan(s)")
        return {"expired_count": n}
    except Exception as e:
        log.error(f"[trigger_engine] expire_stale_plans error: {e}")
        return {"error": str(e), "expired_count": 0}


# ─────────────────────────────────────────────────────────────────────────────
# GET PENDING PLANS
# ─────────────────────────────────────────────────────────────────────────────

def get_pending_plans(db_url: str = "", include_test: bool = False) -> List[dict]:
    """
    Return all PENDING_EXECUTION_PLAN rows (non-expired).
    Used by the scheduler job to build the evaluation loop.
    """
    url = db_url or _DB_URL
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT plan_id, ticker, scan_date, direction,
                           trigger_type, trigger_condition,
                           plan_created_at, plan_expires_at, is_test_record
                    FROM oe_execution_plans
                    WHERE status = 'PENDING_EXECUTION_PLAN'
                      AND plan_expires_at > NOW()
                      AND (is_test_record = FALSE OR %s = TRUE)
                    ORDER BY plan_created_at ASC
                """, (include_test,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.error(f"[trigger_engine] get_pending_plans error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# CHECK ALL PENDING PLANS  (called by APScheduler every 5 min)
# ─────────────────────────────────────────────────────────────────────────────

def check_all_pending_plans(
    snapshot_fn,   # callable(ticker: str) -> dict  — fetches live snapshot
    db_url: str = "",
    champion_config: Optional[dict] = None,
) -> dict:
    """
    Scheduler entry point.  For each PENDING plan:
      1. Call snapshot_fn(ticker) to get live market data
      2. Run evaluate_trigger() → TRIGGER_MET or still PENDING
      3. If TRIGGER_MET → run revalidate_and_fill()

    Also runs expire_stale_plans() first.

    Returns summary dict.
    """
    url = db_url or _DB_URL
    bootstrap_trigger_engine(url)

    expire_result = expire_stale_plans(url)
    plans  = get_pending_plans(url, include_test=False)
    summary = {
        "plans_checked":   0,
        "triggers_fired":  0,
        "fills":           0,
        "cancels":         0,
        "expired_this_run": expire_result.get("expired_count", 0),
        "errors":          [],
    }

    if champion_config is None:
        try:
            import aiem_options_phase5 as _p5
            champion_config = _p5.get_current_champion_config(url)
        except Exception:
            champion_config = {}

    for plan in plans:
        summary["plans_checked"] += 1
        plan_id = plan["plan_id"]
        ticker  = plan["ticker"]
        try:
            snap = snapshot_fn(ticker)
            eval_result = evaluate_trigger(plan_id, snap, url)

            if eval_result.get("fired"):
                summary["triggers_fired"] += 1
                fill_result = revalidate_and_fill(plan_id, snap, url, champion_config)
                if fill_result.get("status") == "FILLED":
                    summary["fills"] += 1
                elif fill_result.get("status") == "CANCELLED":
                    summary["cancels"] += 1

        except Exception as e:
            msg = f"plan_id={plan_id} ticker={ticker}: {e}"
            log.error(f"[trigger_engine] check_all_pending_plans error {msg}")
            summary["errors"].append(msg)

    log.info(
        f"[trigger_engine] check_all_pending_plans done: "
        f"checked={summary['plans_checked']} fired={summary['triggers_fired']} "
        f"fills={summary['fills']} cancels={summary['cancels']} "
        f"expired={summary['expired_this_run']}"
    )
    return summary
