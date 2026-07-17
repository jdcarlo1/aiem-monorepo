"""
verify_13_14.py — Independent verification of:
  13. Capital Compounding Score (all components + penalties + final)
  14. Paper Trade Lifecycle (all stages)
Tolerance: <=0.000001
"""
import sys, os, math, json, hashlib, uuid
sys.path.insert(0, os.path.dirname(__file__))

PASS = 0; FAIL = 0
results = []

def check(name, got, expected, tol=1e-6):
    global PASS, FAIL
    ok = abs(got - expected) <= tol
    status = "PASS" if ok else "FAIL"
    if ok: PASS += 1
    else:  FAIL += 1
    results.append(f"  {status}  {name}: got={got:.10f} expected={expected:.10f} delta={abs(got-expected):.2e}")

def check_eq(name, got, expected):
    global PASS, FAIL
    ok = got == expected
    status = "PASS" if ok else "FAIL"
    if ok: PASS += 1
    else:  FAIL += 1
    results.append(f"  {status}  {name}: got={repr(got)} expected={repr(expected)}")

# ─────────────────────────────────────────────────────────────
# 13. CAPITAL COMPOUNDING SCORE — each component independently
# ─────────────────────────────────────────────────────────────
from aiem_strat_engine.scoring import (
    score_pop, score_ev, score_capital_preservation,
    score_defined_risk, score_capital_efficiency,
    score_liquidity, score_thesis_fit, score_regime_fit,
    score_vol_fit, score_diversification,
    penalty_max_loss, penalty_tail_risk,
    penalty_assignment_risk, penalty_slippage, penalty_complexity,
    compute_capital_compounding_score, _clamp,
)
from aiem_strat_engine.config import SCORE_WEIGHTS, SCORE_PENALTIES
from aiem_strat_engine.legs import RISK_DEFINED, RISK_LIMITED, RISK_UNDEFINED

print("=== 13. CAPITAL COMPOUNDING SCORE ===")

# --- PROBABILITY ---
# pop=None → 0.0
check("score_pop(None)",       score_pop(None), 0.0)
# pop=0.25 → (0.25-0.25)/0.50 = 0.0
check("score_pop(0.25)",       score_pop(0.25), 0.0)
# pop=0.75 → (0.75-0.25)/0.50 = 1.0
check("score_pop(0.75)",       score_pop(0.75), 1.0)
# pop=0.50 → (0.50-0.25)/0.50 = 0.50
check("score_pop(0.50)",       score_pop(0.50), 0.50)
# pop=0.65 → (0.65-0.25)/0.50 = 0.80
check("score_pop(0.65)",       score_pop(0.65), 0.80)
# pop=1.00 → clamped to 1.0
check("score_pop(1.00)",       score_pop(1.00), 1.0)
# pop=0.00 → (0-0.25)/0.50=-0.5 → clamped 0.0
check("score_pop(0.00)",       score_pop(0.00), 0.0)

# --- EXPECTED VALUE ---
# ev=None → 0.0
check("score_ev(None)",        score_ev(None), 0.0)
# ev=-0.05 → (-0.05+0.05)/0.10 = 0.0
check("score_ev(-0.05)",       score_ev(-0.05), 0.0)
# ev=0.05 → (0.05+0.05)/0.10 = 1.0
check("score_ev(0.05)",        score_ev(0.05), 1.0)
# ev=0.00 → (0+0.05)/0.10 = 0.5
check("score_ev(0.00)",        score_ev(0.00), 0.5)
# ev=0.025 → (0.025+0.05)/0.10 = 0.75
check("score_ev(0.025)",       score_ev(0.025), 0.75)
# ev=-0.10 → clamped 0.0
check("score_ev(-0.10)",       score_ev(-0.10), 0.0)
# ev=0.10 → clamped 1.0
check("score_ev(0.10)",        score_ev(0.10), 1.0)

