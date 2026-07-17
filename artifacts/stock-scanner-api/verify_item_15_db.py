"""
verify_item_15_db.py — Item 15: DATABASE VERIFICATION
Covers: Parent Trade, Every Leg, Greeks, Pricing, Valuation Updates,
        Adjustments, Exit, P/L, Performance Summary, FK, No-Dup, No-Orphan,
        Idempotency, Transaction Rollback.
"""
import os, sys, json
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

import psycopg2, psycopg2.extras
from datetime import date, timedelta

from aiem_strat_engine.legs import Leg, MODE_AUTONOMOUS
from aiem_strat_engine.selector import EvaluationResult, SelectionResult
from aiem_strat_engine.paper_trader import (
    insert_paper_trade, close_paper_trade, get_open_trades,
    save_decision_run, _new_trade_id, _new_run_id, _audit_hash,
)
from aiem_strat_engine.position_manager import record_adjustment
from aiem_strat_engine.config import config_sha256
from aiem_strat_engine.db import get_conn
from aiem_strat_engine.reporting import generate_report, verify_report_integrity

DB_URL = os.environ["DATABASE_URL"]
PASS = 0; FAIL = 0
_CLEANUP_PIDS = []
_CLEANUP_RIDS = []

def chk(label, got, exp):
    global PASS, FAIL
    if got == exp:
        print(f"  PASS  {label}: got={got!r}")
        PASS += 1
    else:
        print(f"  FAIL  {label}: got={got!r} exp={exp!r}")
        FAIL += 1

def chk_true(label, val):
    global PASS, FAIL
    if val:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}")
        FAIL += 1

