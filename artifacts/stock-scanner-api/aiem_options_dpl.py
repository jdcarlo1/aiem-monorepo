"""
aiem_options_dpl.py — Decision Proof Layer (DPL)
  Phase 1: Immutable Audit Record
  Phase 2: Decision-Context + Justification Capture

Scope isolation: oe_decision_audit only. No D1/D2/D3 tables touched.
No execution-quality fields (fill probability, slippage, commission) — paper-mode only.
"""

import hashlib
import inspect
import json
import logging
import os
import uuid
from typing import Optional

import psycopg2

log = logging.getLogger("aiem_options_dpl")

_DB_URL    = os.environ.get("DATABASE_URL", "")
_DPL_TABLE = "oe_decision_audit"

_ENGINE_VERSION_FALLBACK = "no_active_champion"
_DB_VERSION_FALLBACK     = "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _conn(db_url=None):
    url = db_url or _DB_URL
    return psycopg2.connect(url, connect_timeout=8,
                            options="-c statement_timeout=15000")


def _sha256(data: dict) -> str:
    """Deterministic SHA-256 of JSON (keys sorted for stability)."""
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _live_engine_version(cur) -> str:
    """Read active champion version_id from oe_model_versions (live source, not hardcoded)."""
    cur.execute(
        "SELECT version_id FROM oe_model_versions "
        "WHERE is_active = TRUE AND is_test_record = FALSE "
        "LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else _ENGINE_VERSION_FALLBACK


def _live_db_version(cur) -> str:
    """Read PostgreSQL version from live server (not hardcoded)."""
    cur.execute("SELECT split_part(version(), ' ', 2)")
    row = cur.fetchone()
    return row[0] if row else _DB_VERSION_FALLBACK


def _post_write_integrity_check(cur, decision_id: str,
                                 expected_input_hash: str,
                                 expected_output_hash: str) -> bool:
    """
    Reject-on-integrity-failure gate: re-read stored hashes immediately after
    INSERT and compare against expected values. Raises ValueError on mismatch.
    Returns True when verified.
    """
    cur.execute(
        "SELECT input_hash, output_hash FROM oe_decision_audit "
        "WHERE decision_id = %s",
        (decision_id,)
    )
    stored = cur.fetchone()
    if stored is None:
        raise ValueError(
            f"DPL integrity gate: row absent after INSERT "
            f"(decision_id={decision_id})"
        )
    stored_input, stored_output = stored
    if stored_input != expected_input_hash or stored_output != expected_output_hash:
        raise ValueError(
            f"DPL integrity gate: hash mismatch after INSERT — "
            f"input_match={stored_input == expected_input_hash} "
            f"output_match={stored_output == expected_output_hash}"
        )
    return True


def _safe_float(v, default=None):
    """Convert to float, return default on failure."""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP  (Phase 1 + Phase 2 schema)
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_dpl(db_url=None) -> bool:
    """
    Create oe_decision_audit table + Phase 2 context columns + immutability trigger.
    Idempotent. Returns True on success.

    Immutability model (Phase 1 unchanged, Phase 2 extends trigger):
      - Test rows (is_test_record=TRUE): DELETE and UPDATE freely permitted.
      - Production rows (is_test_record=FALSE):
          DELETE  → always blocked.
          UPDATE  → only verification_status may change; all other fields immutable
                    (including the five Phase 2 JSONB context columns).
    """
    conn = _conn(db_url)
    try:
        with conn.cursor() as cur:
            # Phase 1 table (idempotent)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_DPL_TABLE} (
                    decision_id         TEXT        PRIMARY KEY,
                    parent_id           TEXT        REFERENCES {_DPL_TABLE}(decision_id),
                    created_at          TIMESTAMPTZ NOT NULL
                                        DEFAULT (NOW() AT TIME ZONE 'UTC'),
                    input_hash          TEXT        NOT NULL,
                    output_hash         TEXT        NOT NULL,
                    verification_status TEXT        NOT NULL DEFAULT 'PENDING'
                                        CHECK (verification_status
                                               IN ('VERIFIED', 'PENDING', 'TAMPERED')),
                    engine_version      TEXT        NOT NULL,
                    db_version          TEXT        NOT NULL,
                    is_test_record      BOOLEAN     NOT NULL DEFAULT FALSE
                )
            """)

            # Phase 2: add five context columns (idempotent — ADD COLUMN IF NOT EXISTS)
            for _col in ("identity_json", "technical_json", "options_intel_json",
                         "probability_risk_json", "justification_json"):
                cur.execute(f"""
                    ALTER TABLE {_DPL_TABLE}
                    ADD COLUMN IF NOT EXISTS {_col} JSONB
                """)

            # Phase 2 trigger: immutability extended to include context columns
            cur.execute("""
                CREATE OR REPLACE FUNCTION _oe_dpl_guard_immutability()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    -- Test records: permit DELETE and UPDATE freely
                    IF TG_OP = 'DELETE' THEN
                        IF OLD.is_test_record THEN
                            RETURN OLD;
                        END IF;
                        RAISE EXCEPTION
                            'oe_decision_audit is append-only: '
                            'DELETE not permitted on production rows';
                    END IF;

                    -- UPDATE: test records unrestricted
                    IF OLD.is_test_record THEN
                        RETURN NEW;
                    END IF;

                    -- UPDATE: production records — core + Phase 2 context fields are immutable
                    IF NEW.decision_id              IS DISTINCT FROM OLD.decision_id              OR
                       NEW.parent_id                IS DISTINCT FROM OLD.parent_id                OR
                       NEW.created_at               IS DISTINCT FROM OLD.created_at               OR
                       NEW.input_hash               IS DISTINCT FROM OLD.input_hash               OR
                       NEW.output_hash              IS DISTINCT FROM OLD.output_hash              OR
                       NEW.engine_version           IS DISTINCT FROM OLD.engine_version           OR
                       NEW.db_version               IS DISTINCT FROM OLD.db_version               OR
                       NEW.is_test_record           IS DISTINCT FROM OLD.is_test_record           OR
                       NEW.identity_json            IS DISTINCT FROM OLD.identity_json            OR
                       NEW.technical_json           IS DISTINCT FROM OLD.technical_json           OR
                       NEW.options_intel_json       IS DISTINCT FROM OLD.options_intel_json       OR
                       NEW.probability_risk_json    IS DISTINCT FROM OLD.probability_risk_json    OR
                       NEW.justification_json       IS DISTINCT FROM OLD.justification_json
                    THEN
                        RAISE EXCEPTION
                            'oe_decision_audit: core fields are immutable '
                            '(only verification_status may be updated on production rows)';
                    END IF;
                    RETURN NEW;
                END;
                $$
            """)

            cur.execute(
                "DROP TRIGGER IF EXISTS trg_oe_dpl_immutable ON oe_decision_audit"
            )
            cur.execute("""
                CREATE TRIGGER trg_oe_dpl_immutable
                BEFORE UPDATE OR DELETE ON oe_decision_audit
                FOR EACH ROW EXECUTE FUNCTION _oe_dpl_guard_immutability()
            """)
        conn.commit()
        bootstrap_dpl_phase3(db_url)           # Phase 3: replay inputs table (idempotent)
        bootstrap_governance_tables(db_url)    # Phase 3 P2: governance tables (idempotent)
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2: CONTEXT ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────────

def assemble_dpl_context(
    ticker:              str,
    scan_date,
    trace_id:            str,
    direction:           str,
    alert_id:            Optional[int]   = None,
    sel_data:            Optional[dict]  = None,
    stock_data:          Optional[dict]  = None,
    verify_result:       Optional[dict]  = None,
    chain_strategies:    Optional[list]  = None,
    best_chain_strategy: Optional[dict]  = None,
    sel_strike:          Optional[float] = None,
    expiry_str:          Optional[str]   = None,
    alert_fields:        Optional[dict]  = None,
    pm_intel:            Optional[dict]  = None,
    mtf_result:          Optional[dict]  = None,
    pattern_result:      Optional[dict]  = None,
    em_result:           Optional[dict]  = None,
    ivr_result:          Optional[dict]  = None,
    call_score:          Optional[float] = None,
    put_score:           Optional[float] = None,
    db_url:              Optional[str]   = None,
) -> dict:
    """
    Assemble the five Phase 2 context blobs from in-memory pipeline data.

    Every field traces to a live computed value or is explicitly flagged with
    _flag/  _reason keys.  Flagged fields (not computed in pipeline):
      - capital_preservation_score  NOT_PER_DECISION
      - capital_efficiency_score    NOT_PER_DECISION
      - time_based_exit_rules       PARTIAL (DTE captured; rules not pre-computed)
      - adjustment_rolling_rules    NOT_COMPUTED
      - invalidation_conditions     PARTIAL (main_risks free-text only)

    Returns dict with keys: identity, technical, options_intel,
    probability_risk, justification.
    """
    sel_data          = sel_data or {}
    stock_data        = stock_data or {}
    verify_result     = verify_result or {}
    chain_strategies  = chain_strategies or []
    alert_fields      = alert_fields or {}
    pm_intel          = pm_intel or {}
    mtf_result        = mtf_result or {}
    pattern_result    = pattern_result or {}
    em_result         = em_result or {}
    ivr_result        = ivr_result or {}

    # ── DB lookups for fields not in memory ──────────────────────────────────
    _confidence_score = None
    _iv_percentile    = None
    _liquidity_score  = None
    _portfolio_ctx    = {}
    try:
        with _conn(db_url) as _dc, _dc.cursor() as _dcur:
            # source: oe_knowledge_base.confidence_score (Phase 3 add_knowledge_base_entry)
            _dcur.execute("""
                SELECT confidence_score FROM oe_knowledge_base
                WHERE ticker = %s AND scan_date = %s
                ORDER BY created_at DESC LIMIT 1
            """, (ticker, scan_date))
            _r = _dcur.fetchone()
            _confidence_score = _r[0] if _r else None

            # source: oe_options_metrics.iv_percentile (snapped by registry via _rc)
            _dcur.execute("""
                SELECT iv_percentile FROM oe_options_metrics
                WHERE trace_id = %s AND direction = %s
                ORDER BY captured_at DESC LIMIT 1
            """, (trace_id, direction))
            _r = _dcur.fetchone()
            _iv_percentile = _safe_float(_r[0]) if _r else None

            # source: oe_strategy_candidates.liquidity_score (Phase 2 capture_strategy_candidates)
            _dcur.execute("""
                SELECT liquidity_score FROM oe_strategy_candidates
                WHERE trace_id = %s AND selected = TRUE
                ORDER BY captured_at DESC LIMIT 1
            """, (trace_id,))
            _r = _dcur.fetchone()
            _liquidity_score = _safe_float(_r[0]) if _r else None

            # source: oe_portfolio_context (capture_portfolio_context, Phase 4)
            _dcur.execute("""
                SELECT portfolio_delta, portfolio_gamma, portfolio_theta,
                       portfolio_vega, n_open_positions, total_max_risk_usd,
                       ticker_concentration, violated_limits, any_violation
                FROM oe_portfolio_context
                WHERE trace_id = %s
                ORDER BY snapshot_ts DESC LIMIT 1
            """, (trace_id,))
            _r = _dcur.fetchone()
            if _r:
                _portfolio_ctx = {
                    "portfolio_delta":    _safe_float(_r[0]),
                    "portfolio_gamma":    _safe_float(_r[1]),
                    "portfolio_theta":    _safe_float(_r[2]),
                    "portfolio_vega":     _safe_float(_r[3]),
                    "n_open_positions":   _r[4],
                    "total_max_risk_usd": _safe_float(_r[5]),
                    "ticker_concentration": _r[6],
                    "violated_limits":    _r[7],
                    "any_violation":      _r[8],
                }
    except Exception as _dbe:
        log.warning(f"[dpl] assemble_dpl_context DB lookups partial: {_dbe}")

    # ── Derive composite values ───────────────────────────────────────────────
    _selected_strategy = (
        best_chain_strategy.get("strategy") if best_chain_strategy else direction
    )
    _legs = (
        best_chain_strategy.get("legs", []) if best_chain_strategy
        else [{"action": "BUY",
               "type":   direction.replace("LONG_", ""),
               "strike": sel_strike,
               "expiry": expiry_str}]
    )
    _strikes  = [leg.get("strike") for leg in _legs if leg.get("strike")]
    _expiries = list(dict.fromkeys(
        leg.get("expiry") or leg.get("expiration")
        for leg in _legs
        if leg.get("expiry") or leg.get("expiration")
    ))
    _premium_at_risk = _safe_float(
        sel_data.get("premium_at_risk") or alert_fields.get("max_premium_risk")
    )
    _max_risk   = _premium_at_risk
    _max_reward = _safe_float(
        sel_data.get("profit_target") or alert_fields.get("profit_target")
    )
    _rr = (
        round(_max_reward / _max_risk, 3)
        if (_max_risk and _max_reward and _max_risk != 0)
        else None
    )
    _rejected_candidates = [
        {
            "strategy":        c.get("strategy") or c.get("strategy_id"),
            "rejection_reason": c.get("rejection_reason") or c.get("reason"),
        }
        for c in chain_strategies if c.get("rejected")
    ]

    # ── 1. Identity / Context ─────────────────────────────────────────────────
    # Every sub-field: source stated in inline comment
    identity = {
        # source: _execute_job local ticker
        "ticker":     ticker,
        # source: _execute_job scan_date param
        "scan_date":  str(scan_date),
        # source: SHA-256 of ticker+scan_date+claim_id (_execute_job line 652)
        "trace_id":   trace_id,
        # source: REQ6 direction scorer (Stage 6)
        "direction":  direction,
        # source: save_options_alert → aiem_options_alerts.id
        "alert_id":   alert_id,
        # source: best_chain_strategy.strategy (Phase 2 chain selection) or direction
        "selected_strategy": _selected_strategy,
        # source: best_chain_strategy.legs[].strike OR sel_strike (Stage 7)
        "strikes":     _strikes or ([sel_strike] if sel_strike else []),
        # source: best_chain_strategy.legs[].expiry OR expiry_str (Stage 7)
        "expiration":  _expiries[0] if _expiries else expiry_str,
        # source: sel_data.premium_at_risk × 100 (1 contract = 100 shares)
        "position_size_usd": (
            round(_premium_at_risk * 100, 2) if _premium_at_risk is not None else None
        ),
        # source: stock_data.market_regime = gex_regime from Stage 2 (options_structure_scan)
        "market_regime": (
            stock_data.get("market_regime") or stock_data.get("gex_regime")
        ),
        # source: ivr_result.iv_label from compute_iv_rank_live (Stage 3)
        "volatility_regime": ivr_result.get("iv_label"),
        # source: options_engine_premarket table via aiem_premarket_intel.get_premarket_intel (Stage 1)
        "premarket_conditions": {
            "premarket_score":     pm_intel.get("premarket_score"),
            "premarket_direction": pm_intel.get("premarket_direction"),
            "premarket_confidence":pm_intel.get("premarket_confidence"),
            "pm_rvol":             pm_intel.get("pm_rvol"),
            "premarket_gap":       pm_intel.get("premarket_gap"),
            "risk_flags":          pm_intel.get("risk_flags") or pm_intel.get("risk_flags_json"),
            "catalyst_flags":      pm_intel.get("catalyst_flags"),
            "sector_confirmed":    pm_intel.get("sector_confirmed"),
        },
    }

    # ── 2. Technical Evidence ─────────────────────────────────────────────────
    technical = {
        # source: mtf_result.dominant_bias (multi-timeframe Stage 4)
        "trend": mtf_result.get("dominant_bias"),
        # source: stock_data fields from Stage 2 (close_strength / rvol / gap_pct)
        "momentum": {
            "close_strength": _safe_float(stock_data.get("close_strength")),
            "rvol":           _safe_float(stock_data.get("rvol") or stock_data.get("rel_volume")),
            "gap_pct":        _safe_float(stock_data.get("gap_pct")),
        },
        # source: oe_pattern_snapshots via pattern_result (Stage 5 pattern detection)
        "pattern_recognition": {
            "pattern_score": _safe_float(pattern_result.get("pattern_score")),
            "patterns_detected": [
                {
                    "id":         p.get("canonical_id") or p.get("id"),
                    "name":       p.get("name"),
                    "confidence": _safe_float(
                        p.get("confidence") or p.get("detection_confidence")),
                    "timeframe":  p.get("timeframe"),
                    "actionable": p.get("actionable"),
                }
                for p in (
                    pattern_result.get("all_patterns")
                    or pattern_result.get("patterns")
                    or []
                )
            ],
        },
        # source: aiem_premarket_intel._support_resistance() stored in options_engine_premarket
        "support_resistance": {
            "premarket_high": _safe_float(pm_intel.get("premarket_high")),
            "premarket_low":  _safe_float(pm_intel.get("premarket_low")),
        },
        # source: mtf_result dict (timeframe_alignment_score, conflict_score,
        #         entry_timing_status — Stage 4 multi-timeframe analysis)
        "multi_timeframe_confirmation": {
            "alignment_score": _safe_float(mtf_result.get("timeframe_alignment_score")),
            "conflict_score":  _safe_float(mtf_result.get("conflict_score")),
            "entry_timing":    mtf_result.get("entry_timing_status"),
            "dominant_bias":   mtf_result.get("dominant_bias"),
        },
    }

    # ── 3. Options Intelligence ───────────────────────────────────────────────
    options_intel = {
        # source: sel_data (call_data or put_data from Stage 3 options chain)
        "greeks": {
            "delta": _safe_float(sel_data.get("delta") or alert_fields.get("delta")),
            "gamma": _safe_float(sel_data.get("gamma") or alert_fields.get("gamma")),
            "theta": _safe_float(sel_data.get("theta") or alert_fields.get("theta")),
            "vega":  _safe_float(sel_data.get("vega")  or alert_fields.get("vega")),
            "rho":   _safe_float(sel_data.get("rho")),
            "vanna": _safe_float(sel_data.get("vanna")),
            "charm": _safe_float(sel_data.get("charm")),
        },
        # source: ivr_result.iv_rank from compute_iv_rank_live (aiem_options_intel Stage 3)
        "iv_rank": _safe_float(
            ivr_result.get("iv_rank") or sel_data.get("iv_rank")
        ),
        # source: oe_options_metrics.iv_percentile (snapped by registry _rc Stage 3)
        "iv_percentile": _iv_percentile,
        # source: em_result.expected_move from compute_expected_move (aiem_options_intel Stage 3)
        "expected_move":     _safe_float(
            em_result.get("expected_move") or alert_fields.get("expected_move")),
        "expected_move_pct": _safe_float(
            em_result.get("expected_move_pct") or alert_fields.get("expected_move_pct")),
        # source: sel_data.open_interest (options chain Stage 3)
        "open_interest": sel_data.get("open_interest") or alert_fields.get("open_interest"),
        # source: sel_data.volume (options chain Stage 3)
        "volume": sel_data.get("volume") or alert_fields.get("volume"),
        # source: sel_data.bid / sel_data.ask / sel_data.bid_ask_spread_pct (Stage 3)
        "bid_ask_spread": {
            "bid":        _safe_float(sel_data.get("bid") or alert_fields.get("bid")),
            "ask":        _safe_float(sel_data.get("ask") or alert_fields.get("ask")),
            "spread_pct": _safe_float(
                sel_data.get("bid_ask_spread_pct")
                or alert_fields.get("bid_ask_spread_pct")),
        },
        # source: oe_strategy_candidates.liquidity_score (Phase 2 capture_strategy_candidates)
        "liquidity_score": _liquidity_score,
        # source: sel_data.iv (options chain front IV, Stage 3)
        "current_iv": _safe_float(sel_data.get("iv") or alert_fields.get("iv")),
    }

    # ── 4. Probability / Risk ─────────────────────────────────────────────────
    _prob_est = _safe_float(
        sel_data.get("probability_estimate")
        or alert_fields.get("probability_estimate")
    )
    _exp_ret = _safe_float(
        sel_data.get("expected_return") or alert_fields.get("expected_return")
    )

    probability_risk = {
        # source: sel_data.probability_estimate from compute_options_probability_matrix
        "probability_engine_output": {
            "probability_estimate": _prob_est,
            "pop": _safe_float(
                sel_data.get("pop") or sel_data.get("probability_estimate")),
        },
        # source: sel_data.expected_return (compute_req6_score D3, Stage 3)
        "expected_value": _exp_ret,
        # source: derived — max_reward / max_risk (Stage 7 alert_fields)
        "risk_reward": _rr,
        # source: sel_data.premium_at_risk (options chain Stage 3)
        "max_risk": _max_risk,
        # source: sel_data.profit_target (Stage 7 alert_fields)
        "max_reward": _max_reward,
        # source: oe_portfolio_context (Phase 4 capture_portfolio_context)
        "portfolio_risk_engine_output": _portfolio_ctx or None,
        # source: oe_portfolio_context.portfolio_delta/gamma/theta/vega
        "portfolio_greeks_impact": (
            {
                "portfolio_delta": _portfolio_ctx.get("portfolio_delta"),
                "portfolio_gamma": _portfolio_ctx.get("portfolio_gamma"),
                "portfolio_theta": _portfolio_ctx.get("portfolio_theta"),
                "portfolio_vega":  _portfolio_ctx.get("portfolio_vega"),
            }
            if _portfolio_ctx else None
        ),
        # source: stock_data.sector / stock_data.sector_strength (Stage 2)
        "sector_exposure_impact": {
            "sector":          (
                stock_data.get("sector") or stock_data.get("sector_name")),
            "sector_strength": stock_data.get("sector_strength"),
        },
        # source: verify_result.correlation_check
        #         (verify_options_decision_inputs in aiem_options_pipeline)
        "correlation_impact": verify_result.get("correlation_check"),
        # source: sel_data.premium_at_risk (capital deployed per contract × 100)
        "buying_power_impact": {
            "capital_deployed_usd": (
                round(_premium_at_risk * 100, 2) if _premium_at_risk is not None else None
            ),
            "capital_reserved_usd": _premium_at_risk,
        },
        # FLAGGED: oe_strategy_scorecards.capital_efficiency is a historical aggregate
        # not a per-recommendation score; no per-decision source computed in pipeline
        "capital_preservation_score": {
            "_flag":   "NOT_PER_DECISION",
            "_reason": (
                "oe_strategy_scorecards.capital_efficiency is a historical aggregate; "
                "no per-recommendation capital_preservation_score computed in pipeline"
            ),
        },
        # FLAGGED: same as capital_preservation_score
        "capital_efficiency_score": {
            "_flag":   "NOT_PER_DECISION",
            "_reason": (
                "oe_strategy_scorecards.capital_efficiency is a historical aggregate; "
                "no per-recommendation capital_efficiency_score computed in pipeline"
            ),
        },
        # source: oe_knowledge_base.confidence_score for ticker/scan_date (Phase 3)
        "confidence_score": _confidence_score,
    }

    # ── 5. Justification ─────────────────────────────────────────────────────
    _no_trade_explanation = None
    if direction == "NO_TRADE":
        _gate_failures = verify_result.get("gate_failures") or []
        _no_trade_explanation = {
            "gate_failures": _gate_failures,
            "call_score":    call_score,
            "put_score":     put_score,
            "summary": (
                f"NO_TRADE: neither direction meets score+margin gates. "
                f"call_score={call_score} put_score={put_score}"
            ),
        }

    justification = {
        # source: alert_fields.why_selected_won (Stage 7 — free-text REQ6 reasoning)
        "why_stock_qualified": alert_fields.get("why_selected_won"),
        # source: chain_strategies entries where rejected=True + rejection_reason
        "why_candidates_rejected": _rejected_candidates,
        # source: oe_decision_records.qualifying_strategies + score_breakdown_json (Phase 2)
        "why_strategy_selected": {
            "selected_strategy": _selected_strategy,
            "call_score":        call_score,
            "put_score":         put_score,
            "direction_winner":  direction,
            "qualifying_strategies": [
                c.get("strategy") or c.get("strategy_id")
                for c in chain_strategies if not c.get("rejected")
            ],
        },
        # source: sel_strike + expiry_str + premium_at_risk + best_chain_strategy
        #         (Stage 7; regime_suitability from Phase 2 oe_strategy_candidates)
        "why_expiration_strikes_size": {
            "strikes":     _strikes or ([sel_strike] if sel_strike else []),
            "expiration":  _expiries[0] if _expiries else expiry_str,
            "dte":         alert_fields.get("dte"),
            "position_size_usd": (
                round(_premium_at_risk * 100, 2)
                if _premium_at_risk is not None else None
            ),
            "regime_suitability": (
                best_chain_strategy.get("regime_suitability")
                if best_chain_strategy else None
            ),
        },
        # source: pm_intel.catalyst_flags / news_headline_count
        #         (aiem_premarket_intel._fetch_polygon_news — Polygon news API)
        "expected_catalyst": {
            "catalyst_flags":      pm_intel.get("catalyst_flags"),
            "news_headline_count": pm_intel.get("news_headline_count"),
            "earnings_in_news":    pm_intel.get("earnings_in_news"),
        },
        # source: alert_fields.breakeven / profit_target / stop_level (Stage 7)
        "entry_exit_plan": {
            "entry_price":   _safe_float(sel_data.get("bid") or alert_fields.get("bid")),
            "breakeven":     _safe_float(
                alert_fields.get("breakeven") or sel_data.get("breakeven")),
            "profit_target": _max_reward,
            # source: sel_data.stop_level (computed in options chain Stage 3)
            "stop_level":    _safe_float(
                sel_data.get("stop_level") or alert_fields.get("stop_level")),
        },
        # source: sel_data.profit_target / alert_fields.profit_target (Stage 7)
        "profit_target_and_plan": {
            "profit_target_mid": _max_reward,
            "profit_target_usd": (
                round(_max_reward * 100, 2) if _max_reward is not None else None
            ),
        },
        # source: sel_data.stop_level (computed in options chain Stage 3 pipeline)
        "stop_loss_criteria": {
            "stop_level": _safe_float(
                sel_data.get("stop_level") or alert_fields.get("stop_level")),
        },
        # PARTIAL: DTE known (alert_fields.dte); per-position time-decay exit
        # thresholds not computed pre-trade in pipeline
        "time_based_exit_rules": {
            "_flag":      "PARTIAL",
            "_reason":    (
                "DTE captured; structured time-based exit thresholds "
                "not computed pre-trade"
            ),
            "dte":        alert_fields.get("dte"),
            "expiration": _expiries[0] if _expiries else expiry_str,
        },
        # FLAGGED: no structured adjustment/rolling criteria computed in pipeline
        "adjustment_rolling_rules": {
            "_flag":   "NOT_COMPUTED",
            "_reason": "No structured adjustment/rolling criteria computed in pipeline",
        },
        # PARTIAL: alert_fields.main_risks (free-text, Stage 7);
        # structured invalidation conditions not computed pre-trade
        "invalidation_conditions": {
            "_flag":      "PARTIAL",
            "_reason":    (
                "main_risks captured as free-text; structured invalidation "
                "conditions not computed pre-trade"
            ),
            "main_risks": alert_fields.get("main_risks"),
        },
        # source: oe_no_trade_candidates.rejection_reasons + verify_result.gate_failures
        "no_trade_explanation": _no_trade_explanation,
    }

    return {
        "identity":         identity,
        "technical":        technical,
        "options_intel":    options_intel,
        "probability_risk": probability_risk,
        "justification":    justification,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WRITE / AMEND / VERIFY
# ─────────────────────────────────────────────────────────────────────────────

def write_decision(
    input_data:     dict,
    output_data:    dict,
    parent_id:      Optional[str]  = None,
    is_test_record: bool           = False,
    context:        Optional[dict] = None,
    db_url:         Optional[str]  = None,
) -> dict:
    """
    Append a new decision audit row with optional Phase 2 context blobs.

    context (Phase 2): dict with keys identity, technical, options_intel,
    probability_risk, justification — from assemble_dpl_context().

    Reject-on-integrity-failure gate: immediately after INSERT the stored hashes
    are re-read and compared. Mismatch → rollback + ValueError.

    Returns dict with decision_id, parent_id, input_hash, output_hash,
    engine_version, db_version, verification_status, has_context.
    """
    conn = _conn(db_url)
    try:
        input_hash  = _sha256(input_data)
        output_hash = _sha256(output_data)
        decision_id = uuid.uuid4().hex[:24]

        ctx = context or {}

        def _jdump(v):
            return json.dumps(v, default=str) if v is not None else None

        identity_json      = _jdump(ctx.get("identity"))
        technical_json     = _jdump(ctx.get("technical"))
        options_intel_json = _jdump(ctx.get("options_intel"))
        prob_risk_json     = _jdump(ctx.get("probability_risk"))
        justif_json        = _jdump(ctx.get("justification"))

        with conn.cursor() as cur:
            eng_ver = _live_engine_version(cur)
            db_ver  = _live_db_version(cur)

            cur.execute(f"""
                INSERT INTO {_DPL_TABLE}
                    (decision_id, parent_id, created_at,
                     input_hash, output_hash, verification_status,
                     engine_version, db_version, is_test_record,
                     identity_json, technical_json, options_intel_json,
                     probability_risk_json, justification_json)
                VALUES (%s, %s, NOW() AT TIME ZONE 'UTC',
                        %s, %s, 'PENDING',
                        %s, %s, %s,
                        %s, %s, %s, %s, %s)
            """, (decision_id, parent_id,
                  input_hash, output_hash,
                  eng_ver, db_ver, is_test_record,
                  identity_json, technical_json, options_intel_json,
                  prob_risk_json, justif_json))

            _post_write_integrity_check(cur, decision_id, input_hash, output_hash)

            cur.execute(
                f"UPDATE {_DPL_TABLE} SET verification_status = 'VERIFIED' "
                "WHERE decision_id = %s",
                (decision_id,)
            )

        conn.commit()
        return {
            "decision_id":         decision_id,
            "parent_id":           parent_id,
            "input_hash":          input_hash,
            "output_hash":         output_hash,
            "engine_version":      eng_ver,
            "db_version":          db_ver,
            "verification_status": "VERIFIED",
            "has_context":         context is not None,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def amend_decision(
    original_decision_id: str,
    new_input_data:       dict,
    new_output_data:      dict,
    is_test_record:       bool           = False,
    context:              Optional[dict] = None,
    db_url:               Optional[str]  = None,
) -> dict:
    """
    'Update' a decision by inserting a new row referencing the original as parent.
    The original row is NOT modified. Returns the new row dict.
    """
    return write_decision(
        input_data      = new_input_data,
        output_data     = new_output_data,
        parent_id       = original_decision_id,
        is_test_record  = is_test_record,
        context         = context,
        db_url          = db_url,
    )


def verify_decision(
    decision_id: str,
    input_data:  dict,
    output_data: dict,
    db_url:      Optional[str] = None,
) -> dict:
    """
    Recompute hashes from provided data and compare against stored values.
    Updates verification_status to VERIFIED or TAMPERED.
    Returns dict with 'status', 'decision_id', 'input_match', 'output_match'.
    """
    conn = _conn(db_url)
    try:
        computed_input  = _sha256(input_data)
        computed_output = _sha256(output_data)

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT input_hash, output_hash FROM {_DPL_TABLE} "
                "WHERE decision_id = %s",
                (decision_id,)
            )
            row = cur.fetchone()

        if row is None:
            return {"status": "NOT_FOUND", "decision_id": decision_id,
                    "input_match": False, "output_match": False}

        stored_input, stored_output = row
        input_match  = computed_input  == stored_input
        output_match = computed_output == stored_output
        status = "VERIFIED" if (input_match and output_match) else "TAMPERED"

        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_DPL_TABLE} SET verification_status = %s "
                "WHERE decision_id = %s",
                (status, decision_id)
            )
        conn.commit()
        return {
            "status":       status,
            "decision_id":  decision_id,
            "input_match":  input_match,
            "output_match": output_match,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3: REPRODUCIBILITY REPLAY
# ─────────────────────────────────────────────────────────────────────────────

class ReplayInputsMissingError(Exception):
    """
    Raised by replay_decision() when no replay inputs exist for the given
    decision_id.  This is an intentional loud failure — never silently fall back
    to live data or cached defaults.
    """

class ReplayCodeDriftError(Exception):
    """
    Raised by replay_decision() when compute_req6_score source has changed
    since the decision was captured.  Status: CODE_DRIFT.
    Never silently proceed — a changed scoring function invalidates reproducibility.
    """


_REPLAY_TABLE          = "oe_decision_replay_inputs"
_REPLAY_SCHEMA_VERSION = "1"

# REQ6 scoring weights — single authoritative source in aiem_options_pipeline.
from aiem_options_pipeline import _REQ6_SCORING_WEIGHTS


def bootstrap_dpl_phase3(db_url=None) -> bool:
    """
    Idempotent CREATE TABLE + ALTER + TRIGGER for oe_decision_replay_inputs.
    Safe to call multiple times.  Called automatically by bootstrap_dpl().
    """
    conn = _conn(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_REPLAY_TABLE} (
                    decision_id             TEXT    PRIMARY KEY
                                            REFERENCES {_DPL_TABLE}(decision_id),
                    alert_id                INTEGER,
                    replay_schema_version   TEXT    NOT NULL DEFAULT '1',
                    is_test_record          BOOLEAN NOT NULL DEFAULT FALSE,
                    contract_data_call      JSONB   NOT NULL,
                    contract_data_put       JSONB   NOT NULL,
                    stock_data_replay       JSONB   NOT NULL,
                    iv_rank                 NUMERIC(8,6) NOT NULL,
                    verify_result_replay    JSONB   NOT NULL,
                    config_versions         JSONB   NOT NULL,
                    data_source_timestamps  JSONB   NOT NULL,
                    scoring_weights_snapshot JSONB,
                    stored_call_score       NUMERIC(5,1),
                    stored_put_score        NUMERIC(5,1),
                    stored_direction        TEXT,
                    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            # Additive ALTER for tables already created without new columns
            for col_ddl in [
                "ALTER TABLE {t} ADD COLUMN IF NOT EXISTS is_test_record BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE {t} ADD COLUMN IF NOT EXISTS scoring_weights_snapshot JSONB",
            ]:
                cur.execute(col_ddl.format(t=_REPLAY_TABLE))
            # scoring_fn_hash stored inside config_versions JSONB (no separate column)
            # Migrate existing test rows: set is_test_record=TRUE where parent audit row is test
            cur.execute(f"""
                UPDATE {_REPLAY_TABLE} ri
                SET    is_test_record = TRUE
                FROM   {_DPL_TABLE} da
                WHERE  ri.decision_id    = da.decision_id
                  AND  da.is_test_record = TRUE
                  AND  ri.is_test_record = FALSE
            """)
            # Immutability trigger: block UPDATE/DELETE on non-test rows
            cur.execute("""
                CREATE OR REPLACE FUNCTION _oe_replay_guard_immutability()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        IF OLD.is_test_record THEN RETURN OLD; END IF;
                        RAISE EXCEPTION
                            'oe_decision_replay_inputs is append-only: '
                            'DELETE not permitted on production rows';
                    END IF;
                    IF OLD.is_test_record THEN RETURN NEW; END IF;
                    RAISE EXCEPTION
                        'oe_decision_replay_inputs: all columns are immutable '
                        'on production rows (is_test_record = FALSE)';
                END;
                $$
            """)
            cur.execute(f"""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_trigger
                        WHERE tgname='trg_oe_replay_immutable'
                          AND tgrelid='{_REPLAY_TABLE}'::regclass
                    ) THEN
                        CREATE TRIGGER trg_oe_replay_immutable
                        BEFORE DELETE OR UPDATE ON {_REPLAY_TABLE}
                        FOR EACH ROW EXECUTE FUNCTION _oe_replay_guard_immutability();
                    END IF;
                END $$
            """)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def bootstrap_governance_tables(db_url=None) -> bool:
    """
    Idempotent CREATE TABLE for the three Phase 2 governance tables:
      - oe_unreplayable_rows     : exemption registry for unreplayable decisions
      - oe_synthetic_row_corrections: corrections to immutable synthetic-row reason text
      - oe_gate_events           : engine-integrity gate suppression audit trail
    Also adds origin attribution columns to oe_decision_replay_inputs.
    Safe to call multiple times.  Called automatically by bootstrap_dpl().
    """
    conn = _conn(db_url)
    try:
        with conn.cursor() as cur:
            # oe_synthetic_row_corrections
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_synthetic_row_corrections (
                    correction_id        TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                    decision_id          TEXT NOT NULL
                                         REFERENCES oe_known_synthetic_rows(decision_id),
                    field_corrected      TEXT NOT NULL,
                    original_value       TEXT NOT NULL,
                    corrected_value      TEXT NOT NULL,
                    correction_rationale TEXT NOT NULL,
                    evidence_ref         TEXT,
                    authenticated_by     TEXT NOT NULL,
                    prev_hash            TEXT NOT NULL DEFAULT 'GENESIS',
                    chain_hash           TEXT,
                    is_test_record       BOOLEAN NOT NULL DEFAULT FALSE,
                    registered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION _oe_synth_corrections_guard()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF OLD.is_test_record THEN RETURN NEW; END IF;
                    RAISE EXCEPTION
                        'oe_synthetic_row_corrections is append-only: '
                        'modification of production rows is not permitted';
                END; $$
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                                   WHERE tgname='trg_oe_synth_corrections_immutable') THEN
                        CREATE TRIGGER trg_oe_synth_corrections_immutable
                        BEFORE UPDATE OR DELETE ON oe_synthetic_row_corrections
                        FOR EACH ROW EXECUTE FUNCTION _oe_synth_corrections_guard();
                    END IF;
                END $$
            """)

            # oe_unreplayable_rows
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_unreplayable_rows (
                    exemption_id             TEXT PRIMARY KEY
                                             DEFAULT gen_random_uuid()::text,
                    decision_id              TEXT NOT NULL UNIQUE
                                             REFERENCES oe_decision_audit(decision_id),
                    primary_reason_code      TEXT NOT NULL,
                    secondary_observation    TEXT,
                    exception_class          TEXT NOT NULL,
                    evidence_seq             INTEGER,
                    log_sha256               TEXT,
                    evidence_ref             TEXT,
                    evidence_ref_json        JSONB,
                    commit_sha               TEXT,
                    stored_hash              TEXT,
                    current_hash             TEXT,
                    hash_scheme_version      TEXT NOT NULL DEFAULT '1',
                    source_state_recoverable BOOLEAN NOT NULL DEFAULT FALSE,
                    tested_commits           TEXT[],
                    authenticated_by         TEXT NOT NULL,
                    prev_hash                TEXT NOT NULL DEFAULT 'GENESIS',
                    chain_hash               TEXT,
                    is_test_record           BOOLEAN NOT NULL DEFAULT FALSE,
                    registered_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT oe_unreplayable_rows_reason_code_check
                        CHECK (primary_reason_code IN (
                            'ERA_INCOMPATIBLE_HASH','SOURCE_CHANGED',
                            'WEIGHTS_DRIFT','UNVERIFIABLE','SCHEMA_MISMATCH'))
                )
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION _oe_unreplayable_guard()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF OLD.is_test_record THEN RETURN NEW; END IF;
                    RAISE EXCEPTION
                        'oe_unreplayable_rows is append-only: '
                        'modification of production rows is not permitted';
                END; $$
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                                   WHERE tgname='trg_oe_unreplayable_immutable') THEN
                        CREATE TRIGGER trg_oe_unreplayable_immutable
                        BEFORE UPDATE OR DELETE ON oe_unreplayable_rows
                        FOR EACH ROW EXECUTE FUNCTION _oe_unreplayable_guard();
                    END IF;
                END $$
            """)

            # oe_gate_events
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_gate_events (
                    gate_event_id    TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                    gate_name        TEXT NOT NULL,
                    fired_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ticker           TEXT,
                    trace_id         TEXT,
                    live_hash        TEXT,
                    expected_hash    TEXT,
                    mismatch_detail  TEXT,
                    decision_context JSONB,
                    action_taken     TEXT NOT NULL DEFAULT 'BLOCKED',
                    CHECK (action_taken IN ('BLOCKED','ALLOWED','LOGGED')),
                    is_test_record   BOOLEAN NOT NULL DEFAULT FALSE,
                    authenticated_by TEXT NOT NULL DEFAULT 'scheduler',
                    prev_hash        TEXT NOT NULL DEFAULT 'GENESIS',
                    chain_hash       TEXT
                )
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION _oe_gate_events_guard()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF OLD.is_test_record THEN RETURN NEW; END IF;
                    RAISE EXCEPTION
                        'oe_gate_events is append-only: '
                        'modification of production rows is not permitted';
                END; $$
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                                   WHERE tgname='trg_oe_gate_events_immutable') THEN
                        CREATE TRIGGER trg_oe_gate_events_immutable
                        BEFORE UPDATE OR DELETE ON oe_gate_events
                        FOR EACH ROW EXECUTE FUNCTION _oe_gate_events_guard();
                    END IF;
                END $$
            """)

            # Gate-event enrichment columns + dedup index (Item 7)
            for _col_ddl in [
                "ALTER TABLE oe_gate_events ADD COLUMN IF NOT EXISTS candidate_id    TEXT",
                "ALTER TABLE oe_gate_events ADD COLUMN IF NOT EXISTS pipeline_job_id TEXT",
                "ALTER TABLE oe_gate_events ADD COLUMN IF NOT EXISTS git_commit      TEXT",
                "ALTER TABLE oe_gate_events ADD COLUMN IF NOT EXISTS reason          TEXT",
            ]:
                cur.execute(_col_ddl)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS oe_gate_events_dedup_idx
                    ON oe_gate_events(gate_name, pipeline_job_id)
                    WHERE pipeline_job_id IS NOT NULL AND is_test_record = FALSE
            """)

            # Origin attribution columns for oe_decision_replay_inputs (Item 15)
            for _col_ddl in [
                "ALTER TABLE oe_decision_replay_inputs ADD COLUMN IF NOT EXISTS origin_type           TEXT",
                "ALTER TABLE oe_decision_replay_inputs ADD COLUMN IF NOT EXISTS scheduler_job_id      TEXT",
                "ALTER TABLE oe_decision_replay_inputs ADD COLUMN IF NOT EXISTS worker_pid            INTEGER",
                "ALTER TABLE oe_decision_replay_inputs ADD COLUMN IF NOT EXISTS deployment_commit_sha TEXT",
            ]:
                cur.execute(_col_ddl)

            # ── Item 9: TRUNCATE protection (statement-level triggers) ────────
            # Row-level BEFORE UPDATE/DELETE triggers do not fire on TRUNCATE.
            # Add statement-level TRUNCATE triggers for all 4 protected tables.
            for _tbl9, _trg9 in [
                ('oe_synthetic_row_corrections', 'trg_oe_synth_corrections_no_truncate'),
                ('oe_unreplayable_rows',         'trg_oe_unreplayable_no_truncate'),
                ('oe_gate_events',               'trg_oe_gate_events_no_truncate'),
                ('oe_decision_replay_inputs',    'trg_oe_replay_inputs_no_truncate'),
            ]:
                cur.execute(f"""
                    CREATE OR REPLACE FUNCTION _trg_fn_{_trg9}()
                    RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN
                        RAISE EXCEPTION
                            '{_tbl9}: TRUNCATE is prohibited on this protected table';
                    END; $$
                """)
                cur.execute(f"""
                    DO $$ BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_trigger
                                       WHERE tgname='{_trg9}') THEN
                            CREATE TRIGGER {_trg9}
                            BEFORE TRUNCATE ON {_tbl9}
                            FOR EACH STATEMENT EXECUTE FUNCTION _trg_fn_{_trg9}();
                        END IF;
                    END $$
                """)

            # ── Item 14: Full decision snapshot table ─────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_decision_snapshots (
                    snapshot_id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                    decision_id               TEXT NOT NULL UNIQUE,
                    options_chain_json        JSONB,
                    underlying_quote          JSONB,
                    portfolio_state           JSONB,
                    risk_limits               JSONB,
                    market_regime_inputs      JSONB,
                    all_candidates_json       JSONB,
                    rejected_alternatives_json JSONB,
                    data_quality_status       TEXT,
                    snapshot_sealed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    is_test_record            BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION _oe_decision_snapshots_guard()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF OLD.is_test_record THEN RETURN NEW; END IF;
                    RAISE EXCEPTION
                        'oe_decision_snapshots is append-only: '
                        'modification of production rows is not permitted';
                END; $$
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                                   WHERE tgname='trg_oe_decision_snapshots_immutable') THEN
                        CREATE TRIGGER trg_oe_decision_snapshots_immutable
                        BEFORE UPDATE OR DELETE ON oe_decision_snapshots
                        FOR EACH ROW EXECUTE FUNCTION _oe_decision_snapshots_guard();
                    END IF;
                END $$
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION _trg_fn_trg_oe_snapshots_no_truncate()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION
                        'oe_decision_snapshots: TRUNCATE is prohibited on this protected table';
                END; $$
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                                   WHERE tgname='trg_oe_snapshots_no_truncate') THEN
                        CREATE TRIGGER trg_oe_snapshots_no_truncate
                        BEFORE TRUNCATE ON oe_decision_snapshots
                        FOR EACH STATEMENT EXECUTE FUNCTION _trg_fn_trg_oe_snapshots_no_truncate();
                    END IF;
                END $$
            """)

            # ── Item 4: Index corrections table (retroactive modification log) ─
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_index_corrections (
                    correction_id        TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                    target_seq           INTEGER NOT NULL,
                    target_record_hash   TEXT,
                    original_value       TEXT NOT NULL,
                    corrected_value      TEXT NOT NULL,
                    correction_reason    TEXT NOT NULL,
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_by           TEXT NOT NULL,
                    approved_by          TEXT NOT NULL,
                    prev_correction_hash TEXT NOT NULL DEFAULT 'GENESIS',
                    correction_hash      TEXT,
                    is_test_record       BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION _oe_index_corrections_guard()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF OLD.is_test_record THEN RETURN NEW; END IF;
                    RAISE EXCEPTION
                        'oe_index_corrections is append-only: '
                        'historical correction records are immutable';
                END; $$
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                                   WHERE tgname='trg_oe_index_corrections_immutable') THEN
                        CREATE TRIGGER trg_oe_index_corrections_immutable
                        BEFORE UPDATE OR DELETE ON oe_index_corrections
                        FOR EACH ROW EXECUTE FUNCTION _oe_index_corrections_guard();
                    END IF;
                END $$
            """)
            cur.execute("""
                CREATE OR REPLACE FUNCTION _trg_fn_trg_oe_idx_corr_no_truncate()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION
                        'oe_index_corrections: TRUNCATE is prohibited';
                END; $$
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                                   WHERE tgname='trg_oe_idx_corr_no_truncate') THEN
                        CREATE TRIGGER trg_oe_idx_corr_no_truncate
                        BEFORE TRUNCATE ON oe_index_corrections
                        FOR EACH STATEMENT EXECUTE FUNCTION _trg_fn_trg_oe_idx_corr_no_truncate();
                    END IF;
                END $$
            """)

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_contamination_exclusions(db_url: Optional[str] = None) -> list:
    """B17 (R7): non-verifier consumer for oe_contamination_exclusions.

    Returns a list of dicts describing all contaminated replay-input rows that
    have been formally excluded from production reads.  The scheduler calls this
    at startup to emit an audit log of what is excluded, so the production run
    never silently includes contaminated rows.

    Returns [] if the table does not yet exist or is empty (safe at boot).
    """
    import psycopg2, os as _os
    _url = db_url or _os.environ.get('DATABASE_URL', '')
    if not _url:
        return []
    try:
        conn = psycopg2.connect(_url)
        cur  = conn.cursor()
        cur.execute("""
            SELECT decision_id, reason_code, excluded_at, excluded_by, notes
            FROM oe_contamination_exclusions
            ORDER BY excluded_at
        """)
        cols = ['decision_id', 'reason_code', 'excluded_at', 'excluded_by', 'notes']
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception:
        return []


def capture_replay_inputs(
    decision_id:            str,
    direction:              str,
    call_score:             float,
    put_score:              float,
    call_data:              dict,
    put_data:               dict,
    stock_data:             dict,
    verify_result:          dict,
    iv_rank:                float,
    alert_id:               Optional[int]  = None,
    is_test_record:         bool           = False,
    db_url:                 Optional[str]  = None,
    # Origin attribution (Item 15)
    origin_type:            Optional[str]  = None,
    scheduler_job_id:       Optional[str]  = None,
    worker_pid:             Optional[int]  = None,
    deployment_commit_sha:  Optional[str]  = None,
) -> bool:
    """
    Persist the raw inputs required to deterministically replay this decision.

    call_data / put_data  — the exact dicts passed to compute_req6_score().
    iv_rank               — 0-1 float (same value passed to compute_req6_score).
    is_test_record        — True for verifier/test rows; FALSE for all production calls.

    Origin attribution (Item 15):
      origin_type           — 'SCHEDULER' | 'scheduled_pipeline' | 'manual' | 'test' | 'backfill'
                              Use 'SCHEDULER' for all calls originating from aiem_options_scheduler._execute_job.
      scheduler_job_id      — job ID from oe_options_pipeline_jobs if applicable
      worker_pid            — os.getpid() of the worker process
      deployment_commit_sha — git HEAD at time of execution

    Idempotent via ON CONFLICT DO NOTHING on the decision_id PK.
    Returns True on success.
    """
    import datetime as _dt
    import os as _os
    from aiem_options_pipeline import compute_req6_score as _crs

    weights_hash = hashlib.sha256(
        json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True).encode()
    ).hexdigest()[:16]
    _fn_src = inspect.getsource(_crs)
    scoring_fn_hash = hashlib.sha256(
        (_fn_src + "\x00" + json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True)).encode()
    ).hexdigest()

    # Auto-populate origin fields if not supplied
    if worker_pid is None:
        worker_pid = _os.getpid()

    config_versions = {
        "req6_weights_hash":     weights_hash,
        "scoring_fn_hash":       scoring_fn_hash,
        "replay_schema_version": _REPLAY_SCHEMA_VERSION,
        "dpl_module":            "aiem_options_dpl.py",
    }
    data_source_timestamps = {
        "polygon_scan_date": str(stock_data.get("scan_date", "")),
        "oss_scan_date":     str(stock_data.get("oss_scan_date", "")),
        "captured_at_utc":   _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    conn = _conn(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO {_REPLAY_TABLE} (
                    decision_id, alert_id, replay_schema_version,
                    is_test_record,
                    contract_data_call, contract_data_put, stock_data_replay,
                    iv_rank, verify_result_replay,
                    config_versions, data_source_timestamps,
                    scoring_weights_snapshot,
                    stored_call_score, stored_put_score, stored_direction,
                    origin_type, scheduler_job_id, worker_pid, deployment_commit_sha
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (decision_id) DO NOTHING
            """, (
                decision_id,
                alert_id,
                _REPLAY_SCHEMA_VERSION,
                is_test_record,
                json.dumps(call_data,     default=str),
                json.dumps(put_data,      default=str),
                json.dumps(stock_data,    default=str),
                round(float(iv_rank), 6),
                json.dumps(verify_result, default=str),
                json.dumps(config_versions),
                json.dumps(data_source_timestamps),
                json.dumps(_REQ6_SCORING_WEIGHTS),
                round(float(call_score), 1),
                round(float(put_score),  1),
                direction,
                origin_type,
                scheduler_job_id,
                worker_pid,
                deployment_commit_sha,
            ))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replay_decision(
    decision_id: str,
    db_url:      Optional[str] = None,
) -> dict:
    """
    Replay a past decision using ONLY the stored inputs. No live data.
    No re-fetching.

    Raises ReplayInputsMissingError (no fallback) when no replay inputs exist
    for the given decision_id.

    Returns:
        decision_id, call_score_replayed, put_score_replayed,
        call_score_stored, put_score_stored,
        direction_replayed, direction_stored,
        call_match (|diff| < 0.05), put_match (|diff| < 0.05),
        direction_match, full_match,
        call_scoring (full compute_req6_score result),
        put_scoring  (full compute_req6_score result).
    """
    from aiem_options_pipeline import compute_req6_score

    conn = _conn(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT contract_data_call, contract_data_put, stock_data_replay,
                       iv_rank, verify_result_replay,
                       stored_call_score, stored_put_score, stored_direction,
                       config_versions, scoring_weights_snapshot
                FROM {_REPLAY_TABLE}
                WHERE decision_id = %s
            """, (decision_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise ReplayInputsMissingError(
            f"[Phase 3] No replay inputs found for decision_id={decision_id!r}. "
            "capture_replay_inputs() must be called at decision write time. "
            "This is an intentional hard failure — no silent fallback is permitted."
        )

    (cdc, cdp, sd, iv_r, vr,
     stored_call, stored_put, stored_direction, config_ver, stored_weights_snap) = row

    # ── CODE_DRIFT check: combined hash (source + weights), fail loudly on mismatch ──
    # Composition: sha256(getsource(compute_req6_score) + '\x00' + json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True))
    stored_fn_hash = (config_ver or {}).get("scoring_fn_hash")
    if stored_fn_hash is None:
        raise ReplayCodeDriftError(
            f"[Phase 3] UNVERIFIABLE — no combined hash stored for decision_id={decision_id!r}. "
            "Row captured before combined-hash patch. Replay integrity cannot be confirmed."
        )
    _fn_src_r = inspect.getsource(compute_req6_score)
    live_fn_hash = hashlib.sha256(
        (_fn_src_r + "\x00" + json.dumps(_REQ6_SCORING_WEIGHTS, sort_keys=True)).encode()
    ).hexdigest()
    if live_fn_hash != stored_fn_hash:
        raise ReplayCodeDriftError(
            f"[Phase 3] CODE_DRIFT detected for decision_id={decision_id!r}. "
            f"stored combined_hash={stored_fn_hash[:16]!r} "
            f"live combined_hash={live_fn_hash[:16]!r}. "
            "compute_req6_score source OR _REQ6_SCORING_WEIGHTS has changed since capture. "
            "Replay is NOT reproducible — resolve before proceeding."
        )

    # ── WEIGHTS_DRIFT: independent snapshot comparison (separate from hash check) ──
    if stored_weights_snap is None:
        raise ReplayCodeDriftError(
            f"[Phase 3] UNVERIFIABLE — no weights snapshot stored for decision_id={decision_id!r}. "
            "Row captured before scoring_weights_snapshot column. Replay integrity cannot be confirmed."
        )
    if stored_weights_snap != _REQ6_SCORING_WEIGHTS:
        _diff_keys = [k for k in set(list(stored_weights_snap) + list(_REQ6_SCORING_WEIGHTS))
                      if stored_weights_snap.get(k) != _REQ6_SCORING_WEIGHTS.get(k)]
        raise ReplayCodeDriftError(
            f"[Phase 3] WEIGHTS_DRIFT detected for decision_id={decision_id!r}. "
            f"Live _REQ6_SCORING_WEIGHTS differs from stored snapshot on keys: {_diff_keys}. "
            "Weights changed since capture — replay is NOT reproducible."
        )

    iv_rank_f = float(iv_r or 0)

    call_result = compute_req6_score(
        contract_data=cdc,
        direction="CALL",
        stock_data=sd,
        iv_rank=iv_rank_f,
        verify_result=vr,
    )
    put_result = compute_req6_score(
        contract_data=cdp,
        direction="PUT",
        stock_data=sd,
        iv_rank=iv_rank_f,
        verify_result=vr,
    )

    call_r = call_result["score"]
    put_r  = put_result["score"]
    margin = abs(call_r - put_r)

    # Direction thresholds mirror aiem_options_scheduler._execute_job() Stage 6
    if call_r >= put_r and call_r >= 55 and margin >= 10:
        dir_r = "LONG_CALL"
    elif put_r > call_r and put_r >= 55 and margin >= 10:
        dir_r = "LONG_PUT"
    else:
        dir_r = "NO_TRADE"

    # ── Item 13: Tightened replay tolerance ──────────────────────────────────
    # compute_req6_score stores scores rounded to 1 decimal (round(x, 1)).
    # Replayed scores are also rounded to 1 decimal before comparison.
    # Exact equality on rounded values is achievable; tolerance is 0.0.
    # The only non-exactness is float→Decimal rounding in the DB NUMERIC column,
    # which can add ≤5e-14 error; we use 1e-9 as the documented defensible bound.
    # This tolerance CANNOT change any decision result: all thresholds are integers
    # (55, 10) so a 1e-9 diff cannot flip LONG_CALL/LONG_PUT/NO_TRADE.
    _REPLAY_TOLERANCE = 1e-9  # documented: IEEE754 NUMERIC round-trip only

    # NULL-safe comparisons: if stored score is NULL, match is None (not False)
    if stored_call is None:
        call_match = None
    else:
        call_match = abs(round(call_r, 1) - round(float(stored_call), 1)) <= _REPLAY_TOLERANCE

    if stored_put is None:
        put_match = None
    else:
        put_match = abs(round(put_r, 1) - round(float(stored_put), 1)) <= _REPLAY_TOLERANCE

    if stored_direction is None:
        dir_match = None
    else:
        dir_match = (dir_r == stored_direction)

    full_match = (call_match is True and put_match is True and dir_match is True)

    return {
        "decision_id":          decision_id,
        "call_score_replayed":  call_r,
        "put_score_replayed":   put_r,
        "call_score_stored":    float(stored_call) if stored_call is not None else None,
        "put_score_stored":     float(stored_put)  if stored_put  is not None else None,
        "direction_replayed":   dir_r,
        "direction_stored":     stored_direction,
        "call_match":           call_match,
        "put_match":            put_match,
        "direction_match":      dir_match,
        "full_match":           full_match,
        "call_scoring":         call_result,
        "put_scoring":          put_result,
    }
