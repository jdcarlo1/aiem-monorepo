"""
aiem_execution_intelligence.py — Execution Intelligence Engine for AIEM Options Pipeline

Evaluates whether a strategy candidate is realistically executable under current
market conditions after accounting for fill quality, transaction costs, and
execution uncertainty.

Architecture
────────────
  evaluate_execution_quality(strategy) → ExecutionAssessment
    ├── compute_fill_probability(legs)
    ├── compute_liquidity_score(strategy)
    ├── compute_execution_costs(strategy)
    ├── compute_net_edge(strategy, costs)
    └── apply_rejection_rules(assessment)

  save_execution_assessment(assessment, db_url) → candidate_id
  record_learning_outcome(candidate_id, actual_data, db_url)  ← called at paper close
  filter_strategies_by_execution(strategies, ...) → approved list

Gating
──────
  EI_GATING_ENABLED (config) = False by default.
  In OBSERVE mode, assessments are stored and logged but do NOT block the pipeline.
  Flip to True only after every verification requirement in execution_intelligence_verify.py
  passes against live data.

Fail-closed rules
─────────────────
  Any missing or invalid execution input → approved=False.
  Exceptions during assessment → approved=False (never silently pass).
"""

from __future__ import annotations

import os
import uuid
import json
import math
import logging
import hashlib
import time
import datetime
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any, Tuple

import psycopg2
import psycopg2.extras

log = logging.getLogger("aiem_execution_intelligence")

_DB_URL = os.environ.get("DATABASE_URL", "")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION (imported from strat engine config when available)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from aiem_strat_engine.config import (
        COMMISSION_PER_LEG,
        REG_FEE_PER_CONTRACT,
        OCC_FER_CLEARING_FEE,
        DEFAULT_SLIPPAGE_FRAC,
        MIN_OPEN_INTEREST,
        MIN_VOLUME,
        MAX_BID_ASK_WIDTH,
        PORTFOLIO_CAPITAL,
        EI_GATING_ENABLED,
        EI_MIN_FILL_PROB,
        EI_MIN_LIQUIDITY_SCORE,
        EI_MIN_NET_EDGE,
        EI_MAX_SPREAD_PCT,
        EI_MAX_TRANSACTION_COST_FRAC,
    )
except ImportError:
    COMMISSION_PER_LEG       = 0.65
    REG_FEE_PER_CONTRACT     = 0.02
    OCC_FER_CLEARING_FEE     = 0.01
    DEFAULT_SLIPPAGE_FRAC    = 0.005
    MIN_OPEN_INTEREST        = 50
    MIN_VOLUME               = 5
    MAX_BID_ASK_WIDTH        = 0.30
    PORTFOLIO_CAPITAL        = 100_000.0
    EI_GATING_ENABLED        = False
    EI_MIN_FILL_PROB         = 0.30
    EI_MIN_LIQUIDITY_SCORE   = 0.25
    EI_MIN_NET_EDGE          = -0.50     # net edge must be > -$0.50 per contract (very lenient floor)
    EI_MAX_SPREAD_PCT        = 0.35
    EI_MAX_TRANSACTION_COST_FRAC = 0.30  # transaction costs cannot exceed 30% of gross edge


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LegExecutionMetrics:
    """Execution quality for a single option leg."""
    strike:                float
    expiration_date:       str
    contract_type:         str        # "call" | "put"
    action:                str        # "BUY" | "SELL"
    bid:                   float
    ask:                   float
    mid:                   float
    spread_pct:            float
    volume:                int
    open_interest:         int
    bid_size:              int
    ask_size:              int
    dte:                   int
    iv:                    Optional[float]
    # Derived
    fill_probability:      float
    mid_fill_probability:  float
    expected_entry_price:  float
    conservative_entry_price: float
    slippage_pct:          float
    slippage_dollars:      float       # per contract (×100)
    spread_cost_dollars:   float       # half-spread per contract (×100)
    commission_dollars:    float
    exit_liquidity_score:  float
    has_quote:             bool
    data_complete:         bool


@dataclass
class ExecutionAssessment:
    """Full execution quality assessment for a strategy candidate."""
    candidate_id:              str
    trace_id:                  str
    ticker:                    str
    scan_date:                 str
    strategy_name:             str
    n_legs:                    int
    legs:                      List[LegExecutionMetrics]

    # Aggregate execution metrics
    fill_probability:          float
    mid_fill_probability:      float
    expected_entry_price:      float    # total debit/credit per contract
    conservative_entry_price:  float
    expected_slippage_pct:     float
    expected_slippage_dollars: float
    spread_cost_dollars:       float
    commission_dollars:        float
    market_impact_dollars:     float
    total_transaction_cost:    float
    legging_risk_score:        float    # 0=no risk, 1=extreme legging risk
    exit_liquidity_score:      float
    early_assignment_risk:     str      # "LOW" | "MODERATE" | "HIGH"
    pin_risk_flag:             bool

    # Scores
    liquidity_score:           float
    gross_expected_edge:       float
    net_expected_edge:         float
    execution_uncertainty:     float
    execution_score:           float    # composite [0,1]

    # Decision
    approved:                  bool
    rejection_reason:          Optional[str]
    position_size_factor:      float    # [0,1] multiply by base notional

    # Audit
    config_sha256:             str
    raw_json:                  dict = field(default_factory=dict)

    # Learning outcomes (filled post-trade)
    actual_fill_price:         Optional[float] = None
    actual_slippage:           Optional[float] = None
    actual_transaction_cost:   Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# DB BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────

_DB_BOOTSTRAPPED = False