def chk_raises(label, exc_type, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  FAIL  {label}: no exception raised")
        FAIL += 1
    except exc_type as e:
        print(f"  PASS  {label}: raised {exc_type.__name__}: {str(e)[:60]}")
        PASS += 1
    except Exception as e:
        print(f"  FAIL  {label}: wrong exception {type(e).__name__}: {e}")
        FAIL += 1

def raw_one(sql, params=None):
    with psycopg2.connect(DB_URL, connect_timeout=5) as c, c.cursor() as cu:
        cu.execute(sql, params or ())
        r = cu.fetchone()
        return r[0] if r else None

def raw_row(sql, params=None):
    with psycopg2.connect(DB_URL, connect_timeout=5) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cu:
            cu.execute(sql, params or ())
            return cu.fetchone()

def raw_rows(sql, params=None):
    with psycopg2.connect(DB_URL, connect_timeout=5) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cu:
            cu.execute(sql, params or ())
            return cu.fetchall()

def make_evaluation(ticker="VRY15", strike_long=100.0, strike_short=110.0, fp_suffix=""):
    exp = (date.today() + timedelta(days=45)).strftime("%Y-%m-%d")
    leg_long = Leg(
        asset_type="CALL", side="LONG", quantity=1, ratio=1,
        strike=strike_long, expiration=exp, dte=45,
        option_symbol=f"{ticker}C{int(strike_long)}",
        bid=3.80, ask=4.20, mid=4.00,
        iv=0.32, delta=0.52, gamma=0.025, theta=-0.09, vega=0.18, rho=0.06,
        volume=850, open_interest=3200, data_provider="tradier",
    )
    leg_short = Leg(
        asset_type="CALL", side="SHORT", quantity=1, ratio=1,
        strike=strike_short, expiration=exp, dte=45,
        option_symbol=f"{ticker}C{int(strike_short)}",
        bid=1.40, ask=1.60, mid=1.50,
        iv=0.28, delta=0.28, gamma=0.015, theta=-0.05, vega=0.12, rho=0.03,
        volume=420, open_interest=1800, data_provider="tradier",
    )
    return EvaluationResult(
        strategy_name="Bull Call Spread",
        strategy_family="SPREAD",
        strategy_fingerprint=f"fp_db15_{fp_suffix}",
        risk_class="DEFINED",
        execution_mode=MODE_AUTONOMOUS,
        eligible=True,
        rejection_reasons=[],
        legs=[leg_long, leg_short],
        payoff_info={
            "max_profit": 600.0, "max_loss": 400.0,
            "is_undefined_risk": False,
        },
        probability_info={"pop": 0.61},
        pricing_info={
            "capital_at_risk": 400.0, "buying_power": 400.0,
            "ev_after_costs": 110.0, "liquidity_score": 0.88,
            "return_on_risk": 1.50,
            "leg_1_fill": 4.00, "leg_2_fill": -1.50,
        },
        greeks_info={"delta": 0.24, "gamma": 0.01, "theta": -0.04, "vega": 0.06},
        score_components={},
        capital_compounding_score=74.0,
    )

def make_selection(ev):
    return SelectionResult(
        decision="TRADE", selected=ev, runner_up=None,
        no_trade_score_=22.0, all_evaluations=[ev], reason="Top score",
    )

# ═══════════════════════════════════════════════════════════════
# BUILD FULL LIFECYCLE TEST TRADE
# ═══════════════════════════════════════════════════════════════
print("=== ITEM 15: DATABASE VERIFICATION ===")

ev = make_evaluation()
sel = make_selection(ev)
run_id = _new_run_id("VRY15", "BULLISH")

save_ok = save_decision_run(
    run_id=run_id, ticker="VRY15", spot=105.0, thesis="BULLISH",
    market_regime="BULL_TREND", volatility_regime="HIGH_IV",
    event_context=None, iv_rank=72.0, iv_percentile=68.0, expected_move=4.5,
    n_evaluated=5, n_rejected=4, selection=sel, config_sha=config_sha256(),
)
chk_true("save_decision_run for lifecycle run", save_ok)
_CLEANUP_RIDS.append(run_id)

pid = insert_paper_trade(
    evaluation=ev, selection=sel, ticker="VRY15", thesis="BULLISH",
    market_regime="BULL_TREND", volatility_regime="HIGH_IV",
    event_context=None, run_id=run_id, underlying_price=105.0,
    planned_exit_date=(date.today() + timedelta(days=30)).strftime("%Y-%m-%d"),
)
chk_true("insert_paper_trade returned id", bool(pid))
_CLEANUP_PIDS.append(pid)

# ── SQL PROOF: Parent Trade ──────────────────────────────────────
print("\n--- SQL: Parent Trade ---")
parent = raw_row(
    "SELECT paper_trade_id, underlying, strategy_name, family, thesis, "
    "status, probability_of_profit, maximum_profit, maximum_loss, "
    "capital_at_risk, selected_score, audit_hash "
    "FROM ase_paper_trades WHERE paper_trade_id=%s", (pid,)
)
chk_true("parent row exists", parent is not None)
chk("parent.underlying", parent["underlying"], "VRY15")
chk("parent.strategy_name", parent["strategy_name"], "Bull Call Spread")
chk("parent.status", parent["status"], "OPEN")
chk("parent.family", parent["family"], "SPREAD")
chk("parent.thesis", parent["thesis"], "BULLISH")
chk_true("parent.audit_hash non-empty", bool(parent["audit_hash"]))
print(f"  INFO  paper_trade_id={pid}")
print(f"  INFO  probability_of_profit={parent['probability_of_profit']}")
print(f"  INFO  maximum_profit={parent['maximum_profit']}  maximum_loss={parent['maximum_loss']}")
print(f"  INFO  capital_at_risk={parent['capital_at_risk']}")

# ── SQL PROOF: Every Leg ─────────────────────────────────────────
print("\n--- SQL: Every Leg ---")
legs = raw_rows(
    "SELECT leg_number, asset_type, call_or_put, buy_or_sell, "
    "quantity, ratio, strike, expiration, dte_at_entry, "
    "bid, ask, mid, modeled_fill, paper_fill "
    "FROM ase_paper_trade_legs WHERE paper_trade_id=%s ORDER BY leg_number",
    (pid,)
)
chk("leg count", len(legs), 2)
chk("leg1.buy_or_sell", legs[0]["buy_or_sell"], "LONG")
chk("leg2.buy_or_sell", legs[1]["buy_or_sell"], "SHORT")
chk("leg1.strike", float(legs[0]["strike"]), 100.0)
chk("leg2.strike", float(legs[1]["strike"]), 110.0)
for i, lg in enumerate(legs, 1):
    print(f"  INFO  leg{i}: {lg['buy_or_sell']} {lg['call_or_put']} "
          f"K={lg['strike']} exp={lg['expiration']} dte={lg['dte_at_entry']}")

# ── SQL PROOF: Greeks ────────────────────────────────────────────
print("\n--- SQL: Greeks ---")
greeks = raw_rows(
    "SELECT leg_number, delta, gamma, theta, vega, rho, iv "
    "FROM ase_paper_trade_legs WHERE paper_trade_id=%s ORDER BY leg_number",
    (pid,)
)
for g in greeks:
    chk_true(f"leg{g['leg_number']}.delta non-null", g["delta"] is not None)
    chk_true(f"leg{g['leg_number']}.gamma non-null", g["gamma"] is not None)
    chk_true(f"leg{g['leg_number']}.theta non-null", g["theta"] is not None)
    chk_true(f"leg{g['leg_number']}.vega non-null", g["vega"] is not None)
    chk_true(f"leg{g['leg_number']}.iv non-null", g["iv"] is not None)
    print(f"  INFO  leg{g['leg_number']}: Δ={g['delta']} Γ={g['gamma']} Θ={g['theta']} ν={g['vega']} ρ={g['rho']}")

# ── SQL PROOF: Pricing ───────────────────────────────────────────
print("\n--- SQL: Pricing ---")
pricing = raw_rows(
    "SELECT leg_number, bid, ask, mid, modeled_fill, paper_fill, data_provider "
    "FROM ase_paper_trade_legs WHERE paper_trade_id=%s ORDER BY leg_number",
    (pid,)
)
for p in pricing:
    chk_true(f"leg{p['leg_number']}.bid non-null", p["bid"] is not None)
    chk_true(f"leg{p['leg_number']}.ask non-null", p["ask"] is not None)
    chk_true(f"leg{p['leg_number']}.mid non-null", p["mid"] is not None)
    chk_true(f"leg{p['leg_number']}.modeled_fill non-null", p["modeled_fill"] is not None)
    chk_true(f"leg{p['leg_number']}.paper_fill non-null", p["paper_fill"] is not None)
    print(f"  INFO  leg{p['leg_number']}: bid={p['bid']} ask={p['ask']} mid={p['mid']} fill={p['modeled_fill']}")

# ── SQL PROOF: Valuation Updates ─────────────────────────────────
print("\n--- SQL: Valuation Updates (direct INSERT — no live chain for VRY15) ---")
today = date.today()
with psycopg2.connect(DB_URL, connect_timeout=5) as vc, vc.cursor() as vcu:
    vcu.execute("""
        INSERT INTO ase_position_valuations (
            paper_trade_id, valuation_date, underlying_price,
            paper_value, unrealized_pnl, delta, gamma, theta, vega
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (paper_trade_id, valuation_date) DO UPDATE
        SET paper_value=EXCLUDED.paper_value, unrealized_pnl=EXCLUDED.unrealized_pnl
    """, (pid, today, 108.5, 2.80, 280.0, 0.25, 0.01, -0.04, 0.07))
    vc.commit()
val = raw_row(
    "SELECT paper_trade_id, valuation_date, underlying_price, paper_value, "
    "unrealized_pnl, delta, gamma, theta, vega "
    "FROM ase_position_valuations WHERE paper_trade_id=%s",
    (pid,)
)
chk_true("valuation row exists", val is not None)
chk("val.underlying_price", float(val["underlying_price"]), 108.5)
chk("val.paper_value", float(val["paper_value"]), 2.80)
chk("val.unrealized_pnl", float(val["unrealized_pnl"]), 280.0)
print(f"  INFO  valuation: spot={val['underlying_price']} paper_value={val['paper_value']} upnl={val['unrealized_pnl']}")

# Idempotency: ON CONFLICT DO UPDATE re-applies same values
with psycopg2.connect(DB_URL, connect_timeout=5) as vc2, vc2.cursor() as vcu2:
    vcu2.execute("""
        INSERT INTO ase_position_valuations (
            paper_trade_id, valuation_date, underlying_price, paper_value, unrealized_pnl
        ) VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (paper_trade_id, valuation_date) DO UPDATE
        SET paper_value=EXCLUDED.paper_value, unrealized_pnl=EXCLUDED.unrealized_pnl
    """, (pid, today, 109.0, 2.90, 290.0))
    vc2.commit()
val2 = raw_row(
    "SELECT unrealized_pnl FROM ase_position_valuations WHERE paper_trade_id=%s", (pid,)
)
chk("valuation idempotency: upsert updates in-place", float(val2["unrealized_pnl"]), 290.0)

vc_count = raw_one(
    "SELECT COUNT(*) FROM ase_position_valuations WHERE paper_trade_id=%s", (pid,)
)
chk("valuation idempotency: exactly 1 row per (pid, date)", vc_count, 1)

# ── SQL PROOF: Adjustments ───────────────────────────────────────
print("\n--- SQL: Adjustments ---")
leg_dicts = [{"leg_number": l["leg_number"], "buy_or_sell": l["buy_or_sell"],
              "strike": float(l["strike"]), "call_or_put": l["call_or_put"]}
             for l in legs]
adj_id = record_adjustment(
    paper_trade_id=pid,
    adjustment_type="ROLL_OUT",
    reason="DTE approaching minimum; rolling to next expiry",
    legs_closed=leg_dicts,
    legs_opened=[{**leg_dicts[1], "expiration": str(date.today() + timedelta(days=75))}],
    net_cost=0.30,
)
chk_true("record_adjustment returned adj_id", bool(adj_id))
adj_row = raw_row(
    "SELECT adjustment_id, paper_trade_id, adjustment_type, reason, net_cost "
    "FROM ase_adjustments WHERE adjustment_id=%s", (adj_id,)
)
chk_true("adjustment row exists in DB", adj_row is not None)
chk("adj.paper_trade_id FK", adj_row["paper_trade_id"], pid)
chk("adj.adjustment_type", adj_row["adjustment_type"], "ROLL_OUT")
chk("adj.net_cost", float(adj_row["net_cost"]), 0.30)
print(f"  INFO  adjustment_id={adj_id}")

# ── SQL PROOF: Exit & P/L ────────────────────────────────────────
print("\n--- SQL: Exit + P/L ---")
gross_pnl = 185.0
commission = 2.60    # 2 legs × 2 sides × $0.65
close_ok = close_paper_trade(pid, "PROFIT_TARGET", gross_pnl, commission)
chk("close_paper_trade returned True", close_ok, True)
closed = raw_row(
    "SELECT status, close_reason, gross_pnl, net_pnl, commission_paid, "
    "return_on_capital_realized, holding_period_days "
    "FROM ase_paper_trades WHERE paper_trade_id=%s", (pid,)
)
chk("exit.status", closed["status"], "CLOSED")
chk("exit.close_reason", closed["close_reason"], "PROFIT_TARGET")
chk("exit.gross_pnl", float(closed["gross_pnl"]), gross_pnl)
net_exp = round(gross_pnl - commission, 4)
chk("exit.net_pnl = gross - commission", float(closed["net_pnl"]), net_exp)
chk_true("exit.return_on_capital_realized non-null", closed["return_on_capital_realized"] is not None)
print(f"  INFO  gross={closed['gross_pnl']} net={closed['net_pnl']} commission={closed['commission_paid']}")
print(f"  INFO  return_on_capital_realized={closed['return_on_capital_realized']}")

# ── SQL PROOF: Performance Summary ──────────────────────────────
print("\n--- SQL: Performance Summary (generate_report for today) ---")
rpt = generate_report("DAILY", today, today)
chk_true("generate_report returned dict", isinstance(rpt, dict))
chk_true("report has win_rate", "win_rate" in rpt)
chk_true("report has equity_curve", "equity_curve" in rpt)
chk_true("report has net_pnl_paper", "net_pnl_paper" in rpt)
chk_true("report has net_pnl_theoretical", "net_pnl_theoretical" in rpt)
chk_true("report has net_pnl_modeled", "net_pnl_modeled" in rpt)
chk_true("report has report_sha256", "report_sha256" in rpt)
print(f"  INFO  report_id={rpt.get('report_id','')}  (SHA-256 integrity verified separately in Item 17)")

# ═══════════════════════════════════════════════════════════════
# FK CONSTRAINTS
# ═══════════════════════════════════════════════════════════════
print("\n--- SQL: Foreign Key Constraints ---")

# FK violation: insert leg with non-existent parent
def _fk_violation():
    with psycopg2.connect(DB_URL, connect_timeout=5) as cx, cx.cursor() as cu:
        cu.execute("""
            INSERT INTO ase_paper_trade_legs
            (paper_trade_id, leg_number, asset_type, buy_or_sell,
             open_or_close, quantity, ratio)
            VALUES ('NONEXISTENT_PARENT_XYZ', 1, 'CALL', 'LONG', 'OPEN', 1, 1)
        """)
        cx.commit()

chk_raises("FK violation on non-existent parent", psycopg2.errors.ForeignKeyViolation, _fk_violation)

# FK violation: insert adjustment with non-existent parent
def _adj_fk_violation():
    with psycopg2.connect(DB_URL, connect_timeout=5) as cx, cx.cursor() as cu:
        cu.execute("""
            INSERT INTO ase_adjustments
            (adjustment_id, paper_trade_id, adjustment_type, reason, legs_closed, legs_opened)
            VALUES ('adj_orphan_xyz', 'NONEXISTENT_XYZ', 'ROLL_OUT', 'test', '[]', '[]')
        """)
        cx.commit()

chk_raises("FK violation on adjustment orphan parent", psycopg2.errors.ForeignKeyViolation, _adj_fk_violation)

# FK violation: insert valuation with non-existent parent
def _val_fk_violation():
    with psycopg2.connect(DB_URL, connect_timeout=5) as cx, cx.cursor() as cu:
        cu.execute("""
            INSERT INTO ase_position_valuations
            (paper_trade_id, valuation_date)
            VALUES ('NONEXISTENT_XYZ', CURRENT_DATE)
        """)
        cx.commit()

chk_raises("FK violation on valuation orphan parent", psycopg2.errors.ForeignKeyViolation, _val_fk_violation)

# ── No Dup Parents ───────────────────────────────────────────────
print("\n--- SQL: No Duplicate Parent Trades ---")
dup_parents = raw_one("""
    SELECT COUNT(*) FROM (
        SELECT paper_trade_id FROM ase_paper_trades
        GROUP BY paper_trade_id HAVING COUNT(*) > 1
    ) x
""")
chk("no duplicate paper_trade_id values", dup_parents, 0)

def _dup_parent_violation():
    with psycopg2.connect(DB_URL, connect_timeout=5) as cx, cx.cursor() as cu:
        cu.execute("""
            INSERT INTO ase_paper_trades (
                paper_trade_id, strategy_fingerprint, decision_run_id,
                underlying, strategy_name, family, thesis,
                market_regime, volatility_regime, status, audit_hash, entry_time
            ) VALUES (%s,'fp_dup','run_dup','DUPTEST','Bull Call Spread','SPREAD',
                      'BULLISH','BULL_TREND','HIGH_IV','OPEN','hash_dup',NOW())
        """, (pid,))
        cx.commit()

chk_raises("UNIQUE violation on duplicate paper_trade_id", psycopg2.errors.UniqueViolation, _dup_parent_violation)

# ── No Orphan Legs ───────────────────────────────────────────────
print("\n--- SQL: No Orphan Legs ---")
orphan_legs = raw_one("""
    SELECT COUNT(*) FROM ase_paper_trade_legs pl
    LEFT JOIN ase_paper_trades pt ON pt.paper_trade_id = pl.paper_trade_id
    WHERE pt.paper_trade_id IS NULL
""")
chk("no orphan legs in DB", orphan_legs, 0)

# ── Idempotency: save_decision_run ON CONFLICT ───────────────────
print("\n--- SQL: Idempotency ---")
save_ok2 = save_decision_run(
    run_id=run_id, ticker="VRY15", spot=105.5, thesis="BULLISH",
    market_regime="BULL_TREND", volatility_regime="HIGH_IV",
    event_context=None, iv_rank=72.5, iv_percentile=68.5, expected_move=4.6,
    n_evaluated=5, n_rejected=4, selection=sel, config_sha=config_sha256(),
)
chk("save_decision_run idempotent (ON CONFLICT DO UPDATE)", save_ok2, True)
run_count = raw_one("SELECT COUNT(*) FROM ase_decision_runs WHERE run_id=%s", (run_id,))
chk("idempotency: exactly 1 row for run_id after double-save", run_count, 1)

# ── Transaction Rollback ─────────────────────────────────────────
print("\n--- SQL: Transaction Rollback ---")
rollback_pid = _new_trade_id()
try:
    with psycopg2.connect(DB_URL, connect_timeout=5) as cx, cx.cursor() as cu:
        cu.execute("""
            INSERT INTO ase_paper_trades (
                paper_trade_id, strategy_fingerprint, decision_run_id,
                underlying, strategy_name, family, thesis,
                market_regime, volatility_regime, status, audit_hash, entry_time
            ) VALUES (%s,'fp_rollback','run_rollback','RBTEST',
                      'Bull Call Spread','SPREAD','BULLISH',
                      'BULL_TREND','HIGH_IV','OPEN',%s,NOW())
        """, (rollback_pid, _audit_hash({"pid": rollback_pid})))
        # Simulate mid-transaction error before commit
        raise RuntimeError("injected_rollback_error")
        cx.commit()   # never reached
except RuntimeError:
    pass  # connection context manager auto-rollbacks on exception

rolled_back = raw_one(
    "SELECT COUNT(*) FROM ase_paper_trades WHERE paper_trade_id=%s", (rollback_pid,)
)
chk("transaction rollback: parent not persisted after exception", rolled_back, 0)

# ═══════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════
print("\n--- Cleanup ---")
with psycopg2.connect(DB_URL, connect_timeout=5) as cx, cx.cursor() as cu:
    for p in _CLEANUP_PIDS:
        cu.execute("DELETE FROM ase_position_valuations WHERE paper_trade_id=%s", (p,))
        cu.execute("DELETE FROM ase_adjustments WHERE paper_trade_id=%s", (p,))
        cu.execute("DELETE FROM ase_paper_trade_legs WHERE paper_trade_id=%s", (p,))
        cu.execute("DELETE FROM ase_paper_trades WHERE paper_trade_id=%s", (p,))
    for r in _CLEANUP_RIDS:
        cu.execute("DELETE FROM ase_decision_runs WHERE run_id=%s", (r,))
    cx.commit()
print(f"  cleaned {len(_CLEANUP_PIDS)} trade(s) + {len(_CLEANUP_RIDS)} run(s)")

print(f"\nPASS={PASS}  FAIL={FAIL}")
if FAIL > 0:
    print("EXIT STATUS: FAIL")
    sys.exit(1)
print("EXIT STATUS: PASS")
sys.exit(0)
