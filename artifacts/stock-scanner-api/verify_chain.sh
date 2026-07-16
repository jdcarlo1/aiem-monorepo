#!/usr/bin/env bash
# verify_chain.sh  —  Independent SHA-256 audit chain verifier for aiem_options_alerts
# Usage: bash verify_chain.sh [alert_id]
# If alert_id is omitted, verifies the most recent row.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")" 

python3 - "$@" << 'PYEOF'
import sys, os, json, hashlib, psycopg2

DB = os.environ["DATABASE_URL"]
alert_id_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None

def sha256_stage(stage_name, data, prev_hash):
    payload = {"stage": stage_name, "prev_hash": prev_hash, "data": data}
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

with psycopg2.connect(DB, connect_timeout=4) as conn, conn.cursor() as cur:
    if alert_id_arg:
        cur.execute("""
            SELECT id, ticker, direction, alert_date, expiry, outcome_status,
                   selected_score, opposite_score, pnl_pct,
                   stage_hashes, audit_chain_sha256, created_at,
                   stock_analysis_json, scoring_json, gate_failures,
                   options_analysis_json, verify_result_json
            FROM aiem_options_alerts WHERE id = %s
        """, (alert_id_arg,))
    else:
        cur.execute("""
            SELECT id, ticker, direction, alert_date, expiry, outcome_status,
                   selected_score, opposite_score, pnl_pct,
                   stage_hashes, audit_chain_sha256, created_at,
                   stock_analysis_json, scoring_json, gate_failures,
                   options_analysis_json, verify_result_json
            FROM aiem_options_alerts
            ORDER BY id DESC LIMIT 1
        """)
    row = cur.fetchone()

if not row:
    print("FAIL: no rows found in aiem_options_alerts")
    sys.exit(1)

(aid, ticker, direction, alert_date, expiry, outcome_status,
 selected_score, opposite_score, pnl_pct,
 sh_raw, stored_chain_hash, created_at,
 stock_json, scoring_json, gate_failures_json,
 options_json, verify_json) = row

stage_hashes = json.loads(sh_raw) if isinstance(sh_raw, str) else (sh_raw or {})
stock_data   = json.loads(stock_json)   if isinstance(stock_json, str)   else (stock_json or {})
scoring_data = json.loads(scoring_json) if isinstance(scoring_json, str) else (scoring_json or {})
options_data = json.loads(options_json) if isinstance(options_json, str) else (options_json or {})
verify_data  = json.loads(verify_json)  if isinstance(verify_json, str)  else (verify_json or {})

print(f"{'='*72}")
print(f"  verify_chain.sh  —  alert_id={aid}  ticker={ticker}  direction={direction}")
print(f"  alert_date={alert_date}  expiry={expiry}  outcome={outcome_status}")
print(f"  stored audit_chain_sha256: {stored_chain_hash}")
print(f"{'='*72}")
print()

# ── Re-fetch live Polygon anchor (Stage 1) ────────────────────────────────
with psycopg2.connect(DB, connect_timeout=4) as conn2, conn2.cursor() as cur2:
    cur2.execute("""
        SELECT scan_date, close_price, vwap, rvol, close_strength, range_pct
        FROM polygon_market_daily
        WHERE ticker = %s AND scan_date >= %s::date - INTERVAL '3 days'
        ORDER BY scan_date DESC LIMIT 1
    """, (ticker, str(alert_date)))
    pmd = cur2.fetchone()
    pmd_data = dict(zip(
        ["scan_date","close","vwap","rvol","close_strength","range_pct"],
        [str(v) if hasattr(v,"year") else
         float(v) if v is not None and hasattr(v,"__float__") else v
         for v in (pmd or [None]*6)]
    )) if pmd else {}

    cur2.execute("""
        SELECT scan_date, spot, front_iv, gex_regime, pc_skew_pp, pc_skew_tag,
               term_tag, gex_m
        FROM options_structure_scan
        WHERE ticker = %s AND scan_date >= %s::date - INTERVAL '3 days'
        ORDER BY scan_date DESC LIMIT 1
    """, (ticker, str(alert_date)))
    oss = cur2.fetchone()
    oss_data = dict(zip(
        ["scan_date","spot","front_iv","gex_regime","pc_skew_pp","pc_skew_tag",
         "term_tag","gex_m"],
        [str(v) if hasattr(v,"year") else
         float(v) if v is not None and hasattr(v,"__float__") else v
         for v in (oss or [None]*8)]
    )) if oss else {}

# ── Re-compute each stage hash independently ─────────────────────────────
passes = []
fails  = []

