"""
verify_live_query.py - a genuinely STANDALONE verifier for an external
auditor. Deliberately kept minimal: it imports NOTHING from the ML
pipeline (no pandas/xgboost/sklearn/model_registry/data_snapshot) - only
psycopg2 (DB read) and aiem_provenance (the same HMAC key infra the
signer used). This means an auditor does not have to trust that
live_query.py's own bookkeeping is honest; this script recomputes the
signature from scratch, independently, against the raw envelope bytes
stored in the database.

What it checks, and reports honestly:
  1. The HMAC-SHA256 signature over the envelope's payload+timestamp+nonce
     verifies against AIEM_SIGNING_KEY (aiem_provenance.verify_payload).
  2. The row's stored pit_status is 'live_unsettled' (this script will
     flag - not silently pass - any row claiming 'pit_safe' out of this
     table, since that would be a false PIT claim; see live_query.py's
     module docstring for why live queries can never legitimately be
     pit_safe).
  3. Basic envelope hygiene: payload.pit_status matches the row's stored
     pit_status column (no drift between the signed content and the
     bookkeeping row).

Usage:
    python3 verify_live_query.py --row-id 13
    python3 verify_live_query.py --ticker MRNA           (most recent row)
    python3 verify_live_query.py --row-id 13 --json
"""
import argparse
import json
import os
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aiem_provenance import verify_payload  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL", "")
TABLE = "aiem_probability_engine_live_queries"


def _fetch_row(row_id: int = None, ticker: str = None) -> dict:
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if row_id is not None:
                cur.execute(f"SELECT * FROM {TABLE} WHERE id = %s", (row_id,))
            else:
                cur.execute(
                    f"SELECT * FROM {TABLE} WHERE ticker = %s ORDER BY id DESC LIMIT 1",
                    (ticker.upper(),),
                )
            return cur.fetchone()


def verify(row_id: int = None, ticker: str = None, max_age_seconds: int = 10 ** 9) -> dict:
    row = _fetch_row(row_id=row_id, ticker=ticker)
    if not row:
        return {"ok": False, "error": f"no row found (row_id={row_id!r}, ticker={ticker!r})"}

    envelope = row["envelope_json"]
    sig_result = verify_payload(envelope, max_age_seconds=max_age_seconds)

    payload = envelope.get("payload", {}) if isinstance(envelope, dict) else {}
    honesty_findings = []

    stored_pit_status = row.get("pit_status")
    payload_pit_status = payload.get("pit_status")
    if stored_pit_status != payload_pit_status:
        honesty_findings.append(
            f"MISMATCH: DB column pit_status={stored_pit_status!r} != signed "
            f"payload.pit_status={payload_pit_status!r} - the bookkeeping and the "
            f"signed content have drifted, do not trust either without investigating."
        )
    if payload_pit_status == "pit_safe":
        honesty_findings.append(
            "FALSE PIT CLAIM: a row in the LIVE query table is claiming pit_status="
            "'pit_safe' - live, not-yet-settled queries can never legitimately be "
            "pit_safe (there is no future signal_date for them to have leaked against "
            "or not); this is either a bug or a deliberately dishonest label."
        )
    elif payload_pit_status != "live_unsettled":
        honesty_findings.append(
            f"UNEXPECTED pit_status={payload_pit_status!r} for a row in the live-query "
            f"table - expected exactly 'live_unsettled'."
        )

    return {
        "ok": bool(sig_result.get("verified")) and not honesty_findings,
        "row_id": row["id"],
        "ticker": row["ticker"],
        "as_of_date": str(row["as_of_date"]),
        "mode": row.get("mode"),
        "signature_check": sig_result,
        "honesty_findings": honesty_findings,
        "payload": payload,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row-id", type=int, default=None)
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.row_id and not args.ticker:
        parser.error("must supply --row-id or --ticker")

    result = verify(row_id=args.row_id, ticker=args.ticker)

    if args.json:
        print(json.dumps(result, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0 if result["ok"] else 1)
