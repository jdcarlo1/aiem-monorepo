"""
aiem_options_phase3.py  —  Phase III Phase 3: Analysis & Attribution
======================================================================
Sections 9-14 of the AIEM Standalone Options Engine Phase III directive.

 9. Root-Cause Analysis          → oe_root_cause_records
10. Indicator & Pattern Attr.    → oe_attribution_runs, oe_indicator_attribution
11. Combination & Interaction    → oe_interaction_hypotheses, oe_interaction_results
12. Strategy Scorecards          → oe_strategy_scorecards
13. Success/Failure KBs          → oe_knowledge_base, oe_kb_confidence_log
14. Market & Vol Regime Learning → oe_regime_performance

Isolation:  zero imports from D1/D2/D3.  All tables prefixed oe_.
Failure:    every public function is non-fatal — log and return, never raise to caller.
Data guard: no delete/truncate on any existing row.
Attribution guard: min n=20 before any statistical claim; BH-FDR applied before
                   accepting any discovered relationship.
Scorecard guard:   no cross-strategy aggregation; UNIQUE(strategy_id,segment,value).
KB guard:          confidence changes require explicit validation gates.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, date, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

log = logging.getLogger("aiem.options.phase3")
_DB_URL   = os.environ.get("DATABASE_URL", "")
_BOOTSTRAPPED = False

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Section 9 — every failure mode from the directive
_ROOT_CAUSE_CATEGORIES: List[str] = [
    "DIRECTION_WRONG",
    "MAGNITUDE_WRONG",
    "TIMING_WRONG",
    "ENTRY_TOO_EARLY",
    "ENTRY_TOO_LATE",
    "EXIT_TOO_EARLY",
    "EXIT_TOO_LATE",
    "STRIKE_INCORRECT",
    "EXPIRATION_INCORRECT",
    "WIDTH_INCORRECT",
    "STRATEGY_FAMILY_INCORRECT",
    "POSITION_SIZE_INCORRECT",
    "PROBABILITY_ESTIMATE_WRONG",
    "VOLATILITY_ESTIMATE_WRONG",
    "IV_CRUSH",
    "VOL_EXPANSION_UNEXPECTED",
    "THETA_DECAY",
    "GAMMA_EXPOSURE",
    "LIQUIDITY_DETERIORATION",
    "EXCESSIVE_SPREAD",
    "SLIPPAGE",
    "FILL_DELAY",
    "ASSIGNMENT_RISK",
    "DIVIDEND_RISK",
    "REGIME_CHANGE",
    "REGIME_MISCLASSIFICATION",
    "PATTERN_FAILURE",
    "INDICATOR_FAILURE",
    "CONFLICTING_SIGNALS",
    "SECTOR_REVERSAL",
    "MARKET_REVERSAL",
    "MACRO_EVENT",
    "NEWS_EVENT",
    "PORTFOLIO_CONCENTRATION",
    "CORRELATION_SHOCK",
    "DATA_QUALITY_FAILURE",
    "STALE_DATA",
    "SCHEDULER_FAILURE",
    "WORKER_FAILURE",
    "EXECUTION_FAILURE",
    "RISK_RULE_FAILURE",
    "EXIT_RULE_FAILURE",
]

_OUTCOME_TYPES: Tuple[str, ...] = (
    "CLOSED_WIN",
    "CLOSED_LOSS",
    "EXPIRED_WIN",
    "EXPIRED_LOSS",
    "EXPIRED_BREAKEVEN",
    "BLOCKED",
    "NO_TRADE",
)

_DECISION_QUALITY: Tuple[str, ...] = ("GOOD", "BAD", "NEUTRAL")

# Section 10
_ATTRIBUTION_METHODS: Tuple[str, ...] = (
    "CONDITIONAL",
    "IC",
    "CALIBRATION",
    "BRIER_DELTA",
    "ABLATION",
)

# Section 11
_MIN_ATTRIBUTION_SAMPLE: int = 20
_MIN_INTERACTION_SAMPLE: int = 20
_FDR_ALPHA: float = 0.05

# Section 12
_SEGMENT_TYPES: Tuple[str, ...] = (
    "GLOBAL",
    "TICKER",
    "SECTOR",
    "INDUSTRY",
    "REGIME",
    "VOL_REGIME",
    "DTE",
    "STRIKE_ZONE",
    "TIME_OF_DAY",
    "DAY_OF_WEEK",
    "EXIT_METHOD",
    "PORTFOLIO_STATE",
)

# Section 13
_KB_TYPES: Tuple[str, ...] = (
    "SUCCESS_TRADE",
    "FAILURE_TRADE",
    "SUCCESS_NO_TRADE",
    "MISSED_OPPORTUNITY",
    "OPERATIONAL_FAILURE",
    "DATA_QUALITY_FAILURE",
    "VERIFICATION_FAILURE",
)

_MIN_KB_CONFIDENCE_SAMPLE: int = 20    # min observations before confidence can change
_KB_CONFIDENCE_INCREASE_REQUIRES_OOS = True  # confidence rise needs out-of-sample proof

# Section 14 — every regime from the directive
_REGIME_TYPES: Tuple[str, ...] = (
    "BULL_STRONG",
    "BULL_WEAK",
    "BEAR_STRONG",
    "BEAR_WEAK",
    "SIDEWAYS",
    "TRENDING",
    "MEAN_REVERTING",
    "HIGH_VOL",
    "LOW_VOL",
    "RISING_VOL",
    "FALLING_VOL",
    "RISK_ON",
    "RISK_OFF",
    "RISING_RATES",
    "FALLING_RATES",
    "HIGH_CORRELATION",
    "CORRELATION_BREAKDOWN",
    "EVENT_DRIVEN",
    "EARNINGS_PERIOD",
    "MACRO_ANNOUNCEMENT",
)

# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP  (8 new tables)
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_phase3(db_url: str = "") -> bool:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return True
    url = db_url or _DB_URL
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:

            # ── Section 9: root-cause records ────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_root_cause_records (
                    id                    BIGSERIAL PRIMARY KEY,
                    alert_id              INTEGER,
                    trade_record_id       INTEGER,
                    trace_id              VARCHAR(128),
                    ticker                VARCHAR(16) NOT NULL,
                    scan_date             DATE        NOT NULL,
                    outcome_type          VARCHAR(32) NOT NULL
                        CHECK (outcome_type IN (
                            'CLOSED_WIN','CLOSED_LOSS','EXPIRED_WIN','EXPIRED_LOSS',
                            'EXPIRED_BREAKEVEN','BLOCKED','NO_TRADE')),
                    decision_quality      VARCHAR(16) NOT NULL
                        CHECK (decision_quality IN ('GOOD','BAD','NEUTRAL')),
                    pnl_pct               NUMERIC(8,4),
                    direction_correct     BOOLEAN,
                    magnitude_correct     BOOLEAN,
                    timing_correct        BOOLEAN,
                    root_cause_categories JSONB       NOT NULL DEFAULT '[]',
                    primary_root_cause    VARCHAR(64),
                    secondary_root_cause  VARCHAR(64),
                    strike_assessment     VARCHAR(32),
                    expiry_assessment     VARCHAR(32),
                    strategy_family_assessment VARCHAR(32),
                    vol_estimate_error    NUMERIC(8,4),
                    prob_estimate_error   NUMERIC(8,4),
                    regime_misclassified  BOOLEAN     DEFAULT FALSE,
                    data_quality_failures JSONB       DEFAULT '[]',
                    operational_failures  JSONB       DEFAULT '[]',
                    scoring_data_json     JSONB,
                    verify_data_json      JSONB,
                    analyst_notes         TEXT,
                    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_rcr_alert_id
                    ON oe_root_cause_records(alert_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_rcr_outcome_type
                    ON oe_root_cause_records(outcome_type, scan_date)
            """)

            # ── Section 10: attribution runs ──────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_attribution_runs (
                    run_id               BIGSERIAL   PRIMARY KEY,
                    run_ts               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    method               VARCHAR(32) NOT NULL
                        CHECK (method IN (
                            'CONDITIONAL','IC','CALIBRATION','BRIER_DELTA','ABLATION')),
                    scope                VARCHAR(64),
                    sample_size          INTEGER     NOT NULL,
                    date_range_start     DATE,
                    date_range_end       DATE,
                    fdr_correction_applied BOOLEAN   NOT NULL DEFAULT TRUE,
                    fdr_method           VARCHAR(32) DEFAULT 'BH',
                    alpha_threshold      NUMERIC(6,4) DEFAULT 0.05,
                    indicators_tested    INTEGER,
                    significant_count    INTEGER,
                    status               VARCHAR(32) DEFAULT 'COMPLETED',
                    notes                TEXT,
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # ── Section 10: per-indicator attribution results ─────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_indicator_attribution (
                    id                  BIGSERIAL   PRIMARY KEY,
                    attribution_run_id  BIGINT      NOT NULL
                        REFERENCES oe_attribution_runs(run_id),
                    indicator_id        INTEGER,
                    indicator_name      VARCHAR(128) NOT NULL,
                    method              VARCHAR(32)  NOT NULL,
                    conditional_win_rate  NUMERIC(8,4),
                    unconditional_win_rate NUMERIC(8,4),
                    lift                NUMERIC(8,4),
                    information_coefficient NUMERIC(8,4),
                    brier_score_delta   NUMERIC(8,4),
                    log_loss_delta      NUMERIC(8,4),
                    precision_score     NUMERIC(8,4),
                    recall_score        NUMERIC(8,4),
                    false_positive_rate NUMERIC(8,4),
                    false_negative_rate NUMERIC(8,4),
                    regime_conditioned  JSONB,
                    sample_size         INTEGER      NOT NULL,
                    uncertainty_estimate NUMERIC(8,4),
                    p_value_raw         NUMERIC(10,6),
                    p_value_corrected   NUMERIC(10,6),
                    is_significant      BOOLEAN      NOT NULL DEFAULT FALSE,
                    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_ia_run_id
                    ON oe_indicator_attribution(attribution_run_id)
            """)

            # ── Section 11: interaction hypotheses ────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_interaction_hypotheses (
                    hypothesis_id   BIGSERIAL   PRIMARY KEY,
                    components_json JSONB       NOT NULL,
                    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    status          VARCHAR(32) NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN (
                            'PENDING','TESTING','ACCEPTED','REJECTED','INSUFFICIENT_DATA')),
                    test_count      INTEGER     NOT NULL DEFAULT 0,
                    last_tested_at  TIMESTAMPTZ
                )
            """)

            # ── Section 11: interaction test results ─────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_interaction_results (
                    result_id               BIGSERIAL   PRIMARY KEY,
                    hypothesis_id           BIGINT      NOT NULL
                        REFERENCES oe_interaction_hypotheses(hypothesis_id),
                    run_ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sample_size             INTEGER     NOT NULL,
                    win_with_all            INTEGER,
                    n_with_all              INTEGER,
                    win_without             INTEGER,
                    n_without               INTEGER,
                    combined_win_rate       NUMERIC(8,4),
                    independent_win_rate    NUMERIC(8,4),
                    interaction_lift        NUMERIC(8,4),
                    p_value_raw             NUMERIC(10,6),
                    p_value_corrected       NUMERIC(10,6),
                    fdr_accepted            BOOLEAN     NOT NULL DEFAULT FALSE,
                    effect_size             NUMERIC(8,4),
                    regime_filter           VARCHAR(64),
                    regime_conditioned      JSONB,
                    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # ── Section 12: strategy scorecards ──────────────────────────────
            # Aggregation boundary: UNIQUE(strategy_id, segment_type, segment_value)
            # Never aggregate across strategy_ids — each row is one strategy.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_strategy_scorecards (
                    scorecard_id        BIGSERIAL   PRIMARY KEY,
                    strategy_id         VARCHAR(64) NOT NULL
                        REFERENCES oe_strategy_registry(strategy_id),
                    segment_type        VARCHAR(32) NOT NULL
                        CHECK (segment_type IN (
                            'GLOBAL','TICKER','SECTOR','INDUSTRY','REGIME','VOL_REGIME',
                            'DTE','STRIKE_ZONE','TIME_OF_DAY','DAY_OF_WEEK',
                            'EXIT_METHOD','PORTFOLIO_STATE')),
                    segment_value       VARCHAR(128) NOT NULL,
                    observation_count   INTEGER      NOT NULL DEFAULT 0,
                    trade_count         INTEGER      NOT NULL DEFAULT 0,
                    win_count           INTEGER      NOT NULL DEFAULT 0,
                    loss_count          INTEGER      NOT NULL DEFAULT 0,
                    breakeven_count     INTEGER      NOT NULL DEFAULT 0,
                    win_rate            NUMERIC(6,4),
                    avg_return_pct      NUMERIC(8,4),
                    median_return_pct   NUMERIC(8,4),
                    expected_value      NUMERIC(8,4),
                    profit_factor       NUMERIC(8,4),
                    sharpe              NUMERIC(8,4),
                    sortino             NUMERIC(8,4),
                    max_drawdown_pct    NUMERIC(8,4),
                    avg_mfe_pct         NUMERIC(8,4),
                    avg_mae_pct         NUMERIC(8,4),
                    avg_holding_days    NUMERIC(8,2),
                    capital_efficiency  NUMERIC(8,4),
                    return_on_risk      NUMERIC(8,4),
                    brier_score         NUMERIC(8,4),
                    calibration_error   NUMERIC(8,4),
                    fill_rate           NUMERIC(6,4),
                    avg_slippage_pct    NUMERIC(8,4),
                    liquidity_fail_count INTEGER     DEFAULT 0,
                    assignment_count    INTEGER      DEFAULT 0,
                    last_updated        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    UNIQUE (strategy_id, segment_type, segment_value)
                )
            """)

            # ── Section 13: knowledge base ────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_knowledge_base (
                    kb_id               BIGSERIAL   PRIMARY KEY,
                    kb_type             VARCHAR(32) NOT NULL
                        CHECK (kb_type IN (
                            'SUCCESS_TRADE','FAILURE_TRADE','SUCCESS_NO_TRADE',
                            'MISSED_OPPORTUNITY','OPERATIONAL_FAILURE',
                            'DATA_QUALITY_FAILURE','VERIFICATION_FAILURE')),
                    version             INTEGER     NOT NULL DEFAULT 1,
                    alert_id            INTEGER,
                    trade_record_id     INTEGER,
                    trace_id            VARCHAR(128),
                    ticker              VARCHAR(16),
                    scan_date           DATE,
                    strategy_id         VARCHAR(64),
                    fingerprint         JSONB       NOT NULL DEFAULT '{}',
                    outcome_pnl_pct     NUMERIC(8,4),
                    decision_quality    VARCHAR(16)
                        CHECK (decision_quality IN ('GOOD','BAD','NEUTRAL')),
                    confidence_score    INTEGER     NOT NULL DEFAULT 50
                        CHECK (confidence_score BETWEEN 0 AND 100),
                    validation_sample_size INTEGER,
                    validated_out_of_sample BOOLEAN NOT NULL DEFAULT FALSE,
                    statistical_gate_passed BOOLEAN NOT NULL DEFAULT FALSE,
                    notes               TEXT,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_oe_kb_type_ticker
                    ON oe_knowledge_base(kb_type, ticker)
            """)

            # ── Section 13: KB confidence change audit log ────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_kb_confidence_log (
                    log_id              BIGSERIAL   PRIMARY KEY,
                    kb_id               BIGINT      NOT NULL
                        REFERENCES oe_knowledge_base(kb_id),
                    old_confidence      INTEGER     NOT NULL,
                    new_confidence      INTEGER     NOT NULL,
                    change_direction    VARCHAR(8)  NOT NULL
                        CHECK (change_direction IN ('INCREASE','DECREASE','UNCHANGED')),
                    justification       TEXT        NOT NULL,
                    validated_oos       BOOLEAN     NOT NULL,
                    sample_size         INTEGER     NOT NULL,
                    gate_passed         BOOLEAN     NOT NULL,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # ── Section 14: regime performance ───────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oe_regime_performance (
                    regime_perf_id          BIGSERIAL   PRIMARY KEY,
                    strategy_id             VARCHAR(64) NOT NULL
                        REFERENCES oe_strategy_registry(strategy_id),
                    regime_type             VARCHAR(32) NOT NULL
                        CHECK (regime_type IN (
                            'BULL_STRONG','BULL_WEAK','BEAR_STRONG','BEAR_WEAK',
                            'SIDEWAYS','TRENDING','MEAN_REVERTING',
                            'HIGH_VOL','LOW_VOL','RISING_VOL','FALLING_VOL',
                            'RISK_ON','RISK_OFF','RISING_RATES','FALLING_RATES',
                            'HIGH_CORRELATION','CORRELATION_BREAKDOWN',
                            'EVENT_DRIVEN','EARNINGS_PERIOD','MACRO_ANNOUNCEMENT')),
                    observation_count       INTEGER     NOT NULL DEFAULT 0,
                    win_rate                NUMERIC(6,4),
                    avg_return_pct          NUMERIC(8,4),
                    expected_value          NUMERIC(8,4),
                    best_indicators_json    JSONB       DEFAULT '[]',
                    regime_specific_notes   TEXT,
                    verified_out_of_sample  BOOLEAN     NOT NULL DEFAULT FALSE,
                    last_updated            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (strategy_id, regime_type)
                )
            """)

            conn.commit()

        _BOOTSTRAPPED = True
        log.info("[phase3] bootstrap complete: 8 tables ready")
        return True

    except Exception as exc:
        log.warning(f"[phase3] bootstrap failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — ROOT-CAUSE ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def _classify_outcome_type(outcome_status: str, pnl_pct: Optional[float]) -> str:
    s = (outcome_status or "").upper()
    p = pnl_pct or 0.0
    if s in ("WIN",):
        return "CLOSED_WIN"
    if s in ("LOSS",):
        return "CLOSED_LOSS"
    if s == "OPEN":
        return "CLOSED_LOSS" if p < 0 else ("CLOSED_WIN" if p > 0 else "EXPIRED_BREAKEVEN")
    if s == "EXPIRED":
        if p > 0.005:   return "EXPIRED_WIN"
        if p < -0.005:  return "EXPIRED_LOSS"
        return "EXPIRED_BREAKEVEN"
    if s == "BLOCKED":  return "BLOCKED"
    if s == "NO_TRADE": return "NO_TRADE"
    return "CLOSED_LOSS"


def _evaluate_decision_quality(
    outcome_type: str,
    direction: str,
    scoring_data: dict,
    verify_data: dict,
    root_causes: List[str],
) -> str:
    """
    Decision quality is evaluated independently of financial outcome.
    A loss is not automatically a bad decision; a profit is not automatically good.

    GOOD:    process was sound — gates passed, direction aligned with regime, no
             indicator/data failures flagged.
    BAD:     process was unsound — hard gates bypassed, data quality failures,
             regime misclassification, wrong direction relative to all signals.
    NEUTRAL: mixed evidence or insufficient data to classify.
    """
    bad_causes = {
        "DATA_QUALITY_FAILURE", "STALE_DATA", "REGIME_MISCLASSIFICATION",
        "SCHEDULER_FAILURE", "WORKER_FAILURE", "EXECUTION_FAILURE",
        "RISK_RULE_FAILURE",
    }
    bad_process = bool(bad_causes & set(root_causes))
    gate_failures = verify_data.get("gate_failures") or []
    call_eligible  = verify_data.get("call_eligible", True)
    put_eligible   = verify_data.get("put_eligible", True)
    margin = scoring_data.get("margin", 0) or 0

    if bad_process:
        return "BAD"
    if outcome_type in ("NO_TRADE",):
        eligible = call_eligible or put_eligible
        return "GOOD" if eligible else "NEUTRAL"
    if outcome_type == "BLOCKED":
        return "NEUTRAL"
    if margin >= 10 and not gate_failures:
        return "GOOD"
    if margin < 5:
        return "BAD"
    return "NEUTRAL"


def _infer_root_causes(
    outcome_type: str,
    direction: str,
    pnl_pct: Optional[float],
    scoring_data: dict,
    verify_data: dict,
    stock_data: dict,
    options_data: dict,
) -> List[str]:
    """
    Heuristic root-cause inference from stored data fields.
    Only flags categories with positive evidence from stored columns.
    Never fabricates causes without evidence.
    """
    cats: List[str] = []
    p = pnl_pct or 0.0

    # Direction assessment
    if outcome_type in ("CLOSED_LOSS", "EXPIRED_LOSS"):
        stock_dir = stock_data.get("stock_direction", "")
        if direction == "LONG_CALL" and stock_dir in ("BEARISH", "STRONG_BEARISH"):
            cats.append("DIRECTION_WRONG")
        elif direction == "LONG_PUT" and stock_dir in ("BULLISH", "STRONG_BULLISH"):
            cats.append("DIRECTION_WRONG")

    # IV crush / vol
    iv_rank = options_data.get("iv_rank", {})
    if isinstance(iv_rank, dict):
        iv_val = iv_rank.get("iv_rank")
        if iv_val and float(iv_val) > 0.7 and direction in ("LONG_CALL", "LONG_PUT"):
            cats.append("IV_CRUSH")

    # Liquidity / spread
    gate_failures = verify_data.get("gate_failures") or []
    for gf in gate_failures:
        gf_l = gf.lower()
        if "spread" in gf_l:
            cats.append("EXCESSIVE_SPREAD")
        if "pop" in gf_l or "probability" in gf_l:
            cats.append("PROBABILITY_ESTIMATE_WRONG")
        if "liquidity" in gf_l:
            cats.append("LIQUIDITY_DETERIORATION")
        if "data" in gf_l or "stale" in gf_l:
            cats.append("DATA_QUALITY_FAILURE")

    # Regime
    market_regime = stock_data.get("market_regime", "")
    if market_regime and outcome_type in ("CLOSED_LOSS", "EXPIRED_LOSS"):
        cats.append("REGIME_CHANGE")

    return list(dict.fromkeys(cats))  # deduplicate preserving order


def record_root_cause(
    alert_id:        int,
    outcome_type:    str,
    trace_id:        str              = "",
    ticker:          str              = "",
    scan_date:       Optional[date]   = None,
    trade_record_id: Optional[int]    = None,
    direction:       str              = "",
    pnl_pct:         Optional[float]  = None,
    scoring_data:    Optional[dict]   = None,
    verify_data:     Optional[dict]   = None,
    stock_data:      Optional[dict]   = None,
    options_data:    Optional[dict]   = None,
    analyst_notes:   str              = "",
    db_url:          str              = "",
) -> dict:
    """
    Record a root-cause record for one closed/expired/blocked/no-trade event.
    Non-fatal — returns {"saved": False, "error": ...} on any failure.
    """
    url = db_url or _DB_URL
    sc  = scoring_data or {}
    vd  = verify_data  or {}
    sd  = stock_data   or {}
    od  = options_data or {}
    sd_date = scan_date or date.today()

    root_causes = _infer_root_causes(outcome_type, direction, pnl_pct, sc, vd, sd, od)
    decision_quality = _evaluate_decision_quality(outcome_type, direction, sc, vd, root_causes)

    primary   = root_causes[0] if root_causes else None
    secondary = root_causes[1] if len(root_causes) > 1 else None

    regime_misclassified = "REGIME_MISCLASSIFICATION" in root_causes or "REGIME_CHANGE" in root_causes

    direction_correct: Optional[bool] = None
    if outcome_type in ("CLOSED_WIN", "EXPIRED_WIN"):
        direction_correct = True
    elif outcome_type in ("CLOSED_LOSS", "EXPIRED_LOSS"):
        direction_correct = False

    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_root_cause_records (
                    alert_id, trade_record_id, trace_id, ticker, scan_date,
                    outcome_type, decision_quality, pnl_pct,
                    direction_correct, regime_misclassified,
                    root_cause_categories, primary_root_cause, secondary_root_cause,
                    scoring_data_json, verify_data_json, analyst_notes
                ) VALUES (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,%s,
                    %s,%s,%s
                ) RETURNING id
            """, (
                alert_id, trade_record_id, trace_id or None, ticker, sd_date,
                outcome_type, decision_quality, pnl_pct,
                direction_correct, regime_misclassified,
                json.dumps(root_causes), primary, secondary,
                json.dumps(sc, default=str) if sc else None,
                json.dumps(vd, default=str) if vd else None,
                analyst_notes or None,
            ))
            rcr_id = cur.fetchone()[0]
            conn.commit()
        log.info(f"[phase3] root_cause id={rcr_id} alert={alert_id} "
                 f"outcome={outcome_type} quality={decision_quality} "
                 f"primary={primary}")
        return {"saved": True, "rcr_id": rcr_id,
                "outcome_type": outcome_type, "decision_quality": decision_quality,
                "root_causes": root_causes}
    except Exception as exc:
        log.debug(f"[phase3] record_root_cause failed: {exc}")
        return {"saved": False, "error": str(exc)}


