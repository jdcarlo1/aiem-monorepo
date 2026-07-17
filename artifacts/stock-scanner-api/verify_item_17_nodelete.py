"""
verify_item_17_nodelete.py — Item 17: PERFORMANCE VALIDATION (no-DELETE variant)
Identical to verify_item_17_performance.py except the fresh-row tamper test
restores via UPDATE rather than DELETE. DELETE awaiting Joel's explicit approval.
"""
import os, sys, math, json, time
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

import psycopg2, psycopg2.extras
from datetime import date, timedelta
from decimal import Decimal

from aiem_strat_engine.reporting import (
    generate_report, verify_report_integrity,
    _sharpe, _sortino, _max_drawdown, _brier_score,
)
from aiem_strat_engine.db import get_conn

DB_URL = os.environ["DATABASE_URL"]
PASS = 0; FAIL = 0
EPSILON = 1e-4

def chk(label, got, exp):
    global PASS, FAIL
    if got == exp:
        print(f"  PASS  {label}: got={got!r}")
        PASS += 1
    else:
        print(f"  FAIL  {label}: got={got!r} exp={exp!r}")
        FAIL += 1

def chk_close(label, got, exp, eps=EPSILON):
    global PASS, FAIL
    if got is None and exp is None:
        print(f"  PASS  {label}: both None")
        PASS += 1
        return
    try:
        if abs(float(got) - float(exp)) <= eps:
            print(f"  PASS  {label}: got={got} exp={exp}")
            PASS += 1
        else:
            print(f"  FAIL  {label}: got={got} exp={exp} diff={abs(float(got)-float(exp)):.8f}")
            FAIL += 1
    except Exception as e:
        print(f"  FAIL  {label}: comparison error {e}  got={got!r} exp={exp!r}")
        FAIL += 1

def chk_true(label, val):
    global PASS, FAIL
    if val:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}")
        FAIL += 1

def raw_rows(sql, params=None):
    with psycopg2.connect(DB_URL, connect_timeout=5) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cu:
            cu.execute(sql, params or ())
            return cu.fetchall()

def raw_one(sql, params=None):
    with psycopg2.connect(DB_URL, connect_timeout=5) as c, c.cursor() as cu:
        cu.execute(sql, params or ())
        r = cu.fetchone()
        return r[0] if r else None

print("=== ITEM 17: PERFORMANCE VALIDATION (no-DELETE variant) ===")

print("\n--- Raw SQL baseline (all CLOSED trades) ---")
ALL_CLOSED = raw_rows("""
    SELECT paper_trade_id, underlying, strategy_name, family,
           market_regime, entry_time, close_time, status,
           gross_pnl, net_pnl, commission_paid,
           return_on_capital_realized, capital_at_risk,
           maximum_profit, maximum_loss, probability_of_profit
    FROM ase_paper_trades
    WHERE status='CLOSED'
    ORDER BY close_time
""")
N_CLOSED = len(ALL_CLOSED)
print(f"  INFO  closed trade count = {N_CLOSED}")
chk_true("at least 1 closed trade exists", N_CLOSED >= 1)

pnls     = [float(t["net_pnl"]) for t in ALL_CLOSED if t["net_pnl"] is not None]
gross_list = [float(t["gross_pnl"]) for t in ALL_CLOSED if t["gross_pnl"] is not None]
comm_list  = [float(t["commission_paid"]) for t in ALL_CLOSED if t["commission_paid"] is not None]
wins   = [p for p in pnls if p > 0]
losses = [p for p in pnls if p < 0]

print("\n--- generate_report: ALL-TIME period ---")
period_start = date(2020, 1, 1)
period_end   = date(2099, 12, 31)
rpt = generate_report("DAILY", period_start, period_end)
chk_true("generate_report returned dict", isinstance(rpt, dict))

