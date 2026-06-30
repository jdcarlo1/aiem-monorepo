"""
Shared behavioral-fingerprint math.

Extracted from main.py's AIEM 24/7 behavioral engine so that BOTH independent
processes (main.py / Flask API server, and aiem_autonomous.py / standalone
scheduler) can compute and compare the same 14-dim fingerprint against the
same `pre_move_templates` table without re-deriving (and silently drifting
from) the feature math in two places.

This module is intentionally dependency-light (numpy + psycopg2 only) and
has NO import-time side effects, so either process can import it safely.

main.py's local `_compute_fingerprint` / `_cosine_sim` are left as-is for now
(lower risk than touching a 46k-line live file) — this module is the new
source of truth going forward; main.py can be migrated to wrap it later
without behavior change, since the math here is a verbatim port.
"""

import numpy as np


FEATURE_ORDER = [
    'avg_gap', 'avg_rvol', 'avg_cs', 'cs_accel', 'vol_accel_5d', 'vol_accel_10d',
    'price_mom_5d', 'price_mom_10d', 'avg_range', 'range_comp',
    'days_positive', 'vwap_above', 'high_prog', 'gap_count',
]

# Column order of the 14 numeric feature columns as stored in pre_move_templates,
# NOTE: the table does not store vol_accel_10d / price_mom_10d (see _rebuild_templates
# in main.py) — those two slots are zero-filled when comparing against a template row,
# matching the convention already established by main.py's _mkt_retrospective_backtest.
TEMPLATE_COLUMNS = (
    "ticker, move_date, move_pct, avg_gap, avg_rvol, avg_cs, cs_accel, "
    "vol_accel_5d, price_mom_5d, avg_range, range_comp, days_positive, "
    "vwap_above, high_prog, gap_count"
)


