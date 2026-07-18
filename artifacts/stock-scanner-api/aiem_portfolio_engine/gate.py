"""
aiem_portfolio_engine/gate.py — S9+S11+S12: Portfolio Risk Gate Orchestrator.

Entry point: run_portfolio_gate()

13-step runtime execution order (S11):
  1.  Reconcile open positions (build_snapshot)
  2.  Compute BEFORE Greeks
  3.  Compute concentration metrics (BEFORE)
  4.  Compute correlation risk
  5.  Run 17 stress scenarios (BEFORE)
  6.  Compute liquidity-adjusted valuation (BEFORE)
  7.  Compute risk budget (BEFORE)
  8.  Compute AFTER Greeks (portfolio + candidate)
  9.  Re-run concentration metrics (AFTER)
  10. Re-run stress scenarios (AFTER)
  11. Re-compute liquidity (AFTER — with candidate)
  12. Re-compute risk budget (AFTER)
  13. Optimize & make decision

Audit evidence (S12): every decision row carries prev_evidence_hash + evidence_hash.
PE_GATING_ENABLED=False: always returns OBSERVE decision; gate does not block.
"""
from __future__ import annotations
import hashlib, json, uuid, datetime, traceback
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

import psycopg2

from .config import PE_GATING_ENABLED, pe_config_sha, NOT_IMPLEMENTED_V1, GATE_STEPS
from .db import bootstrap_portfolio_tables
from .snapshot import PortfolioSnapshot, build_snapshot, save_snapshot
from .greeks import PortfolioGreeks, compute_portfolio_greeks, save_portfolio_greeks
from .limits import ConcentrationResult, RiskBudget, check_concentration, check_risk_budget
from .correlation import CorrelationResult, check_correlation
from .stress import StressScenario, run_stress_tests, worst_stress_loss, save_stress_results
from .valuation import LiquidityValuation, compute_liquidity_adjusted_valuation
from .optimizer import OptimizationResult, optimize_portfolio

try:
    from sector_etf_data import TICKER_SECTOR_MAP
    def _sector(ticker: str) -> Optional[str]:
        return TICKER_SECTOR_MAP.get(ticker.upper())
except ImportError:
    def _sector(ticker: str) -> Optional[str]:
        return None


_GENESIS_HASH = "0" * 64


def _gate_id() -> str:
    return f"ape_gate_{uuid.uuid4().hex[:16]}"


