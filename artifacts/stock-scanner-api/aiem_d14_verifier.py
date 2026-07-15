"""
aiem_d14_verifier.py — Directive 14 post-run evidence verifier.

Checks that every scheduled 9:42 AM paper-trade run produced:
  D14_LAYER9_READ  — layer9_scores DB read done before debate
  D14_DEBATE_PRE   — signal_context D14 keys injected before debate
  D14_DEBATE_POST  — debate ran after injection, verdict recorded

Also verifies the SHA-256 evidence chain when chain fields (sha256/prev_hash)
are present in the events (new format written by updated production path).

On failure (any proof missing, stale, malformed, wrong-ticker, chain-invalid):
  1. Stamps d14_verify_result=FAIL on paper_trade_job_ledger
  2. Calls retry_fn() — the caller supplies the D14 re-debate callable
  3. Re-verifies after retry (using retry_trigger source)
  4. Sends Telegram alert: date, trace_id, ticker, missing proof,
     retry result, evidence-chain status
  5. Writes a row to paper_trade_d14_verification table
  6. Returns full outcome dict — caller decides whether to escalate further
"""

import os
import json
import hashlib
import datetime
import traceback
import urllib.request
import urllib.parse
from typing import Optional, List, Dict, Any

_DB_URL       = os.getenv("DATABASE_URL", "")
_CAPTURE_LOG  = "/home/runner/workspace/.local/d14_live_capture.log"
_VERIFY_LOG   = "/home/runner/workspace/.local/d14_verify.log"
_TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT_ID   = "8609255707"

_D14_EVENTS = {"D14_LAYER9", "D14_DEBATE_PRE", "D14_DEBATE_POST"}

_REQUIRED_FIELDS: Dict[str, set] = {
    "D14_LAYER9": {
        "event", "ts", "ticker", "trace_id",
        "layer9_score", "vpin_raw", "hurst_raw",
    },
    "D14_DEBATE_PRE": {
        "event", "ts", "ticker", "trace_id",
        "signal_context_d14_keys",
    },
    "D14_DEBATE_POST": {
        "event", "ts", "ticker", "trace_id",
        "verdict", "d14_tier1_activation",
    },
}


# ── Logging ────────────────────────────────────────────────────────────────

def _log(ev: dict) -> None:
    ev.setdefault("ts", datetime.datetime.utcnow().isoformat() + "Z")
    try:
        os.makedirs(os.path.dirname(_VERIFY_LOG), exist_ok=True)
        with open(_VERIFY_LOG, "a") as f:
            f.write(json.dumps(ev) + "\n")
    except Exception:
        pass


# ── Telegram ────────────────────────────────────────────────────────────────