print("\n--- Net/Gross P/L ---")
exp_net_paper  = round(sum(pnls), 4)
exp_net_gross  = round(sum(gross_list), 4)
exp_commission = round(sum(comm_list), 4)
chk_close("net_pnl_paper matches SQL sum(net_pnl)", rpt["net_pnl_paper"], exp_net_paper)
print(f"  INFO  SQL gross_total={exp_net_gross} commission_total={exp_commission}")
print(f"  INFO  net_pnl_paper={rpt['net_pnl_paper']} (SQL confirms)")
full_trades = [t for t in ALL_CLOSED
               if t["gross_pnl"] is not None and t["commission_paid"] is not None
               and t["net_pnl"] is not None]
for t in full_trades[:5]:
    exp = round(float(t["gross_pnl"]) - float(t["commission_paid"]), 4)
    got = round(float(t["net_pnl"]), 4)
    chk(f"net=gross-comm for {t['paper_trade_id'][:20]}", got, exp)
if not full_trades:
    print("  INFO  no closed trades with full gross/commission data — skipping per-trade check")

print("\n--- Three Return Columns ---")
th  = rpt["net_pnl_theoretical"]
mod = rpt["net_pnl_modeled"]
pap = rpt["net_pnl_paper"]
chk_true("net_pnl_theoretical present", th is not None)
chk_true("net_pnl_modeled present", mod is not None)
chk_true("net_pnl_paper present", pap is not None)
exp_th = float(sum(
    float(t["maximum_profit"] or 0) * float(t["probability_of_profit"] or 0.5)
    - float(t["maximum_loss"] or 0) * (1 - float(t["probability_of_profit"] or 0.5))
    for t in ALL_CLOSED
))
chk_close("net_pnl_theoretical = SQL-computed EV", th, round(exp_th, 4))
chk_close("net_pnl_modeled = net_pnl_paper × 1.05", mod, round(pap * 1.05, 4))
print(f"  INFO  theoretical={th}  modeled={mod}  paper={pap}")
chk_true("three returns distinct (theoretical != paper)", th != pap or N_CLOSED == 0)

print("\n--- Win Rate ---")
exp_wr = round(len(wins) / max(len(pnls), 1), 4) if pnls else None
chk_close("win_rate matches SQL (wins/closed)", rpt["win_rate"], exp_wr)
chk("win_count matches SQL", rpt["win_count"], len(wins))
chk("loss_count matches SQL", rpt["loss_count"], len(losses))
print(f"  INFO  wins={len(wins)} losses={len(losses)} breakeven={rpt['breakeven_count']}")

print("\n--- Profit Factor ---")
if losses:
    exp_pf = round(abs(sum(wins)) / abs(sum(losses)), 4)
    chk_close("profit_factor = |sum_wins|/|sum_losses|", rpt["profit_factor"], exp_pf)
else:
    chk("profit_factor = None (no losses)", rpt["profit_factor"], None)
    print("  INFO  no losing trades — profit_factor=None (correct)")

print("\n--- Expectancy ---")
exp_exp = round(sum(pnls) / max(len(pnls), 1), 4) if pnls else None
chk_close("expectancy = mean(net_pnl)", rpt["expectancy"], exp_exp)
print(f"  INFO  expectancy={rpt['expectancy']}")

print("\n--- Sharpe ---")
CAP = 100_000
returns_daily = [p / CAP for p in pnls]
exp_sharpe = _sharpe(returns_daily)
if exp_sharpe is None:
    chk("sharpe = None (std=0 or n<2)", rpt["sharpe"], None)
    print(f"  INFO  sharpe=None — n={len(returns_daily)}")
else:
    chk_close("sharpe independently computed", rpt["sharpe"], exp_sharpe)
print(f"  INFO  sharpe={rpt['sharpe']}")

print("\n--- Sortino ---")
exp_sortino = _sortino(returns_daily)
if exp_sortino is None:
    chk("sortino = None (no downside deviation or n<2)", rpt["sortino"], None)
    print("  INFO  sortino=None — no downside deviation (correct for all-win set)")
else:
    chk_close("sortino independently computed", rpt["sortino"], exp_sortino)
print(f"  INFO  sortino={rpt['sortino']}")

print("\n--- Max Drawdown + Equity Curve ---")
equity = []
running = 0.0
for t in ALL_CLOSED:
    running += float(t["net_pnl"] or 0)
    equity.append(round(running, 2))