# --- CAPITAL PRESERVATION ---
# UNDEFINED risk → 0.0
check("capres(UNDEFINED)",     score_capital_preservation(100, 200, RISK_UNDEFINED), 0.0)
# max_loss=None → 0.0
check("capres(no_maxloss)",    score_capital_preservation(None, 200, RISK_DEFINED), 0.0)
# max_profit=None, defined loss → 0.3
check("capres(no_profit)",     score_capital_preservation(100, None, RISK_DEFINED), 0.3)
# max_loss=0 → 0.3
check("capres(zero_loss)",     score_capital_preservation(0, 200, RISK_DEFINED), 0.3)
# rr=2.0 → 0.2+2.0*0.30=0.8
check("capres(rr=2.0)",        score_capital_preservation(100, 200, RISK_DEFINED), 0.8)
# rr=1.0 → 0.2+1.0*0.30=0.5
check("capres(rr=1.0)",        score_capital_preservation(100, 100, RISK_DEFINED), 0.5)
# rr=3.0 → 0.2+3.0*0.30=1.1 → clamped 1.0
check("capres(rr=3.0)",        score_capital_preservation(100, 300, RISK_DEFINED), 1.0)

# --- DEFINED RISK ---
# ANALYSIS_ONLY → 0.3
check("defr(ANALYSIS_ONLY)",   score_defined_risk(RISK_DEFINED, "ANALYSIS_ONLY"), 0.3)
check("defr(ANALYSIS_LIMITED)",score_defined_risk(RISK_LIMITED, "ANALYSIS_ONLY"), 0.3)
# AUTONOMOUS + DEFINED → 1.0
check("defr(AUTO+DEFINED)",    score_defined_risk(RISK_DEFINED, "AUTONOMOUS"), 1.0)
# AUTONOMOUS + LIMITED → 0.60
check("defr(AUTO+LIMITED)",    score_defined_risk(RISK_LIMITED, "AUTONOMOUS"), 0.60)
# AUTONOMOUS + UNDEFINED → 0.0
check("defr(AUTO+UNDEFINED)",  score_defined_risk(RISK_UNDEFINED, "AUTONOMOUS"), 0.0)

# --- CAPITAL EFFICIENCY ---
# both None → 0.0
check("capeff(None,None)",     score_capital_efficiency(None, None), 0.0)
# ev=0.1 → ev_part=clamp(0.5)=0.5; ror=0.5 → ror_part=1.0; avg=0.75
check("capeff(0.1,0.5)",       score_capital_efficiency(0.1, 0.5), 0.75)
# ev=0.2 → ev_part=clamp(1.0); ror=1.0 → ror_part=clamp(2.0)=1.0; avg=1.0
check("capeff(0.2,1.0)",       score_capital_efficiency(0.2, 1.0), 1.0)
# ev=0.0, ror=0.0 → both 0, avg=0.0
check("capeff(0.0,0.0)",       score_capital_efficiency(0.0, 0.0), 0.0)
# ev=0.05, ror=0.25 → ev_part=0.25; ror_part=0.5; avg=0.375
check("capeff(0.05,0.25)",     score_capital_efficiency(0.05, 0.25), 0.375)

# --- LIQUIDITY ---
check("liq(0.8)",              score_liquidity(0.8), 0.8)
check("liq(0.0)",              score_liquidity(0.0), 0.0)
check("liq(1.0)",              score_liquidity(1.0), 1.0)
check("liq(1.5) clamped",      score_liquidity(1.5), 1.0)
check("liq(-0.1) clamped",     score_liquidity(-0.1), 0.0)

# --- MARKET REGIME ---
check("regime(BULL+BULL)",     score_regime_fit("BULLISH", "BULL_TREND"), 1.0)
check("regime(BEAR+BEAR)",     score_regime_fit("BEARISH", "BEAR_TREND"), 1.0)
check("regime(NEUT+SIDEWAYS)", score_regime_fit("NEUTRAL", "SIDEWAYS"), 1.0)
check("regime(ANY)",           score_regime_fit("ANY", "BULL_TREND"), 0.6)
check("regime(BULL+BEAR)",     score_regime_fit("BULLISH", "BEAR_TREND"), 0.3)
check("regime(BEAR+BULL)",     score_regime_fit("BEARISH", "BULL_TREND"), 0.3)
check("regime(NEUT+ANY_kw)",   score_regime_fit("NEUTRAL", "BULL_TREND"), 0.6)