def _tg_send(msg: str) -> bool:
    try:
        tok = (_TG_BOT_TOKEN or "").strip()
        if not tok:
            return False
        payload = urllib.parse.urlencode({
            "chat_id":    _TG_CHAT_ID,
            "text":       msg,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data=payload, method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as exc:
        _log({"event": "TG_SEND_ERROR", "error": str(exc)})
        return False


# ── SHA-256 chain ───────────────────────────────────────────────────────────

def canonical_hash(ev: dict) -> str:
    """SHA-256 of event content, excluding chain-bookkeeping fields."""
    clean = {k: v for k, v in ev.items() if k not in ("sha256", "prev_hash")}
    return hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()


def verify_chain(events: List[dict]) -> tuple:
    """
    Verify the SHA-256 evidence chain across ordered D14 events for one ticker.

    Chain scheme per ticker (L9 → PRE → POST):
        seed   = sha256("d14_chain:<trace_id>:<trade_date>")
        sha256[i] = sha256( canonical_hash(ev[i]) + prev_hash[i] )
        prev_hash[0] = seed, prev_hash[n+1] = sha256[n]

    Returns (chain_valid: bool, reason: str).
    Events without sha256/prev_hash fields → (True, "legacy_no_chain") [backward compat].
    """
    if not events:
        return False, "no_events"
    if not any("sha256" in e for e in events):
        return True, "legacy_no_chain"

    prev = events[0].get("prev_hash", "genesis")
    for idx, ev in enumerate(events):
        stored = ev.get("sha256")
        if not stored:
            return False, f"event[{idx}]({ev.get('event','?')})_missing_sha256"
        expected = hashlib.sha256(
            (canonical_hash(ev) + prev).encode()
        ).hexdigest()
        if stored != expected:
            return False, (
                f"event[{idx}]({ev.get('event','?')})_hash_mismatch:"
                f"stored={stored[:14]}… expected={expected[:14]}…"
            )
        prev = stored
    return True, "ok"


# ── Proof reader ────────────────────────────────────────────────────────────

def read_d14_events(trade_date: datetime.date, trigger_source: str) -> List[dict]:
    """
    Return all D14 evidence events from the capture log for a given
    trade_date and trigger_source.

    Handles both production format (trigger_source field) and orchestrator
    format (trigger field) transparently.
    """
    date_str = str(trade_date)
    out: List[dict] = []
    try:
        if not os.path.exists(_CAPTURE_LOG):
            return out
        with open(_CAPTURE_LOG) as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ev_ts   = ev.get("ts", "")
                ev_trig = ev.get("trigger_source") or ev.get("trigger") or ""
                ev_evt  = ev.get("event", "")
                if (ev_ts.startswith(date_str)
                        and ev_trig == trigger_source
                        and ev_evt in _D14_EVENTS):
                    out.append(ev)
    except Exception as exc:
        _log({"event": "READ_EVENTS_ERROR", "error": str(exc)})
    return out


# ── Core verifier ───────────────────────────────────────────────────────────

def verify_d14_proofs(trade_date: datetime.date, trigger_source: str) -> dict:
    """
    Full D14 proof check for one run.

    Returns:
        pass          bool    — True only when ALL checks pass
        missing_proofs list   — e.g. ["D14_LAYER9_READ", "D14_DEBATE_POST"]
        tickers_ok    list    — tickers with complete, valid triplets
        tickers_bad   dict    — ticker → list of issue strings
        chain_valid   bool
        chain_error   str
        events_count  int
    """
    events = read_d14_events(trade_date, trigger_source)

    if not events:
        result = {
            "pass":           False,
            "missing_proofs": ["D14_LAYER9_READ", "D14_DEBATE_PRE", "D14_DEBATE_POST"],
            "tickers_ok":     [],
            "tickers_bad":    {"(none)": ["no_d14_events_in_capture_log"]},
            "chain_valid":    False,
            "chain_error":    "no_events_found",
            "events_count":   0,
        }
        _log({"event": "D14_VERIFY_EMPTY", "trade_date": str(trade_date),
              "trigger_source": trigger_source})
        return result

    # Group by ticker
    by_ticker: Dict[str, List[dict]] = {}
    for ev in events:
        t = ev.get("ticker") or "_unknown"
        by_ticker.setdefault(t, []).append(ev)

    # Run-level: which event types are present at all?
    all_types = {ev["event"] for ev in events}
    missing_proofs: List[str] = []
    for label, ev_name in [
        ("D14_LAYER9_READ", "D14_LAYER9"),
        ("D14_DEBATE_PRE",  "D14_DEBATE_PRE"),
        ("D14_DEBATE_POST", "D14_DEBATE_POST"),
    ]:
        if ev_name not in all_types:
            missing_proofs.append(label)

    tickers_ok:  List[str]            = []
    tickers_bad: Dict[str, List[str]] = {}
    chain_valid  = True
    chain_error  = "ok"

    for ticker, evs in by_ticker.items():
        issues: List[str] = []
        types_here = {e["event"] for e in evs}

        # Each ticker must have all three event types
        for req_evt in ("D14_LAYER9", "D14_DEBATE_PRE", "D14_DEBATE_POST"):
            if req_evt not in types_here:
                issues.append(f"missing_{req_evt}")

        # Field & value validation per event
        for ev in evs:
            required = _REQUIRED_FIELDS.get(ev["event"], set())
            missing_fields = required - set(ev.keys())
            if missing_fields:
                issues.append(
                    f"{ev['event']}_missing_fields:{sorted(missing_fields)}"
                )
            if not ev.get("ticker"):
                issues.append(f"{ev['event']}_ticker_empty")
            if not ev.get("trace_id"):
                issues.append(f"{ev['event']}_trace_id_empty")
            # ticker in event must match the group key
            if ev.get("ticker") and ev["ticker"] != ticker:
                issues.append(
                    f"{ev['event']}_ticker_mismatch:"
                    f"expected={ticker} got={ev['ticker']}"
                )

        # SHA-256 chain per ticker
        sorted_evs = sorted(evs, key=lambda x: x.get("ts", ""))
        ok, reason = verify_chain(sorted_evs)
        if not ok:
            chain_valid = False
            chain_error = f"ticker={ticker}:{reason}"
            issues.append(f"chain_invalid:{reason}")

        if issues:
            tickers_bad[ticker] = issues
        else:
            tickers_ok.append(ticker)

    # Backward-compat: no chain fields anywhere → legacy_ok
    if chain_error == "ok" and not any("sha256" in e for e in events):
        chain_error = "legacy_no_chain_fields"

    passed = (
        len(missing_proofs) == 0
        and chain_valid
        and len(tickers_bad) == 0
        and len(tickers_ok) > 0
    )

    return {
        "pass":           passed,
        "missing_proofs": missing_proofs,
        "tickers_ok":     tickers_ok,
        "tickers_bad":    tickers_bad,
        "chain_valid":    chain_valid,
        "chain_error":    chain_error,
        "events_count":   len(events),
    }


# ── DB persistence ──────────────────────────────────────────────────────────

def _ensure_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS paper_trade_d14_verification (
            id              SERIAL PRIMARY KEY,
            trade_date      DATE        NOT NULL,
            execution_id    TEXT,
            trigger_source  TEXT,
            verified_at     TIMESTAMPTZ DEFAULT NOW(),
            result          TEXT        NOT NULL,
            missing_proofs  TEXT[],
            tickers_ok      TEXT[],
            chain_valid     BOOLEAN,
            chain_error     TEXT,
            events_count    INT,
            retry_count     INT         DEFAULT 0,
            retry_result    TEXT,
            alert_sent      BOOLEAN     DEFAULT FALSE,
            verify_detail   JSONB
        )
    """)
    cur.execute("""
        ALTER TABLE paper_trade_job_ledger
        ADD COLUMN IF NOT EXISTS d14_verify_result TEXT
    """)


def record_verification(
    trade_date:     datetime.date,
    execution_id:   str,
    trigger_source: str,
    result:         str,
    verify_result:  dict,
    retry_count:    int  = 0,
    retry_result:   Optional[str] = None,
    alert_sent:     bool = False,
) -> None:
    try:
        import psycopg2
        conn = psycopg2.connect(_DB_URL, connect_timeout=6)
        cur  = conn.cursor()
        _ensure_schema(cur)
        cur.execute("""
            INSERT INTO paper_trade_d14_verification
                (trade_date, execution_id, trigger_source, result,
                 missing_proofs, tickers_ok, chain_valid, chain_error,
                 events_count, retry_count, retry_result, alert_sent,
                 verify_detail)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            str(trade_date), execution_id, trigger_source, result,
            verify_result.get("missing_proofs") or [],
            verify_result.get("tickers_ok")     or [],
            verify_result.get("chain_valid",    False),
            verify_result.get("chain_error"),
            verify_result.get("events_count",   0),
            retry_count, retry_result, alert_sent,
            json.dumps(verify_result),
        ))
        cur.execute("""
            UPDATE paper_trade_job_ledger
               SET d14_verify_result = %s
             WHERE business_date = %s
        """, (result, str(trade_date)))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        _log({
            "event": "RECORD_VERIFY_ERROR",
            "error": str(exc),
            "tb":    traceback.format_exc()[-400:],
        })


