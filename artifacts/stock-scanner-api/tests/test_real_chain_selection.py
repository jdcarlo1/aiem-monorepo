#!/usr/bin/env python3
"""H3 — Fixture tests for real-chain selection helpers.

No network calls. All helpers are redefined locally so this file is
runnable without importing aiem_options_scheduler (which has many
side-effects at module level).

The harness in Case 1 reproduces the `if not _cc:` block VERBATIM from
the C2 replacement in aiem_options_scheduler.py. If the real block ever
diverges from the harness, this comment is the signal to update the
harness.

Run:  python3 artifacts/stock-scanner-api/tests/test_real_chain_selection.py
"""

import sys, traceback

# ── Helper definitions (verbatim copies from C2 block) ─────────────────────
_LIQ_IV_MAX   = 3.0
_MIN_DTE_CHAIN = 5
_DELTA_TARGET  = 0.35


def _pick_expiry(chain, min_dte=_MIN_DTE_CHAIN):
    """Nearest expiration with dte >= min_dte from the chain itself."""
    exps = sorted({
        (c.get("dte"), c.get("expiration_date"))
        for c in (chain or [])
        if c.get("expiration_date") and c.get("dte") is not None
        and c.get("dte") >= min_dte
    })
    return exps[0][1] if exps else None


def _liquid_chain(chain, typ, expiry):
    """Filter to liquid contracts of given type and expiry.
    Predicate: bid>0, ask>0, 0<iv<=3.0, abs(delta)>0.
    No OI/volume/spread gates — those are downstream gate concerns.
    """
    out = []
    for c in chain or []:
        if c.get("contract_type") != typ:
            continue
        if expiry and c.get("expiration_date") != expiry:
            continue
        b, a = c.get("bid"), c.get("ask")
        iv, d = c.get("implied_volatility"), c.get("delta")
        if not b or not a or b <= 0 or a <= 0:
            continue
        if iv is None or iv <= 0 or iv > _LIQ_IV_MAX:
            continue
        if d is None or abs(d) == 0.0:
            continue
        out.append(c)
    return out


def _pick_by_delta(cands, target):
    """Nearest-to-target |delta|; lower strike breaks ties deterministically."""
    return min(
        cands,
        key=lambda c: (abs(abs(c.get("delta") or 0.0) - target),
                       float(c.get("strike") or 0.0)),
    )


# ── Harness for Case 1 (reproduces `if not _cc:` block verbatim) ───────────
# VERBATIM from aiem_options_scheduler.py C2 block — keep in sync:
def _harness_no_liquid_call(options_chain_all, _poly_exp, ticker="TST"):
    """Harness: reproduces the 'if not _cc: fail-closed' branch from C2."""
    _NO_CAND_calls = []
    def _NO_CAND(reason, tkr, exp):
        _NO_CAND_calls.append((reason, tkr, exp))

    _cc = (_liquid_chain(options_chain_all, "call", _poly_exp)
           if _poly_exp else [])
    if not _cc:
        call_strike = None
        call_bid = call_ask = call_mid = call_spread = None
        call_delta_bs = call_probability_itm = None
        call_oi = call_vol = None
        call_exp = _poly_exp
        _NO_CAND("NO_LIQUID_CALL_CONTRACT", ticker, _poly_exp)
    else:
        _p = _pick_by_delta(_cc, _DELTA_TARGET)
        call_strike = float(_p["strike"])
        call_delta_bs = float(_p["delta"])
        call_probability_itm = call_delta_bs
        call_bid = float(_p["bid"])
        call_ask = float(_p["ask"])
        call_mid = round((call_bid + call_ask) / 2.0, 2)
        call_spread = round((call_ask - call_bid) / call_mid, 4)
        call_exp = _p["expiration_date"]

    return {
        "call_strike": call_strike,
        "_NO_CAND_calls": _NO_CAND_calls,
    }


# ── Test infrastructure ─────────────────────────────────────────────────────
_PASS = 0
_FAIL = 0


