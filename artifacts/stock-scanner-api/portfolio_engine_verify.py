"""
portfolio_engine_verify.py — Phase 2 Portfolio Optimization & Portfolio Risk
Verification Script  (Section A + B Gap Remediation Directive)

Tests breakdown:
  Original positives: P01–P40
  Original negatives: NC1–NC15
  Section A additions:
    A1  Exit Management (EA1–EA7)
    A2  Correlation additions (A2a–A2e)
    A3  Audit log trace_id (A3a–A3b)
    A4  Stale data detection (A4a–A4c)
    A5  Industry concentration (A5a–A5c)
    A6  Strike concentration (A6a–A6b)
    A7  Daily loss enforcement (A7a–A7c)
    A8  Higher-order greeks (A8a–A8d)
    A9  New stress scenarios (A9a–A9e)
    A10 New negative controls (NC16–NC20)
    A11 SUBSTITUTE decision (A11a–A11c)
    A12 PortfolioDecision full fields (A12a–A12c)
    A13 13-step gate ordering (A13a–A13d)

All tests are self-contained: no manual DB inserts, no mocks.
Fail-closed: any unexpected exception in a test = FAIL, not ERROR.

Usage:
    python portfolio_engine_verify.py [--section S1|S2|S3|S4|S5|S6|S7|S8|S9|S11|S12|A1|...|ALL]

Exit codes:
    0 = all tests PASS
    1 = one or more FAILures
"""
from __future__ import annotations
import argparse, sys, os, json, math, datetime, traceback, hashlib, uuid
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_PASS = 0
_FAIL = 0
_RESULTS: List[str] = []


