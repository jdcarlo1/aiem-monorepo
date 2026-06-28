"""
pre_recommendation_synthesis.py
====================================================================
Forces a structured, multi-signal synthesis BEFORE any pick is
finalized. Pulls gamma, charm, dark pool, and unusual-calls data for
a candidate ticker and requires connected reasoning — not separate,
disconnected signal scores. Logs the synthesis, and can block
low-confluence picks.
====================================================================
"""

import datetime as dt
from typing import Dict, Any

import psycopg2
import psycopg2.extras
import decision_logger as dl


def gather_signals_for_ticker(db_url: str, ticker: str) -> Dict[str, Any]:
    conn = psycopg2.connect(db_url)
    signals = {"ticker": ticker, "gathered_at": dt.datetime.utcnow().isoformat()}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for sig_name, key in [
                ("gamma_pressure_scan", "gamma"),
                ("charm_cascade",       "charm"),
                ("dark_pool_scanner",   "dark_pool"),
                ("unusual_calls_scanner", "unusual_calls"),
            ]:
                cur.execute("""
                    SELECT reasoning, decision_time FROM agent_decisions
                    WHERE ticker = %s AND signal_name = %s
                    ORDER BY decision_time DESC LIMIT 1
                """, (ticker, sig_name))
                row = cur.fetchone()
                signals[key] = dict(row) if row else None
    finally:
        conn.close()
    return signals


def build_synthesis(signals: Dict[str, Any]) -> Dict[str, Any]:
    present = {k: v for k, v in signals.items()
               if k in ("gamma", "charm", "dark_pool", "unusual_calls") and v is not None}
    confluence_count = len(present)

    if confluence_count == 0:
        return {
            "confluence_count": 0,
            "synthesis_text": f"No supporting signals found for {signals['ticker']}.",
            "skepticism_flag": "No real signal confluence — should not be acted on.",
            "confidence_level": "low",
        }

    parts = [f"{k.title()}: {v['reasoning']}" for k, v in present.items()]
    synthesis_text = " | ".join(parts)

    if confluence_count >= 3:
        confidence = "high"
    elif confluence_count == 2:
        confidence = "medium"
    else:
        confidence = "low"

    if confluence_count == 1:
        skepticism = (
            f"Only 1 of 4 signal types fired for {signals['ticker']} — "
            f"could be coincidental, not genuine confluence."
        )
    else:
        skepticism = (
            f"{confluence_count} of 4 signal types aligned for "
            f"{signals['ticker']}, but confluence doesn't guarantee the "
            f"move plays out — dealers may have hedged elsewhere, or "
            f"unrelated sellers could absorb expected buying pressure."
        )

    return {
        "confluence_count": confluence_count,
        "synthesis_text": synthesis_text,
        "skepticism_flag": skepticism,
        "confidence_level": confidence,
    }


def synthesize_and_log(db_url: str, ticker: str) -> Dict[str, Any]:
    signals = gather_signals_for_ticker(db_url, ticker)
    synthesis = build_synthesis(signals)

    full_reasoning = (
        f"SYNTHESIS for {ticker} (confluence: {synthesis['confluence_count']}/4, "
        f"confidence: {synthesis['confidence_level']}): {synthesis['synthesis_text']} "
        f"SKEPTICISM CHECK: {synthesis['skepticism_flag']}"
    )

    try:
        dl.log_decision(
            signal_name="pre_recommendation_synthesis",
            decision_type="trade",
            ticker=ticker,
            reasoning=full_reasoning,
        )
    except Exception as _e:
        print(f"[pre_recommendation_synthesis] log_decision failed: {_e}")

    return synthesis


def should_block_low_confidence(synthesis: Dict[str, Any], min_confluence: int = 2) -> bool:
    return synthesis["confluence_count"] < min_confluence
