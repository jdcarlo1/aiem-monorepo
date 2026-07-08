"""
aiem_attribution.py
--------------------
Attribution module — Diagram 2 learning loop hop between Outcome Tracker
and Learning Systems.

For each resolved paper trade, records which upstream signal/module
receives credit (WIN) or blame (LOSS) for the PnL. This is the formal
intermediary that closes the Outcome → Attribution → Learning → Memory loop.

DB table: aiem_trade_attribution
"""

import os
import datetime as dt
from typing import Optional, Dict, Any

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS aiem_trade_attribution (
    id                  BIGSERIAL PRIMARY KEY,
    trade_id            BIGINT,
    ticker              TEXT          NOT NULL,
    signal_source       TEXT          NOT NULL,
    entry_price         NUMERIC(12,4),
    exit_price          NUMERIC(12,4),
    pnl_pct             NUMERIC(10,6),
    win                 BOOLEAN,
    hold_days           INTEGER,
    module_credits      JSONB,
    blame_vector        JSONB,
    confidence_at_entry NUMERIC(6,4),
    attribution_version TEXT          DEFAULT 'v1',
    trace_id            TEXT,
    attributed_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aiem_attr_trade   ON aiem_trade_attribution (trade_id);
CREATE INDEX IF NOT EXISTS idx_aiem_attr_source  ON aiem_trade_attribution (signal_source);
CREATE INDEX IF NOT EXISTS idx_aiem_attr_ticker  ON aiem_trade_attribution (ticker);
CREATE INDEX IF NOT EXISTS idx_aiem_attr_trace   ON aiem_trade_attribution (trace_id);
"""

_SCHEMA_DONE = False


def init_schema() -> None:
    global _SCHEMA_DONE
    if _SCHEMA_DONE:
        return
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=8)
    conn.autocommit = True
    cur = conn.cursor()
    for stmt in _CREATE_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.close()
    _SCHEMA_DONE = True


def _conn():
    import psycopg2
    return psycopg2.connect(DATABASE_URL, connect_timeout=8)


# ---------------------------------------------------------------------------
# Core attribution logic
# ---------------------------------------------------------------------------

def _module_credits(signal_source: str, win: bool, pnl_pct: float) -> Dict[str, Any]:
    """
    Assign credit/blame fractions to upstream modules that contributed
    to this trade. Currently uses a simple signal-source heuristic;
    will be upgraded to SHAP-based attribution when AUC >= 0.70 baseline
    is achieved on the full model.
    """
    base_credit = 1.0 if win else 0.0

    # Primary signal gets full credit/blame
    credits = {signal_source: base_credit}

    # Secondary modules that co-fired get partial credit (heuristic weights)
    # These are derived from the pipeline stage map — not hardcoded
    if "aiem_ai" in signal_source or "aiem" in signal_source.lower():
        credits["specialist_council"]  = base_credit * 0.3
        credits["risk_manager"]        = base_credit * 0.2
        credits["analysis"]            = base_credit * 0.3
    elif "gap_volume" in signal_source or "rvol" in signal_source:
        credits["analysis"]            = base_credit * 0.5
        credits["market_regime"]       = base_credit * 0.2
    else:
        credits["analysis"]            = base_credit * 0.4

    return credits


def _blame_vector(signal_source: str, win: bool, pnl_pct: float) -> Dict[str, Any]:
    """
    When a trade loses, surface which upstream signals most likely
    contributed to the error.
    """
    if win:
        return {}
    magnitude = abs(pnl_pct)
    blame = {
        signal_source: round(min(1.0, magnitude / 10.0), 4),
    }
    if magnitude > 5.0:
        blame["market_regime"] = round(min(0.5, magnitude / 20.0), 4)
    if magnitude > 10.0:
        blame["risk_manager"] = round(min(0.3, magnitude / 30.0), 4)
    return blame


def record_attribution(
    trade_id: Optional[int],
    ticker: str,
    signal_source: str,
    entry_price: Optional[float],
    exit_price: Optional[float],
    pnl_pct: float,
    hold_days: Optional[int] = None,
    confidence_at_entry: Optional[float] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record attribution for one resolved trade.
    Returns the inserted row as a dict.
    Raises on DB error (callers must handle).
    """
    import json
    win = pnl_pct > 0
    credits = _module_credits(signal_source, win, pnl_pct)
    blame   = _blame_vector(signal_source, win, pnl_pct)

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO aiem_trade_attribution
                (trade_id, ticker, signal_source, entry_price, exit_price,
                 pnl_pct, win, hold_days, module_credits, blame_vector,
                 confidence_at_entry, trace_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, attributed_at
            """,
            (
                trade_id,
                ticker,
                signal_source,
                entry_price,
                exit_price,
                round(float(pnl_pct), 6),
                win,
                hold_days,
                json.dumps(credits),
                json.dumps(blame),
                round(float(confidence_at_entry), 4) if confidence_at_entry is not None else None,
                trace_id,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return {
            "ok":             True,
            "attribution_id": row[0],
            "attributed_at":  row[1].isoformat() if row[1] else None,
            "trade_id":       trade_id,
            "ticker":         ticker,
            "signal_source":  signal_source,
            "pnl_pct":        pnl_pct,
            "win":            win,
            "module_credits": credits,
            "blame_vector":   blame,
            "trace_id":       trace_id,
        }
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_attribution_for_trade(trade_id: int) -> Optional[Dict[str, Any]]:
    """Return the attribution row for a specific trade_id, or None."""
    try:
        conn = _conn()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT id, trade_id, ticker, signal_source, pnl_pct, win,
                   hold_days, module_credits, blame_vector, trace_id, attributed_at
            FROM aiem_trade_attribution
            WHERE trade_id = %s
            ORDER BY attributed_at DESC LIMIT 1
            """,
            (trade_id,)
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id":             row[0],
            "trade_id":       row[1],
            "ticker":         row[2],
            "signal_source":  row[3],
            "pnl_pct":        float(row[4]) if row[4] is not None else None,
            "win":            row[5],
            "hold_days":      row[6],
            "module_credits": row[7],
            "blame_vector":   row[8],
            "trace_id":       row[9],
            "attributed_at":  row[10].isoformat() if row[10] else None,
        }
    except Exception:
        return None


def get_recent_attributions(limit: int = 20) -> list:
    """Return the most recent attribution rows."""
    try:
        conn = _conn()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT id, trade_id, ticker, signal_source, pnl_pct, win,
                   module_credits, blame_vector, trace_id, attributed_at
            FROM aiem_trade_attribution
            ORDER BY attributed_at DESC
            LIMIT %s
            """,
            (limit,)
        )
        rows = cur.fetchall()
        conn.close()
        return [
            {
                "id":             r[0],
                "trade_id":       r[1],
                "ticker":         r[2],
                "signal_source":  r[3],
                "pnl_pct":        float(r[4]) if r[4] is not None else None,
                "win":            r[5],
                "module_credits": r[6],
                "blame_vector":   r[7],
                "trace_id":       r[8],
                "attributed_at":  r[9].isoformat() if r[9] else None,
            }
            for r in rows
        ]
    except Exception:
        return []
