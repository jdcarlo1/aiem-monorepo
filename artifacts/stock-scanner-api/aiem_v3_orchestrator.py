"""
AIEM v3 — Phase 5: Decision Intelligence Engine
Strategy Council + Evidence Arbitration + Master Decision Orchestrator.
Combines macro + technical + discovery into a single explainable decision.
Stores to aiem_decision_history.
"""

import os
import json
from datetime import date
from typing import List, Dict, Optional

_DB_URL = os.environ.get("DATABASE_URL", "")

DECISION_BUY        = "BUY"
DECISION_SMALL_BUY  = "SMALL_BUY"
DECISION_WATCH      = "WATCH"
DECISION_WAIT       = "WAIT"
DECISION_REDUCE     = "REDUCE_EXPOSURE"
DECISION_REJECT     = "REJECT"
DECISION_EXIT       = "EXIT"


def _sf(v, d=0.0) -> float:
    try:
        return float(v) if v is not None else d
    except Exception:
        return d


# ── Strategy Council specialists ───────────────────────────────────────────────

def _trend_specialist(disc: Dict, tech: Dict, macro: Dict) -> Dict:
    trend  = _sf(tech.get("trend_score", 50))
    above20 = tech.get("above_sma20", False)
    above50 = tech.get("above_sma50", False)
    mom5    = _sf(tech.get("momentum_5d"))
    mom20   = _sf(tech.get("momentum_20d"))

    vote  = 0.0
    notes = []
    if trend >= 70:    vote += 0.4; notes.append(f"trend={trend:.0f} strong")
    elif trend >= 55:  vote += 0.2; notes.append(f"trend={trend:.0f} ok")
    elif trend <= 35:  vote -= 0.3; notes.append(f"trend={trend:.0f} weak")

    if above20 and above50: vote += 0.2; notes.append("above SMA20+50")
    elif above20:            vote += 0.1; notes.append("above SMA20")
    elif not above20:        vote -= 0.1; notes.append("below SMA20")

    if mom5  > 3.0:  vote += 0.15; notes.append(f"5d mom={mom5:.1f}%")
    if mom20 > 8.0:  vote += 0.15; notes.append(f"20d mom={mom20:.1f}%")
    if mom5  < -5.0: vote -= 0.2;  notes.append("5d momentum negative")

    return {"name": "trend", "vote": round(max(-1.0, min(1.0, vote)), 3),
            "confidence": 0.80, "reason": "; ".join(notes) or "neutral"}


def _momentum_specialist(disc: Dict, tech: Dict, macro: Dict) -> Dict:
    rsi      = _sf(tech.get("rsi_14", 50))
    macd_h   = _sf(tech.get("macd_hist"))
    disc_sc  = _sf(disc.get("discovery_score", 50))
    rvol     = _sf(disc.get("rvol", 1.0))

    vote  = 0.0
    notes = []
    if 40 <= rsi <= 65:  vote += 0.2;  notes.append(f"RSI={rsi:.0f} healthy")
    elif rsi < 30:       vote += 0.15; notes.append(f"RSI={rsi:.0f} oversold")
    elif rsi > 75:       vote -= 0.2;  notes.append(f"RSI={rsi:.0f} overbought")

    if macd_h > 0:  vote += 0.2; notes.append("MACD hist positive")
    elif macd_h < 0: vote -= 0.1; notes.append("MACD hist negative")

    if disc_sc >= 70:  vote += 0.3; notes.append(f"disc={disc_sc:.0f}")
    elif disc_sc >= 55: vote += 0.1; notes.append(f"disc={disc_sc:.0f}")

    if rvol >= 3.0:  vote += 0.2; notes.append(f"rvol={rvol:.1f}x")
    elif rvol >= 2.0: vote += 0.1

    return {"name": "momentum", "vote": round(max(-1.0, min(1.0, vote)), 3),
            "confidence": 0.75, "reason": "; ".join(notes) or "neutral"}


