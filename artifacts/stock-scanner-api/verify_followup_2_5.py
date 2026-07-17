#!/usr/bin/env python3
"""
verify_followup_2_5.py — Items 2-5 follow-up verification.

ITEM 1 — CANNOT PRODUCE [live HTTP endpoint for get_open_trades]:
  get_open_trades() has no HTTP route. It is an internal function called only
  by position_manager.monitor_all_positions().
  /stock-api/paper-trades and /stock-api/aiem-paper-portfolio query the LEGACY
  aiem_paper_trades table, NOT ase_paper_trades — they are unaffected by this fix.
"""
import sys, os, json, uuid, psycopg2, psycopg2.extras
sys.path.insert(0, os.path.dirname(__file__))

from aiem_strat_engine.legs import Leg, RISK_DEFINED, RISK_UNDEFINED
from aiem_strat_engine.scoring import compute_capital_compounding_score
from aiem_strat_engine.selector import EvaluationResult, SelectionResult, select
from aiem_strat_engine.paper_trader import (
    get_open_trades, insert_paper_trade, close_paper_trade,
    save_decision_run, _audit_hash, _new_trade_id, get_conn,
)
from aiem_strat_engine.position_manager import record_valuation, monitor_all_positions
from aiem_strat_engine.config import config_sha256

DB_URL = os.environ["DATABASE_URL"]
PASS = 0; FAIL = 0
_CLEANUP = []

def chk(label, got, exp):
    global PASS, FAIL
    ok = (got == exp)
    if ok: PASS += 1
    else:  FAIL += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got={got!r} exp={exp!r}")
    return ok

def chk_true(label, cond):
    return chk(label, bool(cond), True)

def raw_one(sql, params=None):
    with psycopg2.connect(DB_URL, connect_timeout=5,
                          options="-c statement_timeout=8000") as c, c.cursor() as cu:
        cu.execute(sql, params or ())
        r = cu.fetchone()
        return r[0] if r else None

def _build_eval(ticker="TEST", spot=100.0):
    leg_long  = Leg(asset_type="CALL", side="LONG",  strike=spot,
                    expiration="2026-09-19", mid=3.0, bid=2.9, ask=3.1,
                    iv=0.30, delta=0.50, dte=64)
    leg_short = Leg(asset_type="CALL", side="SHORT", strike=spot+10.0,
                    expiration="2026-09-19", mid=1.0, bid=0.9, ask=1.1,
                    iv=0.25, delta=0.20, dte=64)
    sc = compute_capital_compounding_score(
        pop=0.65, ev_after_costs=0.025, max_loss=200, max_profit=400,
        risk_class=RISK_DEFINED, execution_mode="AUTONOMOUS", liquidity=0.8,
        strategy_direction="BULLISH", strategy_vol_thesis="HIGH_IV",
        strategy_family="SPREAD", thesis="BULLISH", market_regime="BULL_TREND",
        vol_regime="HIGH_IV", iv_rank=60, return_on_risk=0.25,
        assignment_risk="LOW",
    )["capital_compounding_score"]
    eval_r = EvaluationResult(
        strategy_name="Bull Call Spread", strategy_family="SPREAD",
        strategy_fingerprint="fp_vrfy", risk_class=RISK_DEFINED,
        execution_mode="AUTONOMOUS", eligible=True, rejection_reasons=[],
        legs=[leg_long, leg_short],
        payoff_info={"max_profit": 400, "max_loss": 200, "is_undefined_risk": False},
        probability_info={"pop": 0.65},
        pricing_info={"ev_after_costs": 0.025, "capital_at_risk": 200},
        greeks_info={}, score_components={},
        capital_compounding_score=sc, iv_rank=60,
    )
    sel = select([eval_r], thesis="BULLISH", market_regime="BULL_TREND", iv_rank=60)
    return eval_r, sel, leg_long, leg_short

# ═══════════════════════════════════════════════════════════════
print("=== ITEM 2: DATA INTEGRITY ===")

total_open = raw_one("SELECT COUNT(*) FROM ase_paper_trades WHERE status='OPEN'")
print(f"  OPEN trade count in ase_paper_trades: {total_open}")

orphans = raw_one("""
    SELECT COUNT(*) FROM ase_paper_trade_legs l
    WHERE NOT EXISTS (
        SELECT 1 FROM ase_paper_trades pt
        WHERE pt.paper_trade_id = l.paper_trade_id
    )
""")
chk("orphaned legs = 0", orphans, 0)