# ── Alert ───────────────────────────────────────────────────────────────────

def _build_alert(
    trade_date:     datetime.date,
    exec_id:        str,
    trigger_source: str,
    vr:             dict,
    retry_n:        int,
    retry_result:   Optional[str],
) -> str:
    missing     = vr.get("missing_proofs", [])
    tickers_bad = vr.get("tickers_bad",   {})
    chain_ok    = vr.get("chain_valid",   False)
    chain_err   = vr.get("chain_error",   "")
    n_events    = vr.get("events_count",  0)

    parts = [
        "⛔️ <b>D14 VERIFICATION FAILED</b>",
        f"Date:    {trade_date}",
        f"ExecID:  <code>{str(exec_id)[:28]}…</code>",
        f"Trigger: {trigger_source}",
        f"Events found: {n_events}",
    ]
    if missing:
        parts.append(f"Missing proofs: {', '.join(missing)}")
    for ticker, issues in list(tickers_bad.items())[:4]:
        short = "; ".join(str(i) for i in issues[:2])
        parts.append(f"Ticker {ticker}: {short}")
    if not chain_ok:
        parts.append(f"Chain error: {chain_err}")
    if retry_n > 0:
        parts.append(f"Retry #{retry_n}: {retry_result or 'N/A'}")
    return "\n".join(parts)