def _evidence_hash(row_data: Dict[str, Any]) -> str:
    blob = json.dumps(row_data, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _fetch_prev_evidence_hash(db_url: str) -> str:
    """Fetch evidence_hash of the most recent ape_gate_decisions row."""
    try:
        conn = psycopg2.connect(db_url, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT evidence_hash FROM ape_gate_decisions "
                "WHERE evidence_hash IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
        conn.close()
        return row[0] if row and row[0] else _GENESIS_HASH
    except Exception:
        return _GENESIS_HASH


@dataclass
class PortfolioDecision:
    candidate_id:        str
    trace_id:            str
    snapshot_id:         str
    ticker:              str
    strategy_name:       str
    requested_size:      int
    approved_size:       int
    decision:            str
    decision_reasons:    List[str]
    limits_tested:       List[str]
    limits_passed:       List[str]
    limits_failed:       List[str]
    pe_gating_enabled:   bool
    config_sha256:       str
    prev_evidence_hash:  str
    evidence_hash:       str
    concentration:       Optional[Dict] = None
    correlation:         Optional[Dict] = None
    stress_scenarios:    Optional[List[Dict]] = None
    valuation:           Optional[Dict] = None
    risk_budget:         Optional[Dict] = None
    optimization:        Optional[Dict] = None
    greeks_before:       Optional[Dict] = None
    greeks_after:        Optional[Dict] = None
    reconcile_error:     Optional[str] = None
    not_implemented:     List[str] = field(default_factory=list)
    executed_steps:      List[str] = field(default_factory=list)

    def gate_passed(self) -> bool:
        """True if the gate allows the trade to proceed."""
        if not self.pe_gating_enabled:
            return True  # observe mode — never blocks
        return self.decision in ("APPROVE", "APPROVE_REDUCED_SIZE")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "trace_id": self.trace_id,
            "ticker": self.ticker,
            "strategy_name": self.strategy_name,
            "decision": self.decision,
            "decision_reasons": self.decision_reasons,
            "approved_size": self.approved_size,
            "requested_size": self.requested_size,
            "pe_gating_enabled": self.pe_gating_enabled,
            "config_sha256": self.config_sha256,
            "evidence_hash": self.evidence_hash,
            "prev_evidence_hash": self.prev_evidence_hash,
            "limits_passed": self.limits_passed,
            "limits_failed": self.limits_failed,
        }


def _extract_candidate_info(evaluation: Any, selection: Any, ticker: str) -> Dict:
    """Extract relevant fields from EvaluationResult for the gate."""
    legs      = getattr(evaluation, "legs", [])
    payoff    = getattr(evaluation, "payoff_info", {}) or {}
    pricing   = getattr(evaluation, "pricing_info", {}) or {}
    prob      = getattr(evaluation, "probability_info", {}) or {}
    greeks_i  = getattr(evaluation, "greeks_info", {}) or {}

    n_contracts = max(1, sum(
        getattr(lg, "quantity", 1)
        for lg in legs
        if getattr(lg, "asset_type", "") in ("CALL", "PUT")
    ))

    candidate_legs = []
    for lg in legs:
        candidate_legs.append({
            "leg_number":   getattr(lg, "leg_number", 1) if hasattr(lg, "leg_number") else 1,
            "asset_type":   getattr(lg, "asset_type", "CALL"),
            "call_or_put":  getattr(lg, "asset_type", None),
            "buy_or_sell":  getattr(lg, "side", "LONG"),
            "quantity":     getattr(lg, "quantity", 1),
            "ratio":        getattr(lg, "ratio", 1.0),
            "strike":       getattr(lg, "strike", None),
            "expiration_date": str(getattr(lg, "expiration", "")) if getattr(lg, "expiration", None) else None,
            "dte":          getattr(lg, "dte", 14),
            "bid":          getattr(lg, "bid", None),
            "ask":          getattr(lg, "ask", None),
            "mid":          getattr(lg, "mid", None),
            "iv":           getattr(lg, "iv", None),
            "implied_volatility": getattr(lg, "iv", None),
            "delta":        getattr(lg, "delta", None),
            "gamma":        getattr(lg, "gamma", None),
            "theta":        getattr(lg, "theta", None),
            "vega":         getattr(lg, "vega", None),
            "rho":          getattr(lg, "rho", None),
        })

    first_expiry = None
    for lg in legs:
        exp = getattr(lg, "expiration", None)
        if exp:
            first_expiry = str(exp)
            break

    is_undefined = (
        getattr(evaluation, "risk_class", None) == "UNDEFINED"
        or bool(payoff.get("is_undefined_risk"))
    )

    from .snapshot import _classify_vol
    strat_name   = getattr(evaluation, "strategy_name", "") or ""
    strat_family = getattr(evaluation, "strategy_family", None) or ""
    is_lv, is_sv = _classify_vol(strat_name, strat_family)

    direction = getattr(selection, "direction", None) or (
        getattr(selection, "thesis", "") if hasattr(selection, "thesis") else None
    )
    spot = float(getattr(selection, "underlying_price", None) or
                 getattr(evaluation, "spot", None) or 100.0)

    return {
        "ticker":            ticker,
        "strategy_name":     strat_name,
        "strategy_family":   strat_family,
        "direction":         direction,
        "capital":           float(pricing.get("capital_at_risk") or payoff.get("max_loss") or 0),
        "max_loss":          float(payoff.get("max_loss") or 0),
        "ev":                float(pricing.get("ev_after_costs") or 0),
        "pop":               float(prob.get("pop") or 0),
        "n_contracts":       n_contracts,
        "candidate_legs":    candidate_legs,
        "expiry":            first_expiry,
        "sector":            _sector(ticker),
        "is_long_vol":       is_lv,
        "is_short_vol":      is_sv,
        "is_undefined_risk": is_undefined,
        "spot":              spot,
    }


def run_portfolio_gate(
    evaluation:  Any,
    selection:   Any,
    ticker:      str,
    run_id:      str,
    db_url:      str,
) -> "PortfolioDecision":
    """
    Run all 13 portfolio gate steps. Always returns a PortfolioDecision.
    In observe mode (PE_GATING_ENABLED=False) the decision is logged but never blocks.
    Fail closed: any unhandled exception → REJECT.
    """
    candidate_id = _gate_id()
    config_sha   = pe_config_sha()

    try:
        # Ensure tables exist (idempotent, fast on seeded DB)
        try:
            bootstrap_portfolio_tables(db_url)
        except Exception:
            pass

        # ── Extract candidate info ────────────────────────────────────────────
        c = _extract_candidate_info(evaluation, selection, ticker)
        _steps: List[str] = []

        # ── Step 1: Build portfolio snapshot ─────────────────────────────────
        _steps.append(GATE_STEPS[0])   # S01_reconcile_positions
        snapshot = build_snapshot(run_id, db_url)
        try:
            save_snapshot(snapshot, db_url)
        except Exception:
            pass

        if not snapshot.reconciled:
            return _fail_decision(
                candidate_id, run_id, snapshot.snapshot_id, ticker,
                c["strategy_name"], c["n_contracts"],
                f"RECONCILE_FAILED: {snapshot.reconcile_error}",
                config_sha, db_url,
            )

        # ── Step 2: BEFORE Greeks ─────────────────────────────────────────────
        _steps.append(GATE_STEPS[1])   # S02_greeks_before
        greeks_before = compute_portfolio_greeks(snapshot.positions)

        # ── Step 3: Concentration BEFORE ──────────────────────────────────────
        _steps.append(GATE_STEPS[2])   # S03_concentration_before
        # Derive first candidate strike for strike-area concentration check
        _cand_strike = None
        for _leg in c.get("candidate_legs", []):
            if _leg.get("strike") is not None:
                _cand_strike = float(_leg["strike"])
                break
        conc_before = check_concentration(
            snapshot=snapshot,
            candidate_ticker=c["ticker"],
            candidate_capital=c["capital"],
            candidate_strategy_name=c["strategy_name"],
            candidate_strategy_family=c["strategy_family"],
            candidate_direction=c["direction"],
            candidate_is_long_vol=c["is_long_vol"],
            candidate_is_short_vol=c["is_short_vol"],
            candidate_expiry=c["expiry"],
            candidate_sector=c["sector"],
            candidate_is_undefined_risk=c["is_undefined_risk"],
            candidate_strike=_cand_strike,
        )

        # ── Step 4: Correlation risk ───────────────────────────────────────────
        _steps.append(GATE_STEPS[3])   # S04_correlation_risk
        correlation = check_correlation(
            snapshot=snapshot,
            candidate_ticker=c["ticker"],
            candidate_capital=c["capital"],
            db_url=db_url,
        )

        # ── Step 5: Stress scenarios BEFORE ───────────────────────────────────
        _steps.append(GATE_STEPS[4])   # S05_stress_before
        stress_before = run_stress_tests(
            snapshot=snapshot,
            candidate_legs=None,
            candidate_spot=c["spot"],
        )

        # ── Step 6: Liquidity valuation BEFORE ────────────────────────────────
        _steps.append(GATE_STEPS[5])   # S06_liquidity_before
        valuation_before = compute_liquidity_adjusted_valuation(
            snapshot=snapshot,
            candidate_legs=None,
            candidate_capital=0.0,
        )

        # ── Step 7: Risk budget BEFORE ────────────────────────────────────────
        _steps.append(GATE_STEPS[6])   # S07_risk_budget_before
        wsl_before    = worst_stress_loss(stress_before)
        budget_before = check_risk_budget(snapshot, greeks_before, wsl_before)

        # ── Step 8: AFTER Greeks ──────────────────────────────────────────────
        _steps.append(GATE_STEPS[7])   # S08_greeks_after
        greeks_after = compute_portfolio_greeks(
            snapshot.positions,
            candidate_legs=c["candidate_legs"],
            candidate_spot=c["spot"],
        )

        # ── Step 9: Concentration AFTER (re-evaluate with candidate legs) ─────
        _steps.append(GATE_STEPS[8])   # S09_concentration_after
        conc_after = check_concentration(
            snapshot=snapshot,
            candidate_ticker=c["ticker"],
            candidate_capital=c["capital"],
            candidate_strategy_name=c["strategy_name"],
            candidate_strategy_family=c["strategy_family"],
            candidate_direction=c["direction"],
            candidate_is_long_vol=c["is_long_vol"],
            candidate_is_short_vol=c["is_short_vol"],
            candidate_expiry=c["expiry"],
            candidate_sector=c["sector"],
            candidate_is_undefined_risk=c["is_undefined_risk"],
            candidate_strike=_cand_strike,
        )

        # ── Step 10: Stress scenarios AFTER ───────────────────────────────────
        _steps.append(GATE_STEPS[9])   # S10_stress_after
        stress_after = run_stress_tests(
            snapshot=snapshot,
            candidate_legs=c["candidate_legs"],
            candidate_spot=c["spot"],
        )

        # ── Step 11: Liquidity valuation AFTER ────────────────────────────────
        _steps.append(GATE_STEPS[10])  # S11_liquidity_after
        valuation_after = compute_liquidity_adjusted_valuation(
            snapshot=snapshot,
            candidate_legs=c["candidate_legs"],
            candidate_capital=c["capital"],
        )

        # ── Step 12: Risk budget AFTER ────────────────────────────────────────
        _steps.append(GATE_STEPS[11])  # S12_risk_budget_after
        wsl_after    = worst_stress_loss(stress_after)
        budget_after = check_risk_budget(snapshot, greeks_after, wsl_after)

        # ── Step 13: Optimize & decide ────────────────────────────────────────
        _steps.append(GATE_STEPS[12])  # S13_optimize_decide
        optimization = optimize_portfolio(
            snapshot                = snapshot,
            candidate_ticker        = c["ticker"],
            candidate_strategy_name = c["strategy_name"],
            candidate_ev            = c["ev"],
            candidate_pop           = c["pop"],
            candidate_capital       = c["capital"],
            requested_contracts     = c["n_contracts"],
            greeks_before           = greeks_before,
            greeks_after            = greeks_after,
            concentration           = conc_before,
            correlation             = correlation,
            stress_before           = stress_before,
            stress_after            = stress_after,
            valuation               = valuation_after,
            risk_budget             = budget_after,
        )

        # ── Persist greeks + stress to DB ─────────────────────────────────────
        try:
            save_portfolio_greeks(snapshot.snapshot_id, "BEFORE", greeks_before, db_url)
            save_portfolio_greeks(snapshot.snapshot_id, "AFTER", greeks_after, db_url)
        except Exception:
            pass
        try:
            save_stress_results(snapshot.snapshot_id, "BEFORE", stress_before, db_url)
            save_stress_results(snapshot.snapshot_id, "AFTER", stress_after, db_url)
        except Exception:
            pass

        # ── Classify limits ───────────────────────────────────────────────────
        limits_tested = [
            "MAX_TICKER_CONCENTRATION", "MAX_SECTOR_CONCENTRATION",
            "MAX_STRATEGY_FAMILY_CONC", "MAX_EXPIRATION_CONCENTRATION",
            "MAX_BULLISH_CONCENTRATION", "MAX_BEARISH_CONCENTRATION",
            "MAX_LONG_VOL_CONCENTRATION", "MAX_SHORT_VOL_CONCENTRATION",
            "MAX_UNDEFINED_RISK_EXPOSURE", "MAX_SIMULTANEOUS_POSITIONS",
            "MAX_BUYING_POWER_UTILIZATION", "MAX_PORTFOLIO_RISK_UTILIZATION",
            "MAX_CORRELATION_CLUSTER_EXP", "HISTORICAL_CORRELATION",
            "MAX_PORTFOLIO_DELTA", "MAX_PORTFOLIO_VEGA", "MAX_PORTFOLIO_THETA",
            "STRESS_TEST_LOSS_LIMIT", "LIQUIDITY_ADJ_LOSS_LIMIT",
        ]
        breached_names = (
            {b.limit_name for b in conc_before.breaches}
            | {b.limit_name for b in budget_after.breaches}
            | ({f"CORRELATION_{correlation.action}" } if correlation.action != "APPROVE" else set())
            | ({"LIQUIDITY_ADJ_LOSS_LIMIT"} if valuation_after.liquidity_limit_breach else set())
        )
        limits_passed = [l for l in limits_tested if l not in breached_names]
        limits_failed = [l for l in limits_tested if l in breached_names]

        # ── Observe-mode override ─────────────────────────────────────────────
        effective_decision = optimization.decision
        if not PE_GATING_ENABLED:
            effective_decision = "OBSERVE_" + optimization.decision
            approved_size      = c["n_contracts"]   # observe: never resize
        else:
            approved_size = optimization.approved_size

        # ── Build evidence hash ───────────────────────────────────────────────
        prev_hash = _fetch_prev_evidence_hash(db_url)
        evidence_payload = {
            "candidate_id":       candidate_id,
            "trace_id":           run_id,
            "ticker":             ticker,
            "strategy_name":      c["strategy_name"],
            "decision":           effective_decision,
            "approved_size":      approved_size,
            "limits_failed":      sorted(limits_failed),
            "stress_worst":       round(wsl_after, 2),
            "greeks_after_delta": round(greeks_after.total_delta, 4),
            "config_sha256":      config_sha,
            "prev_hash":          prev_hash,
        }
        ev_hash = _evidence_hash(evidence_payload)

        decision = PortfolioDecision(
            candidate_id       = candidate_id,
            trace_id           = run_id,
            snapshot_id        = snapshot.snapshot_id,
            ticker             = ticker,
            strategy_name      = c["strategy_name"],
            requested_size     = c["n_contracts"],
            approved_size      = approved_size,
            decision           = effective_decision,
            decision_reasons   = optimization.reasons,
            limits_tested      = limits_tested,
            limits_passed      = limits_passed,
            limits_failed      = limits_failed,
            pe_gating_enabled  = PE_GATING_ENABLED,
            config_sha256      = config_sha,
            prev_evidence_hash = prev_hash,
            evidence_hash      = ev_hash,
            concentration      = conc_before.to_dict(),
            correlation        = correlation.to_dict(),
            stress_scenarios   = [s.to_dict() for s in stress_after],
            valuation          = valuation_after.to_dict(),
            risk_budget        = budget_after.to_dict(),
            optimization       = optimization.to_dict(),
            greeks_before      = greeks_before.to_dict(),
            greeks_after       = greeks_after.to_dict(),
            reconcile_error    = None,
            not_implemented    = NOT_IMPLEMENTED_V1,
            executed_steps     = _steps,
        )

        _save_gate_decision(decision, c, db_url)
        return decision

    except Exception as exc:
        tb = traceback.format_exc()
        return _fail_decision(
            candidate_id, run_id, "", ticker, "", 1,
            f"GATE_EXCEPTION: {type(exc).__name__}: {exc}\n{tb}",
            config_sha, db_url,
        )


def _fail_decision(
    candidate_id: str,
    trace_id: str,
    snapshot_id: str,
    ticker: str,
    strategy_name: str,
    n_contracts: int,
    reason: str,
    config_sha: str,
    db_url: str,
) -> PortfolioDecision:
    """Return a fail-closed REJECT decision without running any analysis."""
    prev_hash = _fetch_prev_evidence_hash(db_url)
    ev_hash   = _evidence_hash({
        "candidate_id": candidate_id,
        "trace_id":     trace_id,
        "ticker":       ticker,
        "decision":     "REJECT",
        "reason":       reason,
        "prev_hash":    prev_hash,
    })
    d = PortfolioDecision(
        candidate_id       = candidate_id,
        trace_id           = trace_id,
        snapshot_id        = snapshot_id,
        ticker             = ticker,
        strategy_name      = strategy_name,
        requested_size     = n_contracts,
        approved_size      = 0,
        decision           = "REJECT",
        decision_reasons   = [reason],
        limits_tested      = [],
        limits_passed      = [],
        limits_failed      = ["RECONCILE"],
        pe_gating_enabled  = PE_GATING_ENABLED,
        config_sha256      = config_sha,
        prev_evidence_hash = prev_hash,
        evidence_hash      = ev_hash,
        reconcile_error    = reason,
    )
    try:
        _save_gate_decision(d, {}, db_url)
    except Exception:
        pass
    return d


def _save_gate_decision(decision: PortfolioDecision, c: Dict, db_url: str) -> None:
    """Persist gate decision to ape_gate_decisions."""
    today = datetime.date.today()
    with psycopg2.connect(db_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ape_gate_decisions (
                    candidate_id, trace_id, snapshot_id, ticker, scan_date,
                    strategy_name, requested_size, approved_size,
                    concentration_json, correlation_json, stress_json,
                    liquidity_json, budget_json, optimization_json,
                    greeks_before_json, greeks_after_json,
                    decision, decision_reasons, limits_tested, limits_passed, limits_failed,
                    pe_gating_enabled, config_sha256, prev_evidence_hash, evidence_hash
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
            """, (
                decision.candidate_id, decision.trace_id, decision.snapshot_id,
                decision.ticker, today, decision.strategy_name,
                decision.requested_size, decision.approved_size,
                json.dumps(decision.concentration),
                json.dumps(decision.correlation),
                json.dumps(decision.stress_scenarios),
                json.dumps(decision.valuation),
                json.dumps(decision.risk_budget),
                json.dumps(decision.optimization),
                json.dumps(decision.greeks_before),
                json.dumps(decision.greeks_after),
                decision.decision,
                json.dumps(decision.decision_reasons),
                json.dumps(decision.limits_tested),
                json.dumps(decision.limits_passed),
                json.dumps(decision.limits_failed),
                decision.pe_gating_enabled,
                decision.config_sha256,
                decision.prev_evidence_hash,
                decision.evidence_hash,
            ))
        conn.commit()