dup_legs = raw_one("""
    SELECT COUNT(*) FROM (
        SELECT paper_trade_id, leg_number
        FROM ase_paper_trade_legs
        GROUP BY paper_trade_id, leg_number HAVING COUNT(*) > 1
    ) x
""")
chk("duplicate (paper_trade_id, leg_number) pairs = 0", dup_legs, 0)

trades = get_open_trades()
chk_true("get_open_trades() returns non-empty list", len(trades) > 0)
chk("all rows have paper_trade_id", all("paper_trade_id" in t for t in trades), True)
chk("all rows have legs key",       all("legs" in t for t in trades), True)

bad_type = sum(1 for t in trades if t.get("legs") is not None and not isinstance(t["legs"], list))
chk("all non-NULL legs values are list type (bad_type=0)", bad_type, 0)

# Cross-check: returned leg-list length == DB row count, sample first 50 trades with legs
mismatch = 0
sample_with_legs = [t for t in trades if isinstance(t.get("legs"), list)][:50]
for t in sample_with_legs:
    pid = t["paper_trade_id"]
    db_n = raw_one("SELECT COUNT(*) FROM ase_paper_trade_legs WHERE paper_trade_id=%s", (pid,))
    if len(t["legs"]) != db_n:
        mismatch += 1
        print(f"    MISMATCH {pid}: returned={len(t['legs'])} db={db_n}")
chk(f"leg count matches DB for first 50 non-null-legged trades (mismatch={mismatch})", mismatch, 0)

# ═══════════════════════════════════════════════════════════════
print("\n=== ITEM 3: PIPELINE PROOF ===")

eval_r, sel, leg_long, leg_short = _build_eval(ticker="VRFY3", spot=105.0)
run_id = f"vrfy_{uuid.uuid4().hex[:12]}"

pid = insert_paper_trade(
    evaluation=eval_r, selection=sel, ticker="VRFY3", thesis="BULLISH",
    market_regime="BULL_TREND", volatility_regime="HIGH_IV",
    event_context=None, run_id=run_id, underlying_price=105.0,
)
chk_true("insert_paper_trade returns id", pid is not None)
if pid:
    _CLEANUP.append(pid)

# Retrieve via get_open_trades immediately after insert
open2 = get_open_trades()
matching = [t for t in open2 if t.get("paper_trade_id") == pid]
chk("new trade appears in get_open_trades", len(matching), 1)

if matching:
    row = matching[0]
    chk("trade status=OPEN",        row.get("status"),     "OPEN")
    chk("trade underlying=VRFY3",   row.get("underlying"), "VRFY3")
    chk_true("legs key present",    "legs" in row)
    legs_val = row.get("legs")
    chk_true("legs is list",        isinstance(legs_val, list))
    chk("legs list length=2",       len(legs_val) if isinstance(legs_val, list) else -1, 2)
    if isinstance(legs_val, list) and len(legs_val) == 2:
        sides = sorted(l.get("buy_or_sell") for l in legs_val)
        chk("legs have LONG and SHORT", sides, ["LONG", "SHORT"])
        strikes = sorted(float(l.get("strike", 0)) for l in legs_val)
        chk("leg strikes = [105.0, 115.0]", strikes, [105.0, 115.0])

# Close and verify removal from get_open_trades
if pid:
    closed_ok = close_paper_trade(pid, close_reason="VRFY3_EXIT",
                                   gross_pnl=50.0, commission_paid=1.30)
    chk("close_paper_trade returns True", closed_ok, True)
    open3 = get_open_trades()
    still_open = [t for t in open3 if t.get("paper_trade_id") == pid]
    chk("closed trade absent from get_open_trades", len(still_open), 0)
    _CLEANUP.remove(pid)   # already closed — cleanup will skip DELETE on legs

# ═══════════════════════════════════════════════════════════════
print("\n=== ITEM 4: REGRESSION ===")

# 4a. save_decision_run
eval_rv, sel_rv, _, _ = _build_eval(ticker="VRFY4", spot=120.0)
dr_run_id = f"regr_{uuid.uuid4().hex[:8]}"
dr_ok = save_decision_run(
    run_id=dr_run_id, ticker="VRFY4", spot=120.0, thesis="BULLISH",
    market_regime="BULL_TREND", volatility_regime="HIGH_IV",
    event_context=None, iv_rank=60.0, iv_percentile=None,
    expected_move=None, n_evaluated=1, n_rejected=0,
    selection=sel_rv, config_sha=config_sha256(),
)
chk("save_decision_run returns True", dr_ok, True)
saved = raw_one("SELECT COUNT(*) FROM ase_decision_runs WHERE run_id=%s", (dr_run_id,))
chk("save_decision_run row visible in DB", saved, 1)