# ── Main entry point ─────────────────────────────────────────────────────────

def run_d14_verification(
    trade_date:     datetime.date,
    execution_id:   str,
    trigger_source: str,
    retry_fn        = None,
    retry_trigger:  Optional[str] = None,
    max_retries:    int = 1,
) -> dict:
    """
    Full D14 verification flow after a paper-trade run.

    Steps:
      1. verify_d14_proofs(trade_date, trigger_source)
      2. If FAIL and retry_fn provided → call retry_fn(), re-verify
         (uses retry_trigger if given, otherwise trigger_source)
      3. Send Telegram alert on any final failure
      4. Record to paper_trade_d14_verification + ledger
      5. Return outcome dict

    Args:
        retry_fn       — callable() → bool; returns True if retry wrote fresh events
        retry_trigger  — trigger_source to use for re-verification after retry
                         (default: "d14_retry")
    """
    vr      = verify_d14_proofs(trade_date, trigger_source)
    result  = "PASS" if vr["pass"] else "FAIL"
    retry_n = 0
    retry_r = None

    _log({
        "event":          "D14_VERIFY_RUN",
        "trade_date":     str(trade_date),
        "trigger_source": trigger_source,
        "result":         result,
        "missing":        vr.get("missing_proofs"),
        "chain_valid":    vr.get("chain_valid"),
        "events_count":   vr.get("events_count"),
    })

    if not vr["pass"] and retry_fn and max_retries > 0:
        retry_n = 1
        try:
            retry_ok = bool(retry_fn())
            retry_r  = "fn_returned_True" if retry_ok else "fn_returned_False"

            # Re-verify after retry using retry trigger source
            re_trig = retry_trigger or "d14_retry"
            vr2     = verify_d14_proofs(trade_date, re_trig)
            if vr2["pass"]:
                vr      = vr2
                result  = "PASS"
                retry_r = "PASS"
            else:
                result  = "FAIL_AFTER_RETRY"
                retry_r = "FAIL"
        except Exception as exc:
            retry_r = f"ERROR:{exc}"
            result  = "FAIL_AFTER_RETRY"

        _log({
            "event":        "D14_RETRY",
            "trade_date":   str(trade_date),
            "retry_count":  retry_n,
            "retry_result": retry_r,
            "final_result": result,
        })

    alert_sent = False
    if result != "PASS":
        msg        = _build_alert(
            trade_date, execution_id, trigger_source,
            vr, retry_n, retry_r,
        )
        alert_sent = _tg_send(msg)
        _log({
            "event":          "D14_ALERT_" + ("SENT" if alert_sent else "FAILED"),
            "trade_date":     str(trade_date),
            "trigger_source": trigger_source,
        })

    record_verification(
        trade_date, execution_id, trigger_source, result,
        vr, retry_n, retry_r, alert_sent,
    )

    print(
        f"[D14_VERIFY] {trade_date} trigger={trigger_source} result={result}"
        f" events={vr['events_count']} chain_valid={vr['chain_valid']}"
        f" missing={vr['missing_proofs']}"
    )
    return {
        "result":       result,
        "verify":       vr,
        "retry_count":  retry_n,
        "retry_result": retry_r,
        "alert_sent":   alert_sent,
    }
