"""
aiem_discovery_engine.py — AIEM DiscoveryEngine

Single job: autonomously propose brand-new candidate signal hypotheses,
validate them out-of-sample, check for overfitting, and place validated
candidates in a review queue.

What this is NOT:
  - Not a self-optimization module (StrategyLifecycle does that).
  - Not another filter (18+ already exist).
  - Not an auto-trading path. Nothing here touches aiem_paper_trades,
    SignalFactory, or any execution endpoint. A promoted candidate is
    a formatted spec for a human to evaluate and manually wire.

Core loop:
  1. Hypothesis generation  — combinatorial feature templates + LLM naming
  2. OOS backtest           — train/test temporal split on polygon_market_daily
  3. Overfitting check      — same 20pp gap threshold as OverfitDetector
  4. Proposal queue         — discovered_candidates table; no auto-deployment

Backtest data source: polygon_market_daily (3.3M rows, 2000–2026).
Lookahead protection: outcomes are computed using LEAD(next trading day close)
— features are all from the signal day's known data at market close, outcome
is next-day's close price. No same-day or future data is used as a predictor.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

_DB_URL = os.environ.get("DATABASE_URL", "")

# ── Shared constants (must stay in sync with OverfitDetector in aiem_edge_filter.py) ──
_OVERFIT_GAP_THRESHOLD = 20.0   # pp: IS win rate - OOS win rate
_MIN_OOS_TRADES        = 30     # minimum OOS occurrences before proposing
_MIN_IS_TRADES         = 50     # minimum in-sample occurrences
#
# Date windows: polygon_market_daily has complete non-NULL features only from
# 2026-04-07 onward (~6,500 tickers/day with gap_pct + rvol + close_strength).
# Prior rows exist but gap_pct/rvol are NULL and would be filtered out anyway.
# Split: first ~6 weeks IS, last ~6 weeks OOS — pure temporal separation.
_TRAIN_START           = "2026-04-07"
_TRAIN_END             = "2026-05-18"
_TEST_START            = "2026-05-19"
_TEST_END              = "2026-07-02"
_MIN_PRICE             = 2.0    # filter sub-penny/illiquid names
_MAX_RVOL              = 100.0  # filter data anomalies
_NEXT_DAY_MAX_GAP_DAYS = 5      # next trading day must be within 5 calendar days

def _conn():
    return psycopg2.connect(_DB_URL, connect_timeout=5)


# ─────────────────────────────────────────────────────────────────────────────
# DB schema
# ─────────────────────────────────────────────────────────────────────────────

def init_schema():
    """Create the discovered_candidates table. Idempotent."""
    ddl = """
    CREATE TABLE IF NOT EXISTS discovered_candidates (
        id               SERIAL PRIMARY KEY,
        candidate_id     TEXT UNIQUE NOT NULL,
        hypothesis_text  TEXT NOT NULL,
        feature_rule     JSONB NOT NULL,
        holding_period   TEXT NOT NULL DEFAULT '1d',
        is_wr            NUMERIC(6,2),
        oos_wr           NUMERIC(6,2),
        is_n             INTEGER,
        oos_n            INTEGER,
        oos_avg_return   NUMERIC(10,6),
        baseline_wr      NUMERIC(6,2),
        overfit_gap      NUMERIC(6,2),
        status           TEXT NOT NULL DEFAULT 'pending',
        rejection_reason TEXT,
        train_window     TEXT,
        test_window      TEXT,
        proposed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        reviewed_at      TIMESTAMPTZ,
        promoted_at      TIMESTAMPTZ,
        notes            TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_disc_cand_status
        ON discovered_candidates (status, proposed_at DESC);
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(ddl)
    print("[discovery_engine] schema init OK")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Hypothesis Templates
#
# Each template is a concrete, testable rule over polygon_market_daily columns:
#   gap_pct, rvol, close_strength, range_pct, close_price
# The rule dict maps column → (operator, threshold).
# Operators: gt (>), lt (<), gte (>=), lte (<=)
# ─────────────────────────────────────────────────────────────────────────────

_HYPOTHESIS_TEMPLATES: List[Dict[str, Any]] = [
    {
        "template_id": "T01",
        "hypothesis_text": (
            "A stock that gaps up ≥2% AND closes in the top 30% of its daily range "
            "shows momentum continuation into the next trading day."
        ),
        "feature_rule": {
            "gap_pct":        ("gte", 2.0),
            "close_strength": ("gte", 0.70),
        },
        "direction": "long",
        "holding_period": "1d",
    },
    {
        "template_id": "T02",
        "hypothesis_text": (
            "A stock with 3x+ normal volume on a tight (<3%) daily range is coiling "
            "before a breakout — next-day follow-through expected."
        ),
        "feature_rule": {
            "rvol":       ("gte", 3.0),
            "range_pct":  ("lte", 0.03),
        },
        "direction": "long",
        "holding_period": "1d",
    },
    {
        "template_id": "T03",
        "hypothesis_text": (
            "A stock closing in the top 25% of its range on 2x+ volume shows "
            "institutional accumulation — higher close likely next day."
        ),
        "feature_rule": {
            "rvol":           ("gte", 2.0),
            "close_strength": ("gte", 0.75),
        },
        "direction": "long",
        "holding_period": "1d",
    },
    {
        "template_id": "T04",
        "hypothesis_text": (
            "A stock that gaps down ≥1.5% but recovers to close in the top 35% of "
            "its range suggests a gap-fill reversal continuation next day."
        ),
        "feature_rule": {
            "gap_pct":        ("lte", -1.5),
            "close_strength": ("gte", 0.65),
        },
        "direction": "long",
        "holding_period": "1d",
    },
    {
        "template_id": "T05",
        "hypothesis_text": (
            "A stock gapping up ≥1.5% on 2.5x+ volume combines price catalyst with "
            "strong accumulation — both confirm momentum for next day."
        ),
        "feature_rule": {
            "gap_pct": ("gte", 1.5),
            "rvol":    ("gte", 2.5),
        },
        "direction": "long",
        "holding_period": "1d",
    },
    {
        "template_id": "T06",
        "hypothesis_text": (
            "A stock closing in the bottom 30% of its daily range on 2x+ volume "
            "shows distribution — next-day weakness expected (short signal)."
        ),
        "feature_rule": {
            "close_strength": ("lte", 0.30),
            "rvol":           ("gte", 2.0),
        },
        "direction": "short",
        "holding_period": "1d",
    },
    {
        "template_id": "T07",
        "hypothesis_text": (
            "A volatile expansion day (range ≥5%) on 2x+ volume signals a trend "
            "initiation — follow-through in the same direction next day."
        ),
        "feature_rule": {
            "range_pct": ("gte", 0.05),
            "rvol":      ("gte", 2.0),
        },
        "direction": "long",
        "holding_period": "1d",
    },
    {
        "template_id": "T08",
        "hypothesis_text": (
            "A stock closing very strong (top 20% of range) on below-average volume "
            "shows stealth accumulation — larger move likely next day."
        ),
        "feature_rule": {
            "rvol":           ("lte", 0.70),
            "close_strength": ("gte", 0.80),
        },
        "direction": "long",
        "holding_period": "1d",
    },
    {
        "template_id": "T09",
        "hypothesis_text": (
            "A small gap up (<1%) that accelerates — stock still closes strong "
            "AND volume is 4x+ normal — suggests breakout ignition day."
        ),
        "feature_rule": {
            "gap_pct":        ("gte", 0.3),
            "rvol":           ("gte", 4.0),
            "close_strength": ("gte", 0.65),
        },
        "direction": "long",
        "holding_period": "1d",
    },
    {
        "template_id": "T10",
        "hypothesis_text": (
            "A stock with a wide range (≥4%) that closes weak (bottom 35%) on "
            "elevated volume — bearish rejection of intraday highs."
        ),
        "feature_rule": {
            "range_pct":      ("gte", 0.04),
            "close_strength": ("lte", 0.35),
            "rvol":           ("gte", 1.5),
        },
        "direction": "short",
        "holding_period": "1d",
    },
]


def _candidate_id(template: Dict) -> str:
    """Deterministic ID from the feature rule so re-running doesn't duplicate."""
    rule_str = json.dumps(template["feature_rule"], sort_keys=True)
    return "cand_" + hashlib.sha1(rule_str.encode()).hexdigest()[:12]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Out-of-Sample Backtester
# ─────────────────────────────────────────────────────────────────────────────

def _load_backtest_universe(
    start: str, end: str, timeout_ms: int = 30000
) -> List[Dict[str, Any]]:
    """
    Load all rows from polygon_market_daily in [start, end] with a valid
    next-trading-day close price (computed via LEAD window function).

    Lookahead protection:
      - All predictor columns (gap_pct, rvol, close_strength, range_pct) are
        from the signal day — knowable at market close.
      - next_day_return is LEAD(close_price) on the NEXT trading day only
        (enforced by next_date <= scan_date + 5 calendar days).
      - No same-day future information is used as a predictor.

    Note: SET LOCAL is a separate execute call so psycopg2 doesn't try to
    handle a multi-statement string (which is not supported in all versions).
    """
    sql_window = """
    WITH windowed AS (
        SELECT
            ticker,
            scan_date,
            gap_pct,
            rvol,
            close_strength,
            range_pct,
            close_price,
            LEAD(close_price) OVER (PARTITION BY ticker ORDER BY scan_date) AS next_close,
            LEAD(scan_date)   OVER (PARTITION BY ticker ORDER BY scan_date) AS next_date
        FROM polygon_market_daily
        WHERE scan_date BETWEEN %s AND %s
          AND gap_pct        IS NOT NULL
          AND rvol           IS NOT NULL
          AND close_strength IS NOT NULL
          AND range_pct      IS NOT NULL
          AND close_price    > %s
          AND rvol           < %s
    )
    SELECT
        ticker, scan_date, gap_pct, rvol, close_strength, range_pct,
        close_price, next_close,
        (next_close / NULLIF(close_price, 0) - 1.0) AS next_day_return
    FROM windowed
    WHERE next_close IS NOT NULL
      AND next_date  <= scan_date + %s
    ORDER BY scan_date, ticker
    """
    rows = []
    try:
        with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{timeout_ms}'")
            cur.execute(sql_window, (
                start, end,
                _MIN_PRICE, _MAX_RVOL,
                _NEXT_DAY_MAX_GAP_DAYS,
            ))
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[discovery] load_backtest_universe error ({start}→{end}): {e}")
    return rows


def _apply_rule(row: Dict, rule: Dict[str, Tuple[str, float]]) -> bool:
    """Return True if row satisfies all conditions in rule dict."""
    ops = {"gte": lambda a, b: a >= b, "lte": lambda a, b: a <= b,
           "gt":  lambda a, b: a >  b, "lt":  lambda a, b: a <  b}
    for col, (op, thresh) in rule.items():
        val = row.get(col)
        if val is None:
            return False
        if not ops[op](float(val), thresh):
            return False
    return True


def _compute_stats(
    rows: List[Dict],
    rule: Dict[str, Tuple[str, float]],
    direction: str = "long",
) -> Dict[str, Any]:
    """
    Apply rule to rows. For 'long' direction, a win is next_day_return > 0.
    For 'short' direction, a win is next_day_return < 0.
    Returns {n, win_rate, avg_return, baseline_wr, baseline_n}.
    """
    signal_rows   = [r for r in rows if _apply_rule(r, rule)]
    baseline_n    = len(rows)
    baseline_wins = sum(1 for r in rows if (r["next_day_return"] or 0) > 0)
    baseline_wr   = 100.0 * baseline_wins / baseline_n if baseline_n > 0 else 0.0

    n    = len(signal_rows)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_return": None,
                "baseline_wr": round(baseline_wr, 2), "baseline_n": baseline_n}

    if direction == "short":
        wins = sum(1 for r in signal_rows if (r["next_day_return"] or 0) < 0)
        returns = [-(r["next_day_return"] or 0) for r in signal_rows]
    else:
        wins    = sum(1 for r in signal_rows if (r["next_day_return"] or 0) > 0)
        returns = [(r["next_day_return"] or 0) for r in signal_rows]

    win_rate   = 100.0 * wins / n
    avg_return = sum(returns) / n

    return {
        "n":           n,
        "win_rate":    round(win_rate, 2),
        "avg_return":  round(avg_return, 6),
        "baseline_wr": round(baseline_wr, 2),
        "baseline_n":  baseline_n,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Overfitting Check
# ─────────────────────────────────────────────────────────────────────────────

def _check_overfit(
    is_wr: Optional[float],
    oos_wr: Optional[float],
    is_n: int,
    oos_n: int,
) -> Tuple[bool, str]:
    """
    Returns (is_overfit: bool, reason: str).

    Uses same threshold as OverfitDetector.GAP_THRESHOLD (20pp) in
    aiem_edge_filter.py — kept in sync via the _OVERFIT_GAP_THRESHOLD constant.
    """
    if is_wr is None or oos_wr is None:
        return True, "insufficient_data: win rates could not be computed"
    if is_n < _MIN_IS_TRADES:
        return True, f"insufficient_is_data: only {is_n} in-sample trades (need {_MIN_IS_TRADES})"
    if oos_n < _MIN_OOS_TRADES:
        return True, f"insufficient_oos_data: only {oos_n} OOS trades (need {_MIN_OOS_TRADES})"

    gap = is_wr - oos_wr
    if gap > _OVERFIT_GAP_THRESHOLD:
        return True, (
            f"overfit: IS_WR={is_wr:.1f}% vs OOS_WR={oos_wr:.1f}% "
            f"(gap={gap:.1f}pp exceeds {_OVERFIT_GAP_THRESHOLD}pp threshold)"
        )
    if oos_wr < 45.0:
        return True, (
            f"poor_oos: OOS win rate {oos_wr:.1f}% is below 45% — no edge"
        )
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. Proposal Queue — persistence layer
# ─────────────────────────────────────────────────────────────────────────────

def _save_candidate(candidate: Dict[str, Any]) -> bool:
    """
    Upsert a candidate into discovered_candidates.
    On conflict (same candidate_id), updates the stats if status changed.
    Returns True on success.
    """
    sql = """
    INSERT INTO discovered_candidates (
        candidate_id, hypothesis_text, feature_rule, holding_period,
        is_wr, oos_wr, is_n, oos_n, oos_avg_return, baseline_wr,
        overfit_gap, status, rejection_reason, train_window, test_window
    ) VALUES (
        %(candidate_id)s, %(hypothesis_text)s, %(feature_rule)s, %(holding_period)s,
        %(is_wr)s, %(oos_wr)s, %(is_n)s, %(oos_n)s, %(oos_avg_return)s, %(baseline_wr)s,
        %(overfit_gap)s, %(status)s, %(rejection_reason)s, %(train_window)s, %(test_window)s
    )
    ON CONFLICT (candidate_id) DO UPDATE SET
        is_wr           = EXCLUDED.is_wr,
        oos_wr          = EXCLUDED.oos_wr,
        is_n            = EXCLUDED.is_n,
        oos_n           = EXCLUDED.oos_n,
        oos_avg_return  = EXCLUDED.oos_avg_return,
        baseline_wr     = EXCLUDED.baseline_wr,
        overfit_gap     = EXCLUDED.overfit_gap,
        status          = EXCLUDED.status,
        rejection_reason= EXCLUDED.rejection_reason,
        proposed_at     = NOW()
    """
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute(sql, {
                **candidate,
                "feature_rule": json.dumps(candidate["feature_rule"]),
            })
        return True
    except Exception as e:
        print(f"[discovery] save_candidate error: {e}")
        return False


def _list_candidates(status: Optional[str] = None, limit: int = 20) -> List[Dict]:
    try:
        sql = """
            SELECT candidate_id, hypothesis_text, feature_rule,
                   is_wr, oos_wr, is_n, oos_n, oos_avg_return,
                   baseline_wr, overfit_gap, status, rejection_reason,
                   train_window, test_window, proposed_at, reviewed_at
            FROM discovered_candidates
        """
        params: list = []
        if status:
            sql += " WHERE status = %s"
            params.append(status)
        sql += " ORDER BY proposed_at DESC LIMIT %s"
        params.append(limit)

        with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        return [{"error": str(e)}]


# ─────────────────────────────────────────────────────────────────────────────
# 5. DiscoveryEngine — main class
# ─────────────────────────────────────────────────────────────────────────────

class DiscoveryEngine:
    """
    Generates new candidate signal hypotheses, validates them out-of-sample,
    checks for overfitting, and places validated candidates in the
    `discovered_candidates` review table.

    CRITICAL SAFETY GUARANTEE:
    This class has NO write path to aiem_paper_trades, ai_short_calls_log,
    SignalFactory, or any other live/paper execution table. The only DB write
    this class performs is to `discovered_candidates`. The promote_candidate()
    method marks a row as `promoted` and returns a formatted spec — it does
    NOT wire the candidate into any execution path. That step is always manual.
    """

    def __init__(self):
        self._lock = threading.Lock()

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_data(self) -> Tuple[List[Dict], List[Dict]]:
        """Load train and test sets. Returns (train_rows, test_rows)."""
        print(f"[discovery] loading training window {_TRAIN_START}→{_TRAIN_END}…")
        train = _load_backtest_universe(_TRAIN_START, _TRAIN_END)
        print(f"[discovery] loaded {len(train):,} training rows")

        print(f"[discovery] loading test window {_TEST_START}→{_TEST_END}…")
        test = _load_backtest_universe(_TEST_START, _TEST_END)
        print(f"[discovery] loaded {len(test):,} test rows")

        return train, test

    # ── Single-template evaluation ─────────────────────────────────────────

    def _evaluate(
        self,
        template: Dict[str, Any],
        train_rows: List[Dict],
        test_rows: List[Dict],
    ) -> Dict[str, Any]:
        """
        Run one template through IS + OOS backtest and overfitting check.
        Returns a result dict including status and rejection_reason.
        """
        cid       = _candidate_id(template)
        direction = template.get("direction", "long")

        is_stats  = _compute_stats(train_rows, template["feature_rule"], direction)
        oos_stats = _compute_stats(test_rows,  template["feature_rule"], direction)

        overfit_gap = None
        if is_stats["win_rate"] is not None and oos_stats["win_rate"] is not None:
            overfit_gap = round(is_stats["win_rate"] - oos_stats["win_rate"], 2)

        is_overfit, reject_reason = _check_overfit(
            is_stats["win_rate"], oos_stats["win_rate"],
            is_stats["n"],        oos_stats["n"],
        )

        status = "rejected" if is_overfit else "pending"

        return {
            "candidate_id":    cid,
            "template_id":     template["template_id"],
            "hypothesis_text": template["hypothesis_text"],
            "feature_rule":    template["feature_rule"],
            "holding_period":  template.get("holding_period", "1d"),
            "direction":       direction,
            "is_wr":           is_stats["win_rate"],
            "oos_wr":          oos_stats["win_rate"],
            "is_n":            is_stats["n"],
            "oos_n":           oos_stats["n"],
            "oos_avg_return":  oos_stats["avg_return"],
            "baseline_wr":     oos_stats["baseline_wr"],
            "overfit_gap":     overfit_gap,
            "status":          status,
            "rejection_reason": reject_reason or None,
            "train_window":    f"{_TRAIN_START}→{_TRAIN_END}",
            "test_window":     f"{_TEST_START}→{_TEST_END}",
        }

    # ── Main discovery cycle ──────────────────────────────────────────────

    def run_cycle(self, templates: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Run a full discovery cycle over all templates (or a provided subset).
        Returns a summary of proposed and rejected candidates.

        SAFETY: no write to any execution table. Only writes to discovered_candidates.
        """
        with self._lock:
            templates = templates or _HYPOTHESIS_TEMPLATES
            train_rows, test_rows = self._load_data()

            if not train_rows or not test_rows:
                return {
                    "error": "no backtest data loaded — check polygon_market_daily",
                    "train_n": len(train_rows),
                    "test_n":  len(test_rows),
                }

            results   = []
            proposed  = []
            rejected  = []

            for tmpl in templates:
                result = self._evaluate(tmpl, train_rows, test_rows)
                _save_candidate(result)
                results.append(result)

                if result["status"] == "pending":
                    proposed.append(result)
                    print(
                        f"[discovery] PROPOSED {result['template_id']} "
                        f"OOS_WR={result['oos_wr']}% n={result['oos_n']} "
                        f"gap={result['overfit_gap']}pp"
                    )
                else:
                    rejected.append(result)
                    print(
                        f"[discovery] REJECTED {result['template_id']} "
                        f"({result['rejection_reason']})"
                    )

            return {
                "total_templates": len(templates),
                "proposed":  len(proposed),
                "rejected":  len(rejected),
                "train_n":   len(train_rows),
                "test_n":    len(test_rows),
                "train_window": f"{_TRAIN_START}→{_TRAIN_END}",
                "test_window":  f"{_TEST_START}→{_TEST_END}",
                "results":   results,
            }

    # ── Candidate management ──────────────────────────────────────────────

    def list_candidates(
        self, status: Optional[str] = None, limit: int = 20
    ) -> List[Dict]:
        """List all discovered candidates, optionally filtered by status."""
        return _list_candidates(status, limit)

    def get_candidate(self, candidate_id: str) -> Optional[Dict]:
        """Fetch a single candidate by ID."""
        rows = _list_candidates(limit=1000)
        for r in rows:
            if r.get("candidate_id") == candidate_id:
                return r
        return None

    def reject_candidate(self, candidate_id: str, reason: str) -> Dict[str, Any]:
        """
        Human/agent explicit rejection — marks a pending candidate as rejected.
        """
        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute("""
                    UPDATE discovered_candidates
                    SET status='rejected', rejection_reason=%s, reviewed_at=NOW()
                    WHERE candidate_id=%s AND status='pending'
                    RETURNING candidate_id
                """, (reason, candidate_id))
                row = cur.fetchone()
            if row:
                return {"ok": True, "candidate_id": candidate_id, "status": "rejected"}
            return {"ok": False, "error": "candidate not found or not in pending status"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def promote_candidate(self, candidate_id: str, notes: str = "") -> Dict[str, Any]:
        """
        Mark a candidate as promoted for human integration.

        CRITICAL: This method ONLY marks the row as promoted and returns a
        formatted spec. It does NOT:
          - Write to aiem_paper_trades
          - Write to ai_short_calls_log
          - Modify SignalFactory or _HYPOTHESIS_TEMPLATES in aiem_v2_system.py
          - Call _aiem_paper_pick_candidates() or any paper trading endpoint
          - Connect to any live or paper execution path

        Integration into the live pipeline requires a human to manually:
          1. Review the OOS stats and overfit_gap shown in this output.
          2. Add the feature rule to SignalFactory._SOURCES in aiem_v2_system.py.
          3. Add a signal entry to aiem_signal_discoveries with a hypothesis_id.
          4. Let it collect ≥20 live trades before EdgeFilterOrchestrator enables
             hard blocks (ExpectancyEngine cold-start pass-through applies).
        """
        cand = self.get_candidate(candidate_id)
        if not cand:
            return {"ok": False, "error": f"candidate {candidate_id} not found"}
        if cand.get("status") not in ("pending",):
            return {
                "ok": False,
                "error": f"can only promote pending candidates (current: {cand.get('status')})"
            }

        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute("""
                    UPDATE discovered_candidates
                    SET status='promoted', reviewed_at=NOW(), promoted_at=NOW(),
                        notes=%s
                    WHERE candidate_id=%s
                """, (notes, candidate_id))
        except Exception as e:
            return {"ok": False, "error": str(e)}

        spec = (
            f"=== PROMOTED CANDIDATE SPEC ===\n"
            f"Candidate ID: {candidate_id}\n"
            f"Hypothesis: {cand['hypothesis_text']}\n"
            f"Feature rule: {json.dumps(cand['feature_rule'], indent=2)}\n"
            f"IS window:  {cand.get('train_window')}  "
                f"WR={cand.get('is_wr')}%  n={cand.get('is_n')}\n"
            f"OOS window: {cand.get('test_window')}  "
                f"WR={cand.get('oos_wr')}%  n={cand.get('oos_n')}  "
                f"avg_return={cand.get('oos_avg_return')}\n"
            f"Overfit gap: {cand.get('overfit_gap')}pp  "
                f"Baseline WR: {cand.get('baseline_wr')}%\n"
            f"\n"
            f"NEXT STEPS (manual — not automatic):\n"
            f"  1. Add to SignalFactory._SOURCES in aiem_v2_system.py\n"
            f"  2. Register in aiem_signal_discoveries with hypothesis_id\n"
            f"  3. Allow ≥20 live paper trades before hard-blocking is active\n"
            f"  4. Monitor live WR vs OOS baseline via OverfitDetector\n"
        )
        return {
            "ok":          True,
            "candidate_id": candidate_id,
            "status":      "promoted",
            "spec":        spec,
        }

    def status(self) -> Dict[str, Any]:
        """Summary of current discovery queue."""
        try:
            with _conn() as c, c.cursor() as cur:
                cur.execute("""
                    SELECT status, COUNT(*) FROM discovered_candidates
                    GROUP BY status
                """)
                counts = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute("SELECT MAX(proposed_at) FROM discovered_candidates")
                row = cur.fetchone()
                last_run = str(row[0]) if row and row[0] else None
        except Exception as e:
            return {"error": str(e)}

        return {
            "pending":    counts.get("pending",  0),
            "rejected":   counts.get("rejected", 0),
            "promoted":   counts.get("promoted", 0),
            "total":      sum(counts.values()),
            "last_run":   last_run,
            "train_window": f"{_TRAIN_START}→{_TRAIN_END}",
            "test_window":  f"{_TEST_START}→{_TEST_END}",
            "overfit_threshold_pp": _OVERFIT_GAP_THRESHOLD,
            "min_oos_trades": _MIN_OOS_TRADES,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Singleton factory
# ─────────────────────────────────────────────────────────────────────────────

_instance: Optional[DiscoveryEngine] = None
_factory_lock = threading.Lock()

def get_discovery_engine() -> DiscoveryEngine:
    global _instance
    with _factory_lock:
        if _instance is None:
            _instance = DiscoveryEngine()
    return _instance