def _safe(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def compute_fingerprint(rows):
    """
    Given a list of daily rows (most recent first), compute a 14-dim behavioral
    fingerprint. Each dimension measures something distinct about the stock's
    character over the past 5-10 days.

    rows format: list of dicts with keys:
      close_price, open_price, high_price, low_price, vwap, volume,
      prev_close, gap_pct, rvol, close_strength, range_pct

    Returns dict with 'features' (named dict) and 'vec' (14-dim numpy array),
    or None if there isn't enough history.
    """
    if not rows or len(rows) < 3:
        return None

    r = rows  # most recent first
    n = len(r)

    r5 = r[:min(5, n)]

    avg_gap = np.mean([_safe(x.get('gap_pct')) for x in r5])
    avg_rvol = np.mean([_safe(x.get('rvol'), 1.0) for x in r5])
    avg_cs = np.mean([_safe(x.get('close_strength'), 0.5) for x in r5])
    avg_rng = np.mean([_safe(x.get('range_pct')) for x in r5])

    cs_recent = np.mean([_safe(x.get('close_strength'), 0.5) for x in r[:2]]) if n >= 2 else avg_cs
    cs_prior = np.mean([_safe(x.get('close_strength'), 0.5) for x in r[2:5]]) if n >= 5 else avg_cs
    cs_accel = float(cs_recent - cs_prior)

    vol_recent_5 = np.mean([_safe(x.get('volume'), 100000) for x in r[:2]]) if n >= 2 else 100000
    vol_prior_5 = np.mean([_safe(x.get('volume'), 100000) for x in r[2:5]]) if n >= 5 else 100000
    vol_accel_5d = float((vol_recent_5 / max(vol_prior_5, 1)) - 1)

    vol_recent_10 = np.mean([_safe(x.get('volume'), 100000) for x in r[:5]]) if n >= 5 else 100000
    vol_prior_10 = np.mean([_safe(x.get('volume'), 100000) for x in r[5:10]]) if n >= 10 else vol_prior_5
    vol_accel_10d = float((vol_recent_10 / max(vol_prior_10, 1)) - 1)

    close_now = _safe(r[0].get('close_price'))
    close_5d = _safe(r[min(4, n - 1)].get('close_price'))
    close_10d = _safe(r[min(9, n - 1)].get('close_price'))
    price_mom_5d = float((close_now / max(close_5d, 0.01)) - 1) if close_5d > 0 else 0.0
    price_mom_10d = float((close_now / max(close_10d, 0.01)) - 1) if close_10d > 0 else 0.0

    rng_recent = np.mean([_safe(x.get('range_pct')) for x in r[:2]]) if n >= 2 else avg_rng
    rng_prior = np.mean([_safe(x.get('range_pct')) for x in r[2:5]]) if n >= 5 else avg_rng
    range_comp = float(rng_prior - rng_recent)  # positive = tightening = coiling

    days_pos = sum(1 for x in r5
                   if _safe(x.get('close_price')) > _safe(x.get('prev_close')))

    vwap_above = float(np.mean([
        1.0 if _safe(x.get('close_price')) >= _safe(x.get('vwap'), _safe(x.get('close_price')))
        else 0.0 for x in r5
    ]))

    highs = [_safe(x.get('high_price')) for x in r5 if x.get('high_price')]
    if len(highs) >= 2:
        diffs = [highs[i] - highs[i + 1] for i in range(len(highs) - 1)]
        high_prog = float(np.mean(diffs) / max(close_now, 0.01))
    else:
        high_prog = 0.0

    gap_count = sum(1 for x in r5 if _safe(x.get('gap_pct')) > 0.5)

    features = {
        'avg_gap': round(float(avg_gap), 4),
        'avg_rvol': round(float(avg_rvol), 4),
        'avg_cs': round(float(avg_cs), 4),
        'cs_accel': round(cs_accel, 4),
        'vol_accel_5d': round(vol_accel_5d, 4),
        'vol_accel_10d': round(vol_accel_10d, 4),
        'price_mom_5d': round(price_mom_5d, 4),
        'price_mom_10d': round(price_mom_10d, 4),
        'avg_range': round(float(avg_rng), 4),
        'range_comp': round(range_comp, 4),
        'days_positive': int(days_pos),
        'vwap_above': round(vwap_above, 4),
        'high_prog': round(high_prog, 4),
        'gap_count': int(gap_count),
    }
    vec = np.array([features[k] for k in FEATURE_ORDER], dtype=float)
    return {'features': features, 'vec': vec}


def cosine_similarity(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _template_row_to_vec(tr):
    """tr is a row from TEMPLATE_COLUMNS: ticker(0),move_date(1),move_pct(2),
    avg_gap(3),avg_rvol(4),avg_cs(5),cs_accel(6),vol_accel_5d(7),price_mom_5d(8),
    avg_range(9),range_comp(10),days_positive(11),vwap_above(12),high_prog(13),
    gap_count(14). vol_accel_10d/price_mom_10d are not stored — zero-filled,
    matching main.py's existing _mkt_retrospective_backtest convention."""
    return np.array([
        float(tr[3] or 0),   # avg_gap
        float(tr[4] or 0),   # avg_rvol
        float(tr[5] or 0),   # avg_cs
        float(tr[6] or 0),   # cs_accel
        float(tr[7] or 0),   # vol_accel_5d
        0.0,                 # vol_accel_10d (not stored in template)
        float(tr[8] or 0),   # price_mom_5d
        0.0,                 # price_mom_10d (not stored in template)
        float(tr[9] or 0),   # avg_range
        float(tr[10] or 0),  # range_comp
        float(tr[11] or 0),  # days_positive
        float(tr[12] or 0),  # vwap_above
        float(tr[13] or 0),  # high_prog
        float(tr[14] or 0),  # gap_count
    ], dtype=float)


def best_template_match(cur, fingerprint, exclude_ticker=None, exclude_move_date=None,
                         min_move_pct=10.0, sample_limit=200):
    """
    Given a cursor on a connection already open against the shared DB, and a
    fingerprint dict (from compute_fingerprint), find the best-matching historical
    pre-move template. Returns (best_sim: float, best_match_row: tuple|None).

    best_match_row columns match TEMPLATE_COLUMNS, so best_match_row[0] is ticker,
    best_match_row[1] is move_date, best_match_row[2] is move_pct.
    """
    if fingerprint is None:
        return 0.0, None

    where = ["move_pct >= %s"]
    params = [min_move_pct]
    if exclude_ticker is not None and exclude_move_date is not None:
        where.append("NOT (ticker = %s AND move_date = %s)")
        params.extend([exclude_ticker, exclude_move_date])

    cur.execute(f"""
        SELECT {TEMPLATE_COLUMNS}
        FROM pre_move_templates
        WHERE {' AND '.join(where)}
        ORDER BY RANDOM() LIMIT %s
    """, (*params, sample_limit))
    tmpl_rows = cur.fetchall()

    best_sim, best_match = 0.0, None
    for tr in tmpl_rows:
        tvec = _template_row_to_vec(tr)
        sim = cosine_similarity(fingerprint['vec'], tvec)
        if sim > best_sim:
            best_sim = sim
            best_match = tr

    return best_sim, best_match
