#!/usr/bin/env python3
"""
Diagram 2 Mutation Acceptance Test
===================================
Runs the real D2 stage pipeline (stages 1-17) for NVDA using production DB
data and the real AEIMMasterOrchestrator. Does NOT insert a paper trade.

Phase 1 (MUTATION) — called with --phase=fail:
  MUTATION_KILL_TECHNICAL=True in helpers → stage 10 records FAIL, others PASS.

Phase 2 (RESTORE) — called with --phase=pass:
  MUTATION_KILL_TECHNICAL=False in helpers → all stages PASS.

Results land in aiem_diagram2_trace_audit under a clearly-labelled trace_id.
"""
import os, sys, uuid, argparse

sys.path.insert(0, os.path.dirname(__file__))

import aiem_master_orchestrator as _amo
import aiem_registry as _d2_areg
import aiem_communication_bus as _d2_abus
import aiem_diagram2_stage_helpers as _d2_help
import aiem_diagram2_trace_audit as _d2_audit

parser = argparse.ArgumentParser()
parser.add_argument("--phase", choices=["fail", "pass"], required=True)
args = parser.parse_args()

# ── Verify mutation flag matches requested phase ───────────────────────────
flag_state = _d2_help.MUTATION_KILL_TECHNICAL
if args.phase == "fail":
    assert flag_state is True, f"ABORT: MUTATION_KILL_TECHNICAL must be True for fail-phase, got {flag_state}"
    trace_id = f"D2_MUTATION_FAIL_{str(uuid.uuid4())[:8].upper()}"
    print(f"\n{'='*60}")
    print(f"  PHASE 1 — MUTATION (KILL technical_signal stage)")
    print(f"  MUTATION_KILL_TECHNICAL = {flag_state}")
    print(f"  trace_id: {trace_id}")
    print(f"{'='*60}\n")
else:
    assert flag_state is False, f"ABORT: MUTATION_KILL_TECHNICAL must be False for pass-phase, got {flag_state}"
    trace_id = f"D2_MUTATION_PASS_{str(uuid.uuid4())[:8].upper()}"
    print(f"\n{'='*60}")
    print(f"  PHASE 2 — RESTORE (all stages live)")
    print(f"  MUTATION_KILL_TECHNICAL = {flag_state}")
    print(f"  trace_id: {trace_id}")
    print(f"{'='*60}\n")

# ── Pipeline inputs — reconstructed from NVDA trade 174 (production record) ──
_t         = "NVDA"
pick       = {
    "source": "unusual_calls",
    "detail": "$23,489,310 prem VOI=73.45 [EVENT:cpi=2026-07-14]",
}
_raw_sc        = 17252.90
_fill_price    = 204.2361
_notional      = 1000.0
_trade_type    = "CALL_OPTION"
_tw_lbl        = 0.2000
_dm_lbl        = 0.7000
_fin_sc        = 2554.03
_sizing_gate   = "PASS"
_sizing_stop   = None
_sizing_risk_pct = None
_macro_snap    = None   # Stage 8 uses fail-safe dict path when None
_debate_verdicts = {
    "NVDA": {
        "verdict": "NEUTRAL",
        "debate": {
            "ticker": "NVDA",
            "bull_case": {"thesis": "RSI 50 — room to run", "score": 0.05,
                          "method": "deterministic_rules"},
            "bear_case": {"thesis": "No significant bear case.", "score": 0.00,
                          "method": "deterministic_rules"},
            "synthesis": {"verdict": "NEUTRAL", "confidence": 0.4, "net_edge": 0.05},
        }
    }
}

# ── Orchestrator singleton ─────────────────────────────────────────────────
_orch = _amo.get_orchestrator()

# ── Stage runner — identical to the wiring in main.py ─────────────────────
results = {}

def _d2_run(stage_order, stage_name, display, runtime_fn_name, fn, *fargs):
    try:
        r = _orch.execute_stage(
            trace_id, _t, stage_order, stage_name, display,
            runtime_fn_name, fn, *fargs, paper_trade_id=None,
        )
        results[stage_order] = ("PASS", None)
        print(f"  Stage {stage_order:02d}  {stage_name:<25}  PASS")
        return r
    except Exception as e:
        results[stage_order] = ("FAIL", str(e))
        print(f"  Stage {stage_order:02d}  {stage_name:<25}  FAIL  →  {e}")
        return None

# ── Run stages 1-17 (no paper trade write in mutation test) ───────────────
print("Running stages...\n")

_d2_run(1,  "scanner_signals",    "Scanner Signals",
        "_aiem_paper_pick_candidates",
        lambda: {"source": pick["source"], "raw_score": _raw_sc,
                 "detail": str(pick.get("detail",""))[:200]})

_d2_run(2,  "aeim_intake",        "AEIM Intake",
        "_aiem_paper_execute_today",
        lambda: {"ticker": _t, "trade_type": _trade_type,
                 "fill_price": _fill_price, "notional": _notional})

_d2_run(3,  "data_guards",        "Data Guards",
        "_aiem_paper_execute_today (kill_switch/daily_loss/portfolio_corr gates)",
        lambda: {"kill_switch": "CLEAR", "daily_loss_limit": "CLEAR",
                 "portfolio_correlation": "CLEAR",
                 "checked_at": "batch_level_before_candidate_loop"})

