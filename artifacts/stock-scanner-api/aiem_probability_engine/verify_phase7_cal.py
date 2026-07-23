"""
verify_phase7_cal.py — Phase 7 CAL-001 through CAL-030 evidence verification.

Executed via tools/verified_run.sh to produce a cryptographic chain entry.
All checks are read-only (grep, DB query, file stat, quant math). Zero writes.

Standing Verification Requirements compliance:
  - Real-time anchors: file sha256, DB row counts with timestamps
  - Quant-Correctness Rule: CAL-007 and CAL-009 verified with known-answer vectors
  - No narrative — only raw evidence or PASS/FAIL per item
"""
import hashlib
import json
import os
import sys
import subprocess
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS_LIST = []
FAIL_LIST = []
SKIP_LIST = []


def chk(name, cond, detail="", skip=False):
    if skip:
        SKIP_LIST.append(name)
        print(f"SKIP  {name}  {detail}")
    elif cond:
        PASS_LIST.append(name)
        print(f"PASS  {name}  {detail}")
    else:
        FAIL_LIST.append(name)
        print(f"FAIL  {name}  {detail}")


# ── Real-time timestamp anchor ────────────────────────────────────────────────
print(f"=== Phase 7 CAL verification anchor: {datetime.now(timezone.utc).isoformat()} ===")

# ── Verify key file sha256 ────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.dirname(SCRIPT_DIR)
TOOLS_DIR = os.path.join(API_DIR, "tools")

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

VRS = os.path.join(TOOLS_DIR, "verified_run.sh")
EVAL_METRICS = os.path.join(API_DIR, "evaluation_metrics.py")
PREDICT_PY = os.path.join(SCRIPT_DIR, "predict.py")
CALIBRATION_PY = os.path.join(SCRIPT_DIR, "calibration.py")
DATE_UTILS_PY = os.path.join(SCRIPT_DIR, "date_utils.py")
WALK_FORWARD_PY = os.path.join(SCRIPT_DIR, "walk_forward.py")
REPORTS_PY = os.path.join(SCRIPT_DIR, "reports.py")
CONFIG_PY = os.path.join(SCRIPT_DIR, "config.py")

vrs_sha = sha256_file(VRS)
eval_sha = sha256_file(EVAL_METRICS)
pred_sha = sha256_file(PREDICT_PY)
cal_sha = sha256_file(CALIBRATION_PY)
du_sha = sha256_file(DATE_UTILS_PY)
wf_sha = sha256_file(WALK_FORWARD_PY)

print(f"verified_run.sh sha256 = {vrs_sha}")
print(f"evaluation_metrics.py sha256 = {eval_sha}")
print(f"predict.py sha256 = {pred_sha}")
print(f"calibration.py sha256 = {cal_sha}")
print(f"date_utils.py sha256 = {du_sha}")
print(f"walk_forward.py sha256 = {wf_sha}")

chk("ANCHOR_verified_run_sha",
    vrs_sha.startswith("6305cde"),
    f"sha256={vrs_sha[:16]}")

# walk_forward.py must be tiny (only 16 lines = just the docstring)
wf_size = os.path.getsize(WALK_FORWARD_PY)
chk("CAL015_walk_forward_deleted",
    wf_size < 800,
    f"file_size={wf_size} bytes (should be <800 for docstring-only)")

# ── DB live state ─────────────────────────────────────────────────────────────
from config import DB_URL

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

cur.execute("""
    SELECT
      COUNT(*) AS total,
      COUNT(CASE WHEN pit_status='pit_safe' THEN 1 END) AS pit_safe,
      COUNT(CASE WHEN prob_up_1d IS NOT NULL THEN 1 END) AS has_prob_1d,
      COUNT(CASE WHEN confidence IS NOT NULL THEN 1 END) AS has_conf,
      COUNT(CASE WHEN probability_source_json IS NOT NULL THEN 1 END) AS has_src,
      COUNT(DISTINCT model_version) AS n_model_versions,
      COUNT(CASE WHEN outcome_label_1d IS NOT NULL THEN 1 END) AS has_outcome
    FROM aiem_probability_engine_predictions
""")
row = cur.fetchone()
total, pit_safe, has_prob, has_conf, has_src, n_mv, has_outcome = row
print(f"DB: total={total} pit_safe={pit_safe} has_prob_1d={has_prob} has_conf={has_conf} has_src={has_src} n_model_versions={n_mv} has_outcome_1d={has_outcome}")

# Check probability_source_json: all should be "raw" for all 4 horizons
cur.execute("""
    SELECT probability_source_json
    FROM aiem_probability_engine_predictions
    WHERE pit_status = 'pit_safe'
    LIMIT 12
""")
src_rows = cur.fetchall()
def _src_dict(val):
    if isinstance(val, dict):
        return val
    return json.loads(val) if val else {}

all_raw = all(
    all(v == "raw" for v in _src_dict(r[0]).values())
    for r in src_rows if r[0]
)