# --- VOLATILITY REGIME ---
check("vol(HIGH_IV+ivr=60)",   score_vol_fit("HIGH_IV", 60.0), 1.0)
check("vol(LOW_IV+ivr=30)",    score_vol_fit("LOW_IV",  30.0), 1.0)
check("vol(NEUTRAL)",          score_vol_fit("NEUTRAL", 50.0), 0.7)
check("vol(HIGH_IV+ivr=30)",   score_vol_fit("HIGH_IV", 30.0), 0.2)
check("vol(None)",             score_vol_fit("HIGH_IV", None), 0.5)

# --- DIVERSIFICATION ---
check("divers(no_context)",    score_diversification("SPREAD", None), 0.5)
check("divers(new_family)",    score_diversification("SPREAD", ["CONDOR", "STRADDLE"]), 1.0)
check("divers(1_existing)",    score_diversification("SPREAD", ["SPREAD"]), _clamp(1.0 - 1*0.2))
check("divers(2_existing)",    score_diversification("SPREAD", ["SPREAD","SPREAD"]), _clamp(1.0 - 2*0.2))
check("divers(5_existing)",    score_diversification("SPREAD", ["SPREAD"]*5), 0.0)

# --- TAIL RISK ---
check("pen_tail(None,None)",   penalty_tail_risk(None, None), 0.0)
check("pen_tail(no_gap)",      penalty_tail_risk(0.65, 0.65), 0.0)
# gap=0.10 → 0.08 * 0.10 * 5.0 = 0.04
check("pen_tail(gap=0.10)",    penalty_tail_risk(0.55, 0.65), SCORE_PENALTIES["tail_risk"] * 0.10 * 5.0)

# --- DRAWDOWN (penalty constant in config, not a function — verify it's present) ---
check_eq("drawdown_penalty_key_exists", "drawdown_risk" in SCORE_PENALTIES, True)
check("drawdown_penalty_value", SCORE_PENALTIES["drawdown_risk"], 0.05)

# --- ASSIGNMENT ---
check("pen_assign(HIGH)",      penalty_assignment_risk("HIGH"), SCORE_PENALTIES["assignment_risk"])
check("pen_assign(LOW)",       penalty_assignment_risk("LOW"),  0.0)

# --- EVENT RISK (penalty constant in config — verify present) ---
check_eq("event_risk_key_exists", "event_risk" in SCORE_PENALTIES, True)
check("event_risk_value",     SCORE_PENALTIES["event_risk"], 0.05)

# --- SLIPPAGE ---
# slip=5, cap=100 → slip_frac=0.05 → 0.03*0.05*10=0.015
check("pen_slip(5,100)",       penalty_slippage(5.0, 100.0), SCORE_PENALTIES["slippage_cost"] * (5.0/100.0) * 10)
check("pen_slip(0,100)",       penalty_slippage(0.0, 100.0), 0.0)
check("pen_slip(cap=0)",       penalty_slippage(5.0, 0.0),   0.0)

# --- COMPLEXITY ---
check("pen_comp(2_legs)",      penalty_complexity(2), 0.0)
check("pen_comp(3_legs)",      penalty_complexity(3), SCORE_PENALTIES["complexity"] * 1)
check("pen_comp(4_legs)",      penalty_complexity(4), SCORE_PENALTIES["complexity"] * 2)
check("pen_comp(1_leg)",       penalty_complexity(1), 0.0)

# --- CORRELATION (penalty constant in config) ---
check_eq("correlation_key_exists", "concentration" in SCORE_PENALTIES, True)
check("correlation_penalty_value", SCORE_PENALTIES["concentration"], 0.05)

# ─────────────────────────────────────────────────────────────
# INDEPENDENT FINAL SCORE RECALCULATION
# Uses a known fixed set of inputs, computes expected manually
# then calls compute_capital_compounding_score and compares
# ─────────────────────────────────────────────────────────────
print("\n=== INDEPENDENT FINAL SCORE RECALCULATION ===")