def check_stage(stage_key, expected_hash, stage_name, data, prev_hash):
    recomputed = sha256_stage(stage_name, data, prev_hash)
    ok = (recomputed == expected_hash)
    status = "PASS" if ok else "FAIL"
    icon   = "✓" if ok else "✗"
    print(f"  [{icon}] {stage_name:<30} stored={expected_hash[:20]}...  recomputed={recomputed[:20]}...  {status}")
    if ok:
        passes.append(stage_name)
    else:
        fails.append({"stage": stage_name, "stored": expected_hash, "recomputed": recomputed})
    return recomputed  # return recomputed hash for chain

h1_stored = stage_hashes.get("1_polygon", "MISSING")
h2_stored = stage_hashes.get("2_stock_analysis", "MISSING")
h3_stored = stage_hashes.get("3_options_analysis", "MISSING")
h4_stored = stage_hashes.get("4_risk_gates", "MISSING")
h5_stored = stage_hashes.get("5_req6_scoring", "MISSING")
h6_stored = stage_hashes.get("6_decision", "MISSING")
h7_stored = stage_hashes.get("7_alert", "MISSING")
h8_stored = stage_hashes.get("8_db_write", "MISSING")
h9_stored = stage_hashes.get("9_learning", None)
h10_stored = stage_hashes.get("10_audit_chain_final", None)

# Stage 1: re-compute from live DB data
h1_recomputed = sha256_stage("1_polygon", {
    "ticker": ticker, "market_daily": pmd_data, "options_structure": oss_data
}, "GENESIS")
ok1 = (h1_recomputed == h1_stored)
print(f"  [{'✓' if ok1 else '✗'}] 1_polygon                       stored={h1_stored[:20]}...  recomputed={h1_recomputed[:20]}...  {'PASS' if ok1 else 'FAIL'}")
if ok1: passes.append("1_polygon")
else:   fails.append({"stage": "1_polygon", "stored": h1_stored, "recomputed": h1_recomputed})

# Stage 2: stock_data from DB
h2_recomputed = sha256_stage("2_stock_analysis", {"ticker": ticker, **stock_data}, h1_stored)
ok2 = (h2_recomputed == h2_stored)
print(f"  [{'✓' if ok2 else '✗'}] 2_stock_analysis                stored={h2_stored[:20]}...  recomputed={h2_recomputed[:20]}...  {'PASS' if ok2 else 'FAIL'}")
if ok2: passes.append("2_stock_analysis")
else:   fails.append({"stage": "2_stock_analysis", "stored": h2_stored, "recomputed": h2_recomputed})

# Stage 3: options_analysis from DB
h3_recomputed = sha256_stage("3_options_analysis", {"ticker": ticker, **options_data}, h2_stored)
ok3 = (h3_recomputed == h3_stored)
print(f"  [{'✓' if ok3 else '✗'}] 3_options_analysis              stored={h3_stored[:20]}...  recomputed={h3_recomputed[:20]}...  {'PASS' if ok3 else 'FAIL'}")
if ok3: passes.append("3_options_analysis")
else:   fails.append({"stage": "3_options_analysis", "stored": h3_stored, "recomputed": h3_recomputed})

# Stage 4: verify_result from DB
h4_recomputed = sha256_stage("4_risk_gates", {
    "ticker": ticker,
    "gate_failures":      verify_data.get("gate_failures", []),
    "call_eligible":      verify_data.get("call_eligible"),
    "put_eligible":       verify_data.get("put_eligible"),
    "ready_for_decision": verify_data.get("ready_for_decision"),
}, h3_stored)
ok4 = (h4_recomputed == h4_stored)
print(f"  [{'✓' if ok4 else '✗'}] 4_risk_gates                    stored={h4_stored[:20]}...  recomputed={h4_recomputed[:20]}...  {'PASS' if ok4 else 'FAIL'}")
if ok4: passes.append("4_risk_gates")
else:   fails.append({"stage": "4_risk_gates", "stored": h4_stored, "recomputed": h4_recomputed})

# Stage 5: scoring from DB
h5_recomputed = sha256_stage("5_req6_scoring", {
    "ticker":             ticker,
    "call_score":         scoring_data.get("call_score"),
    "put_score":          scoring_data.get("put_score"),
    "call_components":    scoring_data.get("call_scoring", {}).get("component_scores", {}),
    "put_components":     scoring_data.get("put_scoring",  {}).get("component_scores", {}),
}, h4_stored)
ok5 = (h5_recomputed == h5_stored)
print(f"  [{'✓' if ok5 else '✗'}] 5_req6_scoring                  stored={h5_stored[:20]}...  recomputed={h5_recomputed[:20]}...  {'PASS' if ok5 else 'FAIL'}")
if ok5: passes.append("5_req6_scoring")
else:   fails.append({"stage": "5_req6_scoring", "stored": h5_stored, "recomputed": h5_recomputed})