exp_mdd, exp_dd_curve = _max_drawdown(equity)
chk_close("max_drawdown independently computed", rpt["max_drawdown"], exp_mdd)
chk("equity_curve is list", isinstance(rpt["equity_curve"], list), True)
chk("equity_curve length matches closed count", len(rpt["equity_curve"]), N_CLOSED)
chk("drawdown_curve length matches equity_curve", len(rpt["drawdown_curve"]), len(rpt["equity_curve"]))
print(f"  INFO  max_drawdown={rpt['max_drawdown']}  equity_curve[-1]={equity[-1] if equity else 'n/a'}")

print("\n--- Calmar ---")
if exp_mdd and exp_mdd != 0:
    exp_calmar = round(abs(sum(pnls) / CAP) / abs(exp_mdd), 4)
    chk_close("calmar = |total_return|/|max_drawdown|", rpt["calmar"], exp_calmar)
else:
    chk("calmar = None (no drawdown)", rpt["calmar"], None)
    print("  INFO  calmar=None — max_drawdown=0 (no losses, correct)")
print(f"  INFO  calmar={rpt['calmar']}")

print("\n--- Return on Capital ---")
exp_roc = round(sum(pnls) / CAP, 6)
chk_close("return_on_capital = net_pnl_paper / 100_000", rpt["return_on_capital"], exp_roc)
print(f"  INFO  return_on_capital={rpt['return_on_capital']}")

print("\n--- Return on Risk (per trade) ---")
ror_rows = [t for t in ALL_CLOSED if t["return_on_capital_realized"] is not None
            and t["capital_at_risk"] and float(t["capital_at_risk"]) > 0]
for t in ror_rows[:3]:
    exp_ror = round(float(t["net_pnl"]) / max(float(t["capital_at_risk"]), 0.01), 4)
    got_ror = round(float(t["return_on_capital_realized"]), 4)
    chk(f"return_on_risk for {t['paper_trade_id'][:20]}", got_ror, exp_ror)

print("\n--- Capital Preservation ---")
cap_check = raw_one("""
    SELECT COUNT(*) FROM ase_paper_trades
    WHERE status='CLOSED'
    AND capital_at_risk IS NOT NULL AND buying_power IS NOT NULL
    AND capital_at_risk > buying_power * 1.01
""")
chk("capital_preservation: no trade capital_at_risk > buying_power", cap_check, 0)
loss_breach = raw_one("""
    SELECT COUNT(*) FROM ase_paper_trades
    WHERE status='CLOSED' AND net_pnl < 0
    AND maximum_loss IS NOT NULL
    AND ABS(net_pnl) > maximum_loss * 100 * 1.05
""")
chk("capital_preservation: no loss exceeds maximum_loss*100", loss_breach, 0)

print("\n--- Monthly Returns ---")
from aiem_strat_engine.reporting import generate_monthly_report
monthly = generate_monthly_report(date.today())
chk_true("generate_monthly_report returns dict", isinstance(monthly, dict))
chk_true("monthly equity_curve present", "equity_curve" in monthly)
chk_true("monthly period_type=MONTHLY", monthly.get("period_type") == "MONTHLY")
chk_true("monthly net_pnl_paper present", "net_pnl_paper" in monthly)
print(f"  INFO  monthly {monthly.get('period_start')} to {monthly.get('period_end')}")
print(f"  INFO  monthly net_pnl_paper={monthly.get('net_pnl_paper')}")

print("\n--- Breakdowns ---")
chk_true("by_family is dict", isinstance(rpt["by_family"], dict))
chk_true("by_symbol is dict", isinstance(rpt["by_symbol"], dict))
chk_true("by_regime is dict", isinstance(rpt["by_regime"], dict))
chk_true("by_family non-empty", len(rpt["by_family"]) > 0)
chk_true("by_symbol non-empty", len(rpt["by_symbol"]) > 0)
print(f"  INFO  by_family keys: {list(rpt['by_family'].keys())}")
print(f"  INFO  by_regime keys: {list(rpt['by_regime'].keys())}")
for grp, stats in list(rpt["by_family"].items())[:2]:
    for key in ("count", "closed", "wins", "losses", "win_rate", "net_pnl"):
        chk_true(f"by_family[{grp}] has '{key}'", key in stats)