_d2_run(4,  "master_orchestrator","Master Orchestrator",
        "AEIMMasterOrchestrator.execute_stage",
        lambda: {"orchestrator_singleton_id": id(_orch), "active": True})

_d2_run(5,  "module_registry",    "Module Registry",
        "aiem_registry.get_module_for_stage",
        lambda: {"stages_resolved": sum(
                     1 for i in range(1, 22)
                     if _d2_areg.get_module_for_stage(i).get("found")),
                 "total_stages": 21})

_d2_run(6,  "tool_registry",      "Tool Registry",
        "aiem_registry.get_tool",
        lambda: _d2_areg.get_tool("run_bull_bear_debate"))

_d2_run(7,  "communication_bus",  "Communication Bus",
        "aiem_communication_bus.CommunicationBus.recent_events",
        lambda: {"events_so_far": len(_d2_abus.get_bus().recent_events(trace_id))})

_d2_run(8,  "macro_regime",       "Macro / Regime",
        "aiem_macro_engine (get_macro_gate — reused batch-level snapshot)",
        lambda: ({"regime": _macro_snap.regime, "macro_score": _macro_snap.macro_score}
                 if _macro_snap else {"macro_snap": None,
                 "note": "macro engine errored upstream, batch proceeded per fail-safe"}))

_d2_run(9,  "discovery",          "Discovery",
        "discovery_cycle_log (global cycle freshness)",
        _d2_help.check_discovery_cycle_freshness, _t)

# ── Stage 10 — this is the mutated stage ──────────────────────────────────
_d2_run(10, "technical_signal",   "Technical Signal",
        "module_scores_generated (technical component)",
        lambda: _d2_help.technical_signal_evidence(pick, _raw_sc))

# ── Stages 11-17 continue independently to prove isolation ────────────────
_d2_run(11, "options_smart_money","Options / Smart Money",
        "module_scores_generated (options component)",
        lambda: {"source": pick["source"],
                 "note": "options/smart-money contribution embedded in unified raw_score"})

_d2_run(12, "quant_stat_edge",    "Quant / Statistical Edge",
        "layer9_scores (global scanner freshness)",
        _d2_help.check_layer9_freshness, _t)

print("  Stage 13  probability_engine         calling live_query (may take ~15s)...")
_d2_run(13, "probability_engine", "Probability Engine",
        "aiem_probability_engine.live_query.run_live_query(mode='ticker')",
        _d2_help.run_probability_engine_for_ticker, _t)

_d2_run(14, "scoring_synthesis",  "Scoring / Synthesis",
        "candidate_ranking_created + trust_weights_applied + drift_gate_checked",
        lambda: {"raw_score": _raw_sc, "trust_mult": _tw_lbl,
                 "drift_mult": _dm_lbl, "final_score": _fin_sc})

_d2_run(15, "specialist_council", "Specialist Council / Bull-Bear",
        "aiem_bull_bear.run_bull_bear_debate (reused batch-level debate)",
        lambda: (_debate_verdicts[_t]["debate"] if _t in _debate_verdicts else
                 (_ for _ in ()).throw(RuntimeError(
                     f"no bull/bear debate ran for {_t} today"))))

_d2_run(16, "risk_gate",          "Risk Gate / Position Sizing",
        "sizing gate + batch-level kill_switch/daily_loss/portfolio_corr/macro gates",
        lambda: {"sizing_gate_result": _sizing_gate,
                 "kill_switch": "CLEAR", "daily_loss_limit": "CLEAR",
                 "portfolio_correlation": "CLEAR"})

_d2_run(17, "decision_engine",    "Decision Engine",
        "_aiem_paper_execute_today (final_aiem_decision)",
        lambda: {"decision": "EXECUTE", "ticker": _t,
                 "price": _fill_price, "type": _trade_type})

# ── Summary ────────────────────────────────────────────────────────────────
passes = sum(1 for s, _ in results.values() if s == "PASS")
fails  = sum(1 for s, _ in results.values() if s == "FAIL")

print(f"\n{'='*60}")
print(f"  RESULT: {passes} PASS  |  {fails} FAIL")
print(f"  trace_id: {trace_id}")
print(f"{'='*60}\n")

# ── Pull from DB to confirm records written ────────────────────────────────
import psycopg2, psycopg2.extras
db = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute("""
    SELECT stage_order, stage_name, status,
           EXTRACT(EPOCH FROM (completed_at - started_at))*1000 AS duration_ms,
           error_message
    FROM aiem_diagram2_trace_audit
    WHERE trace_id = %s
    ORDER BY stage_order
""", (trace_id,))
rows = cur.fetchall()
cur.close(); db.close()

print(f"DB audit rows for trace_id={trace_id}:\n")
print(f"  {'#':>3}  {'stage_name':<28}  {'status':<6}  {'ms':>8}  error")
print(f"  {'-'*3}  {'-'*28}  {'-'*6}  {'-'*8}  -----")
for r in rows:
    err = r['error_message'] or ''
    print(f"  {r['stage_order']:>3}  {r['stage_name']:<28}  {r['status']:<6}  "
          f"{float(r['duration_ms'] or 0):>8.1f}  {err[:60]}")
print(f"\nTotal DB rows written: {len(rows)}")