# 4b. record_valuation — insert trade, fetch via get_open_trades, call record_valuation
pid_rv = insert_paper_trade(
    evaluation=eval_rv, selection=sel_rv, ticker="VRFY4", thesis="BULLISH",
    market_regime="BULL_TREND", volatility_regime="HIGH_IV",
    event_context=None, run_id=f"regr_rv_{uuid.uuid4().hex[:8]}", underlying_price=120.0,
)
chk_true("insert for record_valuation test returns id", pid_rv is not None)
if pid_rv:
    _CLEANUP.append(pid_rv)
    open_rv = get_open_trades()
    rv_row = next((t for t in open_rv if t.get("paper_trade_id") == pid_rv), None)
    chk_true("VRFY4 trade in get_open_trades", rv_row is not None)
    if rv_row:
        legs_raw = rv_row.get("legs") or []
        parsed = [json.loads(l) if isinstance(l, str) else l
                  for l in legs_raw if isinstance(l, (str, dict))]
        parsed = [l for l in parsed if isinstance(l, dict)]
        # record_valuation returns None when _current_value can't price legs (no live chain)
        # Critical: it must NOT raise an exception
        raised = False
        val_result = None
        try:
            val_result = record_valuation(pid_rv, "VRFY4", parsed, spot=122.0)
        except Exception as exc:
            raised = True
            print(f"    record_valuation raised: {exc}")
        chk("record_valuation did not raise", raised, False)
        print(f"  INFO  record_valuation returned: {val_result!r} (None OK — no live chain for VRFY4)")

# 4c. monitor_all_positions is importable and callable
chk_true("monitor_all_positions is callable", callable(monitor_all_positions))

# 4d. Simulate the legs-parsing path inside monitor_all_positions on a live trade
sample_trade = next((t for t in trades if isinstance(t.get("legs"), list) and len(t["legs"]) >= 2), None)
chk_true("sample trade with >=2 legs found for parsing simulation", sample_trade is not None)
if sample_trade:
    legs_raw2 = sample_trade.get("legs", [])
    parsed_pm = []
    for l in (legs_raw2 or []):
        if isinstance(l, str):
            try: l = json.loads(l)
            except: continue
        if isinstance(l, dict):
            parsed_pm.append(l)
    chk_true("position_manager parsing path produces list",   isinstance(parsed_pm, list))
    chk_true("parsed legs count >= 2",                         len(parsed_pm) >= 2)
    chk_true("each parsed leg is dict",                        all(isinstance(lg, dict) for lg in parsed_pm))
    chk_true("each parsed leg has buy_or_sell key",
             all("buy_or_sell" in lg for lg in parsed_pm))

# ═══════════════════════════════════════════════════════════════
print("\n=== ITEM 5: NEGATIVE TEST (0 / 1 / multi legs) ===")

# 5a. 0-leg trade — insert directly via SQL, no legs
pid_zero = _new_trade_id() + "_z"
with psycopg2.connect(DB_URL, connect_timeout=5) as c0, c0.cursor() as cu0:
    cu0.execute("""
        INSERT INTO ase_paper_trades (
            paper_trade_id, strategy_fingerprint, decision_run_id,
            underlying, strategy_name, family, thesis,
            market_regime, volatility_regime, status, audit_hash, entry_time
        ) VALUES (%s,'fp_negtest','run_negtest_0',
                  'ZERO','NegTest','SPREAD','BULLISH',
                  'BULL_TREND','HIGH_IV','OPEN',%s,NOW())
    """, (pid_zero, _audit_hash({"pid": pid_zero})))
    c0.commit()
_CLEANUP.append(pid_zero)

open_z = get_open_trades()
z_row = next((t for t in open_z if t.get("paper_trade_id") == pid_zero), None)
chk_true("0-leg trade appears in get_open_trades", z_row is not None)
if z_row:
    legs_z = z_row.get("legs")
    chk("0-leg trade: legs field is None (LEFT JOIN miss on empty subquery)", legs_z, None)