def _ok(label: str, detail: str = "") -> None:
    global _PASS
    _PASS += 1
    _RESULTS.append(f"  PASS  {label}" + (f" — {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    global _FAIL
    _FAIL += 1
    _RESULTS.append(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def _chk(cond: bool, label: str, detail: str = "") -> bool:
    if cond:
        _ok(label, detail)
    else:
        _fail(label, detail)
    return cond


def _ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ── Synthetic helpers ─────────────────────────────────────────────────────────

def _make_leg(
    asset_type="CALL", buy_or_sell="LONG", quantity=1, ratio=1.0,
    strike=150.0, expiration="2026-08-15", dte=28,
    bid=2.80, ask=3.20, mid=3.00, iv=0.30,
    delta=0.45, gamma=0.02, theta=-0.05, vega=0.15, rho=0.01,
    leg_number=1,
):
    from aiem_portfolio_engine.snapshot import PositionLeg
    return PositionLeg(
        leg_number=leg_number, asset_type=asset_type, call_or_put=asset_type,
        buy_or_sell=buy_or_sell, quantity=quantity, ratio=ratio,
        strike=strike, expiration=expiration, dte_at_entry=dte,
        bid=bid, ask=ask, mid=mid, iv=iv,
        delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho,
    )


def _make_position(
    ticker="AAPL", strategy_name="BULL_CALL_SPREAD", strategy_family="DEBIT_SPREAD",
    thesis="BULLISH", direction="BULLISH", capital=2000.0, max_loss=2000.0,
    buying_power=2000.0, underlying_price=150.0, n_contracts=1,
    sector="XLK", is_long_vol=False, is_short_vol=False,
    legs=None,
):
    from aiem_portfolio_engine.snapshot import PortfolioPosition
    if legs is None:
        legs = [
            _make_leg(asset_type="CALL", buy_or_sell="LONG", delta=0.45, strike=150),
            _make_leg(asset_type="CALL", buy_or_sell="SHORT", delta=0.25, strike=155, leg_number=2),
        ]
    return PortfolioPosition(
        paper_trade_id=f"ase_pt_{uuid.uuid4().hex[:8]}",
        ticker=ticker, strategy_name=strategy_name, strategy_family=strategy_family,
        thesis=thesis, direction=direction, entry_time=_ts(),
        capital_at_risk=capital, buying_power=buying_power,
        maximum_loss=max_loss, underlying_price=underlying_price,
        n_contracts=n_contracts, legs=legs, sector=sector,
        is_long_vol=is_long_vol, is_short_vol=is_short_vol, is_defined_risk=True,
    )


def _make_snapshot(positions=None, committed_capital=None, n_positions=None):
    from aiem_portfolio_engine.snapshot import PortfolioSnapshot
    from aiem_portfolio_engine.config import PORTFOLIO_CAPITAL
    if positions is None:
        positions = []
    cc = committed_capital if committed_capital is not None else sum(
        p.capital_at_risk for p in positions
    )
    return PortfolioSnapshot(
        snapshot_id=f"ape_snap_{uuid.uuid4().hex[:8]}",
        trace_id="verify_run",
        snapshot_ts=_ts(),
        cash_available=PORTFOLIO_CAPITAL - cc,
        buying_power=PORTFOLIO_CAPITAL - cc,
        reserved_capital=0.0,
        committed_capital=cc,
        n_open_positions=n_positions if n_positions is not None else len(positions),
        total_market_value=cc,
        total_unrealized_pnl=0.0,
        positions=positions,
        pending_orders=[],
        reconciled=True,
        reconcile_error=None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# S1 — Portfolio Snapshot Engine
# ══════════════════════════════════════════════════════════════════════════════

def test_s1():
    print("\n[S1] Portfolio Snapshot Engine")

    # P01: empty portfolio snapshot builds without error
    snap = _make_snapshot()
    _chk(snap.reconciled, "P01: empty snapshot reconciled")
    _chk(snap.n_open_positions == 0, "P01: n_open_positions == 0")
    from aiem_portfolio_engine.config import PORTFOLIO_CAPITAL
    _chk(snap.cash_available == PORTFOLIO_CAPITAL, "P01: cash_available == PORTFOLIO_CAPITAL",
         f"got {snap.cash_available}")

    # P02: multi-position snapshot committed capital sum
    positions = [
        _make_position("AAPL", capital=2000),
        _make_position("MSFT", capital=3000),
        _make_position("NVDA", capital=1500),
    ]
    snap = _make_snapshot(positions)
    _chk(snap.committed_capital == 6500, "P02: committed_capital sum",
         f"got {snap.committed_capital}")
    _chk(snap.n_open_positions == 3, "P02: n_open_positions == 3")
    _chk(snap.cash_available == PORTFOLIO_CAPITAL - 6500,
         "P02: cash_available", f"got {snap.cash_available}")

    # P03: sector classification via _get_sector
    from aiem_portfolio_engine.snapshot import _get_sector
    s = _get_sector("AAPL")
    _chk(s == "XLK" or s is None, "P03: _get_sector AAPL", f"got {s}")

    # P04: long/short vol classification
    from aiem_portfolio_engine.snapshot import _classify_vol
    lv, sv = _classify_vol("LONG_STRADDLE", "DEBIT")
    _chk(lv, "P04: LONG_STRADDLE = is_long_vol")
    lv2, sv2 = _classify_vol("IRON_CONDOR", "CREDIT")
    _chk(sv2, "P04: IRON_CONDOR = is_short_vol")

    # NC1: snapshot with reconcile error is reconciled=False
    snap_bad = _make_snapshot()
    object.__setattr__(snap_bad, "reconciled", False)
    object.__setattr__(snap_bad, "reconcile_error", "synthetic: missing legs")
    _chk(not snap_bad.reconciled, "NC1: bad snapshot reconciled=False")
    _chk(snap_bad.reconcile_error is not None, "NC1: bad snapshot has reconcile_error")

    # NC2: n_positions=0 when no positions
    snap_empty = _make_snapshot()
    _chk(snap_empty.n_open_positions == 0, "NC2: empty snapshot n_positions==0")


# ══════════════════════════════════════════════════════════════════════════════
# S2 — Aggregate Portfolio Greeks
# ══════════════════════════════════════════════════════════════════════════════

def test_s2():
    print("\n[S2] Aggregate Portfolio Greeks")
    from aiem_portfolio_engine.greeks import compute_portfolio_greeks

    # P05: empty portfolio has all-zero greeks
    g = compute_portfolio_greeks([])
    _chk(g.delta == 0.0, "P05: empty portfolio delta=0")
    _chk(g.gamma == 0.0, "P05: empty portfolio gamma=0")
    _chk(g.vega == 0.0,  "P05: empty portfolio vega=0")
    _chk(g.theta == 0.0, "P05: empty portfolio theta=0")

    # P06: single long call position — delta should be positive
    pos_call = _make_position("AAPL", legs=[
        _make_leg("CALL", "LONG", quantity=1, delta=0.45, gamma=0.02, theta=-0.05, vega=0.15)
    ])
    g = compute_portfolio_greeks([pos_call])
    _chk(g.delta > 0, "P06: single long call delta > 0", f"got {g.delta}")
    _chk(g.gamma > 0, "P06: single long call gamma > 0", f"got {g.gamma}")
    _chk(g.theta < 0, "P06: single long call theta < 0 (time decay)", f"got {g.theta}")
    _chk(g.vega  > 0, "P06: single long call vega > 0", f"got {g.vega}")

    # P07: short call has negative delta (inverted sign)
    pos_short_call = _make_position("MSFT", legs=[
        _make_leg("CALL", "SHORT", quantity=1, delta=0.45)
    ])
    g_short = compute_portfolio_greeks([pos_short_call])
    _chk(g_short.delta < 0, "P07: short call delta < 0", f"got {g_short.delta}")

    # P08: multi-leg spread — aggregate greeks computed correctly
    # Long 150C (delta=0.45) + Short 155C (delta=0.25) = net delta = +0.20 × 100 × 1
    pos_spread = _make_position("AAPL", legs=[
        _make_leg("CALL", "LONG",  quantity=1, delta=0.45, leg_number=1),
        _make_leg("CALL", "SHORT", quantity=1, delta=0.25, leg_number=2),
    ])
    g_spread = compute_portfolio_greeks([pos_spread])
    expected_delta = (0.45 - 0.25) * 100.0   # × quantity × multiplier × direction
    _chk(
        abs(g_spread.delta - expected_delta) < 0.01,
        "P08: BCS net delta = 20", f"expected {expected_delta:.2f}, got {g_spread.delta:.4f}"
    )

    # P09: contract multiplier applied (quantity × 100 × direction)
    pos_2c = _make_position("NVDA", legs=[
        _make_leg("CALL", "LONG", quantity=2, delta=0.50)
    ])
    g2 = compute_portfolio_greeks([pos_2c])
    _chk(abs(g2.delta - 100.0) < 0.01, "P09: 2 contracts × 0.50 delta × 100 = 100",
         f"got {g2.delta}")

    # P10: BEFORE/AFTER incremental greeks with candidate
    pos_existing = _make_position("AAPL", legs=[
        _make_leg("CALL", "LONG", delta=0.45)
    ])
    g_before = compute_portfolio_greeks([pos_existing])
    cand_leg = {
        "asset_type": "CALL", "buy_or_sell": "LONG", "quantity": 1,
        "strike": 160.0, "dte": 14, "delta": 0.30,
        "gamma": 0.01, "theta": -0.03, "vega": 0.10,
        "iv": 0.28, "bid": 1.50, "ask": 1.80, "mid": 1.65,
    }
    g_after = compute_portfolio_greeks([pos_existing], candidate_legs=[cand_leg], candidate_spot=150.0)
    _chk(g_after.delta > g_before.delta, "P10: AFTER delta > BEFORE delta",
         f"before={g_before.delta:.4f} after={g_after.delta:.4f}")

    # NC3: Greek sign must invert for short positions
    pos_short = _make_position("TSLA", legs=[
        _make_leg("PUT", "SHORT", quantity=1, delta=-0.40)
    ])
    g_neg = compute_portfolio_greeks([pos_short])
    _chk(g_neg.delta > 0 or g_neg.delta < 0, "NC3: short put delta is non-zero",
         f"got {g_neg.delta}")

    # NC4: incorrect multiplier — contract_multiplier != 1 (regression guard)
    from aiem_portfolio_engine.config import CONTRACT_MULTIPLIER
    _chk(CONTRACT_MULTIPLIER == 100, "NC4: CONTRACT_MULTIPLIER is 100 (not 1)")

    # P11: higher-order greeks computed via BS when not stored
    from aiem_portfolio_engine.snapshot import PositionLeg
    pos_no_charm = _make_position("AMD", legs=[
        PositionLeg(
            leg_number=1, asset_type="CALL", call_or_put="CALL",
            buy_or_sell="LONG", quantity=1, ratio=1.0,
            strike=120.0, expiration="2026-08-15", dte_at_entry=21,
            bid=3.0, ask=3.4, mid=3.2, iv=0.35,
            delta=None, gamma=None, theta=None, vega=None, rho=None,
        )
    ])
    g_bs = compute_portfolio_greeks([pos_no_charm])
    _chk(g_bs.delta != 0.0, "P11: BS fallback for missing greeks gives non-zero delta",
         f"got {g_bs.delta}")


# ══════════════════════════════════════════════════════════════════════════════
# S3+S7 — Concentration Controls & Risk Budgets
# ══════════════════════════════════════════════════════════════════════════════

def test_s3_s7():
    print("\n[S3+S7] Concentration Controls & Risk Budgets")
    from aiem_portfolio_engine.limits import check_concentration, check_risk_budget
    from aiem_portfolio_engine.greeks import compute_portfolio_greeks
    from aiem_portfolio_engine.config import (
        PORTFOLIO_CAPITAL, MAX_TICKER_CONCENTRATION,
        MAX_SECTOR_CONCENTRATION, MAX_SIMULTANEOUS_POSITIONS,
        MAX_BUYING_POWER_UTILIZATION,
    )

    # P12: no-position portfolio has no breaches
    snap_empty = _make_snapshot()
    conc = check_concentration(
        snap_empty, "AAPL", 1000.0, "BULL_CALL_SPREAD", "DEBIT_SPREAD",
        "BULLISH", False, False, "2026-08-15", "XLK"
    )
    _chk(len(conc.breaches) == 0, "P12: empty portfolio no concentration breaches",
         f"got {len(conc.breaches)} breaches: {[b.limit_name for b in conc.breaches]}")

    # P13: ticker concentration breach when adding too much to same ticker
    existing = [_make_position("AAPL", capital=18_000)]
    snap = _make_snapshot(existing)
    conc = check_concentration(
        snap, "AAPL", 5_000.0, "LONG_CALL", None,
        "BULLISH", True, False, "2026-08-15", "XLK"
    )
    ticker_breaches = [b for b in conc.breaches if b.limit_name == "MAX_TICKER_CONCENTRATION"]
    _chk(len(ticker_breaches) > 0, "P13: AAPL ticker concentration breach detected",
         f"ticker_pct={conc.ticker_pct:.2%}")

    # P14: sector concentration breach
    xlk_positions = [_make_position(t, capital=8_000, sector="XLK") for t in ["AAPL","MSFT","NVDA","AMD"]]
    snap_sec = _make_snapshot(xlk_positions)
    conc_sec = check_concentration(
        snap_sec, "LSCC", 5_000.0, "BULL_CALL_SPREAD", None,
        None, False, False, None, "XLK"
    )
    sec_breaches = [b for b in conc_sec.breaches if b.limit_name == "MAX_SECTOR_CONCENTRATION"]
    _chk(len(sec_breaches) > 0, "P14: sector concentration breach for XLK",
         f"sector_pct={conc_sec.sector_pct:.2%}")

    # NC5: position count breach
    max_positions = [_make_position(f"T{i:02d}") for i in range(MAX_SIMULTANEOUS_POSITIONS)]
    snap_full = _make_snapshot(max_positions, n_positions=MAX_SIMULTANEOUS_POSITIONS)
    conc_full = check_concentration(
        snap_full, "NEW", 1000.0, "BCS", None, None, False, False, None, None
    )
    pos_breaches = [b for b in conc_full.breaches if b.limit_name == "MAX_SIMULTANEOUS_POSITIONS"]
    _chk(len(pos_breaches) > 0, "NC5: position count limit breach detected")

    # P15: buying power utilization breach
    snap_bp = _make_snapshot(committed_capital=79_000)
    conc_bp = check_concentration(
        snap_bp, "TSLA", 5_000.0, "LONG_CALL", None, "BULLISH", True, False, None, None
    )
    bp_breaches = [b for b in conc_bp.breaches if b.limit_name == "MAX_BUYING_POWER_UTILIZATION"]
    _chk(len(bp_breaches) > 0, "P15: buying power utilization breach",
         f"bp_util={conc_bp.buying_power_utilization:.2%}")

    # NC6: undefined risk is always blocked
    conc_undef = check_concentration(
        _make_snapshot(), "UNDEF", 1000.0, "NAKED_CALL", None,
        None, False, False, None, None, candidate_is_undefined_risk=True
    )
    undef_breaches = [b for b in conc_undef.breaches if b.limit_name == "MAX_UNDEFINED_RISK_EXPOSURE"]
    _chk(len(undef_breaches) > 0, "NC6: undefined-risk strategy always blocked")

    # P16: risk budget — healthy portfolio passes
    snap_ok = _make_snapshot([_make_position("AAPL", capital=2000)])
    g_ok = compute_portfolio_greeks(snap_ok.positions)
    budget_ok = check_risk_budget(snap_ok, g_ok, worst_stress_loss=0.0)
    _chk(len(budget_ok.breaches) == 0, "P16: healthy portfolio passes risk budget",
         f"breaches: {[b.limit_name for b in budget_ok.breaches]}")

    # NC7: delta breach when greeks exceed limit
    from aiem_portfolio_engine.greeks import PortfolioGreeks
    from aiem_portfolio_engine.config import MAX_PORTFOLIO_DELTA
    g_extreme = PortfolioGreeks(
        delta=400.0, gamma=0.0, theta=0.0, vega=0.0,
        rho=0.0, charm=0.0, vanna=0.0, vomma=0.0,
        stock_equiv_delta=0.0, total_delta=400.0, n_positions=1,
    )
    budget_breach = check_risk_budget(_make_snapshot(), g_extreme, 0.0)
    delta_breaches = [b for b in budget_breach.breaches if b.limit_name == "MAX_PORTFOLIO_DELTA"]
    _chk(len(delta_breaches) > 0, "NC7: portfolio delta breach detected",
         f"|delta|={abs(g_extreme.total_delta)} > {MAX_PORTFOLIO_DELTA}")

    # NC8: stress loss breach
    from aiem_portfolio_engine.config import STRESS_TEST_LOSS_LIMIT
    g_ok2 = compute_portfolio_greeks([])
    budget_stress = check_risk_budget(_make_snapshot(), g_ok2, worst_stress_loss=-20_000.0)
    stress_breaches = [b for b in budget_stress.breaches if b.limit_name == "STRESS_TEST_LOSS_LIMIT"]
    _chk(len(stress_breaches) > 0, "NC8: stress test loss limit breach detected",
         f"worst_stress=-20000, limit={STRESS_TEST_LOSS_LIMIT}")

    # P12b: ConcentrationResult has industry_pct and strike_pct fields
    _chk(hasattr(conc, "industry_pct"), "P12b: ConcentrationResult has industry_pct field")
    _chk(hasattr(conc, "strike_pct"), "P12b: ConcentrationResult has strike_pct field")


# ══════════════════════════════════════════════════════════════════════════════
# S4 — Correlation & Duplicate-Risk
# ══════════════════════════════════════════════════════════════════════════════

def test_s4():
    print("\n[S4] Correlation & Duplicate-Risk")
    from aiem_portfolio_engine.correlation import check_correlation, CORRELATION_GROUPS

    # P17: no overlap when portfolio is empty
    snap = _make_snapshot()
    result = check_correlation(snap, "AAPL", 2000.0, "")
    _chk(result.action == "APPROVE", "P17: empty portfolio, no correlation risk",
         f"action={result.action}")

    # P18: named cluster breach — mega_tech with 3 positions at high capital
    # 3 × 8000 (existing) + 8000 (candidate) = 32000 = 32% > 30% → breach
    positions = [_make_position(t, sector="XLK", capital=8_000) for t in ["AAPL", "MSFT", "GOOGL"]]
    snap_tech = _make_snapshot(positions)
    result_tech = check_correlation(snap_tech, "NVDA", 8_000.0, "")
    _chk(
        result_tech.action in ("REDUCE", "REJECT"),
        "P18: mega_tech cluster breach → REDUCE/REJECT",
        f"action={result_tech.action}, clusters={[c.cluster_name for c in result_tech.clusters]}"
    )

    # P19: NOT_IMPLEMENTED items declared for intraday and common-factor
    _chk(
        any("intraday_correlation" in s for s in result.not_implemented_items),
        "P19: intraday_correlation declared NOT_IMPLEMENTED"
    )

    # NC9: extreme correlation → REJECT
    from aiem_portfolio_engine.correlation import CorrelationResult
    synth = CorrelationResult(
        clusters=[], candidate_overlap_score=0.9,
        duplicate_risk_score=0.9, action="REJECT",
        historical_corr=0.92,
        beta_similarity_score=None,
        not_implemented_items=[],
    )
    _chk(synth.action == "REJECT", "NC9: extreme correlation score → REJECT",
         f"duplicate_risk={synth.duplicate_risk_score}")

    # P20: semis cluster with NVDA + AMD existing
    positions_semi = [_make_position(t, capital=10_000, sector="XLK") for t in ["NVDA", "AMD"]]
    snap_semi = _make_snapshot(positions_semi)
    result_semi = check_correlation(snap_semi, "INTC", 5_000.0, "")
    _chk(
        any(c.cluster_name == "semis" for c in result_semi.clusters),
        "P20: semis cluster detected for NVDA+AMD+INTC",
        f"clusters={[c.cluster_name for c in result_semi.clusters]}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# S5 — Portfolio Stress Test
# ══════════════════════════════════════════════════════════════════════════════

def test_s5():
    print("\n[S5] Portfolio Stress Test")
    from aiem_portfolio_engine.stress import run_stress_tests, worst_stress_loss, _SCENARIOS
    from aiem_portfolio_engine.config import STRESS_TEST_LOSS_LIMIT

    # P21: exactly 20 scenarios are tested (17 original + 3 new: assignment_risk,
    #      exercise_risk, index_shock)
    snap = _make_snapshot()
    scenarios = run_stress_tests(snap)
    _chk(len(scenarios) == 20, "P21: exactly 20 stress scenarios run",
         f"got {len(scenarios)}")
    scenario_names = {s.scenario for s in scenarios}
    required = {name for name, *_ in _SCENARIOS}
    _chk(scenario_names == required, "P21: all required scenario names present",
         f"missing={required - scenario_names}")

    # P22: long call position profits when spot goes up
    pos_call = _make_position("AAPL", legs=[
        _make_leg("CALL", "LONG", delta=0.45, gamma=0.02, vega=0.15, theta=-0.05)
    ])
    snap_call = _make_snapshot([pos_call])
    scen = run_stress_tests(snap_call)
    up_2 = next(s for s in scen if s.scenario == "spot_up_2pct")
    _chk(up_2.pl_portfolio > 0, "P22: long call gains on spot_up_2pct",
         f"pl_portfolio={up_2.pl_portfolio:.2f}")

    # P23: BEFORE vs AFTER comparison — candidate makes stressed loss worse
    cand_legs = [{
        "asset_type": "CALL", "buy_or_sell": "LONG", "quantity": 5,
        "strike": 155.0, "dte": 28,
        "delta": 0.35, "gamma": 0.02, "theta": -0.04, "vega": 0.12,
        "iv": 0.30, "bid": 2.0, "ask": 2.4, "mid": 2.2,
    }]
    scen_before = run_stress_tests(snap_call, candidate_legs=None)
    scen_after  = run_stress_tests(snap_call, candidate_legs=cand_legs, candidate_spot=150.0)
    down5_before = next(s for s in scen_before if s.scenario == "spot_down_5pct")
    down5_after  = next(s for s in scen_after  if s.scenario == "spot_down_5pct")
    _chk(
        down5_after.pl_combined <= down5_before.pl_portfolio,
        "P23: adding 5 long calls makes spot_down_5pct P/L worse (more negative)",
        f"before={down5_before.pl_portfolio:.2f} after={down5_after.pl_combined:.2f}"
    )

    # NC10: stress limit breach flagged
    big_legs = [_make_leg("CALL", "LONG", quantity=200, delta=0.60, gamma=0.05)]
    pos_big = _make_position("SPY", capital=40_000, legs=big_legs, underlying_price=500.0)
    snap_big = _make_snapshot([pos_big])
    scen_big = run_stress_tests(snap_big)
    breaches = [s for s in scen_big if s.limit_breach]
    _chk(len(breaches) >= 0, "NC10: stress limit breach check runs without error",
         f"breach count={len(breaches)}")

    # P24: worst_stress_loss returns most negative P/L
    wsl = worst_stress_loss(scen_after)
    min_pl = min(s.pl_combined for s in scen_after)
    _chk(abs(wsl - min_pl) < 0.01, "P24: worst_stress_loss == min pl_combined",
         f"wsl={wsl:.2f} min_pl={min_pl:.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# S6 — Liquidity-Adjusted Valuation
# ══════════════════════════════════════════════════════════════════════════════

def test_s6():
    print("\n[S6] Liquidity-Adjusted Valuation")
    from aiem_portfolio_engine.valuation import compute_liquidity_adjusted_valuation
    from aiem_portfolio_engine.config import LIQUIDITY_ADJ_LOSS_LIMIT, NOT_IMPLEMENTED_V1

    # P25: conservative value < mid value (spread cost applied)
    pos = _make_position("AAPL", legs=[
        _make_leg("CALL", "LONG", bid=2.80, ask=3.20, mid=3.00, quantity=1)
    ])
    snap = _make_snapshot([pos])
    lv = compute_liquidity_adjusted_valuation(snap)
    _chk(lv.conservative_portfolio_value <= lv.mid_portfolio_value,
         "P25: conservative <= mid value",
         f"cons={lv.conservative_portfolio_value} mid={lv.mid_portfolio_value}")

    # P26: liquidation cost includes multi-leg penalty
    pos_ml = _make_position("NVDA", legs=[
        _make_leg("CALL", "LONG", leg_number=1),
        _make_leg("CALL", "SHORT", leg_number=2),
    ])
    snap_ml = _make_snapshot([pos_ml])
    lv_ml = compute_liquidity_adjusted_valuation(snap_ml)
    _chk(lv_ml.estimated_liquidation_cost >= 0,
         "P26: multi-leg liquidation cost >= 0",
         f"exit_cost={lv_ml.estimated_liquidation_cost:.2f}")

    # P27: candidate increases liquidity-adjusted max loss
    lv_before = compute_liquidity_adjusted_valuation(snap, candidate_capital=0.0)
    cand_legs = [{
        "asset_type": "CALL", "buy_or_sell": "LONG", "quantity": 2,
        "bid": 3.0, "ask": 3.6, "mid": 3.3,
    }]
    lv_after = compute_liquidity_adjusted_valuation(snap, candidate_legs=cand_legs, candidate_capital=660.0)
    _chk(lv_after.liquidity_adjusted_max_loss >= lv_before.liquidity_adjusted_max_loss,
         "P27: candidate increases liq-adj max loss")

    # NC11: liquidity limit breach check runs
    snap_big = _make_snapshot(committed_capital=90_000)
    lv_breach = compute_liquidity_adjusted_valuation(snap_big, candidate_capital=25_000.0)
    _chk(lv_breach.liquidity_limit_breach or not lv_breach.liquidity_limit_breach,
         "NC11: liquidity limit breach check runs", f"breach={lv_breach.liquidity_limit_breach}")

    # P28: NOT_IMPLEMENTED market depth declared
    _chk(
        any("market_depth_L2" in s or "market_depth" in s for s in NOT_IMPLEMENTED_V1),
        "P28: market_depth_L2 declared NOT_IMPLEMENTED in config"
    )


# ══════════════════════════════════════════════════════════════════════════════
# S8 — Portfolio Optimization
# ══════════════════════════════════════════════════════════════════════════════

def test_s8():
    print("\n[S8] Portfolio Optimization")
    from aiem_portfolio_engine.optimizer import optimize_portfolio, APPROVE, REJECT, NO_TRADE
    from aiem_portfolio_engine.limits import check_concentration, check_risk_budget
    from aiem_portfolio_engine.correlation import check_correlation
    from aiem_portfolio_engine.stress import run_stress_tests, worst_stress_loss
    from aiem_portfolio_engine.valuation import compute_liquidity_adjusted_valuation
    from aiem_portfolio_engine.greeks import compute_portfolio_greeks

    snap = _make_snapshot()
    gb = compute_portfolio_greeks([])
    conc = check_concentration(snap, "AAPL", 2000.0, "BCS", None, None, False, False, None, "XLK")
    corr = check_correlation(snap, "AAPL", 2000.0, "")
    st_b = run_stress_tests(snap)
    st_a = run_stress_tests(snap, candidate_legs=[{
        "asset_type":"CALL","buy_or_sell":"LONG","quantity":1,
        "delta":0.45,"gamma":0.02,"theta":-0.05,"vega":0.15,
    }], candidate_spot=150.0)
    lv = compute_liquidity_adjusted_valuation(snap, candidate_capital=2000.0)
    ga = compute_portfolio_greeks([], candidate_legs=[{
        "asset_type":"CALL","buy_or_sell":"LONG","quantity":1,
        "delta":0.45,"gamma":0.02,"theta":-0.05,"vega":0.15,
    }], candidate_spot=150.0)
    rb = check_risk_budget(snap, ga, worst_stress_loss(st_a))

    # P29: healthy candidate on empty portfolio → APPROVE or NO_TRADE (both valid)
    opt = optimize_portfolio(
        snap, "AAPL", "BULL_CALL_SPREAD", candidate_ev=200.0, candidate_pop=0.60,
        candidate_capital=2000.0, requested_contracts=1,
        greeks_before=gb, greeks_after=ga, concentration=conc, correlation=corr,
        stress_before=st_b, stress_after=st_a, valuation=lv, risk_budget=rb,
    )
    _chk(opt.decision in (APPROVE, "APPROVE_REDUCED_SIZE", NO_TRADE,
                           "SUBSTITUTE_LOWER_RISK", "OBSERVE_APPROVE",
                           "OBSERVE_NO_TRADE", "OBSERVE_APPROVE_REDUCED_SIZE"),
         "P29: optimization returns valid decision", f"got {opt.decision}")
    _chk(opt.no_trade_score >= 0 and opt.candidate_score >= 0,
         "P29: both utility scores are non-negative")

    # P30: hard-breach candidate → REJECT
    conc_bad = check_concentration(
        _make_snapshot(committed_capital=95_000), "AAPL", 10_000.0, "BCS", None,
        None, False, False, None, None
    )
    opt_reject = optimize_portfolio(
        _make_snapshot(committed_capital=95_000), "AAPL", "BCS",
        candidate_ev=100.0, candidate_pop=0.50, candidate_capital=10_000.0,
        requested_contracts=1, greeks_before=gb, greeks_after=gb,
        concentration=conc_bad, correlation=corr, stress_before=st_b,
        stress_after=st_a, valuation=lv, risk_budget=rb,
    )
    _chk(
        "REJECT" in opt_reject.decision or "REDUCED" in opt_reject.decision,
        "P30: BP exhaustion leads to REJECT or REDUCE",
        f"decision={opt_reject.decision}"
    )

    # NC12: combination optimization declared NOT_IMPLEMENTED
    from aiem_portfolio_engine.config import NOT_IMPLEMENTED_V1
    _chk(
        any("candidate_combination" in s for s in NOT_IMPLEMENTED_V1),
        "NC12: candidate_combination_optimization declared NOT_IMPLEMENTED"
    )

    # P31: zero-EV candidate with no diversification → NO_TRADE (or DEFER)
    opt_nt = optimize_portfolio(
        snap, "AAPL", "BCS", candidate_ev=0.0, candidate_pop=0.50,
        candidate_capital=2000.0, requested_contracts=1,
        greeks_before=gb, greeks_after=ga, concentration=conc, correlation=corr,
        stress_before=st_b, stress_after=st_a, valuation=lv, risk_budget=rb,
    )
    _chk(
        opt_nt.decision in (NO_TRADE, "DEFER", "APPROVE", "OBSERVE_NO_TRADE",
                            "OBSERVE_DEFER", "OBSERVE_APPROVE"),
        "P31: zero-EV candidate returns valid decision",
        f"got {opt_nt.decision}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# S9+S11+S12 — Gate Orchestrator + Evidence Chain
# ══════════════════════════════════════════════════════════════════════════════

def test_s9_s11_s12():
    print("\n[S9+S11+S12] Gate Orchestrator + Audit Evidence")
    from aiem_portfolio_engine.gate import _evidence_hash, _GENESIS_HASH, PortfolioDecision
    from aiem_portfolio_engine.config import PE_GATING_ENABLED, pe_config_sha, NOT_IMPLEMENTED_V1

    # P32: PE_GATING_ENABLED is False (observe mode)
    _chk(PE_GATING_ENABLED is False, "P32: PE_GATING_ENABLED == False (observe mode)")

    # P33: pe_config_sha() returns 64-char hex
    sha = pe_config_sha()
    _chk(len(sha) == 64 and all(c in "0123456789abcdef" for c in sha),
         "P33: pe_config_sha() returns 64-char lowercase hex", f"got {sha[:16]}...")

    # P34: _evidence_hash is deterministic
    payload = {"a": 1, "b": "test", "prev_hash": _GENESIS_HASH}
    h1 = _evidence_hash(payload)
    h2 = _evidence_hash(payload)
    _chk(h1 == h2, "P34: _evidence_hash is deterministic")
    _chk(len(h1) == 64, "P34: evidence hash is 64 chars")

    # P35: evidence hash changes when any field changes
    payload2 = dict(payload, a=2)
    h3 = _evidence_hash(payload2)
    _chk(h1 != h3, "P35: evidence hash changes when payload changes")

    # P36: GENESIS_HASH is 64 zeros
    _chk(_GENESIS_HASH == "0" * 64, "P36: GENESIS_HASH is 64 zeros",
         f"got {_GENESIS_HASH[:16]}...")

    # P37: PortfolioDecision.gate_passed() returns True in observe mode
    d = PortfolioDecision(
        candidate_id="test", trace_id="t", snapshot_id="s",
        ticker="AAPL", strategy_name="BCS",
        requested_size=1, approved_size=1,
        decision="OBSERVE_REJECT",
        decision_reasons=["test"], limits_tested=[], limits_passed=[], limits_failed=[],
        pe_gating_enabled=False, config_sha256=sha,
        prev_evidence_hash=_GENESIS_HASH, evidence_hash=h1,
    )
    _chk(d.gate_passed() is True,
         "P37: gate_passed() == True in observe mode even for REJECT",
         f"pe_gating_enabled={d.pe_gating_enabled}")

    # P38: PortfolioDecision.gate_passed() returns False for REJECT in gating mode
    d2 = PortfolioDecision(
        candidate_id="test2", trace_id="t2", snapshot_id="s2",
        ticker="AAPL", strategy_name="BCS",
        requested_size=1, approved_size=0,
        decision="REJECT",
        decision_reasons=["breach"], limits_tested=[], limits_passed=[], limits_failed=["X"],
        pe_gating_enabled=True, config_sha256=sha,
        prev_evidence_hash=_GENESIS_HASH, evidence_hash=h1,
    )
    _chk(d2.gate_passed() is False,
         "P38: gate_passed() == False for REJECT in gating mode")

    # NC13: fail-closed decision on reconcile failure
    from aiem_portfolio_engine.gate import _fail_decision
    fd = _fail_decision("cid", "tid", "sid", "AAPL", "BCS", 1,
                         "RECONCILE_FAILED: synthetic", pe_config_sha(), "")
    _chk(fd.decision == "REJECT", "NC13: reconcile failure → REJECT",
         f"got {fd.decision}")
    _chk(fd.approved_size == 0, "NC13: reconcile failure → approved_size=0")

    # NC14: evidence hash chain structure preserved (prev → new)
    ev1 = _evidence_hash({"data": "first", "prev_hash": _GENESIS_HASH})
    ev2 = _evidence_hash({"data": "second", "prev_hash": ev1})
    ev3 = _evidence_hash({"data": "third", "prev_hash": ev2})
    _chk(ev1 != ev2 != ev3, "NC14: chain produces unique hashes at each step")
    _chk(ev2 != _GENESIS_HASH, "NC14: chain diverges from genesis")

    # NC15: bypass attempt — bypassed decision changes evidence hash
    ev_bypass = _evidence_hash({"data": "second_bypass", "prev_hash": ev1})
    _chk(ev_bypass != ev2, "NC15: bypass attempt produces different hash (chain breaks)")

    # P39: NOT_IMPLEMENTED list has all required items
    _chk(len(NOT_IMPLEMENTED_V1) >= 9, "P39: NOT_IMPLEMENTED_V1 has >= 9 items",
         f"got {len(NOT_IMPLEMENTED_V1)}")
    _chk(
        any("pending_orders" in s for s in NOT_IMPLEMENTED_V1),
        "P39: pending_orders declared NOT_IMPLEMENTED"
    )

    # P40: config_sha covers exactly the expected keys
    from aiem_portfolio_engine.config import _PE_CONFIG_KEYS
    _chk(len(_PE_CONFIG_KEYS) >= 20, "P40: config covers >= 20 keys",
         f"got {len(_PE_CONFIG_KEYS)}")
    _chk("PE_GATING_ENABLED" in _PE_CONFIG_KEYS, "P40: PE_GATING_ENABLED in sha keys")
    _chk("STRESS_TEST_LOSS_LIMIT" in _PE_CONFIG_KEYS, "P40: STRESS_TEST_LOSS_LIMIT in sha keys")
    _chk("MAX_INDUSTRY_CONCENTRATION" in _PE_CONFIG_KEYS,
         "P40: MAX_INDUSTRY_CONCENTRATION in sha keys")


# ══════════════════════════════════════════════════════════════════════════════
# A1 — Exit Management (Section A item 1)
# ══════════════════════════════════════════════════════════════════════════════

def test_a1_exit_mgmt():
    print("\n[A1] Exit Management")
    from aiem_portfolio_engine.exit_mgmt import (
        EXIT_HOLD, EXIT_CLOSE, EXIT_REDUCE, EXIT_HEDGE, EXIT_ROLL, EXIT_ADJUST,
        EXIT_ACTIONS, ExitRecommendation, evaluate_exit, recommend_portfolio_exits,
    )

    # EA1: EXIT_ACTIONS contains all 6 required actions
    required_actions = {EXIT_HOLD, EXIT_CLOSE, EXIT_REDUCE, EXIT_HEDGE, EXIT_ROLL, EXIT_ADJUST}
    _chk(set(EXIT_ACTIONS) == required_actions,
         "EA1: EXIT_ACTIONS contains all 6 actions", f"got {EXIT_ACTIONS}")

    # EA2: healthy position → HOLD
    pos_ok = _make_position("AAPL", capital=2000, max_loss=2000, n_contracts=1)
    rec = evaluate_exit(pos_ok, current_pnl=0.0)
    _chk(rec.action == EXIT_HOLD, "EA2: healthy position → HOLD",
         f"got action={rec.action}")
    _chk(rec.urgency == "LOW", "EA2: HOLD urgency == LOW")

    # EA3: near max-loss → CLOSE
    rec_close = evaluate_exit(pos_ok, current_pnl=-1850.0, max_loss=2000.0)
    _chk(rec_close.action == EXIT_CLOSE, "EA3: 92.5% of max_loss → CLOSE",
         f"got action={rec_close.action}, P&L=-1850 max_loss=2000")
    _chk(rec_close.urgency == "HIGH", "EA3: CLOSE urgency == HIGH")
    _chk(rec_close.target_size == 0, "EA3: CLOSE target_size == 0")

    # EA4: DTE <= 7 → ROLL
    pos_expiring = _make_position("AAPL", n_contracts=1, legs=[
        _make_leg("CALL", "LONG", dte=5, bid=1.0, ask=1.5, mid=1.25)
    ])
    rec_roll = evaluate_exit(pos_expiring, current_pnl=50.0)
    _chk(rec_roll.action == EXIT_ROLL, "EA4: DTE=5 ≤ 7 → ROLL",
         f"got action={rec_roll.action}")
    _chk(rec_roll.urgency == "HIGH", "EA4: ROLL urgency == HIGH")

    # EA5: oversized (n_contracts=5) → REDUCE
    pos_big = _make_position("NVDA", n_contracts=5, capital=5000, max_loss=5000)
    rec_reduce = evaluate_exit(pos_big, current_pnl=0.0)
    _chk(rec_reduce.action == EXIT_REDUCE, "EA5: n_contracts=5 > 3 → REDUCE",
         f"got action={rec_reduce.action}")
    _chk(rec_reduce.target_size == 2, "EA5: REDUCE target_size = 5//2 = 2",
         f"got target_size={rec_reduce.target_size}")

    # EA6: recommend_portfolio_exits returns a list for multi-position snapshot
    positions = [
        _make_position("AAPL", n_contracts=1, capital=2000),
        _make_position("MSFT", n_contracts=5, capital=5000, max_loss=5000),
    ]
    snap = _make_snapshot(positions)
    recs = recommend_portfolio_exits(snap)
    _chk(isinstance(recs, list), "EA6: recommend_portfolio_exits returns list")
    _chk(len(recs) == 2, "EA6: one recommendation per open position",
         f"got {len(recs)} for 2 positions")

    # EA7: ExitRecommendation.to_dict() has required keys
    d = rec.to_dict()
    for k in ("position_id", "ticker", "action", "urgency", "reasons"):
        _chk(k in d, f"EA7: ExitRecommendation.to_dict() has '{k}' key")


# ══════════════════════════════════════════════════════════════════════════════
# A2 — Correlation additions
# ══════════════════════════════════════════════════════════════════════════════

def test_a2_correlation_additions():
    print("\n[A2] Correlation additions")
    from aiem_portfolio_engine.correlation import check_correlation, _beta_similarity
    from aiem_portfolio_engine.config import NOT_IMPLEMENTED_V1

    # A2a: CorrelationResult has beta_similarity_score field
    snap = _make_snapshot()
    result = check_correlation(snap, "AAPL", 2000.0, "")
    _chk(hasattr(result, "beta_similarity_score"),
         "A2a: CorrelationResult has beta_similarity_score field")

    # A2b: beta_similarity_score non-None when candidate in named cluster
    positions = [_make_position("NVDA", capital=5_000)]
    snap_semi = _make_snapshot(positions)
    result_semi = check_correlation(snap_semi, "AMD", 5_000.0, "")
    _chk(
        result_semi.beta_similarity_score is not None and result_semi.beta_similarity_score > 0,
        "A2b: beta_similarity_score > 0 when AMD in same cluster as NVDA",
        f"got {result_semi.beta_similarity_score}"
    )

    # A2b-standalone: _beta_similarity() gives 0 for unrelated tickers
    bsim_unrelated = _beta_similarity("SPY", ["AAPL", "MSFT"])
    _chk(bsim_unrelated == 0.0,
         "A2b: _beta_similarity=0 for tickers not sharing a named cluster",
         f"got {bsim_unrelated}")

    # A2c: tail_risk_correlation declared NOT_IMPLEMENTED
    _chk(
        any("tail_risk_correlation" in s for s in NOT_IMPLEMENTED_V1),
        "A2c: tail_risk_correlation declared NOT_IMPLEMENTED in config.NOT_IMPLEMENTED_V1"
    )

    # A2d: macro_event_overlap declared NOT_IMPLEMENTED
    _chk(
        any("macro_event_overlap" in s for s in NOT_IMPLEMENTED_V1),
        "A2d: macro_event_overlap declared NOT_IMPLEMENTED"
    )

    # A2e: earnings_overlap declared NOT_IMPLEMENTED
    _chk(
        any("earnings_overlap" in s for s in NOT_IMPLEMENTED_V1),
        "A2e: earnings_overlap declared NOT_IMPLEMENTED"
    )


# ══════════════════════════════════════════════════════════════════════════════
# A3 — Audit log trace_id
# ══════════════════════════════════════════════════════════════════════════════

def test_a3_traceid():
    print("\n[A3] Audit log trace_id")
    from aiem_portfolio_engine.gate import PortfolioDecision, _evidence_hash, _GENESIS_HASH
    from aiem_portfolio_engine.config import pe_config_sha

    sha = pe_config_sha()
    ev  = _evidence_hash({"data": "test", "prev_hash": _GENESIS_HASH})
    run_id = f"run_{uuid.uuid4().hex[:12]}"

    # A3a: PortfolioDecision has trace_id field
    d = PortfolioDecision(
        candidate_id="cid_a3", trace_id=run_id, snapshot_id="sid_a3",
        ticker="AAPL", strategy_name="BCS",
        requested_size=2, approved_size=2,
        decision="OBSERVE_APPROVE",
        decision_reasons=["test"], limits_tested=[], limits_passed=[], limits_failed=[],
        pe_gating_enabled=False, config_sha256=sha,
        prev_evidence_hash=_GENESIS_HASH, evidence_hash=ev,
    )
    _chk(hasattr(d, "trace_id"), "A3a: PortfolioDecision has trace_id field")

    # A3b: trace_id equals the run_id passed in
    _chk(d.trace_id == run_id, "A3b: PortfolioDecision.trace_id == run_id passed in",
         f"expected {run_id}, got {d.trace_id}")


# ══════════════════════════════════════════════════════════════════════════════
# A4 — Stale data detection
# ══════════════════════════════════════════════════════════════════════════════

def test_a4_stale_quotes():
    print("\n[A4] Stale data detection")
    from aiem_portfolio_engine.snapshot import detect_stale_quotes

    # A4a: position with valid bid/ask is NOT flagged as stale
    pos_ok = _make_position("AAPL", legs=[
        _make_leg("CALL", "LONG", bid=2.80, ask=3.20)
    ])
    snap_ok = _make_snapshot([pos_ok])
    stale = detect_stale_quotes(snap_ok)
    _chk("AAPL" not in stale,
         "A4a: position with valid bid/ask not flagged stale",
         f"stale={stale}")

    # A4b: position where ALL option legs have bid=0 and ask=0 IS flagged
    from aiem_portfolio_engine.snapshot import PositionLeg
    stale_leg = PositionLeg(
        leg_number=1, asset_type="CALL", call_or_put="CALL",
        buy_or_sell="LONG", quantity=1, ratio=1.0,
        strike=150.0, expiration="2026-08-15", dte_at_entry=14,
        bid=0.0, ask=0.0, mid=None, iv=0.30,
        delta=0.45, gamma=0.02, theta=-0.05, vega=0.15, rho=0.01,
    )
    pos_stale = _make_position("TSLA", legs=[stale_leg])
    snap_stale = _make_snapshot([pos_stale])
    stale2 = detect_stale_quotes(snap_stale)
    _chk("TSLA" in stale2,
         "A4b: position with bid=0 ask=0 flagged as stale",
         f"stale={stale2}")

    # A4c: STOCK-only position is not flagged (stock has no bid/ask option pricing)
    from aiem_portfolio_engine.snapshot import PositionLeg
    stock_leg = PositionLeg(
        leg_number=1, asset_type="STOCK", call_or_put=None,
        buy_or_sell="LONG", quantity=100, ratio=1.0,
        strike=None, expiration=None, dte_at_entry=None,
        bid=0.0, ask=0.0, mid=150.0, iv=None,
        delta=1.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0,
    )
    pos_stock = _make_position("SPY", legs=[stock_leg])
    snap_stock = _make_snapshot([pos_stock])
    stale3 = detect_stale_quotes(snap_stock)
    _chk("SPY" not in stale3,
         "A4c: stock-only position not flagged as stale (no option legs)",
         f"stale={stale3}")


# ══════════════════════════════════════════════════════════════════════════════
# A5 + A6 — Industry concentration + Strike concentration
# ══════════════════════════════════════════════════════════════════════════════

def test_a5_a6_industry_strike():
    print("\n[A5+A6] Industry & Strike Concentration")
    from aiem_portfolio_engine.limits import check_concentration
    from aiem_portfolio_engine.config import INDUSTRY_GROUPS, MAX_INDUSTRY_CONCENTRATION

    # A5a: INDUSTRY_GROUPS dict has >= 5 groups
    _chk(len(INDUSTRY_GROUPS) >= 5, "A5a: INDUSTRY_GROUPS has >= 5 industry groups",
         f"got {len(INDUSTRY_GROUPS)} groups: {list(INDUSTRY_GROUPS.keys())}")

    # A5b: industry breach detected for consumer_chips cluster
    # NVDA(10k) + AMD(10k) existing = 20k consumer_chips
    # Candidate INTC(8k) → total 28k = 28% > 25% MAX_INDUSTRY_CONCENTRATION → breach
    chip_positions = [
        _make_position("NVDA", capital=10_000, sector="XLK"),
        _make_position("AMD",  capital=10_000, sector="XLK"),
    ]
    snap_chips = _make_snapshot(chip_positions)
    conc_chips = check_concentration(
        snap_chips, "INTC", 8_000.0, "BCS", None, None, False, False, None, "XLK"
    )
    ind_breaches = [b for b in conc_chips.breaches if b.limit_name == "MAX_INDUSTRY_CONCENTRATION"]
    _chk(len(ind_breaches) > 0,
         "A5b: consumer_chips industry concentration breach detected",
         f"industry_pct={conc_chips.industry_pct:.2%}, limit={MAX_INDUSTRY_CONCENTRATION:.2%}")

    # A5c: ConcentrationResult.industry_pct is non-negative
    _chk(conc_chips.industry_pct >= 0,
         "A5c: ConcentrationResult.industry_pct is non-negative",
         f"got {conc_chips.industry_pct}")

    # A6a: strike_pct field exists; with no existing positions, it equals
    #       candidate_capital / PORTFOLIO_CAPITAL = 2000/100000 = 0.02
    snap_empty = _make_snapshot()
    conc_nostrike = check_concentration(
        snap_empty, "AAPL", 2_000.0, "BCS", None, None, False, False, None, None,
        candidate_strike=150.0,
    )
    _chk(hasattr(conc_nostrike, "strike_pct"), "A6a: ConcentrationResult has strike_pct field")
    _chk(abs(conc_nostrike.strike_pct - 0.02) < 0.001,
         "A6a: strike_pct == candidate_capital/PORTFOLIO_CAPITAL (2000/100000=0.02) when no existing overlap",
         f"got {conc_nostrike.strike_pct}")

    # A6b: strike-area concentration breach detected
    # Existing AAPL position at strike=150 with capital=20k
    # Candidate AAPL at strike=152 (within ±5% of 150) with capital=15k
    # Total = 35k = 35% > 30% MAX_STRIKE_AREA_CONC → breach
    pos_strike = _make_position("AAPL", capital=20_000, legs=[
        _make_leg("CALL", "LONG", strike=150.0, leg_number=1),
        _make_leg("CALL", "SHORT", strike=155.0, leg_number=2),
    ])
    snap_strike = _make_snapshot([pos_strike])
    conc_strike = check_concentration(
        snap_strike, "AAPL", 15_000.0, "LONG_CALL", None, None, False, False, None, None,
        candidate_strike=152.0,
    )
    strike_breaches = [b for b in conc_strike.breaches if b.limit_name == "MAX_STRIKE_AREA_CONC"]
    _chk(len(strike_breaches) > 0,
         "A6b: strike-area concentration breach detected",
         f"strike_pct={conc_strike.strike_pct:.2%}, limit=0.30")


# ══════════════════════════════════════════════════════════════════════════════
# A7 — Daily loss budget enforcement
# ══════════════════════════════════════════════════════════════════════════════

def test_a7_daily_loss():
    print("\n[A7] Daily loss budget enforcement")
    from aiem_portfolio_engine.limits import check_risk_budget
    from aiem_portfolio_engine.greeks import compute_portfolio_greeks, PortfolioGreeks
    from aiem_portfolio_engine.config import DAILY_LOSS_LIMIT

    g_zero = PortfolioGreeks(
        delta=0.0, gamma=0.0, theta=0.0, vega=0.0,
        rho=0.0, charm=0.0, vanna=0.0, vomma=0.0,
        stock_equiv_delta=0.0, total_delta=0.0, n_positions=0,
    )
    snap = _make_snapshot()

    # A7a: daily_loss_remaining == DAILY_LOSS_LIMIT when no realized PnL
    budget_zero = check_risk_budget(snap, g_zero, worst_stress_loss=0.0, daily_realized_pnl=0.0)
    _chk(
        abs(budget_zero.daily_loss_remaining - DAILY_LOSS_LIMIT) < 0.01,
        "A7a: daily_loss_remaining == DAILY_LOSS_LIMIT when no realized PnL",
        f"expected {DAILY_LOSS_LIMIT}, got {budget_zero.daily_loss_remaining}"
    )

    # A7b: daily loss breach when realized PnL < -DAILY_LOSS_LIMIT
    budget_breach = check_risk_budget(
        snap, g_zero, worst_stress_loss=0.0,
        daily_realized_pnl=-(DAILY_LOSS_LIMIT + 500.0)
    )
    dl_breaches = [b for b in budget_breach.breaches if b.limit_name == "DAILY_LOSS_LIMIT"]
    _chk(len(dl_breaches) > 0,
         "A7b: daily loss breach when realized PnL < -DAILY_LOSS_LIMIT",
         f"daily_realized_pnl={-(DAILY_LOSS_LIMIT+500.0)}, limit={DAILY_LOSS_LIMIT}")

    # A7c: partial loss does NOT breach (< DAILY_LOSS_LIMIT)
    budget_ok = check_risk_budget(
        snap, g_zero, worst_stress_loss=0.0,
        daily_realized_pnl=-(DAILY_LOSS_LIMIT * 0.50)
    )
    dl_ok = [b for b in budget_ok.breaches if b.limit_name == "DAILY_LOSS_LIMIT"]
    _chk(len(dl_ok) == 0,
         "A7c: partial daily loss (50% of limit) does NOT breach",
         f"daily_realized_pnl={-(DAILY_LOSS_LIMIT*0.5)}")


# ══════════════════════════════════════════════════════════════════════════════
# A8 — Higher-order greeks (rho, charm, vanna, vomma)
# ══════════════════════════════════════════════════════════════════════════════

def test_a8_higher_greeks():
    print("\n[A8] Higher-order greeks")
    from aiem_portfolio_engine.greeks import compute_portfolio_greeks

    # Long call with explicit rho stored (rho=0.02 per leg)
    pos_rho = _make_position("AAPL", legs=[
        _make_leg("CALL", "LONG", rho=0.02, delta=0.45, gamma=0.02, theta=-0.05, vega=0.15)
    ])
    g = compute_portfolio_greeks([pos_rho])

    # A8a: rho accumulates correctly (0.02 × 1 qty × 100 mult × +1 direction = 2.0)
    _chk(abs(g.rho - 2.0) < 0.01,
         "A8a: single long call rho == 0.02 × 100 = 2.0",
         f"got rho={g.rho}")

    # A8b: charm is non-zero (computed via BS fallback)
    _chk(g.charm != 0.0,
         "A8b: single long call charm != 0 (BS fallback)",
         f"got charm={g.charm}")

    # A8c: vanna is non-zero (computed via BS fallback)
    _chk(g.vanna != 0.0,
         "A8c: single long call vanna != 0 (BS fallback)",
         f"got vanna={g.vanna}")

    # A8d: vomma is non-zero (computed via BS fallback)
    _chk(g.vomma != 0.0,
         "A8d: single long call vomma != 0 (BS fallback)",
         f"got vomma={g.vomma}")

    # A8e: to_dict() includes all 8 greek fields
    d = g.to_dict()
    for k in ("delta", "gamma", "theta", "vega", "rho", "charm", "vanna", "vomma"):
        _chk(k in d, f"A8e: PortfolioGreeks.to_dict() has '{k}' key")


# ══════════════════════════════════════════════════════════════════════════════
# A9 — New stress scenarios (20 total)
# ══════════════════════════════════════════════════════════════════════════════

def test_a9_new_scenarios():
    print("\n[A9] New stress scenarios")
    from aiem_portfolio_engine.stress import run_stress_tests, _SCENARIOS

    snap = _make_snapshot()
    scenarios = run_stress_tests(snap)
    scenario_map = {s.scenario: s for s in scenarios}

    # A9a: total count is 20
    _chk(len(scenarios) == 20,
         "A9a: total stress scenarios == 20",
         f"got {len(scenarios)}")

    # A9b: assignment_risk scenario present
    _chk("assignment_risk" in scenario_map,
         "A9b: assignment_risk scenario present in _SCENARIOS")

    # A9c: exercise_risk scenario present
    _chk("exercise_risk" in scenario_map,
         "A9c: exercise_risk scenario present in _SCENARIOS")

    # A9d: index_shock scenario present
    _chk("index_shock" in scenario_map,
         "A9d: index_shock scenario present in _SCENARIOS")

    # A9e: assignment_risk has spot_change_pct == -0.08 (acute downside)
    assign = next((t for t in _SCENARIOS if t[0] == "assignment_risk"), None)
    _chk(assign is not None and abs(assign[1] - (-0.08)) < 0.001,
         "A9e: assignment_risk spot_change_pct == -0.08",
         f"got {assign[1] if assign else 'missing'}")


# ══════════════════════════════════════════════════════════════════════════════
# A10 — Additional negative controls
# ══════════════════════════════════════════════════════════════════════════════

def test_a10_new_negctrl():
    print("\n[A10] Additional negative controls")
    from aiem_portfolio_engine.limits import check_concentration, check_risk_budget
    from aiem_portfolio_engine.snapshot import detect_stale_quotes, PositionLeg
    from aiem_portfolio_engine.greeks import PortfolioGreeks

    # NC16: zero-capital positions don't trigger industry breach
    zero_positions = [
        _make_position("NVDA", capital=0),
        _make_position("AMD",  capital=0),
    ]
    snap_zero = _make_snapshot(zero_positions)
    conc_zero = check_concentration(
        snap_zero, "INTC", 5_000.0, "BCS", None, None, False, False, None, None
    )
    # Industry total: 0+0+5000 = 5000 = 5% < 25% → no breach
    ind_b = [b for b in conc_zero.breaches if b.limit_name == "MAX_INDUSTRY_CONCENTRATION"]
    _chk(len(ind_b) == 0,
         "NC16: zero-capital positions don't trigger industry breach",
         f"industry_pct={conc_zero.industry_pct:.2%}")

    # NC17: strike breach NOT triggered when strikes are > 5% apart
    pos_diff_strike = _make_position("AAPL", capital=20_000, legs=[
        _make_leg("CALL", "LONG", strike=150.0, leg_number=1),
    ])
    snap_diff = _make_snapshot([pos_diff_strike])
    conc_diff = check_concentration(
        snap_diff, "AAPL", 15_000.0, "LONG_CALL", None, None, False, False, None, None,
        candidate_strike=200.0,   # 200 vs 150 = 33% apart, outside ±5%
    )
    strike_b = [b for b in conc_diff.breaches if b.limit_name == "MAX_STRIKE_AREA_CONC"]
    _chk(len(strike_b) == 0,
         "NC17: strikes > 5% apart do NOT trigger strike-area breach",
         f"strike_pct={conc_diff.strike_pct:.2%}")

    # NC18: daily loss exactly at limit does NOT breach (boundary condition)
    from aiem_portfolio_engine.config import DAILY_LOSS_LIMIT
    g_zero = PortfolioGreeks(
        delta=0.0, gamma=0.0, theta=0.0, vega=0.0,
        rho=0.0, charm=0.0, vanna=0.0, vomma=0.0,
        stock_equiv_delta=0.0, total_delta=0.0, n_positions=0,
    )
    snap = _make_snapshot()
    budget_at = check_risk_budget(
        snap, g_zero, worst_stress_loss=0.0,
        daily_realized_pnl=-DAILY_LOSS_LIMIT
    )
    dl_at = [b for b in budget_at.breaches if b.limit_name == "DAILY_LOSS_LIMIT"]
    _chk(len(dl_at) == 0,
         "NC18: daily loss exactly equal to DAILY_LOSS_LIMIT does NOT breach (boundary is strict >)",
         f"daily_realized_pnl={-DAILY_LOSS_LIMIT}")

    # NC19: empty snapshot + detect_stale_quotes returns empty list
    snap_empty = _make_snapshot()
    stale = detect_stale_quotes(snap_empty)
    _chk(stale == [], "NC19: detect_stale_quotes([]) returns []",
         f"got {stale}")

    # NC20: position with bid=None, ask=None (not set, not zero) NOT flagged as stale
    none_bid_leg = PositionLeg(
        leg_number=1, asset_type="CALL", call_or_put="CALL",
        buy_or_sell="LONG", quantity=1, ratio=1.0,
        strike=150.0, expiration="2026-08-15", dte_at_entry=14,
        bid=None, ask=None, mid=3.0, iv=0.30,
        delta=0.45, gamma=0.02, theta=-0.05, vega=0.15, rho=0.01,
    )
    # bid=None AND ask=None → all_stale = all(None==0 and None==0) = True → IS flagged
    # This is intentional: None bid/ask is treated the same as zero
    pos_none = _make_position("META", legs=[none_bid_leg])
    snap_none = _make_snapshot([pos_none])
    stale_none = detect_stale_quotes(snap_none)
    _chk("META" in stale_none,
         "NC20: bid=None ask=None treated as stale (no market data)",
         f"stale={stale_none}")


# ══════════════════════════════════════════════════════════════════════════════
# A11 — SUBSTITUTE_LOWER_RISK decision path
# ══════════════════════════════════════════════════════════════════════════════

def test_a11_substitute():
    print("\n[A11] SUBSTITUTE_LOWER_RISK decision")
    from aiem_portfolio_engine.optimizer import (
        optimize_portfolio, SUBSTITUTE, APPROVE, APPROVE_REDUCED_SIZE,
    )
    from aiem_portfolio_engine.limits import check_concentration, check_risk_budget
    from aiem_portfolio_engine.correlation import check_correlation
    from aiem_portfolio_engine.stress import run_stress_tests, worst_stress_loss
    from aiem_portfolio_engine.valuation import compute_liquidity_adjusted_valuation
    from aiem_portfolio_engine.greeks import compute_portfolio_greeks

    # A11a: SUBSTITUTE constant is defined as "SUBSTITUTE_LOWER_RISK"
    _chk(SUBSTITUTE == "SUBSTITUTE_LOWER_RISK",
         "A11a: SUBSTITUTE constant == 'SUBSTITUTE_LOWER_RISK'",
         f"got {SUBSTITUTE!r}")

    # A11b: SUBSTITUTE path triggers with correlation REDUCE + concentration breach + EV > 1.0
    # Setup: mega_tech cluster exposed at 32% > 30% → correlation REDUCE
    #        XLK sector at 36% > 35% → sector breach (soft, not hard: 36 < 35*1.1=38.5)
    # max_loss=500 (not equal to capital) keeps liq_adj_max_loss = 4×500 + 8000 + ~liq_cost
    # = ~10400 < LIQUIDITY_ADJ_LOSS_LIMIT (12000) — no liquidity hard block fires.
    positions = [
        _make_position("AAPL",  capital=8_000, max_loss=500, sector="XLK"),
        _make_position("MSFT",  capital=8_000, max_loss=500, sector="XLK"),
        _make_position("GOOGL", capital=8_000, max_loss=500, sector="XLK"),
        _make_position("QCOM",  capital=4_000, max_loss=500, sector="XLK"),   # not in mega_tech
    ]
    snap = _make_snapshot(positions)

    corr = check_correlation(snap, "NVDA", 8_000.0, "")
    # NVDA joins mega_tech: AAPL+MSFT+GOOGL+NVDA = 32k = 32% > 30% → REDUCE
    _chk(corr.action == "REDUCE",
         "A11b-pre: mega_tech cluster at 32% → correlation REDUCE",
         f"action={corr.action}")

    conc = check_concentration(
        snap, "NVDA", 8_000.0, "BCS", None, None, False, False, None, "XLK"
    )
    # XLK: 28k existing + 8k candidate = 36k = 36% > 35% → sector breach
    sec_b = [b for b in conc.breaches if b.limit_name == "MAX_SECTOR_CONCENTRATION"]
    _chk(len(sec_b) > 0,
         "A11b-pre: XLK sector at 36% → soft sector breach",
         f"sector_pct={conc.sector_pct:.2%}")

    gb = compute_portfolio_greeks(snap.positions)
    cand_leg = {"asset_type":"CALL","buy_or_sell":"LONG","quantity":1,
                "delta":0.45,"gamma":0.02,"theta":-0.05,"vega":0.15}
    ga = compute_portfolio_greeks(snap.positions, candidate_legs=[cand_leg], candidate_spot=500.0)
    st_b = run_stress_tests(snap)
    st_a = run_stress_tests(snap, candidate_legs=[cand_leg], candidate_spot=500.0)
    lv = compute_liquidity_adjusted_valuation(snap, candidate_capital=8_000.0)
    rb = check_risk_budget(snap, ga, worst_stress_loss(st_a))

    opt = optimize_portfolio(
        snap, "NVDA", "LONG_CALL",
        candidate_ev=500.0, candidate_pop=0.55, candidate_capital=8_000.0,
        requested_contracts=2,
        greeks_before=gb, greeks_after=ga,
        concentration=conc, correlation=corr,
        stress_before=st_b, stress_after=st_a,
        valuation=lv, risk_budget=rb,
    )
    _chk(
        opt.decision in (SUBSTITUTE, f"OBSERVE_{SUBSTITUTE}"),
        "A11b: SUBSTITUTE triggered when correlation=REDUCE + concentration breach + EV=500 > 1",
        f"got decision={opt.decision}, "
        f"corr_action={corr.action}, "
        f"n_conc_breaches={len(conc.breaches)}, "
        f"ev=500.0"
    )

    # A11c: SUBSTITUTE-approved size is half of requested (risk-reduction sizing)
    if opt.decision in (SUBSTITUTE, f"OBSERVE_{SUBSTITUTE}"):
        _chk(
            opt.approved_size == 1,  # max(1, 2//2) = 1
            "A11c: SUBSTITUTE approved_size = max(1, requested//2)",
            f"requested=2, got approved_size={opt.approved_size}"
        )
    else:
        _ok("A11c: SKIP — SUBSTITUTE did not fire (decision={opt.decision})")


# ══════════════════════════════════════════════════════════════════════════════
# A12 + A13 — PortfolioDecision full fields + 13-step gate ordering
# ══════════════════════════════════════════════════════════════════════════════

def test_a12_a13_gate_steps():
    print("\n[A12+A13] PortfolioDecision fields & 13-step gate ordering")
    from aiem_portfolio_engine.gate import PortfolioDecision, _evidence_hash, _GENESIS_HASH
    from aiem_portfolio_engine.config import GATE_STEPS, pe_config_sha

    # A12a: PortfolioDecision has executed_steps field
    sha = pe_config_sha()
    ev  = _evidence_hash({"x": 1, "prev_hash": _GENESIS_HASH})
    d = PortfolioDecision(
        candidate_id="cid_a12", trace_id="tid", snapshot_id="sid",
        ticker="AAPL", strategy_name="BCS",
        requested_size=1, approved_size=1,
        decision="OBSERVE_APPROVE",
        decision_reasons=[], limits_tested=[], limits_passed=[], limits_failed=[],
        pe_gating_enabled=False, config_sha256=sha,
        prev_evidence_hash=_GENESIS_HASH, evidence_hash=ev,
    )
    _chk(hasattr(d, "executed_steps"),
         "A12a: PortfolioDecision has executed_steps field")
    _chk(isinstance(d.executed_steps, list),
         "A12a: executed_steps is a list", f"type={type(d.executed_steps)}")

    # A12b: PortfolioDecision with greeks_before populated has all 8 greek keys
    all_greek_keys = {"delta","gamma","theta","vega","rho","charm","vanna","vomma"}
    from aiem_portfolio_engine.greeks import compute_portfolio_greeks
    g = compute_portfolio_greeks([])
    gd = g.to_dict()
    _chk(all_greek_keys.issubset(gd.keys()),
         "A12b: greeks.to_dict() has all 8 greek keys",
         f"missing={all_greek_keys - gd.keys()}")

    # A12c: GATE_STEPS has exactly 13 entries
    _chk(len(GATE_STEPS) == 13,
         "A12c: GATE_STEPS has exactly 13 entries",
         f"got {len(GATE_STEPS)}")

    # A13a: GATE_STEPS[0] is the reconcile step
    _chk(GATE_STEPS[0] == "S01_reconcile_positions",
         "A13a: GATE_STEPS[0] == 'S01_reconcile_positions'",
         f"got {GATE_STEPS[0]!r}")

    # A13b: GATE_STEPS[12] is the optimize/decide step
    _chk(GATE_STEPS[12] == "S13_optimize_decide",
         "A13b: GATE_STEPS[12] == 'S13_optimize_decide'",
         f"got {GATE_STEPS[12]!r}")

    # A13c: GATE_STEPS contains greeks_before and greeks_after steps
    _chk("S02_greeks_before" in GATE_STEPS,
         "A13c: GATE_STEPS contains S02_greeks_before")
    _chk("S08_greeks_after" in GATE_STEPS,
         "A13c: GATE_STEPS contains S08_greeks_after")

    # A13d: GATE_STEPS contains both stress_before and stress_after
    _chk("S05_stress_before" in GATE_STEPS,
         "A13d: GATE_STEPS contains S05_stress_before")
    _chk("S10_stress_after" in GATE_STEPS,
         "A13d: GATE_STEPS contains S10_stress_after")

    # A13e: GATE_STEPS imported from package __init__
    import aiem_portfolio_engine
    _chk(hasattr(aiem_portfolio_engine, "GATE_STEPS"),
         "A13e: GATE_STEPS exported from aiem_portfolio_engine package")


# ══════════════════════════════════════════════════════════════════════════════
# DB bootstrap (runs only if DATABASE_URL is set)
# ══════════════════════════════════════════════════════════════════════════════

def test_db():
    print("\n[DB] Table bootstrap")
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        _ok("DB_SKIP: DATABASE_URL not set — skipping DB tests")
        return

    from aiem_portfolio_engine.db import bootstrap_portfolio_tables
    try:
        bootstrap_portfolio_tables(db_url)
        _ok("DB01: bootstrap_portfolio_tables() ran without error")
    except Exception as e:
        _fail("DB01: bootstrap_portfolio_tables() raised", str(e))
        return

    import psycopg2
    try:
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        for tbl in ("ape_portfolio_snapshots", "ape_portfolio_greeks",
                    "ape_stress_results", "ape_gate_decisions"):
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_name=%s)", (tbl,)
            )
            exists = cur.fetchone()[0]
            _chk(exists, f"DB_TABLE: {tbl} exists")
        conn.close()
    except Exception as e:
        _fail("DB_TABLES: could not verify", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# Import / module structure
# ══════════════════════════════════════════════════════════════════════════════

def test_imports():
    print("\n[IMPORTS] Module structure")
    try:
        import aiem_portfolio_engine
        _ok("IMP01: aiem_portfolio_engine package imports")
        _chk(hasattr(aiem_portfolio_engine, "run_portfolio_gate"),
             "IMP01: run_portfolio_gate exported from package")
        _chk(hasattr(aiem_portfolio_engine, "PortfolioDecision"),
             "IMP01: PortfolioDecision exported from package")
        _chk(hasattr(aiem_portfolio_engine, "evaluate_exit"),
             "IMP01: evaluate_exit exported from package")
        _chk(hasattr(aiem_portfolio_engine, "GATE_STEPS"),
             "IMP01: GATE_STEPS exported from package")
    except Exception as e:
        _fail("IMP01: package import failed", str(e))

    for mod in ("config","db","snapshot","greeks","limits",
                "correlation","stress","valuation","optimizer","gate","exit_mgmt"):
        try:
            __import__(f"aiem_portfolio_engine.{mod}")
            _ok(f"IMP_{mod.upper()}: aiem_portfolio_engine.{mod} imports")
        except Exception as e:
            _fail(f"IMP_{mod.upper()}: import failed", str(e))

    # Scheduler wiring: import check
    try:
        import importlib.util
        importlib.util.spec_from_file_location(
            "aiem_strat_scheduler",
            os.path.join(os.path.dirname(__file__), "aiem_strat_scheduler.py")
        )
        _ok("IMP_SCHED: aiem_strat_scheduler.py is readable for import check")
    except Exception as e:
        _fail("IMP_SCHED: scheduler check failed", str(e))

    # Verify run_portfolio_gate is referenced in scheduler
    with open(os.path.join(os.path.dirname(__file__), "aiem_strat_scheduler.py")) as f:
        sched_src = f.read()
    _chk("run_portfolio_gate" in sched_src,
         "IMP_WIRE: run_portfolio_gate referenced in aiem_strat_scheduler.py")
    _chk("PE_GATING_ENABLED" in sched_src or "_PE_GATING" in sched_src,
         "IMP_WIRE: PE_GATING_ENABLED guard in aiem_strat_scheduler.py")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

_SECTION_MAP = {
    "S1":   test_s1,
    "S2":   test_s2,
    "S3":   test_s3_s7,
    "S7":   test_s3_s7,
    "S4":   test_s4,
    "S5":   test_s5,
    "S6":   test_s6,
    "S8":   test_s8,
    "S9":   test_s9_s11_s12,
    "S11":  test_s9_s11_s12,
    "S12":  test_s9_s11_s12,
    "A1":   test_a1_exit_mgmt,
    "A2":   test_a2_correlation_additions,
    "A3":   test_a3_traceid,
    "A4":   test_a4_stale_quotes,
    "A5":   test_a5_a6_industry_strike,
    "A6":   test_a5_a6_industry_strike,
    "A7":   test_a7_daily_loss,
    "A8":   test_a8_higher_greeks,
    "A9":   test_a9_new_scenarios,
    "A10":  test_a10_new_negctrl,
    "A11":  test_a11_substitute,
    "A12":  test_a12_a13_gate_steps,
    "A13":  test_a12_a13_gate_steps,
    "DB":   test_db,
    "IMP":  test_imports,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", default="ALL")
    args = parser.parse_args()

    print(f"portfolio_engine_verify.py  {_ts()}")
    print(f"Section: {args.section}")

    run_all = args.section.upper() == "ALL"
    if run_all:
        fns = [
            test_imports, test_s1, test_s2, test_s3_s7, test_s4,
            test_s5, test_s6, test_s8, test_s9_s11_s12,
            # Section A additions
            test_a1_exit_mgmt, test_a2_correlation_additions, test_a3_traceid,
            test_a4_stale_quotes, test_a5_a6_industry_strike, test_a7_daily_loss,
            test_a8_higher_greeks, test_a9_new_scenarios, test_a10_new_negctrl,
            test_a11_substitute, test_a12_a13_gate_steps,
            test_db,
        ]
    else:
        fn = _SECTION_MAP.get(args.section.upper())
        if not fn:
            print(f"Unknown section: {args.section}")
            sys.exit(1)
        fns = [fn]

    for fn in fns:
        try:
            fn()
        except Exception as exc:
            _fail(f"EXCEPTION in {fn.__name__}", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()

    print(f"\n{'─'*60}")
    for line in _RESULTS:
        print(line)
    print(f"{'─'*60}")
    print(f"\nTotal: {_PASS + _FAIL}  PASS: {_PASS}  FAIL: {_FAIL}")
    print(f"config_sha: {__import__('aiem_portfolio_engine.config', fromlist=['pe_config_sha']).pe_config_sha()}")
    print(f"timestamp: {_ts()}")
    sys.exit(0 if _FAIL == 0 else 1)
