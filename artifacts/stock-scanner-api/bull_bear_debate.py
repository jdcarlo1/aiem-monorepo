"""
bull_bear_debate.py — Deterministic Bull/Bear Debate Engine
Replaces GPT+Claude calls with rules-based scoring from signal context.
No external AI calls. Zero cost per debate.
"""
import json, psycopg2, os
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

def init_schema():
    """Create bull_bear_debates table if it does not exist."""
    try:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return
        with psycopg2.connect(url) as c, c.cursor() as cu:
            cu.execute("""
                CREATE TABLE IF NOT EXISTS bull_bear_debates (
                    id          BIGSERIAL PRIMARY KEY,
                    ticker      TEXT NOT NULL,
                    debate_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    bull_score  REAL,
                    bear_score  REAL,
                    verdict     TEXT,
                    confidence  REAL,
                    context_json JSONB,
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            c.commit()
    except Exception:
        pass


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


def run_bull_bear_debate(ticker: str, signal_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full pipeline: build bull + bear case, synthesize verdict.
    Deterministic — no external AI calls, zero cost.
    """
    bull = build_bull_case(ticker, signal_context)
    bear = build_bear_case(ticker, signal_context)
    synth = synthesize_debate(ticker, bull, bear, signal_context)
    return {
        "ticker": ticker,
        "bull_case": bull,
        "bear_case": bear,
        "synthesis": synth,
        "verdict": synth["verdict"],
        "confidence": synth["confidence"],
        "method": "deterministic_rules",
    }
