"""
AIEM v3 — Phase 6: Learning Intelligence Engine
Performance auditor, trade attribution, counterfactual analysis,
discovery quality labeling, confidence calibration.
Stores to aiem_counterfactual_results and aiem_strategy_memory.
"""

import os
import json
from datetime import date, timedelta
from typing import List, Dict, Optional

_DB_URL = os.environ.get("DATABASE_URL", "")

LABEL_ELITE     = "ELITE_DISCOVERY"
LABEL_STRONG    = "STRONG_DISCOVERY"
LABEL_VALID     = "VALID_DISCOVERY"
LABEL_EARLY     = "EARLY_DISCOVERY"
LABEL_LATE      = "LATE_DISCOVERY"
LABEL_FALSE_POS = "FALSE_POSITIVE"
LABEL_POOR      = "POOR_DISCOVERY"
LABEL_MACRO_FAIL= "MACRO_FAILURE"
LABEL_TREND_FAIL= "TREND_FAILURE"


def _sf(v, d=0.0) -> float:
    try:
        return float(v) if v is not None else d
    except Exception:
        return d


# ── Completed trade auditing ───────────────────────────────────────────────────

def audit_completed_trades(db_url: str, lookback_days: int = 30) -> List[Dict]:
    """Pull completed paper trades for attribution analysis."""
    import psycopg2
    try:
        with psycopg2.connect(db_url, connect_timeout=8) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, trade_date, exit_date, entry_price, exit_price,
                       pnl_pct, signal_source, notional
                FROM   aiem_paper_trades
                WHERE  status IN ('CLOSED', 'EXPIRED')
                  AND  exit_date >= CURRENT_DATE - %s
                  AND  pnl_pct IS NOT NULL
                ORDER  BY exit_date DESC
                LIMIT  200
            """, (timedelta(days=lookback_days),))
            cols = ["id","ticker","trade_date","exit_date","entry_price",
                    "exit_price","pnl_pct","signal_source","notional"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        print(f"[v3_learning] audit_completed_trades error: {e}")
        return []


def attribute_trade(trade: Dict, macro_at_entry: Optional[Dict] = None) -> Dict:
    """
    Determine WHY a trade won or lost.
    Returns attribution dict with primary_cause, secondary_cause, lesson.
    """
    pnl       = _sf(trade.get("pnl_pct", 0))
    source    = trade.get("signal_source", "unknown")
    won       = pnl > 0
    big_win   = pnl > 5.0
    big_loss  = pnl < -5.0

    # Simplified deterministic attribution
    causes    = []
    lesson    = ""

    if big_win:
        causes.append("strong_momentum")
        lesson = f"Source '{source}' produced +{pnl:.1f}% — reinforce discovery rules"
    elif won:
        causes.append("valid_signal")
        lesson = f"Source '{source}' +{pnl:.1f}% — valid but modest"
    elif big_loss:
        causes.append("trend_failure")
        lesson = f"Source '{source}' -{abs(pnl):.1f}% — review entry criteria"
    else:
        causes.append("small_loss")
        lesson = f"Source '{source}' -{abs(pnl):.1f}% — within normal variance"

    if macro_at_entry:
        macro_sc = _sf(macro_at_entry.get("macro_score", 50))
        if macro_sc < 40 and not won:
            causes.append("macro_headwind")
            lesson += "; macro was hostile at entry"
        elif macro_sc >= 65 and won:
            causes.append("macro_tailwind")

    return {
        "trade_id":       trade["id"],
        "ticker":         trade["ticker"],
        "pnl_pct":        pnl,
        "won":            won,
        "primary_cause":  causes[0] if causes else "unknown",
        "secondary_cause":causes[1] if len(causes) > 1 else None,
        "lesson":         lesson,
        "source":         source,
    }


# ── Counterfactual analysis ────────────────────────────────────────────────────

def compute_counterfactuals(trades: List[Dict], db_url: str) -> List[Dict]:
    """
    For each completed trade, compute 'what if' scenarios.
    Returns list of counterfactual result dicts.
    """
    import psycopg2
    results = []
    if not trades:
        return results

    try:
        with psycopg2.connect(db_url, connect_timeout=8) as conn, conn.cursor() as cur:
            for trade in trades:
                ticker     = trade["ticker"]
                entry_date = trade.get("trade_date")
                entry_px   = _sf(trade.get("entry_price", 0))
                actual_pnl = _sf(trade.get("pnl_pct", 0))

                if not entry_date or entry_px <= 0:
                    continue

                # Fetch price 1 day after entry (what if we waited?)
                cur.execute("""
                    SELECT close_price, scan_date
                    FROM   polygon_market_daily
                    WHERE  ticker    = %s
                      AND  scan_date > %s
                    ORDER  BY scan_date ASC
                    LIMIT  3
                """, (ticker, entry_date))
                next_rows = cur.fetchall()
                if not next_rows:
                    continue

                # CF1: What if we entered 1 day later?
                day1_close = _sf(next_rows[0][0]) if next_rows else 0
                exit_px    = _sf(trade.get("exit_price", 0))
                cf1_pnl    = None
                if day1_close > 0 and exit_px > 0:
                    cf1_pnl = (exit_px - day1_close) / day1_close * 100.0

                lesson = ""
                if cf1_pnl is not None:
                    if cf1_pnl > actual_pnl + 2.0:
                        lesson = "Waiting 1 day for better entry would have improved return"
                    elif cf1_pnl < actual_pnl - 2.0:
                        lesson = "Early entry was correct — waiting hurt"
                    else:
                        lesson = "Entry timing had minimal impact"

                results.append({
                    "trade_id":     trade["id"],
                    "ticker":       ticker,
                    "actual_return":actual_pnl,
                    "cf_return":    round(cf1_pnl, 2) if cf1_pnl is not None else None,
                    "cf_type":      "wait_1_day",
                    "lesson":       lesson,
                })
    except Exception as e:
        print(f"[v3_learning] counterfactual error: {e}")

    return results


def store_counterfactuals(db_url: str, cfs: List[Dict]) -> int:
    import psycopg2
    if not cfs:
        return 0
    written = 0
    try:
        with psycopg2.connect(db_url, connect_timeout=8) as conn, conn.cursor() as cur:
            for cf in cfs:
                cur.execute("""
                    INSERT INTO aiem_counterfactual_results
                        (analysis_date, trade_id, ticker, actual_return,
                         cf_return, cf_type, lesson, computed_at)
                    VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT DO NOTHING
                """, (
                    cf["trade_id"], cf["ticker"],
                    cf["actual_return"], cf.get("cf_return"),
                    cf["cf_type"], cf["lesson"],
                ))
                written += 1
            conn.commit()
    except Exception as e:
        print(f"[v3_learning] store_counterfactuals error: {e}")
    return written


# ── Discovery quality labeling ─────────────────────────────────────────────────

def label_discovery_quality(
    ticker: str, discovery_confidence: float, actual_pnl: Optional[float]
) -> str:
    if actual_pnl is None:
        return LABEL_VALID  # unresolved

    if actual_pnl >= 10.0 and discovery_confidence >= 0.70:
        return LABEL_ELITE
    if actual_pnl >= 5.0:
        return LABEL_STRONG
    if actual_pnl >= 0:
        return LABEL_VALID
    if actual_pnl < -10.0:
        return LABEL_FALSE_POS if discovery_confidence >= 0.60 else LABEL_POOR
    return LABEL_TREND_FAIL


# ── Strategy memory ────────────────────────────────────────────────────────────

def update_strategy_memory(db_url: str, attributions: List[Dict]) -> None:
    """
    Update aiem_strategy_memory with aggregated lessons from this batch.
    Uses EMA-style accumulation: old_value * 0.85 + new_signal * 0.15.
    """
    import psycopg2
    if not attributions:
        return

    # Aggregate win rates per source
    source_stats: Dict[str, Dict] = {}
    for a in attributions:
        src = a.get("source", "unknown")
        if src not in source_stats:
            source_stats[src] = {"wins": 0, "total": 0, "pnl_sum": 0.0}
        source_stats[src]["total"] += 1
        if a.get("won"):
            source_stats[src]["wins"] += 1
        source_stats[src]["pnl_sum"] += _sf(a.get("pnl_pct"))

    try:
        with psycopg2.connect(db_url, connect_timeout=8) as conn, conn.cursor() as cur:
            for src, stats in source_stats.items():
                wr  = stats["wins"] / stats["total"] if stats["total"] else 0.5
                avg_pnl = stats["pnl_sum"] / stats["total"] if stats["total"] else 0.0
                key = f"source_wr_{src}"

                cur.execute("""
                    SELECT memory_value FROM aiem_strategy_memory WHERE memory_key = %s
                """, (key,))
                row = cur.fetchone()
                if row:
                    try:
                        old = json.loads(row[0])
                        new_wr  = old.get("win_rate", wr)  * 0.85 + wr  * 0.15
                        new_pnl = old.get("avg_pnl", avg_pnl) * 0.85 + avg_pnl * 0.15
                        val     = json.dumps({"win_rate": round(new_wr, 4),
                                              "avg_pnl": round(new_pnl, 4),
                                              "n": old.get("n", 0) + stats["total"]})
                    except Exception:
                        val = json.dumps({"win_rate": wr, "avg_pnl": avg_pnl,
                                          "n": stats["total"]})
                    cur.execute("""
                        UPDATE aiem_strategy_memory
                        SET memory_value=%s, version=version+1, updated_at=NOW()
                        WHERE memory_key=%s
                    """, (val, key))
                else:
                    val = json.dumps({"win_rate": wr, "avg_pnl": avg_pnl,
                                      "n": stats["total"]})
                    cur.execute("""
                        INSERT INTO aiem_strategy_memory (memory_key, memory_value, version, updated_at)
                        VALUES (%s, %s, 1, NOW())
                        ON CONFLICT (memory_key)
                        DO UPDATE SET memory_value=EXCLUDED.memory_value,
                                      version=aiem_strategy_memory.version+1,
                                      updated_at=NOW()
                    """, (key, val))
            conn.commit()
    except Exception as e:
        print(f"[v3_learning] update_strategy_memory error: {e}")


# ── Main entry ─────────────────────────────────────────────────────────────────

def run_learning_cycle(db_url: str = None, lookback_days: int = 30) -> Dict:
    """
    Full post-market learning cycle.
    Returns summary dict with attribution + counterfactual counts.
    """
    db_url = db_url or _DB_URL
    print("[v3_learning] starting learning cycle...")

    # 1. Audit completed trades
    trades = audit_completed_trades(db_url, lookback_days)
    print(f"[v3_learning] {len(trades)} completed trades to audit")
    if not trades:
        return {"attributions": 0, "counterfactuals": 0, "status": "no_trades"}

    # 2. Attribution
    attributions = [attribute_trade(t) for t in trades]

    # 3. Counterfactual analysis
    cfs     = compute_counterfactuals(trades, db_url)
    cf_n    = store_counterfactuals(db_url, cfs)

    # 4. Strategy memory update
    update_strategy_memory(db_url, attributions)

    # 5. Mark discoveries as promoted where they led to closed trades
    try:
        import psycopg2
        with psycopg2.connect(db_url, connect_timeout=5) as conn, conn.cursor() as cur:
            for t in trades:
                cur.execute("""
                    UPDATE aiem_discovery_memory
                    SET promoted_to_pick = TRUE,
                        promotion_reason = %s
                    WHERE ticker = %s
                      AND discovery_date >= %s
                      AND NOT promoted_to_pick
                """, (f"trade closed pnl={_sf(t.get('pnl_pct')):.1f}%",
                      t["ticker"],
                      t.get("trade_date") or date.today() - timedelta(days=7)))
            conn.commit()
    except Exception as e:
        print(f"[v3_learning] discovery promotion error: {e}")

    wins     = sum(1 for a in attributions if a["won"])
    avg_pnl  = sum(a["pnl_pct"] for a in attributions) / len(attributions) if attributions else 0.0
    summary  = {
        "attributions":   len(attributions),
        "counterfactuals":cf_n,
        "win_rate":       round(wins / len(attributions), 3) if attributions else 0,
        "avg_pnl_pct":    round(avg_pnl, 3),
        "status":         "ok",
    }
    print(f"[v3_learning] done — {summary}")
    return summary