def _assert(label, condition, detail=""):
    global _PASS, _FAIL
    if condition:
        print(f"  PASS  {label}")
        _PASS += 1
    else:
        print(f"  FAIL  {label}  {detail}")
        _FAIL += 1


def _make_contract(typ, strike, bid, ask, iv, delta, expiry="2026-08-07",
                   dte=5, oi=100, vol=50):
    return {
        "contract_type": typ,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": (bid + ask) / 2 if bid and ask else 0,
        "implied_volatility": iv,
        "delta": delta,
        "expiration_date": expiry,
        "dte": dte,
        "open_interest": oi,
        "volume": vol,
    }


# ── Case 1: all bid=0/ask=0 → _liquid_chain returns []; harness sets
#           call_strike=None with no exception ────────────────────────────
def test_case1():
    print("Case 1: all contracts bid=0, ask=0")
    exp = "2026-08-07"
    chain = [
        _make_contract("call", 12.0, 0.0, 0.0, None, 0.0, expiry=exp),
        _make_contract("call", 13.0, 0.0, 0.0, None, 0.0, expiry=exp),
        _make_contract("put",  11.0, 0.0, 0.0, None, 0.0, expiry=exp),
    ]
    result = _liquid_chain(chain, "call", exp)
    _assert("_liquid_chain([all bid=0], 'call', exp) == []", result == [],
            f"got {result}")

    harness = _harness_no_liquid_call(chain, exp)
    _assert("harness: call_strike is None",
            harness["call_strike"] is None,
            f"got call_strike={harness['call_strike']}")
    _assert("harness: _NO_CAND called with NO_LIQUID_CALL_CONTRACT",
            any(r[0] == "NO_LIQUID_CALL_CONTRACT" for r in harness["_NO_CAND_calls"]),
            f"_NO_CAND_calls={harness['_NO_CAND_calls']}")


# ── Case 2: deltas 0.20/0.34/0.55, target 0.35 → selects 0.34 ─────────────
def test_case2():
    print("Case 2: deltas 0.20/0.34/0.55, target=0.35 → should pick 0.34")
    exp = "2026-08-07"
    chain = [
        _make_contract("call", 15.0, 0.5, 0.8, 0.4, 0.20, expiry=exp),
        _make_contract("call", 13.0, 0.5, 0.8, 0.4, 0.34, expiry=exp),
        _make_contract("call", 12.0, 0.5, 0.8, 0.4, 0.55, expiry=exp),
    ]
    liquid = _liquid_chain(chain, "call", exp)
    chosen = _pick_by_delta(liquid, 0.35)
    _assert("selects delta=0.34 (distance 0.01 < 0.01 and 0.20)",
            chosen["delta"] == 0.34,
            f"chose delta={chosen['delta']} strike={chosen['strike']}")


# ── Case 3: equidistant 0.30/0.40; tiebreak = lower strike ─────────────────
def test_case3():
    print("Case 3: equidistant deltas 0.30/0.40, target=0.35 → lower strike wins")
    exp = "2026-08-07"

    # Lower strike = 12.0 (delta=0.30)
    chain_forward = [
        _make_contract("call", 12.0, 0.5, 0.8, 0.4, 0.30, expiry=exp),
        _make_contract("call", 14.0, 0.5, 0.8, 0.4, 0.40, expiry=exp),
    ]
    # Same contracts, reversed list order
    chain_reversed = list(reversed(chain_forward))

    liquid_f = _liquid_chain(chain_forward, "call", exp)
    liquid_r = _liquid_chain(chain_reversed, "call", exp)

    chosen_f = _pick_by_delta(liquid_f, 0.35)
    chosen_r = _pick_by_delta(liquid_r, 0.35)

    _assert("forward order: selects lower strike=12.0",
            chosen_f["strike"] == 12.0,
            f"chose strike={chosen_f['strike']}")
    _assert("reversed order: same result (strike=12.0)",
            chosen_r["strike"] == 12.0,
            f"chose strike={chosen_r['strike']}")
    _assert("both orders produce identical selection",
            chosen_f["strike"] == chosen_r["strike"],
            f"f={chosen_f['strike']} r={chosen_r['strike']}")