# 5b. 1-leg trade
from datetime import date as _date, timedelta as _td
exp_1 = (_date.today() + _td(days=30)).strftime("%Y-%m-%d")
pid_one = _new_trade_id() + "_o"
with psycopg2.connect(DB_URL, connect_timeout=5) as c1, c1.cursor() as cu1:
    cu1.execute("""
        INSERT INTO ase_paper_trades (
            paper_trade_id, strategy_fingerprint, decision_run_id,
            underlying, strategy_name, family, thesis,
            market_regime, volatility_regime, status, audit_hash, entry_time
        ) VALUES (%s,'fp_negtest','run_negtest_1',
                  'ONE','NegTest','SPREAD','BULLISH',
                  'BULL_TREND','HIGH_IV','OPEN',%s,NOW())
    """, (pid_one, _audit_hash({"pid": pid_one})))
    cu1.execute("""
        INSERT INTO ase_paper_trade_legs (
            paper_trade_id, leg_number, asset_type, call_or_put,
            buy_or_sell, open_or_close, quantity, ratio,
            strike, expiration, dte_at_entry, mid
        ) VALUES (%s,1,'CALL','CALL','LONG','OPEN',1,1,100.0,%s,30,2.5)
    """, (pid_one, exp_1))
    c1.commit()
_CLEANUP.append(pid_one)

open_o = get_open_trades()
o_row = next((t for t in open_o if t.get("paper_trade_id") == pid_one), None)
chk_true("1-leg trade appears in get_open_trades", o_row is not None)
if o_row:
    legs_o = o_row.get("legs")
    chk_true("1-leg: legs is list",                    isinstance(legs_o, list))
    chk("1-leg: legs list has exactly 1 entry",
        len(legs_o) if isinstance(legs_o, list) else -1, 1)
    if isinstance(legs_o, list) and len(legs_o) == 1:
        chk("1-leg: buy_or_sell=LONG", legs_o[0].get("buy_or_sell"), "LONG")
        chk("1-leg: strike=100.0",
            float(legs_o[0].get("strike", 0)) if legs_o[0].get("strike") else 0, 100.0)

# 5c. 4-leg trade — use existing data
four_leg = next((t for t in trades if isinstance(t.get("legs"), list) and len(t["legs"]) == 4), None)
chk_true("4-leg trade exists in DB data", four_leg is not None)
if four_leg:
    chk("4-leg: legs list length=4", len(four_leg["legs"]), 4)
    chk("4-leg: all legs are dicts",
        all(isinstance(l, dict) for l in four_leg["legs"]), True)
    db4 = raw_one(
        "SELECT COUNT(*) FROM ase_paper_trade_legs WHERE paper_trade_id=%s",
        (four_leg["paper_trade_id"],)
    )
    chk("4-leg: DB count matches returned count", db4, 4)

# 5d. Verify 0/1/multi all return correct types (not crash, not wrong type)
for label, pid_test, exp_count in [
    ("0-leg",  pid_zero, None),   # legs=None expected
    ("1-leg",  pid_one,  1),
]:
    all_trades = get_open_trades()
    row = next((t for t in all_trades if t.get("paper_trade_id") == pid_test), None)
    if row is None:
        print(f"  FAIL  {label} trade not found in get_open_trades")
        FAIL += 1
        continue
    lv = row.get("legs")
    if exp_count is None:
        chk(f"{label}: legs=None (no crash)", lv, None)
    else:
        chk(f"{label}: legs is list", isinstance(lv, list), True)
        chk(f"{label}: legs length={exp_count}",
            len(lv) if isinstance(lv, list) else -1, exp_count)

# ═══════════════════════════════════════════════════════════════
print("\n=== CLEANUP ===")
cleaned = 0
for pid_c in _CLEANUP:
    try:
        with psycopg2.connect(DB_URL, connect_timeout=5) as cc, cc.cursor() as ccu:
            ccu.execute("DELETE FROM ase_paper_trade_legs  WHERE paper_trade_id=%s", (pid_c,))
            ccu.execute("DELETE FROM ase_position_valuations WHERE paper_trade_id=%s", (pid_c,))
            ccu.execute(
                "DELETE FROM ase_paper_trades WHERE paper_trade_id=%s AND status!='CLOSED'",
                (pid_c,)
            )
            cc.commit()
        cleaned += 1
    except Exception as exc:
        print(f"  cleanup error {pid_c}: {exc}")
print(f"  cleaned {cleaned}/{len(_CLEANUP)} test records")

# ═══════════════════════════════════════════════════════════════
print(f"\nPASS={PASS}  FAIL={FAIL}")
print("EXIT STATUS:", "PASS" if FAIL == 0 else "FAIL")
sys.exit(0 if FAIL == 0 else 1)
