"""
Controlled TEST F / TEST G proof harness for G4
(LEARNING / MODEL PROMOTION GOVERNANCE), per the Path B spec Section 10.

Unlike G3's harness (which calls require_governance_authorization directly
because G3's real call sites live in a background loop), G4's single real
choke-point is the live Flask admin endpoint itself:
    POST /stock-api/admin/learning-proposals/<id>/approve
so this harness drives that ACTUAL endpoint over real HTTP against the
running stock-api process, using synthetic-but-real test rows inserted into
the real aiem_learning_proposals and model_versions tables (model_name
prefixed G4TEST_ so they can never collide with or be mistaken for a real
production model). This is the only way to prove the real wiring -- not
just the policy math -- actually blocks/allows the real promotion write.

Run directly: python3 aiem_diagram3_g4_verify.py
Requires ADMIN_TOKEN in the environment (same token main.py checks) and the
stock-api process already running on STOCK_API_PORT (default 5050).

All rows written are real, permanent, honestly-tagged test data (append-only
ledger convention, same as G2TEST_*/G3TEST_* -- no delete carve-out).
Restores G4's real mode (SHADOW) at the end, in a finally block, even if an
assertion fails midway. Safe to re-run any time as a regression check after
future G4 changes (each run uses a fresh uuid-suffixed model_name).
"""
import hashlib
import os
import pickle
import sys
import uuid

import numpy as np
import psycopg2
import requests

import aiem_diagram3_governance as d3

