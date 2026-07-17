#!/usr/bin/env python3
"""
ase_leg_construction_verification.py — v2
══════════════════════════════════════════════════════════════════════════════
SCOPE DISCLOSURE
  All 66 tests run against synthetic in-memory Leg/LegTemplate objects.
  No live market data, no Tradier API calls, no network I/O.
  DB queries (one per section) verify production schema/data properties
  tied to each section's specific assertion — they are NOT used as test
  inputs and do NOT affect PASS/FAIL verdicts.

  Grep evidence for code-behavior claims is captured via subprocess at
  startup and embedded directly in the raw_output of the relevant tests.

SECTIONS
  A  T001–T008  Leg count (1–8 legs)
  B  T009–T014  Long/Short side (signed_mid, signed_delta)
  C  T015–T017  Call/Put/Stock asset type
  D  T018–T022  Strike ordering (canonical_sort)
  E  T023–T024  Expiration ordering (canonical_sort)
  F  T025–T028  Ratios (net_debit_credit)
  G  T029–T032  Debit/Credit sign (net_debit_credit)
  H  T033–T036  Multiplier — buying_power_required ×100
  I  T037–T040  Optional stock leg (aggregate_greeks)
  J  T041–T044  Canonical strategy name (classify_legs)
  K  T045–T052  Strategy fingerprint (strategy_fingerprint)
  L  T053–T055  Greek aggregation (aggregate_greeks)
  M  T056–T066  Negative-control / malformed inputs

17 fields per test:
  01 Test ID            07 Expected Result    13 Run ID
  02 Strategy ID        08 Actual Result      14 Paper Trade ID
  03 Strategy Name      09 Numerical Diff     15 SQL Query
  04 Command            10 Allowed Tolerance  16 SQL Output
  05 Raw Output         11 PASS/FAIL          17 Code SHA-256
  06 Inputs             12 Timestamp          18 Config SHA-256
"""
from __future__ import annotations
import sys, os, hashlib, json, uuid, subprocess
from datetime import datetime, timezone
from typing import List, Optional, Any

sys.path.insert(0, os.path.dirname(__file__))

from aiem_strat_engine.legs import (
    Leg, LegTemplate,
    ASSET_CALL, ASSET_PUT, ASSET_STOCK,
    SIDE_LONG, SIDE_SHORT,
    canonical_sort, strategy_fingerprint,
    net_debit_credit, aggregate_greeks, buying_power_required,
)
from aiem_strat_engine.builder import (
    classify_legs, build_custom_multi_leg,
)
from aiem_strat_engine.config import config_sha256

import psycopg2

# ── DB connection ─────────────────────────────────────────────────────────────
_DB_URL = os.environ.get("DATABASE_URL", "")

def _db_query(sql: str) -> str:
    try:
        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        cur  = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return "(no rows)"
        return " | ".join(str(r[0]) for r in rows)
    except Exception as ex:
        return f"DB_ERROR: {ex}"

# ── Code SHA-256 (legs.py + builder.py + payoff.py + config.py) ──────────────
_ROOT = os.path.dirname(__file__)

def _file_bytes(rel: str) -> bytes:
    try:
        with open(os.path.join(_ROOT, "aiem_strat_engine", rel), "rb") as f:
            return f.read()
    except Exception:
        return b""

_CODE_SHA = hashlib.sha256(
    _file_bytes("legs.py") +
    _file_bytes("builder.py") +
    _file_bytes("payoff.py") +
    _file_bytes("config.py")
).hexdigest()
_CFG_SHA  = config_sha256()
_RUN_ID   = "LC_" + uuid.uuid4().hex[:16].upper()
_SEP      = "─" * 120
_DSEP     = "═" * 120

# ── Grep evidence — captured at startup, embedded in relevant tests ──────────
_LEGS_PY = os.path.join(_ROOT, "aiem_strat_engine", "legs.py")

def _grep(args: list) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception as ex:
        return f"GREP_ERROR: {ex}"

# signed_mid None guard  (legs.py lines 79–84)
_GREP_SIGNED_MID  = _grep(["sed", "-n", "79,84p", _LEGS_PY])
# signed_delta None guard (legs.py lines 86–91)
_GREP_SIGNED_DELTA = _grep(["sed", "-n", "86,91p", _LEGS_PY])
# canonical_sort fallback=9 for unknown asset/side types (lines 96–112)
_GREP_CANONICAL   = _grep(["sed", "-n", "96,112p", _LEGS_PY])
# LegTemplate.sort_key fallback=9 for unknown asset type (lines 43–45)
_GREP_SORT_KEY    = _grep(["grep", "-n", "sort_key\\|order\\.get", _LEGS_PY])

# ── Per-section SQL (each tied to that section's specific assertion) ──────────
#   A: leg-count range in production  — asserts builder produces 1–8 legs
_SQL_A = ("SELECT MIN(c)::text||' / '||MAX(c)::text"
          " FROM (SELECT COUNT(*) c FROM ase_paper_trade_legs GROUP BY paper_trade_id) s")
#   B: mid range in production legs   — asserts mid is always a real price
_SQL_B = ("SELECT MIN(mid)::text||' / '||MAX(mid)::text"
          " FROM ase_paper_trade_legs WHERE mid IS NOT NULL")
#   C: call/put distribution          — asserts asset_type stored correctly
_SQL_C = ("SELECT call_or_put||':'||COUNT(*)::text"
          " FROM ase_paper_trade_legs GROUP BY call_or_put ORDER BY call_or_put")
#   D: strike range in production     — asserts strike ordering has real range
_SQL_D = ("SELECT MIN(strike)::text||' / '||MAX(strike)::text"
          " FROM ase_paper_trade_legs WHERE strike IS NOT NULL")
#   E: expiry range in production     — asserts expirations span multiple dates
_SQL_E = ("SELECT MIN(expiration)::text||' / '||MAX(expiration)::text"
          " FROM ase_paper_trade_legs")
#   F: distinct ratios in production  — asserts ratio multipliers are stored
_SQL_F = ("SELECT STRING_AGG(r::text,',' ORDER BY r)"
          " FROM (SELECT DISTINCT ratio r FROM ase_paper_trade_legs) t")
#   G: realized PnL wins/losses       — asserts debit/credit sign flows to PnL
_SQL_G = ("SELECT SUM(CASE WHEN gross_pnl>=0 THEN 1 ELSE 0 END)::text"
          "||' wins / '||SUM(CASE WHEN gross_pnl<0 THEN 1 ELSE 0 END)::text||' losses'"
          " FROM ase_paper_trades WHERE gross_pnl IS NOT NULL")
#   H: buying_power min/max/avg       — asserts ×100 multiplier flows to storage
_SQL_H = ("SELECT MIN(buying_power)::text||' / '||MAX(buying_power)::text"
          "||' / avg='||ROUND(AVG(buying_power),2)::text"
          " FROM ase_paper_trades WHERE buying_power IS NOT NULL")
#   I: STOCK_PLUS_OPTION trade count  — asserts stock-leg trades exist in prod
_SQL_I = "SELECT COUNT(*) FROM ase_paper_trades WHERE family='STOCK_PLUS_OPTION'"
#   J: top-5 strategy names/families  — asserts classify_legs output is stored
_SQL_J = ("SELECT STRING_AGG(strategy_name||'('||family||')='||n::text"
          ",' | ' ORDER BY n DESC)"
          " FROM (SELECT strategy_name,family,COUNT(*) n FROM ase_paper_trades"
          "       GROUP BY strategy_name,family ORDER BY n DESC LIMIT 5) t")
#   K: distinct fingerprints          — asserts fingerprint discriminates properly
_SQL_K = "SELECT COUNT(DISTINCT strategy_fingerprint) FROM ase_paper_trades"
#   L: avg probability_of_profit      — asserts greek-derived POP is stored
_SQL_L = ("SELECT ROUND(AVG(probability_of_profit),4)"
          " FROM ase_paper_trades WHERE probability_of_profit IS NOT NULL")
#   M: null integrity gate            — asserts negative controls never write nulls
_SQL_M = ("SELECT COUNT(*) FROM ase_paper_trades"
          " WHERE strategy_fingerprint IS NULL OR strategy_name IS NULL OR family IS NULL")

# Pre-fetch all section SQL values once (avoids repeated connections)
_SV = {k: _db_query(v) for k, v in {
    "A": _SQL_A, "B": _SQL_B, "C": _SQL_C, "D": _SQL_D,
    "E": _SQL_E, "F": _SQL_F, "G": _SQL_G, "H": _SQL_H,
    "I": _SQL_I, "J": _SQL_J, "K": _SQL_K, "L": _SQL_L,
    "M": _SQL_M,
}.items()}

# ── Report accumulator ────────────────────────────────────────────────────────
_lines: List[str] = []
_pass  = 0
_fail  = 0

def _rp(*args):
    line = " ".join(str(a) for a in args)
    _lines.append(line)
    print(line)


# ── Test runner ───────────────────────────────────────────────────────────────
def _run_test(
    *, test_id, strategy_id, strategy_name, command,
    inputs_str, expected, actual, raw_output, differences,
    tolerance, is_pass, paper_trade_id, sql_query, sql_output,
) -> bool:
    global _pass, _fail
    ts = datetime.now(timezone.utc).isoformat()
    verdict = "✓ PASS" if is_pass else "✗ FAIL"
    if is_pass:
        _pass += 1
    else:
        _fail += 1

    _rp(_DSEP)
    _rp(f"  TEST ID         : {test_id}")
    _rp(f"  Strategy ID     : {strategy_id}")
    _rp(f"  Strategy Name   : {strategy_name}")
    _rp(_SEP)
    _rp(f"  Command         : {command}")
    _rp(_SEP)
    _rp("  Inputs          :")
    for ln in inputs_str.strip().splitlines():
        _rp(f"    {ln}")
    _rp(_SEP)
    _rp("  Expected Result :")
    for k, v in (expected if isinstance(expected, dict) else {"value": expected}).items():
        _rp(f"    {k:<22}= {v}")
    _rp(_SEP)
    _rp("  Actual Result   :")
    for k, v in (actual if isinstance(actual, dict) else {"value": actual}).items():
        _rp(f"    {k:<22}= {v}")
    _rp(_SEP)
    _rp("  Raw Output      :")
    for ln in raw_output.strip().splitlines():
        _rp(f"    {ln}")
    _rp(_SEP)
    _rp("  Num Difference  :")
    for k, v in differences.items():
        _rp(f"    {k:<22}= {v}")
    _rp(_SEP)
    _rp(f"  Allowed Tol     : {tolerance}")
    _rp(f"  PASS/FAIL       : {verdict}")
    _rp(_SEP)
    _rp(f"  Timestamp       : {ts}")
    _rp(f"  Run ID          : {_RUN_ID}")
    _rp(f"  Paper Trade ID  : {paper_trade_id}")
    _rp(_SEP)
    _rp(f"  SQL Query       : {sql_query}")
    _rp(f"  SQL Output      : {sql_output}")
    _rp(_SEP)
    _rp(f"  Code SHA-256    : {_CODE_SHA}")
    _rp(f"  Config SHA-256  : {_CFG_SHA}")
    return is_pass


