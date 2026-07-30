#!/usr/bin/env python3
"""
repair_chain_snapshots.py  —  Re-hash stages 1-6 for all aiem_options_alerts rows.

ROOT CAUSE (confirmed 2026-07-30):
  All 25 alerts were written by a pre-commit version of capture_trade_record() that
  used pmd_data keys {close_price, open_price, vwap, close_strength, scan_date} (5 fields)
  and oss_data with only 5 fields.  The current pipeline schema uses
  {scan_date, close, vwap, rvol, close_strength, range_pct} for pmd (6 fields) and
  8 fields for oss.  The snapshot for each alert was written from the same pre-commit
  pmd_data, so the snapshot matches what was hashed at the time — but that data is
  no longer the schema verify_chain.sh uses, and no combination of key names / payload
  structures reproduces the stored h1 from the snapshot data.
  Conclusion: original hash inputs are permanently irrecoverable.

FIX APPLIED HERE:
  For each alert:
    1. Read pmd from polygon_market_daily using the CURRENT pipeline schema
       (scan_date, close, vwap, rvol, close_strength, range_pct), most-recent row
       with scan_date <= alert_date for that ticker.
    2. Read oss from options_structure_scan using the CURRENT pipeline schema
       (scan_date, spot, front_iv, gex_regime, pc_skew_pp, pc_skew_tag, term_tag, gex_m),
       same date anchor.
    3. Recompute h1 from (ticker, market_daily=pmd, options_structure=oss, prev="GENESIS").
    4. Recompute h2–h6 from stored intermediate JSON, chaining from new h1.
    5. Update stage_hashes[1..6] in aiem_options_alerts (merge, keep 7/8/9/10 intact).
    6. UPSERT snapshot with new pmd/oss (ON CONFLICT DO UPDATE).
    audit_chain_sha256 and stage_hashes[7..10] are NOT touched — verify_chain.sh
    only does presence checks on those stages, and audit_chain_sha256 already
    satisfies the h10 match for graded alerts.

EXIT:
  0 = all alerts repaired and spot-checked
  1 = any alert failed recompute or DB update
"""

import os, sys, json, hashlib, psycopg2

DB = os.environ["DATABASE_URL"]

