"""
bull_bear_debate.py — Deterministic Bull/Bear Debate Engine
Replaces GPT+Claude calls with rules-based scoring from signal context.
No external AI calls. Zero cost per debate.

REMEDIATION A4 (Jul 2026): Added run_risk_review() and run_contradiction_check()
as independently callable functions. run_bull_bear_debate() now calls both and
includes their results as separate top-level keys in the returned dict.
These are logged as individual rows via log_stage15_subchecks() in the
Stage 15 Diagram 2 wiring.
"""
import json, psycopg2, os
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

def init_schema():
    """
    Create bull_bear_debates table if it does not exist (matches the live
    production schema — debate_time/signal_context/bull_argument/
    bear_argument/synthesis/verdict), and ensure the Diagram-2 traceability
    columns (trace_id, paper_trade_id) exist so every persisted debate can be
    joined back to its paper trade (aiem_paper_trades.id) and its audit trail
    (aiem_pipeline_audit_log.trace_id).
    """
    try:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return
        with psycopg2.connect(url) as c, c.cursor() as cu:
            cu.execute("""
                CREATE TABLE IF NOT EXISTS bull_bear_debates (
                    id             BIGSERIAL PRIMARY KEY,
                    debate_time    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ticker         TEXT NOT NULL,
                    signal_context JSONB NOT NULL,
                    bull_argument  TEXT,
                    bear_argument  TEXT,
                    synthesis      JSONB,
                    verdict        TEXT
                )
            """)
            cu.execute("CREATE INDEX IF NOT EXISTS idx_bull_bear_debates_ticker ON bull_bear_debates (ticker)")
            cu.execute("ALTER TABLE bull_bear_debates ADD COLUMN IF NOT EXISTS trace_id TEXT")
            cu.execute("ALTER TABLE bull_bear_debates ADD COLUMN IF NOT EXISTS paper_trade_id BIGINT")
            cu.execute("CREATE INDEX IF NOT EXISTS idx_bull_bear_debates_trace_id ON bull_bear_debates (trace_id)")
            cu.execute("CREATE INDEX IF NOT EXISTS idx_bull_bear_debates_paper_trade_id ON bull_bear_debates (paper_trade_id)")
            c.commit()
    except Exception:
        pass