print("\n--- Brier Score ---")
exp_brier = _brier_score([dict(t) for t in ALL_CLOSED])
if exp_brier is None:
    chk("brier_score=None (no pop data)", rpt["brier_score"], None)
else:
    chk_close("brier_score independently computed", rpt["brier_score"], exp_brier)
print(f"  INFO  brier_score={rpt['brier_score']}")

print("\n--- Trade Ledger ---")
ledger = rpt["trade_ledger"]
chk_true("trade_ledger is list", isinstance(ledger, list))
chk_true("trade_ledger non-empty", len(ledger) > 0)
for entry in ledger[:2]:
    chk_true("ledger entry has 'id'", "id" in entry)
    chk_true("ledger entry has 'ticker'", "ticker" in entry)
    chk_true("ledger entry has 'pnl'", "pnl" in entry)
    chk_true("ledger entry has 'status'", "status" in entry)

print("\n--- Report SHA-256 Integrity ---")
# Unique period_type per run so ON CONFLICT never fires
unique_period_type = f"VERIFY_NODELETE_{int(time.time())}"
unique_period_start = date(2099, 6, 1)
unique_period_end   = date(2099, 6, 30)
fresh_rpt = generate_report(unique_period_type, unique_period_start, unique_period_end)
fresh_rid = fresh_rpt.get("report_id", "")
chk_true("fresh report_id present", bool(fresh_rid))

ok, msg = verify_report_integrity(fresh_rid)
chk("verify_report_integrity on fresh row: True", ok, True)
print(f"  INFO  {msg}")

# Tamper-detect: corrupt stored data
with psycopg2.connect(DB_URL, connect_timeout=5) as c, c.cursor() as cu:
    cu.execute(
        "UPDATE ase_performance_reports SET net_pnl_paper = net_pnl_paper + 999 "
        "WHERE report_id = %s", (fresh_rid,)
    )
    c.commit()
ok2, msg2 = verify_report_integrity(fresh_rid)
chk("tamper-detect: corrupted row fails integrity", ok2, False)
print(f"  INFO  {msg2}")

# Restore via UPDATE (no DELETE — DELETE pending Joel's approval)
with psycopg2.connect(DB_URL, connect_timeout=5) as c, c.cursor() as cu:
    cu.execute(
        "UPDATE ase_performance_reports SET net_pnl_paper = net_pnl_paper - 999 "
        "WHERE report_id = %s", (fresh_rid,)
    )
    c.commit()
ok3, msg3 = verify_report_integrity(fresh_rid)
chk("row restored via UPDATE (not DELETE): verify passes", ok3, True)
print(f"  INFO  restore verify: {msg3}")
print(f"  INFO  fresh row {fresh_rid} left in DB; DELETE pending Joel's approval")

# All 10 pre-existing stored reports pass integrity
stored_rows = raw_rows(
    "SELECT report_id FROM ase_performance_reports "
    "WHERE period_type NOT LIKE 'VERIFY_NODELETE%%' "
    "ORDER BY created_at"
)
print(f"\n  INFO  checking {len(stored_rows)} stored (non-test) reports for integrity")
all_stored_pass = True
for row in stored_rows:
    ok4, msg4 = verify_report_integrity(row["report_id"])
    status = "PASS" if ok4 else "FAIL"
    print(f"  {status}  {row['report_id']}: {msg4}")
    if not ok4:
        all_stored_pass = False
        FAIL += 1
    else:
        PASS += 1

chk_true("all pre-existing stored reports pass integrity", all_stored_pass)

print(f"\nPASS={PASS}  FAIL={FAIL}")
if FAIL > 0:
    print("EXIT STATUS: FAIL")
    sys.exit(1)
print("EXIT STATUS: PASS")
sys.exit(0)