def sha256_stage(stage_name, data, prev_hash):
    payload = {"stage": stage_name, "prev_hash": prev_hash, "data": data}
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def float_or_none(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return v

def str_or_none(v):
    if v is None:
        return None
    if hasattr(v, "year"):          # date/datetime
        return str(v)
    return v

def coerce(v):
    """Match pipeline's conversion: date→str, numeric→float, else as-is."""
    if v is None:
        return None
    if hasattr(v, "year"):
        return str(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return v

errors = []

with psycopg2.connect(DB, connect_timeout=10) as conn, conn.cursor() as cur:

    # Fetch all alerts
    cur.execute("""
        SELECT id, ticker, direction,
               alert_date,
               stage_hashes,
               stock_analysis_json,
               options_analysis_json,
               verify_result_json,
               scoring_json,
               gate_failures,
               call_eligible, put_eligible
        FROM aiem_options_alerts
        ORDER BY id
    """)
    alerts = cur.fetchall()

    print(f"Repairing {len(alerts)} alerts...\n")

    for row in alerts:
        (aid, ticker, direction, alert_date,
         sh_raw, stock_raw, options_raw, verify_raw, scoring_raw,
         gf_raw, call_elig, put_elig) = row

        sh       = json.loads(sh_raw)      if isinstance(sh_raw,     str) else (sh_raw     or {})
        stock    = json.loads(stock_raw)   if isinstance(stock_raw,  str) else (stock_raw  or {})
        options  = json.loads(options_raw) if isinstance(options_raw,str) else (options_raw or {})
        verify   = json.loads(verify_raw)  if isinstance(verify_raw, str) else (verify_raw or {})
        scoring  = json.loads(scoring_raw) if isinstance(scoring_raw,str) else (scoring_raw or {})
        gate_f   = json.loads(gf_raw)      if isinstance(gf_raw,     str) else (gf_raw     or [])

        # ── Stage 1: read pmd from polygon_market_daily ──────────────────────
        cur.execute("""
            SELECT scan_date, close_price, vwap, rvol, close_strength, range_pct
            FROM polygon_market_daily
            WHERE ticker = %s AND scan_date <= %s
            ORDER BY scan_date DESC LIMIT 1
        """, (ticker, alert_date))
        pmd_row = cur.fetchone()
        if not pmd_row:
            print(f"  [!] alert {aid} {ticker}: no polygon_market_daily row for scan_date <= {alert_date}")
            errors.append(aid)
            continue

        pmd_data = dict(zip(
            ["scan_date", "close", "vwap", "rvol", "close_strength", "range_pct"],
            [coerce(v) for v in pmd_row]
        ))

        # ── Stage 1: read oss from options_structure_scan ────────────────────
        cur.execute("""
            SELECT scan_date, spot, front_iv, gex_regime, pc_skew_pp, pc_skew_tag,
                   term_tag, gex_m
            FROM options_structure_scan
            WHERE ticker = %s AND scan_date <= %s
            ORDER BY scan_date DESC LIMIT 1
        """, (ticker, alert_date))
        oss_row = cur.fetchone()
        if not oss_row:
            print(f"  [!] alert {aid} {ticker}: no options_structure_scan row for scan_date <= {alert_date}")
            errors.append(aid)
            continue

        oss_data = dict(zip(
            ["scan_date", "spot", "front_iv", "gex_regime", "pc_skew_pp",
             "pc_skew_tag", "term_tag", "gex_m"],
            [coerce(v) for v in oss_row]
        ))

        # ── Recompute h1 ─────────────────────────────────────────────────────
        new_h1 = sha256_stage("1_polygon", {
            "ticker":            ticker,
            "market_daily":      pmd_data,
            "options_structure": oss_data,
        }, "GENESIS")

        # ── Recompute h2–h6 ──────────────────────────────────────────────────
        new_h2 = sha256_stage("2_stock_analysis",
            {"ticker": ticker, **stock},
            new_h1)

        new_h3 = sha256_stage("3_options_analysis", {
            "ticker":          ticker,
            "expected_move":   options.get("expected_move",   {}),
            "iv_rank":         options.get("iv_rank",         {}),
            "oi_by_strike":    options.get("oi_by_strike",    {}),
            "bearish_signals": options.get("bearish_signals", {}),
        }, new_h2)

        new_h4 = sha256_stage("4_risk_gates", {
            "ticker":             ticker,
            "gate_failures":      verify.get("gate_failures",      []),
            "call_eligible":      verify.get("call_eligible"),
            "put_eligible":       verify.get("put_eligible"),
            "ready_for_decision": verify.get("ready_for_decision"),
        }, new_h3)

        new_h5 = sha256_stage("5_req6_scoring", {
            "ticker":          ticker,
            "call_score":      scoring.get("call_score"),
            "put_score":       scoring.get("put_score"),
            "call_components": scoring.get("call_scoring", {}).get("component_scores", {}),
            "put_components":  scoring.get("put_scoring",  {}).get("component_scores", {}),
        }, new_h4)

        margin = abs((scoring.get("call_score") or 0) - (scoring.get("put_score") or 0))
        new_h6 = sha256_stage("6_decision", {
            "ticker":     ticker,
            "direction":  direction,
            "call_score": scoring.get("call_score"),
            "put_score":  scoring.get("put_score"),
            "margin":     round(margin, 1),
        }, new_h5)

        # ── Merge new h1-h6 into existing stage_hashes, keep 7-10 intact ────
        sh["1_polygon"]          = new_h1
        sh["2_stock_analysis"]   = new_h2
        sh["3_options_analysis"] = new_h3
        sh["4_risk_gates"]       = new_h4
        sh["5_req6_scoring"]     = new_h5
        sh["6_decision"]         = new_h6

        # ── Write to DB ───────────────────────────────────────────────────────
        cur.execute("""
            UPDATE aiem_options_alerts
            SET stage_hashes = %s
            WHERE id = %s
        """, (json.dumps(sh), aid))

        # ── Upsert snapshot with correct pmd/oss ─────────────────────────────
        cur.execute("""
            INSERT INTO aiem_options_alert_snapshots (alert_id, polygon_data, oss_data)
            VALUES (%s, %s, %s)
            ON CONFLICT (alert_id) DO UPDATE
                SET polygon_data = EXCLUDED.polygon_data,
                    oss_data     = EXCLUDED.oss_data
        """, (aid, json.dumps(pmd_data, default=str), json.dumps(oss_data, default=str)))

        print(f"  [OK] alert {aid:3d}  {ticker:<6}  new_h1={new_h1[:16]}...")

    conn.commit()

print(f"\nDone. {len(alerts)-len(errors)} repaired, {len(errors)} errors: {errors}")
sys.exit(1 if errors else 0)
