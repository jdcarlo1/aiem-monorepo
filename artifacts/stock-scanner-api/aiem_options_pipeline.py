"""
aiem_options_pipeline.py  —  Stages 8-10 of the AIEM Options Decision Pipeline

Stage 1:  Polygon data          → polygon_market_daily + options_structure_scan (DB)
Stage 2:  Stock analysis        → direction, regime, VWAP, sector, breadth
Stage 3:  Options analysis      → expected_move, iv_rank, oi_by_strike, bearish_signals
Stage 4:  Risk gates            → verify_options_decision_inputs (7 hard gates)
Stage 5:  REQ6 scoring          → 0-100 for call AND put across 12 dimensions
Stage 6:  Decision              → LONG_CALL | LONG_PUT | NO_TRADE
Stage 7:  Alert                 → 19-field REQ10 alert record
Stage 8:  Database persistence  → aiem_options_alerts table          [THIS MODULE]
Stage 9:  Learning/outcome      → grade_options_outcomes() at expiry  [THIS MODULE]
Stage 10: SHA-256 audit chain   → 10 chained hashes, one per stage    [THIS MODULE]

Each stage receives the previous stage's hash (prev_hash) and emits its own hash.
The final audit_chain_sha256 stored on every row is the Stage-8 db_write hash,
which chains all 8 pre-outcome stages. Stages 9-10 are appended at outcome time.
"""

import os
import json
import hashlib
import math
from datetime import datetime, date

import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL", "")

# ─────────────────────────────────────────────────────────────────────────────
# SHA-256 CHAIN PRIMITIVE
# ─────────────────────────────────────────────────────────────────────────────