def _macro_specialist(disc: Dict, tech: Dict, macro: Dict) -> Dict:
    macro_sc = _sf(macro.get("macro_score", 50))
    regime   = macro.get("regime", "UNKNOWN")
    blocked  = macro.get("block_trades", False)

    vote  = 0.0
    notes = [f"macro={macro_sc:.0f} [{regime}]"]

    if blocked:
        return {"name": "macro", "vote": -1.0, "confidence": 0.95,
                "reason": "macro block_trades=True — hard veto"}

    if   macro_sc >= 65: vote = 0.4
    elif macro_sc >= 55: vote = 0.2
    elif macro_sc >= 45: vote = 0.0
    elif macro_sc >= 35: vote = -0.2
    else:                vote = -0.4; notes.append("hostile macro")

    return {"name": "macro", "vote": round(vote, 3),
            "confidence": 0.85, "reason": "; ".join(notes)}


def _oversold_specialist(disc: Dict, tech: Dict, macro: Dict) -> Dict:
    rsi      = _sf(tech.get("rsi_14", 50))
    dtype    = disc.get("discovery_type", "")
    mom5     = _sf(tech.get("momentum_5d"))
    above20  = tech.get("above_sma20", False)

    vote  = 0.0
    notes = []

    if dtype == "OVERSOLD_BOUNCE":
        if rsi < 35 and above20:
            vote = 0.6; notes.append("oversold in uptrend — quality bounce")
        elif rsi < 40:
            vote = 0.3; notes.append("oversold bounce candidate")
        elif rsi < 50 and mom5 > 0:
            vote = 0.1; notes.append("recovering momentum")
        else:
            vote = -0.1; notes.append("oversold type but not convincing")
    else:
        if rsi < 30 and above20:
            vote = 0.3; notes.append("secondary: oversold in uptrend")
        elif rsi > 70:
            vote = -0.15; notes.append("extended — avoid chasing")

    return {"name": "oversold_bounce", "vote": round(max(-1.0, min(1.0, vote)), 3),
            "confidence": 0.70, "reason": "; ".join(notes) or "not applicable"}


def _risk_specialist(disc: Dict, tech: Dict, macro: Dict, portfolio: Dict) -> Dict:
    heat   = _sf(portfolio.get("portfolio_heat", 50))
    n_open = int(portfolio.get("open_positions", 0))
    macro_sc = _sf(macro.get("macro_score", 50))
    atr    = _sf(tech.get("atr_pct", 2.0), 2.0)

    vote  = 0.0
    notes = []

    if heat >= 80:   vote -= 0.5; notes.append(f"portfolio heat={heat:.0f} very high")
    elif heat >= 65: vote -= 0.2; notes.append(f"heat={heat:.0f} elevated")
    else:            vote += 0.1; notes.append(f"heat={heat:.0f} ok")

    if n_open >= 8:  vote -= 0.3; notes.append(f"{n_open} open positions")
    elif n_open >= 5: vote -= 0.1; notes.append(f"{n_open} open")
    else:             vote += 0.1

    if atr > 8.0:  vote -= 0.2; notes.append(f"ATR={atr:.1f}% very volatile")
    elif atr < 1.0: vote -= 0.1; notes.append("ATR very low")

    if macro_sc < 40: vote -= 0.2; notes.append("macro headwind amplifies risk")

    return {"name": "risk", "vote": round(max(-1.0, min(1.0, vote)), 3),
            "confidence": 0.85, "reason": "; ".join(notes) or "normal risk"}


def _leadership_specialist(disc: Dict, tech: Dict, macro: Dict) -> Dict:
    dtype    = disc.get("discovery_type", "")
    above50  = tech.get("above_sma50", False)
    mom20    = _sf(tech.get("momentum_20d"))

    vote  = 0.0
    notes = []

    if dtype in ("MOMENTUM_LEADER", "TREND_LEADER", "RELATIVE_STRENGTH"):
        vote += 0.3; notes.append(f"leadership type={dtype}")

    if above50 and mom20 > 10:
        vote += 0.3; notes.append(f"20d mom={mom20:.1f}% — sustained leader")
    elif above50:
        vote += 0.1; notes.append("above 50-period MA")
    else:
        vote -= 0.1; notes.append("below 50-period — possible laggard")

    return {"name": "leadership", "vote": round(max(-1.0, min(1.0, vote)), 3),
            "confidence": 0.70, "reason": "; ".join(notes) or "neutral"}