BASE_URL = f"http://127.0.0.1:{os.environ.get('STOCK_API_PORT', 5050)}"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _seed_model_version(model_name: str, version: int, n_samples: int, held_out_score: float):
    """Writes a real model_versions row using the exact weights_blob/
    weights_hash convention online_learning.propose_update() uses (real
    pickle + real sha256, not a fabricated string) so G4's version-manifest
    cross-check is validated against genuine artifact data."""
    weights = np.random.default_rng(42).normal(size=6)
    weights_blob = pickle.dumps(weights)
    weights_hash = hashlib.sha256(weights_blob).hexdigest()[:16]
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO model_versions
                (model_name, version, weights_blob, weights_hash,
                 trained_on_n_samples, held_out_score, is_live, notes)
            VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s)
            """,
            (model_name, version, weights_blob, weights_hash, n_samples,
             held_out_score, "G4TEST controlled proof harness row"),
        )
        conn.commit()
    return weights_hash


def _seed_proposal(model_name: str, n_samples, accepted, version_saved,
                    weights_hash, current_score, new_score, max_drift):
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO aiem_learning_proposals
                (model_name, n_samples, accepted, promoted, version_saved,
                 weights_hash, max_drift_observed, current_score, new_score,
                 reason, notes)
            VALUES (%s, %s, %s, FALSE, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (model_name, n_samples, accepted, version_saved, weights_hash,
             max_drift, current_score, new_score,
             "G4TEST controlled proof harness proposal",
             "G4TEST controlled proof harness row"),
        )
        proposal_id = cur.fetchone()[0]
        conn.commit()
    return proposal_id


def _read_promoted_and_live(model_name: str, proposal_id: int, version: int):
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT promoted FROM aiem_learning_proposals WHERE id=%s", (proposal_id,))
        promoted = cur.fetchone()[0]
        cur.execute(
            "SELECT is_live FROM model_versions WHERE model_name=%s AND version=%s",
            (model_name, version),
        )
        row = cur.fetchone()
        is_live = row[0] if row else None
    return promoted, is_live


def _approve(proposal_id: int):
    resp = requests.post(
        f"{BASE_URL}/stock-api/admin/learning-proposals/{proposal_id}/approve",
        headers={"X-Admin-Token": ADMIN_TOKEN},
        timeout=15,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def main():
    if not ADMIN_TOKEN:
        print("FATAL: ADMIN_TOKEN not set in environment -- cannot drive the real admin endpoint.")
        sys.exit(2)

    failures = []

    cfg_before = d3.get_d3_checkpoint_config()["checkpoints"]
    g4_mode_before = next(r["mode"] for r in cfg_before if r["checkpoint"] == "G4")
    print(f"[setup] G4 mode before test = {g4_mode_before!r}")

    try:
        d3.set_d3_checkpoint_mode(checkpoint="G4", mode="ENFORCE",
                                   reason="TEST F/G controlled proof run",
                                   changed_by="aiem_diagram3_g4_verify.py", confirm=True)

        # ------------------------------------------------------------------
        hr("TEST F — INSUFFICIENT-SAMPLES PROPOSAL BLOCKED, REAL PROMOTION NEVER OCCURS")
        # ------------------------------------------------------------------
        model_f = f"G4TEST_CALIBRATION_FAIL_{uuid.uuid4().hex[:8]}"
        weights_hash_f = _seed_model_version(model_f, version=1, n_samples=30, held_out_score=0.60)
        proposal_f = _seed_proposal(
            model_name=model_f, n_samples=30, accepted=True, version_saved=1,
            weights_hash=weights_hash_f, current_score=0.50, new_score=0.60, max_drift=0.05,
        )
        print(f"[TEST F] seeded model={model_f} proposal_id={proposal_f} "
              f"n_samples=30 (< 100 threshold), accepted=True, otherwise healthy scores")

        status_f, body_f = _approve(proposal_f)
        print(f"[TEST F] HTTP {status_f} body={body_f}")
        promoted_f, is_live_f = _read_promoted_and_live(model_f, proposal_f, 1)
        print(f"[TEST F] readback: promoted={promoted_f} is_live={is_live_f}")

        ok_f = (
            status_f == 409
            and any("INSUFFICIENT_SAMPLES" in rc for rc in [body_f.get("reason_code", "")])
            and promoted_f is False
            and is_live_f is False
            and body_f.get("governance_decision_id") is not None
        )
        print(f"TEST F {'PASS' if ok_f else 'FAIL'}")
        if not ok_f:
            failures.append("TEST F")

        # ------------------------------------------------------------------
        hr("TEST G — HAPPY PATH: ALL GATES PASS, REAL PROMOTION OCCURS")
        # ------------------------------------------------------------------
        model_g = f"G4TEST_HAPPY_PATH_{uuid.uuid4().hex[:8]}"
        weights_hash_g = _seed_model_version(model_g, version=1, n_samples=150, held_out_score=0.55)
        proposal_g = _seed_proposal(
            model_name=model_g, n_samples=150, accepted=True, version_saved=1,
            weights_hash=weights_hash_g, current_score=0.50, new_score=0.55, max_drift=0.05,
        )
        print(f"[TEST G] seeded model={model_g} proposal_id={proposal_g} "
              f"n_samples=150 (>= 100), drift=0.05 (< 0.20), perf improved, first version "
              f"(no rollback target -- must be honestly allowed, not blocked)")

        status_g, body_g = _approve(proposal_g)
        print(f"[TEST G] HTTP {status_g} body={body_g}")
        promoted_g, is_live_g = _read_promoted_and_live(model_g, proposal_g, 1)
        print(f"[TEST G] readback: promoted={promoted_g} is_live={is_live_g}")

        ok_g = (
            status_g == 200
            and body_g.get("promoted") is True
            and promoted_g is True
            and is_live_g is True
            and body_g.get("g4_governance", {}).get("decision") == "ALLOW"
        )
        print(f"TEST G {'PASS' if ok_g else 'FAIL'}")
        if not ok_g:
            failures.append("TEST G")

        # ------------------------------------------------------------------
        hr("TEST G (SUB-CHECK) — SECOND APPROVE ATTEMPT ON SAME PROPOSAL IS REJECTED")
        # ------------------------------------------------------------------
        status_g2, body_g2 = _approve(proposal_g)
        print(f"[TEST G re-approve] HTTP {status_g2} body={body_g2}")
        ok_g2 = status_g2 == 400 and "already promoted" in str(body_g2.get("error", "")).lower()
        print(f"TEST G (already-promoted guard) {'PASS' if ok_g2 else 'FAIL'}")
        if not ok_g2:
            failures.append("TEST G (already-promoted guard)")

    finally:
        d3.set_d3_checkpoint_mode(checkpoint="G4", mode=g4_mode_before,
                                   reason="restore after controlled test run",
                                   changed_by="aiem_diagram3_g4_verify.py",
                                   confirm=(g4_mode_before == "ENFORCE"))
        cfg_after = d3.get_d3_checkpoint_config()["checkpoints"]
        g4_mode_after = next(r["mode"] for r in cfg_after if r["checkpoint"] == "G4")
        print(f"\n[teardown] G4 mode restored to {g4_mode_after!r} "
              f"(matches before={g4_mode_after == g4_mode_before})")

    hr("SUMMARY")
    if failures:
        print(f"FAILED: {failures}")
        sys.exit(1)
    else:
        print("ALL TESTS (F, G) PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