# ── Case 4: put deltas −0.33/−0.38, target=0.35 → abs applied, picks −0.33 ─
def test_case4():
    print("Case 4: put deltas -0.33/-0.38, target=0.35 → abs applied → picks -0.33")
    exp = "2026-08-07"
    chain = [
        _make_contract("put", 12.0, 0.5, 0.8, 0.4, -0.33, expiry=exp),
        _make_contract("put", 11.0, 0.5, 0.8, 0.4, -0.38, expiry=exp),
    ]
    liquid = _liquid_chain(chain, "put", exp)
    chosen = _pick_by_delta(liquid, 0.35)
    _assert("abs(delta=-0.33)=0.33: distance 0.02; abs(delta=-0.38)=0.38: distance 0.03 → picks -0.33",
            chosen["delta"] == -0.33,
            f"chose delta={chosen['delta']}")


# ── Case 5: bid/ask>0 but iv=4.3 (>3.0) → excluded ────────────────────────
def test_case5():
    print("Case 5: bid/ask>0 but iv=4.3 > 3.0 → excluded by _liquid_chain")
    exp = "2026-08-07"
    chain = [
        _make_contract("call", 12.0, 0.5, 0.8, 4.3, 0.35, expiry=exp),
    ]
    liquid = _liquid_chain(chain, "call", exp)
    _assert("iv=4.3 excluded (> _LIQ_IV_MAX=3.0)",
            liquid == [],
            f"got liquid={liquid}")


# ── Case 6: bid/ask>0 but delta=0.0 → excluded ─────────────────────────────
def test_case6():
    print("Case 6: bid/ask>0 but delta=0.0 → excluded by _liquid_chain")
    exp = "2026-08-07"
    chain = [
        _make_contract("call", 12.0, 0.5, 0.8, 0.4, 0.0, expiry=exp),
    ]
    liquid = _liquid_chain(chain, "call", exp)
    _assert("delta=0.0 excluded",
            liquid == [],
            f"got liquid={liquid}")


# ── Case 7: correct type, wrong expiry → excluded ───────────────────────────
def test_case7():
    print("Case 7: correct type, wrong expiration_date → excluded")
    right_exp = "2026-08-07"
    wrong_exp = "2026-08-14"
    chain = [
        _make_contract("call", 12.0, 0.5, 0.8, 0.4, 0.35, expiry=wrong_exp),
    ]
    liquid = _liquid_chain(chain, "call", right_exp)
    _assert("wrong expiry excluded",
            liquid == [],
            f"got liquid={liquid}")


# ── Case 8: nearest-to-target contract has oi=0, spread=0.9 → still selected
#           (downstream gate must reject it) ──────────────────────────────────
def test_case8():
    print("Case 8: nearest-to-delta contract has oi=0, spread=0.9 → STILL selected")
    print("  (selection must not gate on OI or spread; let downstream gate reject)")
    exp = "2026-08-07"
    # Contract A: delta=0.34 (nearest to 0.35), oi=0, spread ≈ 0.93 (wide)
    # Contract B: delta=0.20 (farther), oi=500, spread ≈ 0.14 (tight)
    cA = _make_contract("call", 13.0, bid=0.30, ask=0.89, iv=0.4, delta=0.34,
                        expiry=exp, oi=0, vol=0)
    cB = _make_contract("call", 15.0, bid=0.50, ask=0.58, iv=0.4, delta=0.20,
                        expiry=exp, oi=500, vol=200)

    liquid = _liquid_chain([cA, cB], "call", exp)
    _assert("both pass _liquid_chain predicate (bid>0, ask>0, 0<iv<=3.0, abs(delta)>0)",
            len(liquid) == 2,
            f"liquid count={len(liquid)}")

    chosen = _pick_by_delta(liquid, 0.35)
    _assert("nearest-to-target (delta=0.34, oi=0, wide spread) is selected",
            chosen["delta"] == 0.34,
            f"chose delta={chosen['delta']} oi={chosen['open_interest']}")
    _assert("wide-spread contract IS returned (downstream gate must reject)",
            chosen["open_interest"] == 0,
            f"oi={chosen['open_interest']}")