# Stage 6: decision
margin = abs((scoring_data.get("call_score") or 0) - (scoring_data.get("put_score") or 0))
h6_recomputed = sha256_stage("6_decision", {
    "ticker":     ticker,
    "direction":  direction,
    "call_score": scoring_data.get("call_score"),
    "put_score":  scoring_data.get("put_score"),
    "margin":     round(margin, 1),
}, h5_stored)
ok6 = (h6_recomputed == h6_stored)
print(f"  [{'✓' if ok6 else '✗'}] 6_decision                      stored={h6_stored[:20]}...  recomputed={h6_recomputed[:20]}...  {'PASS' if ok6 else 'FAIL'}")
if ok6: passes.append("6_decision")
else:   fails.append({"stage": "6_decision", "stored": h6_stored, "recomputed": h6_recomputed})

# Stages 7-10: check stored hashes exist and match the stored audit_chain_sha256
for stage_key, h_stored in [
    ("7_alert", h7_stored),
    ("8_db_write", h8_stored),
]:
    missing = (h_stored == "MISSING")
    print(f"  [{'✓' if not missing else '✗'}] {stage_key:<30} stored={h_stored[:20] if not missing else 'MISSING'}...  {'PASS (present)' if not missing else 'FAIL (missing)'}")
    if not missing: passes.append(stage_key)
    else:           fails.append({"stage": stage_key, "stored": "MISSING"})

# Verify stored audit_chain_sha256 matches stage 8
if h8_stored != "MISSING":
    ok_chain = (stored_chain_hash == h8_stored) or (h10_stored and stored_chain_hash == h10_stored)
    print(f"  [{'✓' if ok_chain else '✗'}] audit_chain_sha256 matches db_write/final hash: {'PASS' if ok_chain else 'FAIL'}")
    if ok_chain: passes.append("audit_chain_sha256_match")
    else:        fails.append({"stage": "audit_chain_sha256_match", "stored": stored_chain_hash, "expected": h8_stored})

# Optional: stages 9-10 (present after grading)
for stage_key, h_stored in [("9_learning", h9_stored), ("10_audit_chain_final", h10_stored)]:
    if h_stored:
        print(f"  [✓] {stage_key:<30} stored={h_stored[:20]}...  PASS (present)")
        passes.append(stage_key)
    else:
        print(f"  [~] {stage_key:<30} not yet graded  SKIP")

# ── REQ6 component scores verification ────────────────────────────────────
print()
print("  REQ6 COMPONENT SCORES:")
call_comp = scoring_data.get("call_scoring", {}).get("component_scores", {})
put_comp  = scoring_data.get("put_scoring",  {}).get("component_scores", {})
if call_comp and put_comp:
    print(f"  {'Dimension':<35} {'CALL':>6} {'PUT':>6}")
    print(f"  {'-'*50}")
    for dim in sorted(call_comp):
        print(f"  {dim:<35} {call_comp[dim]:>6.0f} {put_comp[dim]:>6.0f}")
    print(f"  {'FINAL':<35} {scoring_data.get('call_score','?'):>6} {scoring_data.get('put_score','?'):>6}")
    print(f"  margin={scoring_data.get('margin','?')}  winner={scoring_data.get('winner','?')}")
    passes.append("req6_12_components_computed")
else:
    fails.append({"stage": "req6_components", "reason": "no component scores in DB"})

# ── Gate failures audit ────────────────────────────────────────────────────
gate_failures = json.loads(gate_failures_json) if isinstance(gate_failures_json, str) else (gate_failures_json or [])
print()
print(f"  GATE FAILURES ({len(gate_failures)}):")
for gf in gate_failures:
    print(f"    {gf}")
if not gate_failures:
    print("    none — both directions passed all gates")

# ── Final verdict ──────────────────────────────────────────────────────────
print()
print(f"{'='*72}")
total = len(passes) + len(fails)
print(f"  RESULT: {len(passes)}/{total} checks passed")
if fails:
    print(f"  FAILURES:")
    for f in fails:
        print(f"    {f}")
    print(f"  OVERALL: FAIL")
    sys.exit(3)
else:
    print(f"  OVERALL: PASS")
print(f"{'='*72}")
PYEOF