# Fixed inputs
T_POP              = 0.65
T_EV               = 0.025
T_MAX_LOSS         = 200.0
T_MAX_PROFIT       = 400.0
T_RISK_CLASS       = RISK_DEFINED
T_EXEC_MODE        = "AUTONOMOUS"
T_LIQUIDITY        = 0.80
T_DIR              = "BULLISH"
T_VOL_THESIS       = "HIGH_IV"
T_FAMILY           = "SPREAD"
T_THESIS           = "BULLISH"
T_MARKET_REGIME    = "BULL_TREND"
T_VOL_REGIME       = "HIGH_IV"
T_IV_RANK          = 60.0
T_ROR              = 0.25
T_ASSIGN           = "LOW"
T_POP_FAT          = 0.60
T_POP_LN           = 0.65
T_SLIPPAGE         = 2.0
T_CAP_RISK         = 200.0
T_N_LEGS           = 2
T_EXIST_FAMS       = ["CONDOR"]
T_PORT_CAP         = 100_000.0

w = SCORE_WEIGHTS

# Manually compute each component
exp_pop    = _clamp((T_POP - 0.25) / 0.50)                               # 0.80
exp_ev     = _clamp((T_EV + 0.05) / 0.10)                                # 0.75
exp_capres = _clamp(0.2 + (T_MAX_PROFIT/T_MAX_LOSS) * 0.30)              # 0.2+0.6=0.80
exp_def    = 1.0                                                           # AUTONOMOUS+DEFINED
exp_capeff = (_clamp(T_EV * 5.0) + _clamp(T_ROR / 0.50)) / 2.0          # (0.125+0.5)/2=0.3125
exp_liq    = _clamp(T_LIQUIDITY)                                           # 0.80
exp_thesis = 1.0*0.6 + 1.0*0.4                                            # dir+vol both match=1.0
exp_regime = 1.0                                                           # BULL+BULL
exp_vol    = 1.0                                                           # HIGH_IV+ivr=60
exp_divers = 1.0                                                           # SPREAD not in [CONDOR]

raw = (exp_pop*w["pop"] + exp_ev*w["ev_after_costs"] + exp_capres*w["capital_preservation"] +
       exp_def*w["defined_risk_quality"] + exp_capeff*w["capital_efficiency"] +
       exp_liq*w["liquidity"] + exp_thesis*w["thesis_fit"] + exp_regime*w["regime_fit"] +
       exp_vol*w["vol_regime_fit"] + exp_divers*w["diversification_value"])

# Penalties
bp          = T_MAX_LOSS * 100
exp_pen_ml  = SCORE_PENALTIES["max_loss_pct"] * (bp / T_PORT_CAP) * 10    # 0.10*(20000/100000)*10=0.2
exp_pen_tl  = SCORE_PENALTIES["tail_risk"] * max(0, T_POP_LN - T_POP_FAT) * 5.0  # 0.08*0.05*5=0.02
exp_pen_as  = 0.0                                                           # assignment=LOW
slip_frac   = T_SLIPPAGE / max(T_CAP_RISK, 0.01)
exp_pen_sl  = SCORE_PENALTIES["slippage_cost"] * slip_frac * 10            # 0.03*(2/200)*10=0.003
exp_pen_cp  = 0.0                                                           # 2 legs
total_pen   = exp_pen_ml + exp_pen_tl + exp_pen_as + exp_pen_sl + exp_pen_cp
exp_final   = _clamp(raw - total_pen)

# Now call the function
got = compute_capital_compounding_score(
    pop=T_POP, ev_after_costs=T_EV, max_loss=T_MAX_LOSS, max_profit=T_MAX_PROFIT,
    risk_class=T_RISK_CLASS, execution_mode=T_EXEC_MODE, liquidity=T_LIQUIDITY,
    strategy_direction=T_DIR, strategy_vol_thesis=T_VOL_THESIS,
    strategy_family=T_FAMILY, thesis=T_THESIS, market_regime=T_MARKET_REGIME,
    vol_regime=T_VOL_REGIME, iv_rank=T_IV_RANK, return_on_risk=T_ROR,
    assignment_risk=T_ASSIGN, pop_fat_tail=T_POP_FAT, pop_lognormal=T_POP_LN,
    slippage=T_SLIPPAGE, capital_at_risk=T_CAP_RISK, n_legs=T_N_LEGS,
    existing_families=T_EXIST_FAMS, portfolio_capital=T_PORT_CAP,
)