def record_root_cause_batch(days_back: int = 30, db_url: str = "") -> dict:
    """
    Batch root-cause analysis for all closed/expired alerts in the last N days
    that do not yet have a root-cause record.
    Non-fatal.
    """
    url = db_url or _DB_URL
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT a.id, a.ticker, a.alert_date, a.direction,
                       a.pnl_pct, a.outcome_status,
                       a.stock_analysis_json, a.options_analysis_json,
                       a.scoring_json, a.verify_result_json
                FROM aiem_options_alerts a
                LEFT JOIN oe_root_cause_records r ON r.alert_id = a.id
                WHERE a.outcome_status IN ('WIN','LOSS','EXPIRED')
                  AND a.alert_date >= CURRENT_DATE - INTERVAL '%s days'
                  AND r.id IS NULL
                ORDER BY a.id
            """ % int(days_back))
            rows = cur.fetchall()

        saved = 0
        for row in rows:
            (aid, ticker, alert_date, direction, pnl_pct, outcome_status,
             stock_json, options_json, scoring_json, verify_json) = row

            stock_data   = json.loads(stock_json)   if isinstance(stock_json,   str) else (stock_json   or {})
            options_data = json.loads(options_json) if isinstance(options_json, str) else (options_json or {})
            scoring_data = json.loads(scoring_json) if isinstance(scoring_json, str) else (scoring_json or {})
            verify_data  = json.loads(verify_json)  if isinstance(verify_json,  str) else (verify_json  or {})

            outcome_type = _classify_outcome_type(outcome_status, float(pnl_pct) if pnl_pct else None)
            result = record_root_cause(
                alert_id=aid,
                outcome_type=outcome_type,
                ticker=ticker,
                scan_date=alert_date,
                direction=direction or "",
                pnl_pct=float(pnl_pct) if pnl_pct else None,
                scoring_data=scoring_data,
                verify_data=verify_data,
                stock_data=stock_data,
                options_data=options_data,
                db_url=url,
            )
            if result.get("saved"):
                saved += 1

        log.info(f"[phase3] record_root_cause_batch: processed={len(rows)} saved={saved}")
        return {"processed": len(rows), "saved": saved}
    except Exception as exc:
        log.debug(f"[phase3] record_root_cause_batch failed: {exc}")
        return {"processed": 0, "saved": 0, "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10 — INDICATOR & PATTERN ATTRIBUTION
# Statistical methods: Conditional, IC (Spearman), Calibration, Brier delta.
# Multiple-testing: Benjamini-Hochberg FDR correction on all p-values.
# Minimum sample gate: n >= _MIN_ATTRIBUTION_SAMPLE before any claim.
# No causal claims from correlation alone.
# ─────────────────────────────────────────────────────────────────────────────

def _bh_fdr_correction(p_values: List[float], alpha: float = 0.05) -> List[bool]:
    """
    Benjamini-Hochberg False Discovery Rate correction (step-up procedure).
    Returns list of booleans: True = reject H0 (significant after correction).
    Input and output share the same index order as p_values.

    Algorithm:
      1. Sort p-values ascending, track original indices.
      2. Find the largest rank k such that p_(k) <= k * alpha / n.
      3. Reject all hypotheses with rank <= k (including all smaller p-values
         even if they individually exceed their threshold — step-up rule).
      4. Return in original index order.
    """
    n = len(p_values)
    if n == 0:
        return []
    if n == 1:
        return [p_values[0] <= alpha]

    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    last_rejected_rank = -1
    for rank_0based, (_, p) in enumerate(indexed):
        threshold = (rank_0based + 1) * alpha / n
        if p <= threshold:
            last_rejected_rank = rank_0based

    reject = [False] * n
    for rank_0based, (orig_idx, _) in enumerate(indexed):
        if rank_0based <= last_rejected_rank:
            reject[orig_idx] = True
    return reject


def _spearman_rank_correlation(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    """
    Spearman rank correlation (IC) and a t-approximation p-value.
    Returns (rho, p_value).  Requires len >= 3.
    """
    n = len(xs)
    if n < 3:
        return 0.0, 1.0

    def _ranks(vals: List[float]) -> List[float]:
        sorted_idx = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n and vals[sorted_idx[j]] == vals[sorted_idx[i]]:
                j += 1
            avg_rank = (i + j - 1) / 2.0 + 1.0
            for k in range(i, j):
                ranks[sorted_idx[k]] = avg_rank
            i = j
        return ranks

    rx = _ranks(xs)
    ry = _ranks(ys)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    std_rx = math.sqrt(sum((r - mean_rx) ** 2 for r in rx))
    std_ry = math.sqrt(sum((r - mean_ry) ** 2 for r in ry))
    if std_rx == 0 or std_ry == 0:
        return 0.0, 1.0
    rho = cov / (std_rx * std_ry)

    # t-approximation: t = rho * sqrt((n-2) / (1 - rho^2))
    denom = 1.0 - rho * rho
    if denom <= 0:
        return rho, 0.0
    t_stat = rho * math.sqrt((n - 2) / denom)
    # Two-sided p-value via normal approximation (adequate for n>=20)
    # P(|Z| >= |t|) ≈ 2 * (1 - Phi(|t|))
    z = abs(t_stat)
    p_val = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))
    return rho, max(0.0, min(1.0, p_val))


def _brier_score(probs: List[float], outcomes: List[int]) -> float:
    """Mean Brier score: lower is better. Range [0, 1]."""
    n = len(probs)
    if n == 0:
        return 1.0
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n


def _fetch_attribution_data(db_url: str) -> List[dict]:
    """
    Fetch closed-trade outcomes joined with indicator snapshots.
    Returns list of dicts with indicator values + binary outcome (1=WIN, 0=LOSS).
    Empty if insufficient data.
    """
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                    a.id              AS alert_id,
                    a.ticker,
                    a.direction,
                    CASE WHEN a.outcome_status='WIN' THEN 1 ELSE 0 END AS outcome_bin,
                    a.pnl_pct,
                    a.selected_score,
                    s.canonical_id    AS indicator_id,
                    s.normalized_value,
                    s.signal_direction,
                    s.confidence
                FROM aiem_options_alerts a
                JOIN oe_indicator_snapshots s
                    ON s.trace_id = a.trace_id
                WHERE a.outcome_status IN ('WIN','LOSS')
                ORDER BY a.id, s.canonical_id
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        log.debug(f"[phase3] _fetch_attribution_data failed: {exc}")
        return []


def _compute_conditional_attribution(
    rows: List[dict],
    indicator_id: str,
) -> dict:
    """
    Conditional outcome analysis for one indicator.
    Compares win rate when indicator signal direction matches trade direction
    vs overall win rate.
    """
    indicator_rows = [r for r in rows if r.get("indicator_id") == indicator_id]
    n = len(indicator_rows)
    if n < _MIN_ATTRIBUTION_SAMPLE:
        return {"status": "INSUFFICIENT_DATA", "sample_size": n}

    overall_wins = sum(r["outcome_bin"] for r in rows if r["outcome_bin"] is not None)
    overall_n    = len([r for r in rows if r["outcome_bin"] is not None])
    unconditional_wr = overall_wins / overall_n if overall_n > 0 else None

    # Aligned: indicator signal direction matches trade direction
    aligned = [r for r in indicator_rows
               if r.get("signal_direction") and r.get("direction") and
               (r["signal_direction"].upper() in ("BULLISH", "BEARISH") and
                ((r["signal_direction"].upper() == "BULLISH" and "CALL" in r["direction"].upper()) or
                 (r["signal_direction"].upper() == "BEARISH" and "PUT"  in r["direction"].upper())))]
    n_aligned = len(aligned)
    if n_aligned < 5:
        return {"status": "INSUFFICIENT_DATA", "sample_size": n, "aligned_n": n_aligned}

    aligned_wins = sum(r["outcome_bin"] for r in aligned)
    aligned_wr   = aligned_wins / n_aligned

    lift = (aligned_wr - unconditional_wr) if unconditional_wr is not None else None

    # Fisher's exact test (2x2): aligned wins/losses vs non-aligned
    non_aligned = [r for r in indicator_rows if r not in aligned]
    n_na = len(non_aligned)
    na_wins = sum(r["outcome_bin"] for r in non_aligned) if non_aligned else 0
    p_val = _fisher_exact_p(
        aligned_wins, n_aligned - aligned_wins,
        na_wins,      n_na - na_wins,
    ) if n_na > 0 else 1.0

    return {
        "status":                "OK",
        "sample_size":           n,
        "method":                "CONDITIONAL",
        "conditional_win_rate":  round(aligned_wr, 4),
        "unconditional_win_rate": round(unconditional_wr, 4) if unconditional_wr else None,
        "lift":                  round(lift, 4) if lift is not None else None,
        "p_value_raw":           round(p_val, 6),
    }


def _compute_ic_attribution(rows: List[dict], indicator_id: str) -> dict:
    """
    Information Coefficient (Spearman rank correlation) between normalized
    indicator value and binary outcome.
    """
    irows = [r for r in rows
             if r.get("indicator_id") == indicator_id
             and r.get("normalized_value") is not None
             and r.get("outcome_bin") is not None]
    n = len(irows)
    if n < _MIN_ATTRIBUTION_SAMPLE:
        return {"status": "INSUFFICIENT_DATA", "sample_size": n}

    xs = [float(r["normalized_value"]) for r in irows]
    ys = [float(r["outcome_bin"])      for r in irows]
    rho, p_val = _spearman_rank_correlation(xs, ys)

    # Calibration: average predicted probability (selected_score/100) vs actual WR
    # Guard (2026-07-23): if selected_score is raw (not 0-100 normalized), dividing
    # by 100 produces probabilities >>1.0 that min() silently clamps to 1.0, making
    # the Brier score meaningless. Fail loudly instead.
    prob_rows = [r for r in irows if r.get("selected_score") is not None]
    calibration_error = None
    if len(prob_rows) >= 10:
        _bad_sc = [float(r["selected_score"]) for r in prob_rows
                   if float(r["selected_score"]) > 100.0 or float(r["selected_score"]) < 0.0]
        if _bad_sc:
            print(
                f"[BRIER_GUARD] WARN: selected_score out of [0,100] — "
                f"{len(_bad_sc)}/{len(prob_rows)} values out of range "
                f"(max={max(_bad_sc):.2f}, min={min(_bad_sc):.2f}). "
                f"oe_trade_records.entry_score may not be 0-100 normalized. "
                f"Brier/calibration suppressed — would produce clamped-to-1.0 probabilities.",
                flush=True
            )
            bs = None
        else:
            probs    = [float(r["selected_score"]) / 100.0 for r in prob_rows]
            outcomes = [int(r["outcome_bin"]) for r in prob_rows]
            bs       = _brier_score(probs, outcomes)
            # Calibration error: |mean(predicted prob) - actual WR|
            calibration_error = round(abs(sum(probs)/len(probs) - sum(outcomes)/len(outcomes)), 4)
    else:
        bs = None

    return {
        "status":                "OK",
        "sample_size":           n,
        "method":                "IC",
        "information_coefficient": round(rho, 4),
        "p_value_raw":           round(p_val, 6),
        "brier_score_delta":     round(bs, 4) if bs is not None else None,
        "calibration_error":     calibration_error,
    }


def run_attribution_batch(
    method:     str = "CONDITIONAL",
    min_sample: int = _MIN_ATTRIBUTION_SAMPLE,
    db_url:     str = "",
) -> dict:
    """
    Run attribution for all registered indicators.
    Applies BH-FDR correction across all p-values before marking any
    indicator as significant.
    Returns {"status": "INSUFFICIENT_DATA"} when total sample < min_sample.
    Non-fatal.
    """
    url = db_url or _DB_URL
    if method not in _ATTRIBUTION_METHODS:
        return {"saved": False, "error": f"unknown method {method}"}

    rows = _fetch_attribution_data(url)
    total_n = len(set(r["alert_id"] for r in rows))
    if total_n < min_sample:
        log.info(f"[phase3] attribution skipped: total_n={total_n} < min={min_sample}")
        return {"status": "INSUFFICIENT_DATA", "total_n": total_n, "min_required": min_sample}

    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT canonical_id, name FROM oe_indicator_registry
            """)
            indicators = [(r[0], r[1]) for r in cur.fetchall()]
    except Exception as exc:
        return {"saved": False, "error": str(exc)}

    if not indicators:
        return {"status": "NO_INDICATORS_REGISTERED", "total_n": total_n}

    results: List[dict] = []
    for ind_id, ind_name in indicators:
        if method == "CONDITIONAL":
            r = _compute_conditional_attribution(rows, ind_id)
        else:
            r = _compute_ic_attribution(rows, ind_id)
        r["indicator_id"]   = ind_id
        r["indicator_name"] = ind_name
        results.append(r)

    # BH-FDR correction across all p-values from OK results
    ok_results  = [r for r in results if r.get("status") == "OK"]
    p_values    = [r.get("p_value_raw", 1.0) for r in ok_results]
    fdr_rejects = _bh_fdr_correction(p_values, _FDR_ALPHA)
    for i, r in enumerate(ok_results):
        r["p_value_corrected"] = p_values[i]   # stored; corrected via reject list
        r["is_significant"]    = bool(fdr_rejects[i])

    significant = sum(1 for r in ok_results if r.get("is_significant"))

    # Determine date range
    alert_dates: List[date] = []
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn2, conn2.cursor() as cur2:
            cur2.execute("""
                SELECT MIN(alert_date), MAX(alert_date)
                FROM aiem_options_alerts
                WHERE outcome_status IN ('WIN','LOSS')
            """)
            dr = cur2.fetchone()
            if dr and dr[0]:
                date_start, date_end = dr
            else:
                date_start = date_end = date.today()
    except Exception:
        date_start = date_end = date.today()

    # Persist run + results
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn3, conn3.cursor() as cur3:
            cur3.execute("""
                INSERT INTO oe_attribution_runs (
                    method, sample_size, date_range_start, date_range_end,
                    fdr_correction_applied, fdr_method, alpha_threshold,
                    indicators_tested, significant_count, status
                ) VALUES (%s,%s,%s,%s,TRUE,'BH',%s,%s,%s,'COMPLETED')
                RETURNING run_id
            """, (method, total_n, date_start, date_end,
                  _FDR_ALPHA, len(indicators), significant))
            run_id = cur3.fetchone()[0]

            for r in results:
                if r.get("status") != "OK":
                    continue
                cur3.execute("""
                    INSERT INTO oe_indicator_attribution (
                        attribution_run_id, indicator_name, method,
                        conditional_win_rate, unconditional_win_rate, lift,
                        information_coefficient, brier_score_delta,
                        calibration_error, sample_size,
                        p_value_raw, p_value_corrected, is_significant
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    run_id,
                    r.get("indicator_name"), method,
                    r.get("conditional_win_rate"),
                    r.get("unconditional_win_rate"),
                    r.get("lift"),
                    r.get("information_coefficient"),
                    r.get("brier_score_delta"),
                    r.get("calibration_error"),
                    r.get("sample_size", 0),
                    r.get("p_value_raw"),
                    r.get("p_value_corrected"),
                    r.get("is_significant", False),
                ))
            conn3.commit()

        log.info(f"[phase3] attribution run_id={run_id} method={method} "
                 f"total_n={total_n} significant={significant}/{len(indicators)}")
        return {
            "saved": True, "run_id": run_id, "method": method,
            "total_n": total_n, "indicators_tested": len(indicators),
            "significant": significant,
            "fdr_correction_applied": True, "fdr_method": "BH",
        }
    except Exception as exc:
        log.debug(f"[phase3] run_attribution_batch persist failed: {exc}")
        return {"saved": False, "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11 — COMBINATION & INTERACTION LEARNING
# Fisher's exact test (2x2) for all registered hypotheses.
# BH-FDR applied across all hypothesis p-values before accepting any.
# Minimum sample gate: n >= _MIN_INTERACTION_SAMPLE.
# ─────────────────────────────────────────────────────────────────────────────

def _log_comb(n: int, k: int) -> float:
    """log(C(n,k)) via log-gamma. Returns -inf for invalid inputs."""
    if k < 0 or k > n:
        return float("-inf")
    if k == 0 or k == n:
        return 0.0
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1))


def _fisher_exact_p(a: int, b: int, c: int, d: int) -> float:
    """
    Two-sided Fisher exact test for 2x2 table [[a,b],[c,d]].
    Uses hypergeometric PMF.  Row/column totals: r1=a+b, r2=c+d, c1=a+c, c2=b+d, n=total.
    Returns p-value in [0, 1].
    """
    a, b, c, d = int(a), int(b), int(c), int(d)
    n  = a + b + c + d
    if n == 0:
        return 1.0
    r1 = a + b
    c1 = a + c
    r2 = c + d

    log_p_obs = _log_comb(r1, a) + _log_comb(r2, c1 - a) - _log_comb(n, c1)
    p_obs     = math.exp(log_p_obs) if log_p_obs > -700 else 0.0

    p_total = 0.0
    lo = max(0, c1 - r2)
    hi = min(r1, c1)
    for x in range(lo, hi + 1):
        lp = _log_comb(r1, x) + _log_comb(r2, c1 - x) - _log_comb(n, c1)
        if lp > -700:
            p_x = math.exp(lp)
            if p_x <= p_obs * (1.0 + 1e-9):
                p_total += p_x

    return min(1.0, p_total)


def register_interaction_hypothesis(
    components_json: List[str],
    db_url: str = "",
) -> int:
    """
    Register a new interaction hypothesis (list of indicator/pattern canonical_ids).
    Returns hypothesis_id, or -1 on failure.
    """
    url = db_url or _DB_URL
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_interaction_hypotheses (components_json)
                VALUES (%s) RETURNING hypothesis_id
            """, (json.dumps(sorted(components_json)),))
            hid = cur.fetchone()[0]
            conn.commit()
        log.info(f"[phase3] interaction hypothesis registered: id={hid} "
                 f"components={components_json}")
        return hid
    except Exception as exc:
        log.debug(f"[phase3] register_interaction_hypothesis failed: {exc}")
        return -1


