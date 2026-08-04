"""
aiem_pre_move_signals.py — Leading indicators for bullish CALL / AIEM entries.

Empirical basis (2026-07-28 → 2026-08-03 cohort: AEHR, AXTI, NBIS, IREN, MU,
WOLF, VSH, PENG, VPG, LUNR, LRCX, AMAT, ENTG, …):

  As of the EOD BEFORE the big bounce (2026-07-29):
    • 2-day drawdown ≲ −8% to −20%
    • Capitulation close (close_strength ≲ 0.22)
    • Elevated RVOL (≳ 0.85)
    • Range expansion (range_pct ≳ 5.5)
    • Down day (gap_pct ≲ −2%)

  That washout day is the LEADING setup. Next session often rips +10% to +30%.

Also:
  momentum_continuation — strong bounce day (gap≥8%, CS≥0.70) → next-day CALL/STOCK

Used by:
  • aiem_options_scheduler CALL seed lane (washout_reclaim / thrust / continuation)
  • main._aiem_paper_pick_candidates (wide-net sources washout_reclaim,
    momentum_continuation, thrust_pullback — full qualifying universe, not top-N)
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Leveraged / inverse / single-stock 2x-3x ETFs — washout noise, not the thesis.
# TQQQ intentionally allowed (explicit user target).
LEVERAGED_ETF_DENYLIST: Tuple[str, ...] = (
    "SQQQ", "UPRO", "SPXU", "SOXL", "SOXS", "TECL", "TECS",
    "TNA", "TZA", "FAS", "FAZ", "UDOW", "SDOW", "QLD", "QID",
    "SPXL", "SPXS", "LABU", "LABD", "DPST", "UVXY", "VXX", "SVXY",
    "BOIL", "KOLD", "JNUG", "JDST", "NUGT", "DUST", "NAIL", "DRN", "DRV",
    "TMF", "TBT", "UST", "PST", "YINN", "YANG", "CURE", "MIDU",
    "NBIL", "NEBX", "MUU", "MULL", "AMDL", "AMUU", "AMDG", "AMAU",
    "KLAG", "KORU", "HIMZ", "HUTG", "WULX", "ARMG", "LITX", "FRVO",
    "CRWG", "CLSX", "MRAL", "MRVU", "LRCU", "MVLL", "RKLX", "QPUX",
    "SKHU", "SKUU", "SNDU", "CIFU", "COHX", "RIOX", "SHAZ", "VRTL",
)

WASHOUT_D2_MAX = -0.08
WASHOUT_CS_MAX = 0.22
WASHOUT_RVOL_MIN = 0.85
WASHOUT_RANGE_MIN = 5.5
WASHOUT_GAP_MAX = -2.0
# LWLG (~$5) / MRAM (~$13) were classic Jul-29 washouts; keep floor tradeable.
WASHOUT_PRICE_MIN = 5.0
WASHOUT_PRICE_MAX = 1000.0
WASHOUT_DVOL_MIN = 8_000_000.0   # $8M — LWLG/MRAM cleared ~$13M on washout day
# Wide net: return the full qualifying universe (ranked). Jul-29 had ~260 hits.
WASHOUT_LIMIT = 500

CONT_GAP_MIN = 8.0
CONT_CS_MIN = 0.70
CONT_RVOL_MIN = 0.85
CONT_LIMIT = 50

# Mild pullback into strength (ORCL Jul-29 → Aug-3 cohort)
THRUST_LOOKBACK = 3
THRUST_MIN_PRIOR_PCT = 3.0       # had a ≥3% up day in lookback
THRUST_PULLBACK_MIN = -4.5       # today's return not a crash
THRUST_PULLBACK_MAX = -0.5       # at least a mild red day
THRUST_CS_MIN = 0.15
THRUST_CS_MAX = 0.55             # controlled, not capitulation
THRUST_RVOL_MIN = 0.70
THRUST_DVOL_MIN = 50_000_000.0
THRUST_LIMIT = 40


def score_washout_setup(
    d2_pct: float,
    close_strength: float,
    rvol: float,
    range_pct: float,
    dvol: float,
) -> float:
    """Map washout severity → AIEM 5.0–9.0 conviction scale.

    Tuned so classic cohort fingerprints (d2 −10..−22%, rvol 1.0–2.2,
    high dollar volume) outrank levered crash junk (d2 < −30%, rvol > 3).
    """
    d2 = float(d2_pct)
    rv = float(rvol)
    cs = float(close_strength)
    rng = float(range_pct)

    # Core washout intensity
    raw = (
        abs(min(d2, 0.0)) * 0.25
        + (1.0 - min(max(cs, 0.0), 1.0)) * 20.0
        + min(max(rv, 0.0), 2.5) * 10.0
        + min(max(rng, 0.0), 20.0) * 0.35
    )
    # Classic cohort band bonus (AEHR/NBIS/MU fingerprint)
    if -22.0 <= d2 <= -8.0:
        raw += 18.0
    elif d2 < -30.0:
        raw -= 15.0  # crash junk
    if 0.90 <= rv <= 2.20:
        raw += 12.0
    elif rv > 3.0:
        raw -= 10.0
    # Liquidity — real names the engine can option
    if dvol >= 1e9:
        raw += 22.0
    elif dvol >= 2e8:
        raw += 14.0
    elif dvol >= 5e7:
        raw += 6.0
    return round(5.0 + 4.0 * raw / (raw + 80.0), 2) if raw > 0 else 5.0


def score_momentum_continuation(gap_pct: float, close_strength: float, rvol: float) -> float:
    raw = float(gap_pct) * (1.0 + float(close_strength)) * min(float(rvol), 3.0)
    return round(5.0 + 4.0 * raw / (raw + 40.0), 2) if raw > 0 else 5.0


_WASHOUT_SQL = """
WITH ranked AS (
    SELECT ticker, scan_date, close_price, gap_pct, rvol, close_strength,
           range_pct, volume,
           LAG(close_price, 2) OVER (
               PARTITION BY ticker ORDER BY scan_date
           ) AS c2
    FROM polygon_market_daily
    WHERE scan_date BETWEEN %s AND %s
      AND close_price BETWEEN %s AND %s
)
SELECT ticker, scan_date, close_price, gap_pct, rvol, close_strength, range_pct,
       volume,
       ((close_price - c2) / NULLIF(c2, 0)) AS d2_ret,
       (close_price * volume) AS dvol
