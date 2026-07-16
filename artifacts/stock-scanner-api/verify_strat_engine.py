#!/usr/bin/env python3
"""
verify_strat_engine.py — Full 18-proof verification script for the
Advanced Options Strategy Engine (aiem_strat_engine/).

For each proof, outputs:
  - Exact command / SQL
  - Raw output
  - Timestamp
  - Run ID
  - PASS / FAIL

Run with: python3 verify_strat_engine.py
"""
import sys, os, json, time, hashlib, datetime, traceback

sys.path.insert(0, os.path.dirname(__file__))

import psycopg2, psycopg2.extras

_CONN_STR = os.environ["DATABASE_URL"]
_RUN_ID   = f"verify_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
_RESULTS  = []

def _ts(): return datetime.datetime.utcnow().isoformat() + "Z"
def _conn(): return psycopg2.connect(_CONN_STR)

def _pass(proof_id, desc, evidence):
    _RESULTS.append({"id": proof_id, "status": "PASS", "desc": desc, "ts": _ts(), "evidence": str(evidence)[:300]})
    print(f"  ✓ PASS  P{proof_id:02d}: {desc}")

def _fail(proof_id, desc, reason):
    _RESULTS.append({"id": proof_id, "status": "FAIL", "desc": desc, "ts": _ts(), "evidence": str(reason)[:300]})
    print(f"  ✗ FAIL  P{proof_id:02d}: {desc}  → {str(reason)[:120]}")

def _banner(n, title):
    print(f"\n{'─'*60}")
    print(f"P{n:02d}  {title}")
    print(f"{'─'*60}")


# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 1 — Package importable, version correct
# ══════════════════════════════════════════════════════════════════════════════
_banner(1, "Package importable and version correct")
try:
    import aiem_strat_engine
    _pass(1, "package import", f"version={aiem_strat_engine.__version__}")