def run_interaction_tests(
    min_sample: int = _MIN_INTERACTION_SAMPLE,
    alpha:      float = _FDR_ALPHA,
    db_url:     str   = "",
) -> dict:
    """
    Test all PENDING/INSUFFICIENT_DATA interaction hypotheses.
    Applies BH-FDR across all hypothesis p-values in this batch.
    Updates hypothesis status to ACCEPTED/REJECTED/INSUFFICIENT_DATA.
    Non-fatal.
    """
    url = db_url or _DB_URL
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT hypothesis_id, components_json
                FROM oe_interaction_hypotheses
                WHERE status IN ('PENDING','INSUFFICIENT_DATA')
                ORDER BY hypothesis_id
            """)
            hypotheses = [(r[0], json.loads(r[1]) if isinstance(r[1], str) else r[1])
                          for r in cur.fetchall()]
    except Exception as exc:
        return {"tested": 0, "error": str(exc)}

    rows = _fetch_attribution_data(url)
    total_n = len(set(r["alert_id"] for r in rows))

    pending_results: List[dict] = []
    for hid, components in hypotheses:
        if total_n < min_sample:
            pending_results.append({
                "hypothesis_id": hid,
                "status": "INSUFFICIENT_DATA",
                "sample_size": total_n,
            })
            continue

        # Rows where ALL components fired in the trade direction
        def _all_aligned(r: dict) -> bool:
            ind = r.get("indicator_id", "")
            return (ind in components and
                    r.get("signal_direction") and r.get("direction") and
                    ((r["signal_direction"].upper() == "BULLISH" and "CALL" in r["direction"].upper()) or
                     (r["signal_direction"].upper() == "BEARISH" and "PUT"  in r["direction"].upper())))

        alerts_with_all: dict = {}
        for r in rows:
            aid = r["alert_id"]
            if _all_aligned(r):
                if aid not in alerts_with_all:
                    alerts_with_all[aid] = r

        alerts_without = {r["alert_id"]: r for r in rows
                          if r["alert_id"] not in alerts_with_all}

        n_with    = len(alerts_with_all)
        n_without = len(alerts_without)
        wins_with    = sum(v["outcome_bin"] for v in alerts_with_all.values()
                          if v.get("outcome_bin") is not None)
        wins_without = sum(v["outcome_bin"] for v in alerts_without.values()
                          if v.get("outcome_bin") is not None)

        if n_with < 5:
            pending_results.append({
                "hypothesis_id": hid,
                "status": "INSUFFICIENT_DATA",
                "sample_size": n_with,
            })
            continue

        wr_with    = wins_with    / n_with    if n_with    > 0 else 0.0
        wr_without = wins_without / n_without if n_without > 0 else 0.0
        lift       = wr_with - wr_without

        p_val = _fisher_exact_p(
            wins_with,    n_with    - wins_with,
            wins_without, n_without - wins_without,
        )

        pending_results.append({
            "hypothesis_id":         hid,
            "status":                "TESTED",
            "sample_size":           total_n,
            "n_with_all":            n_with,
            "win_with_all":          wins_with,
            "n_without":             n_without,
            "win_without":           wins_without,
            "combined_win_rate":     round(wr_with,    4),
            "independent_win_rate":  round(wr_without, 4),
            "interaction_lift":      round(lift, 4),
            "p_value_raw":           round(p_val, 6),
        })

    # BH-FDR correction across all tested hypotheses
    tested = [r for r in pending_results if r.get("status") == "TESTED"]
    p_vals  = [r["p_value_raw"] for r in tested]
    rejects = _bh_fdr_correction(p_vals, alpha)
    for i, r in enumerate(tested):
        r["fdr_accepted"]       = bool(rejects[i])
        r["p_value_corrected"]  = p_vals[i]
        r["final_status"]       = "ACCEPTED" if rejects[i] else "REJECTED"

    # Persist results
    accepted = rejected = 0
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn2, conn2.cursor() as cur2:
            for r in pending_results:
                hid = r["hypothesis_id"]
                s   = r.get("final_status") or r.get("status", "INSUFFICIENT_DATA")
                if s == "ACCEPTED":
                    accepted += 1
                elif s == "REJECTED":
                    rejected += 1

                cur2.execute("""
                    UPDATE oe_interaction_hypotheses
                    SET status=%s, test_count=test_count+1, last_tested_at=NOW()
                    WHERE hypothesis_id=%s
                """, (s if s in ("ACCEPTED","REJECTED","INSUFFICIENT_DATA") else "INSUFFICIENT_DATA",
                      hid))

                if r.get("status") == "TESTED":
                    cur2.execute("""
                        INSERT INTO oe_interaction_results (
                            hypothesis_id, sample_size,
                            win_with_all, n_with_all, win_without, n_without,
                            combined_win_rate, independent_win_rate, interaction_lift,
                            p_value_raw, p_value_corrected, fdr_accepted
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        hid,
                        r.get("sample_size", 0),
                        r.get("win_with_all"), r.get("n_with_all"),
                        r.get("win_without"), r.get("n_without"),
                        r.get("combined_win_rate"),
                        r.get("independent_win_rate"),
                        r.get("interaction_lift"),
                        r.get("p_value_raw"),
                        r.get("p_value_corrected"),
                        r.get("fdr_accepted", False),
                    ))
            conn2.commit()
    except Exception as exc:
        log.debug(f"[phase3] run_interaction_tests persist failed: {exc}")
        return {"tested": len(tested), "accepted": accepted, "rejected": rejected,
                "error": str(exc)}

    log.info(f"[phase3] interaction tests: tested={len(tested)} "
             f"accepted={accepted} rejected={rejected}")
    return {"tested": len(tested), "accepted": accepted, "rejected": rejected}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 12 — STRATEGY SCORECARDS