FROM ranked
WHERE scan_date = %s
  AND c2 IS NOT NULL
  AND ((close_price - c2) / NULLIF(c2, 0)) <= %s
  AND close_strength <= %s
  AND rvol >= %s
  AND range_pct >= %s
  AND gap_pct <= %s
  AND (close_price * volume) >= %s
  AND (close_price * volume) < %s
  AND LENGTH(ticker) BETWEEN 1 AND 5
  AND ticker <> ALL(%s)
ORDER BY {order_expr}
LIMIT %s
"""

_CONTINUATION_SQL = """
SELECT ticker, scan_date, close_price, gap_pct, rvol, close_strength, volume,
       (close_price * volume) AS dvol
FROM polygon_market_daily
WHERE scan_date = %s
  AND gap_pct >= %s
  AND close_strength >= %s
  AND rvol >= %s
  AND close_price BETWEEN %s AND %s
  AND (close_price * volume) >= %s
  AND LENGTH(ticker) BETWEEN 1 AND 5
  AND ticker <> ALL(%s)
ORDER BY gap_pct * rvol * (1.0 + close_strength) DESC
LIMIT %s
"""


def _asof_window(asof: date) -> Tuple[date, date]:
    return asof - timedelta(days=10), asof


def _row_to_setup(t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tkr = str(t["ticker"]).upper()
    if tkr.endswith(("XL", "XU", "UU", "LL", "ZX", "XZ")):
        return None
    d2 = float(t["d2_ret"] or 0.0)
    cs = float(t["close_strength"] or 0.0)
    rv = float(t["rvol"] or 0.0)
    rng = float(t["range_pct"] or 0.0)
    dvol = float(t["dvol"] or 0.0)
    score = score_washout_setup(d2 * 100.0, cs, rv, rng, dvol)
    return {
        "ticker": tkr,
        "scan_date": t["scan_date"],
        "close_price": float(t["close_price"] or 0),
        "d2_pct": round(d2 * 100.0, 2),
        "gap_pct": round(float(t["gap_pct"] or 0), 2),
        "rvol": round(rv, 2),
        "close_strength": round(cs, 3),
        "range_pct": round(rng, 2),
        "dvol": dvol,
        "score": score,
        "source": "washout_reclaim",
        "detail": (
            f"washout d2={d2*100:.1f}% cs={cs:.2f} "
            f"rvol={rv:.1f}x rng={rng:.1f}%"
        ),
    }


def _fetch_washout_bucket(
    cur,
    asof: date,
    dvol_min: float,
    dvol_max: float,
    limit: int,
    denylist: Sequence[str],
    order_by_dvol: bool,
) -> List[Dict[str, Any]]:
    start, end = _asof_window(asof)
    order_expr = (
        "(close_price * volume) DESC"
        if order_by_dvol
        else """(
            ABS(LEAST(((close_price - c2) / NULLIF(c2, 0)) * 100.0, 0)) * 0.50
            + (1.0 - LEAST(GREATEST(close_strength, 0), 1)) * 30.0
            + LEAST(rvol, 3.0) * 10.0
            + LEAST(range_pct, 25.0) * 0.50
            + LN(GREATEST(close_price * volume / 1000000.0, 1.0)) * 3.0
        ) DESC"""
    )
    sql = _WASHOUT_SQL.format(order_expr=order_expr)
    cur.execute(
        sql,
        (
            start, end,
            WASHOUT_PRICE_MIN, WASHOUT_PRICE_MAX,
            asof,
            WASHOUT_D2_MAX, WASHOUT_CS_MAX, WASHOUT_RVOL_MIN,
            WASHOUT_RANGE_MIN, WASHOUT_GAP_MAX,
            dvol_min, dvol_max,
            list(denylist),
            int(limit),
        ),
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        if isinstance(row, dict):
            t = row
        else:
            cols = [d[0] for d in cur.description]
            t = dict(zip(cols, row))
        setup = _row_to_setup(t)
        if setup:
            out.append(setup)
    return out


def scan_washout_reclaim(
    cur,
    asof: Optional[date] = None,
    limit: int = WASHOUT_LIMIT,
    denylist: Sequence[str] = LEVERAGED_ETF_DENYLIST,
) -> List[Dict[str, Any]]:
    """
    Leading washout-reclaim setups as of `asof` EOD (for NEXT session entries).

    Wide net (user requirement: do not miss moves): return EVERY name that
    clears the fingerprint filters, ranked by score. Limit only caps the
    ranked list (default 200 ≈ full Jul-29 qualifying universe).
    """
    asof = asof or date.today()
    # Fetch the full qualifying set (hard ceiling 500 for safety).
    pool = _fetch_washout_bucket(
        cur, asof, WASHOUT_DVOL_MIN, 1e15, 500, denylist, order_by_dvol=True,
    )
    ranked = sorted(pool, key=lambda r: r["score"], reverse=True)
    return ranked[: max(1, int(limit))]


def scan_all_pre_move(
    cur,
    asof: Optional[date] = None,
    washout_limit: int = WASHOUT_LIMIT,
    continuation_limit: int = CONT_LIMIT,
    thrust_limit: int = THRUST_LIMIT,
) -> List[Dict[str, Any]]:
    """Union of washout + continuation + thrust, deduped (highest score wins)."""
    asof = asof or date.today()
    merged: Dict[str, Dict[str, Any]] = {}
    for row in (
        scan_washout_reclaim(cur, asof=asof, limit=washout_limit)
        + scan_momentum_continuation(cur, asof=asof, limit=continuation_limit)
        + scan_thrust_pullback(cur, asof=asof, limit=thrust_limit)
    ):
        t = row["ticker"]
        prev = merged.get(t)
        if prev is None or float(row["score"]) > float(prev["score"]):
            merged[t] = row
    return sorted(merged.values(), key=lambda r: r["score"], reverse=True)


def scan_momentum_continuation(
    cur,
    asof: Optional[date] = None,
    limit: int = CONT_LIMIT,
    denylist: Sequence[str] = LEVERAGED_ETF_DENYLIST,
) -> List[Dict[str, Any]]:
    """Strong bounce day → candidate for next-session continuation."""
    asof = asof or date.today()
    cur.execute(
        _CONTINUATION_SQL,
        (
            asof,
            CONT_GAP_MIN, CONT_CS_MIN, CONT_RVOL_MIN,
            WASHOUT_PRICE_MIN, WASHOUT_PRICE_MAX,
            WASHOUT_DVOL_MIN,
            list(denylist),
            int(limit),
        ),
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        if isinstance(row, dict):
            t = row
        else:
            cols = [d[0] for d in cur.description]
            t = dict(zip(cols, row))
        tkr = str(t["ticker"]).upper()
        if tkr.endswith(("XL", "XU", "UU", "LL", "ZX", "XZ")):
            continue
        gp = float(t["gap_pct"] or 0)
        cs = float(t["close_strength"] or 0)
        rv = float(t["rvol"] or 0)
        out.append({
            "ticker": tkr,
            "scan_date": t["scan_date"],
            "close_price": float(t["close_price"] or 0),
            "gap_pct": round(gp, 2),
            "rvol": round(rv, 2),
            "close_strength": round(cs, 3),
            "score": score_momentum_continuation(gp, cs, rv),
            "source": "momentum_continuation",
            "detail": f"cont gap={gp:.1f}% cs={cs:.2f} rvol={rv:.1f}x",
        })
    return out


_THRUST_SQL = """
WITH ranked AS (
    SELECT ticker, scan_date, close_price, gap_pct, rvol, close_strength,
           range_pct, volume,
           LAG(gap_pct, 1) OVER (PARTITION BY ticker ORDER BY scan_date) AS gap_1,
           LAG(gap_pct, 2) OVER (PARTITION BY ticker ORDER BY scan_date) AS gap_2,
           LAG(gap_pct, 3) OVER (PARTITION BY ticker ORDER BY scan_date) AS gap_3,
           LAG(close_price, 3) OVER (PARTITION BY ticker ORDER BY scan_date) AS c3
    FROM polygon_market_daily
    WHERE scan_date BETWEEN %s AND %s
      AND close_price BETWEEN %s AND %s
)
SELECT ticker, scan_date, close_price, gap_pct, rvol, close_strength, range_pct,
       volume, gap_1, gap_2, gap_3,
       ((close_price - c3) / NULLIF(c3, 0)) AS d3_ret,
       (close_price * volume) AS dvol
