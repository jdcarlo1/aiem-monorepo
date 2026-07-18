"""
EI Section 3 — DB evidence script.
Runs the three Section 3 queries with full SQL text printed before each result.
Intended to be invoked via tools/verified_run.sh so output is hash-chained.
"""
import os, sys, json, uuid, datetime
sys.path.insert(0, os.path.dirname(__file__))

import psycopg2
from aiem_execution_intelligence import evaluate_execution_quality, save_execution_assessment, record_learning_outcome

DB_URL = os.environ["DATABASE_URL"]
SCAN_DATE = datetime.date.today().isoformat()
TRACE_ID  = "ei_s3_chain_" + uuid.uuid4().hex[:8]

APPROVED_STRAT = {
    "strategy": "LONG_CALL",
    "direction": "BULLISH",
    "ev_after_costs": 100.0,
    "max_profit": 500.0, "max_loss": 200.0, "pop": 0.65,
    "legs": [{
        "action": "BUY", "contract_type": "call",
        "strike": 150.0, "expiration_date": "2026-08-15", "dte": 28,
        "bid": 2.00, "ask": 2.04, "mid": 2.02,
        "bid_ask_spread_pct": 0.02,
        "bid_size": 10, "ask_size": 15,
        "volume": 1000, "open_interest": 5000,
        "implied_volatility": 0.25,
        "delta": 0.55, "gamma": 0.04, "theta": -0.02, "vega": 0.18,
    }],
}

assessment = evaluate_execution_quality(APPROVED_STRAT, TRACE_ID, SCAN_DATE, "S3_CHAIN_TEST")
CANDIDATE_ID = assessment.candidate_id

print("=" * 70)
print("EI SECTION 3 — DB EVIDENCE  (via tools/verified_run.sh)")
print(f"run_utc:      {datetime.datetime.utcnow().isoformat()}Z")
print(f"trace_id:     {TRACE_ID}")
print(f"candidate_id: {CANDIDATE_ID}")
print("=" * 70)

with psycopg2.connect(DB_URL, connect_timeout=10) as conn:
    with conn.cursor() as cur:

        # ── QUERY 1: Schema introspection ─────────────────────────────────
        SQL_SCHEMA = """
SELECT column_name,
       data_type,
       character_maximum_length,
       numeric_precision,
       numeric_scale,
       is_nullable
FROM information_schema.columns
WHERE table_name = 'aiem_execution_assessments'
ORDER BY ordinal_position
""".strip()
        print()
        print("── QUERY 1: Schema introspection ──")
        print("SQL:")
        print(SQL_SCHEMA)
        print()
        cur.execute(SQL_SCHEMA)
        rows = cur.fetchall()
        print(f"{'column_name':<35} {'data_type':<22} {'nullable'}")
        print(f"{'-'*35} {'-'*22} {'-'*8}")
        for r in rows:
            col, dtype, cmax, np_, ns, nullable = r
            type_str = dtype
            if np_ is not None and ns is not None:
                type_str = f"{dtype}({np_},{ns})"
            elif cmax is not None:
                type_str = f"{dtype}({cmax})"
            print(f"{col:<35} {type_str:<22} {nullable}")
        print(f"({len(rows)} columns total)")

        # ── Save assessment so round-trip has a real row ───────────────────
        saved_id = save_execution_assessment(assessment, DB_URL)
        print()
        print(f"[pre-query-2 save] saved_id={saved_id}  (== candidate_id: {saved_id == CANDIDATE_ID})")

        # ── QUERY 2: Round-trip read ───────────────────────────────────────
        SQL_ROUNDTRIP = """
SELECT candidate_id,
       trace_id,
       ticker,
       strategy_name,
       fill_probability,
       liquidity_score,
       net_expected_edge,
       approved,
       config_sha256,
       raw_assessment_json,
       gating_enabled,
       expected_entry_price,
       exit_liquidity_score,
       early_assignment_risk,
       pin_risk_flag,
       market_impact_dollars
FROM aiem_execution_assessments
WHERE candidate_id = %s
""".strip()
        print()
        print("── QUERY 2: Round-trip read ──")
        print("SQL:")
        print(SQL_ROUNDTRIP.replace("%s", f"'{CANDIDATE_ID}'"))
        print()
        cur.execute(SQL_ROUNDTRIP, (CANDIDATE_ID,))
        row = cur.fetchone()
        if row is None:
            print("ERROR: no row found")
            sys.exit(1)
        labels = [
            "candidate_id", "trace_id", "ticker", "strategy_name",
            "fill_probability", "liquidity_score", "net_expected_edge",
            "approved", "config_sha256", "raw_assessment_json",
            "gating_enabled", "expected_entry_price", "exit_liquidity_score",
            "early_assignment_risk", "pin_risk_flag", "market_impact_dollars",
        ]
        for label, val in zip(labels, row):
            if label == "raw_assessment_json":
                n_keys = len(val) if isinstance(val, dict) else len(json.loads(val))
                print(f"  {label:<30} = <jsonb, {n_keys} keys>")
            elif label == "config_sha256":
                print(f"  {label:<30} = {val}  (full 64-char)")
            else:
                print(f"  {label:<30} = {val}")

        # ── Write learning outcome so query 3 has non-null values ──────────
        ok = record_learning_outcome(
            CANDIDATE_ID,
            actual_fill_price=2.0350,
            actual_slippage=0.0600,
            actual_transaction_cost=4.50,
            db_url=DB_URL,
        )
        print()
        print(f"[pre-query-3 learning write] record_learning_outcome returned: {ok}")

        # ── QUERY 3: Learning outcome row ──────────────────────────────────
        SQL_LEARNING = """
SELECT actual_fill_price,
       actual_slippage,
       actual_transaction_cost,
       entry_price_error,
       slippage_error,
       cost_error
FROM aiem_execution_assessments
WHERE candidate_id = %s
""".strip()
        print()
        print("── QUERY 3: Learning outcome row ──")
        print("SQL:")
        print(SQL_LEARNING.replace("%s", f"'{CANDIDATE_ID}'"))
        print()
        cur.execute(SQL_LEARNING, (CANDIDATE_ID,))
        lo = cur.fetchone()
        if lo is None:
            print("ERROR: no learning row found")
            sys.exit(1)
        lo_labels = [
            "actual_fill_price", "actual_slippage", "actual_transaction_cost",
            "entry_price_error", "slippage_error", "cost_error",
        ]
        for label, val in zip(lo_labels, lo):
            print(f"  {label:<30} = {val}")
        print()
        print("(entry_price_error = actual_fill_price - expected_entry_price)")
        print("(slippage_error    = actual_slippage   - expected_slippage_dollars)")
        print("(cost_error        = actual_transaction_cost - total_transaction_cost)")

print()
print("=" * 70)
print("SECTION 3 COMPLETE — 3 queries executed, all results printed above")
print("=" * 70)