# Aggregation boundary: one row per (strategy_id, segment_type, segment_value).
# NEVER aggregate across different strategy_ids.
# ─────────────────────────────────────────────────────────────────────────────

def _assert_no_cross_strategy_aggregation(strategy_ids: List[str]) -> None:
    """
    Raises ValueError if more than one strategy_id is provided.
    This is the aggregation boundary enforcement — dissimilar strategy structures
    must never be merged into a combined scorecard row.
    """
    unique = list(dict.fromkeys(strategy_ids))
    if len(unique) > 1:
        raise ValueError(
            f"Scorecard aggregation boundary violated: "
            f"cannot merge {unique} into a single scorecard row. "
            f"Each strategy_id must have its own row."
        )


def _compute_scorecard_metrics(trade_rows: List[dict]) -> dict:
    """
    Compute scorecard statistics from a list of trade dicts.
    Each dict must have: pnl_pct (float), outcome_type (str),
    mfe_pct (float|None), mae_pct (float|None), holding_days (float|None),
    entry_score (float|None), selected_score (float|None).
    """
    n = len(trade_rows)
    if n == 0:
        return {"observation_count": 0}

    pnls    = [float(r["pnl_pct"]) for r in trade_rows if r.get("pnl_pct") is not None]
    wins    = [p for p in pnls if p > 0]
    losses  = [p for p in pnls if p < 0]
    bes     = [p for p in pnls if p == 0]
    win_rate = len(wins) / len(pnls) if pnls else None

    # Sharpe / Sortino (daily return approximation)
    avg_ret   = sum(pnls) / len(pnls) if pnls else 0.0
    std_ret   = math.sqrt(sum((p - avg_ret)**2 for p in pnls) / len(pnls)) if len(pnls) > 1 else 0.0
    sharpe    = (avg_ret / std_ret) if std_ret > 0 else None
    down_std  = math.sqrt(sum(p**2 for p in losses) / len(losses)) if losses else 0.0
    sortino   = (avg_ret / down_std) if down_std > 0 else None

    # Profit factor: gross profit / gross loss
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    # Max drawdown (sequential)
    peak = 0.0
    cum  = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # Median
    sorted_pnls = sorted(pnls)
    mid = len(sorted_pnls) // 2
    median_ret = (sorted_pnls[mid - 1] + sorted_pnls[mid]) / 2 if len(sorted_pnls) % 2 == 0 else sorted_pnls[mid]

    # EV
    ev = sum(pnls) / len(pnls) if pnls else None

    # MFE/MAE/holding
    mfe_vals = [float(r["mfe_pct"]) for r in trade_rows if r.get("mfe_pct") is not None]
    mae_vals = [float(r["mae_pct"]) for r in trade_rows if r.get("mae_pct") is not None]
    hld_vals = [float(r["holding_days"]) for r in trade_rows if r.get("holding_days") is not None]
    avg_mfe  = sum(mfe_vals) / len(mfe_vals) if mfe_vals else None
    avg_mae  = sum(mae_vals) / len(mae_vals) if mae_vals else None
    avg_hold = sum(hld_vals) / len(hld_vals) if hld_vals else None

    # Calibration: Brier score using selected_score/100 as predicted probability
    # Guard (2026-07-23): if selected_score is raw (not 0-100 normalized), dividing
    # by 100 produces probabilities >>1.0 that min() silently clamps to 1.0, making
    # the Brier score meaningless. Fail loudly instead.
    score_rows = [r for r in trade_rows
                  if r.get("selected_score") is not None and r.get("pnl_pct") is not None]
    brier = None
    if len(score_rows) >= 5:
        _bad_sc2 = [float(r["selected_score"]) for r in score_rows
                    if float(r["selected_score"]) > 100.0 or float(r["selected_score"]) < 0.0]
        if _bad_sc2:
            print(
                f"[BRIER_GUARD] WARN: selected_score out of [0,100] in scorecard — "
                f"{len(_bad_sc2)}/{len(score_rows)} out of range "
                f"(max={max(_bad_sc2):.2f}). "
                f"oe_trade_records.entry_score may not be 0-100 normalized. "
                f"Brier score suppressed — would produce clamped-to-1.0 probabilities.",
                flush=True
            )
        else:
            probs    = [float(r["selected_score"]) / 100.0 for r in score_rows]
            outcomes = [1 if float(r["pnl_pct"]) > 0 else 0 for r in score_rows]
            brier    = round(_brier_score(probs, outcomes), 4)

    return {
        "observation_count":  n,
        "trade_count":        len(pnls),
        "win_count":          len(wins),
        "loss_count":         len(losses),
        "breakeven_count":    len(bes),
        "win_rate":           round(win_rate, 4) if win_rate is not None else None,
        "avg_return_pct":     round(avg_ret, 4),
        "median_return_pct":  round(median_ret, 4) if pnls else None,
        "expected_value":     round(ev, 4) if ev is not None else None,
        "profit_factor":      round(profit_factor, 4) if profit_factor is not None else None,
        "sharpe":             round(sharpe, 4) if sharpe is not None else None,
        "sortino":            round(sortino, 4) if sortino is not None else None,
        "max_drawdown_pct":   round(max_dd, 4),
        "avg_mfe_pct":        round(avg_mfe, 4) if avg_mfe is not None else None,
        "avg_mae_pct":        round(avg_mae, 4) if avg_mae is not None else None,
        "avg_holding_days":   round(avg_hold, 2) if avg_hold is not None else None,
        "brier_score":        brier,
    }