FROM ranked
WHERE scan_date = %s
  AND gap_pct BETWEEN %s AND %s
  AND close_strength BETWEEN %s AND %s
  AND rvol >= %s
  AND (close_price * volume) >= %s
  AND (
        COALESCE(gap_1, -999) >= %s
        OR COALESCE(gap_2, -999) >= %s
        OR COALESCE(gap_3, -999) >= %s
      )
  AND LENGTH(ticker) BETWEEN 1 AND 5
  AND ticker <> ALL(%s)
ORDER BY (close_price * volume) DESC
LIMIT %s
"""


def score_thrust_pullback(gap_pct: float, prior_thrust: float, close_strength: float, dvol: float) -> float:
    raw = (
        abs(float(gap_pct)) * 4.0
        + min(float(prior_thrust), 15.0) * 2.0
        + (1.0 - abs(float(close_strength) - 0.35)) * 20.0
    )
    if dvol >= 1e9:
        raw += 15.0
    elif dvol >= 1e8:
        raw += 8.0
    return round(5.0 + 4.0 * raw / (raw + 50.0), 2) if raw > 0 else 5.0


def scan_thrust_pullback(
    cur,
    asof: Optional[date] = None,
    limit: int = THRUST_LIMIT,
    denylist: Sequence[str] = LEVERAGED_ETF_DENYLIST,
) -> List[Dict[str, Any]]:
    """
    Mild red day after a recent thrust — ORCL Jul-29 class.

    Not a capitulation washout; a controlled digests-the-move pullback that
    often precedes the next leg (ORCL +8% Jul 30, +9% Aug 3).
    """
    asof = asof or date.today()
    start, end = _asof_window(asof)
    cur.execute(
        _THRUST_SQL,
        (
            start, end,
            20.0, WASHOUT_PRICE_MAX,  # liquid larger names for this pattern
            asof,
            THRUST_PULLBACK_MIN, THRUST_PULLBACK_MAX,
            THRUST_CS_MIN, THRUST_CS_MAX,
            THRUST_RVOL_MIN,
            THRUST_DVOL_MIN,
            THRUST_MIN_PRIOR_PCT, THRUST_MIN_PRIOR_PCT, THRUST_MIN_PRIOR_PCT,
            list(denylist),
            max(int(limit) * 3, 24),
        ),
    )
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        if isinstance(row, dict):
            t = row
        else:
            cols = [d[0] for d in cur.description]
            t = dict(zip(cols, row))
        tkr = str(t["ticker"]).upper()
        if tkr.endswith(("XL", "XU", "UU", "LL", "ZX", "XZ")):
            continue
        gp = float(t["gap_pct"] or 0)
        cs = float(t["close_strength"] or 0)
        prior = max(
            float(t["gap_1"] or 0),
            float(t["gap_2"] or 0),
            float(t["gap_3"] or 0),
        )
        dvol = float(t["dvol"] or 0)
        out.append({
            "ticker": tkr,
            "scan_date": t["scan_date"],
            "close_price": float(t["close_price"] or 0),
            "gap_pct": round(gp, 2),
            "rvol": round(float(t["rvol"] or 0), 2),
            "close_strength": round(cs, 3),
            "prior_thrust_pct": round(prior, 2),
            "score": score_thrust_pullback(gp, prior, cs, dvol),
            "source": "thrust_pullback",
            "detail": (
                f"thrust_pb gap={gp:.1f}% prior={prior:.1f}% cs={cs:.2f}"
            ),
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[:limit]


def latest_pmd_date(cur) -> Optional[date]:
    cur.execute("SELECT MAX(scan_date) FROM polygon_market_daily")
    row = cur.fetchone()
    if not row:
        return None
    return row[0] if not isinstance(row, dict) else list(row.values())[0]
