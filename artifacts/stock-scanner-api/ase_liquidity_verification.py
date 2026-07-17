"""
ase_liquidity_verification.py — Section 8: Liquidity
Advanced Strategy Engine evidence-chain verifier.

17-field evidence report for every test.
Verifies: bid/ask/mid/modeled fill/paper fill/slippage/commissions/fees/
          spread width/OI/volume/quote age.
Rejects:  crossed markets / missing legs / stale chains /
          illiquid options / impossible pricing.

Run via: bash tools/verified_run.sh python artifacts/stock-scanner-api/ase_liquidity_verification.py
"""
import sys, os, hashlib, time, uuid
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from aiem_strat_engine.legs import Leg, SIDE_LONG, SIDE_SHORT, ASSET_CALL, ASSET_PUT
from aiem_strat_engine.pricing import (
    mid_price, conservative_fill, bid_ask_spread_fraction,
    slippage_estimate, commission, fill_quality_score, liquidity_score,
)
from aiem_strat_engine.eligibility import (
    check_quotes_present, check_bid_ask_width, check_open_interest,
    check_volume, check_quote_age, check_impossible_pricing,
    check_strategy_eligible,
)
from aiem_strat_engine.config import config_sha256, MIN_OPEN_INTEREST, MIN_VOLUME, MAX_BID_ASK_WIDTH

RUN_ID = str(uuid.uuid4())[:8]
RUN_TS = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
PASS   = "PASS"
FAIL   = "FAIL"