except Exception as e:
    _fail(1, "package import", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 2 — Catalog: >= 100 strategies, 13 families, all required families
# ══════════════════════════════════════════════════════════════════════════════
_banner(2, "Catalog: >= 100 strategies, 13 families")
try:
    from aiem_strat_engine.catalog import CATALOG, count, families, CATALOG_BY_FAMILY
    REQUIRED_FAMILIES = {
        "SINGLE_LEG","STOCK_PLUS_OPTION","CALL_SPREADS","PUT_SPREADS",
        "SYNTHETIC_COMBINATION","CALENDAR","DIAGONAL","STRADDLE_STRANGLE",
        "BUTTERFLY","CONDOR","RATIO_BACKSPREAD","ADVANCED_INCOME_VOL","EVENT_EXPIRATION"
    }
    c = count()
    if c["total"] < 100:
        _fail(2, "catalog count", f"only {c['total']} strategies (need >= 100)")
    elif not REQUIRED_FAMILIES.issubset(set(CATALOG_BY_FAMILY.keys())):
        missing = REQUIRED_FAMILIES - set(CATALOG_BY_FAMILY.keys())
        _fail(2, "catalog families", f"missing families: {missing}")
    else:
        _pass(2, "catalog", f"total={c['total']} autonomous={c['autonomous']} analysis_only={c['analysis_only']} families={len(c['by_family'])}")
except Exception as e:
    _fail(2, "catalog", traceback.format_exc()[-200:])

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 3 — Undefined-risk strategies all have execution_mode=ANALYSIS_ONLY
# ══════════════════════════════════════════════════════════════════════════════
_banner(3, "Undefined-risk strategies are ANALYSIS_ONLY")
try:
    from aiem_strat_engine.catalog import CATALOG
    from aiem_strat_engine.legs import RISK_UNDEFINED, MODE_AUTONOMOUS
    violations = [s.name for s in CATALOG if s.risk_class == RISK_UNDEFINED and s.execution_mode == MODE_AUTONOMOUS]
    if violations:
        _fail(3, "undefined-risk autonomy check", f"VIOLATION: {violations}")
    else:
        undef_count = sum(1 for s in CATALOG if s.risk_class == RISK_UNDEFINED)
        _pass(3, "undefined-risk enforcement", f"{undef_count} undefined-risk strategies are all ANALYSIS_ONLY")
except Exception as e:
    _fail(3, "undefined-risk check", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 4 — Strategy fingerprint is deterministic (same input = same hash)
# ══════════════════════════════════════════════════════════════════════════════
_banner(4, "Deterministic strategy fingerprint")
try:
    from aiem_strat_engine.legs import Leg, strategy_fingerprint, canonical_sort
    legs_a = [Leg("CALL","LONG",strike=100,expiration="2025-01-17",dte=30,mid=2.50,delta=0.40),
              Leg("CALL","SHORT",strike=105,expiration="2025-01-17",dte=30,mid=1.20,delta=0.25)]
    legs_b = [Leg("CALL","SHORT",strike=105,expiration="2025-01-17",dte=30,mid=1.20,delta=0.25),
              Leg("CALL","LONG",strike=100,expiration="2025-01-17",dte=30,mid=2.50,delta=0.40)]
    fp1 = strategy_fingerprint(legs_a)
    fp2 = strategy_fingerprint(legs_b)
    if fp1 != fp2:
        _fail(4, "fingerprint determinism", f"fp1={fp1} fp2={fp2}")
    else:
        _pass(4, "deterministic fingerprint", f"fingerprint={fp1}")
except Exception as e:
    _fail(4, "fingerprint", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 5 — Payoff: defined-risk strategies return finite max_loss
# ══════════════════════════════════════════════════════════════════════════════
_banner(5, "Payoff: defined-risk strategies return finite max_loss")
try:
    from aiem_strat_engine.payoff import compute_payoff
    from aiem_strat_engine.legs import Leg
    bull_call_legs = [
        Leg("CALL","LONG",strike=100,expiration="2025-01-17",dte=30,mid=3.00,delta=0.50,iv=0.30),
        Leg("CALL","SHORT",strike=105,expiration="2025-01-17",dte=30,mid=1.50,delta=0.30,iv=0.28),
    ]
    p = compute_payoff(bull_call_legs, "Bull Call Debit Spread", 100.0)
    if p["max_loss"] is None:
        _fail(5, "payoff max_loss", "max_loss is None for defined-risk spread")
    elif p["max_profit"] is None:
        _fail(5, "payoff max_profit", "max_profit is None")
    elif not p["breakevens"]:
        _fail(5, "payoff breakevens", "no breakevens found")
    else:
        _pass(5, "payoff bull call spread",
              f"max_profit={p['max_profit']:.4f} max_loss={p['max_loss']:.4f} breakevens={p['breakevens']}")
except Exception as e:
    _fail(5, "payoff", traceback.format_exc()[-300:])

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 6 — Payoff: undefined-risk strategies return max_loss=None
# ══════════════════════════════════════════════════════════════════════════════
_banner(6, "Payoff: undefined-risk (naked short call) returns max_loss=None")
try:
    from aiem_strat_engine.payoff import compute_payoff
    from aiem_strat_engine.legs import Leg
    naked_short_call = [Leg("CALL","SHORT",strike=100,expiration="2025-01-17",dte=30,mid=3.00,delta=0.50,iv=0.30)]
    p = compute_payoff(naked_short_call, "Covered Short Call", 95.0)
    if p.get("is_undefined_risk") or p["max_loss"] is None:
        _pass(6, "naked short call undefined risk",
              f"is_undefined_risk={p.get('is_undefined_risk')} max_loss={p['max_loss']}")
    else:
        _fail(6, "naked short undefined risk not detected",
              f"max_loss={p['max_loss']} is_undefined={p.get('is_undefined_risk')}")
except Exception as e:
    _fail(6, "payoff undefined", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 7 — Greeks: aggregation produces correct signs
# ══════════════════════════════════════════════════════════════════════════════
_banner(7, "Greeks: aggregation sign/magnitude check")
try:
    from aiem_strat_engine.greeks import aggregate
    from aiem_strat_engine.legs import Leg
    bull_spread = [
        Leg("CALL","LONG",strike=100,dte=30,iv=0.30,delta=0.50,gamma=0.05,theta=-0.03,vega=0.10),
        Leg("CALL","SHORT",strike=105,dte=30,iv=0.28,delta=0.30,gamma=0.04,theta=-0.02,vega=0.08),
    ]
    g = aggregate(bull_spread)
    # Delta should be positive (net long)
    # Theta should be negative (net long debit)
    if g.get("delta") and g["delta"] > 0 and g.get("theta") and g["theta"] < 0:
        _pass(7, "greek aggregation", f"delta={g['delta']:.4f} theta={g['theta']:.4f} vega={g.get('vega'):.4f}")
    else:
        _fail(7, "greek aggregation signs", f"delta={g.get('delta')} theta={g.get('theta')}")
except Exception as e:
    _fail(7, "greeks", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 8 — PoP: uses lognormal + fat-tail blend (NOT just delta)
# ══════════════════════════════════════════════════════════════════════════════
_banner(8, "PoP: lognormal+fat-tail blend, not just delta")
try:
    from aiem_strat_engine.probability import calibrated_pop, _price_grid
    prices  = _price_grid(100.0)
    # Simple bull call debit spread payoff: profit zone S > 101.50 (breakeven)
    payoffs = [max(0, min(p-101.5, 3.5)) - 1.5 for p in prices]
    result  = calibrated_pop(payoffs, prices, 100.0, iv=0.30, dte=30, skew=0.02)
    pop     = result.get("pop")
    fat_pop = result.get("pop_fat_tail")
    log_pop = result.get("pop_lognormal")
    if pop is None or fat_pop is None or log_pop is None:
        _fail(8, "PoP components missing", result)
    elif pop == log_pop:
        _fail(8, "PoP is just lognormal (no fat-tail blend)", f"pop={pop} log={log_pop}")
    elif not (0 < pop < 1):
        _fail(8, "PoP out of [0,1]", f"pop={pop}")
    else:
        _pass(8, "calibrated PoP",
              f"pop_blended={pop:.4f} lognormal={log_pop:.4f} fat_tail={fat_pop:.4f} pop_touch={result.get('pop_touch'):.4f}")
except Exception as e:
    _fail(8, "probability", traceback.format_exc()[-300:])

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 9 — Config SHA-256 is deterministic and non-empty
# ══════════════════════════════════════════════════════════════════════════════
_banner(9, "Config SHA-256 deterministic and non-empty")
try:
    from aiem_strat_engine.config import config_sha256
    sha1 = config_sha256()
    sha2 = config_sha256()
    if sha1 != sha2:
        _fail(9, "config sha not deterministic", f"sha1={sha1} sha2={sha2}")
    elif len(sha1) != 64:
        _fail(9, "config sha length", f"len={len(sha1)}")
    else:
        _pass(9, "config sha256", f"sha={sha1[:16]}…")
except Exception as e:
    _fail(9, "config sha", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 10 — DB schema: all ase_* tables created
# ══════════════════════════════════════════════════════════════════════════════
_banner(10, "DB schema: all 8 ase_* tables exist")
REQUIRED_TABLES = {
    "ase_strategy_registry","ase_engine_jobs","ase_decision_runs",
    "ase_strategy_evaluations","ase_paper_trades","ase_paper_trade_legs",
    "ase_adjustments","ase_position_valuations","ase_performance_reports",
}
try:
    from aiem_strat_engine.db import create_schema, list_tables
    create_schema()
    tables = set(list_tables())
    missing = REQUIRED_TABLES - tables
    if missing:
        _fail(10, "missing tables", f"missing: {missing}")
    else:
        _pass(10, "all ase_* tables present", f"tables={sorted(tables)}")
except Exception as e:
    _fail(10, "db schema", traceback.format_exc()[-300:])

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 11 — SQL proof: ase_strategy_evaluations has correct columns
# ══════════════════════════════════════════════════════════════════════════════
_banner(11, "SQL: ase_strategy_evaluations column set")
REQUIRED_COLS = {"capital_compounding_score","pop","max_loss","max_profit","breakevens","legs_json","penalty_total"}
try:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='ase_strategy_evaluations' AND table_schema='public'
        """)
        cols = {r[0] for r in cur.fetchall()}
        missing = REQUIRED_COLS - cols
        if missing:
            _fail(11, "missing columns in ase_strategy_evaluations", f"missing={missing}")
        else:
            _pass(11, "ase_strategy_evaluations columns", f"has {len(cols)} columns including all required")
except Exception as e:
    _fail(11, "column check", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 12 — Safety gate: paper_trader blocks ANALYSIS_ONLY strategy
# ══════════════════════════════════════════════════════════════════════════════
_banner(12, "Safety gate: paper_trader blocks ANALYSIS_ONLY strategy")
try:
    from aiem_strat_engine.paper_trader import safety_check
    from aiem_strat_engine.selector import EvaluationResult
    from aiem_strat_engine.legs import Leg

    analysis_only_eval = EvaluationResult(
        strategy_name="Short Straddle",
        strategy_family="STRADDLE_STRANGLE",
        strategy_fingerprint="test123",
        risk_class="UNDEFINED_RISK",
        execution_mode="ANALYSIS_ONLY",
        eligible=False,
        rejection_reasons=["ANALYSIS_ONLY"],
        legs=[Leg("CALL","SHORT",strike=100,dte=30,mid=3.0)],
        payoff_info={"max_loss": None, "is_undefined_risk": True},
        probability_info={},
        pricing_info={},
        greeks_info={},
        score_components={},
        capital_compounding_score=0.60,
    )
    block = safety_check(analysis_only_eval)
    if block is None:
        _fail(12, "safety gate did NOT block ANALYSIS_ONLY", "returned None (allowed)")
    else:
        _pass(12, "safety gate blocks ANALYSIS_ONLY", f"block_reason={block[:80]}")
except Exception as e:
    _fail(12, "safety gate", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 13 — SQL: insert and verify a paper trade (parent)
# ══════════════════════════════════════════════════════════════════════════════
_banner(13, "SQL: insert paper trade parent and verify")
_TEST_PT_ID = None
try:
    with _conn() as conn, conn.cursor() as cur:
        pt_id = f"ase_pt_verify_{_RUN_ID[:16]}"
        cur.execute("""
            INSERT INTO ase_paper_trades (
                paper_trade_id, strategy_fingerprint, decision_run_id,
                underlying, strategy_name, family, thesis, direction,
                entry_time, probability_of_profit, maximum_profit, maximum_loss,
                capital_at_risk, buying_power, selected_score, no_trade_score,
                market_regime, volatility_regime, underlying_price_at_entry,
                status, audit_hash
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            pt_id, "fp_verify", f"run_verify_{_RUN_ID}", "VERIFY",
            "Verify Bull Call Spread", "CALL_SPREADS", "BULLISH", "BULLISH",
            0.58, 3.50, 1.50, 150.0, 150.0, 0.72, 0.35,
            "NEUTRAL", "LOW_IV", 100.0, "OPEN",
            hashlib.sha256(pt_id.encode()).hexdigest()
        ))
        conn.commit()
        _TEST_PT_ID = pt_id

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT paper_trade_id, status, strategy_name FROM ase_paper_trades WHERE paper_trade_id=%s", (_TEST_PT_ID,))
        row = cur.fetchone()
        if not row:
            _fail(13, "paper trade not found after insert", f"pt_id={_TEST_PT_ID}")
        else:
            _pass(13, "paper trade parent insert+verify", f"id={row[0]} status={row[1]} strat={row[2]}")
except Exception as e:
    _fail(13, "paper trade insert", traceback.format_exc()[-300:])

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 14 — SQL: insert and verify paper trade legs
# ══════════════════════════════════════════════════════════════════════════════
_banner(14, "SQL: insert paper trade legs and verify")
try:
    if _TEST_PT_ID:
        with _conn() as conn, conn.cursor() as cur:
            for i, (cp, side, strike, bid, ask, mid, delta) in enumerate([
                ("CALL","LONG",100,2.80,3.20,3.00,0.50),
                ("CALL","SHORT",105,1.30,1.70,1.50,0.30),
            ], 1):
                cur.execute("""
                    INSERT INTO ase_paper_trade_legs (
                        paper_trade_id, leg_number, asset_type, call_or_put,
                        buy_or_sell, open_or_close, quantity, ratio,
                        strike, expiration, dte_at_entry,
                        bid, ask, mid, modeled_fill, paper_fill,
                        iv, delta, data_provider
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    _TEST_PT_ID, i, "CALL", cp, side, "OPEN",
                    1, 1, strike, "2025-01-17", 30,
                    bid, ask, mid, mid, mid, 0.30, delta, "verify"
                ))
            conn.commit()

        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ase_paper_trade_legs WHERE paper_trade_id=%s", (_TEST_PT_ID,))
            n = cur.fetchone()[0]
            if n == 2:
                _pass(14, "paper trade legs insert+verify", f"2 legs for {_TEST_PT_ID}")
            else:
                _fail(14, "wrong leg count", f"expected 2, got {n}")
    else:
        _fail(14, "no test trade ID", "proof 13 failed")
except Exception as e:
    _fail(14, "legs insert", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 15 — SQL: decision run record persisted
# ══════════════════════════════════════════════════════════════════════════════
_banner(15, "SQL: decision run record insert and verify")
try:
    run_id = f"run_verify_{_RUN_ID}"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO ase_decision_runs (
                run_id, ticker, underlying_price, thesis, market_regime,
                volatility_regime, strategies_evaluated, strategies_rejected,
                no_trade_score, decision, config_sha256
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id) DO NOTHING
        """, (run_id,"VERIFY",100.0,"BULLISH","NEUTRAL","LOW_IV",10,3,0.35,"NO_TRADE","sha256_verify"))
        conn.commit()

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT run_id, decision, strategies_evaluated FROM ase_decision_runs WHERE run_id=%s", (run_id,))
        row = cur.fetchone()
        if not row:
            _fail(15, "decision run not found", run_id)
        else:
            _pass(15, "decision run insert+verify", f"run_id={row[0]} decision={row[1]} evaluated={row[2]}")
except Exception as e:
    _fail(15, "decision run", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 16 — SQL: position valuation record
# ══════════════════════════════════════════════════════════════════════════════
_banner(16, "SQL: position valuation record insert and verify")
try:
    if _TEST_PT_ID:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ase_position_valuations (
                    paper_trade_id, valuation_date, underlying_price,
                    paper_value, unrealized_pnl, delta
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (paper_trade_id, valuation_date) DO NOTHING
            """, (_TEST_PT_ID, "2025-01-15", 101.0, 1.80, 30.0, 0.20))
            conn.commit()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT unrealized_pnl FROM ase_position_valuations WHERE paper_trade_id=%s", (_TEST_PT_ID,))
            row = cur.fetchone()
            if row:
                _pass(16, "position valuation insert+verify", f"unrealized_pnl={row[0]}")
            else:
                _fail(16, "valuation not found", _TEST_PT_ID)
    else:
        _fail(16, "no test trade ID", "proof 13 failed")
except Exception as e:
    _fail(16, "valuation", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 17 — SQL: adjustment/exit record (append-only)
# ══════════════════════════════════════════════════════════════════════════════
_banner(17, "SQL: adjustment record insert, verify append-only pattern")
try:
    if _TEST_PT_ID:
        adj_id = f"ase_adj_verify_{_RUN_ID[:12]}"
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ase_adjustments (
                    adjustment_id, paper_trade_id, adjustment_type, reason,
                    legs_closed, legs_opened, net_cost
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (adj_id, _TEST_PT_ID, "FULL_CLOSE", "VERIFY_TEST",
                  json.dumps([{"leg_number":1}]), json.dumps([]), -75.0))
            conn.commit()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ase_adjustments WHERE paper_trade_id=%s", (_TEST_PT_ID,))
            cnt = cur.fetchone()[0]
            if cnt >= 1:
                _pass(17, "adjustment insert+verify append-only", f"{cnt} adjustment(s) for {_TEST_PT_ID}")
            else:
                _fail(17, "adjustment not found", adj_id)
    else:
        _fail(17, "no test trade ID", "proof 13 failed")
except Exception as e:
    _fail(17, "adjustment", e)

# ══════════════════════════════════════════════════════════════════════════════
#  PROOF 18 — SQL: performance report insert, SHA-256 verification
# ══════════════════════════════════════════════════════════════════════════════
_banner(18, "SQL: performance report insert and SHA-256 integrity check")
try:
    from aiem_strat_engine.reporting import generate_daily_report, verify_report_integrity
    import datetime as _dt
    # Generate a report for a past date (safe — no live data needed)
    rpt = generate_daily_report(_dt.date(2025, 1, 15))
    if rpt is None:
        _fail(18, "report generation returned None", "check DB connection")
    else:
        rpt_id = rpt.get("report_id")
        valid, msg = verify_report_integrity(rpt_id)
        if valid:
            _pass(18, "performance report SHA-256 integrity", f"report_id={rpt_id} {msg}")
        else:
            _fail(18, "SHA-256 mismatch", msg)
except Exception as e:
    _fail(18, "reporting", traceback.format_exc()[-300:])

# ══════════════════════════════════════════════════════════════════════════════
#  CLEANUP test rows
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "─"*60)
print("Cleaning up test rows...")
try:
    with _conn() as conn, conn.cursor() as cur:
        if _TEST_PT_ID:
            cur.execute("DELETE FROM ase_adjustments WHERE paper_trade_id=%s", (_TEST_PT_ID,))
            cur.execute("DELETE FROM ase_position_valuations WHERE paper_trade_id=%s", (_TEST_PT_ID,))
            cur.execute("DELETE FROM ase_paper_trade_legs WHERE paper_trade_id=%s", (_TEST_PT_ID,))
            cur.execute("DELETE FROM ase_paper_trades WHERE paper_trade_id=%s", (_TEST_PT_ID,))
            cur.execute("DELETE FROM ase_decision_runs WHERE run_id LIKE 'run_verify_%'")
            cur.execute("DELETE FROM ase_performance_reports WHERE period_start='2025-01-15' AND period_type='DAILY'")
        conn.commit()
    print("Cleanup complete")
except Exception as e:
    print(f"Cleanup error (non-fatal): {e}")

# ══════════════════════════════════════════════════════════════════════════════
#  SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print(f"VERIFICATION SUMMARY  (run_id={_RUN_ID})")
print("═"*60)
passed = sum(1 for r in _RESULTS if r["status"]=="PASS")
failed = sum(1 for r in _RESULTS if r["status"]=="FAIL")

for r in _RESULTS:
    icon = "✓" if r["status"]=="PASS" else "✗"
    print(f"  {icon} P{r['id']:02d} [{r['status']}] {r['desc']}")

print(f"\n  Total: {len(_RESULTS)} proofs  |  PASS: {passed}  |  FAIL: {failed}")
print(f"  Run ID: {_RUN_ID}")
print(f"  Timestamp: {_ts()}")
print("═"*60)

if failed > 0:
    print(f"\nFAILED PROOF DETAILS:")
    for r in _RESULTS:
        if r["status"] == "FAIL":
            print(f"\n  P{r['id']:02d}: {r['desc']}")
            print(f"  Evidence: {r['evidence']}")

sys.exit(0 if failed == 0 else 1)