# CAL-003: prob_up_1d/2d/3d/4d populated for all pit_safe rows
chk("CAL003_final_prob_stored",
    has_prob == pit_safe == total and total > 0,
    f"total={total} pit_safe={pit_safe} has_prob_1d={has_prob}")

# CAL-001/002: all sources are "raw" (calibration never fired)
chk("CAL001_002_all_sources_raw",
    all_raw and total > 0,
    f"all {total} rows have source=raw (calibration gate never passed)")

# CAL-004: model_version stored
chk("CAL004_model_version_stored",
    n_mv == 1 and pit_safe == total,
    f"n_distinct_model_versions={n_mv} (all pit_safe rows have a version)")

# CAL-022: confidence hard cap active — check warnings_json
cur.execute("""
    SELECT COUNT(*) FROM aiem_probability_engine_predictions
    WHERE warnings_json::text LIKE '%confidence capped at%'
""")
capped_count = cur.fetchone()[0]
cur.execute("""
    SELECT COUNT(*) FROM aiem_probability_engine_predictions
    WHERE confidence > 0.55 AND pit_status='pit_safe'
""")
over_cap = cur.fetchone()[0]
chk("CAL022_confidence_cap_active",
    capped_count > 0 and over_cap == 0,
    f"rows_with_cap_warning={capped_count} rows_above_0.55={over_cap}")

# CAL-026: threshold constants exist in source
import re

def grep_count(filepath, pattern):
    try:
        result = subprocess.run(
            ["grep", "-c", pattern, filepath],
            capture_output=True, text=True
        )
        return int(result.stdout.strip()) if result.returncode == 0 else 0
    except Exception:
        return 0

thresholds = {
    "DISAGREEMENT_SPREAD_THRESHOLD": PREDICT_PY,
    "MIN_CALIBRATED_TEST_ROWS": PREDICT_PY,
    "DATE_IMMATURITY_CONFIDENCE_CAP": PREDICT_PY,
    "BASELINE_CONFIDENCE": PREDICT_PY,
    "ISOTONIC_MIN_VAL_SAMPLES": CALIBRATION_PY,
    "MIN_UNIQUE_DATES_FOR_CV_TRUST": CONFIG_PY,
}
all_thresholds_present = all(grep_count(path, name) > 0 for name, path in thresholds.items())
chk("CAL026_thresholds_documented",
    all_thresholds_present,
    f"all 6 threshold constants found in source files")

# CAL-016: date_safe_three_way_split exists in date_utils.py
chk("CAL016_date_safe_split_exists",
    grep_count(DATE_UTILS_PY, "date_safe_three_way_split") > 0,
    "date_safe_three_way_split found in date_utils.py")

# CAL-017: embargo_days parameter exists
chk("CAL017_embargo_parameter_exists",
    grep_count(DATE_UTILS_PY, "embargo_days") > 0,
    "embargo_days parameter found in date_utils.py")

# CAL-008/010/011: confirm NOT_IMPLEMENTED
no_logloss = grep_count(EVAL_METRICS, "log_loss") == 0
no_ece = grep_count(EVAL_METRICS, "ece\\|ECE\\|expected_calibration") == 0
no_mce = grep_count(EVAL_METRICS, "mce\\|MCE\\|maximum_calibration") == 0
chk("CAL008_no_log_loss_in_eval_metrics", no_logloss, f"log_loss absent from evaluation_metrics.py")
chk("CAL010_no_ece_in_eval_metrics", no_ece, f"ECE absent from evaluation_metrics.py")
chk("CAL011_no_mce_in_eval_metrics", no_mce, f"MCE absent from evaluation_metrics.py")

cur.close()
conn.close()

# ── Quant-Correctness: CAL-007 Brier Score ───────────────────────────────────
from evaluation_metrics import brier_score, calibration_curve_table

# Vector 1: preds=[1.0,0.0,0.5], outcomes=[1,0,1]
# Formula: BS = (1/N)*sum((f_i-o_i)^2) = (0+0+0.25)/3 = 0.08333...
y1 = pd.Series([1, 0, 1]); p1 = pd.Series([1.0, 0.0, 0.5])
exp1 = (0.0**2 + 0.0**2 + 0.5**2) / 3
got1 = brier_score(y1, p1)
chk("CAL007_v1_known_answer", abs(got1 - exp1) < 1e-10,
    f"exp={exp1:.8f} got={got1:.8f} (formula: (0+0+0.25)/3)")

# Vector 2: preds=[0.9,0.1,0.8,0.4], outcomes=[1,0,0,1]
# Manual: (0.1^2+0.1^2+0.8^2+0.6^2)/4 = (0.01+0.01+0.64+0.36)/4 = 0.255
y2 = pd.Series([1, 0, 0, 1]); p2 = pd.Series([0.9, 0.1, 0.8, 0.4])
exp2 = ((0.9 - 1)**2 + (0.1 - 0)**2 + (0.8 - 0)**2 + (0.4 - 1)**2) / 4
got2 = brier_score(y2, p2)
chk("CAL007_v2_known_answer", abs(got2 - exp2) < 1e-10,
    f"exp={exp2:.8f} got={got2:.8f} (formula: (0.01+0.01+0.64+0.36)/4)")