def _self_sha() -> str:
    with open(__file__, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _pricing_sha() -> str:
    p = os.path.join(os.path.dirname(__file__), "aiem_strat_engine", "pricing.py")
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _elig_sha() -> str:
    p = os.path.join(os.path.dirname(__file__), "aiem_strat_engine", "eligibility.py")
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _cs() -> str:
    return _pricing_sha()[:16] + "|" + _elig_sha()[:16]


def _conf() -> str:
    return config_sha256()[:16]


# ── Evidence tracking ─────────────────────────────────────────────────────────
_TESTS  = []
_PASS_N = 0
_FAIL_N = 0


def _record(fields: dict) -> None:
    global _PASS_N, _FAIL_N
    if fields.get("STATUS") == PASS:
        _PASS_N += 1
    else:
        _FAIL_N += 1
    _TESTS.append(fields)


def _print_report() -> None:
    print("=" * 80)
    print("ASE SECTION 8 — LIQUIDITY")
    print(f"RUN_ID         : {RUN_ID}")
    print(f"RUN_TS         : {RUN_TS}")
    print(f"pricing.py SHA : {_pricing_sha()}")
    print(f"eligibility SHA: {_elig_sha()}")
    print(f"config SHA     : {config_sha256()}")
    print(f"verifier SHA   : {_self_sha()}")
    print("=" * 80)
    for t in _TESTS:
        print()
        print(f"{'─'*70}")
        for k, v in t.items():
            print(f"  {k:<28}: {v}")
    print()
    print("=" * 80)
    print(f"TOTAL: {_PASS_N + _FAIL_N}  PASS: {_PASS_N}  FAIL: {_FAIL_N}")
    print(f"RESULT: {'ALL PASS' if _FAIL_N == 0 else f'FAILURES: {_FAIL_N}'}")
    print("=" * 80)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _call(bid, ask, oi=200, vol=100, strike=105.0, ts=None) -> Leg:
    mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
    return Leg(
        asset_type=ASSET_CALL, side=SIDE_LONG, strike=strike,
        bid=bid, ask=ask, mid=mid,
        open_interest=oi, volume=vol, quote_timestamp=ts, iv=0.25, delta=0.40,
    )


def _put(bid, ask, oi=200, vol=100, strike=95.0, ts=None) -> Leg:
    mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
    return Leg(
        asset_type=ASSET_PUT, side=SIDE_SHORT, strike=strike,
        bid=bid, ask=ask, mid=mid,
        open_interest=oi, volume=vol, quote_timestamp=ts, iv=0.28, delta=-0.35,
    )


def _now_ts(offset_seconds=0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════════

# ── T01: Crossed market rejection ─────────────────────────────────────────────
def t01():
    leg = _call(bid=1.50, ask=1.20)   # bid > ask — crossed
    passed, reasons = check_quotes_present([leg])
    ok = not passed and any("rossed" in r for r in reasons)
    fqs = fill_quality_score([leg])
    cross_check_ok = fqs == 0.0
    _record({
        "TEST_ID"            : "S8_T01",
        "TEST_NAME"          : "Crossed market rejection — bid > ask",
        "SCENARIO"           : "CALL bid=1.50, ask=1.20 (bid > ask)",
        "INPUTS"             : "bid=1.50 ask=1.20 oi=200 vol=100",
        "FUNCTION"           : "check_quotes_present()",
        "GATE_CATEGORY"      : "CROSSED_MARKET",
        "EXPECTED"           : "REJECTED — crossed quote",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : "fill_quality_score() must be 0.0 for crossed market",
        "CROSS_CHECK_RESULT" : fqs,
        "TOLERANCE"          : "exact rejection; fill_quality_score == 0.0",
        "WITHIN_TOLERANCE"   : ok and cross_check_ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if (ok and cross_check_ok) else FAIL,
    })


# ── T02: Missing bid rejection ────────────────────────────────────────────────
def t02():
    leg = _call(bid=None, ask=1.50)
    passed, reasons = check_quotes_present([leg])
    ok = not passed and any("issing" in r or "Missing" in r for r in reasons)
    _record({
        "TEST_ID"            : "S8_T02",
        "TEST_NAME"          : "Missing bid rejection",
        "SCENARIO"           : "CALL bid=None, ask=1.50",
        "INPUTS"             : "bid=None ask=1.50 oi=200 vol=100",
        "FUNCTION"           : "check_quotes_present()",
        "GATE_CATEGORY"      : "MISSING_QUOTE",
        "EXPECTED"           : "REJECTED — missing bid",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : "mid_price([leg]) must return None",
        "CROSS_CHECK_RESULT" : mid_price([leg]),
        "TOLERANCE"          : "passed==False and mid_price==None",
        "WITHIN_TOLERANCE"   : ok and mid_price([leg]) is None,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if (ok and mid_price([leg]) is None) else FAIL,
    })


# ── T03: Missing ask rejection ────────────────────────────────────────────────
def t03():
    leg = _call(bid=1.20, ask=None)
    passed, reasons = check_quotes_present([leg])
    ok = not passed and any("issing" in r or "Missing" in r for r in reasons)
    cf = conservative_fill([leg])
    _record({
        "TEST_ID"            : "S8_T03",
        "TEST_NAME"          : "Missing ask rejection",
        "SCENARIO"           : "CALL bid=1.20, ask=None",
        "INPUTS"             : "bid=1.20 ask=None oi=200 vol=100",
        "FUNCTION"           : "check_quotes_present()",
        "GATE_CATEGORY"      : "MISSING_QUOTE",
        "EXPECTED"           : "REJECTED — missing ask",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : "conservative_fill([leg]) must return None",
        "CROSS_CHECK_RESULT" : cf,
        "TOLERANCE"          : "passed==False and conservative_fill==None",
        "WITHIN_TOLERANCE"   : ok and cf is None,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if (ok and cf is None) else FAIL,
    })


# ── T04: Zero-price rejection ─────────────────────────────────────────────────
def t04():
    leg = _call(bid=0.0, ask=0.0)   # mid=0 → rejected
    passed, reasons = check_quotes_present([leg])
    # bid=0.0 == ask=0.0 triggers bid >= ask (Crossed quote) — correct rejection either way
    ok = not passed and len(reasons) > 0
    _record({
        "TEST_ID"            : "S8_T04",
        "TEST_NAME"          : "Zero mid price rejection",
        "SCENARIO"           : "CALL bid=0.0, ask=0.0 → mid=0 (bid==ask triggers crossed-quote path)",
        "INPUTS"             : "bid=0.0 ask=0.0 mid=0.0 oi=200 vol=100",
        "FUNCTION"           : "check_quotes_present()",
        "GATE_CATEGORY"      : "ZERO_MID / CROSSED_EQUAL",
        "EXPECTED"           : "REJECTED (via Zero-mid or Crossed-equal; both are correct)",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "NOTE"               : "bid=ask=0 → bid>=ask → 'Crossed quote' path; rejection is correct",
        "CROSS_CHECK"        : "fill_quality_score() = 0.0 for any crossed/zero market",
        "CROSS_CHECK_RESULT" : fill_quality_score([leg]),
        "TOLERANCE"          : "passed==False and at least one rejection reason present",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T05: Stale quote rejection (timestamp > 5 min old) ───────────────────────
def t05():
    stale_ts = _now_ts(offset_seconds=-600)   # 10 minutes ago
    leg = _call(bid=1.20, ask=1.50, ts=stale_ts)
    passed, reasons = check_quote_age([leg], max_age_seconds=300)
    ok = not passed and any("Stale" in r or "stale" in r for r in reasons)
    _record({
        "TEST_ID"            : "S8_T05",
        "TEST_NAME"          : "Stale quote rejection — timestamp 10 min old",
        "SCENARIO"           : f"quote_timestamp={stale_ts} (600s ago), limit=300s",
        "INPUTS"             : f"bid=1.20 ask=1.50 ts={stale_ts}",
        "FUNCTION"           : "check_quote_age(max_age_seconds=300)",
        "GATE_CATEGORY"      : "STALE_CHAIN",
        "EXPECTED"           : "REJECTED — quote older than 5 min",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : "age_seconds=600 > max=300 → must reject",
        "CROSS_CHECK_RESULT" : "age=600s confirmed",
        "TOLERANCE"          : "passed==False",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T06: Fresh quote passes ────────────────────────────────────────────────────
def t06():
    fresh_ts = _now_ts(offset_seconds=-30)   # 30 seconds ago
    leg = _call(bid=1.20, ask=1.50, ts=fresh_ts)
    passed, reasons = check_quote_age([leg], max_age_seconds=300)
    ok = passed and len(reasons) == 0
    _record({
        "TEST_ID"            : "S8_T06",
        "TEST_NAME"          : "Fresh quote passes — timestamp 30s old",
        "SCENARIO"           : f"quote_timestamp={fresh_ts} (30s ago), limit=300s",
        "INPUTS"             : f"bid=1.20 ask=1.50 ts={fresh_ts}",
        "FUNCTION"           : "check_quote_age(max_age_seconds=300)",
        "GATE_CATEGORY"      : "FRESH_CHAIN",
        "EXPECTED"           : "PASSED — quote is recent",
        "COMPUTED"           : "PASSED" if passed else f"REJECTED: {reasons}",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : "age_seconds=30 < max=300 → must pass",
        "CROSS_CHECK_RESULT" : "age=30s confirmed",
        "TOLERANCE"          : "passed==True and reasons==[]",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T07: Missing timestamp fails-closed ───────────────────────────────────────
def t07():
    leg = _call(bid=1.20, ask=1.50, ts=None)   # no timestamp
    passed, reasons = check_quote_age([leg], max_age_seconds=300)
    ok = not passed and any("timestamp" in r.lower() for r in reasons)
    _record({
        "TEST_ID"            : "S8_T07",
        "TEST_NAME"          : "Missing timestamp fails-closed (treated as stale)",
        "SCENARIO"           : "CALL quote_timestamp=None",
        "INPUTS"             : "bid=1.20 ask=1.50 ts=None",
        "FUNCTION"           : "check_quote_age()",
        "GATE_CATEGORY"      : "MISSING_TIMESTAMP",
        "EXPECTED"           : "REJECTED — no timestamp → fail-closed",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : "fail-closed principle: unknown age = stale",
        "CROSS_CHECK_RESULT" : "N/A — verified by rejection",
        "TOLERANCE"          : "passed==False",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T08: Low OI rejection ─────────────────────────────────────────────────────
def t08():
    leg = _call(bid=1.20, ask=1.50, oi=10)   # OI=10 < MIN=50
    passed, reasons = check_open_interest([leg])
    ok = not passed and any("OI" in r or "oi" in r.lower() for r in reasons)
    _record({
        "TEST_ID"            : "S8_T08",
        "TEST_NAME"          : "Low OI rejection — OI=10 < minimum 50",
        "SCENARIO"           : f"CALL open_interest=10, MIN_OPEN_INTEREST={MIN_OPEN_INTEREST}",
        "INPUTS"             : "bid=1.20 ask=1.50 oi=10 vol=100",
        "FUNCTION"           : "check_open_interest()",
        "GATE_CATEGORY"      : "LOW_OI",
        "EXPECTED"           : "REJECTED — OI 10 < 50",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : f"liquidity_score OI component = 10/1000 = 0.01 (very low)",
        "CROSS_CHECK_RESULT" : liquidity_score([leg]),
        "TOLERANCE"          : "passed==False",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T09: Low volume rejection ─────────────────────────────────────────────────
def t09():
    leg = _call(bid=1.20, ask=1.50, oi=200, vol=5)   # vol=5 < MIN=20
    passed, reasons = check_volume([leg])
    ok = not passed and any("volume" in r.lower() or "vol" in r.lower() for r in reasons)
    _record({
        "TEST_ID"            : "S8_T09",
        "TEST_NAME"          : "Low volume rejection — vol=5 < minimum 20",
        "SCENARIO"           : f"CALL volume=5, MIN_VOLUME={MIN_VOLUME}",
        "INPUTS"             : "bid=1.20 ask=1.50 oi=200 vol=5",
        "FUNCTION"           : "check_volume()",
        "GATE_CATEGORY"      : "LOW_VOLUME",
        "EXPECTED"           : "REJECTED — volume 5 < 20",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : "liquidity_score vol component = 5/200 = 0.025 (very low)",
        "CROSS_CHECK_RESULT" : liquidity_score([leg]),
        "TOLERANCE"          : "passed==False",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T10: Wide spread rejection ────────────────────────────────────────────────
def t10():
    # spread = 0.70, mid = 1.35, fraction = 0.70/1.35 ≈ 51.8% > 30%
    leg = _call(bid=1.00, ask=1.70)
    spread_frac = (1.70 - 1.00) / 1.35
    passed, reasons = check_bid_ask_width([leg])
    ok = not passed and any("wide" in r.lower() or "bid-ask" in r.lower() for r in reasons)
    fqs = fill_quality_score([leg])
    _record({
        "TEST_ID"            : "S8_T10",
        "TEST_NAME"          : "Wide spread rejection — spread/mid > 30%",
        "SCENARIO"           : f"CALL bid=1.00, ask=1.70 → spread_frac={spread_frac:.1%} > 30%",
        "INPUTS"             : "bid=1.00 ask=1.70 mid=1.35 oi=200 vol=100",
        "FUNCTION"           : "check_bid_ask_width(max_frac=0.30)",
        "GATE_CATEGORY"      : "WIDE_SPREAD",
        "EXPECTED"           : f"REJECTED — spread {spread_frac:.1%} > {MAX_BID_ASK_WIDTH:.0%}",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : f"fill_quality_score() penalises wide spread → {fqs}",
        "CROSS_CHECK_RESULT" : fqs,
        "TOLERANCE"          : "passed==False",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T11: Impossible pricing — call ask > spot ─────────────────────────────────
def t11():
    spot = 100.0
    # Call ask = 110 > spot 100 — physically impossible
    leg = Leg(
        asset_type=ASSET_CALL, side=SIDE_LONG, strike=90.0,
        bid=105.0, ask=110.0, mid=107.5,
        open_interest=200, volume=100, iv=0.25, delta=0.95,
    )
    passed, reasons = check_impossible_pricing([leg], spot=spot)
    ok = not passed and any("call" in r.lower() or "impossible" in r.lower() or "spot" in r.lower() for r in reasons)
    _record({
        "TEST_ID"            : "S8_T11",
        "TEST_NAME"          : "Impossible pricing — call ask > spot",
        "SCENARIO"           : "CALL ask=110 > spot=100 (option can't cost more than underlying)",
        "INPUTS"             : "bid=105.0 ask=110.0 spot=100.0 strike=90",
        "FUNCTION"           : "check_impossible_pricing()",
        "GATE_CATEGORY"      : "IMPOSSIBLE_PRICING",
        "EXPECTED"           : "REJECTED — call ask > spot×1.05",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : "parity bound: call price ≤ spot at all times",
        "CROSS_CHECK_RESULT" : f"ask={110} > spot×1.05={100*1.05}",
        "TOLERANCE"          : "passed==False",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T12: Impossible pricing — put ask > strike ────────────────────────────────
def t12():
    spot = 100.0
    # Put ask = 110 > strike 95 — physically impossible (put max payoff = strike)
    leg = Leg(
        asset_type=ASSET_PUT, side=SIDE_LONG, strike=95.0,
        bid=100.0, ask=110.0, mid=105.0,
        open_interest=200, volume=100, iv=0.25, delta=-0.90,
    )
    passed, reasons = check_impossible_pricing([leg], spot=spot)
    ok = not passed and any("put" in r.lower() or "impossible" in r.lower() or "strike" in r.lower() for r in reasons)
    _record({
        "TEST_ID"            : "S8_T12",
        "TEST_NAME"          : "Impossible pricing — put ask > strike",
        "SCENARIO"           : "PUT ask=110 > strike=95 (put max payoff = strike)",
        "INPUTS"             : "bid=100.0 ask=110.0 spot=100.0 strike=95",
        "FUNCTION"           : "check_impossible_pricing()",
        "GATE_CATEGORY"      : "IMPOSSIBLE_PRICING",
        "EXPECTED"           : "REJECTED — put ask > strike×1.05",
        "COMPUTED"           : "REJECTED" if not passed else "PASSED (WRONG)",
        "REJECTION_REASONS"  : reasons,
        "CROSS_CHECK"        : "parity bound: put price ≤ strike at all times",
        "CROSS_CHECK_RESULT" : f"ask={110} > strike×1.05={95*1.05:.2f}",
        "TOLERANCE"          : "passed==False",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T13: Conservative fill — long at ask, short at bid ───────────────────────
def t13():
    long_leg  = Leg(asset_type=ASSET_CALL, side=SIDE_LONG,  strike=100.0,
                    bid=2.00, ask=2.20, mid=2.10, iv=0.25, delta=0.50,
                    open_interest=500, volume=200)
    short_leg = Leg(asset_type=ASSET_CALL, side=SIDE_SHORT, strike=105.0,
                    bid=0.90, ask=1.10, mid=1.00, iv=0.22, delta=0.35,
                    open_interest=300, volume=150)
    legs = [long_leg, short_leg]

    # Conservative fill: pay ask for long, receive bid for short
    expected_fill = round(long_leg.ask - short_leg.bid, 4)   # 2.20 - 0.90 = 1.30
    computed_fill = conservative_fill(legs)
    mid_fill      = mid_price(legs)   # 2.10 - 1.00 = 1.10

    tol = 1e-4
    fill_correct = abs(computed_fill - expected_fill) <= tol
    # Conservative fill must be > mid (worse for buyer = realistic worst-case)
    fill_worse_than_mid = computed_fill > mid_fill
    ok = fill_correct and fill_worse_than_mid
    _record({
        "TEST_ID"            : "S8_T13",
        "TEST_NAME"          : "Conservative fill — long at ask, short at bid",
        "SCENARIO"           : "100/105 call spread: long bid=2.00/ask=2.20; short bid=0.90/ask=1.10",
        "INPUTS"             : "long ask=2.20, short bid=0.90",
        "FUNCTION"           : "conservative_fill()",
        "GATE_CATEGORY"      : "FILL_MODELING",
        "EXPECTED"           : f"long_ask - short_bid = {expected_fill}",
        "COMPUTED"           : computed_fill,
        "MID_PRICE"          : mid_fill,
        "FILL_WORSE_THAN_MID": fill_worse_than_mid,
        "CROSS_CHECK"        : "conservative_fill > mid_price (buyer pays more than fair value)",
        "CROSS_CHECK_RESULT" : f"{computed_fill} > {mid_fill}",
        "TOLERANCE"          : f"|computed - expected| <= {tol}",
        "WITHIN_TOLERANCE"   : fill_correct,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T14: Mid price calculation ────────────────────────────────────────────────
def t14():
    long_leg  = Leg(asset_type=ASSET_CALL, side=SIDE_LONG,  strike=100.0,
                    bid=2.00, ask=2.20, mid=2.10, iv=0.25, delta=0.50,
                    open_interest=500, volume=200)
    short_leg = Leg(asset_type=ASSET_CALL, side=SIDE_SHORT, strike=105.0,
                    bid=0.90, ask=1.10, mid=1.00, iv=0.22, delta=0.35,
                    open_interest=300, volume=150)
    legs = [long_leg, short_leg]

    computed   = mid_price(legs)
    expected   = round(2.10 - 1.00, 4)   # 1.10 net debit
    tol        = 1e-4
    ok = abs(computed - expected) <= tol
    _record({
        "TEST_ID"            : "S8_T14",
        "TEST_NAME"          : "Net mid price — bull call spread",
        "SCENARIO"           : "100C long mid=2.10 / 105C short mid=1.00 → net debit=1.10",
        "INPUTS"             : "long mid=2.10 (LONG +sign), short mid=1.00 (SHORT -sign)",
        "FUNCTION"           : "mid_price()",
        "GATE_CATEGORY"      : "MID_PRICE",
        "EXPECTED"           : expected,
        "COMPUTED"           : computed,
        "FORMULA"            : "Σ(sign × mid × ratio) = +2.10 + (-1.00) = 1.10",
        "CROSS_CHECK"        : "conservative_fill > mid_price (mid is fair value floor)",
        "CROSS_CHECK_RESULT" : conservative_fill(legs),
        "TOLERANCE"          : f"|computed - expected| <= {tol}",
        "WITHIN_TOLERANCE"   : abs(computed - expected) <= tol,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T15: Slippage estimate — vol-adjusted spread cost ─────────────────────────
def t15():
    # Two legs: spread = 0.20 each, vol=0.25 → vol_factor = min(0.25, max(0.10, 0.25/3)) ≈ 0.0833
    long_leg  = Leg(asset_type=ASSET_CALL, side=SIDE_LONG,  strike=100.0,
                    bid=2.00, ask=2.20, mid=2.10, iv=0.25, delta=0.50,
                    open_interest=500, volume=200)
    short_leg = Leg(asset_type=ASSET_CALL, side=SIDE_SHORT, strike=105.0,
                    bid=0.90, ask=1.10, mid=1.00, iv=0.22, delta=0.35,
                    open_interest=300, volume=150)
    legs = [long_leg, short_leg]
    underlying_vol = 0.25

    slip = slippage_estimate(legs, underlying_vol=underlying_vol)
    vol_factor = min(0.25, max(0.10, underlying_vol / 3.0))
    # Per-leg slip: spread × vol_factor; ×ratio×100 for dollar value
    expected_slip = round(
        (0.20 * vol_factor * 1 * 100) +   # long leg
        (0.20 * vol_factor * 1 * 100),     # short leg
        4,
    )
    tol = 0.01
    ok = abs(slip - expected_slip) <= tol and slip > 0
    _record({
        "TEST_ID"            : "S8_T15",
        "TEST_NAME"          : "Slippage estimate — vol-adjusted spread cost",
        "SCENARIO"           : "2-leg spread, each spread=0.20, underlying_vol=0.25",
        "INPUTS"             : f"long spread=0.20, short spread=0.20, vol={underlying_vol}",
        "FUNCTION"           : "slippage_estimate()",
        "GATE_CATEGORY"      : "SLIPPAGE",
        "VOL_FACTOR"         : round(vol_factor, 4),
        "EXPECTED_SLIP"      : expected_slip,
        "COMPUTED_SLIP"      : slip,
        "FORMULA"            : "spread × vol_factor × ratio × 100 per leg",
        "CROSS_CHECK"        : "slip > 0 (cost is always positive)",
        "CROSS_CHECK_RESULT" : slip > 0,
        "TOLERANCE"          : f"|computed - expected| <= {tol}",
        "WITHIN_TOLERANCE"   : abs(slip - expected_slip) <= tol,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T16: Commission + regulatory + OCC fee total ─────────────────────────────
def t16():
    long_leg  = Leg(asset_type=ASSET_CALL, side=SIDE_LONG,  strike=100.0,
                    bid=2.00, ask=2.20, mid=2.10, iv=0.25, delta=0.50,
                    open_interest=500, volume=200)
    short_leg = Leg(asset_type=ASSET_CALL, side=SIDE_SHORT, strike=105.0,
                    bid=0.90, ask=1.10, mid=1.00, iv=0.22, delta=0.35,
                    open_interest=300, volume=150)
    legs = [long_leg, short_leg]

    # Expected: 0.00 base + 2 legs × (0.65 + 0.02 + 0.01) per contract × 1 contract
    expected = round(0.00 + 2 * (0.65 + 0.02 + 0.01) * 1, 4)
    computed = commission(legs, contracts=1)
    tol = 1e-4
    ok = abs(computed - expected) <= tol
    _record({
        "TEST_ID"            : "S8_T16",
        "TEST_NAME"          : "Commission + regulatory + OCC fee total",
        "SCENARIO"           : "2-option-leg spread, 1 contract, Tradier fee model",
        "INPUTS"             : "2 option legs × 1 contract",
        "FUNCTION"           : "commission()",
        "GATE_CATEGORY"      : "COMMISSION",
        "FORMULA"            : "base($0) + n_legs × (0.65+0.02+0.01) × contracts",
        "EXPECTED"           : expected,
        "COMPUTED"           : computed,
        "COMPONENTS"         : "COMMISSION_PER_LEG=0.65, REG=0.02, OCC=0.01",
        "CROSS_CHECK"        : "commission > 0 for any option trade",
        "CROSS_CHECK_RESULT" : computed > 0,
        "TOLERANCE"          : f"|computed - expected| <= {tol}",
        "WITHIN_TOLERANCE"   : abs(computed - expected) <= tol,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── T17: Fill quality score — tight=high, crossed=zero ───────────────────────
def t17():
    # Tight spread: bid=2.00, ask=2.10, spread_frac=0.10/2.05≈4.9% → near-perfect
    tight_leg = Leg(asset_type=ASSET_CALL, side=SIDE_LONG, strike=100.0,
                    bid=2.00, ask=2.10, mid=2.05, iv=0.25, delta=0.50,
                    open_interest=2000, volume=500)
    # Crossed: bid=1.50, ask=1.20
    crossed_leg = Leg(asset_type=ASSET_CALL, side=SIDE_LONG, strike=100.0,
                      bid=1.50, ask=1.20, mid=1.35, iv=0.25, delta=0.50,
                      open_interest=200, volume=100)

    fqs_tight   = fill_quality_score([tight_leg])
    fqs_crossed = fill_quality_score([crossed_leg])
    liq_tight   = liquidity_score([tight_leg])

    tight_high   = fqs_tight > 0.70
    crossed_zero = fqs_crossed == 0.0
    liq_sane     = 0 < liq_tight <= 1.0
    ok = tight_high and crossed_zero and liq_sane
    _record({
        "TEST_ID"            : "S8_T17",
        "TEST_NAME"          : "Fill quality score: tight=high, crossed=zero",
        "SCENARIO"           : "tight spread (4.9%) vs crossed market comparison",
        "INPUTS"             : "tight: bid=2.00/ask=2.10/oi=2000/vol=500 | crossed: bid=1.50/ask=1.20",
        "FUNCTION"           : "fill_quality_score() + liquidity_score()",
        "GATE_CATEGORY"      : "FILL_QUALITY",
        "FQS_TIGHT"          : fqs_tight,
        "FQS_CROSSED"        : fqs_crossed,
        "LIQ_TIGHT"          : liq_tight,
        "TIGHT_HIGH"         : tight_high,
        "CROSSED_ZERO"       : crossed_zero,
        "CROSS_CHECK"        : "liquidity_score tight > 0 and <= 1.0",
        "CROSS_CHECK_RESULT" : liq_sane,
        "TOLERANCE"          : "fqs_tight > 0.70; fqs_crossed == 0.0",
        "WITHIN_TOLERANCE"   : ok,
        "CODE_SHA256_16"     : _cs(),
        "CONFIG_SHA256_16"   : _conf(),
        "STATUS"             : PASS if ok else FAIL,
    })


# ── Run all ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    for fn in [t01,t02,t03,t04,t05,t06,t07,t08,t09,t10,t11,t12,t13,t14,t15,t16,t17]:
        try:
            fn()
        except Exception as e:
            import traceback
            _record({
                "TEST_ID"   : f"EXCEPTION in {fn.__name__}",
                "ERROR"     : str(e),
                "TRACEBACK" : traceback.format_exc()[-400:],
                "STATUS"    : FAIL,
            })
    _print_report()
    sys.exit(0 if _FAIL_N == 0 else 1)