# ── Evidence Arbitration ───────────────────────────────────────────────────────

_REGIME_WEIGHTS = {
    "BULL_STRONG":   {"trend": 0.30, "momentum": 0.25, "macro": 0.15,
                      "oversold_bounce": 0.10, "risk": 0.15, "leadership": 0.05},
    "BULL":          {"trend": 0.25, "momentum": 0.20, "macro": 0.20,
                      "oversold_bounce": 0.10, "risk": 0.15, "leadership": 0.10},
    "CORRECTION":    {"trend": 0.20, "momentum": 0.15, "macro": 0.30,
                      "oversold_bounce": 0.15, "risk": 0.15, "leadership": 0.05},
    "BEAR_SEVERE":   {"trend": 0.10, "momentum": 0.10, "macro": 0.40,
                      "oversold_bounce": 0.10, "risk": 0.25, "leadership": 0.05},
    "VOLATILE":      {"trend": 0.15, "momentum": 0.15, "macro": 0.25,
                      "oversold_bounce": 0.10, "risk": 0.30, "leadership": 0.05},
}
_DEFAULT_WEIGHTS = {"trend": 0.25, "momentum": 0.20, "macro": 0.20,
                    "oversold_bounce": 0.10, "risk": 0.15, "leadership": 0.10}


def arbitrate_evidence(opinions: List[Dict], regime: str) -> Dict:
    weights = _REGIME_WEIGHTS.get(regime, _DEFAULT_WEIGHTS)
    total_w = 0.0
    weighted_vote = 0.0
    for op in opinions:
        w = weights.get(op["name"], 0.10)
        weighted_vote += op["vote"] * op["confidence"] * w
        total_w += w
    if total_w > 0:
        weighted_vote /= total_w
    return {"weighted_vote": round(weighted_vote, 4), "regime": regime}


# ── Position sizing ────────────────────────────────────────────────────────────

def compute_position_size(confidence: float, macro: Dict, tech: Dict, portfolio: Dict) -> float:
    """Return position size as % of standard allocation (0.0 – 1.25)."""
    base = confidence / 100.0
    macro_mod = _sf(macro.get("position_size_modifier", 1.0), 1.0)
    heat      = _sf(portfolio.get("portfolio_heat", 50))

    size = base * macro_mod
    if heat >= 80: size *= 0.50
    elif heat >= 65: size *= 0.75

    atr = _sf(tech.get("atr_pct", 2.0), 2.0)
    if atr > 6.0: size *= 0.70
    elif atr > 4.0: size *= 0.85

    return round(max(0.0, min(1.25, size)), 3)


# ── Master decision ────────────────────────────────────────────────────────────