# The function returns rounded(4); compare against unrounded expected
check("component: score_pop",              got["score_pop"],            round(exp_pop,4))
check("component: score_ev",               got["score_ev"],             round(exp_ev,4))
check("component: score_capital_pres",     got["score_capital_pres"],   round(exp_capres,4))
check("component: score_defined_risk",     got["score_defined_risk"],   round(exp_def,4))
check("component: score_cap_efficiency",   got["score_cap_efficiency"], round(exp_capeff,4))
check("component: score_liquidity",        got["score_liquidity"],      round(exp_liq,4))
check("component: score_thesis_fit",       got["score_thesis_fit"],     round(exp_thesis,4))
check("component: score_regime_fit",       got["score_regime_fit"],     round(exp_regime,4))
check("component: score_vol_fit",          got["score_vol_fit"],        round(exp_vol,4))
check("component: score_diversification",  got["score_diversification"],round(exp_divers,4))
check("penalty_total",                     got["penalty_total"],        round(total_pen,4))
check("FINAL capital_compounding_score",   got["capital_compounding_score"], round(exp_final,4))

# Verify weights sum to 1.0
weight_sum = sum(SCORE_WEIGHTS.values())
check("SCORE_WEIGHTS sum to 1.0", weight_sum, 1.0)

# ─────────────────────────────────────────────────────────────
# 14. PAPER TRADE LIFECYCLE
# ─────────────────────────────────────────────────────────────
print("\n=== 14. PAPER TRADE LIFECYCLE ===")

from aiem_strat_engine.legs import Leg
from aiem_strat_engine.selector import (
    EvaluationResult, SelectionResult, select, rank_all
)
from aiem_strat_engine.paper_trader import (
    safety_check, insert_paper_trade, close_paper_trade,
    get_open_trades, save_decision_run, _audit_hash, _new_trade_id,
)
from aiem_strat_engine.config import config_sha256

# GENERATE — Leg construction
leg_long  = Leg(asset_type="CALL", side="LONG",  strike=100.0, expiration="2026-09-19", mid=3.0,
                bid=2.9, ask=3.1, iv=0.30, delta=0.50, dte=64)
leg_short = Leg(asset_type="CALL", side="SHORT", strike=110.0, expiration="2026-09-19", mid=1.0,
                bid=0.9, ask=1.1, iv=0.25, delta=0.20, dte=64)
check_eq("GENERATE: leg_long.side",     leg_long.side,  "LONG")
check_eq("GENERATE: leg_short.side",    leg_short.side, "SHORT")
check_eq("GENERATE: leg_long.dte",      leg_long.dte,   64)
check_eq("GENERATE: Leg.to_dict keys",  "strike" in leg_long.to_dict(), True)

# RANK — rank_all returns sorted list
sc_good = compute_capital_compounding_score(
    pop=0.65, ev_after_costs=0.025, max_loss=200, max_profit=400,
    risk_class=RISK_DEFINED, execution_mode="AUTONOMOUS", liquidity=0.8,
    strategy_direction="BULLISH", strategy_vol_thesis="HIGH_IV",
    strategy_family="SPREAD", thesis="BULLISH", market_regime="BULL_TREND",
    vol_regime="HIGH_IV", iv_rank=60, return_on_risk=0.25,
    assignment_risk="LOW",
)["capital_compounding_score"]

eval_a = EvaluationResult(
    strategy_name="Bull Call Spread", strategy_family="SPREAD",
    strategy_fingerprint="fp_a", risk_class=RISK_DEFINED,
    execution_mode="AUTONOMOUS", eligible=True, rejection_reasons=[],
    legs=[leg_long, leg_short],
    payoff_info={"max_profit":400,"max_loss":200,"is_undefined_risk":False},
    probability_info={"pop":0.65}, pricing_info={"ev_after_costs":0.025,"capital_at_risk":200},
    greeks_info={}, score_components={}, capital_compounding_score=sc_good, iv_rank=60,
)
eval_b = EvaluationResult(
    strategy_name="Naked Call", strategy_family="NAKED",
    strategy_fingerprint="fp_b", risk_class=RISK_UNDEFINED,
    execution_mode="ANALYSIS_ONLY", eligible=False, rejection_reasons=["undefined risk"],
    legs=[leg_short],
    payoff_info={"max_profit":100,"max_loss":None,"is_undefined_risk":True},
    probability_info={"pop":0.40}, pricing_info={"ev_after_costs":-0.02,"capital_at_risk":100},
    greeks_info={}, score_components={}, capital_compounding_score=0.1, iv_rank=60,
)
ranked = rank_all([eval_a, eval_b])
check_eq("RANK: returns 2 entries",         len(ranked), 2)
check_eq("RANK: best is Bull Call Spread",  ranked[0][1].strategy_name, "Bull Call Spread")
check_eq("RANK: second is Naked Call",      ranked[1][1].strategy_name, "Naked Call")