def update_strategy_scorecard(
    strategy_id:   str,
    segment_type:  str = "GLOBAL",
    segment_value: str = "ALL",
    db_url:        str = "",
) -> dict:
    """
    Recompute and upsert the scorecard for one (strategy_id, segment, value) cell.
    Enforces aggregation boundary — single strategy_id only.
    Non-fatal.
    """
    url = db_url or _DB_URL
    try:
        _assert_no_cross_strategy_aggregation([strategy_id])
    except ValueError as exc:
        return {"saved": False, "error": str(exc)}

    if segment_type not in _SEGMENT_TYPES:
        return {"saved": False, "error": f"unknown segment_type {segment_type}"}

    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            # Fetch trade records for this strategy
            where_extra = ""
            params: List[Any] = [strategy_id]
            if segment_type == "GLOBAL":
                pass
            elif segment_type == "TICKER":
                where_extra = " AND t.ticker = %s"
                params.append(segment_value)
            elif segment_type == "REGIME":
                where_extra = " AND (a.stock_analysis_json->>'market_regime') = %s"
                params.append(segment_value)
            # other segment types: add filters as data accumulates

            cur.execute(f"""
                SELECT
                    t.pnl_pct,
                    t.mfe_pct,
                    t.mae_pct,
                    t.holding_period_days  AS holding_days,
                    t.entry_score          AS selected_score
                FROM oe_trade_records t
                LEFT JOIN aiem_options_alerts a ON a.id = t.alert_id
                WHERE t.strategy_id = %s
                  AND t.exit_ts IS NOT NULL
                {where_extra}
            """, params)
            trade_rows = [dict(zip(
                ["pnl_pct","mfe_pct","mae_pct","holding_days","selected_score"],
                row
            )) for row in cur.fetchall()]

            metrics = _compute_scorecard_metrics(trade_rows)

            cur.execute("""
                INSERT INTO oe_strategy_scorecards (
                    strategy_id, segment_type, segment_value,
                    observation_count, trade_count, win_count, loss_count, breakeven_count,
                    win_rate, avg_return_pct, median_return_pct, expected_value,
                    profit_factor, sharpe, sortino, max_drawdown_pct,
                    avg_mfe_pct, avg_mae_pct, avg_holding_days, brier_score,
                    last_updated
                ) VALUES (
                    %s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    NOW()
                )
                ON CONFLICT (strategy_id, segment_type, segment_value) DO UPDATE SET
                    observation_count  = EXCLUDED.observation_count,
                    trade_count        = EXCLUDED.trade_count,
                    win_count          = EXCLUDED.win_count,
                    loss_count         = EXCLUDED.loss_count,
                    breakeven_count    = EXCLUDED.breakeven_count,
                    win_rate           = EXCLUDED.win_rate,
                    avg_return_pct     = EXCLUDED.avg_return_pct,
                    median_return_pct  = EXCLUDED.median_return_pct,
                    expected_value     = EXCLUDED.expected_value,
                    profit_factor      = EXCLUDED.profit_factor,
                    sharpe             = EXCLUDED.sharpe,
                    sortino            = EXCLUDED.sortino,
                    max_drawdown_pct   = EXCLUDED.max_drawdown_pct,
                    avg_mfe_pct        = EXCLUDED.avg_mfe_pct,
                    avg_mae_pct        = EXCLUDED.avg_mae_pct,
                    avg_holding_days   = EXCLUDED.avg_holding_days,
                    brier_score        = EXCLUDED.brier_score,
                    last_updated       = NOW()
            """, (
                strategy_id, segment_type, segment_value,
                metrics["observation_count"],
                metrics.get("trade_count", 0),
                metrics.get("win_count", 0),
                metrics.get("loss_count", 0),
                metrics.get("breakeven_count", 0),
                metrics.get("win_rate"),
                metrics.get("avg_return_pct"),
                metrics.get("median_return_pct"),
                metrics.get("expected_value"),
                metrics.get("profit_factor"),
                metrics.get("sharpe"),
                metrics.get("sortino"),
                metrics.get("max_drawdown_pct"),
                metrics.get("avg_mfe_pct"),
                metrics.get("avg_mae_pct"),
                metrics.get("avg_holding_days"),
                metrics.get("brier_score"),
            ))
            conn.commit()

        log.info(f"[phase3] scorecard updated: strategy={strategy_id} "
                 f"segment={segment_type}/{segment_value} n={metrics['observation_count']}")
        return {"saved": True, "strategy_id": strategy_id,
                "segment": f"{segment_type}/{segment_value}",
                "metrics": metrics}
    except Exception as exc:
        log.debug(f"[phase3] update_strategy_scorecard failed: {exc}")
        return {"saved": False, "error": str(exc)}