def _bootstrap_ei_table(db_url: str) -> None:
    global _DB_BOOTSTRAPPED
    if _DB_BOOTSTRAPPED:
        return
    try:
        with psycopg2.connect(db_url, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS aiem_execution_assessments (
                    id                        BIGSERIAL PRIMARY KEY,
                    candidate_id              VARCHAR(64) NOT NULL UNIQUE,
                    trace_id                  VARCHAR(48),
                    strategy_id               VARCHAR(64),
                    ticker                    VARCHAR(20)  NOT NULL,
                    scan_date                 DATE         NOT NULL,
                    strategy_name             VARCHAR(64),
                    n_legs                    INTEGER,
                    -- Best-leg / aggregate quote fields
                    bid                       NUMERIC(10,4),
                    ask                       NUMERIC(10,4),
                    mid                       NUMERIC(10,4),
                    spread_pct                NUMERIC(8,4),
                    volume                    INTEGER,
                    open_interest             INTEGER,
                    bid_size                  INTEGER,
                    ask_size                  INTEGER,
                    iv                        NUMERIC(8,4),
                    dte                       INTEGER,
                    -- Execution quality
                    fill_probability          NUMERIC(6,4),
                    mid_fill_probability      NUMERIC(6,4),
                    expected_entry_price      NUMERIC(10,4),
                    conservative_entry_price  NUMERIC(10,4),
                    expected_slippage_pct     NUMERIC(8,4),
                    expected_slippage_dollars NUMERIC(10,4),
                    spread_cost_dollars       NUMERIC(10,4),
                    commission_dollars        NUMERIC(10,4),
                    market_impact_dollars     NUMERIC(10,4),
                    total_transaction_cost    NUMERIC(10,4),
                    legging_risk_score        NUMERIC(6,4),
                    exit_liquidity_score      NUMERIC(6,4),
                    early_assignment_risk     VARCHAR(10),
                    pin_risk_flag             BOOLEAN DEFAULT FALSE,
                    -- Scores
                    liquidity_score           NUMERIC(6,4),
                    gross_expected_edge       NUMERIC(10,4),
                    net_expected_edge         NUMERIC(10,4),
                    execution_uncertainty     NUMERIC(8,4),
                    execution_score           NUMERIC(6,4),
                    -- Decision
                    approved                  BOOLEAN NOT NULL,
                    rejection_reason          VARCHAR(200),
                    position_size_factor      NUMERIC(6,4),
                    -- Learning outcomes
                    actual_fill_price         NUMERIC(10,4),
                    actual_slippage           NUMERIC(10,4),
                    actual_transaction_cost   NUMERIC(10,4),
                    fill_prob_error           NUMERIC(8,4),
                    entry_price_error         NUMERIC(10,4),
                    slippage_error            NUMERIC(10,4),
                    cost_error                NUMERIC(10,4),
                    -- Audit
                    config_sha256             VARCHAR(64),
                    raw_assessment_json       JSONB,
                    gating_enabled            BOOLEAN DEFAULT FALSE,
                    created_at                TIMESTAMPTZ DEFAULT NOW(),
                    updated_at                TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ei_ticker_date
                    ON aiem_execution_assessments(ticker, scan_date)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ei_trace_id
                    ON aiem_execution_assessments(trace_id)
            """)
            conn.commit()
        _DB_BOOTSTRAPPED = True
        log.info("[ei_bootstrap] aiem_execution_assessments table ready")
    except Exception as e:
        log.warning(f"[ei_bootstrap] failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CORE MODELS
# ─────────────────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(v)))


def _config_sha() -> str:
    blob = json.dumps({
        "COMMISSION_PER_LEG":         COMMISSION_PER_LEG,
        "REG_FEE_PER_CONTRACT":       REG_FEE_PER_CONTRACT,
        "OCC_FER_CLEARING_FEE":       OCC_FER_CLEARING_FEE,
        "DEFAULT_SLIPPAGE_FRAC":      DEFAULT_SLIPPAGE_FRAC,
        "MIN_OPEN_INTEREST":          MIN_OPEN_INTEREST,
        "MIN_VOLUME":                 MIN_VOLUME,
        "MAX_BID_ASK_WIDTH":          MAX_BID_ASK_WIDTH,
        "EI_MIN_FILL_PROB":           EI_MIN_FILL_PROB,
        "EI_MIN_LIQUIDITY_SCORE":     EI_MIN_LIQUIDITY_SCORE,
        "EI_MIN_NET_EDGE":            EI_MIN_NET_EDGE,
        "EI_MAX_SPREAD_PCT":          EI_MAX_SPREAD_PCT,
        "EI_MAX_TRANSACTION_COST_FRAC": EI_MAX_TRANSACTION_COST_FRAC,
    }, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def compute_leg_fill_probability(
    bid: float, ask: float, mid: float,
    volume: int, open_interest: int,
    bid_size: int, ask_size: int,
    spread_pct: float, action: str,
) -> Tuple[float, float]:
    """
    Estimate fill probability and mid-price fill probability for a single leg.

    Returns: (fill_probability, mid_fill_probability)

    Model:
      Base score = 0.50
      Spread quality  ±0.25
      Volume          ±0.15
      Open interest   ±0.10
      Size depth      ±0.05

    Fail-closed: if mid <= 0 or ask <= 0 → (0.0, 0.0)
    """
    if mid <= 0 or ask <= 0 or bid < 0:
        return 0.0, 0.0

    base = 0.50

    # Spread quality component
    if spread_pct <= 0.02:
        spread_adj = +0.25
    elif spread_pct <= 0.05:
        spread_adj = +0.20
    elif spread_pct <= 0.10:
        spread_adj = +0.12
    elif spread_pct <= 0.15:
        spread_adj = +0.05
    elif spread_pct <= 0.25:
        spread_adj = -0.05
    elif spread_pct <= 0.35:
        spread_adj = -0.15
    else:
        spread_adj = -0.25

    # Volume component
    if volume >= 1000:
        vol_adj = +0.15
    elif volume >= 500:
        vol_adj = +0.10
    elif volume >= 100:
        vol_adj = +0.05
    elif volume >= 20:
        vol_adj = 0.0
    elif volume >= 5:
        vol_adj = -0.08
    else:
        vol_adj = -0.15

    # Open interest component
    if open_interest >= 5000:
        oi_adj = +0.10
    elif open_interest >= 2000:
        oi_adj = +0.07
    elif open_interest >= 500:
        oi_adj = +0.03
    elif open_interest >= 50:
        oi_adj = 0.0
    else:
        oi_adj = -0.10

    # Size depth component (bid_size for BUY orders, ask_size for SELL)
    depth = ask_size if action == "BUY" else bid_size
    if depth >= 20:
        size_adj = +0.05
    elif depth >= 10:
        size_adj = +0.02
    elif depth >= 5:
        size_adj = 0.0
    elif depth >= 1:
        size_adj = -0.02
    else:
        size_adj = -0.05   # unknown size — conservative

    fill_prob = _clamp(base + spread_adj + vol_adj + oi_adj + size_adj, 0.05, 0.95)

    # Mid-fill probability: harder than limit-fill; spread quality is the main driver
    mid_fill_base = fill_prob * 0.75
    if spread_pct <= 0.03:
        mid_fill_base = fill_prob * 0.90
    elif spread_pct <= 0.08:
        mid_fill_base = fill_prob * 0.80
    mid_fill_prob = _clamp(mid_fill_base, 0.03, 0.90)

    return round(fill_prob, 4), round(mid_fill_prob, 4)


def compute_liquidity_score(strategy: dict) -> float:
    """
    Normalized [0,1] composite liquidity score for a strategy.

    Components (weighted average):
      1. Spread quality        (weight 0.30)
      2. Open interest depth   (weight 0.25)
      3. Volume depth          (weight 0.20)
      4. Bid/ask size depth    (weight 0.10)
      5. DTE appropriateness   (weight 0.08)
      6. Leg count discount    (weight 0.07)

    Fail-closed: if any leg has mid=0 → 0.0
    """
    legs = strategy.get("legs", [])
    if not legs:
        return 0.0

    option_legs = [l for l in legs if l.get("action") not in ("LONG_STOCK",)]
    if not option_legs:
        return 0.0

    # Check for any dead quotes
    for leg in option_legs:
        mid = float(leg.get("mid", 0.0) or 0.0)
        if mid <= 0:
            return 0.0   # fail-closed: cannot assess liquidity without valid quote

    scores = []
    for leg in option_legs:
        bid        = float(leg.get("bid", 0.0)  or 0.0)
        ask        = float(leg.get("ask", 0.0)  or 0.0)
        mid        = float(leg.get("mid", 0.0)  or 0.0)
        spread_pct = float(leg.get("bid_ask_spread_pct", 1.0) or 1.0)
        volume     = int(leg.get("volume", 0)         or 0)
        oi         = int(leg.get("open_interest", 0)  or 0)
        bid_size   = int(leg.get("bid_size", 0)       or 0)
        ask_size   = int(leg.get("ask_size", 0)       or 0)
        dte        = int(leg.get("dte", 0)            or 0)

        # 1. Spread quality [0,1]: 0% spread=1.0, MAX_BID_ASK_WIDTH=0.0
        spread_score = _clamp(1.0 - spread_pct / MAX_BID_ASK_WIDTH)

        # 2. OI depth: log scale up to 5,000
        oi_score = _clamp(math.log1p(oi) / math.log1p(5000)) if oi > 0 else 0.0

        # 3. Volume depth: log scale up to 2,000
        vol_score = _clamp(math.log1p(volume) / math.log1p(2000)) if volume > 0 else 0.0

        # 4. Size depth: combined bid+ask size up to 50
        size_total = bid_size + ask_size
        size_score = _clamp(size_total / 50.0) if size_total > 0 else 0.20  # neutral if unknown

        # 5. DTE appropriateness: 7–21 DTE is ideal
        if 7 <= dte <= 21:
            dte_score = 1.0
        elif 5 <= dte <= 6 or 22 <= dte <= 30:
            dte_score = 0.80
        elif 3 <= dte <= 4 or 31 <= dte <= 45:
            dte_score = 0.60
        elif dte <= 2:
            dte_score = 0.25
        else:
            dte_score = 0.50

        leg_score = (
            spread_score * 0.30 +
            oi_score     * 0.25 +
            vol_score    * 0.20 +
            size_score   * 0.10 +
            dte_score    * 0.08
        ) / 0.93   # re-normalize sub-weights to 1 (leg count uses 0.07 below)

        scores.append(_clamp(leg_score))

    if not scores:
        return 0.0

    # Average across legs
    avg_score = sum(scores) / len(scores)

    # 6. Leg count discount: each extra leg beyond 1 costs 5%
    n_option_legs = len(option_legs)
    leg_discount = max(0.0, 1.0 - (n_option_legs - 1) * 0.05)

    return round(_clamp(avg_score * 0.93 + leg_discount * 0.07), 4)


def compute_execution_costs(
    strategy: dict,
    n_contracts: int = 1,
) -> dict:
    """
    Estimate all transaction costs for a strategy.

    Returns dict with per-contract and total cost fields.
    Cost convention: all values in dollars (positive = cost to us).
    """
    legs = [l for l in strategy.get("legs", []) if l.get("action") not in ("LONG_STOCK",)]
    n_legs = len(legs)

    if n_legs == 0:
        return {
            "n_legs": 0, "n_contracts": n_contracts,
            "spread_cost_dollars": 0.0,
            "slippage_dollars": 0.0,
            "commission_dollars": 0.0,
            "market_impact_dollars": 0.0,
            "total_transaction_cost": 0.0,
            "cost_as_pct_of_gross": 1.0,
            "data_complete": False,
        }

    # Spread cost: half-spread per leg per contract
    spread_cost = 0.0
    slippage    = 0.0
    for leg in legs:
        bid = float(leg.get("bid", 0.0) or 0.0)
        ask = float(leg.get("ask", 0.0) or 0.0)
        mid = float(leg.get("mid", 0.0) or 0.0)
        if mid > 0:
            half_spread = (ask - bid) / 2.0
            spread_cost += half_spread * 100 * n_contracts   # $ per contract
            slippage    += mid * DEFAULT_SLIPPAGE_FRAC * 100 * n_contracts
        else:
            # No quote → conservative estimate
            spread_cost += 0.50 * n_contracts
            slippage    += 0.25 * n_contracts

    # Commission: per leg per contract both sides
    commission = (
        (COMMISSION_PER_LEG + REG_FEE_PER_CONTRACT + OCC_FER_CLEARING_FEE)
        * n_legs * n_contracts * 2   # ×2 for entry + exit
    )

    # Market impact: negligible for paper trades (1 contract), small for larger
    market_impact = 0.0 if n_contracts <= 2 else n_contracts * 0.05

    total = round(spread_cost + slippage + commission + market_impact, 4)

    gross_edge = abs(strategy.get("ev_after_costs") or 0.0)
    cost_pct   = (total / gross_edge) if gross_edge > 0.01 else 1.0

    return {
        "n_legs":                n_legs,
        "n_contracts":           n_contracts,
        "spread_cost_dollars":   round(spread_cost, 4),
        "slippage_dollars":      round(slippage, 4),
        "commission_dollars":    round(commission, 4),
        "market_impact_dollars": round(market_impact, 4),
        "total_transaction_cost":total,
        "cost_as_pct_of_gross":  round(cost_pct, 4),
        "data_complete":         True,
    }


def compute_net_edge(
    strategy: dict,
    exec_costs: dict,
    fill_probability: float,
) -> Tuple[float, float, float]:
    """
    Compute execution-adjusted expected value.

    net_edge = gross_edge
             - spread_cost
             - commission
             - slippage
             - market_impact
             - execution_uncertainty

    execution_uncertainty = (1 - fill_prob) × |gross_edge| × 0.30

    Returns: (gross_edge, net_edge, execution_uncertainty)
    """
    gross_edge = float(strategy.get("ev_after_costs") or 0.0)
    if not exec_costs.get("data_complete"):
        return gross_edge, gross_edge - 99.0, 99.0  # extreme penalty → fail-closed

    spread_cost    = exec_costs["spread_cost_dollars"]
    commission     = exec_costs["commission_dollars"]
    slippage       = exec_costs["slippage_dollars"]
    market_impact  = exec_costs["market_impact_dollars"]

    uncertainty = (1.0 - _clamp(fill_probability)) * abs(gross_edge) * 0.30

    net_edge = gross_edge - spread_cost - commission - slippage - market_impact - uncertainty

    return (
        round(gross_edge, 4),
        round(net_edge,   4),
        round(uncertainty, 4),
    )


def _compute_exit_liquidity(legs: list) -> float:
    """
    Exit liquidity: can we get out cleanly?
    Uses OI as proxy — large OI = good exit liquidity.
    Discount for wide spreads at exit (assume same spread at exit).
    """
    if not legs:
        return 0.0
    option_legs = [l for l in legs if l.get("action") != "LONG_STOCK"]
    scores = []
    for leg in option_legs:
        oi         = int(leg.get("open_interest", 0) or 0)
        spread_pct = float(leg.get("bid_ask_spread_pct", 1.0) or 1.0)
        oi_score   = _clamp(math.log1p(oi) / math.log1p(10_000))
        spread_ok  = _clamp(1.0 - spread_pct / 0.40)
        scores.append((oi_score * 0.60 + spread_ok * 0.40))
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def _compute_legging_risk(n_legs: int, fill_probability_per_leg: float) -> float:
    """
    Legging risk: probability that NOT all legs fill simultaneously.
    For 1 leg: 0.0 (no legging risk).
    For N legs: 1 - P(all fill) = 1 - fill_prob^N.
    """
    if n_legs <= 1:
        return 0.0
    p_all = fill_probability_per_leg ** n_legs
    return round(1.0 - p_all, 4)


def _compute_assignment_risk(legs: list, dte: int) -> str:
    """Early assignment risk: HIGH if short ITM legs with DTE≤5."""
    short_legs = [l for l in legs if l.get("action") == "SELL"]
    if not short_legs:
        return "LOW"
    for leg in short_legs:
        d     = abs(float(leg.get("delta", 0.0) or 0.0))
        l_dte = int(leg.get("dte", 99) or 99)
        if d >= 0.70 and l_dte <= 5:
            return "HIGH"
        if d >= 0.50 and l_dte <= 3:
            return "HIGH"
    return "LOW" if dte > 7 else "MODERATE"


def _pin_risk(legs: list, spot: float) -> bool:
    """Pin risk: any short leg with strike within 0.5% of spot at DTE≤3."""
    short_legs = [l for l in legs if l.get("action") == "SELL"]
    for leg in short_legs:
        strike = float(leg.get("strike", 0.0) or 0.0)
        dte    = int(leg.get("dte", 99)    or 99)
        if spot > 0 and dte <= 3 and abs(strike - spot) / spot <= 0.005:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# LEG-LEVEL ASSESSMENT
# ─────────────────────────────────────────────────────────────────────────────

def _assess_leg(leg: dict, n_contracts: int = 1) -> LegExecutionMetrics:
    """Build LegExecutionMetrics from a strategy leg dict."""
    bid        = float(leg.get("bid", 0.0)  or 0.0)
    ask        = float(leg.get("ask", 0.0)  or 0.0)
    mid        = float(leg.get("mid", 0.0)  or 0.0)
    spread_pct = float(leg.get("bid_ask_spread_pct", 1.0) or 1.0)
    volume     = int(leg.get("volume",        0) or 0)
    oi         = int(leg.get("open_interest", 0) or 0)
    bid_size   = int(leg.get("bid_size",      0) or 0)
    ask_size   = int(leg.get("ask_size",      0) or 0)
    dte        = int(leg.get("dte",           0) or 0)
    iv         = leg.get("implied_volatility")
    action     = leg.get("action", "BUY")

    has_quote    = (mid > 0 or (bid > 0 and ask > 0))
    data_complete = has_quote and oi >= 0 and volume >= 0

    fill_prob, mid_fill = compute_leg_fill_probability(
        bid, ask, mid, volume, oi, bid_size, ask_size, spread_pct, action)

    # Expected entry: between mid and ask (for BUY) or mid and bid (for SELL)
    if action == "BUY":
        expected_entry = round((mid + ask) / 2.0 * 0.55 + mid * 0.45, 4) if mid > 0 else ask
        conservative   = ask
    else:
        expected_entry = round((mid + bid) / 2.0 * 0.55 + mid * 0.45, 4) if mid > 0 else bid
        conservative   = bid

    slip_pct     = DEFAULT_SLIPPAGE_FRAC
    slip_dollars = round(mid * slip_pct * 100 * n_contracts, 4) if mid > 0 else 0.0
    spread_cost  = round((ask - bid) / 2.0 * 100 * n_contracts, 4) if (ask > bid) else 0.0
    commission   = round(
        (COMMISSION_PER_LEG + REG_FEE_PER_CONTRACT + OCC_FER_CLEARING_FEE) * n_contracts,
        4)

    exit_liq = _clamp(
        (math.log1p(oi) / math.log1p(10_000)) * 0.60
        + _clamp(1.0 - spread_pct / 0.40) * 0.40
    ) if oi > 0 else 0.0

    return LegExecutionMetrics(
        strike=float(leg.get("strike", 0.0) or 0.0),
        expiration_date=leg.get("expiration_date", ""),
        contract_type=leg.get("contract_type", ""),
        action=action,
        bid=bid, ask=ask, mid=mid,
        spread_pct=spread_pct,
        volume=volume, open_interest=oi,
        bid_size=bid_size, ask_size=ask_size,
        dte=dte, iv=float(iv) if iv else None,
        fill_probability=fill_prob,
        mid_fill_probability=mid_fill,
        expected_entry_price=expected_entry,
        conservative_entry_price=conservative,
        slippage_pct=slip_pct,
        slippage_dollars=slip_dollars,
        spread_cost_dollars=spread_cost,
        commission_dollars=commission,
        exit_liquidity_score=round(exit_liq, 4),
        has_quote=has_quote,
        data_complete=data_complete,
    )


# ─────────────────────────────────────────────────────────────────────────────
# REJECTION RULES
# ─────────────────────────────────────────────────────────────────────────────

def apply_rejection_rules(
    strategy: dict,
    fill_probability: float,
    liquidity_score: float,
    net_edge: float,
    exec_costs: dict,
    legs: list,
) -> Tuple[bool, Optional[str]]:
    """
    Apply all rejection rules.  Returns (approved, reason).
    Fail-closed: any missing required data → rejected.

    Rules applied in priority order (first failure returns immediately):
      R1  Missing quote data (any leg)
      R2  Spread exceeds limit (any leg)
      R3  OI below minimum (any leg)
      R4  Volume below minimum (any leg)
      R5  Bid/ask sizes inadequate (any BUY leg)
      R6  Fill probability too low
      R7  Liquidity score too low
      R8  Transaction costs eliminate edge
      R9  Net edge below floor
      R10 Missing/incomplete execution data
    """
    option_legs = [l for l in legs if l.action != "LONG_STOCK"]

    # R1: No valid quotes
    for leg in option_legs:
        if not leg.data_complete or not leg.has_quote:
            return False, f"R1_no_quote: {leg.contract_type} strike={leg.strike}"

    # R2: Spread too wide
    for leg in option_legs:
        if leg.spread_pct > EI_MAX_SPREAD_PCT:
            return False, (f"R2_spread_too_wide: {leg.contract_type} strike={leg.strike} "
                           f"spread={leg.spread_pct:.3f} > {EI_MAX_SPREAD_PCT}")

    # R3: Insufficient open interest
    for leg in option_legs:
        if leg.open_interest < MIN_OPEN_INTEREST:
            return False, (f"R3_oi_insufficient: {leg.contract_type} strike={leg.strike} "
                           f"OI={leg.open_interest} < {MIN_OPEN_INTEREST}")

    # R4: Insufficient volume
    for leg in option_legs:
        if leg.volume < MIN_VOLUME:
            return False, (f"R4_volume_insufficient: {leg.contract_type} strike={leg.strike} "
                           f"vol={leg.volume} < {MIN_VOLUME}")

    # R5: Inadequate bid/ask sizes (only flag if size data is available and clearly zero)
    for leg in option_legs:
        if leg.action == "BUY" and leg.ask_size == 0 and leg.bid_size == 0 and leg.volume < 10:
            return False, (f"R5_size_inadequate: {leg.contract_type} strike={leg.strike} "
                           f"bid_size={leg.bid_size} ask_size={leg.ask_size}")

    # R6: Fill probability
    if fill_probability < EI_MIN_FILL_PROB:
        return False, f"R6_fill_prob_low: {fill_probability:.3f} < {EI_MIN_FILL_PROB}"

    # R7: Liquidity score
    if liquidity_score < EI_MIN_LIQUIDITY_SCORE:
        return False, f"R7_liquidity_low: {liquidity_score:.3f} < {EI_MIN_LIQUIDITY_SCORE}"

    # R8: Transaction cost fraction of gross edge
    cost_frac = exec_costs.get("cost_as_pct_of_gross", 1.0)
    if cost_frac > EI_MAX_TRANSACTION_COST_FRAC:
        return False, (f"R8_costs_eliminate_edge: cost_frac={cost_frac:.3f} "
                       f"> {EI_MAX_TRANSACTION_COST_FRAC}")

    # R9: Net edge floor
    if net_edge < EI_MIN_NET_EDGE:
        return False, f"R9_net_edge_below_floor: {net_edge:.4f} < {EI_MIN_NET_EDGE}"

    # R10: Incomplete execution data
    if not exec_costs.get("data_complete"):
        return False, "R10_missing_execution_data"

    return True, None


# ─────────────────────────────────────────────────────────────────────────────
# POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────

def determine_position_size_factor(
    fill_probability: float,
    liquidity_score: float,
    net_edge: float,
    gross_edge: float,
    exec_costs: dict,
    approved: bool,
) -> float:
    """
    Position size factor [0, 1.0].
    Multiply base notional by this to get actual position size.

    Poorer execution quality → smaller position.
    Rejected → 0.0.
    """
    if not approved:
        return 0.0

    fill_factor = _clamp(fill_probability / 0.80)         # 80% fill = full size
    liq_factor  = _clamp(liquidity_score  / 0.70)         # 70% liq  = full size
    edge_factor = 1.0
    if gross_edge > 0:
        edge_ratio  = (net_edge / gross_edge) if gross_edge != 0 else 0
        edge_factor = _clamp(edge_ratio, 0.2, 1.0)        # penalize if net << gross

    cost_frac   = exec_costs.get("cost_as_pct_of_gross", 0.5)
    cost_factor = _clamp(1.0 - cost_frac / EI_MAX_TRANSACTION_COST_FRAC, 0.2, 1.0)

    composite = (
        fill_factor  * 0.30 +
        liq_factor   * 0.30 +
        edge_factor  * 0.25 +
        cost_factor  * 0.15
    )
    return round(_clamp(composite, 0.10, 1.0), 4)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN EVALUATION
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_execution_quality(
    strategy: dict,
    trace_id: str,
    scan_date,
    ticker: str,
    spot: float = 0.0,
    n_contracts: int = 1,
) -> ExecutionAssessment:
    """
    Full execution quality assessment for a single strategy candidate.

    Fail-closed: any exception inside this function returns an assessment
    with approved=False and a descriptive rejection reason.

    This is the main entry point for the EI layer.
    """
    candidate_id = f"ei_{ticker}_{scan_date}_{uuid.uuid4().hex[:12]}"
    strategy_name = strategy.get("strategy", "UNKNOWN")
    legs_raw      = strategy.get("legs", [])
    n_legs_total  = len(legs_raw)

    try:
        # Assess each leg
        leg_metrics = [
            _assess_leg(leg, n_contracts)
            for leg in legs_raw
            if leg.get("action") != "LONG_STOCK"
        ]

        if not leg_metrics:
            return ExecutionAssessment(
                candidate_id=candidate_id, trace_id=trace_id,
                ticker=ticker, scan_date=str(scan_date),
                strategy_name=strategy_name, n_legs=n_legs_total,
                legs=[], fill_probability=0.0, mid_fill_probability=0.0,
                expected_entry_price=0.0, conservative_entry_price=0.0,
                expected_slippage_pct=0.0, expected_slippage_dollars=0.0,
                spread_cost_dollars=0.0, commission_dollars=0.0,
                market_impact_dollars=0.0, total_transaction_cost=0.0,
                legging_risk_score=0.0, exit_liquidity_score=0.0,
                early_assignment_risk="LOW", pin_risk_flag=False,
                liquidity_score=0.0, gross_expected_edge=0.0,
                net_expected_edge=0.0, execution_uncertainty=0.0,
                execution_score=0.0,
                approved=False, rejection_reason="R10_no_option_legs",
                position_size_factor=0.0,
                config_sha256=_config_sha(),
            )

        # Aggregate fill probability (legs assumed partially correlated)
        per_leg_fp = [l.fill_probability for l in leg_metrics]
        per_leg_mfp = [l.mid_fill_probability for l in leg_metrics]

        if n_legs_total == 1:
            agg_fill_prob     = per_leg_fp[0]
            agg_mid_fill_prob = per_leg_mfp[0]
        else:
            # Product for multi-leg (independence assumption), with correlation boost
            raw_product  = math.prod(per_leg_fp)
            min_leg      = min(per_leg_fp)
            agg_fill_prob = round(_clamp((raw_product + min_leg) / 2.0, 0.05, 0.90), 4)
            agg_mid_fill_prob = round(_clamp(
                (math.prod(per_leg_mfp) + min(per_leg_mfp)) / 2.0, 0.03, 0.85), 4)

        # Aggregate entry price
        agg_expected_entry = round(sum(l.expected_entry_price for l in leg_metrics), 4)
        agg_conservative   = round(sum(l.conservative_entry_price for l in leg_metrics), 4)

        # Execution costs
        exec_costs = compute_execution_costs(strategy, n_contracts)

        # Gross/net edge
        gross_edge, net_edge, uncertainty = compute_net_edge(
            strategy, exec_costs, agg_fill_prob)

        # Liquidity score
        liq_score = compute_liquidity_score(strategy)

        # Risk flags
        worst_dte      = min((l.dte for l in leg_metrics), default=0)
        legging_risk   = _compute_legging_risk(len(leg_metrics), min(per_leg_fp))
        exit_liq       = round(sum(l.exit_liquidity_score for l in leg_metrics) / len(leg_metrics), 4)
        assign_risk    = _compute_assignment_risk(legs_raw, worst_dte)
        pin_risk       = _pin_risk(legs_raw, spot)

        # Aggregate slippage / spread
        total_slippage     = round(sum(l.slippage_dollars for l in leg_metrics), 4)
        total_spread_cost  = round(sum(l.spread_cost_dollars for l in leg_metrics), 4)
        avg_slippage_pct   = round(
            sum(l.slippage_pct for l in leg_metrics) / len(leg_metrics), 4)

        # Execution score: composite quality signal [0,1]
        execution_score = round(_clamp(
            agg_fill_prob * 0.35
            + liq_score   * 0.30
            + _clamp(1.0 - legging_risk) * 0.15
            + exit_liq    * 0.10
            + (_clamp((net_edge + 5.0) / 10.0) if gross_edge != 0 else 0.5) * 0.10
        ), 4)

        # Rejection rules
        approved, reason = apply_rejection_rules(
            strategy, agg_fill_prob, liq_score, net_edge, exec_costs, leg_metrics)

        # Position size factor
        size_factor = determine_position_size_factor(
            agg_fill_prob, liq_score, net_edge, gross_edge, exec_costs, approved)

        _rep = leg_metrics[0] if leg_metrics else None
        assessment = ExecutionAssessment(
            candidate_id=candidate_id,
            trace_id=trace_id,
            ticker=ticker,
            scan_date=str(scan_date),
            strategy_name=strategy_name,
            n_legs=n_legs_total,
            legs=leg_metrics,
            fill_probability=agg_fill_prob,
            mid_fill_probability=agg_mid_fill_prob,
            expected_entry_price=agg_expected_entry,
            conservative_entry_price=agg_conservative,
            expected_slippage_pct=avg_slippage_pct,
            expected_slippage_dollars=total_slippage,
            spread_cost_dollars=total_spread_cost,
            commission_dollars=exec_costs["commission_dollars"],
            market_impact_dollars=exec_costs["market_impact_dollars"],
            total_transaction_cost=exec_costs["total_transaction_cost"],
            legging_risk_score=legging_risk,
            exit_liquidity_score=exit_liq,
            early_assignment_risk=assign_risk,
            pin_risk_flag=pin_risk,
            liquidity_score=liq_score,
            gross_expected_edge=gross_edge,
            net_expected_edge=net_edge,
            execution_uncertainty=uncertainty,
            execution_score=execution_score,
            approved=approved,
            rejection_reason=reason,
            position_size_factor=size_factor,
            config_sha256=_config_sha(),
            raw_json={
                # ── Identity ────────────────────────────────────────────────
                "candidate_id":              candidate_id,
                "trace_id":                  trace_id,
                "strategy_id":               f"{strategy_name}_{ticker}_{str(scan_date).replace('-','')}",
                "symbol":                    ticker,
                "scan_date":                 str(scan_date),
                "n_contracts":               n_contracts,
                "timestamp_utc":             datetime.datetime.utcnow().isoformat() + "Z",
                # ── Strategy ────────────────────────────────────────────────
                "strategy":                  strategy_name,
                "n_legs":                    n_legs_total,
                "legs_count":                len(leg_metrics),
                # ── Primary leg quote fields ─────────────────────────────────
                "bid":                       round(_rep.bid, 4) if _rep else None,
                "ask":                       round(_rep.ask, 4) if _rep else None,
                "mid":                       round(_rep.mid, 4) if _rep else None,
                "spread_pct":                round(_rep.spread_pct, 4) if _rep else None,
                "volume":                    _rep.volume if _rep else None,
                "open_interest":             _rep.open_interest if _rep else None,
                # quote_age_seconds: NOT_IMPLEMENTED — Polygon batch data
                # carries no per-quote timestamp; real-time feed required.
                "quote_age_seconds":         None,
                # ── Execution quality ────────────────────────────────────────
                "per_leg_fp":                per_leg_fp,
                "fill_probability":          agg_fill_prob,
                "mid_fill_probability":      agg_mid_fill_prob,
                "expected_entry_price":      agg_expected_entry,
                "conservative_entry_price":  agg_conservative,
                "expected_slippage_dollars": total_slippage,
                "spread_cost_dollars":       total_spread_cost,
                "commission_dollars":        exec_costs["commission_dollars"],
                "market_impact_dollars":     exec_costs["market_impact_dollars"],
                "total_transaction_cost":    exec_costs["total_transaction_cost"],
                "cost_as_pct_of_gross":      exec_costs["cost_as_pct_of_gross"],
                # ── Scores ──────────────────────────────────────────────────
                "liquidity_score":           liq_score,
                "gross_expected_edge":       gross_edge,
                "net_expected_edge":         net_edge,
                "execution_uncertainty":     uncertainty,
                "execution_score":           execution_score,
                # ── Decision ────────────────────────────────────────────────
                "approved":                  approved,
                "rejection_reason":          reason,
                "gating_enabled":            EI_GATING_ENABLED,
                # ── Not implemented in v1 ────────────────────────────────────
                # partial_fill_probability: requires order-book depth data
                # not available in Polygon daily batch.
                "partial_fill_probability":  "NOT_IMPLEMENTED",
                # roll_liquidity_score: requires front/back month OI comparison;
                # not in scope for v1.
                "roll_liquidity_score":      "NOT_IMPLEMENTED",
                # ── Learning outcomes (pre-trade = null) ────────────────────
                # Filled in by record_learning_outcome() at paper close.
                "actual_fill_price":         None,
                "actual_slippage":           None,
                "actual_transaction_cost":   None,
                # ── Full cost breakdown ──────────────────────────────────────
                "exec_costs":                exec_costs,
            },
        )

        log.info(
            f"[EI] {ticker} {strategy_name}: "
            f"fill_prob={agg_fill_prob:.3f} "
            f"liq={liq_score:.3f} "
            f"net_edge={net_edge:.2f} "
            f"{'APPROVED' if approved else 'REJECTED: ' + (reason or '')}"
        )
        return assessment

    except Exception as exc:
        log.error(f"[EI] evaluate_execution_quality EXCEPTION for {ticker} {strategy_name}: {exc}")
        return ExecutionAssessment(
            candidate_id=candidate_id, trace_id=trace_id,
            ticker=ticker, scan_date=str(scan_date),
            strategy_name=strategy_name, n_legs=n_legs_total,
            legs=[], fill_probability=0.0, mid_fill_probability=0.0,
            expected_entry_price=0.0, conservative_entry_price=0.0,
            expected_slippage_pct=0.0, expected_slippage_dollars=0.0,
            spread_cost_dollars=0.0, commission_dollars=0.0,
            market_impact_dollars=0.0, total_transaction_cost=0.0,
            legging_risk_score=1.0, exit_liquidity_score=0.0,
            early_assignment_risk="HIGH", pin_risk_flag=False,
            liquidity_score=0.0, gross_expected_edge=0.0,
            net_expected_edge=-99.0, execution_uncertainty=99.0,
            execution_score=0.0,
            approved=False,
            rejection_reason=f"EI_EXCEPTION: {str(exc)[:120]}",
            position_size_factor=0.0,
            config_sha256=_config_sha(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE FILTER
# ─────────────────────────────────────────────────────────────────────────────

def filter_strategies_by_execution(
    strategies: List[dict],
    trace_id: str,
    scan_date,
    ticker: str,
    spot: float = 0.0,
    db_url: str = "",
) -> Tuple[List[dict], List[ExecutionAssessment]]:
    """
    Run EI assessment on every strategy candidate.
    Saves each assessment to DB.

    Returns:
      (approved_strategies, all_assessments)

    If EI_GATING_ENABLED=False: returns all strategies (observe mode) + assessments.
    If EI_GATING_ENABLED=True:  returns only approved strategies.

    This is the public entry point for the scheduler Stage EI.
    """
    _bootstrap_ei_table(db_url or _DB_URL)

    all_assessments: List[ExecutionAssessment] = []
    approved_pairs:  List[Tuple[dict, ExecutionAssessment]] = []

    for strat in strategies:
        ei = evaluate_execution_quality(strat, trace_id, scan_date, ticker, spot)
        save_execution_assessment(ei, db_url or _DB_URL)
        all_assessments.append(ei)

        if ei.approved:
            # Replace strategy fields with execution-adjusted values
            strat_ei = dict(strat)
            strat_ei["ev_after_costs"]  = ei.net_expected_edge
            strat_ei["liquidity"]       = ei.liquidity_score    # override simple bool
            strat_ei["execution_score"] = ei.execution_score
            strat_ei["_ei_candidate_id"] = ei.candidate_id
            approved_pairs.append((strat_ei, ei))
        else:
            log.info(f"[EI] {ticker} {strat.get('strategy')} "
                     f"{'GATED-OUT' if EI_GATING_ENABLED else 'OBSERVE-REJECT'}: "
                     f"{ei.rejection_reason}")

    if EI_GATING_ENABLED:
        approved_strategies = [p[0] for p in approved_pairs]
    else:
        # Observe mode: let all through (use original strategy, not ei-adjusted)
        approved_strategies = strategies

    n_approved = len(approved_pairs)
    n_total    = len(strategies)
    log.info(
        f"[EI] {ticker}: {n_approved}/{n_total} strategies approved "
        f"({'GATING' if EI_GATING_ENABLED else 'OBSERVE'})"
    )
    return approved_strategies, all_assessments


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def save_execution_assessment(
    assessment: ExecutionAssessment,
    db_url: str = "",
) -> str:
    """
    Persist a full ExecutionAssessment to aiem_execution_assessments.
    Returns candidate_id on success, empty string on failure.
    Fail-closed: any DB error is logged and returned, never silently dropped.
    """
    url = db_url or _DB_URL
    if not url:
        log.error("[EI] save_execution_assessment: no DB_URL configured")
        return ""

    _bootstrap_ei_table(url)

    # Representative leg (first option leg)
    rep = assessment.legs[0] if assessment.legs else None

    try:
        with psycopg2.connect(url, connect_timeout=8) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO aiem_execution_assessments (
                    candidate_id, trace_id, ticker, scan_date, strategy_name, n_legs,
                    bid, ask, mid, spread_pct, volume, open_interest,
                    bid_size, ask_size, iv, dte,
                    fill_probability, mid_fill_probability,
                    expected_entry_price, conservative_entry_price,
                    expected_slippage_pct, expected_slippage_dollars,
                    spread_cost_dollars, commission_dollars,
                    market_impact_dollars, total_transaction_cost,
                    legging_risk_score, exit_liquidity_score,
                    early_assignment_risk, pin_risk_flag,
                    liquidity_score, gross_expected_edge, net_expected_edge,
                    execution_uncertainty, execution_score,
                    approved, rejection_reason, position_size_factor,
                    config_sha256, raw_assessment_json, gating_enabled
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s
                )
                ON CONFLICT (candidate_id) DO UPDATE
                SET updated_at=NOW(),
                    approved=EXCLUDED.approved,
                    rejection_reason=EXCLUDED.rejection_reason
            """, (
                assessment.candidate_id,
                assessment.trace_id,
                assessment.ticker,
                assessment.scan_date,
                assessment.strategy_name,
                assessment.n_legs,
                # leg quote fields
                rep.bid if rep else None,
                rep.ask if rep else None,
                rep.mid if rep else None,
                rep.spread_pct if rep else None,
                rep.volume if rep else None,
                rep.open_interest if rep else None,
                rep.bid_size if rep else None,
                rep.ask_size if rep else None,
                rep.iv if rep else None,
                rep.dte if rep else None,
                # execution quality
                assessment.fill_probability,
                assessment.mid_fill_probability,
                assessment.expected_entry_price,
                assessment.conservative_entry_price,
                assessment.expected_slippage_pct,
                assessment.expected_slippage_dollars,
                assessment.spread_cost_dollars,
                assessment.commission_dollars,
                assessment.market_impact_dollars,
                assessment.total_transaction_cost,
                assessment.legging_risk_score,
                assessment.exit_liquidity_score,
                assessment.early_assignment_risk,
                assessment.pin_risk_flag,
                # scores / decision
                assessment.liquidity_score,
                assessment.gross_expected_edge,
                assessment.net_expected_edge,
                assessment.execution_uncertainty,
                assessment.execution_score,
                assessment.approved,
                assessment.rejection_reason,
                assessment.position_size_factor,
                # audit
                assessment.config_sha256,
                json.dumps(assessment.raw_json),
                EI_GATING_ENABLED,
            ))
            conn.commit()
        log.debug(f"[EI] saved assessment candidate_id={assessment.candidate_id}")
        return assessment.candidate_id

    except Exception as e:
        log.error(f"[EI] save_execution_assessment FAILED: {e}")
        return ""


def record_learning_outcome(
    candidate_id: str,
    actual_fill_price: Optional[float],
    actual_slippage: Optional[float],
    actual_transaction_cost: Optional[float],
    db_url: str = "",
) -> bool:
    """
    Called when a paper trade closes.
    Compares predicted vs actual execution metrics and stores errors.
    Must be called via the paper trade close path — never bypasses governance.
    """
    url = db_url or _DB_URL
    if not url or not candidate_id:
        return False

    try:
        with psycopg2.connect(url, connect_timeout=8) as conn, conn.cursor() as cur:
            # Fetch the original predictions
            cur.execute("""
                SELECT expected_entry_price, expected_slippage_dollars,
                       total_transaction_cost, fill_probability
                FROM aiem_execution_assessments
                WHERE candidate_id = %s
            """, (candidate_id,))
            row = cur.fetchone()
            if not row:
                log.warning(f"[EI] record_learning_outcome: candidate_id={candidate_id} not found")
                return False

            pred_entry, pred_slip, pred_cost, pred_fp = row

            fill_prob_error  = None  # actual fill_prob is binary (filled or not)
            entry_price_err  = (
                round(float(actual_fill_price) - float(pred_entry or 0), 4)
                if actual_fill_price is not None and pred_entry is not None else None
            )
            slippage_err     = (
                round(float(actual_slippage) - float(pred_slip or 0), 4)
                if actual_slippage is not None and pred_slip is not None else None
            )
            cost_err         = (
                round(float(actual_transaction_cost) - float(pred_cost or 0), 4)
                if actual_transaction_cost is not None and pred_cost is not None else None
            )

            cur.execute("""
                UPDATE aiem_execution_assessments
                SET actual_fill_price       = %s,
                    actual_slippage         = %s,
                    actual_transaction_cost = %s,
                    entry_price_error       = %s,
                    slippage_error          = %s,
                    cost_error              = %s,
                    updated_at              = NOW()
                WHERE candidate_id = %s
            """, (
                actual_fill_price, actual_slippage, actual_transaction_cost,
                entry_price_err, slippage_err, cost_err,
                candidate_id,
            ))
            conn.commit()

        log.info(
            f"[EI] learning outcome recorded: candidate_id={candidate_id} "
            f"entry_err={entry_price_err} slip_err={slippage_err} cost_err={cost_err}"
        )
        return True

    except Exception as e:
        log.error(f"[EI] record_learning_outcome FAILED: {e}")
        return False