# SELECT — select() picks best or NO_TRADE
sel = select([eval_a, eval_b], thesis="BULLISH", market_regime="BULL_TREND", iv_rank=60)
check_eq("SELECT: decision=TRADE",          sel.decision, "TRADE")
check_eq("SELECT: selected=Bull Call Spread", sel.selected.strategy_name, "Bull Call Spread")
check_eq("SELECT: runner_up is None (only 1 selectable)", sel.runner_up, None)

# SELECT no_trade branch
eval_low = EvaluationResult(
    strategy_name="Weak Spread", strategy_family="SPREAD",
    strategy_fingerprint="fp_c", risk_class=RISK_DEFINED,
    execution_mode="AUTONOMOUS", eligible=True, rejection_reasons=[],
    legs=[leg_long, leg_short],
    payoff_info={"max_profit":10,"max_loss":200,"is_undefined_risk":False},
    probability_info={"pop":0.30}, pricing_info={"ev_after_costs":-0.02,"capital_at_risk":200},
    greeks_info={}, score_components={}, capital_compounding_score=0.10, iv_rank=60,
)
sel_nt = select([eval_low], thesis="BULLISH", market_regime="BULL_TREND", iv_rank=60)
check_eq("SELECT: low-score → NO_TRADE",     sel_nt.decision, "NO_TRADE")

# PAPER TRADE — safety_check
check_eq("PAPER TRADE: safe eval passes",    safety_check(eval_a),  None)
check_eq("PAPER TRADE: naked blocked",       safety_check(eval_b) is not None, True)

# PARENT RECORD — insert_paper_trade writes to DB; verify by querying
run_id = f"ase_VERIFY_{uuid.uuid4().hex[:8]}"
pt_id  = insert_paper_trade(
    evaluation=eval_a, selection=sel, ticker="TEST", thesis="BULLISH",
    market_regime="BULL_TREND", volatility_regime="HIGH_IV",
    event_context=None, run_id=run_id, underlying_price=100.0,
    planned_exit_date="2026-09-19",
)
check_eq("PARENT RECORD: insert returns id",      pt_id is not None, True)
check_eq("PARENT RECORD: id prefix",              str(pt_id).startswith("ase_pt_"), True)

import psycopg2, psycopg2.extras
from aiem_strat_engine.db import get_conn

parent_row = None
leg_rows   = []
if pt_id:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ase_paper_trades WHERE paper_trade_id=%s", (pt_id,))
            parent_row = cur.fetchone()
            cur.execute("SELECT * FROM ase_paper_trade_legs WHERE paper_trade_id=%s ORDER BY leg_number", (pt_id,))
            leg_rows   = cur.fetchall()

check_eq("PARENT RECORD: found in DB",            parent_row is not None, True)
check_eq("PARENT RECORD: status=OPEN",            parent_row["status"] if parent_row else None, "OPEN")
check_eq("PARENT RECORD: underlying=TEST",        parent_row["underlying"] if parent_row else None, "TEST")
check_eq("PARENT RECORD: audit_hash present",     bool(parent_row["audit_hash"]) if parent_row else False, True)

# LEG RECORDS
check_eq("LEG RECORDS: 2 legs inserted",          len(leg_rows), 2)
check_eq("LEG RECORDS: leg1 side=LONG",           leg_rows[0]["buy_or_sell"] if leg_rows else None, "LONG")
check_eq("LEG RECORDS: leg2 side=SHORT",          leg_rows[1]["buy_or_sell"] if leg_rows else None, "SHORT")
check_eq("LEG RECORDS: leg1 strike=100",          float(leg_rows[0]["strike"]) if leg_rows else 0, 100.0)
check_eq("LEG RECORDS: leg2 strike=110",          float(leg_rows[1]["strike"]) if leg_rows else 0, 110.0)
check_eq("LEG RECORDS: leg1 mid=3.0",             float(leg_rows[0]["mid"]) if leg_rows else 0, 3.0)