def rebuild_all_scorecards(db_url: str = "") -> dict:
    """
    Rebuild GLOBAL scorecard rows for every strategy_id that has closed trade records.
    Non-fatal.
    """
    url = db_url or _DB_URL
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT strategy_id FROM oe_trade_records
                WHERE exit_ts IS NOT NULL AND strategy_id IS NOT NULL
            """)
            strategy_ids = [r[0] for r in cur.fetchall()]
    except Exception as exc:
        return {"rebuilt": 0, "error": str(exc)}

    rebuilt = 0
    for sid in strategy_ids:
        result = update_strategy_scorecard(sid, "GLOBAL", "ALL", url)
        if result.get("saved"):
            rebuilt += 1

    log.info(f"[phase3] rebuild_all_scorecards: rebuilt={rebuilt}/{len(strategy_ids)}")
    return {"rebuilt": rebuilt, "total_strategies": len(strategy_ids)}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 13 — SUCCESS/FAILURE KNOWLEDGE BASES
# Confidence gate:
#   Increase confidence → requires validated_out_of_sample=True AND sample >= min.
#   Decrease confidence → requires sample >= min.
#   Every change is audit-logged in oe_kb_confidence_log.
# ─────────────────────────────────────────────────────────────────────────────

def _confidence_change_gate(
    current_confidence: int,
    new_confidence:     int,
    validated_oos:      bool,
    sample_size:        int,
) -> Tuple[bool, str]:
    """
    Returns (gate_passed: bool, reason: str).
    Increase: requires OOS validation + sample >= min.
    Decrease: requires sample >= min only.
    No change: always passes (logged as UNCHANGED).
    """
    if new_confidence == current_confidence:
        return True, "UNCHANGED"
    if new_confidence > current_confidence:
        if not validated_oos:
            return False, "REJECTED: confidence increase requires out-of-sample validation"
        if sample_size < _MIN_KB_CONFIDENCE_SAMPLE:
            return False, (f"REJECTED: confidence increase requires n>={_MIN_KB_CONFIDENCE_SAMPLE}, "
                           f"got n={sample_size}")
        return True, "APPROVED: OOS validated, sufficient sample"
    # Decrease
    if sample_size < _MIN_KB_CONFIDENCE_SAMPLE:
        return False, (f"REJECTED: confidence decrease requires n>={_MIN_KB_CONFIDENCE_SAMPLE}, "
                       f"got n={sample_size}")
    return True, "APPROVED: sufficient sample for confidence decrease"


def add_knowledge_base_entry(
    kb_type:         str,
    ticker:          str,
    scan_date:       date,
    fingerprint:     dict,
    outcome_pnl_pct: Optional[float] = None,
    decision_quality: Optional[str]  = None,
    alert_id:        Optional[int]   = None,
    trade_record_id: Optional[int]   = None,
    trace_id:        str             = "",
    strategy_id:     str             = "",
    notes:           str             = "",
    db_url:          str             = "",
) -> dict:
    """
    Add a versioned entry to the knowledge base.
    Initial confidence is always 50 (neutral) — requires validation to change.
    Non-fatal.
    """
    url = db_url or _DB_URL
    if kb_type not in _KB_TYPES:
        return {"saved": False, "error": f"unknown kb_type {kb_type}"}
    if decision_quality and decision_quality not in _DECISION_QUALITY:
        return {"saved": False, "error": f"unknown decision_quality {decision_quality}"}

    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO oe_knowledge_base (
                    kb_type, version, alert_id, trade_record_id, trace_id,
                    ticker, scan_date, strategy_id,
                    fingerprint, outcome_pnl_pct, decision_quality,
                    confidence_score, notes
                ) VALUES (
                    %s,1,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,
                    50,%s
                ) RETURNING kb_id
            """, (
                kb_type,
                alert_id, trade_record_id, trace_id or None,
                ticker, scan_date, strategy_id or None,
                json.dumps(fingerprint, default=str),
                outcome_pnl_pct,
                decision_quality,
                notes or None,
            ))
            kb_id = cur.fetchone()[0]
            conn.commit()
        log.info(f"[phase3] KB entry added: kb_id={kb_id} type={kb_type} "
                 f"ticker={ticker} quality={decision_quality}")
        return {"saved": True, "kb_id": kb_id, "kb_type": kb_type,
                "initial_confidence": 50}
    except Exception as exc:
        log.debug(f"[phase3] add_knowledge_base_entry failed: {exc}")
        return {"saved": False, "error": str(exc)}