def persist_debate(ticker: str, signal_context: Dict[str, Any], debate: Dict[str, Any],
                    trace_id: Optional[str] = None, paper_trade_id: Optional[int] = None) -> Optional[int]:
    """
    Persist one completed bull/bear debate result, linked to its paper-trade
    candidate (paper_trade_id) and its audit trace (trace_id — joins to
    aiem_pipeline_audit_log.trace_id). Returns the new row id, or None if the
    write failed (never raises — callers must not have the paper-trade path
    interrupted by a persistence issue).
    """
    try:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return None
        with psycopg2.connect(url) as c, c.cursor() as cu:
            cu.execute("""
                INSERT INTO bull_bear_debates
                    (ticker, signal_context, bull_argument, bear_argument,
                     synthesis, verdict, trace_id, paper_trade_id,
                     candidate_id, audit_log_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                ticker,
                json.dumps(signal_context),
                json.dumps(debate.get("bull_case") or {}),
                json.dumps(debate.get("bear_case") or {}),
                json.dumps(debate.get("synthesis") or {}),
                debate.get("verdict"),
                trace_id,
                paper_trade_id,
                paper_trade_id,   # candidate_id mirrors paper_trade_id
                trace_id,         # audit_log_id mirrors trace_id (joins aiem_pipeline_audit_log)
            ))
            row = cu.fetchone()
            c.commit()
            return row[0] if row else None
    except Exception:
        return None


def build_bull_case(ticker: str, signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic bull case: score based on momentum, volume, and technical signals.
    Returns thesis dict with score 0-1.
    """
    score = 0.0
    points = []

    rvol = float(signal_context.get("rvol") or 0)
    cs   = float(signal_context.get("close_strength") or 0)
    gap  = float(signal_context.get("gap_pct") or 0)
    rsi  = float(signal_context.get("rsi_14") or 50)
    cmf  = float(signal_context.get("cmf_20") or 0)
    conv = float(signal_context.get("conviction_score") or 0)
    sweep_voi = float(signal_context.get("sweep_vol_oi") or 0)

    if rvol >= 3.0:
        score += 0.25; points.append(f"RVOL {rvol:.1f}x — institutional-level volume")
    elif rvol >= 1.5:
        score += 0.10; points.append(f"RVOL {rvol:.1f}x — elevated volume")

    if cs >= 0.75:
        score += 0.25; points.append(f"Close strength {cs:.2f} — buyers in control at close")
    elif cs >= 0.55:
        score += 0.10; points.append(f"Close strength {cs:.2f} — mild buying pressure")

    if gap >= 2.0:
        score += 0.15; points.append(f"Gap {gap:.1f}% — catalyst-driven opening")

    if rsi < 35:
        score += 0.15; points.append(f"RSI {rsi:.0f} — oversold, reversion upside")
    elif rsi < 60:
        score += 0.05; points.append(f"RSI {rsi:.0f} — room to run")

    if cmf > 0.1:
        score += 0.10; points.append(f"CMF {cmf:.2f} — money flowing in")

    if sweep_voi >= 3.0:
        score += 0.10; points.append(f"Sweep VOI {sweep_voi:.1f}x — smart money options activity")

    if conv >= 6:
        score += 0.10; points.append(f"Conviction {conv:.0f}/10 — multi-layer confirmation")

    score = min(1.0, score)
    strongest = points[0] if points else "No strong bull signals identified"
    return {
        "thesis": "BULL: " + " | ".join(points) if points else "No significant bull case.",
        "strongest_point": strongest,
        "catalyst": gap > 2.0 and f"Gap-up {gap:.1f}%" or None,
        "score": round(score, 3),
        "method": "deterministic_rules",
    }


def build_bear_case(ticker: str, signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic bear case: score based on overextension, weakness, and risk signals.
    """
    score = 0.0
    points = []

    rsi  = float(signal_context.get("rsi_14") or 50)
    cs   = float(signal_context.get("close_strength") or 0.5)
    gap  = float(signal_context.get("gap_pct") or 0)
    cmf  = float(signal_context.get("cmf_20") or 0)
    rvol = float(signal_context.get("rvol") or 1)
    days_held = int(signal_context.get("days_held") or 0)

    if rsi > 72:
        score += 0.30; points.append(f"RSI {rsi:.0f} — overbought, reversal risk")
    elif rsi > 65:
        score += 0.10; points.append(f"RSI {rsi:.0f} — elevated, watch for fade")

    if cs < 0.30:
        score += 0.25; points.append(f"Close strength {cs:.2f} — sellers dominated close")
    elif cs < 0.45:
        score += 0.10; points.append(f"Close strength {cs:.2f} — weak close")

    if gap > 15:
        score += 0.20; points.append(f"Gap {gap:.1f}% — overextended, gap-fill risk")

    if cmf < -0.1:
        score += 0.15; points.append(f"CMF {cmf:.2f} — money flowing out")

    if days_held >= 5:
        score += 0.10; points.append(f"Held {days_held}d — position aging, time-decay risk")

    score = min(1.0, score)
    strongest = points[0] if points else "No strong bear signals identified"
    return {
        "thesis": "BEAR: " + " | ".join(points) if points else "No significant bear case.",
        "strongest_point": strongest,
        "risk": points[0] if points else None,
        "score": round(score, 3),
        "method": "deterministic_rules",
    }


def synthesize_debate(ticker: str, bull_case: Dict[str, Any], bear_case: Dict[str, Any],
                      signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic synthesis: compare bull vs bear scores, return verdict and confidence.
    """
    bull_s = float(bull_case.get("score") or 0)
    bear_s = float(bear_case.get("score") or 0)
    net    = bull_s - bear_s

    if net >= 0.30:
        verdict = "BUY"
        conf    = round(min(0.95, 0.60 + net), 2)
        summary = f"Bull case dominates ({bull_s:.2f} vs {bear_s:.2f}). {bull_case.get('strongest_point','')}"
    elif net <= -0.30:
        verdict = "AVOID"
        conf    = round(min(0.95, 0.60 + abs(net)), 2)
        summary = f"Bear case dominates ({bear_s:.2f} vs {bull_s:.2f}). {bear_case.get('strongest_point','')}"
    elif net >= 0.10:
        verdict = "LEAN_BUY"
        conf    = round(0.45 + net, 2)
        summary = f"Slight bull edge ({bull_s:.2f} vs {bear_s:.2f})."
    elif net <= -0.10:
        verdict = "LEAN_AVOID"
        conf    = round(0.45 + abs(net), 2)
        summary = f"Slight bear edge ({bear_s:.2f} vs {bull_s:.2f})."
    else:
        verdict = "NEUTRAL"
        conf    = 0.40
        summary = f"No clear edge (bull={bull_s:.2f}, bear={bear_s:.2f}). Hold or skip."

    return {
        "verdict": verdict,
        "confidence": conf,
        "summary": summary,
        "bull_score": bull_s,
        "bear_score": bear_s,
        "net_edge": round(net, 3),
        "method": "deterministic_rules",
    }


def run_risk_review(
    ticker: str,
    bull_case: Dict[str, Any],
    bear_case: Dict[str, Any],
    signal_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    REMEDIATION A4 — Independent risk assessment step.

    Evaluates asymmetric downside exposure for this pick:
      - Bear score above risk threshold (0.35 / 0.50)
      - RSI overbought (> 72)
      - Extreme gap (> 15%) creating gap-fill risk
      - Thin conviction margin (bull–bear edge < 0.10)

    Returns risk_level (LOW / MEDIUM / HIGH), dominant_risk, position_limit_mult,
    and all identified risk factors. This is a separate, independently-callable
    function — not a derived field from the synthesis blob.
    """
    bear_s = float(bear_case.get("score") or 0)
    bull_s = float(bull_case.get("score") or 0)
    rsi    = float(signal_context.get("rsi_14") or 50)
    gap    = float(signal_context.get("gap_pct") or 0)

    risks = []
    risk_score = 0.0

    if bear_s >= 0.50:
        risk_score += 0.40
        risks.append(f"bear_score={bear_s:.2f} exceeds HIGH threshold (0.50)")
    elif bear_s >= 0.35:
        risk_score += 0.20
        risks.append(f"bear_score={bear_s:.2f} in elevated range (0.35–0.50)")

    if rsi > 72:
        risk_score += 0.30
        risks.append(f"RSI={rsi:.0f} overbought — mean-reversion risk")

    if gap > 15.0:
        risk_score += 0.20
        risks.append(f"gap={gap:.1f}% overextended — gap-fill risk above 15%")

    if bull_s > 0 and (bull_s - bear_s) < 0.10:
        risk_score += 0.10
        risks.append(f"thin conviction margin: bull({bull_s:.2f})–bear({bear_s:.2f}) edge < 0.10")

    if risk_score >= 0.50:
        risk_level       = "HIGH"
        position_limit   = 0.50
    elif risk_score >= 0.25:
        risk_level       = "MEDIUM"
        position_limit   = 0.75
    else:
        risk_level       = "LOW"
        position_limit   = 1.00

    return {
        "risk_level": risk_level,
        "risk_score": round(risk_score, 3),
        "dominant_risk": risks[0] if risks else "no elevated risk factors",
        "all_risks": risks,
        "position_limit_mult": position_limit,
        "inputs": {
            "bull_score": bull_s,
            "bear_score": bear_s,
            "rsi_14": rsi,
            "gap_pct": gap,
        },
        "method": "deterministic_rules",
    }


def run_contradiction_check(
    ticker: str,
    bull_case: Dict[str, Any],
    bear_case: Dict[str, Any],
    signal_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    REMEDIATION A4 — Independent signal contradiction check.

    Detects cases where bull and bear cases cite conflicting indicators in
    opposite directions, which reduces the reliability of either thesis:
      - RSI overbought (bear) AND high RVOL (bull) — momentum vs. exhaustion
      - Positive CMF (bull) AND weak close strength (bear) — flow vs. price action
      - Both scores elevated (>= 0.35) — genuinely ambiguous, signal conflict

    Returns contradictions_found, full contradiction_details list, and
    confidence_adjustment (negative, applied by run_bull_bear_debate). This is
    a separate, independently-callable function — not a derived field.
    """
    rsi  = float(signal_context.get("rsi_14") or 50)
    rvol = float(signal_context.get("rvol") or 1)
    cs   = float(signal_context.get("close_strength") or 0.5)
    cmf  = float(signal_context.get("cmf_20") or 0)
    bull_s = float(bull_case.get("score") or 0)
    bear_s = float(bear_case.get("score") or 0)

    contradictions = []

    # Contradiction 1: RSI overbought conflicts with strong RVOL momentum
    if rsi > 65 and rvol >= 2.0:
        contradictions.append({
            "type": "rsi_vs_rvol",
            "description": (f"RSI={rsi:.0f} (overbought/reversal risk) conflicts with "
                            f"RVOL={rvol:.1f}x (strong momentum signal)"),
            "bull_signal": f"RVOL={rvol:.1f}x",
            "bear_signal": f"RSI={rsi:.0f}",
        })

    # Contradiction 2: positive CMF conflicts with weak close strength
    if cmf > 0.05 and cs < 0.35:
        contradictions.append({
            "type": "cmf_vs_close_strength",
            "description": (f"CMF={cmf:.2f} (money flowing in) conflicts with "
                            f"close_strength={cs:.2f} (sellers dominated close)"),
            "bull_signal": f"CMF={cmf:.2f}",
            "bear_signal": f"close_strength={cs:.2f}",
        })

    # Contradiction 3: both bull and bear scores elevated — ambiguous direction
    if bull_s >= 0.35 and bear_s >= 0.35:
        contradictions.append({
            "type": "both_high_scores",
            "description": (f"bull_score={bull_s:.2f} and bear_score={bear_s:.2f} "
                            f"both >= 0.35 — genuinely ambiguous direction"),
            "bull_signal": f"bull={bull_s:.2f}",
            "bear_signal": f"bear={bear_s:.2f}",
        })

    confidence_adjustment = round(-0.05 * len(contradictions), 3)

    return {
        "contradictions_found": len(contradictions),
        "contradiction_details": contradictions,
        "confidence_adjustment": confidence_adjustment,
        "resolved_direction": "ambiguous" if contradictions else "clean",
        "inputs": {
            "rsi_14": rsi, "rvol": rvol,
            "close_strength": cs, "cmf_20": cmf,
            "bull_score": bull_s, "bear_score": bear_s,
        },
        "method": "deterministic_rules",
    }


def run_bull_bear_debate(ticker: str, signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full pipeline: build bull + bear case, synthesize verdict, run risk review
    and contradiction check as independent steps. All four steps are deterministic
    — no external AI calls, zero cost.

    risk_review and contradiction_check are separate top-level keys so they can
    be independently logged via log_stage15_subchecks() in the Diagram 2 wiring.
    """
    bull  = build_bull_case(ticker, signal_context)
    bear  = build_bear_case(ticker, signal_context)
    synth = synthesize_debate(ticker, bull, bear, signal_context)
    risk_review       = run_risk_review(ticker, bull, bear, signal_context)
    contradiction_check = run_contradiction_check(ticker, bull, bear, signal_context)

    # Apply contradiction confidence adjustment to final confidence
    adjusted_confidence = round(
        max(0.0, min(0.95, synth["confidence"] + contradiction_check["confidence_adjustment"])),
        3,
    )

    return {
        "ticker": ticker,
        "bull_case": bull,
        "bear_case": bear,
        "synthesis": synth,
        "risk_review": risk_review,
        "contradiction_check": contradiction_check,
        "verdict": synth["verdict"],
        "confidence": adjusted_confidence,
        "method": "deterministic_rules",
    }
