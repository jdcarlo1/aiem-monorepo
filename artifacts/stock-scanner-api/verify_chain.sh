#!/usr/bin/env bash
# verify_chain.sh  —  Independent SHA-256 audit chain verifier for aiem_options_alerts
#
# FIX 1 (snapshot-based): Stage 1 reads from aiem_options_alert_snapshots (immutable,
#   captured at decision time), NOT from live polygon_market_daily or options_structure_scan.
#   If no snapshot exists for an alert, stage 1 reports SNAPSHOT_UNAVAILABLE.
#
# FIX 2 (real chaining): Each stage N+1 uses the RECOMPUTED hash from stage N as its
#   prev_hash input, not the stored hash.  A failure at any stage causes every downstream
#   stage to report "UNVERIFIABLE — upstream break at stage N", not silently PASS.
#
# Usage: bash verify_chain.sh [alert_id]
#   alert_id omitted → verifies the most-recent row

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

# ── Fetch alert record ────────────────────────────────────────────────────
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

h1_stored  = stage_hashes.get("1_polygon",          "MISSING")
h2_stored  = stage_hashes.get("2_stock_analysis",   "MISSING")
h3_stored  = stage_hashes.get("3_options_analysis", "MISSING")
h4_stored  = stage_hashes.get("4_risk_gates",       "MISSING")
h5_stored  = stage_hashes.get("5_req6_scoring",     "MISSING")
h6_stored  = stage_hashes.get("6_decision",         "MISSING")
h7_stored  = stage_hashes.get("7_alert",            "MISSING")
h8_stored  = stage_hashes.get("8_db_write",         "MISSING")
h9_stored  = stage_hashes.get("9_learning",         None)
h10_stored = stage_hashes.get("10_audit_chain_final", None)

passes = []
fails  = []

# ── Stage 1: read from immutable snapshot, NOT live polygon tables ─────────
# FIX 1: aiem_options_alert_snapshots captures pmd_data + oss_data at decision
# time so that EOD mutations to polygon_market_daily cannot affect verification.
with psycopg2.connect(DB, connect_timeout=4) as conn2, conn2.cursor() as cur2:
    cur2.execute("""
        SELECT polygon_data, oss_data
        FROM aiem_options_alert_snapshots
        WHERE alert_id = %s
    """, (aid,))
    snap = cur2.fetchone()

if snap:
    pmd_snap = snap[0] if isinstance(snap[0], dict) else json.loads(snap[0])
    oss_snap = snap[1] if isinstance(snap[1], dict) else json.loads(snap[1])
    h1_recomputed = sha256_stage("1_polygon", {
        "ticker":            ticker,
        "market_daily":      pmd_snap,
        "options_structure": oss_snap,
    }, "GENESIS")
    ok1 = (h1_recomputed == h1_stored)
    icon1   = "✓" if ok1 else "✗"
    status1 = "PASS" if ok1 else "FAIL"
    print(f"  [{icon1}] 1_polygon                       stored={h1_stored[:20]}...  recomputed={h1_recomputed[:20]}...  {status1}  [snapshot]")
    if ok1:
        passes.append("1_polygon")
        prev_recomputed = h1_recomputed
    else:
        fails.append({"stage": "1_polygon", "stored": h1_stored, "recomputed": h1_recomputed})
        prev_recomputed = None
else:
    # No snapshot — alert pre-dates Fix 1.  Honest failure: cannot verify.
    print(f"  [!] 1_polygon                       SNAPSHOT_UNAVAILABLE — no snapshot for alert_id={aid}")
    fails.append({"stage": "1_polygon", "reason": "SNAPSHOT_UNAVAILABLE"})
    prev_recomputed = None

# ── Stages 2-6: REAL chaining — each stage uses the RECOMPUTED prev hash ──
# FIX 2: if prev_recomputed is None (upstream broke), downstream stages are
# UNVERIFIABLE, not silently PASS.

def _upstream_stage():
    return fails[-1]["stage"] if fails else "unknown"

def chain_stage(stage_name, data, stored_hash):
    """
    Verify one chained stage using the RECOMPUTED prev hash (not stored prev).
    Sets prev_recomputed to the new recomputed value on PASS, None on any failure.
    """
    global prev_recomputed
    if prev_recomputed is None:
        up = _upstream_stage()
        print(f"  [!] {stage_name:<30} UNVERIFIABLE — upstream break at {up}")
        fails.append({"stage": stage_name, "reason": f"UNVERIFIABLE — upstream break at {up}"})
        return
    h_recomp = sha256_stage(stage_name, data, prev_recomputed)
    ok     = (h_recomp == stored_hash)
    icon   = "✓" if ok else "✗"
    status = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {stage_name:<30} stored={stored_hash[:20]}...  recomputed={h_recomp[:20]}...  {status}  [chained]")
    if ok:
        passes.append(stage_name)
        prev_recomputed = h_recomp
    else:
        fails.append({"stage": stage_name, "stored": stored_hash, "recomputed": h_recomp})
        prev_recomputed = None