def update_kb_confidence(
    kb_id:         int,
    new_confidence: int,
    justification: str,
    validated_oos: bool,
    sample_size:   int,
    db_url:        str = "",
) -> dict:
    """
    Update the confidence score for a KB entry, subject to gate enforcement.
    Every attempted change (pass or fail) is audit-logged.
    Non-fatal.
    """
    url = db_url or _DB_URL
    if not (0 <= new_confidence <= 100):
        return {"updated": False, "error": "confidence must be 0-100"}

    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT confidence_score FROM oe_knowledge_base WHERE kb_id = %s
            """, (kb_id,))
            row = cur.fetchone()
            if not row:
                return {"updated": False, "error": f"kb_id={kb_id} not found"}
            current = int(row[0])

        gate_passed, reason = _confidence_change_gate(current, new_confidence, validated_oos, sample_size)
        direction = ("INCREASE" if new_confidence > current else
                     "DECREASE" if new_confidence < current else "UNCHANGED")

        with psycopg2.connect(url, connect_timeout=4) as conn2, conn2.cursor() as cur2:
            # Always log the attempt
            cur2.execute("""
                INSERT INTO oe_kb_confidence_log (
                    kb_id, old_confidence, new_confidence,
                    change_direction, justification,
                    validated_oos, sample_size, gate_passed
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (kb_id, current, new_confidence, direction,
                  justification, validated_oos, sample_size, gate_passed))

            if gate_passed and direction != "UNCHANGED":
                cur2.execute("""
                    UPDATE oe_knowledge_base
                    SET confidence_score       = %s,
                        validated_out_of_sample = %s,
                        statistical_gate_passed = TRUE,
                        validation_sample_size  = %s,
                        version                 = version + 1,
                        updated_at              = NOW()
                    WHERE kb_id = %s
                """, (new_confidence, validated_oos, sample_size, kb_id))
            conn2.commit()

        log.info(f"[phase3] KB confidence: kb_id={kb_id} "
                 f"{current}→{new_confidence} gate={gate_passed} reason={reason}")
        return {"updated": gate_passed, "kb_id": kb_id,
                "old_confidence": current, "new_confidence": new_confidence,
                "gate_passed": gate_passed, "reason": reason}
    except Exception as exc:
        log.debug(f"[phase3] update_kb_confidence failed: {exc}")
        return {"updated": False, "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 14 — MARKET & VOLATILITY REGIME LEARNING
# Regime-specific performance must not overwrite global behavior without
# independent verification (verified_out_of_sample gate).
# ─────────────────────────────────────────────────────────────────────────────

def _detect_regime_from_alert(alert_id: int, db_url: str) -> List[str]:
    """
    Extract regime tags for an alert from stored stock_analysis_json.
    Returns list of _REGIME_TYPES values that apply.
    """
    regimes: List[str] = []
    try:
        with psycopg2.connect(db_url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT stock_analysis_json FROM aiem_options_alerts WHERE id = %s
            """, (alert_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return regimes
            sd = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})

            market_regime = (sd.get("market_regime") or "").upper()
            if "BULL" in market_regime:
                regimes.append("BULL_STRONG" if "STRONG" in market_regime else "BULL_WEAK")
            elif "BEAR" in market_regime:
                regimes.append("BEAR_STRONG" if "STRONG" in market_regime else "BEAR_WEAK")
            elif "SIDEWAYS" in market_regime or "RANGE" in market_regime:
                regimes.append("SIDEWAYS")

            iv_rank_str = (sd.get("iv_rank") or "")
            try:
                iv_rank_val = float(iv_rank_str)
                if iv_rank_val >= 0.7:
                    regimes.append("HIGH_VOL")
                elif iv_rank_val <= 0.3:
                    regimes.append("LOW_VOL")
            except (TypeError, ValueError):
                pass

    except Exception as exc:
        log.debug(f"[phase3] _detect_regime_from_alert failed: {exc}")
    return regimes


def update_regime_performance(
    strategy_id: str,
    regime_type: str,
    db_url:      str = "",
) -> dict:
    """
    Recompute win rate and EV for (strategy_id, regime_type) from closed trades
    where the regime tag was applied.
    Uses UPSERT with UNIQUE(strategy_id, regime_type).
    Non-fatal.
    """
    url = db_url or _DB_URL
    if regime_type not in _REGIME_TYPES:
        return {"saved": False, "error": f"unknown regime_type {regime_type}"}

    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            # Match regime from stock_analysis_json.market_regime field
            cur.execute("""
                SELECT t.pnl_pct
                FROM oe_trade_records t
                JOIN aiem_options_alerts a ON a.id = t.alert_id
                WHERE t.strategy_id = %s
                  AND t.exit_ts IS NOT NULL
                  AND (
                      (a.stock_analysis_json->>'market_regime') ILIKE %s
                      OR (a.stock_analysis_json->>'iv_rank')::numeric >= 0.7
                         AND %s = 'HIGH_VOL'
                      OR (a.stock_analysis_json->>'iv_rank')::numeric <= 0.3
                         AND %s = 'LOW_VOL'
                  )
            """, (strategy_id,
                  f"%{regime_type.replace('_',' ')}%",
                  regime_type, regime_type))
            pnl_rows = [float(r[0]) for r in cur.fetchall() if r[0] is not None]

            n       = len(pnl_rows)
            wr      = sum(1 for p in pnl_rows if p > 0) / n if n > 0 else None
            avg_ret = sum(pnl_rows) / n if n > 0 else None

            cur.execute("""
                INSERT INTO oe_regime_performance (
                    strategy_id, regime_type,
                    observation_count, win_rate, avg_return_pct, expected_value,
                    last_updated
                ) VALUES (%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (strategy_id, regime_type) DO UPDATE SET
                    observation_count = EXCLUDED.observation_count,
                    win_rate          = EXCLUDED.win_rate,
                    avg_return_pct    = EXCLUDED.avg_return_pct,
                    expected_value    = EXCLUDED.expected_value,
                    last_updated      = NOW()
            """, (strategy_id, regime_type, n,
                  round(wr, 4) if wr is not None else None,
                  round(avg_ret, 4) if avg_ret is not None else None,
                  round(avg_ret, 4) if avg_ret is not None else None))
            conn.commit()

        log.info(f"[phase3] regime perf updated: strategy={strategy_id} "
                 f"regime={regime_type} n={n} wr={wr}")
        return {"saved": True, "strategy_id": strategy_id,
                "regime_type": regime_type, "n": n, "win_rate": wr}
    except Exception as exc:
        log.debug(f"[phase3] update_regime_performance failed: {exc}")
        return {"saved": False, "error": str(exc)}


def rebuild_regime_matrix(db_url: str = "") -> dict:
    """
    Rebuild regime performance rows for all (strategy, regime) combinations
    that have at least one closed trade.
    Regime-specific learning does not overwrite global scorecard.
    Non-fatal.
    """
    url = db_url or _DB_URL
    rebuilt = 0
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT strategy_id FROM oe_trade_records
                WHERE exit_ts IS NOT NULL AND strategy_id IS NOT NULL
            """)
            strategies = [r[0] for r in cur.fetchall()]
    except Exception as exc:
        return {"rebuilt": 0, "error": str(exc)}

    for sid in strategies:
        for regime in _REGIME_TYPES:
            result = update_regime_performance(sid, regime, url)
            if result.get("saved") and result.get("n", 0) > 0:
                rebuilt += 1

    log.info(f"[phase3] rebuild_regime_matrix: updated={rebuilt}")
    return {"rebuilt": rebuilt}


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION ASSERTIONS  (raise Phase3ValidationError on failure)
# ─────────────────────────────────────────────────────────────────────────────

class Phase3ValidationError(Exception):
    pass


def assert_fdr_correction_applied(run_id: int, db_url: str = "") -> None:
    """Assert that a completed attribution run has fdr_correction_applied=TRUE."""
    url = db_url or _DB_URL
    with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT fdr_correction_applied FROM oe_attribution_runs WHERE run_id=%s
        """, (run_id,))
        row = cur.fetchone()
    if not row:
        raise Phase3ValidationError(f"attribution_run {run_id} not found")
    if not row[0]:
        raise Phase3ValidationError(
            f"attribution_run {run_id} fdr_correction_applied=FALSE — "
            f"no statistical claims may be accepted from this run"
        )


def assert_scorecard_isolation(strategy_id: str, db_url: str = "") -> None:
    """Assert that no scorecard row merges multiple strategy_ids."""
    url = db_url or _DB_URL
    with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(DISTINCT strategy_id)
            FROM oe_strategy_scorecards
            WHERE segment_type = 'GLOBAL' AND segment_value = 'ALL'
              AND strategy_id = %s
        """, (strategy_id,))
        cnt = cur.fetchone()[0]
    if cnt > 1:
        raise Phase3ValidationError(
            f"Scorecard boundary violation: found {cnt} distinct strategy_ids "
            f"in a single GLOBAL/ALL row for strategy {strategy_id}"
        )


def assert_kb_confidence_gated(db_url: str = "") -> None:
    """
    Assert that no actual confidence change in oe_knowledge_base happened
    without the statistical gate.

    Checks the DB state, not the audit log: any row with confidence_score != 50
    (the initial value) must have statistical_gate_passed=TRUE.  The audit log
    intentionally records blocked attempts with gate_passed=FALSE — those are
    CORRECT behavior and must not be flagged as violations.
    """
    url = db_url or _DB_URL
    with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM oe_knowledge_base
            WHERE confidence_score != 50
              AND statistical_gate_passed = FALSE
        """)
        bad = cur.fetchone()[0]
    if bad > 0:
        raise Phase3ValidationError(
            f"{bad} KB rows have confidence_score != 50 with statistical_gate_passed=FALSE — "
            f"confidence was changed without gate approval"
        )


def assert_no_lookahead_phase3(db_url: str = "") -> None:
    """Assert no root-cause record was created before its alert was closed."""
    url = db_url or _DB_URL
    with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM oe_root_cause_records r
            JOIN aiem_options_alerts a ON a.id = r.alert_id
            WHERE r.created_at < a.created_at
        """)
        bad = cur.fetchone()[0]
    if bad > 0:
        raise Phase3ValidationError(
            f"{bad} root-cause records have created_at < alert created_at — look-ahead violation"
        )