# ── Case 9: 21-strike ladder — chain-scale selection (Amendment_A / A1) ────
# Real chains: CLF 31, HAL 43, VRTX 85 strikes.  All previous cases used
# 2-3 contracts.  This case proves _pick_by_delta works correctly at scale.
#
# Ladder: strike 25–45, deltas descending 0.95→0.05 in 0.045 steps,
# all passing the liquidity predicate (bid>0, ask>0, 0<iv≤3.0, delta≠0).
#
# Step 1: all 21 contracts present → nearest to 0.35 is strike=38 (delta=0.365,
#         dist=0.015).
# Step 2: remove strike=38, re-run → next-nearest is strike=39 (delta=0.32,
#         dist=0.030), NOT the first or last list element.
def test_case9():
    print("Case 9: 21-strike ladder (chain-scale) — nearest-delta selection")
    exp = "2026-08-07"
    # Build the ladder: strike=25+i, delta=0.95-0.045*i  (i=0..20)
    ladder = []
    for i in range(21):
        strike = 25.0 + i
        delta  = round(0.95 - 0.045 * i, 3)
        ladder.append(
            _make_contract("call", strike, bid=0.50, ask=0.70, iv=0.40,
                           delta=delta, expiry=exp, dte=7, oi=500, vol=200)
        )

    # ── Step 1: full 21-contract chain ──────────────────────────────────────
    liquid = _liquid_chain(ladder, "call", exp)
    _assert("step1: all 21 contracts pass liquidity predicate",
            len(liquid) == 21,
            f"liquid count={len(liquid)}")

    chosen1 = _pick_by_delta(liquid, 0.35)
    _assert("step1: nearest to 0.35 is strike=38 (delta=0.365, dist=0.015)",
            chosen1["strike"] == 38.0,
            f"chose strike={chosen1['strike']} delta={chosen1['delta']}")
    _assert("step1: chosen delta is 0.365",
            chosen1["delta"] == 0.365,
            f"delta={chosen1['delta']}")

    # ── Step 2: remove strike=38, re-run ────────────────────────────────────
    liquid2 = [c for c in liquid if c["strike"] != 38.0]
    _assert("step2: 20 contracts remain after removing nearest",
            len(liquid2) == 20,
            f"count={len(liquid2)}")

    chosen2 = _pick_by_delta(liquid2, 0.35)
    _assert("step2: next-nearest is strike=39 (delta=0.32, dist=0.030)",
            chosen2["strike"] == 39.0,
            f"chose strike={chosen2['strike']} delta={chosen2['delta']}")
    _assert("step2: result is NOT first element of list (strike=25)",
            chosen2["strike"] != 25.0,
            f"strike={chosen2['strike']}")
    _assert("step2: result is NOT last element of list (strike=45)",
            chosen2["strike"] != 45.0,
            f"strike={chosen2['strike']}")


# ── Run all ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [test_case1, test_case2, test_case3, test_case4,
             test_case5, test_case6, test_case7, test_case8, test_case9]
    for i, t in enumerate(tests, 1):
        try:
            t()
        except Exception as exc:
            print(f"  EXCEPTION in Case {i}: {exc}")
            traceback.print_exc()
            _FAIL += 1
        print()

    print(f"{'='*50}")
    print(f"TOTAL: {_PASS} PASS  {_FAIL} FAIL  ({_PASS + _FAIL} assertions)")
    sys.exit(0 if _FAIL == 0 else 1)