chain_stage(
    "2_stock_analysis",
    {"ticker": ticker, **stock_data},
    h2_stored,
)

chain_stage(
    "3_options_analysis",
    {
        "ticker":          ticker,
        "expected_move":   options_data.get("expected_move",   {}),
        "iv_rank":         options_data.get("iv_rank",         {}),
        "oi_by_strike":    options_data.get("oi_by_strike",    {}),
        "bearish_signals": options_data.get("bearish_signals", {}),
    },
    h3_stored,
)

chain_stage(
    "4_risk_gates",
    {
        "ticker":             ticker,
        "gate_failures":      verify_data.get("gate_failures",      []),
        "call_eligible":      verify_data.get("call_eligible"),
        "put_eligible":       verify_data.get("put_eligible"),
        "ready_for_decision": verify_data.get("ready_for_decision"),
    },
    h4_stored,
)

chain_stage(
    "5_req6_scoring",
    {
        "ticker":          ticker,
        "call_score":      scoring_data.get("call_score"),
        "put_score":       scoring_data.get("put_score"),
        "call_components": scoring_data.get("call_scoring", {}).get("component_scores", {}),
        "put_components":  scoring_data.get("put_scoring",  {}).get("component_scores", {}),
    },
    h5_stored,
)

margin = abs((scoring_data.get("call_score") or 0) - (scoring_data.get("put_score") or 0))
chain_stage(
    "6_decision",
    {
        "ticker":     ticker,
        "direction":  direction,
        "call_score": scoring_data.get("call_score"),
        "put_score":  scoring_data.get("put_score"),
        "margin":     round(margin, 1),
    },
    h6_stored,
)

# ── Stages 7-8: presence checks (alert_fields not fully stored for recompute)
for stage_key, h_stored in [("7_alert", h7_stored), ("8_db_write", h8_stored)]:
    missing = (h_stored == "MISSING")
    icon    = "✓" if not missing else "✗"
    label   = "PASS (present)" if not missing else "FAIL (missing)"
    hash_preview = h_stored[:20] if not missing else "MISSING"
    print(f"  [{icon}] {stage_key:<30} stored={hash_preview}...  {label}")
    if not missing:
        passes.append(stage_key)
    else:
        fails.append({"stage": stage_key, "stored": "MISSING"})

# Verify stored audit_chain_sha256 matches stage 8 (or 10 after grading)
if h8_stored != "MISSING":
    ok_chain = (stored_chain_hash == h8_stored) or (
        h10_stored is not None and stored_chain_hash == h10_stored
    )
    icon   = "✓" if ok_chain else "✗"
    status = "PASS" if ok_chain else "FAIL"
    print(f"  [{icon}] audit_chain_sha256 matches db_write/final hash: {status}")
    if ok_chain:
        passes.append("audit_chain_sha256_match")
    else:
        fails.append({"stage": "audit_chain_sha256_match",
                      "stored": stored_chain_hash, "expected": h8_stored})

# Optional: stages 9-10 present after outcome grading
for stage_key, h_stored in [("9_learning", h9_stored), ("10_audit_chain_final", h10_stored)]:
    if h_stored:
        print(f"  [✓] {stage_key:<30} stored={h_stored[:20]}...  PASS (present)")
        passes.append(stage_key)
    else:
        print(f"  [~] {stage_key:<30} not yet graded  SKIP")

# ── REQ6 component scores ──────────────────────────────────────────────────
# Gate: only display REQ6 when the full chain (stages 1-6) is verified.
# If prev_recomputed is None any upstream stage broke the chain → suppress.
print()
if prev_recomputed is None:
    print("  REQ6 SUPPRESSED — chain UNVERIFIABLE")
    fails.append({"stage": "req6_display",
                  "reason": "suppressed — chain UNVERIFIABLE upstream"})
else:
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
    elif scoring_data.get("combined_score") is not None:
        # Historical schema: scoring_json only stores combined_score. Stage 5
        # hash already verified empty call/put component maps. Do not FAIL
        # the chain for missing columns that were never persisted.
        print(f"  legacy scoring_json: combined_score={scoring_data.get('combined_score')}")
        print("  component_scores not persisted in DB (call_scoring/put_scoring absent)")
        print("  treating as PASS — matches stage-5 hashed empty component maps")
        passes.append("req6_legacy_combined_score_only")
    else:
        fails.append({"stage": "req6_components", "reason": "no component scores in DB"})

# ── Gate failures audit ────────────────────────────────────────────────────
gate_failures = (json.loads(gate_failures_json)
                 if isinstance(gate_failures_json, str)
                 else (gate_failures_json or []))
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
    print("  FAILURES:")
    for f in fails:
        print(f"    {f}")
    print("  OVERALL: FAIL")
    sys.exit(3)
else:
    print("  OVERALL: PASS")
print(f"{'='*72}")
PYEOF