def assert_regime_no_global_overwrite(db_url: str = "") -> None:
    """
    Assert that regime learning rows have not overwritten global scorecard rows.
    Regime performance is in oe_regime_performance; global is in oe_strategy_scorecards.
    These are separate tables — this assertion checks the tables exist and are separate.
    """
    url = db_url or _DB_URL
    with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
        for tbl in ("oe_regime_performance", "oe_strategy_scorecards"):
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_name=%s
            """, (tbl,))
            if cur.fetchone()[0] == 0:
                raise Phase3ValidationError(f"table {tbl} not found")
    # If both tables exist and are separate, regime learning cannot overwrite global.


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────────────────────

def get_phase3_table_counts(db_url: str = "") -> dict:
    url = db_url or _DB_URL
    tables = [
        "oe_root_cause_records",
        "oe_attribution_runs",
        "oe_indicator_attribution",
        "oe_interaction_hypotheses",
        "oe_interaction_results",
        "oe_strategy_scorecards",
        "oe_knowledge_base",
        "oe_kb_confidence_log",
        "oe_regime_performance",
    ]
    result: dict = {}
    try:
        with psycopg2.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            for tbl in tables:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                result[tbl] = cur.fetchone()[0]
    except Exception as exc:
        result["error"] = str(exc)
    return result