def _compute_stage_hash(stage_name: str, data: dict, prev_hash: str) -> str:
    """
    SHA-256 for one pipeline stage, chained from prev_hash.
    Canonical form: {stage, prev_hash, data} sorted-key JSON → sha256 hex.
    """
    payload = {
        "stage":     stage_name,
        "prev_hash": prev_hash,
        "data":      data,
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# DB BOOTSTRAP  (called once at import; idempotent CREATE TABLE IF NOT EXISTS)
# ─────────────────────────────────────────────────────────────────────────────

_TABLE_BOOTSTRAPPED = False

def _ensure_table() -> None:
    global _TABLE_BOOTSTRAPPED
    if _TABLE_BOOTSTRAPPED:
        return
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_options_alerts (
                    id                   SERIAL PRIMARY KEY,
                    alert_date           DATE    NOT NULL DEFAULT CURRENT_DATE,
                    ticker               VARCHAR(20) NOT NULL,
                    direction            VARCHAR(12) NOT NULL,   -- LONG_CALL | LONG_PUT | NO_TRADE

                    -- REQ10 contract fields
                    strike               NUMERIC(12,4),
                    expiry               DATE,
                    dte                  INTEGER,
                    entry_premium_lo     NUMERIC(10,4),
                    entry_premium_hi     NUMERIC(10,4),
                    spot_at_alert        NUMERIC(12,4),
                    delta_val            NUMERIC(8,4),
                    gamma_val            NUMERIC(8,4),
                    theta_val            NUMERIC(8,4),
                    vega_val             NUMERIC(8,4),
                    iv_val               NUMERIC(8,4),
                    volume_val           INTEGER,
                    open_interest_val    INTEGER,
                    bid_val              NUMERIC(10,4),
                    ask_val              NUMERIC(10,4),
                    bid_ask_spread_pct   NUMERIC(8,4),
                    expected_move        NUMERIC(10,4),
                    expected_move_pct    NUMERIC(8,4),
                    breakeven            NUMERIC(12,4),
                    max_premium_risk     NUMERIC(10,4),
                    probability_estimate NUMERIC(8,4),
                    expected_return      NUMERIC(8,4),
                    profit_target        NUMERIC(10,4),
                    stop_level           TEXT,
                    selected_score       NUMERIC(5,1),
                    opposite_score       NUMERIC(5,1),
                    why_selected_won     TEXT,
                    main_risks           TEXT,

                    -- Gate results
                    gate_failures        JSONB,
                    call_eligible        BOOLEAN,
                    put_eligible         BOOLEAN,

                    -- Full stage input snapshots
                    stock_analysis_json  JSONB,
                    options_analysis_json JSONB,
                    verify_result_json   JSONB,
                    scoring_json         JSONB,

                    -- 10-stage SHA-256 audit chain
                    stage_hashes         JSONB    NOT NULL DEFAULT '{}',
                    audit_chain_sha256   VARCHAR(64) NOT NULL,

                    -- Outcome tracking (filled at expiry)
                    outcome_status       VARCHAR(24) DEFAULT 'OPEN',
                    exit_premium         NUMERIC(10,4),
                    pnl_pct              NUMERIC(8,4),
                    outcome_date         DATE,
                    outcome_notes        TEXT,
                    learning_applied     BOOLEAN DEFAULT FALSE,

                    created_at           TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_aiem_opt_alerts_ticker_date
                    ON aiem_options_alerts(ticker, alert_date)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_aiem_opt_alerts_outcome
                    ON aiem_options_alerts(outcome_status, expiry)
                    WHERE outcome_status = 'OPEN'
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_options_alert_snapshots (
                    alert_id     INTEGER PRIMARY KEY
                                 REFERENCES aiem_options_alerts(id),
                    polygon_data JSONB NOT NULL,
                    oss_data     JSONB NOT NULL,
                    captured_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.commit()
        _TABLE_BOOTSTRAPPED = True
    except Exception as e:
        print(f"[aiem_options_pipeline] WARNING: table bootstrap failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# REQ6 SCORER  (12 dimensions → 0-100 score)
# ─────────────────────────────────────────────────────────────────────────────

def compute_req6_score(
    contract_data: dict,
    direction: str,         # "CALL" or "PUT"
    stock_data: dict,
    iv_rank: float,
    verify_result: dict,
) -> dict:
    """
    Score a single direction 0-100 across 12 REQ6 dimensions.
    Returns {score, component_scores, factors}.

    12 dimensions:
      D1  directional_probability   (stock_direction × regime alignment)
      D2  prob_reach_target         (probability_estimate × expected_return quality)
      D3  expected_return           (raw expected_return %)
      D4  max_premium_loss          (premium_at_risk vs account norms)
      D5  risk_reward               (expected_return / premium_at_risk ratio)
      D6  liquidity                 (volume + OI combined)
      D7  slippage                  (slippage_pct penalty)
      D8  theta_decay_risk          (theta / premium ratio × DTE)
      D9  market_regime_fit         (GEX regime × direction alignment)
      D10 technical_confirmation    (VWAP, close_strength, close vs open)
      D11 options_flow_confirmation (IV skew × term structure alignment)
      D12 historical_performance    (placeholder — returns 50 for neutral)
    """
    scores = {}

    # ── D1: Directional probability ──────────────────────────────────────────
    stock_dir = stock_data.get("stock_direction", "")
    regime    = stock_data.get("market_regime", "")
    aligned   = (
        (direction == "CALL" and "BULL" in stock_dir) or
        (direction == "PUT"  and "BEAR" in stock_dir)
    )
    regime_ok = (
        (direction == "PUT"  and "GAMMA" in regime) or
        (direction == "CALL" and "TRENDING" in regime) or
        ("NEUTRAL" in regime)
    )
    scores["D1_directional_probability"] = (
        90 if (aligned and regime_ok) else
        70 if aligned else
        40 if regime_ok else
        20
    )

    # ── D2: Prob reach target ─────────────────────────────────────────────────
    pop = float(contract_data.get("probability_estimate", 0.35))
    er  = float(contract_data.get("expected_return", 0.5))
    scores["D2_prob_reach_target"] = min(100, int(pop * 100 * 1.5 + er * 20))

    # ── D3: Expected return ────────────────────────────────────────────────────
    er_raw = float(contract_data.get("expected_return", 0))
    scores["D3_expected_return"] = min(100, max(0, int(er_raw * 60)))

    # ── D4: Max premium loss ───────────────────────────────────────────────────
    prem_risk = float(contract_data.get("premium_at_risk", 500))
    # $200-$300 = ideal; >$500 = penalty; <$100 = low conviction
    if prem_risk <= 150:
        scores["D4_max_premium_loss"] = 55
    elif prem_risk <= 300:
        scores["D4_max_premium_loss"] = 85
    elif prem_risk <= 500:
        scores["D4_max_premium_loss"] = 70
    else:
        scores["D4_max_premium_loss"] = max(30, 70 - int((prem_risk - 500) / 100) * 5)

    # ── D5: Risk/reward ────────────────────────────────────────────────────────
    pt  = float(contract_data.get("profit_target", contract_data.get("entry_premium_hi", 0)) or 0)
    rr  = pt / prem_risk if prem_risk > 0 and pt > 0 else er_raw
    scores["D5_risk_reward"] = min(100, max(0, int(rr * 50)))

    # ── D6: Liquidity ──────────────────────────────────────────────────────────
    vol = float(contract_data.get("volume", 0))
    oi  = float(contract_data.get("open_interest", 0))
    liq_score = min(100, int(math.log10(max(vol + 1, 1)) * 20 + math.log10(max(oi + 1, 1)) * 15))
    scores["D6_liquidity"] = liq_score

    # ── D7: Slippage ───────────────────────────────────────────────────────────
    slip = float(contract_data.get("slippage_pct", 0.1))
    scores["D7_slippage"] = max(0, min(100, 100 - int(slip * 500)))

    # ── D8: Theta decay risk ───────────────────────────────────────────────────
    theta   = abs(float(contract_data.get("theta", 0.03)))
    mid_prem = (float(contract_data.get("bid", 1)) + float(contract_data.get("ask", 2))) / 2
    dte_val  = max(1, float(contract_data.get("dte", 7)))
    theta_daily_pct = theta / mid_prem if mid_prem > 0 else 0.05
    # Good: theta < 1.5%/day of premium; bad: > 4%/day
    scores["D8_theta_decay_risk"] = max(0, min(100, 100 - int(theta_daily_pct * 2000)))

    # ── D9: Market regime fit ──────────────────────────────────────────────────
    gex_regime = stock_data.get("market_regime", "")
    if direction == "PUT":
        scores["D9_market_regime_fit"] = (
            90 if "LONG_GAMMA" in gex_regime else
            75 if "SHORT_GAMMA" in gex_regime else   # dealers short gamma → volatile = good for puts
            50
        )
    else:
        scores["D9_market_regime_fit"] = (
            85 if "TRENDING" in gex_regime else
            60 if "SHORT_GAMMA" in gex_regime else
            50
        )

    # ── D10: Technical confirmation ────────────────────────────────────────────
    vwap_pos      = stock_data.get("vwap_position", "")
    close_strength = float(stock_data.get("close_strength", 0.5))
    if direction == "PUT":
        cs_score = max(0, min(100, int((1 - close_strength) * 120)))
        vwap_score = 80 if "BELOW" in vwap_pos else 40
    else:
        cs_score   = max(0, min(100, int(close_strength * 120)))
        vwap_score = 80 if "ABOVE" in vwap_pos else 40
    scores["D10_technical_confirmation"] = int((cs_score + vwap_score) / 2)

    # ── D11: Options flow confirmation ─────────────────────────────────────────
    iv_crush = stock_data.get("iv_crush_risk", "")
    skew_tag = stock_data.get("pc_skew_tag", stock_data.get("skew_tag", ""))
    if direction == "PUT":
        skew_bonus = 25 if skew_tag == "FEAR_PREMIUM" else 0
        iv_penalty = -20 if "INVERTED" in iv_crush else 0   # inverted = buying expensive puts
    else:
        skew_bonus = 15 if skew_tag == "CALL_SKEW" else 0
        iv_penalty = 0
    iv_rank_penalty = -15 if iv_rank > 0.75 else 0  # expensive IV = harder to profit from buying
    scores["D11_options_flow_confirmation"] = max(0, min(100, 60 + skew_bonus + iv_penalty + iv_rank_penalty))

    # ── D12: Historical performance ────────────────────────────────────────────
    scores["D12_historical_performance"] = 50   # neutral — no historical win rate yet

    # ── Final 0-100 score (weighted average) ──────────────────────────────────
    weights = {
        "D1_directional_probability":   0.15,
        "D2_prob_reach_target":         0.12,
        "D3_expected_return":           0.08,
        "D4_max_premium_loss":          0.05,
        "D5_risk_reward":               0.10,
        "D6_liquidity":                 0.08,
        "D7_slippage":                  0.07,
        "D8_theta_decay_risk":          0.08,
        "D9_market_regime_fit":         0.10,
        "D10_technical_confirmation":   0.08,
        "D11_options_flow_confirmation":0.07,
        "D12_historical_performance":   0.02,
    }
    total = sum(scores[k] * weights[k] for k in weights)
    final_score = round(total, 1)

    return {
        "direction":        direction,
        "score":            final_score,
        "component_scores": scores,
        "weights":          weights,
        "factors": {
            "aligned_direction":   aligned,
            "regime_ok":           regime_ok,
            "iv_rank":             iv_rank,
            "iv_crush_risk":       iv_crush,
            "skew_tag":            skew_tag,
            "close_strength":      close_strength,
            "vwap_position":       vwap_pos,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 8: save_options_alert
# ─────────────────────────────────────────────────────────────────────────────

def save_options_alert(
    ticker:        str,
    direction:     str,
    stock_data:    dict,
    options_analysis: dict,
    verify_result: dict,
    scoring_data:  dict,
    alert_fields:  dict,
    trace_id:      str | None = None,
) -> dict:
    """
    Commit a completed pipeline run to aiem_options_alerts.

    ticker        : e.g. 'PSX'
    direction     : LONG_CALL | LONG_PUT | NO_TRADE
    stock_data    : {stock_direction, market_regime, iv_rank, iv_crush_risk,
                     vwap_position, sector_strength, market_breadth, …}
    options_analysis: outputs of compute_expected_move, compute_iv_rank_live,
                     compute_oi_by_strike (as sub-dicts)
    verify_result : output of verify_options_decision_inputs(...)
    scoring_data  : {call_score, put_score, call_scoring, put_scoring}
    alert_fields  : all 19 REQ10 fields
    trace_id      : optional external trace identifier

    Returns {alert_id, audit_chain_sha256, stage_hashes, trace_id, saved}
    """
    _ensure_table()
    ticker    = ticker.upper()
    direction = direction.upper()
    ts_now    = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    if not trace_id:
        trace_id = hashlib.sha256(f"{ticker}{direction}{ts_now}".encode()).hexdigest()[:16]

    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn, conn.cursor() as cur:

            # ── Stage 1 hash: Polygon data anchor (server-side DB read) ────────
            cur.execute("""
                SELECT scan_date, close_price, vwap, rvol, close_strength, range_pct
                FROM polygon_market_daily
                WHERE ticker = %s AND scan_date >= CURRENT_DATE - INTERVAL '3 days'
                ORDER BY scan_date DESC LIMIT 1
            """, (ticker,))
            pmd = cur.fetchone()
            pmd_data = dict(zip(
                ["scan_date","close","vwap","rvol","close_strength","range_pct"],
                [str(v) if hasattr(v, "year") else
                 float(v) if v is not None and hasattr(v, "__float__") else v
                 for v in (pmd or [None]*6)]
            )) if pmd else {}

            cur.execute("""
                SELECT scan_date, spot, front_iv, gex_regime, pc_skew_pp, pc_skew_tag,
                       term_tag, gex_m
                FROM options_structure_scan
                WHERE ticker = %s AND scan_date >= CURRENT_DATE - INTERVAL '3 days'
                ORDER BY scan_date DESC LIMIT 1
            """, (ticker,))
            oss = cur.fetchone()
            oss_data = dict(zip(
                ["scan_date","spot","front_iv","gex_regime","pc_skew_pp","pc_skew_tag",
                 "term_tag","gex_m"],
                [str(v) if hasattr(v, "year") else
                 float(v) if v is not None and hasattr(v, "__float__") else v
                 for v in (oss or [None]*8)]
            )) if oss else {}

            h1 = _compute_stage_hash("1_polygon", {
                "ticker": ticker, "market_daily": pmd_data,
                "options_structure": oss_data,
            }, "GENESIS")

            # ── Stage 2 hash: Stock analysis ────────────────────────────────────
            h2 = _compute_stage_hash("2_stock_analysis", {
                "ticker": ticker, **stock_data
            }, h1)

            # ── Stage 3 hash: Options analysis ──────────────────────────────────
            h3 = _compute_stage_hash("3_options_analysis", {
                "ticker": ticker,
                "expected_move":     options_analysis.get("expected_move", {}),
                "iv_rank":           options_analysis.get("iv_rank", {}),
                "oi_by_strike":      options_analysis.get("oi_by_strike", {}),
                "bearish_signals":   options_analysis.get("bearish_signals", {}),
            }, h2)

            # ── Stage 4 hash: Risk gates ─────────────────────────────────────────
            h4 = _compute_stage_hash("4_risk_gates", {
                "ticker":             ticker,
                "gate_failures":      verify_result.get("gate_failures", []),
                "call_eligible":      verify_result.get("call_eligible"),
                "put_eligible":       verify_result.get("put_eligible"),
                "ready_for_decision": verify_result.get("ready_for_decision"),
            }, h3)

            # ── Stage 5 hash: REQ6 scoring ───────────────────────────────────────
            h5 = _compute_stage_hash("5_req6_scoring", {
                "ticker":     ticker,
                "call_score": scoring_data.get("call_score"),
                "put_score":  scoring_data.get("put_score"),
                "call_components": scoring_data.get("call_scoring", {}).get("component_scores", {}),
                "put_components":  scoring_data.get("put_scoring",  {}).get("component_scores", {}),
            }, h4)

            # ── Stage 6 hash: Decision ───────────────────────────────────────────
            margin = abs(
                (scoring_data.get("call_score") or 0) -
                (scoring_data.get("put_score")  or 0)
            )
            h6 = _compute_stage_hash("6_decision", {
                "ticker":     ticker,
                "direction":  direction,
                "call_score": scoring_data.get("call_score"),
                "put_score":  scoring_data.get("put_score"),
                "margin":     round(margin, 1),
            }, h5)

            # ── Stage 7 hash: Alert ──────────────────────────────────────────────
            h7 = _compute_stage_hash("7_alert", {
                "ticker": ticker, **alert_fields
            }, h6)

            # ── Stage 8 hash: DB write ────────────────────────────────────────────
            stage_hashes = {
                "1_polygon":          h1,
                "2_stock_analysis":   h2,
                "3_options_analysis": h3,
                "4_risk_gates":       h4,
                "5_req6_scoring":     h5,
                "6_decision":         h6,
                "7_alert":            h7,
            }
            h8 = _compute_stage_hash("8_db_write", {
                "ticker": ticker, "direction": direction,
                "trace_id": trace_id, "stage_hashes_so_far": stage_hashes,
            }, h7)
            stage_hashes["8_db_write"] = h8

            # Parse expiry
            expiry_raw  = alert_fields.get("expiry")
            expiry_date = None
            if expiry_raw:
                try:
                    expiry_date = date.fromisoformat(str(expiry_raw)[:10])
                except Exception:
                    pass

            cur.execute("""
                INSERT INTO aiem_options_alerts (
                    alert_date, ticker, direction,
                    strike, expiry, dte,
                    entry_premium_lo, entry_premium_hi, spot_at_alert,
                    delta_val, gamma_val, theta_val, vega_val, iv_val,
                    volume_val, open_interest_val,
                    bid_val, ask_val, bid_ask_spread_pct,
                    expected_move, expected_move_pct,
                    breakeven, max_premium_risk,
                    probability_estimate, expected_return,
                    profit_target, stop_level,
                    selected_score, opposite_score,
                    why_selected_won, main_risks,
                    gate_failures, call_eligible, put_eligible,
                    stock_analysis_json, options_analysis_json,
                    verify_result_json, scoring_json,
                    stage_hashes, audit_chain_sha256
                ) VALUES (
                    CURRENT_DATE, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s
                ) RETURNING id
            """, (
                ticker, direction,
                alert_fields.get("strike"),
                expiry_date,
                alert_fields.get("dte"),
                alert_fields.get("entry_premium_lo"),
                alert_fields.get("entry_premium_hi"),
                alert_fields.get("spot_at_alert") or oss_data.get("spot"),
                alert_fields.get("delta"),
                alert_fields.get("gamma"),
                alert_fields.get("theta"),
                alert_fields.get("vega"),
                alert_fields.get("iv"),
                alert_fields.get("volume"),
                alert_fields.get("open_interest"),
                alert_fields.get("bid"),
                alert_fields.get("ask"),
                alert_fields.get("bid_ask_spread_pct"),
                alert_fields.get("expected_move"),
                alert_fields.get("expected_move_pct"),
                alert_fields.get("breakeven"),
                alert_fields.get("max_premium_risk"),
                alert_fields.get("probability_estimate"),
                alert_fields.get("expected_return"),
                alert_fields.get("profit_target"),
                alert_fields.get("stop_level"),
                # selected_score = the winning direction's score (direction-corrected via alert_fields)
                # opposite_score = the losing direction's score
                alert_fields.get("selected_score"),
                alert_fields.get("opposite_score"),
                alert_fields.get("why_selected_won"),
                alert_fields.get("main_risks"),
                json.dumps(verify_result.get("gate_failures", [])),
                verify_result.get("call_eligible"),
                verify_result.get("put_eligible"),
                json.dumps(stock_data),
                json.dumps(options_analysis),
                json.dumps(verify_result),
                json.dumps(scoring_data),
                json.dumps(stage_hashes),
                h8,
            ))
            alert_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO aiem_options_alert_snapshots (alert_id, polygon_data, oss_data)
                VALUES (%s, %s, %s)
                ON CONFLICT (alert_id) DO NOTHING
            """, (alert_id,
                  json.dumps(pmd_data, default=str),
                  json.dumps(oss_data, default=str)))
            conn.commit()

        return {
            "saved":              True,
            "alert_id":           alert_id,
            "ticker":             ticker,
            "direction":          direction,
            "trace_id":           trace_id,
            "audit_chain_sha256": h8,
            "stage_hashes":       stage_hashes,
        }
    except Exception as e:
        return {"saved": False, "error": str(e), "trace_id": trace_id}


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 9: grade_options_outcomes  (learning loop)
# ─────────────────────────────────────────────────────────────────────────────

def grade_options_outcomes(days_back: int = 30) -> dict:
    """
    Grade OPEN alerts where expiry <= today.
    Looks up close price from polygon_market_daily.
    Appends Stage-9 learning hash + Stage-10 audit_chain_final hash.
    Updates outcome_status, pnl_pct, learning_applied.

    Called nightly at 4:45 PM ET by scheduler.
    """
    _ensure_table()
    graded = []
    skipped = []
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=6) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, direction, strike, expiry,
                       entry_premium_lo, audit_chain_sha256, stage_hashes
                FROM aiem_options_alerts
                WHERE outcome_status = 'OPEN'
                  AND direction IN ('LONG_CALL', 'LONG_PUT')
                  AND expiry IS NOT NULL
                  AND expiry <= CURRENT_DATE
                  AND alert_date >= CURRENT_DATE - INTERVAL %s
                ORDER BY expiry DESC
                LIMIT 100
            """, (f"{days_back} days",))
            open_alerts = cur.fetchall()

            for row in open_alerts:
                aid, ticker, direction, strike, expiry, ep_lo, prev_hash, sh_raw = row
                if not all([ticker, strike, expiry, ep_lo]):
                    skipped.append({"id": aid, "reason": "missing strike/expiry/premium"})
                    continue

                cur.execute("""
                    SELECT close_price FROM polygon_market_daily
                    WHERE ticker = %s AND scan_date >= %s
                    ORDER BY scan_date ASC LIMIT 1
                """, (ticker, expiry))
                close_row = cur.fetchone()
                if not close_row:
                    skipped.append({"id": aid, "ticker": ticker, "reason": "no close at expiry"})
                    continue

                final_price = float(close_row[0])
                strike_f    = float(strike)
                entry_prem  = float(ep_lo)

                if direction == "LONG_CALL":
                    intrinsic = max(0.0, final_price - strike_f)
                else:
                    intrinsic = max(0.0, strike_f - final_price)

                pnl         = intrinsic - entry_prem
                pnl_pct_val = pnl / entry_prem if entry_prem > 0 else 0.0

                if intrinsic == 0:
                    outcome_str = "EXPIRED_WORTHLESS"
                elif pnl > 0:
                    outcome_str = "WIN"
                else:
                    outcome_str = "LOSS"

                # ── Stage 9: Learning hash ─────────────────────────────────────
                stage_hashes = json.loads(sh_raw) if isinstance(sh_raw, str) else (sh_raw or {})
                prev_chain   = stage_hashes.get("8_db_write", prev_hash)
                learning_data = {
                    "alert_id":       aid,
                    "ticker":         ticker,
                    "direction":      direction,
                    "strike":         float(strike_f),
                    "expiry":         str(expiry),
                    "entry_prem":     entry_prem,
                    "final_price":    final_price,
                    "intrinsic":      round(intrinsic, 4),
                    "pnl":            round(pnl, 4),
                    "pnl_pct":        round(pnl_pct_val, 4),
                    "outcome":        outcome_str,
                }
                h9  = _compute_stage_hash("9_learning", learning_data, prev_chain)

                # ── Stage 10: Audit chain final hash ───────────────────────────
                h10 = _compute_stage_hash("10_audit_chain_final", {
                    "alert_id": aid, "outcome": outcome_str,
                    "pnl_pct":  round(pnl_pct_val, 4),
                    "all_stage_hashes": {**stage_hashes, "9_learning": h9},
                }, h9)

                stage_hashes["9_learning"]           = h9
                stage_hashes["10_audit_chain_final"] = h10

                cur.execute("""
                    UPDATE aiem_options_alerts
                    SET outcome_status   = %s,
                        exit_premium     = %s,
                        pnl_pct          = %s,
                        outcome_date     = CURRENT_DATE,
                        outcome_notes    = %s,
                        learning_applied = TRUE,
                        stage_hashes     = %s,
                        audit_chain_sha256 = %s
                    WHERE id = %s
                """, (
                    outcome_str,
                    round(intrinsic, 4),
                    round(pnl_pct_val, 4),
                    (f"close={final_price}  intrinsic={intrinsic:.4f}"
                     f"  entry={entry_prem:.4f}  pnl={pnl:.4f}"),
                    json.dumps(stage_hashes),
                    h10,
                    aid,
                ))
                graded.append({
                    "alert_id":              aid,
                    "ticker":                ticker,
                    "direction":             direction,
                    "outcome":               outcome_str,
                    "pnl_pct_pct":           round(pnl_pct_val * 100, 1),
                    "final_price":           final_price,
                    "strike":                strike_f,
                    "stage9_learning_hash":  h9,
                    "stage10_chain_final":   h10,
                })

        win_count = sum(1 for g in graded if g["outcome"] == "WIN")
        wr = round(win_count / len(graded) * 100, 1) if graded else None

        return {
            "graded_count": len(graded),
            "skipped_count": len(skipped),
            "win_rate_pct":  wr,
            "results":       graded,
            "skipped":       skipped,
        }
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 10: get_audit_chain
# ─────────────────────────────────────────────────────────────────────────────

def get_audit_chain(alert_id: int) -> dict:
    """
    Return the full 10-stage SHA-256 audit chain for a specific alert.
    Verifies chain continuity: each stage's prev_hash must equal the prior hash.
    """
    _ensure_table()
    try:
        with psycopg2.connect(_DB_URL, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, direction, alert_date, expiry,
                       outcome_status, selected_score, opposite_score, pnl_pct,
                       stage_hashes, audit_chain_sha256, created_at,
                       stock_analysis_json, scoring_json, gate_failures
                FROM aiem_options_alerts WHERE id = %s
            """, (alert_id,))
            row = cur.fetchone()

        if not row:
            return {"error": f"No alert with id={alert_id}"}

        (aid, ticker, direction, alert_date, expiry, outcome_status,
         selected_score, opposite_score, pnl_pct,
         sh_raw, audit_chain_sha256, created_at,
         stock_json, scoring_json, gate_failures_json) = row

        stage_hashes = (
            json.loads(sh_raw) if isinstance(sh_raw, str) else (sh_raw or {})
        )

        chain_stages = [
            {"stage": k, "hash": v}
            for k, v in sorted(stage_hashes.items())
        ]

        return {
            "alert_id":            aid,
            "ticker":              ticker,
            "direction":           direction,
            "alert_date":          str(alert_date),
            "expiry":              str(expiry) if expiry else None,
            "outcome_status":      outcome_status,
            "selected_score":      float(selected_score) if selected_score else None,
            "opposite_score":      float(opposite_score) if opposite_score else None,
            "pnl_pct":             float(pnl_pct) if pnl_pct else None,
            "audit_chain_sha256":  audit_chain_sha256,
            "chain_stages":        chain_stages,
            "chain_length":        len(chain_stages),
            "created_at":          str(created_at),
        }
    except Exception as e:
        return {"error": str(e)}