def make_decision(disc: Dict, tech: Dict, macro: Dict, portfolio: Dict) -> Dict:
    """
    Full decision for one candidate.
    Returns dict with decision, confidence, position_size_pct, explanation.
    """
    regime  = macro.get("regime", "UNKNOWN")
    blocked = macro.get("block_trades", False)

    if blocked:
        return {
            "ticker": disc["ticker"], "decision": DECISION_REJECT,
            "confidence": 0, "position_size_pct": 0.0,
            "block_reason": "macro_block",
            "explanation": {"summary": "Macro engine blocked all trades."},
        }

    opinions = [
        _trend_specialist(disc, tech, macro),
        _momentum_specialist(disc, tech, macro),
        _macro_specialist(disc, tech, macro),
        _oversold_specialist(disc, tech, macro),
        _risk_specialist(disc, tech, macro, portfolio),
        _leadership_specialist(disc, tech, macro),
    ]

    arb      = arbitrate_evidence(opinions, regime)
    weighted = arb["weighted_vote"]  # -1.0 to +1.0

    # Confidence 0-100
    raw_conf = 50.0 + weighted * 50.0
    conf     = max(0.0, min(100.0, raw_conf))

    # Decision
    if   weighted >= 0.45:  decision = DECISION_BUY
    elif weighted >= 0.20:  decision = DECISION_SMALL_BUY
    elif weighted >= 0.05:  decision = DECISION_WATCH
    elif weighted >= -0.10: decision = DECISION_WAIT
    elif weighted >= -0.30: decision = DECISION_REDUCE
    else:                   decision = DECISION_REJECT

    # Hard gates
    disc_sc = _sf(disc.get("discovery_score", 0))
    if disc_sc < 25 and decision in (DECISION_BUY, DECISION_SMALL_BUY):
        decision = DECISION_WATCH

    pos_size = compute_position_size(conf, macro, tech, portfolio)
    if decision not in (DECISION_BUY, DECISION_SMALL_BUY):
        pos_size = 0.0

    explanation = {
        "summary": f"{decision} — weighted_vote={weighted:.3f} conf={conf:.0f}%",
        "macro_summary":     _macro_specialist(disc, tech, macro)["reason"],
        "technical_summary": _trend_specialist(disc, tech, macro)["reason"],
        "discovery_reason":  disc.get("detail", ""),
        "primary_risks":     [op["reason"] for op in opinions if op["vote"] < -0.1],
        "council":           [{"name": op["name"], "vote": op["vote"], "reason": op["reason"]}
                              for op in opinions],
        "arbitration":       arb,
    }

    return {
        "ticker":           disc["ticker"],
        "decision":         decision,
        "confidence":       round(conf, 1),
        "position_size_pct":pos_size,
        "block_reason":     None,
        "explanation":      explanation,
        "macro_score":      _sf(macro.get("macro_score")),
        "trend_score":      _sf(tech.get("trend_score")),
        "technical_score":  _sf(tech.get("technical_score")),
        "risk_score":       50.0,  # placeholder; Phase 6 updates this
    }


# ── Storage ────────────────────────────────────────────────────────────────────

def store_decisions(db_url: str, decisions: List[Dict], decision_date) -> int:
    import psycopg2
    if not decisions:
        return 0
    written = 0
    try:
        with psycopg2.connect(db_url, connect_timeout=8) as conn, conn.cursor() as cur:
            for d in decisions:
                cur.execute("""
                    INSERT INTO aiem_decision_history
                        (decision_date, ticker, decision, macro_score,
                         trend_score, technical_score, risk_score,
                         final_confidence, position_size_pct,
                         block_reason, decision_payload, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                """, (
                    decision_date, d["ticker"], d["decision"],
                    d.get("macro_score"), d.get("trend_score"),
                    d.get("technical_score"), d.get("risk_score"),
                    d["confidence"], d["position_size_pct"],
                    d.get("block_reason"),
                    json.dumps(d.get("explanation", {})),
                ))
                written += 1
            conn.commit()
    except Exception as e:
        print(f"[v3_orchestrator] store error: {e}")
    return written


# ── Main entry ─────────────────────────────────────────────────────────────────

def run_orchestrator(
    discoveries: List[Dict],
    tech_scores: Dict[str, Dict],
    macro: Dict,
    portfolio: Dict = None,
    db_url: str = None,
) -> List[Dict]:
    """
    Run full decision pipeline for all discovery candidates.
    Returns list of decision dicts, sorted by confidence desc.
    """
    db_url    = db_url or _DB_URL
    portfolio = portfolio or {"portfolio_heat": 50, "open_positions": 0}

    print(f"[v3_orchestrator] evaluating {len(discoveries)} candidates, "
          f"macro={macro.get('macro_score','?')} [{macro.get('regime','?')}]")

    decisions = []
    for disc in discoveries:
        ticker = disc["ticker"]
        tech   = tech_scores.get(ticker, {})
        dec    = make_decision(disc, tech, macro, portfolio)
        decisions.append(dec)

    decisions.sort(key=lambda x: x["confidence"], reverse=True)

    today   = date.today()
    written = store_decisions(db_url, decisions, today)

    buys = [d for d in decisions if d["decision"] in (DECISION_BUY, DECISION_SMALL_BUY)]
    print(f"[v3_orchestrator] {len(decisions)} evaluated → {len(buys)} BUY/SMALL_BUY, "
          f"{written} stored")
    return decisions