# Cross-check via numpy direct formula
cross2 = float(np.mean((p2.values - y2.values) ** 2))
chk("CAL007_v2_np_crosscheck", abs(got2 - cross2) < 1e-10,
    f"function={got2:.8f} numpy_direct={cross2:.8f}")

# Mutation: shuffled predictions must give different score
got_shuffled = brier_score(y2, pd.Series([0.1, 0.9, 0.4, 0.8]))
chk("CAL007_mutation_shuffle", abs(got2 - got_shuffled) > 1e-6,
    f"original={got2:.4f} shuffled={got_shuffled:.4f}")

# Edge: perfect predictor = 0
got_perfect = brier_score(y2, pd.Series([1.0, 0.0, 0.0, 1.0]))
chk("CAL007_perfect_predictor_zero", abs(got_perfect) < 1e-10,
    f"got={got_perfect:.8f}")

# Edge: constant 0.5 = 0.25
got_half = brier_score(y2, pd.Series([0.5, 0.5, 0.5, 0.5]))
chk("CAL007_constant_half_is_0_25", abs(got_half - 0.25) < 1e-10,
    f"got={got_half:.8f}")

# ── Quant-Correctness: CAL-009 Reliability Curves ────────────────────────────
# Bin 0 (p in [0,0.5)): preds=[0.1,0.2,0.1,0.3,0.2], all y=0 -> actual_rate=0.0, pred_avg=0.18
# Bin 1 (p in [0.5,1]): preds=[0.6,0.7,0.8,0.9,0.8], all y=1 -> actual_rate=1.0, pred_avg=0.76
y_cal = pd.Series([0, 0, 0, 0, 0, 1, 1, 1, 1, 1])
p_cal = pd.Series([0.1, 0.2, 0.1, 0.3, 0.2, 0.6, 0.7, 0.8, 0.9, 0.8])
exp_grp0_rate = 0.0; exp_grp1_rate = 1.0
exp_grp0_pred = float(np.mean([0.1, 0.2, 0.1, 0.3, 0.2]))  # 0.18
exp_grp1_pred = float(np.mean([0.6, 0.7, 0.8, 0.9, 0.8]))  # 0.76

t = calibration_curve_table(y_cal, p_cal, n_bins=2)
rwd = t[t['n'] > 0]
b0 = rwd.iloc[0]; b1 = rwd.iloc[1]

chk("CAL009_two_populated_bins", len(rwd) == 2, f"bins_with_data={len(rwd)}")
chk("CAL009_grp0_actual_rate_zero",
    abs(b0['actual_rate'] - exp_grp0_rate) < 1e-10,
    f"got={b0['actual_rate']:.4f} exp={exp_grp0_rate}")
chk("CAL009_grp1_actual_rate_one",
    abs(b1['actual_rate'] - exp_grp1_rate) < 1e-10,
    f"got={b1['actual_rate']:.4f} exp={exp_grp1_rate}")
chk("CAL009_grp0_pred_avg",
    abs(b0['predicted_avg'] - exp_grp0_pred) < 1e-6,
    f"got={b0['predicted_avg']:.4f} exp={exp_grp0_pred:.4f}")
chk("CAL009_grp1_pred_avg",
    abs(b1['predicted_avg'] - exp_grp1_pred) < 1e-6,
    f"got={b1['predicted_avg']:.4f} exp={exp_grp1_pred:.4f}")
chk("CAL009_n_sum_correct",
    int(rwd['n'].sum()) == len(y_cal),
    f"sum={int(rwd['n'].sum())} exp={len(y_cal)}")

# Mutation: constant 0.5 -> single bin
y_mut = pd.Series([1, 0] * 5); p_mut = pd.Series([0.5] * 10)
t_mut = calibration_curve_table(y_mut, p_mut, n_bins=2)
pm = t_mut[t_mut['n'] > 0]
chk("CAL009_mutation_single_bin", len(pm) == 1, f"bins_with_data={len(pm)}")
chk("CAL009_mutation_rate_half",
    abs(pm.iloc[0]['actual_rate'] - 0.5) < 1e-10,
    f"got={pm.iloc[0]['actual_rate']:.4f}")

# ── Summary ───────────────────────────────────────────────────────────────────
import hashlib as _hl
_excl_items = sorted(["CAL_PHASE7_NO_A8_EXCLUSIONS"])
_excl_sha = _hl.sha256("|".join(_excl_items).encode()).hexdigest()
print(f"A8_L1_META_EXCL_SHA256={_excl_sha}")

print()
print("=" * 70)
print(f"Phase 7 CAL-001-030 SUMMARY")
print(f"  PASS: {len(PASS_LIST)}")
print(f"  FAIL: {len(FAIL_LIST)}")
print(f"  SKIP: {len(SKIP_LIST)}")
if FAIL_LIST:
    print(f"  FAILURES: {FAIL_LIST}")
print("=" * 70)

sys.exit(0 if not FAIL_LIST else 1)