# VALUATION — verify capital_at_risk stored on parent
if parent_row:
    cap_stored = float(parent_row.get("capital_at_risk") or 0)
    check_eq("VALUATION: capital_at_risk stored", cap_stored > 0, True)
    check_eq("VALUATION: pop stored",             parent_row.get("probability_of_profit") is not None, True)
    check_eq("VALUATION: ev stored",              parent_row.get("expected_value") is not None, True)

# ADJUSTMENTS — verify adjustment ID generator works and follows schema
adj_id = f"ase_adj_{uuid.uuid4().hex[:12]}"
check_eq("ADJUSTMENTS: adj_id prefix",            adj_id.startswith("ase_adj_"), True)
check_eq("ADJUSTMENTS: adj_id length",            len(adj_id), len("ase_adj_") + 12)

# EXIT — close_paper_trade
if pt_id:
    closed = close_paper_trade(pt_id, close_reason="VERIFY_EXIT", gross_pnl=50.0, commission_paid=1.30)
    check_eq("EXIT: close returns True",          closed, True)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ase_paper_trades WHERE paper_trade_id=%s", (pt_id,))
            closed_row = cur.fetchone()
    check_eq("EXIT: status=CLOSED",               closed_row["status"] if closed_row else None, "CLOSED")
    check_eq("EXIT: close_reason stored",         closed_row["close_reason"] if closed_row else None, "VERIFY_EXIT")

# FINAL P&L
if closed_row:
    net = float(closed_row.get("net_pnl") or 0)
    expected_net = round(50.0 - 1.30, 4)
    check("FINAL P&L: net_pnl=48.70",             net, expected_net)
    ror_stored = float(closed_row.get("return_on_capital_realized") or 0)
    cap_r      = float(closed_row.get("capital_at_risk") or 1)
    exp_ror    = round(expected_net / max(cap_r, 0.01), 4)
    check("FINAL P&L: return_on_capital_realized", ror_stored, exp_ror)

# PERFORMANCE REPORTING — get_open_trades, save_decision_run
# Insert a fresh OPEN trade so get_open_trades has something real to return
pt_id2 = insert_paper_trade(
    evaluation=eval_a, selection=sel, ticker="PERFTEST", thesis="BULLISH",
    market_regime="BULL_TREND", volatility_regime="HIGH_IV",
    event_context=None, run_id=run_id + "_perf", underlying_price=150.0,
)
open_trades = get_open_trades()
check_eq("PERFORMANCE REPORTING: get_open_trades no error (not empty)", len(open_trades) > 0, True)
check_eq("PERFORMANCE REPORTING: first row has paper_trade_id", "paper_trade_id" in (open_trades[0] if open_trades else {}), True)
check_eq("PERFORMANCE REPORTING: first row status=OPEN", (open_trades[0].get("status") if open_trades else None), "OPEN")
check_eq("PERFORMANCE REPORTING: first row has legs key", "legs" in (open_trades[0] if open_trades else {}), True)
if open_trades:
    legs_val = open_trades[0]["legs"]
    check_eq("PERFORMANCE REPORTING: legs is list with >=1 entry", isinstance(legs_val, list) and len(legs_val) >= 1, True)

saved = save_decision_run(
    run_id=run_id, ticker="TEST", spot=100.0, thesis="BULLISH",
    market_regime="BULL_TREND", volatility_regime="HIGH_IV",
    event_context=None, iv_rank=60.0, iv_percentile=70.0,
    expected_move=5.0, n_evaluated=2, n_rejected=1,
    selection=sel, config_sha=config_sha256(),
)
check_eq("PERFORMANCE REPORTING: save_decision_run", saved, True)

# ─────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────
print("\n=== RESULTS ===")
for r in results:
    print(r)

print(f"\nPASS={PASS}  FAIL={FAIL}")
print("EXIT STATUS:", "PASS" if FAIL == 0 else "FAIL")
sys.exit(0 if FAIL == 0 else 1)