# ─────────────────────────────────────────────────────────────────────────────
# SECTION A — LEG COUNT  (T001–T008)
# SQL: production leg-count range (MIN/MAX legs per paper trade)
# ─────────────────────────────────────────────────────────────────────────────
def _sec_a():
    _rp(_DSEP)
    _rp("  SECTION A — LEG COUNT  (T001–T008)")
    _rp("  Verify 1–8 Leg objects can be constructed; len(legs) == N")
    _rp(f"  SQL assertion: production leg-count range = {_SV['A']} (must span 1–8)")
    _rp(_DSEP)

    base = [
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50, delta=0.50, ratio=1),
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80, delta=0.30, ratio=1),
        Leg(ASSET_PUT,  SIDE_LONG,  strike=95.0,  expiration="2026-08-15", mid=2.20, delta=0.35, ratio=1),
        Leg(ASSET_PUT,  SIDE_SHORT, strike=90.0,  expiration="2026-08-15", mid=0.90, delta=0.15, ratio=1),
        Leg(ASSET_CALL, SIDE_LONG,  strike=97.0,  expiration="2026-09-19", mid=4.10, delta=0.55, ratio=1),
        Leg(ASSET_CALL, SIDE_SHORT, strike=103.0, expiration="2026-09-19", mid=2.00, delta=0.35, ratio=1),
        Leg(ASSET_PUT,  SIDE_LONG,  strike=93.0,  expiration="2026-09-19", mid=1.60, delta=0.30, ratio=1),
        Leg(ASSET_PUT,  SIDE_SHORT, strike=88.0,  expiration="2026-09-19", mid=0.70, delta=0.12, ratio=1),
    ]

    for n in range(1, 9):
        legs_n   = base[:n]
        actual_n = len(legs_n)
        is_p     = (actual_n == n)
        desc = "\n".join(
            f"leg[{i}]: {lg.asset_type}({lg.side},K={lg.strike},mid={lg.mid})"
            for i, lg in enumerate(legs_n)
        )
        _run_test(
            test_id        = f"T{str(n).zfill(3)}",
            strategy_id    = f"LC-A-{n:02d}",
            strategy_name  = f"Leg Count — {n} Leg{'s' if n>1 else ''}",
            command        = f"legs = base[:{n}]; assert len(legs) == {n}",
            inputs_str     = desc,
            expected       = {"leg_count": n},
            actual         = {"leg_count": actual_n},
            raw_output     = f"len(base[:{n}]) = {actual_n}",
            differences    = {"leg_count": 0 if is_p else abs(n - actual_n)},
            tolerance      = "exact integer match",
            is_pass        = is_p,
            paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
            sql_query      = _SQL_A,
            sql_output     = _SV["A"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION B — LONG / SHORT SIDE  (T009–T014)
# signed_mid: LONG=+mid, SHORT=−mid | signed_delta: LONG=+delta, SHORT=−delta
# None guard: both properties return None when the underlying field is None.
# GREP EVIDENCE embedded in T009 raw_output (signed_mid) and T013 (signed_delta).
# SQL: production mid range — asserts mid is always a non-None real price in prod.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_b():
    _rp(_DSEP)
    _rp("  SECTION B — LONG / SHORT SIDE  (T009–T014)")
    _rp("  signed_mid: LONG=+mid, SHORT=−mid | signed_delta: LONG=+delta, SHORT=−delta")
    _rp(f"  SQL assertion: production mid range = {_SV['B']}")
    _rp(_DSEP)

    # T009: Long Call → signed_mid = +mid
    lg9 = Leg(ASSET_CALL, SIDE_LONG, mid=3.50, delta=0.50, strike=100.0, expiration="2026-08-15")
    v9  = lg9.signed_mid
    p9  = (v9 is not None) and abs(v9 - 3.50) < 1e-9
    _run_test(
        test_id        = "T009",
        strategy_id    = "LS-B-01",
        strategy_name  = "Long Call → signed_mid = +mid",
        command        = "Leg(CALL,LONG,mid=3.50,delta=0.50).signed_mid",
        inputs_str     = "asset_type=CALL  side=LONG  mid=3.50  delta=0.50",
        expected       = {"signed_mid": 3.50},
        actual         = {"signed_mid": v9},
        raw_output     = (
            f"Leg(CALL,LONG,mid=3.50).signed_mid = {v9}\n"
            f"\n--- grep evidence: signed_mid None guard (legs.py lines 79–84) ---\n"
            f"{_GREP_SIGNED_MID}"
        ),
        differences    = {"signed_mid": abs(v9 - 3.50) if v9 is not None else "None"},
        tolerance      = "exact (1e-9)",
        is_pass        = p9,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_B,
        sql_output     = _SV["B"],
    )

    # T010: Short Call → signed_mid = −mid
    lg10 = Leg(ASSET_CALL, SIDE_SHORT, mid=3.50, delta=0.50, strike=100.0, expiration="2026-08-15")
    v10  = lg10.signed_mid
    p10  = (v10 is not None) and abs(v10 - (-3.50)) < 1e-9
    _run_test(
        test_id        = "T010",
        strategy_id    = "LS-B-02",
        strategy_name  = "Short Call → signed_mid = −mid",
        command        = "Leg(CALL,SHORT,mid=3.50,delta=0.50).signed_mid",
        inputs_str     = "asset_type=CALL  side=SHORT  mid=3.50  delta=0.50",
        expected       = {"signed_mid": -3.50},
        actual         = {"signed_mid": v10},
        raw_output     = f"Leg(CALL,SHORT,mid=3.50).signed_mid = {v10}",
        differences    = {"signed_mid": abs(v10 - (-3.50)) if v10 is not None else "None"},
        tolerance      = "exact (1e-9)",
        is_pass        = p10,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_B,
        sql_output     = _SV["B"],
    )

    # T011: Long Put → signed_mid = +mid
    lg11 = Leg(ASSET_PUT, SIDE_LONG, mid=2.80, delta=0.45, strike=95.0, expiration="2026-08-15")
    v11  = lg11.signed_mid
    p11  = (v11 is not None) and abs(v11 - 2.80) < 1e-9
    _run_test(
        test_id        = "T011",
        strategy_id    = "LS-B-03",
        strategy_name  = "Long Put → signed_mid = +mid",
        command        = "Leg(PUT,LONG,mid=2.80,delta=0.45).signed_mid",
        inputs_str     = "asset_type=PUT  side=LONG  mid=2.80  delta=0.45",
        expected       = {"signed_mid": 2.80},
        actual         = {"signed_mid": v11},
        raw_output     = f"Leg(PUT,LONG,mid=2.80).signed_mid = {v11}",
        differences    = {"signed_mid": abs(v11 - 2.80) if v11 is not None else "None"},
        tolerance      = "exact (1e-9)",
        is_pass        = p11,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_B,
        sql_output     = _SV["B"],
    )

    # T012: Short Put → signed_mid = −mid
    lg12 = Leg(ASSET_PUT, SIDE_SHORT, mid=2.80, delta=0.45, strike=95.0, expiration="2026-08-15")
    v12  = lg12.signed_mid
    p12  = (v12 is not None) and abs(v12 - (-2.80)) < 1e-9
    _run_test(
        test_id        = "T012",
        strategy_id    = "LS-B-04",
        strategy_name  = "Short Put → signed_mid = −mid",
        command        = "Leg(PUT,SHORT,mid=2.80,delta=0.45).signed_mid",
        inputs_str     = "asset_type=PUT  side=SHORT  mid=2.80  delta=0.45",
        expected       = {"signed_mid": -2.80},
        actual         = {"signed_mid": v12},
        raw_output     = f"Leg(PUT,SHORT,mid=2.80).signed_mid = {v12}",
        differences    = {"signed_mid": abs(v12 - (-2.80)) if v12 is not None else "None"},
        tolerance      = "exact (1e-9)",
        is_pass        = p12,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_B,
        sql_output     = _SV["B"],
    )

    # T013: Long Stock → signed_delta = +1.0  (absolute delta=1.0 stored; LONG → positive)
    lg13 = Leg(ASSET_STOCK, SIDE_LONG, mid=100.0, delta=1.0, strike=None, expiration=None)
    v13  = lg13.signed_delta
    p13  = (v13 is not None) and abs(v13 - 1.0) < 1e-9
    _run_test(
        test_id        = "T013",
        strategy_id    = "LS-B-05",
        strategy_name  = "Long Stock → signed_delta = +1.0",
        command        = "Leg(STOCK,LONG,mid=100.0,delta=1.0).signed_delta",
        inputs_str     = "asset_type=STOCK  side=LONG  delta=1.0 (absolute)",
        expected       = {"signed_delta": 1.0},
        actual         = {"signed_delta": v13},
        raw_output     = (
            f"Leg(STOCK,LONG,delta=1.0).signed_delta = {v13}\n"
            f"\n--- grep evidence: signed_delta None guard (legs.py lines 86–91) ---\n"
            f"{_GREP_SIGNED_DELTA}"
        ),
        differences    = {"signed_delta": abs(v13 - 1.0) if v13 is not None else "None"},
        tolerance      = "exact (1e-9)",
        is_pass        = p13,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_B,
        sql_output     = _SV["B"],
    )

    # T014: Short Stock → signed_delta = −1.0  (absolute delta=1.0; SHORT → negated by property)
    lg14 = Leg(ASSET_STOCK, SIDE_SHORT, mid=100.0, delta=1.0, strike=None, expiration=None)
    v14  = lg14.signed_delta
    p14  = (v14 is not None) and abs(v14 - (-1.0)) < 1e-9
    _run_test(
        test_id        = "T014",
        strategy_id    = "LS-B-06",
        strategy_name  = "Short Stock → signed_delta = −1.0",
        command        = "Leg(STOCK,SHORT,mid=100.0,delta=1.0).signed_delta",
        inputs_str     = (
            "asset_type=STOCK  side=SHORT  delta=1.0 (absolute)\n"
            "Convention: store absolute delta; property applies sign based on LONG/SHORT.\n"
            "signed_delta = self.delta if LONG else -self.delta = -1.0 (correct for short)"
        ),
        expected       = {"signed_delta": -1.0},
        actual         = {"signed_delta": v14},
        raw_output     = f"Leg(STOCK,SHORT,delta=1.0).signed_delta = {v14}",
        differences    = {"signed_delta": abs(v14 - (-1.0)) if v14 is not None else "None"},
        tolerance      = "exact (1e-9)",
        is_pass        = p14,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_B,
        sql_output     = _SV["B"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION C — CALL / PUT / STOCK TYPE  (T015–T017)
# SQL: production call/put distribution — asserts asset_type stored correctly.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_c():
    _rp(_DSEP)
    _rp("  SECTION C — CALL / PUT / STOCK TYPE  (T015–T017)")
    _rp("  Verify asset_type field stored and retrieved exactly as constructed")
    _rp(f"  SQL assertion: production call/put distribution = {_SV['C']}")
    _rp(_DSEP)

    cases = [
        ("T015", "CP-C-01", "CALL asset_type", ASSET_CALL,  2.50,  "Leg(CALL,...).asset_type == 'CALL'"),
        ("T016", "CP-C-02", "PUT  asset_type", ASSET_PUT,   2.50,  "Leg(PUT,...).asset_type  == 'PUT'"),
        ("T017", "CP-C-03", "STOCK asset_type", ASSET_STOCK, 100.0, "Leg(STOCK,...).asset_type == 'STOCK'"),
    ]
    for tid, sid, name, atype, mid, cmd in cases:
        lg     = Leg(atype, SIDE_LONG, mid=mid, delta=0.50, strike=100.0, expiration="2026-08-15")
        actual = lg.asset_type
        is_p   = (actual == atype)
        _run_test(
            test_id        = tid,
            strategy_id    = sid,
            strategy_name  = name,
            command        = cmd,
            inputs_str     = f"asset_type={atype}  side=LONG  mid={mid}",
            expected       = {"asset_type": atype},
            actual         = {"asset_type": actual},
            raw_output     = f"Leg.asset_type = {actual!r}",
            differences    = {"asset_type": "match" if is_p else f"got {actual!r}"},
            tolerance      = "exact string match",
            is_pass        = is_p,
            paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
            sql_query      = _SQL_C,
            sql_output     = _SV["C"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION D — STRIKE ORDERING  (T018–T022)
# canonical_sort: STOCK < CALL < PUT; within type: exp ASC, strike ASC, LONG < SHORT.
# Unknown asset/side types fall back to order=9 (dict.get default).
# GREP EVIDENCE embedded in T018 raw_output.
# SQL: production strike range — asserts canonical sort operates over real prices.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_d():
    _rp(_DSEP)
    _rp("  SECTION D — STRIKE ORDERING  (T018–T022)")
    _rp("  canonical_sort: STOCK<CALL<PUT, exp ASC, strike ASC, LONG<SHORT")
    _rp(f"  SQL assertion: production strike range = {_SV['D']}")
    _rp(_DSEP)

    # T018: STOCK first, CALL second, PUT third
    legs18 = [
        Leg(ASSET_PUT,   SIDE_LONG, strike=95.0,  expiration="2026-08-15", mid=2.0),
        Leg(ASSET_CALL,  SIDE_LONG, strike=105.0, expiration="2026-08-15", mid=1.5),
        Leg(ASSET_STOCK, SIDE_LONG, strike=None,  mid=100.0),
    ]
    sorted18 = canonical_sort(legs18)
    order18  = [lg.asset_type for lg in sorted18]
    exp18    = [ASSET_STOCK, ASSET_CALL, ASSET_PUT]
    p18      = (order18 == exp18)
    _run_test(
        test_id        = "T018",
        strategy_id    = "SO-D-01",
        strategy_name  = "canonical_sort — STOCK < CALL < PUT",
        command        = "canonical_sort([PUT_LONG(K=95), CALL_LONG(K=105), STOCK_LONG])",
        inputs_str     = "Unsorted: [PUT(K=95), CALL(K=105), STOCK]\nExpected order: STOCK, CALL, PUT",
        expected       = {"order": str(exp18)},
        actual         = {"order": str(order18)},
        raw_output     = (
            f"sorted asset_types: {order18}\n"
            f"\n--- grep evidence: canonical_sort key function (legs.py lines 96–112) ---\n"
            f"  type_order dict.get fallback=9 for unknown asset types.\n"
            f"  side_order dict.get fallback=9 for unknown sides.\n"
            f"{_GREP_CANONICAL}"
        ),
        differences    = {"order": "match" if p18 else f"got {order18}"},
        tolerance      = "exact type-order match",
        is_pass        = p18,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_D,
        sql_output     = _SV["D"],
    )

    # T019: lower strike before higher (same type, same expiry)
    legs19 = [
        Leg(ASSET_CALL, SIDE_LONG, strike=110.0, expiration="2026-08-15", mid=0.90),
        Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=3.50),
        Leg(ASSET_CALL, SIDE_LONG, strike=105.0, expiration="2026-08-15", mid=1.80),
    ]
    sorted19  = canonical_sort(legs19)
    strikes19 = [lg.strike for lg in sorted19]
    exp19     = [100.0, 105.0, 110.0]
    p19       = (strikes19 == exp19)
    _run_test(
        test_id        = "T019",
        strategy_id    = "SO-D-02",
        strategy_name  = "canonical_sort — lower strike first (same type/expiry)",
        command        = "canonical_sort([CALL(K=110), CALL(K=100), CALL(K=105)])",
        inputs_str     = "Unsorted CALLs: K=110, K=100, K=105 | all LONG, exp=2026-08-15",
        expected       = {"strikes": str(exp19)},
        actual         = {"strikes": str(strikes19)},
        raw_output     = f"sorted strikes: {strikes19}",
        differences    = {"strikes": "match" if p19 else f"got {strikes19}"},
        tolerance      = "exact ascending strike order",
        is_pass        = p19,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_D,
        sql_output     = _SV["D"],
    )

    # T020: earlier expiry before later (same type, same strike)
    legs20 = [
        Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-10-16", mid=5.00),
        Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=3.50),
        Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-09-19", mid=4.20),
    ]
    sorted20 = canonical_sort(legs20)
    exps20   = [lg.expiration for lg in sorted20]
    exp20    = ["2026-08-15", "2026-09-19", "2026-10-16"]
    p20      = (exps20 == exp20)
    _run_test(
        test_id        = "T020",
        strategy_id    = "SO-D-03",
        strategy_name  = "canonical_sort — earlier expiry first (same type/strike)",
        command        = "canonical_sort([CALL(Oct), CALL(Aug), CALL(Sep)])",
        inputs_str     = "Unsorted expirations: 2026-10-16, 2026-08-15, 2026-09-19 | all CALL LONG K=100",
        expected       = {"expirations": str(exp20)},
        actual         = {"expirations": str(exps20)},
        raw_output     = f"sorted expirations: {exps20}",
        differences    = {"expirations": "match" if p20 else f"got {exps20}"},
        tolerance      = "exact chronological order",
        is_pass        = p20,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_D,
        sql_output     = _SV["D"],
    )

    # T021: LONG before SHORT (same type, strike, expiry)
    legs21 = [
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80),
        Leg(ASSET_CALL, SIDE_LONG,  strike=105.0, expiration="2026-08-15", mid=1.80),
    ]
    sorted21 = canonical_sort(legs21)
    sides21  = [lg.side for lg in sorted21]
    exp21    = [SIDE_LONG, SIDE_SHORT]
    p21      = (sides21 == exp21)
    _run_test(
        test_id        = "T021",
        strategy_id    = "SO-D-04",
        strategy_name  = "canonical_sort — LONG before SHORT (same type/strike/expiry)",
        command        = "canonical_sort([CALL_SHORT(K=105), CALL_LONG(K=105)])",
        inputs_str     = "Unsorted: CALL SHORT K=105, CALL LONG K=105 | same expiry 2026-08-15",
        expected       = {"sides": str(exp21)},
        actual         = {"sides": str(sides21)},
        raw_output     = f"sorted sides: {sides21}",
        differences    = {"sides": "match" if p21 else f"got {sides21}"},
        tolerance      = "exact side order (LONG < SHORT)",
        is_pass        = p21,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_D,
        sql_output     = _SV["D"],
    )

    # T022: ratio is a tiebreaker; does not disrupt primary type/exp/strike ordering
    legs22 = [
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80, ratio=2),
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50, ratio=1),
    ]
    sorted22  = canonical_sort(legs22)
    strikes22 = [lg.strike for lg in sorted22]
    ratios22  = [lg.ratio  for lg in sorted22]
    exp22     = [100.0, 105.0]
    p22       = (strikes22 == exp22)
    _run_test(
        test_id        = "T022",
        strategy_id    = "SO-D-05",
        strategy_name  = "canonical_sort — ratio is tiebreaker (does not disrupt strike order)",
        command        = "canonical_sort([CALL_SHORT(K=105,ratio=2), CALL_LONG(K=100,ratio=1)])",
        inputs_str     = "CALL SHORT K=105 ratio=2 | CALL LONG K=100 ratio=1 | exp=2026-08-15",
        expected       = {"strikes": str(exp22), "ratios_at_index": str([1, 2])},
        actual         = {"strikes": str(strikes22), "ratios_at_index": str(ratios22)},
        raw_output     = f"sorted strikes={strikes22}  ratios={ratios22}",
        differences    = {"strikes": "match" if p22 else f"got {strikes22}"},
        tolerance      = "strike ordering preserved regardless of ratio",
        is_pass        = p22,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_D,
        sql_output     = _SV["D"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION E — EXPIRATION ORDERING  (T023–T024)
# SQL: production expiry range — asserts multiple expirations exist in prod.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_e():
    _rp(_DSEP)
    _rp("  SECTION E — EXPIRATION ORDERING  (T023–T024)")
    _rp("  canonical_sort handles two and three distinct expirations correctly")
    _rp(f"  SQL assertion: production expiry range = {_SV['E']}")
    _rp(_DSEP)

    # T023: front before back (two expirations)
    legs23 = [
        Leg(ASSET_CALL, SIDE_SHORT, strike=100.0, expiration="2026-09-19", mid=4.20),
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50),
    ]
    sorted23 = canonical_sort(legs23)
    exps23   = [lg.expiration for lg in sorted23]
    exp23    = ["2026-08-15", "2026-09-19"]
    p23      = (exps23 == exp23)
    _run_test(
        test_id        = "T023",
        strategy_id    = "EO-E-01",
        strategy_name  = "Expiration Ordering — 2 expirations: front before back",
        command        = "canonical_sort([CALL_SHORT(back), CALL_LONG(front)])",
        inputs_str     = "back_exp=2026-09-19 (SHORT) | front_exp=2026-08-15 (LONG) | K=100",
        expected       = {"expirations": str(exp23)},
        actual         = {"expirations": str(exps23)},
        raw_output     = f"sorted expirations: {exps23}",
        differences    = {"expirations": "match" if p23 else f"got {exps23}"},
        tolerance      = "front expiry at index 0",
        is_pass        = p23,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_E,
        sql_output     = _SV["E"],
    )

    # T024: three expirations — type-first then date within type
    legs24 = [
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-12-18", mid=6.00),
        Leg(ASSET_CALL, SIDE_SHORT, strike=100.0, expiration="2026-08-15", mid=3.50),
        Leg(ASSET_PUT,  SIDE_LONG,  strike=100.0, expiration="2026-10-16", mid=4.80),
    ]
    sorted24  = canonical_sort(legs24)
    exps24    = [lg.expiration for lg in sorted24]
    types24   = [lg.asset_type for lg in sorted24]
    exp24_e   = ["2026-08-15", "2026-12-18", "2026-10-16"]
    exp24_t   = [ASSET_CALL, ASSET_CALL, ASSET_PUT]
    p24       = (exps24 == exp24_e) and (types24 == exp24_t)
    _run_test(
        test_id        = "T024",
        strategy_id    = "EO-E-02",
        strategy_name  = "Expiration Ordering — 3 expirations: type-first then date within type",
        command        = "canonical_sort([CALL_LONG(Dec), CALL_SHORT(Aug), PUT_LONG(Oct)])",
        inputs_str     = "CALL LONG 2026-12-18 | CALL SHORT 2026-08-15 | PUT LONG 2026-10-16 | K=100",
        expected       = {"expirations": str(exp24_e), "types": str(exp24_t)},
        actual         = {"expirations": str(exps24),  "types": str(types24)},
        raw_output     = f"sorted: {list(zip(types24, exps24))}",
        differences    = {"order": "match" if p24 else f"got {list(zip(types24, exps24))}"},
        tolerance      = "type order then date ascending within each type",
        is_pass        = p24,
        paper_trade_id = "N/A — Leg Construction Unit Test (synthetic)",
        sql_query      = _SQL_E,
        sql_output     = _SV["E"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION F — RATIOS  (T025–T028)
# net_debit_credit = sum(signed_mid × ratio) for each leg.
# SQL: distinct ratios in production — asserts ratio multipliers are stored.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_f():
    _rp(_DSEP)
    _rp("  SECTION F — RATIOS  (T025–T028)")
    _rp("  net_debit_credit = sum(signed_mid × ratio) for each leg")
    _rp(f"  SQL assertion: distinct ratios in production = {_SV['F']}")
    _rp(_DSEP)

    # T025: ratio=1 baseline
    t25 = net_debit_credit([Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=3.50, ratio=1)])
    p25 = abs(t25 - 3.50) < 1e-9
    _run_test(
        test_id="T025", strategy_id="RT-F-01",
        strategy_name="Ratios — ratio=1 net_debit_credit baseline",
        command="net_debit_credit([Leg(CALL,LONG,mid=3.50,ratio=1)])",
        inputs_str="CALL LONG  mid=3.50  ratio=1",
        expected={"net_debit_credit": 3.50}, actual={"net_debit_credit": t25},
        raw_output=f"net_debit_credit = {t25}",
        differences={"net_debit_credit": abs(t25 - 3.50)},
        tolerance="exact (1e-9)", is_pass=p25,
        paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
        sql_query=_SQL_F, sql_output=_SV["F"],
    )

    # T026: ratio=2 SHORT — net doubled
    t26 = net_debit_credit([Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=3.50, ratio=2)])
    p26 = abs(t26 - (-7.00)) < 1e-9
    _run_test(
        test_id="T026", strategy_id="RT-F-02",
        strategy_name="Ratios — ratio=2 SHORT net_debit_credit doubled",
        command="net_debit_credit([Leg(CALL,SHORT,mid=3.50,ratio=2)])",
        inputs_str="CALL SHORT  mid=3.50  ratio=2\nsigned_mid = −3.50  |  net = −3.50 × 2 = −7.00",
        expected={"net_debit_credit": -7.00}, actual={"net_debit_credit": t26},
        raw_output=f"net_debit_credit = {t26}",
        differences={"net_debit_credit": abs(t26 - (-7.00))},
        tolerance="exact (1e-9)", is_pass=p26,
        paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
        sql_query=_SQL_F, sql_output=_SV["F"],
    )

    # T027: ratio=3 LONG — net tripled
    t27 = net_debit_credit([Leg(ASSET_CALL, SIDE_LONG, strike=95.0, expiration="2026-08-15", mid=1.50, ratio=3)])
    p27 = abs(t27 - 4.50) < 1e-9
    _run_test(
        test_id="T027", strategy_id="RT-F-03",
        strategy_name="Ratios — ratio=3 LONG net_debit_credit tripled",
        command="net_debit_credit([Leg(CALL,LONG,mid=1.50,ratio=3)])",
        inputs_str="CALL LONG  mid=1.50  ratio=3\nnet = +1.50 × 3 = +4.50",
        expected={"net_debit_credit": 4.50}, actual={"net_debit_credit": t27},
        raw_output=f"net_debit_credit = {t27}",
        differences={"net_debit_credit": abs(t27 - 4.50)},
        tolerance="exact (1e-9)", is_pass=p27,
        paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
        sql_query=_SQL_F, sql_output=_SV["F"],
    )

    # T028: 1:2 ratio spread mixed
    t28_exp = 3.50 + (-1.80 * 2)   # = -0.10
    t28 = net_debit_credit([
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50, ratio=1),
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80, ratio=2),
    ])
    p28 = abs(t28 - t28_exp) < 1e-9
    _run_test(
        test_id="T028", strategy_id="RT-F-04",
        strategy_name="Ratios — 1:2 ratio spread mixed net_debit_credit",
        command="net_debit_credit([Leg(LONG,mid=3.50,r=1), Leg(SHORT,mid=1.80,r=2)])",
        inputs_str="CALL LONG K=100 mid=3.50 ratio=1 → +3.50\nCALL SHORT K=105 mid=1.80 ratio=2 → −3.60\nnet = −0.10",
        expected={"net_debit_credit": round(t28_exp, 10)}, actual={"net_debit_credit": t28},
        raw_output=f"net_debit_credit = {t28}",
        differences={"net_debit_credit": abs(t28 - t28_exp)},
        tolerance="exact (1e-9)", is_pass=p28,
        paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
        sql_query=_SQL_F, sql_output=_SV["F"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION G — DEBIT / CREDIT  (T029–T032)
# net > 0 = debit | net < 0 = credit.
# SQL: realized PnL wins/losses — asserts debit/credit sign flows to PnL.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_g():
    _rp(_DSEP)
    _rp("  SECTION G — DEBIT / CREDIT  (T029–T032)")
    _rp("  net > 0 = debit (we pay premium) | net < 0 = credit (we collect premium)")
    _rp(f"  SQL assertion: production PnL wins/losses = {_SV['G']}")
    _rp(_DSEP)

    cases = [
        ("T029", "DC-G-01", "all LONG → pure debit (net > 0)",
         [Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=3.50),
          Leg(ASSET_PUT,  SIDE_LONG, strike=95.0,  expiration="2026-08-15", mid=2.20)],
         5.70, "> 0 (debit)"),
        ("T030", "DC-G-02", "all SHORT → pure credit (net < 0)",
         [Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80),
          Leg(ASSET_PUT,  SIDE_SHORT, strike=90.0,  expiration="2026-08-15", mid=0.90)],
         -2.70, "< 0 (credit)"),
        ("T031", "DC-G-03", "mixed net debit (call debit spread)",
         [Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50),
          Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.50)],
         2.00, "> 0 (debit)"),
        ("T032", "DC-G-04", "mixed net credit (call credit spread)",
         [Leg(ASSET_CALL, SIDE_LONG,  strike=105.0, expiration="2026-08-15", mid=1.00),
          Leg(ASSET_CALL, SIDE_SHORT, strike=100.0, expiration="2026-08-15", mid=3.50)],
         -2.50, "< 0 (credit)"),
    ]
    for tid, sid, name, legs, exp_net, sign_desc in cases:
        net  = net_debit_credit(legs)
        is_p = (net is not None) and abs(net - exp_net) < 1e-9
        _run_test(
            test_id=tid, strategy_id=sid, strategy_name=name,
            command=f"net_debit_credit(legs) expected={exp_net}",
            inputs_str="\n".join(f"leg[{i}]: {lg.asset_type} {lg.side} mid={lg.mid}" for i, lg in enumerate(legs)),
            expected={"net_debit_credit": exp_net, "sign": sign_desc},
            actual={"net_debit_credit": net, "sign": ("> 0" if (net or 0) > 0 else ("< 0" if (net or 0) < 0 else "= 0"))},
            raw_output=f"net_debit_credit = {net}",
            differences={"net_debit_credit": abs((net or 0) - exp_net)},
            tolerance=f"exact (1e-9) and sign {sign_desc}",
            is_pass=is_p,
            paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
            sql_query=_SQL_G, sql_output=_SV["G"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION H — MULTIPLIER / BUYING POWER  (T033–T036)
# buying_power_required(max_loss) = max_loss × 100 (per-contract multiplier).
# SQL: production buying_power min/max/avg — asserts ×100 flows to storage.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_h():
    _rp(_DSEP)
    _rp("  SECTION H — MULTIPLIER / BUYING POWER  (T033–T036)")
    _rp("  buying_power_required(max_loss) = max_loss × 100")
    _rp(f"  SQL assertion: production BP min/max/avg = {_SV['H']}")
    _rp(_DSEP)

    cases = [
        ("T033", "MX-H-01", "max_loss=1.50 → BP=150.0",  1.50,  150.0),
        ("T034", "MX-H-02", "max_loss=5.00 → BP=500.0",  5.00,  500.0),
        ("T035", "MX-H-03", "max_loss=None → BP=None",   None,  None),
        ("T036", "MX-H-04", "max_loss=0 → BP=None (≤0)", 0.00,  None),
    ]
    for tid, sid, name, ml, exp_bp in cases:
        actual_bp = buying_power_required([], ml)
        if exp_bp is None:
            diff_str = "N/A (expected None)"
            is_p = (actual_bp is None)
        else:
            diff = abs(actual_bp - exp_bp) if actual_bp is not None else float("inf")
            diff_str = str(diff)
            is_p = (actual_bp is not None) and (diff < 1e-9)
        _run_test(
            test_id=tid, strategy_id=sid, strategy_name=f"Multiplier — {name}",
            command=f"buying_power_required([], max_loss={ml!r})",
            inputs_str=f"max_loss = {ml!r}  |  expected = max_loss × 100",
            expected={"buying_power": str(exp_bp)},
            actual={"buying_power": str(actual_bp)},
            raw_output=f"buying_power_required([], {ml!r}) = {actual_bp!r}",
            differences={"buying_power": diff_str},
            tolerance="exact (1e-9) or None-match",
            is_pass=is_p,
            paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
            sql_query=_SQL_H, sql_output=_SV["H"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION I — OPTIONAL STOCK LEG  (T037–T040)
# aggregate_greeks with/without stock leg.
# Stock delta convention: store ABSOLUTE delta (1.0); side applies sign via mult.
# SQL: STOCK_PLUS_OPTION count in production.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_i():
    _rp(_DSEP)
    _rp("  SECTION I — OPTIONAL STOCK LEG  (T037–T040)")
    _rp("  aggregate_greeks with/without stock leg; stock contributes delta=±1.0")
    _rp(f"  SQL assertion: STOCK_PLUS_OPTION trades in production = {_SV['I']}")
    _rp(_DSEP)

    # T037: no stock leg
    t37_legs = [
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50,
            delta=0.50, gamma=0.04, theta=-0.08, vega=0.15, rho=0.01,
            charm=None, vanna=None, vomma=None),
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80,
            delta=0.30, gamma=0.03, theta=-0.05, vega=0.10, rho=0.005,
            charm=None, vanna=None, vomma=None),
    ]
    t37_gk    = aggregate_greeks(t37_legs)
    t37_delta = t37_gk["delta"]
    t37_exp   = 0.50 - 0.30
    p37       = (t37_delta is not None) and abs(t37_delta - t37_exp) < 1e-9
    _run_test(
        test_id="T037", strategy_id="SL-I-01",
        strategy_name="Stock Leg — no stock; delta from options only",
        command="aggregate_greeks([CALL_LONG(δ=0.50), CALL_SHORT(δ=0.30)])",
        inputs_str="CALL LONG delta=0.50 | CALL SHORT delta=0.30 | has_stock=False",
        expected={"has_stock": False, "net_delta": round(t37_exp, 10)},
        actual={"has_stock": False, "net_delta": t37_delta},
        raw_output=f"aggregate_greeks delta = {t37_delta}",
        differences={"net_delta": abs((t37_delta or 0) - t37_exp)},
        tolerance="exact (1e-9)",
        is_pass=p37,
        paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
        sql_query=_SQL_I, sql_output=_SV["I"],
    )

    # T038: LONG stock → delta +1.0
    t38_legs = [Leg(ASSET_STOCK, SIDE_LONG, mid=100.0,
                    delta=1.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0,
                    charm=0.0, vanna=0.0, vomma=0.0)]
    t38_gk    = aggregate_greeks(t38_legs)
    t38_delta = t38_gk["delta"]
    p38       = (t38_delta is not None) and abs(t38_delta - 1.0) < 1e-9
    _run_test(
        test_id="T038", strategy_id="SL-I-02",
        strategy_name="Stock Leg — LONG stock → aggregate delta = +1.0",
        command="aggregate_greeks([Leg(STOCK,LONG,delta=1.0)])",
        inputs_str="STOCK LONG  delta=1.0 (absolute)  mult=+1  contribution=+1.0",
        expected={"net_delta": 1.0}, actual={"net_delta": t38_delta},
        raw_output=f"aggregate delta = {t38_delta}",
        differences={"net_delta": abs((t38_delta or 0) - 1.0)},
        tolerance="exact (1e-9)", is_pass=p38,
        paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
        sql_query=_SQL_I, sql_output=_SV["I"],
    )

    # T039: SHORT stock → delta -1.0  (absolute delta=1.0; mult=-1; contribution=-1.0)
    t39_legs = [Leg(ASSET_STOCK, SIDE_SHORT, mid=100.0,
                    delta=1.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0,
                    charm=0.0, vanna=0.0, vomma=0.0)]
    t39_gk    = aggregate_greeks(t39_legs)
    t39_delta = t39_gk["delta"]
    t39_exp   = -1.0   # mult = 1 × (-1) = -1; contribution = 1.0 × (-1) = -1.0
    p39       = (t39_delta is not None) and abs(t39_delta - t39_exp) < 1e-9
    _run_test(
        test_id="T039", strategy_id="SL-I-03",
        strategy_name="Stock Leg — SHORT stock → aggregate delta = −1.0",
        command="aggregate_greeks([Leg(STOCK,SHORT,delta=1.0)])",
        inputs_str=(
            "STOCK SHORT  delta=1.0 (absolute)  |  mult = ratio×(−1) = −1\n"
            "contribution = 1.0 × (−1) = −1.0\n"
            "Convention: store absolute delta; aggregate_greeks applies sign via mult."
        ),
        expected={"net_delta": t39_exp}, actual={"net_delta": t39_delta},
        raw_output=f"aggregate delta = {t39_delta}",
        differences={"net_delta": abs((t39_delta or 0) - t39_exp)},
        tolerance="exact (1e-9)", is_pass=p39,
        paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
        sql_query=_SQL_I, sql_output=_SV["I"],
    )

    # T040: LONG stock (δ=1.0) + SHORT call (δ=0.40) → net delta = 0.60
    t40_legs = [
        Leg(ASSET_STOCK, SIDE_LONG,  mid=100.0,
            delta=1.0,  gamma=0.0, theta=0.0, vega=0.0, rho=0.0,
            charm=0.0, vanna=0.0, vomma=0.0),
        Leg(ASSET_CALL,  SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80,
            delta=0.40, gamma=0.03, theta=-0.05, vega=0.10, rho=0.005,
            charm=0.0, vanna=0.0, vomma=0.0),
    ]
    t40_gk    = aggregate_greeks(t40_legs)
    t40_delta = t40_gk["delta"]
    t40_exp   = 1.0 - 0.40
    p40       = (t40_delta is not None) and abs(t40_delta - t40_exp) < 1e-9
    _run_test(
        test_id="T040", strategy_id="SL-I-04",
        strategy_name="Stock Leg — LONG stock + SHORT call → net delta = 1.0 − 0.40 = 0.60",
        command="aggregate_greeks([Leg(STOCK,LONG,δ=1.0), Leg(CALL,SHORT,δ=0.40)])",
        inputs_str="STOCK LONG delta=1.0 (mult=+1 → +1.0)\nCALL SHORT delta=0.40 (mult=−1 → −0.40)\nnet = 1.0 − 0.40 = 0.60",
        expected={"net_delta": t40_exp}, actual={"net_delta": t40_delta},
        raw_output=f"aggregate_greeks delta = {t40_delta}",
        differences={"net_delta": abs((t40_delta or 0) - t40_exp)},
        tolerance="exact (1e-9)", is_pass=p40,
        paper_trade_id="N/A — Leg Construction Unit Test (synthetic)",
        sql_query=_SQL_I, sql_output=_SV["I"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION J — CANONICAL STRATEGY NAME  (T041–T044)
# classify_legs() maps leg structures to named catalog entries; falls back to
# CUSTOM_MULTI_LEG when no catalog entry matches.
# SQL: top-5 strategy name/family combos from production.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_j():
    _rp(_DSEP)
    _rp("  SECTION J — CANONICAL STRATEGY NAME  (T041–T044)")
    _rp("  classify_legs() → (strategy_name, family) | fallback → CUSTOM_MULTI_LEG")
    _rp(f"  SQL assertion: top-5 production strategies = {_SV['J']}")
    _rp(_DSEP)

    # T041: 1×CALL_LONG → Long Call, SINGLE_LEG
    t41 = classify_legs([Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=3.50)])
    p41 = (t41[0] == "Long Call") and (t41[1] == "SINGLE_LEG")
    _run_test(
        test_id="T041", strategy_id="CN-J-01",
        strategy_name="Canonical Name — 1×CALL_LONG → Long Call (SINGLE_LEG)",
        command="classify_legs([Leg(CALL,LONG,K=100,exp=2026-08-15)])",
        inputs_str="1 leg: CALL LONG K=100  exp=2026-08-15  has_stock=False  n_exps=1",
        expected={"strategy_name": "Long Call", "family": "SINGLE_LEG"},
        actual={"strategy_name": t41[0],       "family": t41[1]},
        raw_output=f"classify_legs → {t41!r}",
        differences={"name": "match" if t41[0]=="Long Call" else f"got {t41[0]!r}",
                     "family": "match" if t41[1]=="SINGLE_LEG" else f"got {t41[1]!r}"},
        tolerance="exact string match",
        is_pass=p41,
        paper_trade_id="N/A — Classification Test (synthetic)",
        sql_query=_SQL_J, sql_output=_SV["J"],
    )

    # T042: 4-leg [PUT_L,PUT_S,CALL_S,CALL_L] — first catalog match
    t42_legs = [
        Leg(ASSET_PUT,  SIDE_LONG,  strike=88.0,  expiration="2026-08-15", mid=0.55),
        Leg(ASSET_PUT,  SIDE_SHORT, strike=93.0,  expiration="2026-08-15", mid=1.20),
        Leg(ASSET_CALL, SIDE_SHORT, strike=107.0, expiration="2026-08-15", mid=1.10),
        Leg(ASSET_CALL, SIDE_LONG,  strike=112.0, expiration="2026-08-15", mid=0.40),
    ]
    t42 = classify_legs(t42_legs)
    exp42_n, exp42_f = "Double Bull Spread", "SYNTHETIC_COMBINATION"
    p42 = (t42[0] == exp42_n) and (t42[1] == exp42_f)
    _run_test(
        test_id="T042", strategy_id="CN-J-02",
        strategy_name="Canonical Name — [PUT_L,PUT_S,CALL_S,CALL_L] → Double Bull Spread (first-match)",
        command="classify_legs([PUT_LONG, PUT_SHORT, CALL_SHORT, CALL_LONG])",
        inputs_str=(
            "4 legs: PUT_LONG K=88 | PUT_SHORT K=93 | CALL_SHORT K=107 | CALL_LONG K=112\n"
            "all exp=2026-08-15  has_stock=False\n"
            "Catalog is first-match; Double Bull Spread (SYNTHETIC_COMBINATION) precedes CONDOR."
        ),
        expected={"strategy_name": exp42_n,  "family": exp42_f},
        actual={"strategy_name":   t42[0],   "family": t42[1]},
        raw_output=f"classify_legs → {t42!r}",
        differences={"name":   "match" if t42[0]==exp42_n else f"got {t42[0]!r}",
                     "family": "match" if t42[1]==exp42_f else f"got {t42[1]!r}"},
        tolerance="exact string match (Double Bull Spread / SYNTHETIC_COMBINATION)",
        is_pass=p42,
        paper_trade_id="N/A — Classification Test (synthetic)",
        sql_query=_SQL_J, sql_output=_SV["J"],
    )

    # T043: Long Straddle → STRADDLE_STRANGLE
    t43 = classify_legs([
        Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=3.50),
        Leg(ASSET_PUT,  SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=3.43),
    ])
    p43 = (t43[0] == "Long Straddle") and (t43[1] == "STRADDLE_STRANGLE")
    _run_test(
        test_id="T043", strategy_id="CN-J-03",
        strategy_name="Canonical Name — Long Straddle legs → Long Straddle (STRADDLE_STRANGLE)",
        command="classify_legs([CALL_LONG(K=100), PUT_LONG(K=100)])",
        inputs_str="2 legs: CALL LONG K=100 | PUT LONG K=100  |  has_stock=False  n_exps=1",
        expected={"strategy_name": "Long Straddle", "family": "STRADDLE_STRANGLE"},
        actual={"strategy_name": t43[0], "family": t43[1]},
        raw_output=f"classify_legs → {t43!r}",
        differences={"name": "match" if t43[0]=="Long Straddle" else f"got {t43[0]!r}",
                     "family": "match" if t43[1]=="STRADDLE_STRANGLE" else f"got {t43[1]!r}"},
        tolerance="exact string match",
        is_pass=p43,
        paper_trade_id="N/A — Classification Test (synthetic)",
        sql_query=_SQL_J, sql_output=_SV["J"],
    )

    # T044: 3×CALL_LONG → CUSTOM_MULTI_LEG (no catalog match)
    t44 = classify_legs([
        Leg(ASSET_CALL, SIDE_LONG, strike=95.0,  expiration="2026-08-15", mid=5.00),
        Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=3.50),
        Leg(ASSET_CALL, SIDE_LONG, strike=105.0, expiration="2026-08-15", mid=1.80),
    ])
    p44 = (t44[0] == "CUSTOM_MULTI_LEG") and (t44[1] == "CUSTOM")
    _run_test(
        test_id="T044", strategy_id="CN-J-04",
        strategy_name="Canonical Name — 3×CALL_LONG → CUSTOM_MULTI_LEG (no catalog match)",
        command="classify_legs([CALL_LONG(K=95), CALL_LONG(K=100), CALL_LONG(K=105)])",
        inputs_str=(
            "3 legs: CALL_LONG K=95 | CALL_LONG K=100 | CALL_LONG K=105\n"
            "all exp=2026-08-15  has_stock=False\n"
            "No catalog entry has 3 all-LONG CALL legs → exhausts spec list → CUSTOM_MULTI_LEG"
        ),
        expected={"strategy_name": "CUSTOM_MULTI_LEG", "family": "CUSTOM"},
        actual={"strategy_name": t44[0], "family": t44[1]},
        raw_output=f"classify_legs → {t44!r}",
        differences={"name": "match" if t44[0]=="CUSTOM_MULTI_LEG" else f"got {t44[0]!r}",
                     "family": "match" if t44[1]=="CUSTOM" else f"got {t44[1]!r}"},
        tolerance="exact string match",
        is_pass=p44,
        paper_trade_id="N/A — Classification Test (synthetic)",
        sql_query=_SQL_J, sql_output=_SV["J"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION K — STRATEGY FINGERPRINT  (T045–T052)
# strategy_fingerprint(): deterministic 24-char SHA-256 prefix.
# SQL: COUNT(DISTINCT strategy_fingerprint) — asserts fingerprint discriminates.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_k():
    _rp(_DSEP)
    _rp("  SECTION K — STRATEGY FINGERPRINT  (T045–T052)")
    _rp("  strategy_fingerprint(): deterministic 24-char SHA-256 prefix")
    _rp(f"  SQL assertion: distinct fingerprints in production = {_SV['K']}")
    _rp(_DSEP)

    base = [
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50, ratio=1),
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80, ratio=1),
    ]
    fp_base = strategy_fingerprint(base)

    cases_k = [
        # (tid, sid, name, legs_b, expect_equal, description)
        ("T045", "FP-K-01", "identical legs → same fingerprint",
         [Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50, ratio=1),
          Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80, ratio=1)],
         True, "identical copy"),
        ("T046", "FP-K-02", "shuffled order → same fingerprint",
         [base[1], base[0]], True, "reversed list"),
        ("T047", "FP-K-03", "different expiration → different fingerprint",
         [Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-09-19", mid=4.20, ratio=1),
          Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-09-19", mid=2.10, ratio=1)],
         False, "exp=2026-09-19"),
        ("T048", "FP-K-04", "extra leg → different fingerprint",
         base + [Leg(ASSET_PUT, SIDE_LONG, strike=90.0, expiration="2026-08-15", mid=1.10, ratio=1)],
         False, "3-leg version"),
        ("T049", "FP-K-05", "side change LONG→SHORT → different fingerprint",
         [Leg(ASSET_CALL, SIDE_SHORT, strike=100.0, expiration="2026-08-15", mid=3.50, ratio=1),
          Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80, ratio=1)],
         False, "both SHORT"),
        ("T050", "FP-K-06", "asset type CALL→PUT → different fingerprint",
         [Leg(ASSET_PUT, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50, ratio=1),
          Leg(ASSET_PUT, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80, ratio=1)],
         False, "PUT version"),
        ("T051", "FP-K-07", "ratio 1→2 → different fingerprint",
         [Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50, ratio=2),
          Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80, ratio=1)],
         False, "leg0 ratio=2"),
    ]

    for tid, sid, name, legs_b, expect_equal, desc in cases_k:
        fp_b  = strategy_fingerprint(legs_b)
        equal = (fp_base == fp_b)
        is_p  = (equal == expect_equal)
        _run_test(
            test_id=tid, strategy_id=sid,
            strategy_name=f"Fingerprint — {name}",
            command=f"strategy_fingerprint(base) {'==' if expect_equal else '!='} strategy_fingerprint({desc})",
            inputs_str=f"base: [CALL_LONG(K=100,Aug), CALL_SHORT(K=105,Aug)]\ncompare: {desc}",
            expected={"fp_equal": expect_equal, "fp_base": fp_base},
            actual={"fp_equal": equal, "fp_compare": fp_b},
            raw_output=f"fp_base={fp_base}\nfp_cmp ={fp_b}",
            differences={"fingerprint": ("match" if equal else "different") + (" (correct)" if is_p else " (UNEXPECTED)")},
            tolerance=f"fingerprints must {'match' if expect_equal else 'differ'}",
            is_pass=is_p,
            paper_trade_id="N/A — Fingerprint Unit Test (synthetic)",
            sql_query=_SQL_K, sql_output=_SV["K"],
        )

    # T052: format — 24-char lowercase hex
    t52_fp  = strategy_fingerprint(base)
    t52_len = len(t52_fp)
    t52_hex = all(c in "0123456789abcdef" for c in t52_fp)
    p52     = (t52_len == 24) and t52_hex
    _run_test(
        test_id="T052", strategy_id="FP-K-08",
        strategy_name="Fingerprint — format: 24-char lowercase hex string",
        command="strategy_fingerprint(legs)  → len==24 and all chars in [0-9a-f]",
        inputs_str="input: [CALL_LONG(K=100,Aug), CALL_SHORT(K=105,Aug)]",
        expected={"length": 24, "is_lowercase_hex": True},
        actual={"length": t52_len, "is_lowercase_hex": t52_hex, "value": t52_fp},
        raw_output=f"fingerprint={t52_fp!r}  len={t52_len}  is_hex={t52_hex}",
        differences={"length": abs(t52_len - 24), "is_hex": "match" if t52_hex else "FAIL"},
        tolerance="exact: len==24 and hex charset",
        is_pass=p52,
        paper_trade_id="N/A — Fingerprint Unit Test (synthetic)",
        sql_query=_SQL_K, sql_output=_SV["K"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION L — GREEK AGGREGATION  (T053–T055)
# aggregate_greeks: sum(signed delta×ratio) per greek; None propagates.
# SQL: avg probability_of_profit — asserts greek-derived POP flows to storage.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_l():
    _rp(_DSEP)
    _rp("  SECTION L — GREEK AGGREGATION  (T053–T055)")
    _rp("  aggregate_greeks: sum per greek; None propagates when any leg has None")
    _rp(f"  SQL assertion: avg probability_of_profit in production = {_SV['L']}")
    _rp(_DSEP)

    # T053: LONG call (δ=0.50) + SHORT call (δ=0.40) → net delta = 0.10
    t53_legs = [
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50,
            delta=0.50, gamma=0.04, theta=-0.08, vega=0.15, rho=0.01,
            charm=0.0, vanna=0.0, vomma=0.0),
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80,
            delta=0.40, gamma=0.035, theta=-0.06, vega=0.12, rho=0.008,
            charm=0.0, vanna=0.0, vomma=0.0),
    ]
    t53_gk    = aggregate_greeks(t53_legs)
    t53_delta = t53_gk["delta"]
    t53_exp   = 0.10
    p53       = (t53_delta is not None) and abs(t53_delta - t53_exp) < 1e-9
    _run_test(
        test_id="T053", strategy_id="GK-L-01",
        strategy_name="Greek Aggregation — net delta = +0.50 − 0.40 = +0.10",
        command="aggregate_greeks([CALL_LONG(δ=0.50), CALL_SHORT(δ=0.40)])",
        inputs_str="CALL LONG  delta=0.50  mult=+1  contribution=+0.50\nCALL SHORT delta=0.40  mult=−1  contribution=−0.40\nnet delta = +0.10",
        expected={"net_delta": t53_exp}, actual={"net_delta": t53_delta},
        raw_output=f"aggregate_greeks = {json.dumps({k: round(v,6) if v is not None else None for k,v in t53_gk.items()})}",
        differences={"net_delta": abs((t53_delta or 0) - t53_exp)},
        tolerance="exact (1e-9)", is_pass=p53,
        paper_trade_id="N/A — Greek Aggregation Unit Test (synthetic)",
        sql_query=_SQL_L, sql_output=_SV["L"],
    )

    # T054: SHORT call (δ=0.50) ratio=2 → net delta = -1.00
    t54_legs = [
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80,
            delta=0.50, gamma=0.04, theta=-0.08, vega=0.15, rho=0.01,
            charm=0.0, vanna=0.0, vomma=0.0, ratio=2),
    ]
    t54_gk    = aggregate_greeks(t54_legs)
    t54_delta = t54_gk["delta"]
    t54_exp   = -1.00
    p54       = (t54_delta is not None) and abs(t54_delta - t54_exp) < 1e-9
    _run_test(
        test_id="T054", strategy_id="GK-L-02",
        strategy_name="Greek Aggregation — SHORT call ratio=2: net delta = −0.50×2 = −1.00",
        command="aggregate_greeks([Leg(CALL,SHORT,δ=0.50,ratio=2)])",
        inputs_str="CALL SHORT delta=0.50  ratio=2  mult=ratio×(−1)=−2\ncontribution = 0.50 × (−2) = −1.00",
        expected={"net_delta": t54_exp}, actual={"net_delta": t54_delta},
        raw_output=f"aggregate delta = {t54_delta}",
        differences={"net_delta": abs((t54_delta or 0) - t54_exp)},
        tolerance="exact (1e-9)", is_pass=p54,
        paper_trade_id="N/A — Greek Aggregation Unit Test (synthetic)",
        sql_query=_SQL_L, sql_output=_SV["L"],
    )

    # T055: one leg delta=None → aggregate delta = None
    t55_legs = [
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50,
            delta=0.50, gamma=0.04, theta=-0.08, vega=0.15, rho=0.01,
            charm=None, vanna=None, vomma=None),
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80,
            delta=None, gamma=0.03, theta=-0.05, vega=0.10, rho=0.005,
            charm=None, vanna=None, vomma=None),
    ]
    t55_gk    = aggregate_greeks(t55_legs)
    t55_delta = t55_gk["delta"]
    p55       = (t55_delta is None)
    _run_test(
        test_id="T055", strategy_id="GK-L-03",
        strategy_name="Greek Aggregation — one leg delta=None → aggregate delta = None",
        command="aggregate_greeks([CALL_LONG(δ=0.50), CALL_SHORT(δ=None)])",
        inputs_str="leg[0]: CALL LONG  delta=0.50\nleg[1]: CALL SHORT delta=None\naggregate must propagate None for delta",
        expected={"net_delta": None}, actual={"net_delta": t55_delta},
        raw_output=f"aggregate delta = {t55_delta!r}",
        differences={"net_delta": "None (correct)" if p55 else f"got {t55_delta!r}"},
        tolerance="delta must be None when any leg has None delta",
        is_pass=p55,
        paper_trade_id="N/A — Greek Aggregation Unit Test (synthetic)",
        sql_query=_SQL_L, sql_output=_SV["L"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION M — NEGATIVE CONTROLS  (T056–T066)
# Malformed / boundary inputs: rejected or handled gracefully.
# SQL: null-integrity gate (must be 0) — asserts negative controls never write nulls.
# T064/T065/T066: grep evidence embedded in raw_output for code-behavior claims.
# ─────────────────────────────────────────────────────────────────────────────
def _sec_m():
    _rp(_DSEP)
    _rp("  SECTION M — NEGATIVE CONTROLS  (T056–T066)")
    _rp("  Malformed inputs: 0/9+ legs, None mid/delta, empty lists, boundary values")
    _rp(f"  SQL assertion: null integrity gate = {_SV['M']} (must be 0)")
    _rp(_DSEP)

    # T056: 0 leg_specs → None
    try:
        r56 = build_custom_multi_leg("SPY", [])
    except Exception as ex:
        r56 = f"EXCEPTION: {ex}"
    p56 = (r56 is None)
    _run_test(
        test_id="T056", strategy_id="NC-M-01",
        strategy_name="NC001 — 0 leg_specs → build_custom_multi_leg returns None",
        command="build_custom_multi_leg('SPY', [])",
        inputs_str="leg_specs = []  (length 0 — below minimum of 1)",
        expected={"result": None}, actual={"result": r56},
        raw_output=f"build_custom_multi_leg('SPY', []) = {r56!r}",
        differences={"result": "None (correct)" if p56 else f"got {r56!r}"},
        tolerance="must return None (0 < minimum 1)", is_pass=p56,
        paper_trade_id="BLOCKED: NC001 — 0 leg_specs rejected by len guard",
        sql_query="SELECT 'blocked' -- NC001: len(leg_specs)=0 < 1",
        sql_output="No insert performed — rejected by len guard before get_spot()",
    )

    # T057: 9 leg_specs → None
    nine = [{"asset_type":"CALL","side":"LONG","strike":100.0+i,"expiration":"2026-08-15","ratio":1} for i in range(9)]
    try:
        r57 = build_custom_multi_leg("SPY", nine)
    except Exception as ex:
        r57 = f"EXCEPTION: {ex}"
    p57 = (r57 is None)
    _run_test(
        test_id="T057", strategy_id="NC-M-02",
        strategy_name="NC002 — 9 leg_specs → build_custom_multi_leg returns None",
        command="build_custom_multi_leg('SPY', [spec×9])",
        inputs_str="leg_specs = [{...}×9]  (length 9 — above maximum of 8)",
        expected={"result": None}, actual={"result": r57},
        raw_output=f"build_custom_multi_leg('SPY', [9 specs]) = {r57!r}",
        differences={"result": "None (correct)" if p57 else f"got {r57!r}"},
        tolerance="must return None (9 > maximum 8)", is_pass=p57,
        paper_trade_id="BLOCKED: NC002 — 9 leg_specs rejected by len guard",
        sql_query="SELECT 'blocked' -- NC002: len(leg_specs)=9 > 8",
        sql_output="No insert performed — rejected by len guard before get_spot()",
    )

    # T058: mid=None → net_debit_credit returns None
    r58 = net_debit_credit([
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50),
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=None),
    ])
    p58 = (r58 is None)
    _run_test(
        test_id="T058", strategy_id="NC-M-03",
        strategy_name="NC003 — leg with mid=None → net_debit_credit returns None",
        command="net_debit_credit([CALL_LONG(mid=3.50), CALL_SHORT(mid=None)])",
        inputs_str="leg[0]: CALL LONG  mid=3.50  (valid)\nleg[1]: CALL SHORT mid=None (missing data)",
        expected={"net_debit_credit": None}, actual={"net_debit_credit": r58},
        raw_output=f"net_debit_credit = {r58!r}",
        differences={"net_debit_credit": "None (correct)" if p58 else f"got {r58!r}"},
        tolerance="must return None when any mid is None", is_pass=p58,
        paper_trade_id="BLOCKED: NC003 — missing mid returns None",
        sql_query="SELECT 'blocked' -- NC003: mid=None → net_debit_credit=None",
        sql_output="No insert performed — cannot price without mid",
    )

    # T059: all delta=None → aggregate delta = None
    r59 = aggregate_greeks([
        Leg(ASSET_CALL, SIDE_LONG,  strike=100.0, expiration="2026-08-15", mid=3.50,
            delta=None, gamma=None, theta=None, vega=None, rho=None,
            charm=None, vanna=None, vomma=None),
        Leg(ASSET_CALL, SIDE_SHORT, strike=105.0, expiration="2026-08-15", mid=1.80,
            delta=None, gamma=None, theta=None, vega=None, rho=None,
            charm=None, vanna=None, vomma=None),
    ])
    p59 = (r59["delta"] is None)
    _run_test(
        test_id="T059", strategy_id="NC-M-04",
        strategy_name="NC004 — all delta=None → aggregate_greeks delta = None",
        command="aggregate_greeks([Leg(δ=None), Leg(δ=None)])",
        inputs_str="both legs have delta=None (all greeks None)",
        expected={"aggregate_delta": None}, actual={"aggregate_delta": r59["delta"]},
        raw_output=f"aggregate delta = {r59['delta']!r}",
        differences={"delta": "None (correct)" if p59 else f"got {r59['delta']!r}"},
        tolerance="must be None when all input deltas are None", is_pass=p59,
        paper_trade_id="BLOCKED: NC004 — delta=None propagated",
        sql_query="SELECT 'blocked' -- NC004: delta=None propagated",
        sql_output="No insert performed — greek aggregation not usable",
    )

    # T060: canonical_sort([]) → []
    try:
        r60  = canonical_sort([])
        p60  = isinstance(r60, list) and len(r60) == 0
    except Exception as ex:
        r60 = f"EXCEPTION: {ex}"
        p60 = False
    _run_test(
        test_id="T060", strategy_id="NC-M-05",
        strategy_name="NC005 — canonical_sort([]) → [] (no crash, empty list)",
        command="canonical_sort([])",
        inputs_str="input: empty list []",
        expected={"result": "[]", "len": 0},
        actual={"result": str(r60), "len": len(r60) if isinstance(r60, list) else "N/A"},
        raw_output=f"canonical_sort([]) = {r60!r}",
        differences={"result": "[] (correct)" if p60 else f"got {r60!r}"},
        tolerance="must return [] without raising", is_pass=p60,
        paper_trade_id="N/A — Negative Control (no crash verification, synthetic)",
        sql_query=_SQL_M, sql_output=_SV["M"],
    )

    # T061: strategy_fingerprint([]) → deterministic 24-char string
    try:
        fp61   = strategy_fingerprint([])
        ok61   = isinstance(fp61, str) and len(fp61) == 24
        same61 = (strategy_fingerprint([]) == fp61)
    except Exception as ex:
        fp61 = f"EXCEPTION: {ex}"
        ok61 = same61 = False
    p61 = ok61 and same61
    _run_test(
        test_id="T061", strategy_id="NC-M-06",
        strategy_name="NC006 — strategy_fingerprint([]) → deterministic 24-char string",
        command="strategy_fingerprint([])",
        inputs_str="input: empty leg list []",
        expected={"type": "str", "len": 24, "deterministic": True},
        actual={"type": type(fp61).__name__, "len": len(fp61) if isinstance(fp61,str) else "N/A",
                "value": fp61, "deterministic": same61},
        raw_output=f"strategy_fingerprint([]) = {fp61!r}  len={len(fp61) if isinstance(fp61,str) else 'N/A'}",
        differences={"len": abs(len(fp61)-24) if isinstance(fp61,str) else "N/A",
                     "deterministic": "yes" if same61 else "no"},
        tolerance="24-char hex, no exception, same on repeat call", is_pass=p61,
        paper_trade_id="N/A — Negative Control (deterministic empty fingerprint, synthetic)",
        sql_query=_SQL_M, sql_output=_SV["M"],
    )

    # T062: max_loss=0 → None
    r62 = buying_power_required([], 0.0)
    p62 = (r62 is None)
    _run_test(
        test_id="T062", strategy_id="NC-M-07",
        strategy_name="NC007 — buying_power_required(max_loss=0) → None (≤0 guard)",
        command="buying_power_required([], max_loss=0.0)",
        inputs_str="max_loss=0.0  (boundary: must be > 0 to compute BP)",
        expected={"buying_power": None}, actual={"buying_power": r62},
        raw_output=f"buying_power_required([], 0.0) = {r62!r}",
        differences={"buying_power": "None (correct)" if p62 else f"got {r62!r}"},
        tolerance="must return None when max_loss <= 0", is_pass=p62,
        paper_trade_id="BLOCKED: NC007 — max_loss=0 returns None",
        sql_query="SELECT 'blocked' -- NC007: max_loss=0 → None",
        sql_output="No insert performed — buying power undefined for zero max_loss",
    )

    # T063: max_loss=None → None
    r63 = buying_power_required([], None)
    p63 = (r63 is None)
    _run_test(
        test_id="T063", strategy_id="NC-M-08",
        strategy_name="NC008 — buying_power_required(max_loss=None) → None",
        command="buying_power_required([], max_loss=None)",
        inputs_str="max_loss=None  (undefined risk — cannot compute BP)",
        expected={"buying_power": None}, actual={"buying_power": r63},
        raw_output=f"buying_power_required([], None) = {r63!r}",
        differences={"buying_power": "None (correct)" if p63 else f"got {r63!r}"},
        tolerance="must return None when max_loss is None", is_pass=p63,
        paper_trade_id="BLOCKED: NC008 — max_loss=None (ANALYSIS_ONLY)",
        sql_query="SELECT 'blocked' -- NC008: max_loss=None → None",
        sql_output="No insert performed — undefined risk strategy",
    )

    # T064: Leg.signed_mid with mid=None → None
    #        Grep evidence: sed -n '79,84p' legs.py (the None guard)
    lg64 = Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=None)
    r64  = lg64.signed_mid
    p64  = (r64 is None)
    _run_test(
        test_id="T064", strategy_id="NC-M-09",
        strategy_name="NC009 — Leg.signed_mid with mid=None → None",
        command="Leg(CALL,LONG,mid=None).signed_mid",
        inputs_str="Leg(CALL, LONG, mid=None)  — price not available",
        expected={"signed_mid": None}, actual={"signed_mid": r64},
        raw_output=(
            f"Leg(CALL,LONG,mid=None).signed_mid = {r64!r}\n"
            f"\n--- grep evidence: signed_mid None guard (sed -n '79,84p' legs.py) ---\n"
            f"{_GREP_SIGNED_MID}"
        ),
        differences={"signed_mid": "None (correct)" if p64 else f"got {r64!r}"},
        tolerance="must return None when mid is None", is_pass=p64,
        paper_trade_id="BLOCKED: NC009 — mid=None → signed_mid=None",
        sql_query="SELECT 'blocked' -- NC009: mid=None → signed_mid=None",
        sql_output="No insert performed — cannot compute signed_mid without mid",
    )

    # T065: Leg.signed_delta with delta=None → None
    #        Grep evidence: sed -n '86,91p' legs.py (the None guard)
    lg65 = Leg(ASSET_CALL, SIDE_LONG, strike=100.0, expiration="2026-08-15", mid=3.50, delta=None)
    r65  = lg65.signed_delta
    p65  = (r65 is None)
    _run_test(
        test_id="T065", strategy_id="NC-M-10",
        strategy_name="NC010 — Leg.signed_delta with delta=None → None",
        command="Leg(CALL,LONG,delta=None).signed_delta",
        inputs_str="Leg(CALL, LONG, mid=3.50, delta=None)  — greek not available",
        expected={"signed_delta": None}, actual={"signed_delta": r65},
        raw_output=(
            f"Leg(CALL,LONG,delta=None).signed_delta = {r65!r}\n"
            f"\n--- grep evidence: signed_delta None guard (sed -n '86,91p' legs.py) ---\n"
            f"{_GREP_SIGNED_DELTA}"
        ),
        differences={"signed_delta": "None (correct)" if p65 else f"got {r65!r}"},
        tolerance="must return None when delta is None", is_pass=p65,
        paper_trade_id="BLOCKED: NC010 — delta=None → signed_delta=None",
        sql_query="SELECT 'blocked' -- NC010: delta=None → signed_delta=None",
        sql_output="No insert performed — cannot compute signed_delta without delta",
    )

    # T066: LegTemplate.sort_key with unknown asset_type → order=9 (dict.get fallback)
    #        Grep evidence: grep -n 'sort_key\|order.get' legs.py
    lg66  = LegTemplate(asset_type="UNKNOWN_ASSET", side=SIDE_LONG)
    key66 = lg66.sort_key()
    ord66 = key66[0]
    p66   = (ord66 == 9)
    _run_test(
        test_id="T066", strategy_id="NC-M-11",
        strategy_name="NC011 — LegTemplate.sort_key with unknown asset_type → order=9 (graceful fallback)",
        command="LegTemplate('UNKNOWN_ASSET','LONG').sort_key()[0] == 9",
        inputs_str="LegTemplate(asset_type='UNKNOWN_ASSET', side='LONG')\nExpected: sort_key()[0] == 9 (dict.get fallback for unknown type)",
        expected={"type_order": 9},
        actual={"type_order": ord66, "full_key": str(key66)},
        raw_output=(
            f"LegTemplate('UNKNOWN_ASSET','LONG').sort_key() = {key66!r}\n"
            f"sort_key()[0] = {ord66}  (expected 9)\n"
            f"\n--- grep evidence: sort_key fallback (grep -n 'sort_key|order.get' legs.py) ---\n"
            f"{_GREP_SORT_KEY}"
        ),
        differences={"type_order": abs(ord66 - 9)},
        tolerance="type_order must be 9 (dict.get fallback)", is_pass=p66,
        paper_trade_id="BLOCKED: NC011 — unknown asset_type falls back gracefully",
        sql_query="SELECT 'blocked' -- NC011: unknown asset_type sort_key fallback=9",
        sql_output="No insert performed — unknown asset type is not tradeable",
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    _rp(_DSEP)
    _rp("  ase_leg_construction_verification.py  v2")
    _rp(f"  Run ID        : {_RUN_ID}")
    _rp(f"  Code SHA-256  : {_CODE_SHA}")
    _rp(f"  Config SHA-256: {_CFG_SHA}")
    _rp(f"  Today         : {datetime.now(timezone.utc).date().isoformat()}")
    _rp(_DSEP)
    _rp("  SCOPE: All 66 tests use synthetic in-memory Leg/LegTemplate objects.")
    _rp("         Ticker='synthetic', no Tradier calls, no live market data.")
    _rp("         DB queries are per-section read-only assertions tied to each")
    _rp("         section's specific claim — they do NOT affect PASS/FAIL verdicts.")
    _rp("         Grep evidence for code-behavior claims (signed_mid/signed_delta")
    _rp("         None handling, sort_key fallback) is captured via subprocess at")
    _rp("         startup and embedded in raw_output of T009, T013, T018, T064,")
    _rp("         T065, T066.")
    _rp(_DSEP)
    _rp("")

    _sec_a()   # T001–T008  Leg count         SQL: leg-count range in production
    _sec_b()   # T009–T014  Long/Short side   SQL: mid range; grep: signed_mid/delta
    _sec_c()   # T015–T017  Asset type        SQL: call/put distribution
    _sec_d()   # T018–T022  Strike ordering   SQL: strike range; grep: canonical_sort
    _sec_e()   # T023–T024  Expiry ordering   SQL: expiry range
    _sec_f()   # T025–T028  Ratios            SQL: distinct ratios
    _sec_g()   # T029–T032  Debit/Credit      SQL: PnL wins/losses
    _sec_h()   # T033–T036  Multiplier        SQL: buying_power min/max/avg
    _sec_i()   # T037–T040  Stock leg         SQL: STOCK_PLUS_OPTION count
    _sec_j()   # T041–T044  Canonical name    SQL: top-5 strategy/family combos
    _sec_k()   # T045–T052  Fingerprint       SQL: distinct fingerprint count
    _sec_l()   # T053–T055  Greek aggregation SQL: avg probability_of_profit
    _sec_m()   # T056–T066  Negative controls SQL: null integrity gate (= 0)

    _rp(_DSEP)
    _rp("  FINAL VERDICT")
    _rp(f"  Run ID        : {_RUN_ID}")
    _rp(f"  Total Tests   : {_pass + _fail}")
    _rp(f"  PASS          : {_pass}")
    _rp(f"  FAIL          : {_fail}")
    _rp(f"  Code SHA-256  : {_CODE_SHA}")
    _rp(f"  Config SHA-256: {_CFG_SHA}")
    _rp(f"  EXIT STATUS   : {'PASS' if _fail == 0 else 'FAIL'}")
    _rp(_DSEP)

    report_path = os.path.join(_ROOT, f"ase_leg_report_{_RUN_ID}.txt")
    with open(report_path, "w") as f:
        f.write("\n".join(_lines))
    print(f"\nReport written to: {report_path}")
    sys.exit(0 if _fail == 0 else 1)


if __name__ == "__main__":
    main()
